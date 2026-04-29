from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.subscriber.service import SubscriberService
from app.modules.subscriber.schemas import (
    SubscriberCreateSchema, SubscriberUpdateSchema, 
    PurchasePlanSchema, CheckAccessSchema, AddDeviceSchema
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger

class SubscriberController:
  
    def __init__(self):
        self.service = SubscriberService()
    
    @token_required
    def create(self):
        """Create subscriber"""
        try:
            data = SubscriberCreateSchema().load(request.json)
            subscriber, created = self.service.get_or_create_subscriber(
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
        except Exception as e:
            logger.error(f"Create subscriber error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get(self, subscriber_id):
        """Get subscriber by ID"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            subscriber = self.service.repository.get_by_id(subscriber_uuid, g.organization_id)
            if not subscriber:
                return jsonify({'error': 'Subscriber not found'}), 404
            return jsonify(subscriber.to_dict()), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get subscriber error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def update(self, subscriber_id):
        """Update subscriber"""
        try:
            data = SubscriberUpdateSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            subscriber = self.service.repository.update(subscriber_uuid, g.organization_id, data)
            if not subscriber:
                return jsonify({'error': 'Subscriber not found'}), 404
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
                'message': 'Subscriber updated successfully'
            }), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Update subscriber error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def list(self):
        """List subscribers with pagination and filters"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            filters = {
                'status': request.args.get('status'),
                'search': request.args.get('search')
            }
            
            subscribers = self.service.repository.get_by_organization(
                g.organization_id, skip, per_page, filters
            )
            total = self.service.repository.count_by_organization(g.organization_id)
            
            return jsonify({
                'subscribers': [s.to_dict() for s in subscribers],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page
            }), 200
        except Exception as e:
            logger.error(f"List subscribers error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
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
        except Exception as e:
            logger.error(f"Check access error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def purchase_plan(self, subscriber_id):
        """Purchase plan for subscriber"""
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
        except Exception as e:
            logger.error(f"Purchase plan error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_stats(self, subscriber_id):
        """Get subscriber statistics"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            stats = self.service.get_subscriber_stats(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id
            )
            return jsonify(stats), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get stats error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def renew_subscription(self, subscriber_id):
        """Renew subscription for subscriber"""
        try:
            # Get active subscription
            active_subscription = self.service.repository.get_active_subscription(
                UUID(subscriber_id), g.organization_id
            )
            
            if not active_subscription:
                return jsonify({'error': 'No active subscription found'}), 404
            
            result = self.service.renew_subscription(
                subscription_id=active_subscription.id,
                organization_id=g.organization_id
            )
            return jsonify(result), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Renew subscription error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
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
        except Exception as e:
            logger.error(f"Add device error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def remove_device(self, device_id):
        """Remove device from subscriber"""
        try:
            device_uuid = UUID(device_id)
            result = self.service.remove_device(
                device_id=device_uuid,
                organization_id=g.organization_id
            )
            return jsonify(result), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid device ID format'}), 400
        except Exception as e:
            logger.error(f"Remove device error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_devices(self, subscriber_id):
        """Get all devices for a subscriber"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            devices = self.service.repository.get_devices(subscriber_uuid, g.organization_id)
            return jsonify({
                'devices': [
                    {
                        'id': str(d.id),
                        'mac_address': d.mac_address,
                        'device_name': d.device_name,
                        'device_type': d.device_type,
                        'is_primary': d.is_primary,
                        'is_active': d.is_active,
                        'last_seen': d.last_seen_at.isoformat() if d.last_seen_at else None
                    } for d in devices
                ],
                'count': len(devices)
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get devices error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
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
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_usage(self, subscriber_id):
        """Get usage statistics for a subscriber"""
        try:
            subscriber_uuid = UUID(subscriber_id)
            days = request.args.get('days', 30, type=int)
            
            usage = self.service.get_user_usage(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id,
                days=days
            )
            return jsonify(usage), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get usage error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500