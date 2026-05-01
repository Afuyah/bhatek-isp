from flask import Blueprint, render_template, redirect, url_for, flash, session, request
from app.core.logging.logger import logger
import requests
from uuid import UUID

# Create web blueprint with organization ID in URL pattern
network_web_bp = Blueprint('network_web', __name__, url_prefix='/organization/<org_id>/networks')

# API Base URL
API_BASE_URL = 'http://localhost:5000/api/v1'

def get_auth_headers():
    """Get authentication headers for API calls"""
    token = session.get('access_token')
    if not token:
        logger.error("No access token in session")
        return {}
    return {
        'Authorization': f"Bearer {token}",
        'Content-Type': 'application/json'
    }

def get_current_organization(org_id):
    """Helper to get organization from session and validate"""
    # If session org_id is None, set it from URL
    if session.get('organization_id') is None:
        session['organization_id'] = org_id
        
        # Fetch organization details to set name and slug
        try:
            response = requests.get(
                f"{API_BASE_URL}/organizations/{org_id}",
                headers=get_auth_headers()
            )
            if response.status_code == 200:
                org_data = response.json()
                session['organization_name'] = org_data.get('name')
                session['organization_slug'] = org_data.get('slug')
                logger.info(f"Set organization session: {org_data.get('name')}")
        except Exception as e:
            logger.error(f"Failed to fetch organization: {e}")
    
    # Validate that the org_id in URL matches the session
    if str(session.get('organization_id')) != str(org_id):
        logger.warning(f"Organization mismatch: URL org_id={org_id}, Session org_id={session.get('organization_id')}")
        return None
    
    return {
        'id': session.get('organization_id'),
        'name': session.get('organization_name'),
        'slug': session.get('organization_slug')
    }

def get_user():
    """Get user from session"""
    return {
        'email': session.get('user_email', ''),
        'first_name': session.get('user_first_name', ''),
        'last_name': session.get('user_last_name', ''),
        'id': session.get('user_id', '')
    }


@network_web_bp.route('/')
def index(org_id):
    """List networks page"""
    # Validate organization access
    organization = get_current_organization(org_id)
    if not organization:
        flash('Invalid organization access', 'danger')
        return redirect(url_for('web.dashboard'))
    
    user = get_user()
    
    # Fetch networks from API
    try:
        response = requests.get(
            f"{API_BASE_URL}/networks/",
            headers=get_auth_headers(),
            params=request.args
        )
        
        if response.status_code == 200:
            networks_data = response.json()
            networks = networks_data.get('networks', [])
            pagination = {
                'total': networks_data.get('total', 0),
                'page': networks_data.get('page', 1),
                'per_page': networks_data.get('per_page', 20),
                'pages': networks_data.get('pages', 0)
            }
        else:
            networks = []
            pagination = {}
            flash('Failed to load networks', 'danger')
            
    except Exception as e:
        logger.error(f"Error fetching networks: {e}")
        networks = []
        pagination = {}
        flash('Error connecting to API', 'danger')
    
    return render_template('web/network/index.html',
                         organization=organization,
                         user=user,
                         networks=networks,
                         pagination=pagination)


@network_web_bp.route('/create', methods=['GET', 'POST'])
def create(org_id):
    """Create network page"""
    organization = get_current_organization(org_id)
    if not organization:
        flash('Invalid organization access', 'danger')
        return redirect(url_for('web.dashboard'))
    
    user = get_user()
    
    if request.method == 'GET':
        return render_template('web/network/create.html', 
                             organization=organization,
                             user=user)
    
    # POST - Create network
    try:
        data = {
            'name': request.form.get('name'),
            'type': request.form.get('type', 'hybrid'),
            'description': request.form.get('description', ''),
            'is_active': request.form.get('is_active', 'true') == 'true',
            'settings': {}
        }
        
        response = requests.post(
            f"{API_BASE_URL}/networks/",
            json=data,
            headers=get_auth_headers()
        )
        
        if response.status_code == 201:
            flash('Network created successfully!', 'success')
            return redirect(url_for('network_web.index', org_id=org_id))
        else:
            error_data = response.json()
            flash(f"Error: {error_data.get('error', 'Unknown error')}", 'danger')
            
    except Exception as e:
        logger.error(f"Error creating network: {e}")
        flash('Error creating network', 'danger')
    
    return render_template('web/network/create.html', 
                         organization=organization,
                         user=user)


