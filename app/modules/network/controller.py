from flask import request, g, jsonify
from marshmallow import ValidationError

from app.modules.network.service import NetworkService
from app.modules.network.schemas import NetworkCreateSchema, NetworkUpdateSchema
from app.core.security.jwt import token_required
from app.core.logging.logger import logger

class NetworkController:
    """Network controller"""
    
    def __init__(self):
        self.service = NetworkService()
    
    @token_required
    def create(self):
        """Create network"""
        try:
            data = NetworkCreateSchema().load(request.json)
            network = self.service.create_network(g.organization_id, data)
            return jsonify({
                'success': True,
                'network': network.to_dict(),
                'message': 'Network created successfully'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Create network error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get(self, network_id):
        """Get network by ID"""
        try:
            network = self.service.get_network(network_id, g.organization_id)
            return jsonify(network.to_dict()), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 404
    
    @token_required
    def update(self, network_id):
        """Update network"""
        try:
            data = NetworkUpdateSchema().load(request.json)
            network = self.service.update_network(network_id, g.organization_id, data)
            return jsonify({
                'success': True,
                'network': network.to_dict(),
                'message': 'Network updated successfully'
            }), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def delete(self, network_id):
        """Delete network"""
        try:
            self.service.delete_network(network_id, g.organization_id)
            return jsonify({'success': True, 'message': 'Network deleted successfully'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def list(self):
        """List networks"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            networks = self.service.list_networks(g.organization_id, skip, per_page)
            return jsonify({
                'networks': [n.to_dict() for n in networks],
                'total': len(networks),
                'page': page,
                'per_page': per_page
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500