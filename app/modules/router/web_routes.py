from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, session, request, abort, jsonify,
)
from functools import wraps
from uuid import UUID
from datetime import datetime
import secrets

from app.core.logging.logger import logger
from app.modules.router.service import RouterService
from app.modules.router.repository import (
    RouterRepository,
    HotspotServerRepository,
    PPPoeServerRepository,
)
from app.modules.network.service import NetworkService
from app.modules.organization.service import OrganizationService
from app.modules.auth.repository import UserRepository
from app.core.database.session import db
from app.models.nas import NAS


router_web_bp = Blueprint(
    'router_web', __name__,
    url_prefix='/organization/<org_id>/routers'
)

router_service = RouterService()
router_repo = RouterRepository()
hotspot_repo = HotspotServerRepository()
pppoe_repo = PPPoeServerRepository()
network_service = NetworkService()
organization_service = OrganizationService()
user_repo = UserRepository()

# DECORATORS
def web_router_access_required(f):
    """
    Decorator to validate organization access and load user/org context.

    Checks:
        1. User is authenticated (session)
        2. User account exists and is active
        3. Organization ID is valid UUID
        4. User belongs to the organization
        5. Organization exists

    Injects current_user and current_organization into kwargs.
    """
    @wraps(f)
    def decorated_function(org_id, *args, **kwargs):
        # Check authentication
        user_id = session.get('user_id')
        if not user_id:
            flash('Please login to continue', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('web.login'))

        # Verify user exists and is active
        user = user_repo.get_by_id(user_id)
        if not user or not user.is_active:
            session.clear()
            flash('User account not found or inactive', 'danger')
            return redirect(url_for('web.login'))

        # Validate organization ID
        try:
            org_uuid = UUID(org_id)
        except (ValueError, AttributeError):
            logger.warning(f"Invalid org_id format in URL: {org_id}")
            abort(404)

        # Verify user belongs to organization
        user_orgs = organization_service.get_organizations_by_user(user.id)
        if org_uuid not in [org.id for org in user_orgs]:
            logger.warning(
                f"User {user.id} attempted to access organization "
                f"{org_id} without permission"
            )
            flash('You do not have access to this organization', 'danger')
            return redirect(url_for('web.dashboard'))

        # Load organization
        organization = organization_service.get_organization(org_uuid)
        if not organization:
            flash('Organization not found', 'danger')
            return redirect(url_for('web.dashboard'))

        # Inject context
        kwargs['current_user'] = user
        kwargs['current_organization'] = organization

        return f(org_id, *args, **kwargs)

    return decorated_function

# HELPER: Build router summary for sidebar/dashboard context
def _get_router_context(organization_id):
    """Get summary counts for the router sidebar."""
    return {
        'total': router_repo.count_by_organization(organization_id),
        'online': router_repo.count_by_organization(
            organization_id, status='online'
        ),
        'offline': router_repo.count_by_organization(
            organization_id, status='offline'
        ),
        'radius_pending': router_repo.count_radius_pending(organization_id),
        'radius_failed': router_repo.count_radius_failed(organization_id),
    }

# LIST ROUTERS
@router_web_bp.route('/')
@web_router_access_required
def index(org_id, current_user=None, current_organization=None):
    """
    GET /organization/<org_id>/routers/

    List all routers with filtering and pagination.
    """
    # Parse query parameters
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    skip = (page - 1) * per_page

    status_filter = request.args.get('status')
    network_id = request.args.get('network_id')
    radius_config_status = request.args.get('radius_config_status')

    # Validate network_id UUID
    network_uuid = None
    if network_id:
        try:
            network_uuid = UUID(network_id)
        except ValueError:
            flash('Invalid network filter', 'warning')
            network_id = None

    # Get networks for filter dropdown
    networks = network_service.get_organization_networks(
        current_organization.id, 0, 100
    )

    # Fetch routers
    routers = router_service.get_routers_by_organization(
        organization_id=current_organization.id,
        skip=skip,
        limit=per_page,
        status=status_filter,
        network_id=network_uuid,
        radius_config_status=radius_config_status,
    )

    # Get total count for pagination
    total = router_repo.count_by_organization(
        current_organization.id,
        status=status_filter,
        radius_config_status=radius_config_status,
    )

    # Build pagination
    total_pages = (total + per_page - 1) // per_page if total > 0 else 0
    pagination = {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': total_pages,
        'has_next': page < total_pages,
        'has_prev': page > 1,
    }

    # Get sidebar context
    context = _get_router_context(current_organization.id)

    # Display one-time RADIUS secret from session (after regeneration)
    new_secret = session.pop('new_radius_secret', None)
    radius_instructions = session.pop('radius_instructions', None)

    return render_template(
        'web/router/index.html',
        organization=current_organization,
        user=current_user,
        routers=routers,
        networks=networks,
        pagination=pagination,
        context=context,
        filters={
            'status': status_filter,
            'network_id': network_id,
            'radius_config_status': radius_config_status,
        },
        new_secret=new_secret,
        radius_instructions=radius_instructions,
    )