@network_web_bp.route('/<network_id>')
def show(org_id, network_id):
    """Network details page"""
    organization = get_current_organization(org_id)
    if not organization:
        flash('Invalid organization access', 'danger')
        return redirect(url_for('web.dashboard'))
    
    user = get_user()
    
    try:
        UUID(network_id)
        
        response = requests.get(
            f"{API_BASE_URL}/networks/{network_id}",
            headers=get_auth_headers()
        )
        
        if response.status_code == 200:
            network = response.json()
            return render_template('web/network/show.html',
                                 organization=organization,
                                 user=user,
                                 network=network)
        elif response.status_code == 404:
            flash('Network not found', 'danger')
        else:
            flash('Error loading network', 'danger')
            
    except ValueError:
        flash('Invalid network ID', 'danger')
    except Exception as e:
        logger.error(f"Error fetching network {network_id}: {e}")
        flash('Error loading network', 'danger')
    
    return redirect(url_for('network_web.index', org_id=org_id))


@network_web_bp.route('/<network_id>/edit', methods=['GET', 'POST'])
def edit(org_id, network_id):
    """Edit network page"""
    organization = get_current_organization(org_id)
    if not organization:
        flash('Invalid organization access', 'danger')
        return redirect(url_for('web.dashboard'))
    
    user = get_user()
    
    try:
        UUID(network_id)
        
        if request.method == 'GET':
            response = requests.get(
                f"{API_BASE_URL}/networks/{network_id}",
                headers=get_auth_headers()
            )
            
            if response.status_code == 200:
                network = response.json()
                return render_template('web/network/edit.html',
                                     organization=organization,
                                     user=user,
                                     network=network)
            else:
                flash('Network not found', 'danger')
                return redirect(url_for('network_web.index', org_id=org_id))
        
        # POST - Update network
        data = {
            'name': request.form.get('name'),
            'type': request.form.get('type'),
            'description': request.form.get('description'),
            'is_active': request.form.get('is_active') == 'true'
        }
        
        data = {k: v for k, v in data.items() if v is not None}
        
        response = requests.put(
            f"{API_BASE_URL}/networks/{network_id}",
            json=data,
            headers=get_auth_headers()
        )
        
        if response.status_code == 200:
            flash('Network updated successfully!', 'success')
            return redirect(url_for('network_web.show', org_id=org_id, network_id=network_id))
        else:
            error_data = response.json()
            flash(f"Error: {error_data.get('error', 'Unknown error')}", 'danger')
            
    except ValueError:
        flash('Invalid network ID', 'danger')
    except Exception as e:
        logger.error(f"Error updating network {network_id}: {e}")
        flash('Error updating network', 'danger')
    
    return redirect(url_for('network_web.index', org_id=org_id))


@network_web_bp.route('/<network_id>/delete', methods=['POST'])
def delete(org_id, network_id):
    """Delete network"""
    organization = get_current_organization(org_id)
    if not organization:
        flash('Invalid organization access', 'danger')
        return redirect(url_for('web.dashboard'))
    
    try:
        UUID(network_id)
        
        response = requests.delete(
            f"{API_BASE_URL}/networks/{network_id}",
            headers=get_auth_headers()
        )
        
        if response.status_code == 200:
            flash('Network deleted successfully!', 'success')
        elif response.status_code == 409:
            flash('Cannot delete network with associated routers. Remove routers first.', 'danger')
        else:
            error_data = response.json()
            flash(f"Error: {error_data.get('error', 'Unknown error')}", 'danger')
            
    except ValueError:
        flash('Invalid network ID', 'danger')
    except Exception as e:
        logger.error(f"Error deleting network {network_id}: {e}")
        flash('Error deleting network', 'danger')
    
    return redirect(url_for('network_web.index', org_id=org_id))


@network_web_bp.route('/stats')
def stats(org_id):
    """Network statistics page"""
    organization = get_current_organization(org_id)
    if not organization:
        flash('Invalid organization access', 'danger')
        return redirect(url_for('web.dashboard'))
    
    user = get_user()
    
    try:
        response = requests.get(
            f"{API_BASE_URL}/networks/stats",
            headers=get_auth_headers()
        )
        
        if response.status_code == 200:
            stats = response.json()
        else:
            stats = {}
            flash('Failed to load statistics', 'danger')
            
    except Exception as e:
        logger.error(f"Error fetching network stats: {e}")
        stats = {}
        flash('Error loading statistics', 'danger')
    
    return render_template('web/network/stats.html',
                         organization=organization,
                         user=user,
                         stats=stats)