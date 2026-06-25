"""
Router Web Routes
=================
Web interface routes for router management.
All routes are scoped to an organization via the URL prefix.

Route prefix: /organization/<org_id>/routers
"""

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, session, request, abort,
)
from functools import wraps
from uuid import UUID
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
from app.integrations.wireguard.manager import WireGuardManager


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
wg_manager = WireGuardManager()


# =============================================================================
# DECORATOR
# =============================================================================

def web_router_access_required(f):
    """Validate organization access and load user/org context."""
    @wraps(f)
    def decorated_function(org_id, *args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            flash('Please login to continue', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('web.login'))

        user = user_repo.get_by_id(user_id)
        if not user or not user.is_active:
            session.clear()
            flash('User account not found or inactive', 'danger')
            return redirect(url_for('web.login'))

        try:
            org_uuid = UUID(org_id)
        except (ValueError, AttributeError):
            logger.warning(f"Invalid org_id format in URL: {org_id}")
            abort(404)

        user_orgs = organization_service.get_organizations_by_user(user.id)
        if org_uuid not in [org.id for org in user_orgs]:
            logger.warning(f"User {user.id} attempted to access organization {org_id} without permission")
            flash('You do not have access to this organization', 'danger')
            return redirect(url_for('web.dashboard'))

        organization = organization_service.get_organization(org_uuid)
        if not organization:
            flash('Organization not found', 'danger')
            return redirect(url_for('web.dashboard'))

        kwargs['current_user'] = user
        kwargs['current_organization'] = organization
        return f(org_id, *args, **kwargs)
    return decorated_function


def _get_router_context(organization_id):
    """Get summary counts for the router sidebar."""
    return {
        'total': router_repo.count_by_organization(organization_id),
        'online': router_repo.count_by_organization(organization_id, status='online'),
        'offline': router_repo.count_by_organization(organization_id, status='offline'),
        'radius_pending': router_repo.count_radius_pending(organization_id),
        'radius_failed': router_repo.count_radius_failed(organization_id),
    }


# =============================================================================
# LIST ROUTERS
# =============================================================================

@router_web_bp.route('/')
@web_router_access_required
def index(org_id, current_user=None, current_organization=None):
    """GET /organization/<org_id>/routers/ — List all routers."""
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    skip = (page - 1) * per_page

    status_filter = request.args.get('status')
    network_id = request.args.get('network_id')
    radius_config_status = request.args.get('radius_config_status')

    network_uuid = None
    if network_id:
        try: network_uuid = UUID(network_id)
        except ValueError: flash('Invalid network filter', 'warning'); network_id = None

    networks = network_service.get_organization_networks(current_organization.id, 0, 100)

    routers = router_service.get_routers_by_organization(
        organization_id=current_organization.id, skip=skip, limit=per_page,
        status=status_filter, network_id=network_uuid, radius_config_status=radius_config_status,
    )

    total = router_repo.count_by_organization(current_organization.id, status=status_filter, radius_config_status=radius_config_status)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 0

    return render_template(
        'web/router/index.html',
        organization=current_organization, user=current_user,
        routers=routers, networks=networks,
        pagination={'total': total, 'page': page, 'per_page': per_page, 'pages': total_pages, 'has_next': page < total_pages, 'has_prev': page > 1},
        context=_get_router_context(current_organization.id),
        filters={'status': status_filter, 'network_id': network_id, 'radius_config_status': radius_config_status},
        new_secret=session.pop('new_radius_secret', None),
        radius_instructions=session.pop('radius_instructions', None),
    )


# =============================================================================
# CREATE ROUTER
# =============================================================================

@router_web_bp.route('/create', methods=['GET', 'POST'])
@web_router_access_required
def create(org_id, current_user=None, current_organization=None):
    """GET/POST /organization/<org_id>/routers/create"""
    networks = network_service.get_organization_networks(current_organization.id, 0, 100)

    if request.method == 'GET':
        return render_template('web/router/create.html', organization=current_organization, user=current_user, networks=networks, form_data=None)

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

    errors = []
    if not form_data['name']: errors.append('Router name is required')
    if not form_data['network_id']: errors.append('Network selection is required')
    if not form_data['ip_address']: errors.append('IP address is required')
    if not form_data['username']: errors.append('Username is required')
    if not form_data['password']: errors.append('Password is required')

    if errors:
        for error in errors: flash(error, 'danger')
        return render_template('web/router/create.html', organization=current_organization, user=current_user, networks=networks, form_data=form_data)

    try:
        result = router_service.create_router(
            organization_id=current_organization.id,
            network_id=UUID(form_data['network_id']),
            data=form_data,
        )
        router = result.get('router')
        flash(f'Router "{router.name}" created successfully! Copy the setup script below into your MikroTik terminal.', 'success')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router.id))
    except Exception as e:
        logger.error(f"Error creating router: {e}", exc_info=True)
        flash(f'Error creating router: {str(e)}', 'danger')
        return render_template('web/router/create.html', organization=current_organization, user=current_user, networks=networks, form_data=form_data)


