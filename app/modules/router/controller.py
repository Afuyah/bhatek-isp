# app/modules/router/controller.py
from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID
from flask import current_app
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
    """Router controller for API endpoints with RADIUS auto-configuration support"""
    
    def __init__(self):
        self.service = RouterService()
    
    # CREATE (with RADIUS auto-configuration)
    
    @token_required
    def create(self):
        """
        Create a new router with automatic RADIUS configuration.
        
        The system will:
        1. Generate a unique RADIUS shared secret
        2. Create a NAS entry for FreeRADIUS
        3. Test connection to the router
        4. Auto-configure RADIUS on the MikroTik if connection succeeds
        5. Return router details with configuration status
        """
        try:
            data = RouterCreateSchema().load(request.json)
            
            # Validate required fields
            if 'network_id' not in data:
                return jsonify({'error': 'network_id is required'}), 400
            
            # Create router with auto-configuration
            result = self.service.create_router(
                organization_id=g.organization_id,
                network_id=UUID(data['network_id']),
                data=data
            )
            
            # Extract router and metadata from result
            router = result.get('router')
            auto_configured = result.get('auto_configured', False)
            
            response_data = {
                'success': True,
                'router': router.to_dict(include_sensitive=False),
                'auto_configured': auto_configured,
                'message': 'Router created successfully'
            }
            
            # Include RADIUS configuration details if auto-config failed or for reference
            if result.get('radius_secret'):
                response_data['radius_secret'] = result['radius_secret']
                response_data['radius_server_ip'] = result['radius_server_ip']
                response_data['radius_ports'] = {
                    'authentication': 1812,
                    'accounting': 1813
                }
            
            # Include manual configuration instructions if auto-config failed
            if not auto_configured and result.get('manual_config_instructions'):
                response_data['manual_config_instructions'] = result['manual_config_instructions']
                response_data['warning'] = 'RADIUS auto-configuration failed. Please configure manually using the instructions provided.'
            
            return jsonify(response_data), 201
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Create router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # READ
    
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
            radius_config_status = request.args.get('radius_config_status')  # pending/configured/failed/manual
            
            # Convert network_id to UUID if provided
            network_uuid = None
            if network_id:
                try:
                    network_uuid = UUID(network_id)
                except ValueError:
                    return jsonify({'error': 'Invalid network_id format'}), 400
            
            # Get routers via service
            routers = self.service.get_routers_by_organization(
                organization_id=g.organization_id,
                skip=skip,
                limit=per_page,
                status=status,
                network_id=network_uuid,
                radius_config_status=radius_config_status
            )
            
            # Get total count
            total = self.service.repository.count_by_organization(
                g.organization_id, 
                status=status,
                radius_config_status=radius_config_status
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
                    {
                        'id': str(r.id), 
                        'name': r.name, 
                        'ip_address': str(r.ip_address),
                        'radius_config_status': r.radius_config_status
                    } for r in routers
                ]
            }), 200
            
        except Exception as e:
            logger.error(f"Get active routers error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_pending_radius(self):
        """Get routers pending RADIUS configuration"""
        try:
            routers = self.service.get_routers_pending_radius_config(g.organization_id)
            
            return jsonify({
                'routers': [
                    {
                        'id': str(r.id),
                        'name': r.name,
                        'ip_address': str(r.ip_address),
                        'radius_config_status': r.radius_config_status,
                        'auto_config_attempts': r.auto_config_attempts,
                        'last_config_error': r.last_config_error
                    } for r in routers
                ],
                'count': len(routers)
            }), 200
            
        except Exception as e:
            logger.error(f"Get pending RADIUS routers error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # UPDATE
    
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
    
    # DELETE
    
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
    
    # CONNECTION & DISCOVERY
    
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
        """Auto-discover router capabilities"""
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
        """Get router health metrics"""
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
        """Get router connection status summary"""
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
    
    # SYNC
    
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
    
    # RADIUS CONFIGURATION
    
    @token_required
    def configure_radius(self, router_id):
        """
        Manually configure RADIUS settings on router (legacy method)
        For routers created with auto-config, use retry_radius endpoint instead
        """
        try:
            router_uuid = UUID(router_id)
            data = RouterRadiusSchema().load(request.json)
            
            result = self.service.configure_radius_manual(
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
    
    @token_required
    def retry_radius_config(self, router_id):
        """
        Retry RADIUS auto-configuration for a router that previously failed.
        This will attempt to configure RADIUS on the MikroTik using the stored secret.
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.retry_radius_configuration(router_uuid, g.organization_id)
            
            if result.get('success'):
                return jsonify({
                    'success': True,
                    'message': result.get('message'),
                    'radius_server_ip': result.get('radius_server_ip')
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'message': result.get('message'),
                    'manual_config_instructions': result.get('manual_config_instructions')
                }), 207
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Retry RADIUS config error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_radius_secret(self, router_id):
        """
        Get the RADIUS shared secret for a router (only for manual configuration).
        This endpoint should be restricted and logged.
        """
        try:
            router_uuid = UUID(router_id)
            router = self.service.get_router(router_uuid, g.organization_id)
            
            if not router.radius_secret:
                return jsonify({'error': 'No RADIUS secret configured for this router'}), 404
            
            # Log this access for security auditing
            logger.warning(f"RADIUS secret accessed for router {router_id} by user {g.user_id}")
            
            return jsonify({
                'router_id': str(router.id),
                'router_name': router.name,
                'radius_secret': router.radius_secret,
                'radius_server_ip': current_app.config.get('RADIUS_SERVER_IP', '163.245.217.16'),
                'warning': 'This secret is sensitive. Keep it secure and do not share.'
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get RADIUS secret error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # BULK OPERATIONS
    
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
    
    @token_required
    def bulk_retry_radius(self):
        """Bulk retry RADIUS configuration for multiple routers"""
        try:
            data = request.get_json()
            router_ids = data.get('router_ids', [])
            
            if not router_ids:
                return jsonify({'error': 'No router IDs provided'}), 400
            
            results = []
            success_count = 0
            
            for rid in router_ids:
                try:
                    result = self.service.retry_radius_configuration(UUID(rid), g.organization_id)
                    results.append({
                        'id': rid,
                        'success': result.get('success', False),
                        'message': result.get('message')
                    })
                    if result.get('success'):
                        success_count += 1
                except Exception as e:
                    results.append({'id': rid, 'success': False, 'error': str(e)})
            
            return jsonify({
                'results': results,
                'success_count': success_count,
                'total_count': len(router_ids),
                'message': f'Successfully configured {success_count}/{len(router_ids)} routers'
            }), 200
            
        except Exception as e:
            logger.error(f"Bulk retry RADIUS error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500