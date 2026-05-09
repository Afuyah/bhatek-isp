from flask import Blueprint, render_template, redirect, url_for, flash, session, request, current_app, abort
from functools import wraps
from uuid import UUID
from typing import Dict, Any, Optional

from app.core.logging.logger import logger
from app.modules.network.service import NetworkService
from app.modules.organization.service import OrganizationService
from app.modules.auth.repository import UserRepository

# Create web blueprint with organization ID in URL pattern
network_web_bp = Blueprint('network_web', __name__, url_prefix='/organization/<org_id>/networks')

# Initialize services
network_service = NetworkService()
organization_service = OrganizationService()
user_repo = UserRepository()


# ============================================================================
# DECORATORS
# ============================================================================

def web_network_access_required(f):
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
        
        # Store in g-like context (using session for now, but consider Flask g)
        session['current_organization_id'] = str(org_uuid)
        session['current_organization_name'] = organization.name
        session['current_organization_slug'] = organization.slug
        
        # Make user and org available to template
        kwargs['current_user'] = user
        kwargs['current_organization'] = organization
        
        return f(org_id, *args, **kwargs)
    return decorated_function


def get_current_user():
    """Get current user from session with fresh data"""
    user_id = session.get('user_id')
    if not user_id:
        return None
    return user_repo.get_by_id(user_id)


def get_current_organization_from_session():
    """Get current organization from session"""
    org_id = session.get('current_organization_id')
    if not org_id:
        return None
    try:
        return organization_service.get_organization(UUID(org_id))
    except Exception:
        return None


# ============================================================================
# ROUTES
# ============================================================================

@network_web_bp.route('/')
@web_network_access_required
def index(org_id, current_user=None, current_organization=None):
    """List networks page"""
    
    # Get filters from query params
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    skip = (page - 1) * per_page
    
    filters = {
        'type': request.args.get('type'),
        'is_active': request.args.get('is_active', type=bool),
        'search': request.args.get('search')
    }
    filters = {k: v for k, v in filters.items() if v is not None}
    
    # Fetch networks directly from service
    networks = network_service.get_organization_networks(
        current_organization.id, skip, per_page, filters
    )
    
    # Get total count
    total = network_service.network_repo.count_by_organization(
        current_organization.id, filters.get('is_active')
    )
    
    pagination = {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total else 0
    }
    
    #  GET NETWORK STATISTICS
    stats = network_service.get_network_stats(current_organization.id)
    
    return render_template(
        'web/network/index.html',
        organization=current_organization,
        user=current_user,
        networks=[n.to_dict() for n in networks],
        pagination=pagination,
        filters=filters,
        stats=stats  
    )

