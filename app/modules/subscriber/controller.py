from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID
from datetime import datetime

from app.modules.subscriber.service import SubscriberService
from app.modules.subscriber.schemas import (
    SubscriberCreateSchema, SubscriberUpdateSchema, 
    PurchasePlanSchema, CheckAccessSchema, AddDeviceSchema,
    PPPoECreateSchema
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError as AppValidationError


class SubscriberController:
    """Subscriber controller for hotspot and PPPoE users"""
    
    def __init__(self):
        self.service = SubscriberService()
    
    # SUBSCRIBER CRUD
    
    @token_required
    def create_hotspot_subscriber(self):
        """Create or get hotspot subscriber (auto-created via phone)"""
        try:
            data = SubscriberCreateSchema().load(request.json)
            subscriber, created = self.service.get_or_create_hotspot_subscriber(
                organization_id=g.organization_id,
                phone=data['phone'],
                name=data.get('name')
            )
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
                'created': created,
                'message': 'Subscriber created successfully' if created else 'Subscriber already exists'
            }), 201 if created else 200
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Create hotspot subscriber error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    @permission_required('pppoe_create')
    def create_pppoe_subscriber(self):
        """Create PPPoE subscriber (admin only)"""
        try:
            data = PPPoECreateSchema().load(request.json)
            subscriber = self.service.create_pppoe_subscriber(
                organization_id=g.organization_id,
                username=data['username'],
                password=data['password'],
                plan_id=UUID(data['plan_id']),
                phone=data.get('phone'),
                first_name=data.get('first_name'),
                last_name=data.get('last_name')
            )
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
                'message': 'PPPoE subscriber created successfully'
            }), 201
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Create PPPoE subscriber error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_subscriber(self, subscriber_id):
        """Get subscriber by ID"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            subscriber = self.service.get_subscriber(subscriber_uuid, g.organization_id)
            return jsonify(subscriber.to_dict()), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get subscriber error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def update_subscriber(self, subscriber_id):
        """Update subscriber"""
        try:
            data = SubscriberUpdateSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            subscriber = self.service.update_subscriber(subscriber_uuid, g.organization_id, data)
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
                'message': 'Subscriber updated successfully'
            }), 200
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"Update subscriber error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def delete_subscriber(self, subscriber_id):
        """Delete or deactivate subscriber"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            self.service.delete_subscriber(subscriber_uuid, g.organization_id, soft_delete=soft)
            
            message = 'Subscriber deactivated successfully' if soft else 'Subscriber deleted permanently'
            return jsonify({'success': True, 'message': message}), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Delete subscriber error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # SUBSCRIBER LISTING & FILTERS
    @token_required
    def list_subscribers(self):
        """List subscribers with pagination and filters"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            filters = {
                'status': request.args.get('status'),
                'search': request.args.get('search'),
                'has_active_subscription': request.args.get('has_active_subscription', type=bool)
            }
            filters = {k: v for k, v in filters.items() if v is not None}
            
            subscriber_type = request.args.get('subscriber_type')  # hotspot, pppoe
            
            subscribers = self.service.get_organization_subscribers(
                g.organization_id, skip, per_page, filters, subscriber_type
            )
            total = self.service.repository.count_by_organization(g.organization_id, 
                                                                   subscriber_type=subscriber_type)
            
            return jsonify({
                'subscribers': [s.to_dict() for s in subscribers],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
            
        except Exception as e:
            logger.error(f"List subscribers error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def list_hotspot_users(self):
        """List hotspot users only"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            subscribers = self.service.get_hotspot_users(g.organization_id, skip, per_page)
            total = self.service.repository.count_by_organization(g.organization_id, 
                                                                   subscriber_type='hotspot')
            
            return jsonify({
                'subscribers': [s.to_dict() for s in subscribers],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
            
        except Exception as e:
            logger.error(f"List hotspot users error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def list_pppoe_users(self):
        """List PPPoE users only"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            subscribers = self.service.get_pppoe_users(g.organization_id, skip, per_page)
            total = self.service.repository.count_by_organization(g.organization_id, 
                                                                   subscriber_type='pppoe')
            
            return jsonify({
                'subscribers': [s.to_dict() for s in subscribers],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
            
        except Exception as e:
            logger.error(f"List PPPoE users error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # SUBSCRIPTION MANAGEMENT
    
    @token_required
    def purchase_plan(self, subscriber_id):
        """Purchase plan for subscriber (hotspot user)"""
        try:
            data = PurchasePlanSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            result = self.service.purchase_plan(
                organization_id=g.organization_id,
                subscriber_id=subscriber_uuid,
                plan_id=UUID(data['plan_id']),
                payment_method=data['payment_method'],
                payment_details=data.get('payment_details', {})
            )
            
            status_code = 200 if result.get('success') else 400
            return jsonify(result), status_code
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid UUID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Purchase plan error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def create_subscription(self, subscriber_id):
        """Create subscription for subscriber (admin creates for PPPoE)"""
        try:
            data = request.get_json()
            plan_id = UUID(data.get('plan_id'))
            auto_renew = data.get('auto_renew', False)
            
            subscriber_uuid = UUID(subscriber_id)
            subscription = self.service.create_subscription(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id,
                plan_id=plan_id,
                auto_renew=auto_renew
            )
            
            return jsonify({
                'success': True,
                'subscription': subscription.to_dict(include_plan=True),
                'message': 'Subscription created successfully'
            }), 201
            
        except ValueError:
            return jsonify({'error': 'Invalid UUID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Create subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_active_subscription(self, subscriber_id):
        """Get active subscription for subscriber"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            subscription = self.service.get_active_subscription(subscriber_uuid, g.organization_id)
            
            if not subscription:
                return jsonify({'active': False, 'message': 'No active subscription found'}), 200
            
            return jsonify({
                'active': True,
                'subscription': subscription.to_dict(include_plan=True)
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get active subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def renew_subscription(self, subscriber_id):
        """Renew subscription for subscriber"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            result = self.service.renew_subscription_for_subscriber(
                subscriber_uuid, g.organization_id
            )
            return jsonify(result), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Renew subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def cancel_subscription(self, subscription_id):
        """Cancel a subscription"""
        try:
            sub_uuid = UUID(subscription_id)
            reason = request.json.get('reason') if request.json else None
            result = self.service.cancel_subscription(sub_uuid, g.organization_id, reason)
            
            return jsonify({
                'success': result,
                'message': 'Subscription cancelled successfully'
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Cancel subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ACCESS CONTROL & AUTHENTICATION
    
    @token_required
    def check_access(self, subscriber_id):
        """Check subscriber access for a device"""
        try:
            data = CheckAccessSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            result = self.service.check_subscriber_access(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id,
                device_mac=data['device_mac']
            )
            return jsonify(result), 200
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Check access error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def authenticate(self):
        """Authenticate subscriber for RADIUS (public endpoint for FreeRADIUS)"""
        try:
            # This endpoint should be exempt from JWT for RADIUS
            data = request.get_json()
            username = data.get('username')
            password = data.get('password')
            organization_slug = data.get('organization_slug')
            
            if not username or not password:
                return jsonify({'success': False, 'error': 'Username and password required'}), 400
            
            # Get organization by slug
            from app.modules.organization.service import OrganizationService
            org_service = OrganizationService()
            organization = org_service.get_organization_by_slug(organization_slug)
            
            if not organization:
                return jsonify({'success': False, 'error': 'Organization not found'}), 404
            
            # Authenticate
            subscriber = self.service.authenticate_subscriber(username, password, organization.id)
            
            if not subscriber:
                return jsonify({'success': False, 'error': 'Authentication failed'}), 401
            
            # Get active subscription for bandwidth limits
            subscription = self.service.get_active_subscription(subscriber.id, organization.id)
            
            return jsonify({
                'success': True,
                'subscriber_id': str(subscriber.id),
                'subscriber_type': subscriber.subscriber_type,
                'bandwidth_up': subscription.get_bandwidth_up() if subscription else 0,
                'bandwidth_down': subscription.get_bandwidth_down() if subscription else 0,
                'session_timeout': subscription.plan.session_timeout_seconds if subscription else 86400,
                'idle_timeout': subscription.plan.idle_timeout_seconds if subscription else 300
            }), 200
            
        except Exception as e:
            logger.error(f"Authenticate error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500
    
    # STATISTICS & REPORTING
    
    @token_required
    def get_subscriber_stats(self, subscriber_id):
        """Get detailed subscriber statistics"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            stats = self.service.get_subscriber_stats(subscriber_uuid, g.organization_id)
            return jsonify(stats), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get subscriber stats error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_dashboard_stats(self):
        """Get subscriber dashboard statistics for organization"""
        try:
            stats = self.service.get_subscriber_dashboard_stats(g.organization_id)
            return jsonify(stats), 200
            
        except Exception as e:
            logger.error(f"Get dashboard stats error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_subscription_history(self, subscriber_id):
        """Get subscription history for a subscriber"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            history = self.service.repository.get_subscription_history(
                subscriber_uuid, g.organization_id, limit=50
            )
            return jsonify({
                'subscriptions': [
                    {
                        'id': str(sub.id),
                        'plan_name': sub.plan.name,
                        'plan_id': str(sub.plan_id),
                        'start_time': sub.start_time.isoformat(),
                        'expiry_time': sub.expiry_time.isoformat(),
                        'status': sub.status,
                        'bandwidth_up': sub.bandwidth_up_mbps or sub.plan.bandwidth_up_mbps,
                        'bandwidth_down': sub.bandwidth_down_mbps or sub.plan.bandwidth_down_mbps
                    } for sub in history
                ],
                'count': len(history)
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get subscription history error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # DEVICE MANAGEMENT
    
    @token_required
    def add_device(self, subscriber_id):
        """Add device to subscriber"""
        try:
            data = AddDeviceSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            result = self.service.add_device(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id,
                mac_address=data['mac_address'],
                device_name=data.get('device_name'),
                device_type=data.get('device_type')
            )
            return jsonify(result), 200
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Add device error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def remove_device(self, device_id):
        """Remove device from subscriber"""
        try:
            device_uuid = UUID(device_id)
            result = self.service.remove_device(device_uuid, g.organization_id)
            return jsonify(result), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid device ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Remove device error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_devices(self, subscriber_id):
        """Get all devices for a subscriber"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            devices = self.service.get_devices(subscriber_uuid, g.organization_id)
            return jsonify({
                'devices': devices,
                'count': len(devices)
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get devices error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500