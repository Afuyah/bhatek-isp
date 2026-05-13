"""
RADIUS Authentication Handler - Processes RADIUS Access-Request packets from MikroTik routers
This is a Flask route that FreeRADIUS can proxy to or that runs as a standalone service
"""
from flask import request, jsonify, Blueprint, current_app
from datetime import datetime
import hashlib
import hmac

from app.core.logging.logger import logger
from app.modules.subscriber.service import SubscriberService
from app.integrations.radius.radius_cache import RadiusCache
from app.integrations.radius.dictionary import MikroTikDictionary

# Create blueprint for RADIUS auth endpoints
radius_auth_bp = Blueprint('radius_auth', __name__, url_prefix='/api/radius')


class RadiusAuthHandler:
    """
    Handles RADIUS authentication requests from MikroTik routers
    """
    
    def __init__(self):
        self.subscriber_service = SubscriberService()
        self.cache = RadiusCache
    
    def authenticate(self, username: str, password: str, nas_ip: str = None,
                     calling_station_id: str = None, called_station_id: str = None,
                     organization_id: str = None) -> dict:
        """
        Authenticate a user against the subscriber database
        
        Returns: RADIUS response attributes
        """
        try:
            # Try to get from cache first
            cached_auth = self.cache.get_auth_data(username)
            if cached_auth:
                logger.debug(f"Cache hit for {username}")
                return self._build_accept_response(cached_auth)
            
            # Find organization (if not provided, try to resolve from NAS IP or domain)
            if not organization_id:
                organization_id = self._resolve_organization(nas_ip, called_station_id)
            
            if not organization_id:
                logger.warning(f"Cannot resolve organization for {username}")
                return self._build_reject_response("Organization not found")
            
            # Authenticate subscriber
            subscriber = self.subscriber_service.authenticate_subscriber(
                credential=username,
                password=password,
                organization_id=organization_id
            )
            
            if not subscriber:
                logger.warning(f"Authentication failed for {username}")
                return self._build_reject_response("Invalid credentials")
            
            # Get active subscription
            subscription = self.subscriber_service.get_active_subscription(
                subscriber.id, organization_id
            )
            
            if not subscription:
                logger.warning(f"No active subscription for {username}")
                return self._build_reject_response("No active subscription")
            
            # Check expiry
            if subscription.expiry_time <= datetime.utcnow():
                logger.warning(f"Subscription expired for {username}")
                return self._build_reject_response("Subscription expired")
            
            # Build response attributes
            response = self._build_accept_response_from_subscription(subscriber, subscription)
            
            # Cache the result
            self.cache.set_auth_data(username, response, ttl=300)
            
            logger.info(f"Authentication successful for {username}")
            return response
            
        except Exception as e:
            logger.error(f"Authentication error for {username}: {e}", exc_info=True)
            return self._build_reject_response("Internal error")
    
    def _resolve_organization(self, nas_ip: str, called_station_id: str) -> str:
        """Resolve organization ID from NAS IP or hotspot domain"""
        # Try cache first
        if nas_ip:
            cached_org = self.cache.get_nas(nas_ip)
            if cached_org:
                return cached_org.get('organization_id')
        
        # Query database for router by IP
        from app.models.router import Router
        router = Router.query.filter_by(ip_address=nas_ip).first()
        if router:
            org_id = str(router.organization_id)
            self.cache.cache_nas(nas_ip, {'organization_id': org_id}, ttl=3600)
            return org_id
        
        # Try to resolve from called_station_id (hotspot domain)
        if called_station_id:
            from app.models.organization import Organization
            org = Organization.query.filter_by(slug=called_station_id).first()
            if org:
                return str(org.id)
        
        return None
    
    def _build_accept_response(self, auth_data: dict) -> dict:
        """Build RADIUS Access-Accept response from cached data"""
        attributes = {
            'Session-Timeout': auth_data.get('session_timeout', 86400),
            'Idle-Timeout': auth_data.get('idle_timeout', 300),
        }
        
        # Add rate limit if present
        if auth_data.get('bandwidth_up') or auth_data.get('bandwidth_down'):
            rate_limit = MikroTikDictionary.format_rate_limit(
                upload=auth_data.get('bandwidth_up', 0),
                download=auth_data.get('bandwidth_down', 0),
                unit="M"
            )
            attributes['Mikrotik-Rate-Limit'] = rate_limit
        
        return {
            'success': True,
            'attributes': attributes
        }
    
    def _build_accept_response_from_subscription(self, subscriber, subscription) -> dict:
        """Build RADIUS Access-Accept response from subscription data"""
        plan = subscription.plan
        
        attributes = {
            'Session-Timeout': plan.session_timeout_seconds or 86400,
            'Idle-Timeout': plan.idle_timeout_seconds or 300,
        }
        
        # Bandwidth limits
        bandwidth_up = subscription.bandwidth_up_mbps or plan.bandwidth_up_mbps or 0
        bandwidth_down = subscription.bandwidth_down_mbps or plan.bandwidth_down_mbps or 0
        
        if bandwidth_up > 0 or bandwidth_down > 0:
            rate_limit = MikroTikDictionary.format_rate_limit(
                upload=bandwidth_up if bandwidth_up > 0 else bandwidth_down,
                download=bandwidth_down if bandwidth_down > 0 else bandwidth_up,
                unit="M"
            )
            attributes['Mikrotik-Rate-Limit'] = rate_limit
        
        # Data cap
        if plan.validity_type == 'data_based' and plan.data_limit_mb:
            total_limit_bytes = int(plan.data_limit_mb) * 1024 * 1024
            attributes['Mikrotik-Total-Limit'] = total_limit_bytes
        
        return {
            'success': True,
            'attributes': attributes,
            'subscriber_id': str(subscriber.id),
            'subscription_id': str(subscription.id)
        }
    
    def _build_reject_response(self, reason: str = None) -> dict:
        """Build RADIUS Access-Reject response"""
        return {
            'success': False,
            'error': reason or 'Access denied'
        }


