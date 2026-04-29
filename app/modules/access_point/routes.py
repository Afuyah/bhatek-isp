from flask import Blueprint
from app.modules.access_point.controller import AccessPointController
from app.core.security.jwt import token_required

ap_bp = Blueprint('access_point', __name__)
controller = AccessPointController()

@ap_bp.route('', methods=['POST'])
@token_required
def create():
    """Create access point"""
    return controller.create()

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
    """Delete access point"""
    return controller.delete(ap_id)

@ap_bp.route('', methods=['GET'])
@token_required
def list_access_points():
    """List access points"""
    return controller.list()

@ap_bp.route('/<uuid:ap_id>/stats', methods=['GET'])
@token_required
def get_stats(ap_id):
    """Get access point statistics"""
    return controller.get_stats(ap_id)