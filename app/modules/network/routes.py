from flask import Blueprint
from app.modules.network.controller import NetworkController
from app.core.security.jwt import token_required

network_bp = Blueprint('network', __name__)
controller = NetworkController()


@network_bp.route('/', methods=['POST'])
@token_required
def create_network():
    """Create a new network"""
    return controller.create_network()


@network_bp.route('/', methods=['GET'])
@token_required
def list_networks():
    """List networks for current organization"""
    return controller.list_networks()


@network_bp.route('/active', methods=['GET'])
@token_required
def get_active_networks():
    """Get active networks for dropdown"""
    return controller.get_active_networks()


@network_bp.route('/stats', methods=['GET'])
@token_required
def get_network_stats():
    """Get network statistics"""
    return controller.get_network_stats()


@network_bp.route('/bulk-status', methods=['PUT'])
@token_required
def bulk_update_status():
    """Bulk update network status"""
    return controller.bulk_update_status()


@network_bp.route('/<network_id>', methods=['GET'])
@token_required
def get_network(network_id):
    """Get network by ID"""
    return controller.get_network(network_id)


@network_bp.route('/by-slug/<slug>', methods=['GET'])
@token_required
def get_network_by_slug(slug):
    """Get network by slug"""
    return controller.get_network_by_slug(slug)


@network_bp.route('/<network_id>', methods=['PUT'])
@token_required
def update_network(network_id):
    """Update network"""
    return controller.update_network(network_id)


@network_bp.route('/<network_id>', methods=['DELETE'])
@token_required
def delete_network(network_id):
    """Delete network"""
    return controller.delete_network(network_id)