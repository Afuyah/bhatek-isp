"""
Router Controller Module
========================
REST API controller for router management with WireGuard VPN, RADIUS,
and Walled Garden configuration.

Provides endpoints for:
    - CRUD operations with tenant isolation
    - WireGuard VPN onboarding
    - Auto-configuration after WireGuard connection
    - Connection testing and health monitoring
    - Auto-discovery of router capabilities
    - RADIUS configuration (auto and manual)
    - Walled garden configuration for captive portal
    - Configuration sync (hotspot/PPPoE servers)
    - Bulk operations (delete, sync, RADIUS retry)
    - Dashboard statistics and summaries

All endpoints require JWT authentication.
Organization context is derived from the authenticated user's token.
"""

from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.router.service import RouterService
from app.modules.router.schemas import (
    RouterCreateSchema,
    RouterUpdateSchema,
    RouterRadiusSchema,
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
    Router API controller with WireGuard VPN, RADIUS, and Walled Garden integration.

    All methods require valid JWT with organization context.
    Tenant isolation is enforced at the service/repository layer.
    """

    def __init__(self):
        self.service = RouterService()

    # =========================================================================
    # CREATE (WIREGUARD-INTEGRATED ONBOARDING)
    # =========================================================================

    @token_required
    def create(self):
        """
        POST /api/v1/routers

        Create a new router with WireGuard VPN + RADIUS + Walled Garden config.

        The system will:
            1. Generate WireGuard keypair + allocate IP in org subnet
            2. Generate RADIUS shared secret
            3. Create Router record + NAS entry
            4. Add WireGuard peer on VPS via SSH
            5. Return MikroTik setup script (includes walled garden commands)

        Admin pastes the script → clicks Test Connection → system auto-configures.
        """
        try:
            data = RouterCreateSchema().load(request.json)

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

            result = self.service.create_router(
                organization_id=g.organization_id,
                network_id=network_uuid,
                data=data,
            )

            router = result.get('router')
            wireguard_info = result.get('wireguard', {})
            radius_info = result.get('radius', {})
            setup_script = result.get('setup_script', '')

            response_data = {
                'success': True,
                'message': (
                    'Router created successfully. '
                    'Paste the setup script into your MikroTik terminal, '
                    'then click Test Connection.'
                ),
                'router': self._serialize_router_full(router),
                'wireguard': {
                    'ip': wireguard_info.get('ip'),
                    'public_key': wireguard_info.get('public_key'),
                    'private_key': wireguard_info.get('private_key'),
                    'peer_added_to_vps': wireguard_info.get('peer_added_to_vps'),
                    '_warning': (
                        'Save the private key now. It will not be shown again.'
                    ),
                },
                'radius': {
                    'server': radius_info.get('server'),
                    'secret': radius_info.get('secret'),
                    'auth_port': radius_info.get('auth_port', 1812),
                    'acct_port': radius_info.get('acct_port', 1813),
                    '_warning': (
                        'Save the RADIUS secret now. It will not be shown again.'
                    ),
                },
                'setup_script': setup_script,
                'next_step': result.get('next_step'),
            }

            if not wireguard_info.get('peer_added_to_vps'):
                response_data['warning'] = (
                    'WireGuard peer could not be added to VPS automatically. '
                    'Please contact support to add it manually.'
                )

            logger.info(
                f"Router created: {router.name} "
                f"(WireGuard IP: {wireguard_info.get('ip')}) "
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
    # AUTO-CONFIGURE AFTER WIREGUARD
    # =========================================================================

    @token_required
    def auto_configure_after_wireguard(self, router_id):
        """
        POST /api/v1/routers/<router_id>/auto-configure

        Run after admin confirms WireGuard tunnel is working.
        Configures RADIUS, Walled Garden, and discovers router capabilities.
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.auto_configure_after_wireguard(
                router_uuid, g.organization_id
            )

            status_code = 200 if result.get('all_success') else 207

            return jsonify({
                'success': result.get('all_success', False),
                'radius_configured': result.get('radius_configured'),
                'walled_garden_configured': result.get('walled_garden_configured', False),
                'discovered': result.get('discovered'),
                'discovery': result.get('discovery'),
                'steps': result.get('steps'),
                'message': (
                    'Auto-configuration complete. Router is fully operational.'
                    if result.get('all_success')
                    else 'Some steps failed. Check details below.'
                ),
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
            logger.error(f"Auto-configure error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # WALLED GARDEN CONFIGURATION
    # =========================================================================

    @token_required
    def configure_walled_garden(self, router_id):
        """
        POST /api/v1/routers/<router_id>/walled-garden

        Configure walled garden on the MikroTik router.
        Allows the captive portal to be accessible without internet access.

        Request body (optional):
            {
                "platform_domain": "isp.bhatek.space",
                "additional_domains": ["custom-isp-domain.com"]
            }
        """
        try:
            router_uuid = UUID(router_id)
            data = request.get_json() or {}

            result = self.service.configure_walled_garden(
                router_id=router_uuid,
                organization_id=g.organization_id,
            )

            return jsonify({
                'success': result.get('success', False),
                'dns_added': result.get('dns_added', False),
                'domains_added': result.get('domains_added', 0),
                'errors': result.get('errors', []),
                'message': (
                    f"Walled garden configured: {result.get('domains_added', 0)} domains added"
                    if result.get('success')
                    else 'Walled garden configuration had issues'
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
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'WALLED_GARDEN_FAILED',
            }), 500
        except Exception as e:
            logger.error(f"Configure walled garden error: {e}", exc_info=True)
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
        """GET /api/v1/routers/<router_id>"""
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
        """GET /api/v1/routers — List routers with pagination and filters."""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page

            status = request.args.get('status')
            network_id = request.args.get('network_id')
            radius_config_status = request.args.get('radius_config_status')
            search = request.args.get('search')

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

            routers = self.service.get_routers_by_organization(
                organization_id=g.organization_id,
                skip=skip, limit=per_page,
                status=status, network_id=network_uuid,
                radius_config_status=radius_config_status,
            )

            if search:
                search_lower = search.lower()
                routers = [
                    r for r in routers
                    if search_lower in r.name.lower()
                    or search_lower in str(r.ip_address)
                ]

            total = self.service.repository.count_by_organization(
                g.organization_id, status=status,
                radius_config_status=radius_config_status,
            )

            summary = self._get_router_summary(g.organization_id)

            return jsonify({
                'success': True,
                'routers': [self._serialize_router_list(r) for r in routers],
                'summary': summary,
                'pagination': {
                    'page': page, 'per_page': per_page, 'total': total,
                    'pages': (total + per_page - 1) // per_page if total > 0 else 0,
                    'has_next': (page * per_page) < total,
                    'has_prev': page > 1,
                },
                'filters_applied': {
                    'status': status, 'network_id': network_id,
                    'radius_config_status': radius_config_status, 'search': search,
                },
            }), 200
        except Exception as e:
            logger.error(f"List routers error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # FILTERED VIEWS
    # =========================================================================

    @token_required
    def get_by_network(self, network_id):
        """GET /api/v1/routers/by-network/<network_id>"""
        try:
            network_uuid = UUID(network_id)
            routers = self.service.get_routers_by_network(network_uuid, g.organization_id)
            return jsonify({
                'success': True, 'network_id': network_id,
                'routers': [self._serialize_router_list(r) for r in routers],
                'count': len(routers),
            }), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid network ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(f"Get routers by network error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_active(self):
        """GET /api/v1/routers/active"""
        try:
            routers = self.service.repository.get_all_active(g.organization_id)
            return jsonify({
                'success': True,
                'routers': [{
                    'id': str(r.id), 'name': r.name,
                    'ip_address': str(r.ip_address), 'wireguard_ip': r.wireguard_ip,
                    'status': r.status, 'radius_config_status': r.radius_config_status,
                    'model': r.model,
                    'network_id': str(r.network_id) if r.network_id else None,
                } for r in routers],
                'count': len(routers),
            }), 200
        except Exception as e:
            logger.error(f"Get active routers error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_pending_radius(self):
        """GET /api/v1/routers/pending-radius"""
        try:
            routers = self.service.get_routers_pending_radius_config(g.organization_id)
            return jsonify({
                'success': True,
                'routers': [{
                    'id': str(r.id), 'name': r.name,
                    'ip_address': str(r.ip_address), 'wireguard_ip': r.wireguard_ip,
                    'radius_config_status': r.radius_config_status,
                    'auto_config_attempts': r.auto_config_attempts or 0,
                    'last_config_error': r.last_config_error,
                    'status': r.status,
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                } for r in routers],
                'count': len(routers),
                'counts': {
                    'pending': sum(1 for r in routers if r.radius_config_status == 'pending'),
                    'failed': sum(1 for r in routers if r.radius_config_status == 'failed'),
                },
            }), 200
        except Exception as e:
            logger.error(f"Get pending RADIUS error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_issues(self):
        """GET /api/v1/routers/issues"""
        try:
            routers = self.service.repository.get_routers_with_issues(g.organization_id)
            return jsonify({
                'success': True,
                'routers': [{
                    'id': str(r.id), 'name': r.name,
                    'ip_address': str(r.ip_address), 'wireguard_ip': r.wireguard_ip,
                    'status': r.status, 'radius_config_status': r.radius_config_status,
                    'auto_config_attempts': r.auto_config_attempts or 0,
                    'last_config_error': r.last_config_error,
                    'last_seen_at': r.last_seen_at.isoformat() if r.last_seen_at else None,
                } for r in routers],
                'count': len(routers),
            }), 200
        except Exception as e:
            logger.error(f"Get router issues error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_stats(self):
        """GET /api/v1/routers/stats"""
        try:
            summary = self._get_router_summary(g.organization_id)
            summary['total_active'] = summary['counts']['online'] + summary['counts']['unknown']
            summary['health_percentage'] = round(
                summary['counts']['online'] / max(summary['total'], 1) * 100, 1
            )
            return jsonify({'success': True, 'stats': summary}), 200
        except Exception as e:
            logger.error(f"Get router stats error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # UPDATE
    # =========================================================================

    @token_required
    def update(self, router_id):
        """PUT /api/v1/routers/<router_id>"""
        try:
            router_uuid = UUID(router_id)
            data = RouterUpdateSchema().load(request.json)
            router = self.service.update_router(router_uuid, g.organization_id, data)
            logger.info(f"Router {router_id} updated by user {g.user_id}")
            return jsonify({
                'success': True, 'message': 'Router updated successfully',
                'router': self._serialize_router_full(router),
            }), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except ValidationError as e:
            return jsonify({
                'success': False, 'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR', 'details': e.messages,
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Update router error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # DELETE
    # =========================================================================

    @token_required
    def delete(self, router_id):
        """DELETE /api/v1/routers/<router_id>?soft=true"""
        try:
            router_uuid = UUID(router_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            self.service.delete_router(router_uuid, g.organization_id, soft_delete=soft)
            message = 'Router deactivated successfully' if soft else 'Router permanently deleted'
            logger.info(f"Router {router_id} {'deactivated' if soft else 'deleted'} by user {g.user_id}")
            return jsonify({'success': True, 'message': message, 'soft_delete': soft}), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'BUSINESS_ERROR',
            }), 409
        except Exception as e:
            logger.error(f"Delete router error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # CONNECTION & DISCOVERY
    # =========================================================================

    @token_required
    def test_connection(self, router_id):
        """POST /api/v1/routers/<router_id>/test"""
        try:
            router_uuid = UUID(router_id)
            result = self.service.test_connection(router_uuid, g.organization_id)
            return jsonify({'success': True, 'connection': result}), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'CONNECTION_ERROR',
                'connection': {'success': False, 'connected': False},
            }), 200
        except Exception as e:
            logger.error(f"Test connection error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def discover(self, router_id):
        """POST /api/v1/routers/<router_id>/discover"""
        try:
            router_uuid = UUID(router_id)
            result = self.service.discover_router(router_uuid, g.organization_id)
            status_code = 200 if result.get('success') else 207
            return jsonify({
                'success': result.get('success', False),
                'method': result.get('method'), 'info': result.get('info'),
                'attempts': result.get('attempts'), 'message': result.get('message'),
            }), status_code
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Discover router error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def health(self, router_id):
        """GET /api/v1/routers/<router_id>/health"""
        try:
            router_uuid = UUID(router_id)
            health = self.service.update_health(router_uuid, g.organization_id)
            return jsonify({'success': True, 'health': health}), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'HEALTH_CHECK_FAILED',
                'health': None,
            }), 200
        except Exception as e:
            logger.error(f"Health check error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def status(self, router_id):
        """GET /api/v1/routers/<router_id>/status"""
        try:
            router_uuid = UUID(router_id)
            status = self.service.get_connection_status(router_uuid, g.organization_id)
            return jsonify({'success': True, 'status': status}), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get status error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def sync(self, router_id):
        """POST /api/v1/routers/<router_id>/sync"""
        try:
            router_uuid = UUID(router_id)
            result = self.service.sync_router(router_uuid, g.organization_id)
            return jsonify({'success': True, 'sync_result': result}), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'SYNC_FAILED',
            }), 500
        except Exception as e:
            logger.error(f"Sync router error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # RADIUS CONFIGURATION
    # =========================================================================

    @token_required
    def configure_radius(self, router_id):
        """POST /api/v1/routers/<router_id>/radius"""
        try:
            router_uuid = UUID(router_id)
            data = RouterRadiusSchema().load(request.json)
            result = self.service.configure_radius_manual(
                router_id=router_uuid, organization_id=g.organization_id,
                radius_server=data['radius_server'], radius_secret=data['radius_secret'],
            )
            return jsonify({
                'success': result.get('success', False),
                'message': result.get('message', result.get('error', 'Unknown result')),
            }), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except ValidationError as e:
            return jsonify({
                'success': False, 'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR', 'details': e.messages,
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'RADIUS_CONFIG_FAILED',
            }), 500
        except Exception as e:
            logger.error(f"Configure RADIUS error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def retry_radius_config(self, router_id):
        """POST /api/v1/routers/<router_id>/radius/retry"""
        try:
            router_uuid = UUID(router_id)
            result = self.service.retry_radius_configuration(router_uuid, g.organization_id)
            if result.get('success'):
                return jsonify({
                    'success': True, 'message': result.get('message'),
                    'radius_server_ip': result.get('radius_server_ip'),
                }), 200
            else:
                return jsonify({
                    'success': False, 'message': result.get('message'),
                }), 207
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'RADIUS_RETRY_FAILED',
            }), 409
        except Exception as e:
            logger.error(f"Retry RADIUS config error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('routers:read_secret')
    def get_radius_secret(self, router_id):
        """GET /api/v1/routers/<router_id>/radius/secret"""
        try:
            router_uuid = UUID(router_id)
            router = self.service.get_router(router_uuid, g.organization_id)
            if not router.radius_secret:
                return jsonify({
                    'success': False, 'error': 'No RADIUS secret configured',
                    'error_code': 'NO_SECRET',
                }), 404
            logger.warning(f"RADIUS secret accessed for router {router_id} by user {g.user_id}")
            return jsonify({
                'success': True, 'router_id': str(router.id), 'router_name': router.name,
                'radius_secret': router.radius_secret,
                'radius_server_ip': self.service._get_radius_server(),
                '_warning': 'This secret is sensitive. Keep it secure.',
            }), 200
        except ValueError:
            return jsonify({
                'success': False, 'error': 'Invalid router ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False, 'error': str(e), 'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get RADIUS secret error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    @token_required
    def bulk_delete(self):
        """POST /api/v1/routers/bulk/delete"""
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])
            soft = data.get('soft', True)
            if not router_ids:
                return jsonify({
                    'success': False, 'error': 'No router IDs provided',
                    'error_code': 'MISSING_IDS',
                }), 400
            deleted_count, errors = 0, []
            for rid in router_ids:
                try:
                    self.service.delete_router(UUID(rid), g.organization_id, soft_delete=soft)
                    deleted_count += 1
                except Exception as e:
                    errors.append({'id': rid, 'error': str(e)})
            return jsonify({
                'success': len(errors) == 0,
                'message': f'{deleted_count}/{len(router_ids)} routers {"deactivated" if soft else "deleted"}',
                'deleted_count': deleted_count, 'total_count': len(router_ids), 'errors': errors,
            }), 200
        except Exception as e:
            logger.error(f"Bulk delete error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def bulk_sync(self):
        """POST /api/v1/routers/bulk/sync"""
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])
            if not router_ids:
                return jsonify({
                    'success': False, 'error': 'No router IDs provided',
                    'error_code': 'MISSING_IDS',
                }), 400
            results, synced, failed = [], 0, 0
            for rid in router_ids:
                try:
                    result = self.service.sync_router(UUID(rid), g.organization_id)
                    results.append({'id': rid, 'success': True, 'result': result})
                    synced += 1
                except Exception as e:
                    results.append({'id': rid, 'success': False, 'error': str(e)})
                    failed += 1
            return jsonify({
                'success': failed == 0,
                'message': f'Synced {synced}/{len(router_ids)} routers',
                'synced_count': synced, 'failed_count': failed,
                'total_count': len(router_ids), 'results': results,
            }), 200
        except Exception as e:
            logger.error(f"Bulk sync error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def bulk_retry_radius(self):
        """POST /api/v1/routers/bulk/radius/retry"""
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])
            if not router_ids:
                return jsonify({
                    'success': False, 'error': 'No router IDs provided',
                    'error_code': 'MISSING_IDS',
                }), 400
            results, success_count, failed_count = [], 0, 0
            for rid in router_ids:
                try:
                    result = self.service.retry_radius_configuration(UUID(rid), g.organization_id)
                    results.append({'id': rid, 'success': result.get('success', False), 'message': result.get('message')})
                    if result.get('success'): success_count += 1
                    else: failed_count += 1
                except Exception as e:
                    results.append({'id': rid, 'success': False, 'error': str(e)})
                    failed_count += 1
            return jsonify({
                'success': failed_count == 0,
                'message': f'RADIUS configured for {success_count}/{len(router_ids)} routers',
                'success_count': success_count, 'failed_count': failed_count,
                'total_count': len(router_ids), 'results': results,
            }), 200
        except Exception as e:
            logger.error(f"Bulk retry RADIUS error: {e}", exc_info=True)
            return jsonify({
                'success': False, 'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # SERIALIZATION HELPERS
    # =========================================================================

    def _serialize_router_full(self, router) -> dict:
        settings = router.settings or {}
        return {
            'id': str(router.id),
            'organization_id': str(router.organization_id),
            'network_id': str(router.network_id) if router.network_id else None,
            'name': router.name, 'model': router.model,
            'firmware_version': router.firmware_version,
            'description': router.description, 'location': router.location,
            'latitude': router.latitude, 'longitude': router.longitude,
            'ip_address': str(router.ip_address),
            'local_ip': router.local_ip, 'wireguard_ip': router.wireguard_ip,
            'wireguard_public_key': router.wireguard_public_key,
            'api_port': router.api_port, 'api_ssl_port': router.api_ssl_port,
            'username': router.username, 'ssh_port': router.ssh_port,
            'connection_pool_size': router.connection_pool_size,
            'status': router.status, 'is_active': router.is_active,
            'last_seen_at': router.last_seen_at.isoformat() if router.last_seen_at else None,
            'last_sync_at': router.last_sync_at.isoformat() if router.last_sync_at else None,
            'radius_config_status': router.radius_config_status,
            'radius_configured_at': router.radius_configured_at.isoformat() if router.radius_configured_at else None,
            'auto_config_attempts': router.auto_config_attempts or 0,
            'last_config_error': router.last_config_error,
            'nas_entry_id': str(router.nas_entry_id) if router.nas_entry_id else None,
            'has_radius_secret': bool(router.radius_secret),
            'health': settings.get('health', {}),
            'discovery': settings.get('discovery', {}),
            'created_at': router.created_at.isoformat() if router.created_at else None,
            'updated_at': router.updated_at.isoformat() if router.updated_at else None,
            'settings': {k: v for k, v in settings.items() if k not in ('health', 'discovery')},
        }

    def _serialize_router_list(self, router) -> dict:
        return {
            'id': str(router.id), 'name': router.name,
            'ip_address': str(router.ip_address), 'wireguard_ip': router.wireguard_ip,
            'local_ip': router.local_ip, 'model': router.model,
            'status': router.status, 'is_active': router.is_active,
            'radius_config_status': router.radius_config_status,
            'auto_config_attempts': router.auto_config_attempts or 0,
            'last_seen_at': router.last_seen_at.isoformat() if router.last_seen_at else None,
            'network_id': str(router.network_id) if router.network_id else None,
            'location': router.location,
            'created_at': router.created_at.isoformat() if router.created_at else None,
        }

    def _get_router_summary(self, organization_id: UUID) -> dict:
        repo = self.service.repository
        total = repo.count_by_organization(organization_id)
        return {
            'total': total,
            'counts': {
                'online': repo.count_by_organization(organization_id, status='online'),
                'offline': repo.count_by_organization(organization_id, status='offline'),
                'unknown': repo.count_by_organization(organization_id, status='unknown'),
                'error': repo.count_by_organization(organization_id, status='error'),
            },
            'radius': {
                'configured': repo.count_radius_configured(organization_id),
                'pending': repo.count_radius_pending(organization_id),
                'failed': repo.count_radius_failed(organization_id),
            },
        }