# CREATE ROUTER
@router_web_bp.route('/create', methods=['GET', 'POST'])
@web_router_access_required
def create(org_id, current_user=None, current_organization=None):
    """
    GET/POST /organization/<org_id>/routers/create

    Create a new router with automatic RADIUS configuration.
    """
    networks = network_service.get_organization_networks(
        current_organization.id, 0, 100
    )

    if request.method == 'GET':
        # Show the form
        # Check for test result in session (from AJAX test)
        test_result = session.pop('router_test_result', None)

        return render_template(
            'web/router/create.html',
            organization=current_organization,
            user=current_user,
            networks=networks,
            form_data=None,
            test_result=test_result,
        )

    # -------------------------------------------------------------------------
    # POST — Create router
    # -------------------------------------------------------------------------
    form_data = {
        'name': request.form.get('name', '').strip(),
        'network_id': request.form.get('network_id'),
        'ip_address': request.form.get('ip_address', '').strip(),
        'username': request.form.get('username', '').strip(),
        'password': request.form.get('password', ''),
        'api_port': request.form.get('api_port', 8728, type=int),
        'location': request.form.get('location', '').strip(),
        'description': request.form.get('description', '').strip(),
        'is_active': True,
    }

    # Validate required fields
    errors = []
    if not form_data['name']:
        errors.append('Router name is required')
    if not form_data['network_id']:
        errors.append('Network selection is required')
    if not form_data['ip_address']:
        errors.append('IP address is required')
    if not form_data['username']:
        errors.append('Username is required')
    if not form_data['password']:
        errors.append('Password is required')

    if errors:
        for error in errors:
            flash(error, 'danger')
        return render_template(
            'web/router/create.html',
            organization=current_organization,
            user=current_user,
            networks=networks,
            form_data=form_data,
        )

    try:
        result = router_service.create_router(
            organization_id=current_organization.id,
            network_id=UUID(form_data['network_id']),
            data=form_data,
        )

        router = result.get('router')
        auto_configured = result.get('auto_configured', False)

        if auto_configured:
            flash(
                f'Router "{router.name}" created and RADIUS configured '
                f'automatically!',
                'success'
            )
        else:
            flash(
                f'Router "{router.name}" created but RADIUS auto-configuration '
                f'failed. Please configure manually.',
                'warning'
            )
            # Store manual instructions for display
            if result.get('manual_config_instructions'):
                session['radius_instructions'] = result[
                    'manual_config_instructions'
                ]
            if result.get('radius_secret'):
                session['new_radius_secret'] = result['radius_secret']

        # Optionally test connection
        if request.form.get('test_connection') == 'true':
            try:
                test_result = router_service.test_connection(
                    router.id, current_organization.id
                )
                if test_result.get('success'):
                    flash(
                        'Connection test successful! Router is online.',
                        'success'
                    )
                else:
                    flash(
                        'Router added but connection test failed. '
                        'You can test again later.',
                        'warning'
                    )
            except Exception as e:
                flash(
                    f'Router added but connection test failed: {e}',
                    'warning'
                )

        return redirect(url_for('router_web.show',
                                org_id=org_id, router_id=router.id))

    except Exception as e:
        logger.error(f"Error creating router: {e}", exc_info=True)
        flash(f'Error creating router: {str(e)}', 'danger')
        return render_template(
            'web/router/create.html',
            organization=current_organization,
            user=current_user,
            networks=networks,
            form_data=form_data,
        )