@network_web_bp.route('/create', methods=['GET', 'POST'])
@web_network_access_required
def create(org_id, current_user=None, current_organization=None):
    """Create network page"""
    
    if request.method == 'GET':
        # GET request - just show empty form
        return render_template(
            'web/network/create.html',
            organization=current_organization,
            user=current_user,
            form_data=None  # ✅ Pass None for new form
        )
    
    # POST - Create network
    form_data = {
        'name': request.form.get('name', '').strip(),
        'slug': request.form.get('slug', '').strip(),
        'type': request.form.get('type', 'hybrid'),
        'description': request.form.get('description', '').strip(),
        'is_active': request.form.get('is_active', 'true') == 'true',
        'settings': request.form.get('settings', '')
    }
    
    try:
        # Validate required fields
        if not form_data['name']:
            flash('Network name is required', 'danger')
            return render_template(
                'web/network/create.html',
                organization=current_organization,
                user=current_user,
                form_data=form_data  # ✅ Pass form_data to preserve input
            )
        
        # Parse settings JSON if provided
        settings = {}
        if form_data['settings']:
            import json
            try:
                settings = json.loads(form_data['settings'])
            except json.JSONDecodeError as e:
                flash(f'Invalid JSON in settings: {str(e)}', 'danger')
                return render_template(
                    'web/network/create.html',
                    organization=current_organization,
                    user=current_user,
                    form_data=form_data
                )
        
        # Prepare data for service
        network_data = {
            'name': form_data['name'],
            'type': form_data['type'],
            'description': form_data['description'],
            'is_active': form_data['is_active'],
            'settings': settings
        }
        
        # Only add slug if provided
        if form_data['slug']:
            network_data['slug'] = form_data['slug']
        
        # Create network via service
        network = network_service.create_network(current_organization.id, network_data)
        
        flash(f'Network "{network.name}" created successfully!', 'success')
        return redirect(url_for('network_web.index', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating network: {e}", exc_info=True)
        flash(f'Error creating network: {str(e)}', 'danger')
        return render_template(
            'web/network/create.html',
            organization=current_organization,
            user=current_user,
            form_data=form_data  # ✅ Pass form_data to preserve input
        )


@network_web_bp.route('/<network_id>')
@web_network_access_required
def show(org_id, network_id, current_user=None, current_organization=None):
    """Network details page"""
    
    try:
        network_uuid = UUID(network_id)
        
        # Fetch network via service
        network = network_service.get_network(network_uuid, current_organization.id)
        
        #  Fetch routers belonging to this network directly
        from app.modules.router.service import RouterService
        router_service = RouterService()
        routers = router_service.get_routers_by_network(network_uuid, current_organization.id)
        
        #  Convert routers to dict for template
        routers_data = []
        for router in routers:
            router_dict = router.to_dict()
            routers_data.append(router_dict)
        
        # Placeholders for APs and sessions (will be implemented later)
        access_points = []
        active_sessions = 0
        bandwidth_usage = 0
        
        return render_template(
            'web/network/show.html',
            organization=current_organization,
            user=current_user,
            network=network.to_dict(),
            routers=routers_data, 
            access_points=access_points, 
            active_sessions=active_sessions, 
            bandwidth_usage=bandwidth_usage 
        )
        
    except ValueError:
        flash('Invalid network ID format', 'danger')
        return redirect(url_for('network_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error fetching network {network_id}: {e}", exc_info=True)
        flash('Network not found', 'danger')
        return redirect(url_for('network_web.index', org_id=org_id))

@network_web_bp.route('/<network_id>/edit', methods=['GET', 'POST'])
@web_network_access_required
def edit(org_id, network_id, current_user=None, current_organization=None):
    """Edit network page"""
    
    try:
        network_uuid = UUID(network_id)
        
        if request.method == 'GET':
            # Fetch network via service (no HTTP call)
            network = network_service.get_network(network_uuid, current_organization.id)
            
            return render_template(
                'web/network/edit.html',
                organization=current_organization,
                user=current_user,
                network=network.to_dict()
            )
        
        # POST - Update network
        data = {}
        
        name = request.form.get('name', '').strip()
        if name:
            data['name'] = name
        
        network_type = request.form.get('type')
        if network_type:
            data['type'] = network_type
        
        description = request.form.get('description', '').strip()
        if description:
            data['description'] = description
        
        data['is_active'] = request.form.get('is_active') == 'true'
        
        # Update network via service (no HTTP call)
        updated_network = network_service.update_network(
            network_uuid, current_organization.id, data
        )
        
        flash(f'Network "{updated_network.name}" updated successfully!', 'success')
        return redirect(url_for('network_web.show', org_id=org_id, network_id=network_id))
        
    except ValueError:
        flash('Invalid network ID format', 'danger')
        return redirect(url_for('network_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error updating network {network_id}: {e}", exc_info=True)
        flash(f'Error updating network: {str(e)}', 'danger')
        return redirect(url_for('network_web.index', org_id=org_id))


@network_web_bp.route('/<network_id>/delete', methods=['POST'])
@web_network_access_required
def delete(org_id, network_id, current_user=None, current_organization=None):
    """Delete network"""
    
    try:
        network_uuid = UUID(network_id)
        
        # Delete network via service (no HTTP call)
        network_service.delete_network(network_uuid, current_organization.id)
        
        flash('Network deleted successfully!', 'success')
        
    except ValueError:
        flash('Invalid network ID format', 'danger')
    except Exception as e:
        error_msg = str(e)
        if 'Cannot delete network with associated routers' in error_msg:
            flash('Cannot delete network with associated routers. Remove routers first.', 'danger')
        else:
            logger.error(f"Error deleting network {network_id}: {e}", exc_info=True)
            flash(f'Error deleting network: {error_msg}', 'danger')
    
    return redirect(url_for('network_web.index', org_id=org_id))


@network_web_bp.route('/stats')
@web_network_access_required
def stats(org_id, current_user=None, current_organization=None):
    """Network statistics page"""
    
    # Fetch stats via service (no HTTP call)
    stats = network_service.get_network_stats(current_organization.id)
    
    # Add organization name to stats for display
    stats['organization_name'] = current_organization.name
    
    return render_template(
        'web/network/stats.html',
        organization=current_organization,
        user=current_user,
        stats=stats
    )


@network_web_bp.route('/<network_id>/toggle-status', methods=['POST'])
@web_network_access_required
def toggle_status(org_id, network_id, current_user=None, current_organization=None):
    """Toggle network active status (AJAX or form)"""
    
    try:
        network_uuid = UUID(network_id)
        network = network_service.get_network(network_uuid, current_organization.id)
        
        # Toggle status
        updated_network = network_service.update_network(
            network_uuid, 
            current_organization.id, 
            {'is_active': not network.is_active}
        )
        
        status = 'activated' if updated_network.is_active else 'deactivated'
        flash(f'Network "{updated_network.name}" {status}!', 'success')
        
    except ValueError:
        flash('Invalid network ID format', 'danger')
    except Exception as e:
        logger.error(f"Error toggling network {network_id} status: {e}", exc_info=True)
        flash(f'Error toggling network status: {str(e)}', 'danger')
    
    return redirect(url_for('network_web.index', org_id=org_id))


@network_web_bp.route('/bulk-action', methods=['POST'])
@web_network_access_required
def bulk_action(org_id, current_user=None, current_organization=None):
    """Handle bulk actions on networks"""
    
    network_ids = request.form.getlist('network_ids')
    action = request.form.get('action')
    
    if not network_ids:
        flash('No networks selected', 'warning')
        return redirect(url_for('network_web.index', org_id=org_id))
    
    try:
        network_uuids = [UUID(nid) for nid in network_ids]
        
        if action == 'activate':
            count = network_service.bulk_update_status(
                current_organization.id, network_uuids, True
            )
            flash(f'{count} network(s) activated successfully!', 'success')
            
        elif action == 'deactivate':
            count = network_service.bulk_update_status(
                current_organization.id, network_uuids, False
            )
            flash(f'{count} network(s) deactivated successfully!', 'success')
            
        elif action == 'delete':
            # Delete each network individually (to respect router checks)
            deleted_count = 0
            failed_count = 0
            
            for nid in network_uuids:
                try:
                    network_service.delete_network(nid, current_organization.id)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete network {nid}: {e}")
                    failed_count += 1
            
            if deleted_count > 0:
                flash(f'{deleted_count} network(s) deleted successfully!', 'success')
            if failed_count > 0:
                flash(f'{failed_count} network(s) could not be deleted (may have routers)', 'warning')
        else:
            flash('Invalid action selected', 'danger')
            
    except ValueError:
        flash('Invalid network ID format', 'danger')
    except Exception as e:
        logger.error(f"Error performing bulk action: {e}", exc_info=True)
        flash(f'Error performing bulk action: {str(e)}', 'danger')
    
    return redirect(url_for('network_web.index', org_id=org_id))