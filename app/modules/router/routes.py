"""
Router API Routes
=================
Blueprint for all router management endpoints.

Route Structure:
    /api/v1/routers/
        ├── POST   /test                          (public) Test connection before adding
        ├── GET    /stats                         (auth)   Dashboard statistics
        ├── GET    /issues                        (auth)   Routers needing attention
        ├── GET    /active                        (auth)   Active routers for dropdowns
        ├── GET    /pending-radius                (auth)   Routers needing RADIUS config
        ├── GET    /by-network/<network_id>       (auth)   Routers in a network
        │
        ├── POST   /                              (auth)   Create router
        ├── GET    /                              (auth)   List routers (paginated)
        │
        ├── GET    /<router_id>                   (auth)   Get router detail
        ├── PUT    /<router_id>                   (auth)   Update router
        ├── DELETE /<router_id>                   (auth)   Delete/deactivate router
        │
        ├── POST   /<router_id>/test              (auth)   Test connection
        ├── POST   /<router_id>/auto-configure    (auth)   Auto-configure after WireGuard
        ├── POST   /<router_id>/discover          (auth)   Auto-discover capabilities
        ├── POST   /<router_id>/sync              (auth)   Sync hotspot/PPPoE servers
        ├── GET    /<router_id>/health            (auth)   Live health metrics
        ├── GET    /<router_id>/status            (auth)   Connection status summary
        │
        ├── POST   /<router_id>/radius            (auth)   Manual RADIUS config
        ├── POST   /<router_id>/radius/retry      (auth)   Retry auto RADIUS config
        ├── GET    /<router_id>/radius/secret     (auth)   Get RADIUS secret (audited)
        │
        ├── POST   /bulk/delete                   (auth)   Bulk delete
        ├── POST   /bulk/sync                     (auth)   Bulk sync
        └── POST   /bulk/radius/retry             (auth)   Bulk RADIUS retry
"""

from flask import Blueprint
from marshmallow import ValidationError

from app.modules.router.controller import RouterController
from app.modules.router.schemas import RouterTestSchema
from app.core.security.jwt import token_required
from app.core.logging.logger import logger
from app.integrations.mikrotik.client import MikroTikClient

