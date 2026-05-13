from flask import Blueprint, render_template, redirect, url_for, flash, session, request, abort
from functools import wraps
from uuid import UUID
from datetime import datetime

from app.core.logging.logger import logger
from app.modules.router.service import RouterService
from app.modules.router.repository import RouterRepository
from app.modules.network.service import NetworkService
from app.modules.organization.service import OrganizationService
from app.modules.auth.repository import UserRepository
from app.core.database.session import db
from app.models.nas import NAS

# Create web blueprint with organization ID in URL pattern
router_web_bp = Blueprint('router_web', __name__, url_prefix='/organization/<org_id>/routers')

# Initialize services
router_service = RouterService()
router_repo = RouterRepository()
network_service = NetworkService()
organization_service = OrganizationService()
user_repo = UserRepository()

# DECORATORS
def web_router_access_required(f):
    """Decorator to validate organization access and load context"""
    @wraps(f)
    def decorated_function(org_id, *args, **kwargs):
        # Check if user is logged in
        if not session.get('user_id'):
            flash('Please login to continue', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('web.login'))
        
        # Get user from database
        user = user_repo.get_by_id(session['user_id'])
        if not user or not user.is_active:
            session.clear()
            flash('User account not found or inactive', 'danger')
            return redirect(url_for('web.login'))
        
        # Validate organization ID format
        try:
            org_uuid = UUID(org_id)
        except ValueError:
            logger.warning(f"Invalid org_id format: {org_id}")
            abort(404)
        
        # Check if user belongs to this organization
        user_orgs = organization_service.get_organizations_by_user(user.id)
        if org_uuid not in [org.id for org in user_orgs]:
            logger.warning(f"User {user.id} attempted to access organization {org_id} without permission")
            flash('You do not have access to this organization', 'danger')
            return redirect(url_for('web.dashboard'))
        
        # Get organization details
        organization = organization_service.get_organization(org_uuid)
        
        # Make user and org available to template
        kwargs['current_user'] = user
        kwargs['current_organization'] = organization
        
        return f(org_id, *args, **kwargs)
    return decorated_function

# ROUTES
@router_web_bp.route('/')
@web_router_access_required
def index(org_id, current_user=None, current_organization=None):
    """List routers page"""
    
    # Get filters from query params
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    skip = (page - 1) * per_page
    
    status = request.args.get('status')
    network_id = request.args.get('network_id')
    
    # Convert network_id to UUID if provided
    network_uuid = None
    if network_id:
        try:
            network_uuid = UUID(network_id)
        except ValueError:
            pass
    
    # Get networks for filter dropdown
    networks = network_service.get_organization_networks(current_organization.id, 0, 100)
    
    # Fetch routers via service
    routers = router_service.get_routers_by_organization(
        organization_id=current_organization.id,
        skip=skip,
        limit=per_page,
        status=status,
        network_id=network_uuid
    )
    
    # Get total count
    total = router_repo.count_by_organization(current_organization.id, status=status)
    
    pagination = {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total else 0
    }
    
    return render_template(
        'web/router/index.html',
        organization=current_organization,
        user=current_user,
        routers=routers,
        networks=networks,
        pagination=pagination,
        filters={'status': status, 'network_id': network_id}
    )


