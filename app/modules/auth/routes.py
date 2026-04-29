from flask import Blueprint, request, g, jsonify
from app.modules.auth.controller import AuthController
from app.core.security.jwt import token_required

auth_bp = Blueprint('auth', __name__)
controller = AuthController()

# Existing routes
@auth_bp.route('/register', methods=['POST'])
def register():
    """Register new user"""
    return controller.register()

@auth_bp.route('/login', methods=['POST'])
def login():
    """Login user"""
    return controller.login()

@auth_bp.route('/refresh', methods=['POST'])
def refresh():
    """Refresh access token"""
    return controller.refresh()

@auth_bp.route('/logout', methods=['POST'])
@token_required
def logout():
    """Logout user"""
    return controller.logout()

@auth_bp.route('/change-password', methods=['POST'])
@token_required
def change_password():
    """Change password"""
    return controller.change_password()

@auth_bp.route('/me', methods=['GET'])
@token_required
def get_me():
    """Get current user info"""
    from app.modules.auth.repository import UserRepository
    user_repo = UserRepository()
    user = user_repo.get_by_id(g.get('user_id'))
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify(user.to_dict()), 200


@auth_bp.route('/send-verification', methods=['POST'])
def send_verification():
    """Send verification email to user"""
    return controller.send_verification()

@auth_bp.route('/verify-email', methods=['POST'])
def verify_email():
    """Verify email token"""
    return controller.verify_email()

@auth_bp.route('/register-organization', methods=['POST'])
def register_organization():
    """Register new organization and admin user"""
    return controller.register_organization()