from flask import Blueprint
from app.modules.organization.controller import OrganizationController
from app.core.security.jwt import token_required

org_bp = Blueprint('organization', __name__)
controller = OrganizationController()


@org_bp.route('/', methods=['POST'])
@token_required
def create_organization():
    """Create a new organization"""
    return controller.create_organization()


@org_bp.route('/', methods=['GET'])
@token_required
def list_organizations():
    """List organizations for current user"""
    return controller.list_organizations()


@org_bp.route('/<org_id>', methods=['GET'])
@token_required
def get_organization(org_id):
    """Get organization by ID"""
    return controller.get_organization(org_id)


@org_bp.route('/by-slug/<slug>', methods=['GET'])
@token_required
def get_organization_by_slug(slug):
    """Get organization by slug"""
    return controller.get_organization_by_slug(slug)


@org_bp.route('/<org_id>', methods=['PUT'])
@token_required
def update_organization(org_id):
    """Update organization"""
    return controller.update_organization(org_id)


@org_bp.route('/<org_id>', methods=['DELETE'])
@token_required
def delete_organization(org_id):
    """Delete organization"""
    return controller.delete_organization(org_id)


@org_bp.route('/<org_id>/users', methods=['GET'])
@token_required
def get_organization_users(org_id):
    """Get all users in organization"""
    return controller.get_organization_users(org_id)


@org_bp.route('/<org_id>/users', methods=['POST'])
@token_required
def add_user_to_organization(org_id):
    """Add user to organization"""
    return controller.add_user_to_organization(org_id)


@org_bp.route('/<org_id>/users/<user_id>', methods=['DELETE'])
@token_required
def remove_user_from_organization(org_id, user_id):
    """Remove user from organization"""
    return controller.remove_user_from_organization(org_id, user_id)


@org_bp.route('/<org_id>/users/<user_id>/role', methods=['PUT'])
@token_required
def update_user_role(org_id, user_id):
    """Update user's role in organization"""
    return controller.update_user_role(org_id, user_id)


@org_bp.route('/<org_id>/stats', methods=['GET'])
@token_required
def get_organization_stats(org_id):
    """Get organization statistics"""
    return controller.get_organization_stats(org_id)


@org_bp.route('/switch/<org_id>', methods=['POST'])
@token_required
def switch_organization(org_id):
    """Switch current working organization"""
    return controller.switch_organization(org_id)