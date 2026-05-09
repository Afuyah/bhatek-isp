from flask import Blueprint
from app.modules.access_point.controller import AccessPointController
from app.core.security.jwt import token_required

ap_bp = Blueprint('access_point', __name__, url_prefix='/api/v1/access-points')
controller = AccessPointController()


# BULK OPERATIONS

@ap_bp.route('/bulk/delete', methods=['POST'])
@token_required
def bulk_delete():
    """Bulk delete access points"""
    return controller.bulk_delete()


# COLLECTION ROUTES (no ID parameter)

@ap_bp.route('', methods=['POST'])
@token_required
def create():
    """Create a new access point"""
    return controller.create()


@ap_bp.route('', methods=['GET'])
@token_required
def list_access_points():
    """List access points with filters and pagination"""
    return controller.list()


@ap_bp.route('/active', methods=['GET'])
@token_required
def get_active():
    """Get active access points for dropdowns"""
    return controller.get_active()


@ap_bp.route('/online', methods=['GET'])
@token_required
def get_online():
    """Get online access points for monitoring"""
    return controller.get_online()


@ap_bp.route('/offline', methods=['GET'])
@token_required
def get_offline():
    """Get offline access points for alerts"""
    return controller.get_offline()


@ap_bp.route('/issues', methods=['GET'])
@token_required
def get_issues():
    """Get access points with issues for dashboard"""
    return controller.get_issues()


@ap_bp.route('/stats', methods=['GET'])
@token_required
def get_organization_stats():
    """Get access point statistics for organization"""
    return controller.get_organization_stats()


@ap_bp.route('/by-router/<uuid:router_id>', methods=['GET'])
@token_required
def get_by_router(router_id):
    """Get all access points for a specific router"""
    return controller.get_by_router(router_id)


# SINGLE RESOURCE ROUTES (with ID parameter)

@ap_bp.route('/<uuid:ap_id>', methods=['GET'])
@token_required
def get(ap_id):
    """Get access point by ID"""
    return controller.get(ap_id)


@ap_bp.route('/<uuid:ap_id>', methods=['PUT'])
@token_required
def update(ap_id):
    """Update access point"""
    return controller.update(ap_id)


@ap_bp.route('/<uuid:ap_id>', methods=['DELETE'])
@token_required
def delete(ap_id):
    """Delete or deactivate access point"""
    return controller.delete(ap_id)


@ap_bp.route('/<uuid:ap_id>/stats', methods=['GET'])
@token_required
def get_stats(ap_id):
    """Get access point statistics (sessions, usage)"""
    return controller.get_stats(ap_id)