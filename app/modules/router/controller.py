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
    """Router API controller with WireGuard VPN and RADIUS integration."""

    def __init__(self):
        self.service = RouterService()

    # =========================================================================
    # CREATE (WIREGUARD-INTEGRATED)
    # =========================================================================

    @token_required
    def create(self):
        """
        POST /api/v1/routers

        Create a new router with WireGuard VPN + RADIUS configuration.

        The system:
            1. Generates WireGuard keypair + allocates IP in org subnet
            2. Creates Router + NAS records
            3. Adds WireGuard peer on VPS via SSH
            4. Returns stepped MikroTik setup script for admin
            5. Admin pastes script → clicks Test Connection → system auto-configures
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
            setup_script = result.get('setup_script', {})

            response_data = {
                'success': True,
                'message': 'Router created. Paste the WireGuard script into your MikroTik terminal.',
                'router': self._serialize_router_full(router),
                'wireguard': {
                    'ip': wireguard_info.get('ip'),
                    'public_key': wireguard_info.get('public_key'),
                    'private_key': wireguard_info.get('private_key'),
                    'peer_added_to_vps': wireguard_info.get('peer_added_to_vps'),
                },
                'radius': {
                    'secret': radius_info.get('secret'),
                    'server': radius_info.get('server'),
                },
                'setup_script': setup_script,
                'next_step': result.get('next_step'),
            }

            # Warn if VPS peer addition failed
            if not wireguard_info.get('peer_added_to_vps'):
                response_data['warning'] = (
                    'WireGuard peer could not be added to VPS automatically. '
                    'Please contact support.'
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
        Configures RADIUS and discovers router capabilities via the tunnel.
        """
        try:
            router_uuid = UUID(router_id)
            result = self.service.auto_configure_after_wireguard(
                router_uuid, g.organization_id
            )

            return jsonify({
                'success': result.get('all_success', False),
                'radius_configured': result.get('radius_configured'),
                'discovered': result.get('discovered'),
                'discovery': result.get('discovery'),
                'steps': result.get('steps'),
                'message': (
                    'Auto-configuration complete.'
                    if result.get('all_success')
                    else 'Some steps failed. Check details.'
                ),
            }), 200 if result.get('all_success') else 207

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
    # READ — SINGLE
    # =========================================================================

    @token_required
    def get(self, router_id):
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
    # READ — LIST
    # =========================================================================

    @token_required
    def list(self):
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
                status=status,
                network_id=network_uuid,
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
                g.organization_id,
                status=status,
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
    # FILTERED VIEWS
    # =========================================================================

    @token_required
    def get_by_network(self, network_id):
        try:
            network_uuid = UUID(network_id)
            routers = self.service.get_routers_by_network(network_uuid, g.organization_id)
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
        try:
            routers = self.service.repository.get_all_active(g.organization_id)
            return jsonify({
                'success': True,
                'routers': [{
                    'id': str(r.id), 'name': r.name,
                    'ip_address': str(r.ip_address), 'status': r.status,
                    'radius_config_status': r.radius_config_status,
                    'model': r.model,
                    'network_id': str(r.network_id) if r.network_id else None,
                } for r in routers],
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
        try:
            routers = self.service.get_routers_pending_radius_config(g.organization_id)
            return jsonify({
                'success': True,
                'routers': [{
                    'id': str(r.id), 'name': r.name,
                    'ip_address': str(r.ip_address),
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
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_issues(self):
        try:
            routers = self.service.repository.get_routers_with_issues(g.organization_id)
            return jsonify({
                'success': True,
                'routers': [{
                    'id': str(r.id), 'name': r.name,
                    'ip_address': str(r.ip_address), 'status': r.status,
                    'radius_config_status': r.radius_config_status,
                    'auto_config_attempts': r.auto_config_attempts or 0,
                    'last_config_error': r.last_config_error,
                    'last_seen_at': r.last_seen_at.isoformat() if r.last_seen_at else None,
                } for r in routers],
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
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # UPDATE
    # =========================================================================

    @token_required
    def update(self, router_id):
        try:
            router_uuid = UUID(router_id)
            data = RouterUpdateSchema().load(request.json)
            router = self.service.update_router(router_uuid, g.organization_id, data)
            logger.info(f"Router {router_id} updated by user {g.user_id}")
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
        try:
            router_uuid = UUID(router_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            self.service.delete_router(router_uuid, g.organization_id, soft_delete=soft)
            message = 'Router deactivated' if soft else 'Router permanently deleted'
            logger.info(f"Router {router_id} {message} by user {g.user_id}")
            return jsonify({
                'success': True, 'message': f'{message} successfully',
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
        try:
            router_uuid = UUID(router_id)
            result = self.service.test_connection(router_uuid, g.organization_id)
            return jsonify({'success': True, 'connection': result}), 200
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
            }), 200
        except Exception as e:
            logger.error(f"Test connection error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def discover(self, router_id):
        try:
            router_uuid = UUID(router_id)
            result = self.service.discover_router(router_uuid, g.organization_id)
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
        try:
            router_uuid = UUID(router_id)
            health = self.service.update_health(router_uuid, g.organization_id)
            return jsonify({'success': True, 'health': health}), 200
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
            }), 200
        except Exception as e:
            logger.error(f"Health check error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def status(self, router_id):
        try:
            router_uuid = UUID(router_id)
            status = self.service.get_connection_status(router_uuid, g.organization_id)
            return jsonify({'success': True, 'status': status}), 200
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

    @token_required
    def sync(self, router_id):
        try:
            router_uuid = UUID(router_id)
            result = self.service.sync_router(router_uuid, g.organization_id)
            return jsonify({'success': True, 'sync_result': result}), 200
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
        try:
            router_uuid = UUID(router_id)
            result = self.service.retry_radius_configuration(router_uuid, g.organization_id)
            if result.get('success'):
                return jsonify({
                    'success': True, 'message': result.get('message'),
                }), 200
            else:
                return jsonify({
                    'success': False, 'message': result.get('message'),
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
        try:
            router_uuid = UUID(router_id)
            router = self.service.get_router(router_uuid, g.organization_id)
            if not router.radius_secret:
                return jsonify({
                    'success': False,
                    'error': 'No RADIUS secret configured',
                    'error_code': 'NO_SECRET',
                }), 404
            logger.warning(f"RADIUS secret accessed for router {router_id} by user {g.user_id}")
            return jsonify({
                'success': True,
                'router_id': str(router.id),
                'router_name': router.name,
                'radius_secret': router.radius_secret,
                'radius_server_ip': self.service._get_radius_server(),
                '_warning': 'This secret is sensitive. Keep it secure.',
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
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])
            if not router_ids:
                return jsonify({
                    'success': False,
                    'error': 'No router IDs provided',
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
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def bulk_retry_radius(self):
        try:
            data = request.get_json() or {}
            router_ids = data.get('router_ids', [])
            if not router_ids:
                return jsonify({
                    'success': False,
                    'error': 'No router IDs provided',
                    'error_code': 'MISSING_IDS',
                }), 400
            results, success_count, failed_count = [], 0, 0
            for rid in router_ids:
                try:
                    result = self.service.retry_radius_configuration(UUID(rid), g.organization_id)
                    results.append({'id': rid, 'success': result.get('success', False), 'message': result.get('message')})
                    if result.get('success'):
                        success_count += 1
                    else:
                        failed_count += 1
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
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # SERIALIZATION
    # =========================================================================

    def _serialize_router_full(self, router) -> dict:
        settings = router.settings or {}
        return {
            'id': str(router.id),
            'organization_id': str(router.organization_id),
            'network_id': str(router.network_id) if router.network_id else None,
            'name': router.name, 'model': router.model,
            'firmware_version': router.firmware_version,
            'description': router.description,
            'location': router.location,
            'ip_address': str(router.ip_address),
            'local_ip': router.local_ip,
            'wireguard_ip': router.wireguard_ip,
            'api_port': router.api_port,
            'username': router.username,
            'status': router.status,
            'is_active': router.is_active,
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
        }

    def _serialize_router_list(self, router) -> dict:
        return {
            'id': str(router.id), 'name': router.name,
            'ip_address': str(router.ip_address),
            'wireguard_ip': router.wireguard_ip,
            'local_ip': router.local_ip,
            'model': router.model, 'status': router.status,
            'is_active': router.is_active,
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