router_bp = Blueprint('router', __name__, url_prefix='/api/v1/routers')
controller = RouterController()
# PUBLIC ENDPOINTS
@router_bp.route('/test', methods=['POST'])
def test_connection():
    """POST /api/v1/routers/test — Test connection before adding router."""
    try:
        data = RouterTestSchema().load(request.json)
        client = MikroTikClient()
        result = client.test_connection(
            host=data['ip_address'],
            username=data['username'],
            password=data['password'],
            port=data.get('port', 8728),
            use_ssl=data.get('api_ssl', False),
        )
        return jsonify({
            'success': result.get('success', False),
            'message': 'Connection successful' if result.get('success') else result.get('error'),
            'router_info': result.get('router_info', {}),
        }), 200
    except ValidationError as e:
        return jsonify({
            'success': False, 'error': 'Validation error',
            'error_code': 'VALIDATION_ERROR', 'details': e.messages,
        }), 400
    except Exception as e:
        logger.error(f"Test connection error: {e}", exc_info=True)
        return jsonify({
            'success': False, 'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500
# DASHBOARD & SUMMARY
@router_bp.route('/stats', methods=['GET'])
@token_required
def get_stats():
    """GET /api/v1/routers/stats — Dashboard statistics."""
    return controller.get_stats()

@router_bp.route('/issues', methods=['GET'])
@token_required
def get_issues():
    """GET /api/v1/routers/issues — Routers needing attention."""
    return controller.get_issues()
# BULK OPERATIONS
@router_bp.route('/bulk/delete', methods=['POST'])
@token_required
def bulk_delete():
    """POST /api/v1/routers/bulk/delete — Bulk delete routers."""
    return controller.bulk_delete()

@router_bp.route('/bulk/sync', methods=['POST'])
@token_required
def bulk_sync():
    """POST /api/v1/routers/bulk/sync — Bulk sync routers."""
    return controller.bulk_sync()

@router_bp.route('/bulk/radius/retry', methods=['POST'])
@token_required
def bulk_retry_radius():
    """POST /api/v1/routers/bulk/radius/retry — Bulk retry RADIUS config."""
    return controller.bulk_retry_radius()
# COLLECTION
@router_bp.route('', methods=['POST'])
@token_required
def create():
    """POST /api/v1/routers — Create router with WireGuard + RADIUS."""
    return controller.create()

@router_bp.route('', methods=['GET'])
@token_required
def list_routers():
    """GET /api/v1/routers — List routers with filters and pagination."""
    return controller.list()
# FILTERED COLLECTION
@router_bp.route('/active', methods=['GET'])
@token_required
def get_active():
    """GET /api/v1/routers/active — Active routers for dropdowns."""
    return controller.get_active()

@router_bp.route('/pending-radius', methods=['GET'])
@token_required
def get_pending_radius():
    """GET /api/v1/routers/pending-radius — Routers needing RADIUS config."""
    return controller.get_pending_radius()

@router_bp.route('/by-network/<network_id>', methods=['GET'])
@token_required
def get_by_network(network_id):
    """GET /api/v1/routers/by-network/<network_id> — Routers in a network."""
    return controller.get_by_network(network_id)
# SINGLE RESOURCE
@router_bp.route('/<router_id>', methods=['GET'])
@token_required
def get(router_id):
    """GET /api/v1/routers/<router_id> — Router detail."""
    return controller.get(router_id)

@router_bp.route('/<router_id>', methods=['PUT'])
@token_required
def update(router_id):
    """PUT /api/v1/routers/<router_id> — Update router."""
    return controller.update(router_id)

@router_bp.route('/<router_id>', methods=['DELETE'])
@token_required
def delete(router_id):
    """DELETE /api/v1/routers/<router_id> — Delete/deactivate router."""
    return controller.delete(router_id)
# CONNECTION & DISCOVERY
@router_bp.route('/<router_id>/test', methods=['POST'])
@token_required
def test(router_id):
    """POST /api/v1/routers/<router_id>/test — Test connection."""
    return controller.test_connection(router_id)

@router_bp.route('/<router_id>/auto-configure', methods=['POST'])
@token_required
def auto_configure_after_wireguard(router_id):
    """POST /api/v1/routers/<router_id>/auto-configure — Auto-configure after WireGuard."""
    return controller.auto_configure_after_wireguard(router_id)

@router_bp.route('/<router_id>/discover', methods=['POST'])
@token_required
def discover(router_id):
    """POST /api/v1/routers/<router_id>/discover — Auto-discover capabilities."""
    return controller.discover(router_id)

@router_bp.route('/<router_id>/sync', methods=['POST'])
@token_required
def sync(router_id):
    """POST /api/v1/routers/<router_id>/sync — Sync hotspot/PPPoE servers."""
    return controller.sync(router_id)

@router_bp.route('/<router_id>/health', methods=['GET'])
@token_required
def health(router_id):
    """GET /api/v1/routers/<router_id>/health — Live health metrics."""
    return controller.health(router_id)

@router_bp.route('/<router_id>/status', methods=['GET'])
@token_required
def status(router_id):
    """GET /api/v1/routers/<router_id>/status — Connection status summary."""
    return controller.status(router_id)
# RADIUS CONFIGURATION
@router_bp.route('/<router_id>/radius', methods=['POST'])
@token_required
def configure_radius(router_id):
    """POST /api/v1/routers/<router_id>/radius — Manual RADIUS config."""
    return controller.configure_radius(router_id)

@router_bp.route('/<router_id>/radius/retry', methods=['POST'])
@token_required
def retry_radius_config(router_id):
    """POST /api/v1/routers/<router_id>/radius/retry — Retry RADIUS auto-config."""
    return controller.retry_radius_config(router_id)

@router_bp.route('/<router_id>/radius/secret', methods=['GET'])
@token_required
def get_radius_secret(router_id):
    """GET /api/v1/routers/<router_id>/radius/secret — Get RADIUS secret (audited)."""
    return controller.get_radius_secret(router_id)