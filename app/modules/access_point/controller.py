from flask import request, g, jsonify
from marshmallow import ValidationError

from app.modules.access_point.service import AccessPointService
from app.modules.access_point.schemas import AccessPointCreateSchema, AccessPointUpdateSchema
from app.core.security.jwt import token_required
from app.core.logging.logger import logger

class AccessPointController:
    """Access Point controller"""
    
    def __init__(self):
        self.service = AccessPointService()
    
    @token_required
    def create(self):
        """Create access point"""
        try:
            data = AccessPointCreateSchema().load(request.json)
            ap = self.service.create_access_point(g.organization_id, data)
            return jsonify({
                'success': True,
                'access_point': ap.to_dict(),
                'message': 'Access point created successfully'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Create access point error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get(self, ap_id):
        """Get access point by ID"""
        try:
            ap = self.service.get_access_point(ap_id, g.organization_id)
            return jsonify(ap.to_dict()), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 404
    
    @token_required
    def update(self, ap_id):
        """Update access point"""
        try:
            data = AccessPointUpdateSchema().load(request.json)
            ap = self.service.update_access_point(ap_id, g.organization_id, data)
            return jsonify({
                'success': True,
                'access_point': ap.to_dict(),
                'message': 'Access point updated successfully'
            }), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def delete(self, ap_id):
        """Delete access point"""
        try:
            self.service.delete_access_point(ap_id, g.organization_id)
            return jsonify({'success': True, 'message': 'Access point deleted successfully'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def list(self):
        """List access points"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            router_id = request.args.get('router_id')
            hotspot_id = request.args.get('hotspot_server_id')
            
            if router_id:
                aps = self.service.get_access_points_by_router(router_id, g.organization_id)
            elif hotspot_id:
                aps = self.service.get_access_points_by_hotspot(hotspot_id, g.organization_id)
            else:
                aps = self.service.repository.get_by_organization(g.organization_id, skip, per_page)
            
            return jsonify({
                'access_points': [ap.to_dict() for ap in aps],
                'total': len(aps),
                'page': page,
                'per_page': per_page
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_stats(self, ap_id):
        """Get access point statistics"""
        try:
            stats = self.service.get_ap_stats(ap_id, g.organization_id)
            return jsonify(stats), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500