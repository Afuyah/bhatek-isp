from flask import Blueprint, render_template, redirect, url_for, flash, session, request, abort
from functools import wraps
from uuid import UUID
from datetime import datetime

from app.core.logging.logger import logger
from app.modules.access_point.service import AccessPointService
from app.modules.router.service import RouterService
from app.modules.network.service import NetworkService
from app.modules.organization.service import OrganizationService
from app.modules.auth.repository import UserRepository

# Create web blueprint with organization ID in URL pattern
ap_web_bp = Blueprint('ap_web', __name__, url_prefix='/organization/<org_id>/access-points')

# Initialize services
ap_service = AccessPointService()
router_service = RouterService()
network_service = NetworkService()
organization_service = OrganizationService()
user_repo = UserRepository()

# DECORATORS
def web_ap_access_required(f):
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
        
        # Get all routers for dropdowns
        routers = router_service.get_routers_by_organization(org_uuid, 0, 100)
        
        # Make user, org, and routers available to template
        kwargs['current_user'] = user
        kwargs['current_organization'] = organization
        kwargs['routers'] = routers
        
        return f(org_id, *args, **kwargs)
    return decorated_function

# ROUTES
@ap_web_bp.route('/')
@web_ap_access_required
def index(org_id, current_user=None, current_organization=None, routers=None):
    """List access points page"""
    
    # Get filters from query params
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    skip = (page - 1) * per_page
    
    status = request.args.get('status')
    router_id = request.args.get('router_id')
    search = request.args.get('search')
    
    # Convert router_id to UUID if provided
    router_uuid = None
    if router_id:
        try:
            router_uuid = UUID(router_id)
        except ValueError:
            pass
    
    # Build filters
    filters = {}
    if status:
        filters['status'] = status
    if search:
        filters['search'] = search
    
    # Fetch access points
    aps = ap_service.get_access_points_by_organization(
        organization_id=current_organization.id,
        skip=skip,
        limit=per_page,
        status=status,
        router_id=router_uuid
    )
    
    # Get total count
    total = ap_service.repository.count_by_organization(
        current_organization.id, status=status
    )
    
    pagination = {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total else 0
    }
    
    # Get stats for dashboard
    stats = ap_service.get_organization_stats(current_organization.id)
    
    return render_template(
        'web/access_point/index.html',
        organization=current_organization,
        user=current_user,
        access_points=aps,
        routers=routers,
        pagination=pagination,
        stats=stats,
        filters={'status': status, 'router_id': router_id, 'search': search}
    )


