from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.network.service import NetworkService
from app.modules.network.schemas import (
    NetworkCreateSchema, NetworkUpdateSchema, BulkNetworkStatusSchema
)
from app.core.security.jwt import token_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError, ValidationError as AppValidationError


class NetworkController:
    """Network controller"""
    
    def __init__(self):
        self.service = NetworkService()
    
    @token_required
    def create_network(self):
        """Create a new network"""
        try:
            data = NetworkCreateSchema().load(request.json)
            network = self.service.create_network(g.organization_id, data)
            
            return jsonify({
                'success': True,
                'message': 'Network created successfully',
                'network': network.to_dict()
            }), 201
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Create network error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_network(self, network_id):
        """Get network by ID"""
        try:
            network_id_uuid = UUID(network_id)
            network = self.service.get_network(network_id_uuid, g.organization_id)
            
            return jsonify(network.to_dict()), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid network ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get network error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_network_by_slug(self, slug):
        """Get network by slug"""
        try:
            network = self.service.get_network_by_slug(slug, g.organization_id)
            return jsonify(network.to_dict()), 200
            
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get network by slug error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def update_network(self, network_id):
        """Update network"""
        try:
            network_id_uuid = UUID(network_id)
            data = NetworkUpdateSchema().load(request.json)
            network = self.service.update_network(network_id_uuid, g.organization_id, data)
            
            return jsonify({
                'success': True,
                'message': 'Network updated successfully',
                'network': network.to_dict()
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid network ID format'}), 400
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Update network error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def delete_network(self, network_id):
        """Delete network"""
        try:
            network_id_uuid = UUID(network_id)
            self.service.delete_network(network_id_uuid, g.organization_id)
            
            return jsonify({
                'success': True,
                'message': 'Network deleted successfully'
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid network ID format'}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Delete network error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def list_networks(self):
        """List networks for current organization"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            filters = {
                'type': request.args.get('type'),
                'is_active': request.args.get('is_active', type=bool),
                'search': request.args.get('search')
            }
            filters = {k: v for k, v in filters.items() if v is not None}
            
            networks = self.service.get_organization_networks(
                g.organization_id, skip, per_page, filters
            )
            total = self.service.network_repo.count_by_organization(
                g.organization_id, filters.get('is_active')
            )
            
            return jsonify({
                'networks': [n.to_dict() for n in networks],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
            
        except Exception as e:
            logger.error(f"List networks error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_network_stats(self):
        """Get network statistics"""
        try:
            stats = self.service.get_network_stats(g.organization_id)
            return jsonify(stats), 200
            
        except Exception as e:
            logger.error(f"Get network stats error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def bulk_update_status(self):
        """Bulk update network status"""
        try:
            data = BulkNetworkStatusSchema().load(request.json)
            network_ids = [UUID(nid) for nid in data['network_ids']]
            count = self.service.bulk_update_status(
                g.organization_id, network_ids, data['is_active']
            )
            
            return jsonify({
                'success': True,
                'message': f'{count} network(s) updated',
                'updated_count': count
            }), 200
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Bulk update error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_active_networks(self):
        """Get all active networks"""
        try:
            networks = self.service.get_active_networks(g.organization_id)
            return jsonify({
                'networks': [{'id': str(n.id), 'name': n.name, 'type': n.type} for n in networks]
            }), 200
            
        except Exception as e:
            logger.error(f"Get active networks error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500