@router_web_bp.route('/create', methods=['GET', 'POST'])
@web_router_access_required
def create(org_id, current_user=None, current_organization=None):
    """Create router page"""
    
    # Get networks for dropdown
    networks = network_service.get_organization_networks(current_organization.id, 0, 100)
    
    if request.method == 'GET':
        return render_template(
            'web/router/create.html',
            organization=current_organization,
            user=current_user,
            networks=networks,
            form_data=None
        )
    
    # POST - Create router
    form_data = {
        'name': request.form.get('name', '').strip(),
        'network_id': request.form.get('network_id'),
        'ip_address': request.form.get('ip_address', '').strip(),
        'username': request.form.get('username', '').strip(),
        'password': request.form.get('password', ''),
        'api_port': request.form.get('api_port', 8728, type=int),
        'location': request.form.get('location', '').strip(),
        'description': request.form.get('description', '').strip(),
        'is_active': request.form.get('is_active') == 'true'
    }
    
    try:
        # Validate required fields
        if not form_data['name']:
            flash('Router name is required', 'danger')
            return render_template('web/router/create.html', 
                                 organization=current_organization, user=current_user,
                                 networks=networks, form_data=form_data)
        
        if not form_data['network_id']:
            flash('Network selection is required', 'danger')
            return render_template('web/router/create.html',
                                 organization=current_organization, user=current_user,
                                 networks=networks, form_data=form_data)
        
        if not form_data['ip_address']:
            flash('IP address is required', 'danger')
            return render_template('web/router/create.html',
                                 organization=current_organization, user=current_user,
                                 networks=networks, form_data=form_data)
        
        if not form_data['username'] or not form_data['password']:
            flash('Username and password are required', 'danger')
            return render_template('web/router/create.html',
                                 organization=current_organization, user=current_user,
                                 networks=networks, form_data=form_data)
        
        # Create router - returns a DICT with router and metadata
        result = router_service.create_router(
            organization_id=current_organization.id,
            network_id=UUID(form_data['network_id']),
            data=form_data
        )
        
        # Extract router from result (it's a dict with 'router' key)
        router = result.get('router')
        auto_configured = result.get('auto_configured', False)
        
        flash(f'Router "{router.name}" created successfully!', 'success')
        
        # If auto-config failed, show warning with instructions
        if not auto_configured and result.get('radius_secret'):
            flash('RADIUS auto-configuration failed. Please configure manually using the instructions provided in the success modal.', 'warning')
        
        # Optionally test connection immediately (if not already tested)
        if request.form.get('test_connection') == 'true' or request.form.get('test_connection') == 'on':
            try:
                test_result = router_service.test_connection(router.id, current_organization.id)
                if test_result.get('success'):
                    flash('Connection test successful! Router is online.', 'success')
                else:
                    flash('Router added but connection test failed. You can test again later.', 'warning')
            except Exception as e:
                flash(f'Router added but connection test failed: {str(e)}', 'warning')
        
        return redirect(url_for('router_web.index', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating router: {e}", exc_info=True)
        flash(f'Error creating router: {str(e)}', 'danger')
        return render_template('web/router/create.html',
                             organization=current_organization, user=current_user,
                             networks=networks, form_data=form_data)


@router_web_bp.route('/<router_id>')
@web_router_access_required
def show(org_id, router_id, current_user=None, current_organization=None):
    """Router details page"""
    
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        
        # Get network name
        network_name = None
        if router.network_id:
            network = network_service.get_network(router.network_id, current_organization.id)
            network_name = network.name if network else None
        
        # Get hotspot servers
        hotspot_servers = router.hotspot_servers.all() if router.hotspot_servers else []
        
        # Get PPPoE servers
        pppoe_servers = router.pppoe_servers.all() if router.pppoe_servers else []
        
        return render_template(
            'web/router/show.html',
            organization=current_organization,
            user=current_user,
            router=router,
            network_name=network_name,
            hotspot_servers=hotspot_servers,
            pppoe_servers=pppoe_servers
        )
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error fetching router {router_id}: {e}", exc_info=True)
        flash('Router not found', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))


@router_web_bp.route('/<router_id>/edit', methods=['GET', 'POST'])
@web_router_access_required
def edit(org_id, router_id, current_user=None, current_organization=None):
    """Edit router page"""
    
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        
        # Get networks for dropdown
        networks = network_service.get_organization_networks(current_organization.id, 0, 100)
        
        if request.method == 'GET':
            return render_template(
                'web/router/edit.html',
                organization=current_organization,
                user=current_user,
                router=router,
                networks=networks
            )
        
        # POST - Update router
        data = {}
        
        name = request.form.get('name', '').strip()
        if name:
            data['name'] = name
        
        network_id = request.form.get('network_id')
        if network_id:
            data['network_id'] = UUID(network_id)
        
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
        if location:
            data['location'] = location
        
        description = request.form.get('description', '').strip()
        if description:
            data['description'] = description
        
        data['is_active'] = request.form.get('is_active') == 'true'
        
        # Update router
        updated_router = router_service.update_router(router_uuid, current_organization.id, data)
        
        flash(f'Router "{updated_router.name}" updated successfully!', 'success')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error updating router {router_id}: {e}", exc_info=True)
        flash(f'Error updating router: {str(e)}', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))


