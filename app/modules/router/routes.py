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
        ├── POST   /<router_id>/walled-garden     (auth)   Configure walled garden
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
    """
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


# BULK OPERATIONS (must be before <router_id> routes)

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


# COLLECTION ROUTES

@router_bp.route('', methods=['POST'])
@token_required
def create():
    """POST /api/v1/routers — Create router with WireGuard + RADIUS + Walled Garden."""
    return controller.create()


@router_bp.route('', methods=['GET'])
@token_required
def list_routers():
    """GET /api/v1/routers — List routers with filters and pagination."""
    return controller.list()


# FILTERED COLLECTION ROUTES

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


# POST-ONBOARDING PROVISIONING


@router_bp.route('/<router_id>/provision-plan', methods=['GET', 'POST'])
@token_required
def provision_plan(router_id):
    """Preview hardening/topology/hotspot without touching the device."""
    return controller.provision_plan(router_id)


@router_bp.route('/<router_id>/provision', methods=['POST'])
@token_required
def provision(router_id):
    """Apply hardening, LAN/port roles and hotspot configuration."""
    return controller.provision(router_id)


@router_bp.route('/<router_id>/onboarding-link', methods=['POST'])
@token_required
def regenerate_onboarding_link(router_id):
    """Issue a fresh provisioning link, invalidating the previous one."""
    return controller.regenerate_onboarding_link(router_id)