# =============================================================================
# SHOW ROUTER DETAIL
# =============================================================================

@router_web_bp.route('/<router_id>')
@web_router_access_required
def show(org_id, router_id, current_user=None, current_organization=None):
    """GET /organization/<org_id>/routers/<router_id> — Router detail page."""
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)

        network_name = None
        if router.network_id:
            network = network_service.get_network(router.network_id, current_organization.id)
            network_name = network.name if network else None

        hotspot_servers = hotspot_repo.get_by_router(router_uuid, current_organization.id)
        pppoe_servers = pppoe_repo.get_by_router(router_uuid, current_organization.id)
        connection_status = router_service.get_connection_status(router_uuid, current_organization.id)

        # ═══════════════════════════════════════════════════════════
        # BUILD SETUP SCRIPT — Uses WireGuardManager for CORRECT commands
        # ═══════════════════════════════════════════════════════════
        setup_script = None

        if router.status in ['pending_wireguard', 'unknown', 'offline', 'error'] or not router.wireguard_ip:
            setup_script = wg_manager.generate_mikrotik_setup_script(
                wireguard_ip=router.wireguard_ip or 'PENDING',
                mikrotik_private_key='YOUR_PRIVATE_KEY_FROM_CREATION',
                radius_secret=router.radius_secret or 'PENDING',
                include_radius=True,
            )

        return render_template(
            'web/router/show.html',
            organization=current_organization, user=current_user,
            router=router, network_name=network_name,
            hotspot_servers=hotspot_servers, pppoe_servers=pppoe_servers,
            connection_status=connection_status,
            context=_get_router_context(current_organization.id),
            setup_script=setup_script,
            new_secret=session.pop('new_radius_secret', None),
            radius_instructions=session.pop('radius_instructions', None),
        )
    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error fetching router {router_id}: {e}", exc_info=True)
        flash('Router not found', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))


# =============================================================================
# EDIT ROUTER
# =============================================================================

@router_web_bp.route('/<router_id>/edit', methods=['GET', 'POST'])
@web_router_access_required
def edit(org_id, router_id, current_user=None, current_organization=None):
    """GET/POST /organization/<org_id>/routers/<router_id>/edit"""
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        networks = network_service.get_organization_networks(current_organization.id, 0, 100)

        if request.method == 'GET':
            return render_template('web/router/edit.html', organization=current_organization, user=current_user, router=router, networks=networks, new_secret=session.pop('new_radius_secret', None))

        data = {}
        if request.form.get('name', '').strip(): data['name'] = request.form.get('name', '').strip()
        if request.form.get('network_id'):
            try: data['network_id'] = UUID(request.form.get('network_id'))
            except ValueError: flash('Invalid network selection', 'danger'); return render_template('web/router/edit.html', organization=current_organization, user=current_user, router=router, networks=networks)
        if request.form.get('ip_address', '').strip(): data['ip_address'] = request.form.get('ip_address', '').strip()
        if request.form.get('username', '').strip(): data['username'] = request.form.get('username', '').strip()
        if request.form.get('password'): data['password'] = request.form.get('password')
        if request.form.get('api_port', type=int): data['api_port'] = request.form.get('api_port', type=int)
        data['location'] = request.form.get('location', '').strip() or None
        data['description'] = request.form.get('description', '').strip() or None
        data['is_active'] = request.form.get('is_active') == 'true'

        if data:
            updated = router_service.update_router(router_uuid, current_organization.id, data)
            flash(f'Router "{updated.name}" updated successfully!', 'success')
        else:
            flash('No changes submitted', 'info')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))
    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error updating router {router_id}: {e}", exc_info=True)
        flash(f'Error updating router: {str(e)}', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))