# SHOW ROUTER DETAIL
@router_web_bp.route('/<router_id>')
@web_router_access_required
def show(org_id, router_id, current_user=None, current_organization=None):
    """
    GET /organization/<org_id>/routers/<router_id>

    Router detail page with WireGuard setup wizard, hotspot/PPPoE servers,
    RADIUS status, and health monitoring.
    """
    try:
        from flask import current_app
        
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)

        # Get network name
        network_name = None
        if router.network_id:
            network = network_service.get_network(
                router.network_id, current_organization.id
            )
            network_name = network.name if network else None

        # Get hotspot servers via repository (org-scoped)
        hotspot_servers = hotspot_repo.get_by_router(
            router_uuid, current_organization.id
        )

        # Get PPPoE servers via repository (org-scoped)
        pppoe_servers = pppoe_repo.get_by_router(
            router_uuid, current_organization.id
        )

        # Get connection status for health display
        connection_status = router_service.get_connection_status(
            router_uuid, current_organization.id
        )

        # ═══════════════════════════════════════════════════════════
        # BUILD WIREGUARD SETUP SCRIPT
        # Shown when router needs WireGuard configuration
        # ═══════════════════════════════════════════════════════════
        setup_script = None

        if router.status in ['pending_wireguard', 'unknown', 'offline', 'error'] or not router.wireguard_ip:
            vps_pubkey = current_app.config.get(
                'VPS_WIREGUARD_PUBLIC_KEY',
                '274kTJCdNISjJEBMLP9SuqaMyQ8GkDSqjXLttDgNsz4='
            )
            vps_endpoint = current_app.config.get(
                'VPS_WIREGUARD_ENDPOINT',
                '163.245.217.16:51820'
            )
            wg_ip = router.wireguard_ip or '10.0.1.10'
            radius_secret = router.radius_secret or 'YOUR_GENERATED_SECRET'

            setup_script = {
                'title': 'WireGuard VPN Setup',
                'description': 'Copy and paste ALL commands below into your MikroTik terminal in order',
                'steps': [
                    {
                        'step': 1,
                        'title': 'Create WireGuard Interface',
                        'description': 'Creates the VPN tunnel interface on your MikroTik',
                        'commands': [
                            '/interface wireguard',
                            'add listen-port=51820 name=wg-to-vps'
                        ]
                    },
                    {
                        'step': 2,
                        'title': 'Connect to ISP Platform (VPS)',
                        'description': f'Establishes secure tunnel to the platform WireGuard server at {vps_endpoint}',
                        'commands': [
                            '/interface wireguard peers',
                            f'add allowed-address=10.0.0.0/16 endpoint-address=163.245.217.16 endpoint-port=51820 interface=wg-to-vps persistent-keepalive=25 public-key="{vps_pubkey}"'
                        ]
                    },
                    {
                        'step': 3,
                        'title': 'Assign Router IP Address',
                        'description': f'This IP ({wg_ip}) is how the platform will reach your router through the VPN tunnel',
                        'commands': [
                            '/ip address',
                            f'add address={wg_ip}/16 interface=wg-to-vps network=10.0.0.0'
                        ]
                    },
                    {
                        'step': 4,
                        'title': 'Configure RADIUS Authentication',
                        'description': 'Points your router to the platform RADIUS server at 10.0.0.1 through the VPN tunnel',
                        'commands': [
                            f'/radius add address=10.0.0.1 secret="{radius_secret}" service=hotspot,ppp authentication-port=1812 accounting-port=1813 timeout=3000',
                            '/ip hotspot set [find] radius=yes',
                            '/ip hotspot profile set [find] use-radius=yes',
                            '/ppp profile set [find] use-radius=yes',
                            '/radius incoming set accept=yes'
                        ]
                    },
                    {
                        'step': 99,
                        'title': '✓ Setup Complete — Verify Connection',
                        'description': 'After pasting all commands above, click "Test Connection" to verify the tunnel is active and your router is online.'
                    }
                ]
            }

        # Check for one-time messages from session
        new_secret = session.pop('new_radius_secret', None)
        radius_instructions = session.pop('radius_instructions', None)

        return render_template(
            'web/router/show.html',
            organization=current_organization,
            user=current_user,
            router=router,
            network_name=network_name,
            hotspot_servers=hotspot_servers,
            pppoe_servers=pppoe_servers,
            connection_status=connection_status,
            context=_get_router_context(current_organization.id),
            setup_script=setup_script,
            new_secret=new_secret,
            radius_instructions=radius_instructions,
        )

    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error fetching router {router_id}: {e}", exc_info=True)
        flash('Router not found', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))

