from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID
from datetime import datetime

from app.modules.router.service import RouterService
from app.modules.router.schemas import (
    RouterCreateSchema,
    RouterUpdateSchema,
    RouterTestSchema,
    RouterRadiusSchema,
    RouterSyncSchema,
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import (
    NotFoundError,
    BusinessError,
    ValidationError as AppValidationError,
)


class RouterController:
    """
    Router API controller.

    All methods require valid JWT with organization context.
    Tenant isolation is enforced at the service/repository layer.
    """

    def __init__(self):
        self.service = RouterService()

    # =========================================================================
    # CREATE
    # =========================================================================

    @token_required
    def create(self):
        """
        POST /api/v1/routers

        Create a new router with automatic RADIUS configuration.

        Request body:
            {
                "network_id": "uuid (required)",
                "name": "string (required)",
                "ip_address": "string (required)",
                "username": "string (required)",
                "password": "string (required)",
                "model": "string (optional)",
                "api_port": 8728,
                "location": "string (optional)",
                "description": "string (optional)"
            }

        The system will:
            1. Validate all required fields
            2. Generate a unique RADIUS shared secret
            3. Encrypt the router password for storage
            4. Create Router record in database
            5. Create NAS entry for FreeRADIUS
            6. Test connection to the router
            7. Auto-configure RADIUS if reachable
            8. Return comprehensive response with config status

        Responses:
            201: Router created successfully
            400: Validation error
            409: Business logic conflict
            500: Internal server error
        """
        try:
            data = RouterCreateSchema().load(request.json)

            # Validate network_id separately (not in schema)
            network_id = data.get('network_id') or request.json.get('network_id')
            if not network_id:
                return jsonify({
                    'success': False,
                    'error': 'network_id is required',
                    'error_code': 'MISSING_NETWORK_ID',
                }), 400

            try:
                network_uuid = UUID(network_id)
            except (ValueError, AttributeError):
                return jsonify({
                    'success': False,
                    'error': 'Invalid network_id format',
                    'error_code': 'INVALID_NETWORK_ID',
                }), 400

            # Create router with full auto-configuration pipeline
            result = self.service.create_router(
                organization_id=g.organization_id,
                network_id=network_uuid,
                data=data,
            )

            router = result.get('router')
            auto_configured = result.get('auto_configured', False)

            # Build comprehensive response
            response_data = {
                'success': True,
                'message': 'Router created successfully',
                'router': self._serialize_router_full(router),
                'auto_configured': auto_configured,
                'configuration': {
                    'radius_server_ip': result.get('radius_server_ip'),
                    'radius_ports': {
                        'authentication': 1812,
                        'accounting': 1813,
                    },
                    'status': router.radius_config_status,
                    'attempts': router.auto_config_attempts or 0,
                },
                'organization': {
                    'id': result.get('organization_id'),
                    'name': result.get('organization_name'),
                    'slug': result.get('organization_slug'),
                },
            }

            # Include RADIUS secret (only returned once at creation)
            if result.get('radius_secret'):
                response_data['radius_secret'] = result['radius_secret']
                response_data['_warning'] = (
                    'Store this RADIUS secret securely. '
                    'It will not be shown again.'
                )

            # Include manual configuration instructions if auto-config failed
            if not auto_configured:
                response_data['manual_config_instructions'] = result.get(
                    'manual_config_instructions'
                )
                response_data['warning'] = (
                    'RADIUS auto-configuration failed. '
                    'Please configure manually using the instructions provided.'
                )
                if result.get('error'):
                    response_data['error_details'] = result['error']

            logger.info(
                f"Router created: {router.name} "
                f"(auto_configured={auto_configured}) "
                f"by user {g.user_id}"
            )
            return jsonify(response_data), 201

        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except AppValidationError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'VALIDATION_ERROR',
            }), 400
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'BUSINESS_ERROR',
            }), 409
        except Exception as e:
            logger.error(f"Create router error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # READ — SINGLE
    # =========================================================================

    @token_required
    def get(self, router_id):
        """
        GET /api/v1/routers/<router_id>

        Get detailed router information including RADIUS config status,
        health metrics, and linked NAS entry.

        Responses:
            200: Router details
            400: Invalid router ID
            404: Router not found
        """
        try:
            router_uuid = UUID(router_id)
            router = self.service.get_router(router_uuid, g.organization_id)

            return jsonify({
                'success': True,
                'router': self._serialize_router_full(router),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get router error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # READ — LIST WITH FILTERING & PAGINATION
    # =========================================================================

    @token_required
    def list(self):
        """
        GET /api/v1/routers

        List routers for the current organization with filtering and pagination.

        Query parameters:
            page (int): Page number (default: 1)
            per_page (int): Items per page (default: 20, max: 100)
            status (str): Filter by status (online, offline, unknown, error)
            network_id (str): Filter by network UUID
            radius_config_status (str): Filter by RADIUS state
                (pending, configured, failed)
            search (str): Search by name or IP address

        Responses:
            200: Paginated router list with metadata
        """
        try:
            # Pagination
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page

            # Filters
            status = request.args.get('status')
            network_id = request.args.get('network_id')
            radius_config_status = request.args.get('radius_config_status')
            search = request.args.get('search')

            # Validate UUID filters
            network_uuid = None
            if network_id:
                try:
                    network_uuid = UUID(network_id)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'error': 'Invalid network_id format',
                        'error_code': 'INVALID_UUID',
                    }), 400

            # Get routers
            routers = self.service.get_routers_by_organization(
                organization_id=g.organization_id,
                skip=skip,
                limit=per_page,
                status=status,
                network_id=network_uuid,
                radius_config_status=radius_config_status,
            )

            # Client-side search filter (if search param provided)
            if search:
                search_lower = search.lower()
                routers = [
                    r for r in routers
                    if search_lower in r.name.lower()
                    or search_lower in str(r.ip_address)
                ]

            # Get total count
            total = self.service.repository.count_by_organization(
                g.organization_id,
                status=status,
                radius_config_status=radius_config_status,
            )

            # Build summary statistics for dashboard
            summary = self._get_router_summary(g.organization_id)

            return jsonify({
                'success': True,
                'routers': [self._serialize_router_list(r) for r in routers],
                'summary': summary,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page if total > 0 else 0,
                    'has_next': (page * per_page) < total,
                    'has_prev': page > 1,
                },
                'filters_applied': {
                    'status': status,
                    'network_id': network_id,
                    'radius_config_status': radius_config_status,
                    'search': search,
                },
            }), 200

        except Exception as e:
            logger.error(f"List routers error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # READ — FILTERED VIEWS
    # =========================================================================

    @token_required
    def get_by_network(self, network_id):
        """
        GET /api/v1/routers/by-network/<network_id>

        Get all routers in a specific network.
        """
        try:
            network_uuid = UUID(network_id)
            routers = self.service.get_routers_by_network(
                network_uuid, g.organization_id
            )

            return jsonify({
                'success': True,
                'network_id': network_id,
                'routers': [self._serialize_router_list(r) for r in routers],
                'count': len(routers),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid network ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(f"Get routers by network error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_active(self):
        """
        GET /api/v1/routers/active

        Get all active routers for dropdowns and selection lists.
        Returns minimal data for performance.
        """
        try:
            routers = self.service.repository.get_all_active(g.organization_id)

            return jsonify({
                'success': True,
                'routers': [
                    {
                        'id': str(r.id),
                        'name': r.name,
                        'ip_address': str(r.ip_address),
                        'status': r.status,
                        'radius_config_status': r.radius_config_status,
                        'model': r.model,
                        'network_id': str(r.network_id) if r.network_id else None,
                    }
                    for r in routers
                ],
                'count': len(routers),
            }), 200

        except Exception as e:
            logger.error(f"Get active routers error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_pending_radius(self):
        """
        GET /api/v1/routers/pending-radius

        Get routers that need RADIUS configuration attention.
        Includes routers with 'pending' or 'failed' status.
        """
        try:
            routers = self.service.get_routers_pending_radius_config(
                g.organization_id
            )

            return jsonify({
                'success': True,
                'routers': [
                    {
                        'id': str(r.id),
                        'name': r.name,
                        'ip_address': str(r.ip_address),
                        'radius_config_status': r.radius_config_status,
                        'auto_config_attempts': r.auto_config_attempts or 0,
                        'last_config_error': r.last_config_error,
                        'status': r.status,
                        'created_at': r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in routers
                ],
                'count': len(routers),
                'counts': {
                    'pending': sum(
                        1 for r in routers
                        if r.radius_config_status == 'pending'
                    ),
                    'failed': sum(
                        1 for r in routers
                        if r.radius_config_status == 'failed'
                    ),
                },
            }), 200

        except Exception as e:
            logger.error(f"Get pending RADIUS routers error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_issues(self):
        """
        GET /api/v1/routers/issues

        Get routers that need attention (offline, errors, failed RADIUS config).
        """
        try:
            routers = self.service.repository.get_routers_with_issues(
                g.organization_id
            )

            return jsonify({
                'success': True,
                'routers': [
                    {
                        'id': str(r.id),
                        'name': r.name,
                        'ip_address': str(r.ip_address),
                        'status': r.status,
                        'radius_config_status': r.radius_config_status,
                        'auto_config_attempts': r.auto_config_attempts or 0,
                        'last_config_error': r.last_config_error,
                        'last_seen_at': (
                            r.last_seen_at.isoformat()
                            if r.last_seen_at else None
                        ),
                    }
                    for r in routers
                ],
                'count': len(routers),
            }), 200

        except Exception as e:
            logger.error(f"Get router issues error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_stats(self):
        """
        GET /api/v1/routers/stats

        Get router statistics summary for dashboards.

        Returns counts by status, RADIUS config state, and overall health.
        """
        try:
            summary = self._get_router_summary(g.organization_id)

            # Add additional computed stats
            summary['total_active'] = (
                summary['counts']['online']
                + summary['counts']['unknown']
            )
            summary['health_percentage'] = (
                round(
                    summary['counts']['online']
                    / max(summary['total'], 1) * 100,
                    1,
                )
            )

            return jsonify({
                'success': True,
                'stats': summary,
            }), 200

        except Exception as e:
            logger.error(f"Get router stats error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # UPDATE
    # =========================================================================

    @token_required
    def update(self, router_id):
        """
        PUT /api/v1/routers/<router_id>

        Update router information.
        Password is re-encrypted if a new one is provided.
        """
        try:
            router_uuid = UUID(router_id)
            data = RouterUpdateSchema().load(request.json)

            router = self.service.update_router(
                router_uuid, g.organization_id, data
            )

            logger.info(
                f"Router {router_id} updated by user {g.user_id}"
            )

            return jsonify({
                'success': True,
                'message': 'Router updated successfully',
                'router': self._serialize_router_full(router),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Update router error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # DELETE
    # =========================================================================

    @token_required
    def delete(self, router_id):
        """
        DELETE /api/v1/routers/<router_id>?soft=true

        Delete or deactivate a router.
        Soft delete (default): Deactivates router and NAS entry.
        Hard delete: Permanently removes. Requires no active services.
        """
        try:
            router_uuid = UUID(router_id)
            soft = request.args.get('soft', 'true').lower() == 'true'

            self.service.delete_router(
                router_uuid, g.organization_id, soft_delete=soft
            )

            message = (
                'Router deactivated successfully'
                if soft
                else 'Router permanently deleted'
            )
            logger.info(
                f"Router {router_id} "
                f"{'deactivated' if soft else 'deleted'} "
                f"by user {g.user_id}"
            )

            return jsonify({
                'success': True,
                'message': message,
                'soft_delete': soft,
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'BUSINESS_ERROR',
            }), 409
        except Exception as e:
            logger.error(f"Delete router error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # CONNECTION & DISCOVERY
    # =========================================================================

    @token_required
    def test_connection(self, router_id):
        """
        POST /api/v1/routers/<router_id>/test

        Test connection to a router.
        Returns connection status, response time, and router info.
        Updates router status based on result.
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.test_connection(
                router_uuid, g.organization_id
            )

            return jsonify({
                'success': True,
                'connection': result,
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'CONNECTION_ERROR',
                'connection': {'success': False, 'connected': False},
            }), 200  # 200 even on failure — the connection test result is valid
        except Exception as e:
            logger.error(f"Test connection error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def discover(self, router_id):
        """
        POST /api/v1/routers/<router_id>/discover

        Auto-discover router capabilities via API, SSH, SNMP, Telnet.
        Updates router with discovered model, version, and capabilities.

        Returns 207 if discovery failed (partial success — router exists
        but capabilities couldn't be detected).
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.discover_router(
                router_uuid, g.organization_id
            )

            status_code = 200 if result.get('success') else 207

            return jsonify({
                'success': result.get('success', False),
                'method': result.get('method'),
                'info': result.get('info'),
                'attempts': result.get('attempts'),
                'message': result.get('message'),
            }), status_code

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Discover router error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def health(self, router_id):
        """
        GET /api/v1/routers/<router_id>/health

        Get live health metrics from the router.
        Fetches CPU, memory, uptime, and system info via API.
        """
        try:
            router_uuid = UUID(router_id)
            health = self.service.update_health(
                router_uuid, g.organization_id
            )

            return jsonify({
                'success': True,
                'health': health,
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'HEALTH_CHECK_FAILED',
                'health': None,
            }), 200  # 200 — health check failure is valid data
        except Exception as e:
            logger.error(f"Health check error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def status(self, router_id):
        """
        GET /api/v1/routers/<router_id>/status

        Get comprehensive connection status and health summary.
        Includes RADIUS config state, health metrics, and error info.
        """
        try:
            router_uuid = UUID(router_id)
            status = self.service.get_connection_status(
                router_uuid, g.organization_id
            )

            return jsonify({
                'success': True,
                'status': status,
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get status error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # SYNC
    # =========================================================================

    @token_required
    def sync(self, router_id):
        """
        POST /api/v1/routers/<router_id>/sync

        Sync router configuration into the database.
        Pulls hotspot servers and PPPoE servers from the router.
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.sync_router(
                router_uuid, g.organization_id
            )

            return jsonify({
                'success': True,
                'sync_result': result,
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'SYNC_FAILED',
            }), 500
        except Exception as e:
            logger.error(f"Sync router error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # RADIUS CONFIGURATION
    # =========================================================================

    @token_required
    def configure_radius(self, router_id):
        """
        POST /api/v1/routers/<router_id>/radius/configure

        Manually configure RADIUS settings on the router.
        Use this for routers where auto-configuration is not possible.

        Request body:
            {
                "radius_server": "string (required)",
                "radius_secret": "string (required)"
            }
        """
        try:
            router_uuid = UUID(router_id)
            data = RouterRadiusSchema().load(request.json)

            result = self.service.configure_radius_manual(
                router_id=router_uuid,
                organization_id=g.organization_id,
                radius_server=data['radius_server'],
                radius_secret=data['radius_secret'],
            )

            return jsonify({
                'success': result.get('success', False),
                'message': result.get('message', result.get('error')),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'RADIUS_CONFIG_FAILED',
            }), 500
        except Exception as e:
            logger.error(f"Configure RADIUS error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def retry_radius_config(self, router_id):
        """
        POST /api/v1/routers/<router_id>/radius/retry

        Retry RADIUS auto-configuration for a router that previously failed.
        Uses the stored RADIUS secret — does not generate a new one.
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.retry_radius_configuration(
                router_uuid, g.organization_id
            )

            if result.get('success'):
                return jsonify({
                    'success': True,
                    'message': result.get('message'),
                    'radius_server_ip': result.get('radius_server_ip'),
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'message': result.get('message'),
                    'manual_config_instructions': result.get(
                        'manual_config_instructions'
                    ),
                }), 207

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'RADIUS_RETRY_FAILED',
            }), 409
        except Exception as e:
            logger.error(f"Retry RADIUS config error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('routers:read_secret')
    def get_radius_secret(self, router_id):
        """
        GET /api/v1/routers/<router_id>/radius/secret

        Get the RADIUS shared secret for a router.
        Requires 'routers:read_secret' permission.
        Access is logged for security auditing.
        """
        try:
            router_uuid = UUID(router_id)
            router = self.service.get_router(
                router_uuid, g.organization_id
            )

            if not router.radius_secret:
                return jsonify({
                    'success': False,
                    'error': 'No RADIUS secret configured for this router',
                    'error_code': 'NO_SECRET',
                }), 404

            # Audit log for security
            logger.warning(
                f"RADIUS secret accessed for router {router_id} "
                f"by user {g.user_id} "
                f"(email: {getattr(g, 'user_email', 'unknown')})"
            )

            return jsonify({
                'success': True,
                'router_id': str(router.id),
                'router_name': router.name,
                'radius_secret': router.radius_secret,
                'radius_server_ip': self.service._get_radius_server(),
                '_warning': (
                    'This secret is sensitive. Keep it secure and do not share.'
                ),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get RADIUS secret error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    @token_required
    def bulk_delete(self):
        """
        POST /api/v1/routers/bulk/delete

        Bulk delete/deactivate routers.

        Request body:
            {
                "router_ids": ["uuid1", "uuid2", ...],
                "soft": true
            }
        """
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])
            soft = data.get('soft', True)

            if not router_ids:
                return jsonify({
                    'success': False,
                    'error': 'No router IDs provided',
                    'error_code': 'MISSING_IDS',
                }), 400

            results = []
            deleted_count = 0
            errors = []

            for rid in router_ids:
                try:
                    self.service.delete_router(
                        UUID(rid), g.organization_id, soft_delete=soft
                    )
                    results.append({'id': rid, 'success': True})
                    deleted_count += 1
                except NotFoundError as e:
                    errors.append({'id': rid, 'error': str(e)})
                except BusinessError as e:
                    errors.append({'id': rid, 'error': str(e)})
                except Exception as e:
                    errors.append({'id': rid, 'error': str(e)})

            return jsonify({
                'success': len(errors) == 0,
                'message': (
                    f'{deleted_count}/{len(router_ids)} routers '
                    f'{"deactivated" if soft else "deleted"}'
                ),
                'deleted_count': deleted_count,
                'total_count': len(router_ids),
                'errors': errors,
            }), 200

        except Exception as e:
            logger.error(f"Bulk delete error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def bulk_sync(self):
        """
        POST /api/v1/routers/bulk/sync

        Bulk sync multiple routers.

        Request body:
            {
                "router_ids": ["uuid1", "uuid2", ...]
            }
        """
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])

            if not router_ids:
                return jsonify({
                    'success': False,
                    'error': 'No router IDs provided',
                    'error_code': 'MISSING_IDS',
                }), 400

            results = []
            synced_count = 0
            failed_count = 0

            for rid in router_ids:
                try:
                    result = self.service.sync_router(
                        UUID(rid), g.organization_id
                    )
                    results.append({
                        'id': rid,
                        'success': True,
                        'result': result,
                    })
                    synced_count += 1
                except Exception as e:
                    results.append({
                        'id': rid,
                        'success': False,
                        'error': str(e),
                    })
                    failed_count += 1

            return jsonify({
                'success': failed_count == 0,
                'message': (
                    f'Synced {synced_count}/{len(router_ids)} routers'
                ),
                'synced_count': synced_count,
                'failed_count': failed_count,
                'total_count': len(router_ids),
                'results': results,
            }), 200

        except Exception as e:
            logger.error(f"Bulk sync error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def bulk_retry_radius(self):
        """
        POST /api/v1/routers/bulk/radius/retry

        Bulk retry RADIUS configuration for multiple routers.

        Request body:
            {
                "router_ids": ["uuid1", "uuid2", ...]
            }
        """
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])

            if not router_ids:
                return jsonify({
                    'success': False,
                    'error': 'No router IDs provided',
                    'error_code': 'MISSING_IDS',
                }), 400

            results = []
            success_count = 0
            failed_count = 0

            for rid in router_ids:
                try:
                    result = self.service.retry_radius_configuration(
                        UUID(rid), g.organization_id
                    )
                    results.append({
                        'id': rid,
                        'success': result.get('success', False),
                        'message': result.get('message'),
                    })
                    if result.get('success'):
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    results.append({
                        'id': rid,
                        'success': False,
                        'error': str(e),
                    })
                    failed_count += 1

            return jsonify({
                'success': failed_count == 0,
                'message': (
                    f'RADIUS configured for '
                    f'{success_count}/{len(router_ids)} routers'
                ),
                'success_count': success_count,
                'failed_count': failed_count,
                'total_count': len(router_ids),
                'results': results,
            }), 200

        except Exception as e:
            logger.error(f"Bulk retry RADIUS error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # SERIALIZATION HELPERS
    # =========================================================================

    def _serialize_router_full(self, router) -> dict:
        """
        Serialize a router with comprehensive detail for single-record views.

        Includes all fields the frontend needs for detail pages and forms.
        """
        settings = router.settings or {}

        return {
            # Identity
            'id': str(router.id),
            'organization_id': str(router.organization_id),
            'network_id': str(router.network_id) if router.network_id else None,
            'name': router.name,
            'model': router.model,
            'firmware_version': router.firmware_version,
            'description': router.description,
            'location': router.location,
            'latitude': router.latitude,
            'longitude': router.longitude,

            # Connection
            'ip_address': str(router.ip_address),
            'api_port': router.api_port,
            'api_ssl_port': router.api_ssl_port,
            'username': router.username,
            'ssh_port': router.ssh_port,
            'connection_pool_size': router.connection_pool_size,

            # Status
            'status': router.status,
            'is_active': router.is_active,
            'last_seen_at': (
                router.last_seen_at.isoformat()
                if router.last_seen_at else None
            ),
            'last_sync_at': (
                router.last_sync_at.isoformat()
                if router.last_sync_at else None
            ),

            # RADIUS Configuration
            'radius_config_status': router.radius_config_status,
            'radius_configured_at': (
                router.radius_configured_at.isoformat()
                if router.radius_configured_at else None
            ),
            'auto_config_attempts': router.auto_config_attempts or 0,
            'last_config_error': router.last_config_error,
            'nas_entry_id': str(router.nas_entry_id) if router.nas_entry_id else None,
            'has_radius_secret': bool(router.radius_secret),

            # Health (from settings JSON)
            'health': settings.get('health', {}),

            # Discovery (from settings JSON)
            'discovery': settings.get('discovery', {}),

            # Timestamps
            'created_at': (
                router.created_at.isoformat()
                if router.created_at else None
            ),
            'updated_at': (
                router.updated_at.isoformat()
                if router.updated_at else None
            ),

            # Settings (non-sensitive only)
            'settings': {
                k: v for k, v in settings.items()
                if k not in ('health', 'discovery')
            },
        }

    def _serialize_router_list(self, router) -> dict:
        """
        Serialize a router with essential fields for list views.

        Lighter payload for paginated list responses.
        """
        return {
            'id': str(router.id),
            'name': router.name,
            'ip_address': str(router.ip_address),
            'model': router.model,
            'status': router.status,
            'is_active': router.is_active,
            'radius_config_status': router.radius_config_status,
            'auto_config_attempts': router.auto_config_attempts or 0,
            'last_seen_at': (
                router.last_seen_at.isoformat()
                if router.last_seen_at else None
            ),
            'network_id': str(router.network_id) if router.network_id else None,
            'location': router.location,
            'created_at': (
                router.created_at.isoformat()
                if router.created_at else None
            ),
        }

    def _get_router_summary(self, organization_id: UUID) -> dict:
        """
        Build summary statistics for router dashboard.

        Returns counts by status and RADIUS configuration state.
        """
        repo = self.service.repository

        total = repo.count_by_organization(organization_id)
        online = repo.count_by_organization(organization_id, status='online')
        offline = repo.count_by_organization(organization_id, status='offline')
        unknown = repo.count_by_organization(organization_id, status='unknown')
        error = repo.count_by_organization(organization_id, status='error')

        radius_configured = repo.count_radius_configured(organization_id)
        radius_pending = repo.count_radius_pending(organization_id)
        radius_failed = repo.count_radius_failed(organization_id)

        return {
            'total': total,
            'counts': {
                'online': online,
                'offline': offline,
                'unknown': unknown,
                'error': error,
            },
            'radius': {
                'configured': radius_configured,
                'pending': radius_pending,
                'failed': radius_failed,
            },
        }