# Flask route for RADIUS authentication (for use with radrest module)
@radius_auth_bp.route('/authenticate', methods=['POST'])
def radius_authenticate():
    """
    RADIUS authentication endpoint for FreeRADIUS rest module
    FreeRADIUS will POST to this endpoint with authentication request
    """
    try:
        data = request.get_json() or request.form
        
        username = data.get('username')
        password = data.get('password')
        nas_ip = data.get('nas_ip_address')
        called_station_id = data.get('called_station_id')
        calling_station_id = data.get('calling_station_id')
        organization_slug = data.get('organization_slug')
        
        if not username or not password:
            return jsonify({'result': 'reject', 'reason': 'Missing credentials'}), 401
        
        handler = RadiusAuthHandler()
        
        # Try to get organization from slug
        organization_id = None
        if organization_slug:
            from app.models.organization import Organization
            org = Organization.query.filter_by(slug=organization_slug).first()
            if org:
                organization_id = str(org.id)
        
        result = handler.authenticate(
            username=username,
            password=password,
            nas_ip=nas_ip,
            called_station_id=called_station_id,
            calling_station_id=calling_station_id,
            organization_id=organization_id
        )
        
        if result.get('success'):
            return jsonify({
                'result': 'accept',
                'reply_attributes': result.get('attributes', {})
            }), 200
        else:
            return jsonify({
                'result': 'reject',
                'reason': result.get('error', 'Access denied')
            }), 401
            
    except Exception as e:
        logger.error(f"RADIUS auth endpoint error: {e}", exc_info=True)
        return jsonify({'result': 'reject', 'reason': 'Internal error'}), 500


@radius_auth_bp.route('/disconnect', methods=['POST'])
def radius_disconnect():
    """
    RADIUS Disconnect (CoA) endpoint
    """
    try:
        data = request.get_json() or request.form
        username = data.get('username')
        
        if not username:
            return jsonify({'result': 'fail', 'reason': 'Missing username'}), 400
        
        # Clear cached auth data
        RadiusCache.delete_auth_data(username)
        
        logger.info(f"Disconnect request for {username}")
        return jsonify({'result': 'ack'}), 200
        
    except Exception as e:
        logger.error(f"Disconnect error: {e}", exc_info=True)
        return jsonify({'result': 'fail', 'reason': str(e)}), 500