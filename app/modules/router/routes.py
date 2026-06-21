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

from flask import Blueprint, request, jsonify
from marshmallow import ValidationError

from app.modules.router.controller import RouterController
from app.modules.router.schemas import RouterTestSchema
from app.core.security.jwt import token_required
from app.core.logging.logger import logger
from app.integrations.mikrotik.client import MikroTikClient

router_bp = Blueprint('router', __name__, url_prefix='/api/v1/routers')
controller = RouterController()

# PUBLIC ENDPOINTS (no authentication required)
@router_bp.route('/test', methods=['POST'])
def test_connection():
    """
    POST /api/v1/routers/test  (PUBLIC)

    Test connection to a router before adding it to the system.
    Used during the router onboarding wizard to validate credentials.

    Request body:
        {
            "ip_address": "192.168.1.1",
            "username": "admin",
            "password": "password",
            "port": 8728,
            "api_ssl": false
        }

    Responses:
        200: Connection test result (success or failure)
        400: Validation error
        500: Internal server error
    """
    try:
        data = RouterTestSchema().load(request.json)

        # Use MikroTikClient.test_connection() which has proper error handling
        # and the corrected authentication flow
        client = MikroTikClient()

        result = client.test_connection(
            host=data['ip_address'],
            username=data['username'],
            password=data['password'],
            port=data.get('port', 8728),
            use_ssl=data.get('api_ssl', False),
        )

        if result.get('success'):
            return jsonify({
                'success': True,
                'message': 'Connection successful',
                'router_info': result.get('router_info', {}),
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Connection failed'),
                'message': result.get('error', 'Connection failed'),
            }), 200  # 200 — valid response, just negative result

    except ValidationError as e:
        return jsonify({
            'success': False,
            'error': 'Validation error',
            'error_code': 'VALIDATION_ERROR',
            'details': e.messages,
        }), 400
    except Exception as e:
        logger.error(f"Test connection error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500

# DASHBOARD & SUMMARY ROUTES
@router_bp.route('/stats', methods=['GET'])
@token_required
def get_stats():
    """
    GET /api/v1/routers/stats

    Get router statistics summary for dashboards.
    Returns counts by status and RADIUS configuration state.
    """
    return controller.get_stats()


@router_bp.route('/issues', methods=['GET'])
@token_required
def get_issues():
    """
    GET /api/v1/routers/issues

    Get routers that need attention (offline, errors, failed RADIUS).
    """
    return controller.get_issues()

# BULK OPERATIONS (must be before <router_id> routes to avoid conflicts)
@router_bp.route('/bulk/delete', methods=['POST'])
@token_required
def bulk_delete():
    """
    POST /api/v1/routers/bulk/delete

    Bulk delete/deactivate multiple routers.
    """
    return controller.bulk_delete()


@router_bp.route('/bulk/sync', methods=['POST'])
@token_required
def bulk_sync():
    """
    POST /api/v1/routers/bulk/sync

    Bulk sync multiple routers.
    """
    return controller.bulk_sync()


@router_bp.route('/bulk/radius/retry', methods=['POST'])
@token_required
def bulk_retry_radius():
    """
    POST /api/v1/routers/bulk/radius/retry

    Bulk retry RADIUS configuration for multiple routers.
    """
    return controller.bulk_retry_radius()

# COLLECTION ROUTES
@router_bp.route('', methods=['POST'])
@token_required
def create():
    """
    POST /api/v1/routers

    Create a new router with automatic RADIUS configuration.
    """
    return controller.create()


@router_bp.route('', methods=['GET'])
@token_required
def list_routers():
    """
    GET /api/v1/routers

    List routers with filters and pagination.

    Query parameters:
        page, per_page, status, network_id, radius_config_status, search
    """
    return controller.list()

# FILTERED COLLECTION ROUTES
@router_bp.route('/active', methods=['GET'])
@token_required
def get_active():
    """
    GET /api/v1/routers/active

    Get active routers for dropdowns and selection lists.
    """
    return controller.get_active()


@router_bp.route('/pending-radius', methods=['GET'])
@token_required
def get_pending_radius():
    """
    GET /api/v1/routers/pending-radius

    Get routers pending or failed RADIUS configuration.
    """
    return controller.get_pending_radius()


@router_bp.route('/by-network/<network_id>', methods=['GET'])
@token_required
def get_by_network(network_id):
    """
    GET /api/v1/routers/by-network/<network_id>

    Get all routers in a specific network.
    """
    return controller.get_by_network(network_id)

# SINGLE RESOURCE ROUTES
@router_bp.route('/<router_id>', methods=['GET'])
@token_required
def get(router_id):
    """
    GET /api/v1/routers/<router_id>

    Get detailed router information.
    """
    return controller.get(router_id)


@router_bp.route('/<router_id>', methods=['PUT'])
@token_required
def update(router_id):
    """
    PUT /api/v1/routers/<router_id>

    Update router information.
    """
    return controller.update(router_id)


@router_bp.route('/<router_id>', methods=['DELETE'])
@token_required
def delete(router_id):
    """
    DELETE /api/v1/routers/<router_id>?soft=true

    Delete or deactivate a router.
    """
    return controller.delete(router_id)

# CONNECTION & DISCOVERY ROUTES
@router_bp.route('/<router_id>/test', methods=['POST'])
@token_required
def test(router_id):
    """
    POST /api/v1/routers/<router_id>/test

    Test connection to an existing router.
    """
    return controller.test_connection(router_id)


@router_bp.route('/<router_id>/discover', methods=['POST'])
@token_required
def discover(router_id):
    """
    POST /api/v1/routers/<router_id>/discover

    Auto-discover router capabilities (API, SSH, SNMP, Telnet).
    """
    return controller.discover(router_id)


@router_bp.route('/<router_id>/sync', methods=['POST'])
@token_required
def sync(router_id):
    """
    POST /api/v1/routers/<router_id>/sync

    Sync router configuration into the database.
    Pulls hotspot and PPPoE servers from the router.
    """
    return controller.sync(router_id)


@router_bp.route('/<router_id>/health', methods=['GET'])
@token_required
def health(router_id):
    """
    GET /api/v1/routers/<router_id>/health

    Get live health metrics from the router (CPU, memory, uptime).
    """
    return controller.health(router_id)


@router_bp.route('/<router_id>/status', methods=['GET'])
@token_required
def status(router_id):
    """
    GET /api/v1/routers/<router_id>/status

    Get comprehensive connection status and health summary.
    Includes RADIUS configuration status and health metrics.
    """
    return controller.status(router_id)

# RADIUS CONFIGURATION ROUTES
@router_bp.route('/<router_id>/radius', methods=['POST'])
@token_required
def configure_radius(router_id):
    """
    POST /api/v1/routers/<router_id>/radius

    Manually configure RADIUS on the router.
    For routers where auto-configuration is not possible.

    Request body:
        {
            "radius_server": "163.245.217.16",
            "radius_secret": "generated-secret"
        }
    """
    return controller.configure_radius(router_id)


@router_bp.route('/<router_id>/radius/retry', methods=['POST'])
@token_required
def retry_radius_config(router_id):
    """
    POST /api/v1/routers/<router_id>/radius/retry

    Retry RADIUS auto-configuration for a router that previously failed.
    Uses the stored RADIUS secret.
    """
    return controller.retry_radius_config(router_id)


@router_bp.route('/<router_id>/radius/secret', methods=['GET'])
@token_required
def get_radius_secret(router_id):
    """
    GET /api/v1/routers/<router_id>/radius/secret

    Get the RADIUS shared secret for a router.
    Requires 'routers:read_secret' permission.
    Access is logged for security auditing.

    ⚠️ This endpoint returns sensitive data.
    """
    return controller.get_radius_secret(router_id)