# =============================================================================
# DELETE ROUTER
# =============================================================================

@router_web_bp.route('/<router_id>/delete', methods=['POST'])
@web_router_access_required
def delete(org_id, router_id, current_user=None, current_organization=None):
    """POST /organization/<org_id>/routers/<router_id>/delete"""
    try:
        router_uuid = UUID(router_id)
        soft = request.form.get('soft', 'true') == 'true'
        router_service.delete_router(router_uuid, current_organization.id, soft_delete=soft)
        flash('Router deactivated successfully!' if soft else 'Router permanently deleted!', 'success')
    except ValueError: flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error deleting router {router_id}: {e}", exc_info=True)
        flash(f'Error deleting router: {str(e)}', 'danger')
    return redirect(url_for('router_web.index', org_id=org_id))


# =============================================================================
# CONNECTION TEST
# =============================================================================

@router_web_bp.route('/<router_id>/test', methods=['POST'])
@web_router_access_required
def test_connection(org_id, router_id, current_user=None, current_organization=None):
    """POST /organization/<org_id>/routers/<router_id>/test"""
    try:
        router_uuid = UUID(router_id)
        result = router_service.test_connection(router_uuid, current_organization.id)
        if result.get('success'):
            flash(f'Connection test successful! Router is online. Version: {result.get("router_info", {}).get("version", "Unknown")}', 'success')
        else:
            flash(f'Connection test failed: {result.get("error", "Unknown error")}', 'danger')
    except ValueError: flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error testing router {router_id}: {e}", exc_info=True)
        flash(f'Connection test failed: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


# =============================================================================
# DISCOVERY
# =============================================================================

@router_web_bp.route('/<router_id>/discover', methods=['POST'])
@web_router_access_required
def discover(org_id, router_id, current_user=None, current_organization=None):
    """POST /organization/<org_id>/routers/<router_id>/discover"""
    try:
        router_uuid = UUID(router_id)
        result = router_service.discover_router(router_uuid, current_organization.id)
        if result.get('success'):
            flash(f'Discovery successful! Model: {result.get("info", {}).get("board_name", "Unknown")}', 'success')
        else:
            flash('Discovery failed. Router may be offline.', 'warning')
    except ValueError: flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error discovering router {router_id}: {e}", exc_info=True)
        flash(f'Discovery failed: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


# =============================================================================
# SYNC
# =============================================================================

@router_web_bp.route('/<router_id>/sync', methods=['POST'])
@web_router_access_required
def sync(org_id, router_id, current_user=None, current_organization=None):
    """POST /organization/<org_id>/routers/<router_id>/sync"""
    try:
        router_uuid = UUID(router_id)
        result = router_service.sync_router(router_uuid, current_organization.id)
        flash(f'Sync successful! {result.get("hotspot_synced", 0)} hotspot(s) and {result.get("pppoe_synced", 0)} PPPoE server(s).', 'success')
    except ValueError: flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error syncing router {router_id}: {e}", exc_info=True)
        flash(f'Sync failed: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


# =============================================================================
# RADIUS — REGENERATE SECRET
# =============================================================================

@router_web_bp.route('/<router_id>/radius/regenerate', methods=['POST'])
@web_router_access_required
def regenerate_radius_secret(org_id, router_id, current_user=None, current_organization=None):
    """POST /organization/<org_id>/routers/<router_id>/radius/regenerate"""
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        new_secret = secrets.token_urlsafe(32)
        router_service.update_router(router_uuid, current_organization.id, {
            'radius_secret': new_secret, 'radius_config_status': 'pending',
            'radius_configured_at': None, 'auto_config_attempts': 0, 'last_config_error': None,
        })
        if router.nas_entry_id:
            nas = NAS.query.filter_by(id=router.nas_entry_id, organization_id=current_organization.id).first()
            if nas: nas.secret = new_secret; db.session.commit()
        session['new_radius_secret'] = new_secret
        flash('New RADIUS secret generated successfully! Please reconfigure your MikroTik router.', 'success')
        logger.warning(f"RADIUS secret regenerated for router {router_id} by user {current_user.id}")
    except ValueError: flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error regenerating RADIUS secret: {e}", exc_info=True)
        flash(f'Failed to regenerate RADIUS secret: {str(e)}', 'danger')
    return redirect(url_for('router_web.edit', org_id=org_id, router_id=router_id))


# =============================================================================
# RADIUS — RETRY CONFIGURATION
# =============================================================================

@router_web_bp.route('/<router_id>/radius/retry', methods=['POST'])
@web_router_access_required
def retry_radius_config(org_id, router_id, current_user=None, current_organization=None):
    """POST /organization/<org_id>/routers/<router_id>/radius/retry"""
    try:
        router_uuid = UUID(router_id)
        result = router_service.retry_radius_configuration(router_uuid, current_organization.id)
        if result.get('success'): flash('RADIUS configuration successful!', 'success')
        else: flash(f'RADIUS configuration failed: {result.get("message")}. Please configure manually.', 'warning')
    except ValueError: flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error retrying RADIUS config: {e}", exc_info=True)
        flash(f'Failed to configure RADIUS: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


# =============================================================================
# HOTSPOT SERVER — CREATE
# =============================================================================

@router_web_bp.route('/hotspot-servers/create', methods=['GET', 'POST'])
@web_router_access_required
def create_hotspot(org_id, current_user=None, current_organization=None):
    """GET/POST — Create hotspot server."""
    router_id = request.args.get('router_id')
    if not router_id: flash('Router ID is required', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        if request.method == 'GET': return render_template('web/router/hotspot_create.html', organization=current_organization, user=current_user, router=router)
        hs_data = {
            'organization_id': current_organization.id, 'router_id': router_uuid,
            'name': request.form.get('name', '').strip(), 'hotspot_id': request.form.get('hotspot_id', '').strip(),
            'interface': request.form.get('interface', '').strip() or None,
            'address_pool': request.form.get('address_pool', '').strip() or None,
            'dns_name': request.form.get('dns_name', '').strip() or None,
            'idle_timeout': request.form.get('idle_timeout', 300, type=int),
            'session_timeout': request.form.get('session_timeout', 86400, type=int),
            'keepalive_timeout': request.form.get('keepalive_timeout', 120, type=int),
            'is_active': request.form.get('is_active') == 'true',
        }
        if not hs_data['name'] or not hs_data['hotspot_id']: flash('Name and Hotspot ID are required', 'danger'); return render_template('web/router/hotspot_create.html', organization=current_organization, user=current_user, router=router)
        hotspot_repo.create(hs_data)
        flash('Hotspot server created successfully!', 'success')
    except ValueError: flash('Invalid router ID format', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e: logger.error(f"Error creating hotspot server: {e}", exc_info=True); flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


@router_web_bp.route('/hotspot-servers/<hotspot_id>/edit', methods=['GET', 'POST'])
@web_router_access_required
def edit_hotspot(org_id, hotspot_id, current_user=None, current_organization=None):
    """GET/POST — Edit hotspot server."""
    try:
        hotspot_uuid = UUID(hotspot_id)
        hotspot = hotspot_repo.get_by_id(hotspot_uuid, current_organization.id)
        if not hotspot: flash('Hotspot server not found', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
        router = router_service.get_router(hotspot.router_id, current_organization.id)
        if request.method == 'GET': return render_template('web/router/hotspot_edit.html', organization=current_organization, user=current_user, router=router, hotspot=hotspot)
        update_data = {
            'name': request.form.get('name', '').strip(), 'hotspot_id': request.form.get('hotspot_id', '').strip(),
            'interface': request.form.get('interface', '').strip() or None,
            'address_pool': request.form.get('address_pool', '').strip() or None,
            'dns_name': request.form.get('dns_name', '').strip() or None,
            'idle_timeout': request.form.get('idle_timeout', 300, type=int),
            'session_timeout': request.form.get('session_timeout', 86400, type=int),
            'keepalive_timeout': request.form.get('keepalive_timeout', 120, type=int),
            'is_active': request.form.get('is_active') == 'true',
        }
        hotspot_repo.update(hotspot_uuid, current_organization.id, update_data)
        flash('Hotspot server updated successfully!', 'success')
    except ValueError: flash('Invalid ID format', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e: logger.error(f"Error editing hotspot server: {e}", exc_info=True); flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=hotspot.router_id))


# =============================================================================
# PPPoE SERVER — CREATE
# =============================================================================

@router_web_bp.route('/pppoe-servers/create', methods=['GET', 'POST'])
@web_router_access_required
def create_pppoe(org_id, current_user=None, current_organization=None):
    """GET/POST — Create PPPoE server."""
    router_id = request.args.get('router_id')
    if not router_id: flash('Router ID is required', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        if request.method == 'GET': return render_template('web/router/pppoe_create.html', organization=current_organization, user=current_user, router=router)
        ps_data = {
            'organization_id': current_organization.id, 'router_id': router_uuid,
            'name': request.form.get('name', '').strip(), 'interface': request.form.get('interface', '').strip(),
            'service_name': request.form.get('service_name', '').strip(),
            'mtu': request.form.get('mtu', 1492, type=int), 'max_sessions': request.form.get('max_sessions', 100, type=int),
            'authentication_protocols': ['chap', 'mschapv2'], 'is_active': request.form.get('is_active') == 'true',
        }
        if not ps_data['name']: flash('PPPoE server name is required', 'danger'); return render_template('web/router/pppoe_create.html', organization=current_organization, user=current_user, router=router)
        pppoe_repo.create(ps_data)
        flash('PPPoE server created successfully!', 'success')
    except ValueError: flash('Invalid router ID format', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e: logger.error(f"Error creating PPPoE server: {e}", exc_info=True); flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


@router_web_bp.route('/pppoe-servers/<pppoe_id>/edit', methods=['GET', 'POST'])
@web_router_access_required
def edit_pppoe(org_id, pppoe_id, current_user=None, current_organization=None):
    """GET/POST — Edit PPPoE server."""
    try:
        pppoe_uuid = UUID(pppoe_id)
        pppoe = pppoe_repo.get_by_id(pppoe_uuid, current_organization.id)
        if not pppoe: flash('PPPoE server not found', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
        router = router_service.get_router(pppoe.router_id, current_organization.id)
        if request.method == 'GET': return render_template('web/router/pppoe_edit.html', organization=current_organization, user=current_user, router=router, pppoe=pppoe)
        update_data = {
            'name': request.form.get('name', '').strip(), 'interface': request.form.get('interface', '').strip(),
            'service_name': request.form.get('service_name', '').strip(),
            'mtu': request.form.get('mtu', 1492, type=int), 'max_sessions': request.form.get('max_sessions', 100, type=int),
            'is_active': request.form.get('is_active') == 'true',
        }
        pppoe_repo.update(pppoe_uuid, current_organization.id, update_data)
        flash('PPPoE server updated successfully!', 'success')
    except ValueError: flash('Invalid ID format', 'danger'); return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e: logger.error(f"Error editing PPPoE server: {e}", exc_info=True); flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('router_web.show', org_id=org_id, router_id=pppoe.router_id))