@ap_web_bp.route('/create', methods=['GET', 'POST'])
@web_ap_access_required
def create(org_id, current_user=None, current_organization=None, routers=None):
    """Create access point page"""
    
    if request.method == 'GET':
        # Get router_id from query param to pre-select
        pre_selected_router = request.args.get('router_id')
        
        return render_template(
            'web/access_point/create.html',
            organization=current_organization,
            user=current_user,
            routers=routers,
            pre_selected_router=pre_selected_router,
            form_data=None
        )
    
    # POST - Create access point
    form_data = {
        'router_id': request.form.get('router_id'),
        'name': request.form.get('name', '').strip(),
        'mac_address': request.form.get('mac_address', '').strip().upper(),
        'ssid': request.form.get('ssid', '').strip(),
        'location': request.form.get('location', '').strip(),
        'ip_address': request.form.get('ip_address', '').strip(),
        'hotspot_server_id': request.form.get('hotspot_server_id'),
        'description': request.form.get('description', '').strip(),
        'is_active': request.form.get('is_active') == 'true'
    }
    
    try:
        # Validate required fields
        if not form_data['router_id']:
            flash('Router selection is required', 'danger')
            return render_template('web/access_point/create.html',
                                 organization=current_organization, user=current_user,
                                 routers=routers, form_data=form_data)
        
        if not form_data['name']:
            flash('Access point name is required', 'danger')
            return render_template('web/access_point/create.html',
                                 organization=current_organization, user=current_user,
                                 routers=routers, form_data=form_data)
        
        if not form_data['mac_address']:
            flash('MAC address is required', 'danger')
            return render_template('web/access_point/create.html',
                                 organization=current_organization, user=current_user,
                                 routers=routers, form_data=form_data)
        
        if not form_data['ssid']:
            flash('SSID is required', 'danger')
            return render_template('web/access_point/create.html',
                                 organization=current_organization, user=current_user,
                                 routers=routers, form_data=form_data)
        
        if not form_data['location']:
            flash('Location is required', 'danger')
            return render_template('web/access_point/create.html',
                                 organization=current_organization, user=current_user,
                                 routers=routers, form_data=form_data)
        
        # Create access point
        ap = ap_service.create_access_point(
            organization_id=current_organization.id,
            router_id=UUID(form_data['router_id']),
            data=form_data
        )
        
        flash(f'Access point "{ap.name}" created successfully!', 'success')
        return redirect(url_for('ap_web.index', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating access point: {e}", exc_info=True)
        flash(f'Error creating access point: {str(e)}', 'danger')
        return render_template('web/access_point/create.html',
                             organization=current_organization, user=current_user,
                             routers=routers, form_data=form_data)


@ap_web_bp.route('/<ap_id>')
@web_ap_access_required
def show(org_id, ap_id, current_user=None, current_organization=None, routers=None):
    """Access point details page"""
    
    try:
        ap_uuid = UUID(ap_id)
        ap = ap_service.get_access_point(ap_uuid, current_organization.id)
        
        # Get router name
        router_name = None
        if ap.router_id:
            try:
                router = router_service.get_router(ap.router_id, current_organization.id)
                router_name = router.name
            except:
                pass
        
        return render_template(
            'web/access_point/show.html',
            organization=current_organization,
            user=current_user,
            access_point=ap,
            router_name=router_name
        )
        
    except ValueError:
        flash('Invalid access point ID format', 'danger')
        return redirect(url_for('ap_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error fetching access point {ap_id}: {e}", exc_info=True)
        flash('Access point not found', 'danger')
        return redirect(url_for('ap_web.index', org_id=org_id))


@ap_web_bp.route('/<ap_id>/edit', methods=['GET', 'POST'])
@web_ap_access_required
def edit(org_id, ap_id, current_user=None, current_organization=None, routers=None):
    """Edit access point page"""
    
    try:
        ap_uuid = UUID(ap_id)
        ap = ap_service.get_access_point(ap_uuid, current_organization.id)
        
        if request.method == 'GET':
            return render_template(
                'web/access_point/edit.html',
                organization=current_organization,
                user=current_user,
                access_point=ap,
                routers=routers
            )
        
        # POST - Update access point
        data = {}
        
        name = request.form.get('name', '').strip()
        if name:
            data['name'] = name
        
        router_id = request.form.get('router_id')
        if router_id:
            data['router_id'] = UUID(router_id)
        
        mac_address = request.form.get('mac_address', '').strip().upper()
        if mac_address:
            data['mac_address'] = mac_address
        
        ssid = request.form.get('ssid', '').strip()
        if ssid:
            data['ssid'] = ssid
        
        location = request.form.get('location', '').strip()
        if location:
            data['location'] = location
        
        ip_address = request.form.get('ip_address', '').strip()
        if ip_address:
            data['ip_address'] = ip_address
        
        hotspot_server_id = request.form.get('hotspot_server_id')
        if hotspot_server_id:
            data['hotspot_server_id'] = UUID(hotspot_server_id)
        
        description = request.form.get('description', '').strip()
        if description:
            data['description'] = description
        
        data['is_active'] = request.form.get('is_active') == 'true'
        
        # Update access point
        updated_ap = ap_service.update_access_point(ap_uuid, current_organization.id, data)
        
        flash(f'Access point "{updated_ap.name}" updated successfully!', 'success')
        return redirect(url_for('ap_web.show', org_id=org_id, ap_id=ap_id))
        
    except ValueError:
        flash('Invalid access point ID format', 'danger')
        return redirect(url_for('ap_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error updating access point {ap_id}: {e}", exc_info=True)
        flash(f'Error updating access point: {str(e)}', 'danger')
        return redirect(url_for('ap_web.index', org_id=org_id))


@ap_web_bp.route('/<ap_id>/delete', methods=['POST'])
@web_ap_access_required
def delete(org_id, ap_id, current_user=None, current_organization=None, routers=None):
    """Delete access point"""
    
    try:
        ap_uuid = UUID(ap_id)
        ap_service.delete_access_point(ap_uuid, current_organization.id, soft_delete=True)
        flash('Access point deleted successfully!', 'success')
        
    except ValueError:
        flash('Invalid access point ID format', 'danger')
    except Exception as e:
        logger.error(f"Error deleting access point {ap_id}: {e}", exc_info=True)
        flash(f'Error deleting access point: {str(e)}', 'danger')
    
    return redirect(url_for('ap_web.index', org_id=org_id))


@ap_web_bp.route('/stats')
@web_ap_access_required
def stats(org_id, current_user=None, current_organization=None, routers=None):
    """Access point statistics page"""
    
    stats = ap_service.get_organization_stats(current_organization.id)
    stats['organization_name'] = current_organization.name
    
    # Get APs with issues for detailed view
    aps_with_issues = ap_service.repository.get_aps_with_issues(current_organization.id)
    
    return render_template(
        'web/access_point/stats.html',
        organization=current_organization,
        user=current_user,
        stats=stats,
        aps_with_issues=aps_with_issues
    )