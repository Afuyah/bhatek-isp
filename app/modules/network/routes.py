from flask import Blueprint
from app.modules.network.controller import NetworkController
from app.core.security.jwt import token_required

network_bp = Blueprint('network', __name__)
controller = NetworkController()

@network_bp.route('', methods=['POST'])
@token_required
def create():
    """Create network"""
    return controller.create()

@network_bp.route('/<uuid:network_id>', methods=['GET'])
@token_required
def get(network_id):
    """Get network by ID"""
    return controller.get(network_id)

@network_bp.route('/<uuid:network_id>', methods=['PUT'])
@token_required
def update(network_id):
    """Update network"""
    return controller.update(network_id)

@network_bp.route('/<uuid:network_id>', methods=['DELETE'])
@token_required
def delete(network_id):
    """Delete network"""
    return controller.delete(network_id)

@network_bp.route('', methods=['GET'])
@token_required
def list_networks():
    """List networks"""
    return controller.list()