# EDIT ROUTER
@router_web_bp.route('/<router_id>/edit', methods=['GET', 'POST'])
@web_router_access_required
def edit(org_id, router_id, current_user=None, current_organization=None):
    """
    GET/POST /organization/<org_id>/routers/<router_id>/edit

    Edit router information.
    """
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        networks = network_service.get_organization_networks(
            current_organization.id, 0, 100
        )

        if request.method == 'GET':
            # Check for one-time secret from regeneration
            new_secret = session.pop('new_radius_secret', None)

            return render_template(
                'web/router/edit.html',
                organization=current_organization,
                user=current_user,
                router=router,
                networks=networks,
                new_secret=new_secret,
            )

        # ---------------------------------------------------------------------
        # POST — Update router
        # ---------------------------------------------------------------------
        data = {}

        # Only include fields that were actually submitted
        name = request.form.get('name', '').strip()
        if name:
            data['name'] = name

        network_id = request.form.get('network_id')
        if network_id:
            try:
                data['network_id'] = UUID(network_id)
            except ValueError:
                flash('Invalid network selection', 'danger')
                return render_template(
                    'web/router/edit.html',
                    organization=current_organization,
                    user=current_user,
                    router=router,
                    networks=networks,
                )

        ip_address = request.form.get('ip_address', '').strip()
        if ip_address:
            data['ip_address'] = ip_address

        username = request.form.get('username', '').strip()
        if username:
            data['username'] = username

        password = request.form.get('password')
        if password:
            data['password'] = password

        api_port = request.form.get('api_port', type=int)
        if api_port:
            data['api_port'] = api_port

        location = request.form.get('location', '').strip()
        data['location'] = location if location else None

        description = request.form.get('description', '').strip()
        data['description'] = description if description else None

        data['is_active'] = request.form.get('is_active') == 'true'

        if data:
            updated_router = router_service.update_router(
                router_uuid, current_organization.id, data
            )
            flash(
                f'Router "{updated_router.name}" updated successfully!',
                'success'
            )
        else:
            flash('No changes submitted', 'info')

        return redirect(url_for('router_web.show',
                                org_id=org_id, router_id=router_id))

    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error updating router {router_id}: {e}", exc_info=True)
        flash(f'Error updating router: {str(e)}', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))

# DELETE ROUTER
@router_web_bp.route('/<router_id>/delete', methods=['POST'])
@web_router_access_required
def delete(org_id, router_id, current_user=None, current_organization=None):
    """
    POST /organization/<org_id>/routers/<router_id>/delete

    Delete or deactivate a router.
    """
    try:
        router_uuid = UUID(router_id)
        soft = request.form.get('soft', 'true') == 'true'
        router_service.delete_router(
            router_uuid, current_organization.id, soft_delete=soft
        )
        flash(
            'Router deactivated successfully!'
            if soft
            else 'Router permanently deleted!',
            'success'
        )

    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error deleting router {router_id}: {e}", exc_info=True)
        flash(f'Error deleting router: {str(e)}', 'danger')

    return redirect(url_for('router_web.index', org_id=org_id))

