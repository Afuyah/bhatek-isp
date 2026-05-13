# app/modules/router/routes.py
from flask import Blueprint, request, jsonify, current_app
from marshmallow import ValidationError

from app.modules.router.controller import RouterController
from app.modules.router.schemas import RouterTestSchema
from app.core.security.jwt import token_required
from app.core.logging.logger import logger

router_bp = Blueprint('router', __name__, url_prefix='/api/v1/routers')
controller = RouterController()

# PUBLIC ENDPOINTS (no authentication required)
@router_bp.route('/test', methods=['POST'])
def test_connection():
    """Test connection to a router before adding (public)
    
    Expected payload:
    {
        "ip_address": "192.168.1.1",
        "username": "admin",
        "password": "password",
        "port": 8728
    }
    """
    try:
        data = RouterTestSchema().load(request.json)
        
        from app.integrations.mikrotik.client import MikroTikConnection
        
        # Create a temporary connection to test
        conn = None
        try:
            conn = MikroTikConnection(
                host=data['ip_address'],
                username=data['username'],
                password=data['password'],
                port=data.get('port', 8728),
                use_ssl=data.get('api_ssl', False),
                timeout=8
            )
            conn.connect()
            
            # Try to get system resource as a test
            result = conn.execute('/system/resource/print')
            
            if result and len(result) > 0:
                resource = result[0]
                
                # Get identity as well
                identity_result = conn.execute('/system/identity/print')
                identity = identity_result[0] if identity_result else {}
                
                return jsonify({
                    'success': True,
                    'message': 'Connection successful',
                    'router_info': {
                        'name': identity.get('name', 'Unknown'),
                        'version': resource.get('version', 'Unknown'),
                        'board_name': resource.get('board-name', 'Unknown'),
                        'cpu': resource.get('cpu-load', 'Unknown'),
                        'uptime': resource.get('uptime', 'Unknown'),
                        'free_memory': resource.get('free-memory', 'Unknown'),
                        'total_memory': resource.get('total-memory', 'Unknown')
                    }
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'error': 'No response from router'
                }), 401
                
        except TimeoutError as e:
            return jsonify({
                'success': False,
                'error': 'Connection timeout. Router not reachable or API port is closed.',
                'message': f'Timeout connecting to {data["ip_address"]}:{data.get("port", 8728)}'
            }), 401
        except ConnectionRefusedError as e:
            return jsonify({
                'success': False,
                'error': 'Connection refused. Check if API is enabled on the router.',
                'message': f'Port {data.get("port", 8728)} may be closed or API service not running'
            }), 401
        except Exception as e:
            error_msg = str(e).lower()
            if 'authentication' in error_msg or 'login' in error_msg or 'password' in error_msg:
                return jsonify({
                    'success': False,
                    'error': 'Authentication failed. Invalid username or password.',
                    'message': 'Please check your credentials'
                }), 401
            else:
                return jsonify({
                    'success': False,
                    'error': str(e),
                    'message': 'Failed to connect to router. Check IP, credentials, and API port.'
                }), 401
        finally:
            if conn:
                try:
                    conn.disconnect()
                except:
                    pass
            
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400
    except Exception as e:
        logger.error(f"Test connection error: {e}", exc_info=True)
        return jsonify({'error': str(e), 'success': False}), 500

# BULK OPERATIONS
@router_bp.route('/bulk/delete', methods=['POST'])
@token_required
def bulk_delete():
    """Bulk delete multiple routers"""
    return controller.bulk_delete()


@router_bp.route('/bulk/sync', methods=['POST'])
@token_required
def bulk_sync():
    """Bulk sync multiple routers"""
    return controller.bulk_sync()


@router_bp.route('/bulk/radius/retry', methods=['POST'])
@token_required
def bulk_retry_radius():
    """Bulk retry RADIUS configuration for multiple routers"""
    return controller.bulk_retry_radius()

# COLLECTION ROUTES
@router_bp.route('', methods=['POST'])
@token_required
def create():
    """Create a new router (auto-configures RADIUS)"""
    return controller.create()


@router_bp.route('', methods=['GET'])
@token_required
def list_routers():
    """List routers with filters and pagination"""
    return controller.list()


@router_bp.route('/active', methods=['GET'])
@token_required
def get_active():
    """Get active routers for dropdowns"""
    return controller.get_active()


@router_bp.route('/pending-radius', methods=['GET'])
@token_required
def get_pending_radius():
    """Get routers pending RADIUS configuration"""
    return controller.get_pending_radius()


@router_bp.route('/network/<uuid:network_id>', methods=['GET'])
@token_required
def get_by_network(network_id):
    """Get routers by network ID"""
    return controller.get_by_network(network_id)

# SINGLE RESOURCE ROUTES
@router_bp.route('/<uuid:router_id>', methods=['GET'])
@token_required
def get(router_id):
    """Get router by ID"""
    return controller.get(router_id)


@router_bp.route('/<uuid:router_id>', methods=['PUT'])
@token_required
def update(router_id):
    """Update router"""
    return controller.update(router_id)


@router_bp.route('/<uuid:router_id>', methods=['DELETE'])
@token_required
def delete(router_id):
    """Delete or deactivate router"""
    return controller.delete(router_id)

# CONNECTION & DISCOVERY ROUTES
@router_bp.route('/<uuid:router_id>/test', methods=['POST'])
@token_required
def test(router_id):
    """Test connection to an existing router"""
    return controller.test_connection(router_id)


@router_bp.route('/<uuid:router_id>/discover', methods=['POST'])
@token_required
def discover(router_id):
    """Auto-discover router capabilities"""
    return controller.discover(router_id)


@router_bp.route('/<uuid:router_id>/sync', methods=['POST'])
@token_required
def sync(router_id):
    """Sync router configuration (hotspot, PPPoE servers)"""
    return controller.sync(router_id)


@router_bp.route('/<uuid:router_id>/health', methods=['GET'])
@token_required
def health(router_id):
    """Get router health metrics"""
    return controller.health(router_id)


@router_bp.route('/<uuid:router_id>/status', methods=['GET'])
@token_required
def status(router_id):
    """Get router connection status (includes RADIUS config status)"""
    return controller.status(router_id)

# RADIUS CONFIGURATION ROUTES
@router_bp.route('/<uuid:router_id>/radius', methods=['POST'])
@token_required
def configure_radius(router_id):
    """Manually configure RADIUS on router (legacy method)"""
    return controller.configure_radius(router_id)


@router_bp.route('/<uuid:router_id>/radius/retry', methods=['POST'])
@token_required
def retry_radius_config(router_id):
    """
    Retry RADIUS auto-configuration for a router.
    Uses the stored RADIUS secret to configure the MikroTik.
    """
    return controller.retry_radius_config(router_id)


@router_bp.route('/<uuid:router_id>/radius/secret', methods=['GET'])
@token_required
def get_radius_secret(router_id):
    """
    Get the RADIUS shared secret for a router.
    WARNING: This endpoint is audited and should be restricted.
    """
    return controller.get_radius_secret(router_id)