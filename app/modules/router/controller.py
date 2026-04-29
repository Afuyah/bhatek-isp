from flask import request, g, jsonify
from marshmallow import ValidationError

from app.modules.router.service import RouterService
from app.modules.router.schemas import RouterCreateSchema, RouterUpdateSchema, RouterTestSchema
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger

class RouterController:
    """Router controller"""
    
    def __init__(self):
        self.service = RouterService()
    
    @token_required
    def create(self):
        """Create router"""
        try:
            data = RouterCreateSchema().load(request.json)
            router = self.service.create_router(g.organization_id, data)
            return jsonify({
                'success': True,
                'router': router.to_dict(),
                'message': 'Router created successfully'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Create router error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get(self, router_id):
        """Get router by ID"""
        try:
            router = self.service.get_router(router_id, g.organization_id)
            return jsonify(router.to_dict()), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 404
    
    @token_required
    def update(self, router_id):
        """Update router"""
        try:
            data = RouterUpdateSchema().load(request.json)
            router = self.service.update_router(router_id, g.organization_id, data)
            return jsonify({
                'success': True,
                'router': router.to_dict(),
                'message': 'Router updated successfully'
            }), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def delete(self, router_id):
        """Delete router"""
        try:
            self.service.delete_router(router_id, g.organization_id)
            return jsonify({'success': True, 'message': 'Router deleted successfully'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def list(self):
        """List routers"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            routers = self.service.repository.get_by_organization(g.organization_id, skip, per_page)
            return jsonify({
                'routers': [r.to_dict() for r in routers],
                'total': len(routers),
                'page': page,
                'per_page': per_page
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def test(self, router_id):
        """Test router connection"""
        try:
            result = self.service.test_connection(router_id, g.organization_id)
            return jsonify(result), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def sync(self, router_id):
        """Sync router data"""
        try:
            result = self.service.sync_router(router_id, g.organization_id)
            return jsonify(result), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500