# CONNECTION TEST
@router_web_bp.route('/<router_id>/test', methods=['POST'])
@web_router_access_required
def test_connection(org_id, router_id, current_user=None, current_organization=None):
    """
    POST /organization/<org_id>/routers/<router_id>/test

    Test connection to an existing router.
    """
    try:
        router_uuid = UUID(router_id)
        result = router_service.test_connection(
            router_uuid, current_organization.id
        )

        if result.get('success'):
            flash(
                f'Connection test successful! '
                f'Router is online. '
                f'Version: {result.get("router_info", {}).get("version", "Unknown")}',
                'success'
            )
        else:
            flash(
                f'Connection test failed: {result.get("error", "Unknown error")}',
                'danger'
            )

    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error testing router {router_id}: {e}", exc_info=True)
        flash(f'Connection test failed: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=router_id))

# DISCOVERY
@router_web_bp.route('/<router_id>/discover', methods=['POST'])
@web_router_access_required
def discover(org_id, router_id, current_user=None, current_organization=None):
    """
    POST /organization/<org_id>/routers/<router_id>/discover

    Auto-discover router capabilities.
    """
    try:
        router_uuid = UUID(router_id)
        result = router_service.discover_router(
            router_uuid, current_organization.id
        )

        if result.get('success'):
            flash(
                f'Discovery successful! Router identified via '
                f'{result.get("method")}. '
                f'Model: {result.get("info", {}).get("model", "Unknown")}',
                'success'
            )
        else:
            flash(
                'Discovery failed. Router may be offline or unreachable.',
                'warning'
            )

    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error discovering router {router_id}: {e}", exc_info=True)
        flash(f'Discovery failed: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=router_id))

# SYNC
@router_web_bp.route('/<router_id>/sync', methods=['POST'])
@web_router_access_required
def sync(org_id, router_id, current_user=None, current_organization=None):
    """
    POST /organization/<org_id>/routers/<router_id>/sync

    Sync router configuration (hotspot and PPPoE servers).
    """
    try:
        router_uuid = UUID(router_id)
        result = router_service.sync_router(
            router_uuid, current_organization.id
        )

        flash(
            f'Sync successful! '
            f'Synced {result.get("hotspot_synced", 0)} hotspot(s) and '
            f'{result.get("pppoe_synced", 0)} PPPoE server(s).',
            'success'
        )

    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error syncing router {router_id}: {e}", exc_info=True)
        flash(f'Sync failed: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=router_id))

# RADIUS — REGENERATE SECRET
@router_web_bp.route('/<router_id>/radius/regenerate', methods=['POST'])
@web_router_access_required
def regenerate_radius_secret(
    org_id, router_id, current_user=None, current_organization=None
):
    """
    POST /organization/<org_id>/routers/<router_id>/radius/regenerate

    Regenerate the RADIUS shared secret for a router.
    Updates both the Router record and the linked NAS entry.
    Requires manual reconfiguration of the MikroTik afterwards.
    """
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(
            router_uuid, current_organization.id
        )

        # Generate new secret
        new_secret = secrets.token_urlsafe(32)

        # Update router record via service
        router_service.update_router(
            router_uuid, current_organization.id, {
                'radius_secret': new_secret,
                'radius_config_status': 'pending',
                'radius_configured_at': None,
                'auto_config_attempts': 0,
                'last_config_error': None,
            }
        )

        # Update linked NAS entry
        if router.nas_entry_id:
            nas = NAS.query.filter_by(
                id=router.nas_entry_id,
                organization_id=current_organization.id,
            ).first()
            if nas:
                nas.secret = new_secret
                db.session.commit()
                logger.info(
                    f"NAS entry {nas.id} secret updated for router {router_id}"
                )
            else:
                logger.warning(
                    f"NAS entry {router.nas_entry_id} not found for "
                    f"router {router_id}"
                )

        # Store new secret in session for one-time display
        session['new_radius_secret'] = new_secret

        flash(
            'New RADIUS secret generated successfully! '
            'Please reconfigure your MikroTik router with the new secret '
            'or use the "Retry RADIUS Config" button.',
            'success'
        )

        logger.warning(
            f"RADIUS secret regenerated for router {router_id} "
            f"by user {current_user.id}"
        )

    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(
            f"Error regenerating RADIUS secret for router "
            f"{router_id}: {e}",
            exc_info=True,
        )
        flash(f'Failed to regenerate RADIUS secret: {str(e)}', 'danger')

    return redirect(url_for('router_web.edit',
                            org_id=org_id, router_id=router_id))

# RADIUS — RETRY CONFIGURATION
@router_web_bp.route('/<router_id>/radius/retry', methods=['POST'])
@web_router_access_required
def retry_radius_config(
    org_id, router_id, current_user=None, current_organization=None
):
    """
    POST /organization/<org_id>/routers/<router_id>/radius/retry

    Retry automatic RADIUS configuration on the MikroTik router.
    Uses the stored RADIUS secret.
    """
    try:
        router_uuid = UUID(router_id)
        result = router_service.retry_radius_configuration(
            router_uuid, current_organization.id
        )

        if result.get('success'):
            flash(
                'RADIUS configuration successful! Router is now configured '
                'for automatic authentication.',
                'success'
            )
        else:
            flash(
                f'RADIUS configuration failed: {result.get("message")}. '
                f'Please configure manually.',
                'warning'
            )
            if result.get('manual_config_instructions'):
                session['radius_instructions'] = result[
                    'manual_config_instructions'
                ]

    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(
            f"Error retrying RADIUS config for router {router_id}: {e}",
            exc_info=True,
        )
        flash(f'Failed to configure RADIUS: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=router_id))

# HOTSPOT SERVER — CREATE
@router_web_bp.route('/hotspot-servers/create', methods=['GET', 'POST'])
@web_router_access_required
def create_hotspot(org_id, current_user=None, current_organization=None):
    """
    GET/POST /organization/<org_id>/routers/hotspot-servers/create?router_id=<id>

    Create a hotspot server for a router.
    """
    router_id = request.args.get('router_id')

    if not router_id:
        flash('Router ID is required', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))

    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(
            router_uuid, current_organization.id
        )

        if request.method == 'GET':
            return render_template(
                'web/router/hotspot_create.html',
                organization=current_organization,
                user=current_user,
                router=router,
            )

        # ---------------------------------------------------------------------
        # POST — Create hotspot server via repository (org-scoped)
        # ---------------------------------------------------------------------
        hotspot_data = {
            'organization_id': current_organization.id,
            'router_id': router_uuid,
            'name': request.form.get('name', '').strip(),
            'hotspot_id': request.form.get('hotspot_id', '').strip(),
            'interface': request.form.get('interface', '').strip() or None,
            'address_pool': request.form.get('address_pool', '').strip() or None,
            'dns_name': request.form.get('dns_name', '').strip() or None,
            'idle_timeout': request.form.get('idle_timeout', 300, type=int),
            'session_timeout': request.form.get(
                'session_timeout', 86400, type=int
            ),
            'keepalive_timeout': request.form.get(
                'keepalive_timeout', 120, type=int
            ),
            'is_active': request.form.get('is_active') == 'true',
        }

        if not hotspot_data['name'] or not hotspot_data['hotspot_id']:
            flash('Name and Hotspot ID are required', 'danger')
            return render_template(
                'web/router/hotspot_create.html',
                organization=current_organization,
                user=current_user,
                router=router,
            )

        hotspot_repo.create(hotspot_data)

        flash('Hotspot server created successfully!', 'success')

    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error creating hotspot server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=router_id))

# HOTSPOT SERVER — EDIT
@router_web_bp.route(
    '/hotspot-servers/<hotspot_id>/edit', methods=['GET', 'POST']
)
@web_router_access_required
def edit_hotspot(org_id, hotspot_id, current_user=None, current_organization=None):
    """
    GET/POST /organization/<org_id>/routers/hotspot-servers/<hotspot_id>/edit

    Edit a hotspot server.
    """
    try:
        hotspot_uuid = UUID(hotspot_id)
        hotspot = hotspot_repo.get_by_id(
            hotspot_uuid, current_organization.id
        )

        if not hotspot:
            flash('Hotspot server not found', 'danger')
            return redirect(url_for('router_web.index', org_id=org_id))

        router = router_service.get_router(
            hotspot.router_id, current_organization.id
        )

        if request.method == 'GET':
            return render_template(
                'web/router/hotspot_edit.html',
                organization=current_organization,
                user=current_user,
                router=router,
                hotspot=hotspot,
            )

        # ---------------------------------------------------------------------
        # POST — Update hotspot server
        # ---------------------------------------------------------------------
        update_data = {
            'name': request.form.get('name', '').strip(),
            'hotspot_id': request.form.get('hotspot_id', '').strip(),
            'interface': request.form.get('interface', '').strip() or None,
            'address_pool': request.form.get('address_pool', '').strip() or None,
            'dns_name': request.form.get('dns_name', '').strip() or None,
            'idle_timeout': request.form.get('idle_timeout', 300, type=int),
            'session_timeout': request.form.get(
                'session_timeout', 86400, type=int
            ),
            'keepalive_timeout': request.form.get(
                'keepalive_timeout', 120, type=int
            ),
            'is_active': request.form.get('is_active') == 'true',
        }

        hotspot_repo.update(hotspot_uuid, current_organization.id, update_data)
        flash('Hotspot server updated successfully!', 'success')

    except ValueError:
        flash('Invalid ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error editing hotspot server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=hotspot.router_id))

# PPPoE SERVER — CREATE
@router_web_bp.route('/pppoe-servers/create', methods=['GET', 'POST'])
@web_router_access_required
def create_pppoe(org_id, current_user=None, current_organization=None):
    """
    GET/POST /organization/<org_id>/routers/pppoe-servers/create?router_id=<id>

    Create a PPPoE server for a router.
    """
    router_id = request.args.get('router_id')

    if not router_id:
        flash('Router ID is required', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))

    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(
            router_uuid, current_organization.id
        )

        if request.method == 'GET':
            return render_template(
                'web/router/pppoe_create.html',
                organization=current_organization,
                user=current_user,
                router=router,
            )

        # ---------------------------------------------------------------------
        # POST — Create PPPoE server via repository (org-scoped)
        # ---------------------------------------------------------------------
        pppoe_data = {
            'organization_id': current_organization.id,
            'router_id': router_uuid,
            'name': request.form.get('name', '').strip(),
            'interface': request.form.get('interface', '').strip(),
            'service_name': request.form.get('service_name', '').strip(),
            'mtu': request.form.get('mtu', 1492, type=int),
            'max_sessions': request.form.get('max_sessions', 100, type=int),
            'authentication_protocols': ['chap', 'mschapv2'],
            'is_active': request.form.get('is_active') == 'true',
        }

        if not pppoe_data['name']:
            flash('PPPoE server name is required', 'danger')
            return render_template(
                'web/router/pppoe_create.html',
                organization=current_organization,
                user=current_user,
                router=router,
            )

        pppoe_repo.create(pppoe_data)

        flash('PPPoE server created successfully!', 'success')

    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error creating PPPoE server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=router_id))

