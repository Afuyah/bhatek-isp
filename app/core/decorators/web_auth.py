from functools import wraps
from flask import session, redirect, url_for, g, request
from app.models.auth import User
from app.core.logging.logger import logger

def web_login_required(f):
    """Decorator for web routes that require authentication (uses session)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # Store the original URL to redirect back after login
            session['next_url'] = request.url
            return redirect(url_for('web.login'))
        
        # Set user in g for easy access
        try:
            user = User.query.get(session['user_id'])
            if not user or not user.is_active:
                session.clear()
                return redirect(url_for('web.login'))
            
            g.current_user = user
            g.user_id = user.id
            g.user_role = user.role
            g.organization_id = user.organization_id
            
        except Exception as e:
            logger.error(f"Error loading user for web request: {e}")
            session.clear()
            return redirect(url_for('web.login'))
        
        return f(*args, **kwargs)
    return decorated_function


def web_super_admin_required(f):
    """Decorator for super admin only web routes"""
    @wraps(f)
    @web_login_required
    def decorated_function(*args, **kwargs):
        # Check if current user is super admin
        # g.current_user is a User object, not a dictionary
        if not hasattr(g, 'current_user'):
            return redirect(url_for('web.dashboard'))
        
        # Access is_super_admin as an attribute, not as a dictionary key
        if not g.current_user.is_super_admin:
            return redirect(url_for('web.dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function


def web_organization_member_required(f):
    """Decorator for organization member only web routes"""
    @wraps(f)
    @web_login_required
    def decorated_function(*args, **kwargs):
        # Get org_id from URL
        org_id = kwargs.get('org_id')
        
        # Check if user belongs to this organization
        if org_id:
            # Compare as strings to handle UUID vs string
            if str(g.organization_id) != str(org_id):
                return redirect(url_for('web.dashboard'))
        elif not g.organization_id:
            # User has no organization
            return redirect(url_for('web.dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function