@router_web_bp.route('/<router_id>/delete', methods=['POST'])
@web_router_access_required
def delete(org_id, router_id, current_user=None, current_organization=None):
    """Delete router"""
    
    try:
        router_uuid = UUID(router_id)
        router_service.delete_router(router_uuid, current_organization.id)
        flash('Router deleted successfully!', 'success')
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error deleting router {router_id}: {e}", exc_info=True)
        flash(f'Error deleting router: {str(e)}', 'danger')
    
    return redirect(url_for('router_web.index', org_id=org_id))


@router_web_bp.route('/<router_id>/test', methods=['POST'])
@web_router_access_required
def test_connection(org_id, router_id, current_user=None, current_organization=None):
    """Test connection to router"""
    
    try:
        router_uuid = UUID(router_id)
        result = router_service.test_connection(router_uuid, current_organization.id)
        
        if result.get('success'):
            flash('Connection test successful! Router is online.', 'success')
        else:
            flash(f'Connection test failed: {result.get("message", "Unknown error")}', 'danger')
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error testing router {router_id}: {e}", exc_info=True)
        flash(f'Connection test failed: {str(e)}', 'danger')
    
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


@router_web_bp.route('/<router_id>/sync', methods=['POST'])
@web_router_access_required
def sync(org_id, router_id, current_user=None, current_organization=None):
    """Sync router configuration"""
    
    try:
        router_uuid = UUID(router_id)
        result = router_service.sync_router(router_uuid, current_organization.id)
        
        if result.get('success'):
            flash(f'Sync successful! Synced {result.get("hotspot_synced", 0)} hotspots and {result.get("pppoe_synced", 0)} PPPoE servers.', 'success')
        else:
            flash('Sync completed with errors. Check logs for details.', 'warning')
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error syncing router {router_id}: {e}", exc_info=True)
        flash(f'Sync failed: {str(e)}', 'danger')
    
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


@router_web_bp.route('/<router_id>/discover', methods=['POST'])
@web_router_access_required
def discover(org_id, router_id, current_user=None, current_organization=None):
    """Auto-discover router capabilities"""
    
    try:
        router_uuid = UUID(router_id)
        result = router_service.discover_router(router_uuid, current_organization.id)
        
        if result.get('success'):
            flash(f'Discovery successful! Router identified via {result.get("method")}.', 'success')
        else:
            flash('Discovery failed. Router added in offline mode.', 'warning')
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error discovering router {router_id}: {e}", exc_info=True)
        flash(f'Discovery failed: {str(e)}', 'danger')
    
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))

@router_web_bp.route('/<router_id>/radius/regenerate', methods=['POST'])
@web_router_access_required
def regenerate_radius_secret(org_id, router_id, current_user=None, current_organization=None):
    """Regenerate RADIUS secret for a router"""
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        
        import secrets
        new_secret = secrets.token_urlsafe(32)
        
        # Update router
        router.radius_secret = new_secret
        router.radius_config_status = 'pending'
        router.radius_configured_at = None
        router.auto_config_attempts = 0
        router.last_config_error = None
        
        # Also update the NAS entry
        if router.nas_entry_id:
            from app.models.nas import NAS
            nas = NAS.query.get(router.nas_entry_id)
            if nas:
                nas.secret = new_secret
        
        db.session.commit()
        
        # Store the new secret in session to display once
        session['new_radius_secret'] = new_secret
        
        flash('New RADIUS secret generated successfully! Please reconfigure your MikroTik router with the new secret.', 'success')
        
    except Exception as e:
        logger.error(f"Error regenerating RADIUS secret for router {router_id}: {e}", exc_info=True)
        flash(f'Failed to regenerate RADIUS secret: {str(e)}', 'danger')
    
    return redirect(url_for('router_web.edit', org_id=org_id, router_id=router_id))


@router_web_bp.route('/<router_id>/radius/retry', methods=['POST'])
@web_router_access_required
def retry_radius_config(org_id, router_id, current_user=None, current_organization=None):
    """Retry RADIUS configuration for a router"""
    
    try:
        router_uuid = UUID(router_id)
        result = router_service.retry_radius_configuration(router_uuid, current_organization.id)
        
        if result.get('success'):
            flash('RADIUS configuration successful! Router is now configured.', 'success')
        else:
            flash(f'RADIUS configuration failed: {result.get("message")}. Please configure manually.', 'warning')
            if result.get('manual_config_instructions'):
                # Store instructions in session to show on next page
                session['radius_instructions'] = result.get('manual_config_instructions')
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
    except Exception as e:
        logger.error(f"Error retrying RADIUS config for router {router_id}: {e}", exc_info=True)
        flash(f'Failed to configure RADIUS: {str(e)}', 'danger')
    
    return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


