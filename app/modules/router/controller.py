from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.router.service import RouterService
from app.modules.router.schemas import (
    RouterCreateSchema, 
    RouterUpdateSchema, 
    RouterTestSchema,
    RouterRadiusSchema,
    RouterSyncSchema
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError as AppValidationError


class RouterController:
    """Router controller for API endpoints"""
    
    def __init__(self):
        self.service = RouterService()
    
    # ==========================================================================
    # CREATE
    # ==========================================================================
    
    @token_required
    def create(self):
        """Create a new router"""
        try:
            data = RouterCreateSchema().load(request.json)
            
            # Validate required fields
            if 'network_id' not in data:
                return jsonify({'error': 'network_id is required'}), 400
            
            router = self.service.create_router(
                organization_id=g.organization_id,
                network_id=UUID(data['network_id']),
                data=data
            )
            
            return jsonify({
                'success': True,
                'router': router.to_dict(),
                'message': 'Router created successfully'
            }), 201
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"Create router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # READ
    # ==========================================================================
    
    @token_required
    def get(self, router_id):
        """Get router by ID"""
        try:
            router_uuid = UUID(router_id)
            router = self.service.get_router(router_uuid, g.organization_id)
            return jsonify(router.to_dict(include_sensitive=False)), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def list(self):
        """List routers for current organization with filters and pagination"""
        try:
            # Pagination
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            # Filters
            status = request.args.get('status')
            network_id = request.args.get('network_id')
            
            # Convert network_id to UUID if provided
            network_uuid = None
            if network_id:
                try:
                    network_uuid = UUID(network_id)
                except ValueError:
                    return jsonify({'error': 'Invalid network_id format'}), 400
            
            # Get routers via service (not repository directly)
            routers = self.service.get_routers_by_organization(
                organization_id=g.organization_id,
                skip=skip,
                limit=per_page,
                status=status,
                network_id=network_uuid
            )
            
            # Get total count
            total = self.service.repository.count_by_organization(
                g.organization_id, 
                status=status
            )
            
            return jsonify({
                'routers': [r.to_dict(include_sensitive=False) for r in routers],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
            
        except Exception as e:
            logger.error(f"List routers error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_by_network(self, network_id):
        """Get all routers in a specific network"""
        try:
            network_uuid = UUID(network_id)
            routers = self.service.get_routers_by_network(network_uuid, g.organization_id)
            
            return jsonify({
                'routers': [r.to_dict(include_sensitive=False) for r in routers]
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid network ID format'}), 400
        except Exception as e:
            logger.error(f"Get routers by network error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_active(self):
        """Get all active routers for dropdown/selection"""
        try:
            routers = self.service.repository.get_all_active(g.organization_id)
            
            return jsonify({
                'routers': [
                    {'id': str(r.id), 'name': r.name, 'ip_address': str(r.ip_address)} 
                    for r in routers
                ]
            }), 200
            
        except Exception as e:
            logger.error(f"Get active routers error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # UPDATE
    # ==========================================================================
    
    @token_required
    def update(self, router_id):
        """Update router information"""
        try:
            router_uuid = UUID(router_id)
            data = RouterUpdateSchema().load(request.json)
            router = self.service.update_router(router_uuid, g.organization_id, data)
            
            return jsonify({
                'success': True,
                'router': router.to_dict(include_sensitive=False),
                'message': 'Router updated successfully'
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Update router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # DELETE
    # ==========================================================================
    
    @token_required
    def delete(self, router_id):
        """Delete or deactivate router"""
        try:
            router_uuid = UUID(router_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            
            self.service.delete_router(router_uuid, g.organization_id, soft_delete=soft)
            
            message = 'Router deactivated successfully' if soft else 'Router deleted permanently'
            return jsonify({'success': True, 'message': message}), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Delete router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # CONNECTION & DISCOVERY
    # ==========================================================================
    
    @token_required
    def test_connection(self, router_id):
        """Test connection to router"""
        try:
            router_uuid = UUID(router_id)
            result = self.service.test_connection(router_uuid, g.organization_id)
            return jsonify(result), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e), 'success': False}), 500
        except Exception as e:
            logger.error(f"Test connection error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def discover(self, router_id):
        """Auto-discover router capabilities"
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.discover_router(router_uuid, g.organization_id)
            return jsonify(result), 200 if result.get('success') else 207
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Discover router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def health(self, router_id):
        """Get router health metrics"
        """
        try:
            router_uuid = UUID(router_id)
            health = self.service.update_health(router_uuid, g.organization_id)
            return jsonify(health), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 500
        except Exception as e:
            logger.error(f"Health check error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def status(self, router_id):
        """Get router connection status summary"
        """
        try:
            router_uuid = UUID(router_id)
            status = self.service.get_connection_status(router_uuid, g.organization_id)
            return jsonify(status), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get status error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # SYNC
    # ==========================================================================
    
    @token_required
    def sync(self, router_id):
        """Sync router configuration (hotspot, PPPoE, etc.)"""
        try:
            router_uuid = UUID(router_id)
            result = self.service.sync_router(router_uuid, g.organization_id)
            return jsonify(result), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 500
        except Exception as e:
            logger.error(f"Sync router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # RADIUS CONFIGURATION
    # ==========================================================================
    
    @token_required
    def configure_radius(self, router_id):
        """Configure RADIUS settings on router"""
        try:
            router_uuid = UUID(router_id)
            data = RouterRadiusSchema().load(request.json)
            
            result = self.service.configure_radius(
                router_id=router_uuid,
                organization_id=g.organization_id,
                radius_server=data['radius_server'],
                radius_secret=data['radius_secret']
            )
            
            return jsonify(result), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 500
        except Exception as e:
            logger.error(f"Configure RADIUS error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # BULK OPERATIONS
    # ==========================================================================
    
    @token_required
    def bulk_delete(self):
        """Bulk delete routers"""
        try:
            data = request.get_json()
            router_ids = data.get('router_ids', [])
            soft = data.get('soft', True)
            
            if not router_ids:
                return jsonify({'error': 'No router IDs provided'}), 400
            
            deleted_count = 0
            errors = []
            
            for rid in router_ids:
                try:
                    self.service.delete_router(UUID(rid), g.organization_id, soft_delete=soft)
                    deleted_count += 1
                except Exception as e:
                    errors.append({'id': rid, 'error': str(e)})
            
            return jsonify({
                'success': True,
                'deleted_count': deleted_count,
                'errors': errors
            }), 200
            
        except Exception as e:
            logger.error(f"Bulk delete error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def bulk_sync(self):
        """Bulk sync multiple routers"""
        try:
            data = request.get_json()
            router_ids = data.get('router_ids', [])
            
            if not router_ids:
                return jsonify({'error': 'No router IDs provided'}), 400
            
            results = []
            for rid in router_ids:
                try:
                    result = self.service.sync_router(UUID(rid), g.organization_id)
                    results.append({'id': rid, 'success': True, 'result': result})
                except Exception as e:
                    results.append({'id': rid, 'success': False, 'error': str(e)})
            
            return jsonify({
                'results': results,
                'synced_count': sum(1 for r in results if r['success'])
            }), 200
            
        except Exception as e:
            logger.error(f"Bulk sync error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500