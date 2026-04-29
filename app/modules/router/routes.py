from flask import Blueprint
from app.modules.router.controller import RouterController
from app.core.security.jwt import token_required

router_bp = Blueprint('router', __name__)
controller = RouterController()

@router_bp.route('', methods=['POST'])
@token_required
def create():
    """Create router"""
    return controller.create()

@router_bp.route('/<uuid:router_id>', methods=['GET'])
@token_required
def get(router_id):
    """Get router by ID"""
    return controller.get(router_id)

@router_bp.route('/<uuid:router_id>', methods=['PUT'])
@token_required
def update(router_id):
    """Update router"""
    return controller.update(router_id)

@router_bp.route('/<uuid:router_id>', methods=['DELETE'])
@token_required
def delete(router_id):
    """Delete router"""
    return controller.delete(router_id)

@router_bp.route('', methods=['GET'])
@token_required
def list_routers():
    """List routers"""
    return controller.list()

@router_bp.route('/<uuid:router_id>/test', methods=['POST'])
@token_required
def test(router_id):
    """Test router connection"""
    return controller.test(router_id)

@router_bp.route('/<uuid:router_id>/sync', methods=['POST'])
@token_required
def sync(router_id):
    """Sync router data"""
    return controller.sync(router_id)