# ============================================================================
# HOTSPOT SERVER ROUTES
# ============================================================================

@router_web_bp.route('/hotspot-servers/create', methods=['GET', 'POST'])
@web_router_access_required
def create_hotspot(org_id, current_user=None, current_organization=None):
    """Create hotspot server for a router"""
    router_id = request.args.get('router_id')
    
    if not router_id:
        flash('Router ID is required', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        
        if request.method == 'GET':
            # Return inline HTML - no template file needed
            return f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Add Hotspot Server</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
                <style>
                    body {{ background: #f5f5f5; }}
                    .card {{ border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                </style>
            </head>
            <body>
                <div class="container mt-4">
                    <div class="card">
                        <div class="card-header bg-white">
                            <h4><i class="fas fa-wifi"></i> Add Hotspot Server for {router.name}</h4>
                        </div>
                        <div class="card-body">
                            <form method="POST">
                                <div class="mb-3">
                                    <label class="form-label">Name *</label>
                                    <input type="text" name="name" class="form-control" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">Hotspot ID *</label>
                                    <input type="text" name="hotspot_id" class="form-control" required>
                                    <small class="text-muted">Unique identifier on MikroTik</small>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">Interface</label>
                                    <input type="text" name="interface" class="form-control" placeholder="e.g., ether2">
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">Address Pool</label>
                                    <input type="text" name="address_pool" class="form-control" placeholder="e.g., dhcp_pool1">
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">DNS Name</label>
                                    <input type="text" name="dns_name" class="form-control" placeholder="hotspot.example.com">
                                </div>
                                <div class="row">
                                    <div class="col-md-4">
                                        <label class="form-label">Idle Timeout (s)</label>
                                        <input type="number" name="idle_timeout" class="form-control" value="300">
                                    </div>
                                    <div class="col-md-4">
                                        <label class="form-label">Session Timeout (s)</label>
                                        <input type="number" name="session_timeout" class="form-control" value="86400">
                                    </div>
                                    <div class="col-md-4">
                                        <label class="form-label">Keepalive Timeout (s)</label>
                                        <input type="number" name="keepalive_timeout" class="form-control" value="120">
                                    </div>
                                </div>
                                <div class="mb-3 form-check mt-3">
                                    <input type="checkbox" name="is_active" class="form-check-input" id="isActive" checked>
                                    <label class="form-check-label" for="isActive">Active</label>
                                </div>
                                <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Create</button>
                                <a href="{url_for('router_web.show', org_id=org_id, router_id=router_id)}" class="btn btn-secondary"><i class="fas fa-times"></i> Cancel</a>
                            </form>
                        </div>
                    </div>
                </div>
                <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
            </body>
            </html>
            '''
        
        # POST - Create hotspot server
        from app.models.router import HotspotServer
        
        hotspot = HotspotServer(
            organization_id=current_organization.id,
            router_id=router_uuid,
            name=request.form.get('name', '').strip(),
            hotspot_id=request.form.get('hotspot_id', '').strip(),
            interface=request.form.get('interface', '').strip() or None,
            address_pool=request.form.get('address_pool', '').strip() or None,
            dns_name=request.form.get('dns_name', '').strip() or None,
            idle_timeout=request.form.get('idle_timeout', 300, type=int),
            session_timeout=request.form.get('session_timeout', 86400, type=int),
            keepalive_timeout=request.form.get('keepalive_timeout', 120, type=int),
            is_active=request.form.get('is_active') == 'true'
        )
        db.session.add(hotspot)
        db.session.commit()
        
        flash('Hotspot server created successfully!', 'success')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error creating hotspot server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


@router_web_bp.route('/hotspot-servers/<hotspot_id>/edit', methods=['GET', 'POST'])
@web_router_access_required
def edit_hotspot(org_id, hotspot_id, current_user=None, current_organization=None):
    """Edit hotspot server"""
    try:
        from app.models.router import HotspotServer
        
        hotspot_uuid = UUID(hotspot_id)
        hotspot = HotspotServer.query.filter_by(id=hotspot_uuid, organization_id=current_organization.id).first()
        
        if not hotspot:
            flash('Hotspot server not found', 'danger')
            return redirect(url_for('router_web.index', org_id=org_id))
        
        router = router_service.get_router(hotspot.router_id, current_organization.id)
        
        if request.method == 'GET':
            return render_template(
                'web/router/hotspot_edit.html',
                organization=current_organization,
                user=current_user,
                router=router,
                hotspot=hotspot
            )
        
        # POST - Update hotspot server
        hotspot.name = request.form.get('name', '').strip()
        hotspot.hotspot_id = request.form.get('hotspot_id', '').strip()
        hotspot.interface = request.form.get('interface', '').strip()
        hotspot.address_pool = request.form.get('address_pool', '').strip()
        hotspot.dns_name = request.form.get('dns_name', '').strip()
        hotspot.idle_timeout = request.form.get('idle_timeout', 300, type=int)
        hotspot.session_timeout = request.form.get('session_timeout', 86400, type=int)
        hotspot.keepalive_timeout = request.form.get('keepalive_timeout', 120, type=int)
        hotspot.is_active = request.form.get('is_active') == 'true'
        
        db.session.commit()
        flash('Hotspot server updated successfully!', 'success')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router.id))
        
    except ValueError:
        flash('Invalid ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error editing hotspot server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))


# ============================================================================
# PPPoE SERVER ROUTES
# ============================================================================

@router_web_bp.route('/pppoe-servers/create', methods=['GET', 'POST'])
@web_router_access_required
def create_pppoe(org_id, current_user=None, current_organization=None):
    """Create PPPoE server for a router"""
    router_id = request.args.get('router_id')
    
    if not router_id:
        flash('Router ID is required', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    
    try:
        router_uuid = UUID(router_id)
        router = router_service.get_router(router_uuid, current_organization.id)
        
        if request.method == 'GET':
            return render_template(
                'web/router/pppoe_create.html',
                organization=current_organization,
                user=current_user,
                router=router
            )
        
        # POST - Create PPPoE server
        from app.models.router import PPPoeServer
        
        pppoe = PPPoeServer(
            organization_id=current_organization.id,
            router_id=router_uuid,
            name=request.form.get('name', '').strip(),
            interface=request.form.get('interface', '').strip(),
            service_name=request.form.get('service_name', '').strip(),
            mtu=request.form.get('mtu', 1492, type=int),
            max_sessions=request.form.get('max_sessions', 100, type=int),
            is_active=request.form.get('is_active') == 'true'
        )
        db.session.add(pppoe)
        db.session.commit()
        
        flash('PPPoE server created successfully!', 'success')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))
        
    except ValueError:
        flash('Invalid router ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error creating PPPoE server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router_id))


@router_web_bp.route('/pppoe-servers/<pppoe_id>/edit', methods=['GET', 'POST'])
@web_router_access_required
def edit_pppoe(org_id, pppoe_id, current_user=None, current_organization=None):
    """Edit PPPoE server"""
    try:
        from app.models.router import PPPoeServer
        
        pppoe_uuid = UUID(pppoe_id)
        pppoe = PPPoeServer.query.filter_by(id=pppoe_uuid, organization_id=current_organization.id).first()
        
        if not pppoe:
            flash('PPPoE server not found', 'danger')
            return redirect(url_for('router_web.index', org_id=org_id))
        
        router = router_service.get_router(pppoe.router_id, current_organization.id)
        
        if request.method == 'GET':
            return render_template(
                'web/router/pppoe_edit.html',
                organization=current_organization,
                user=current_user,
                router=router,
                pppoe=pppoe
            )
        
        # POST - Update PPPoE server
        pppoe.name = request.form.get('name', '').strip()
        pppoe.interface = request.form.get('interface', '').strip()
        pppoe.service_name = request.form.get('service_name', '').strip()
        pppoe.mtu = request.form.get('mtu', 1492, type=int)
        pppoe.max_sessions = request.form.get('max_sessions', 100, type=int)
        pppoe.is_active = request.form.get('is_active') == 'true'
        
        db.session.commit()
        flash('PPPoE server updated successfully!', 'success')
        return redirect(url_for('router_web.show', org_id=org_id, router_id=router.id))
        
    except ValueError:
        flash('Invalid ID format', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error editing PPPoE server: {e}", exc_info=True)
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('router_web.index', org_id=org_id))