# PPPoE SERVER — EDIT
@router_web_bp.route(
    '/pppoe-servers/<pppoe_id>/edit', methods=['GET', 'POST']
)
@web_router_access_required
def edit_pppoe(org_id, pppoe_id, current_user=None, current_organization=None):
    """
    GET/POST /organization/<org_id>/routers/pppoe-servers/<pppoe_id>/edit

    Edit a PPPoE server.
    """
    try:
        pppoe_uuid = UUID(pppoe_id)
        pppoe = pppoe_repo.get_by_id(pppoe_uuid, current_organization.id)

        if not pppoe:
            flash('PPPoE server not found', 'danger')
            return redirect(url_for('router_web.index', org_id=org_id))

        router = router_service.get_router(
            pppoe.router_id, current_organization.id
        )

        if request.method == 'GET':
            return render_template(
                'web/router/pppoe_edit.html',
                organization=current_organization,
                user=current_user,
                router=router,
                pppoe=pppoe,
            )

        # ---------------------------------------------------------------------
        # POST — Update PPPoE server
        # ---------------------------------------------------------------------
        update_data = {
            'name': request.form.get('name', '').strip(),
            'interface': request.form.get('interface', '').strip(),
            'service_name': request.form.get('service_name', '').strip(),
            'mtu': request.form.get('mtu', 1492, type=int),
            'max_sessions': request.form.get('max_sessions', 100, type=int),
            'is_active': request.form.get('is_active') == 'true',
        }

        pppoe_repo.update(pppoe_uuid, current_organization.id, update_data)
        flash('PPPoE server updated successfully!', 'success')

    except ValueError:
        flash('Invalid ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error editing PPPoE server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('router_web.show',
                            org_id=org_id, router_id=pppoe.router_id))