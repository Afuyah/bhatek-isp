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
        
        # Create router
        router = router_service.create_router(
            organization_id=current_organization.id,
            network_id=UUID(form_data['network_id']),
            data=form_data
        )
        
        flash(f'Router "{router.name}" created successfully!', 'success')
        
        # Optionally test connection immediately
        if request.form.get('test_connection') == 'true':
            try:
                result = router_service.test_connection(router.id, current_organization.id)
                if result.get('success'):
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