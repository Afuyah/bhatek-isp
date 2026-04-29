from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID
from datetime import datetime, timedelta
import re

from flask import current_app
from sqlalchemy import func

from app.modules.subscriber.repository import SubscriberRepository, PlanRepository
from app.models.subscriber import Subscriber
from app.modules.session.service import SessionService
from app.modules.payment.service import PaymentService
from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError, ValidationError
from app.core.database.session import db
from app.integrations.sms.provider import SMSService
from app.integrations.radius.redius_cache import RadiusCache, RedisCache

# Alias for compatibility
RedisCache = RadiusCache


class SubscriberService:
    """Business logic for subscriber management - Production Ready"""
    
    def __init__(self):
        self.repository = SubscriberRepository()
        self.plan_repository = PlanRepository()
        self.session_service = SessionService()
        self.payment_service = PaymentService()
        self.sms_service = SMSService()
        self.radius_cache = RadiusCache()
    
    def normalize_phone(self, phone: str) -> str:
        """Normalize phone number to 254 format"""
        if not phone:
            return phone
        phone = re.sub(r'\D', '', phone)
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif phone.startswith('7') or phone.startswith('1'):
            phone = '254' + phone
        return phone
    
    def validate_phone(self, phone: str) -> bool:
        """Validate Kenyan phone number"""
        if not phone:
            raise ValidationError('Phone number is required')
        
        normalized = self.normalize_phone(phone)
        pattern = r'^254[17]\d{8}$'
        if not re.match(pattern, normalized):
            raise ValidationError('Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX')
        return True
    
    def get_or_create_subscriber(self, organization_id: UUID, phone: str, name: str = None) -> Tuple[Subscriber, bool]:
        """Get existing subscriber or create new one"""
        try:
            self.validate_phone(phone)
            normalized_phone = self.normalize_phone(phone)
            
            subscriber = self.repository.get_by_phone(normalized_phone, organization_id)
            created = False
            
            if not subscriber:
                # Parse name
                first_name = None
                last_name = None
                if name:
                    parts = name.strip().split()
                    first_name = parts[0]
                    last_name = ' '.join(parts[1:]) if len(parts) > 1 else None
                
                data = {
                    'organization_id': organization_id,
                    'phone': normalized_phone,
                    'first_name': first_name,
                    'last_name': last_name,
                    'status': 'active',
                    'total_spent': 0
                }
                subscriber = self.repository.create(data)
                created = True
                logger.info(f"Created new subscriber: {normalized_phone} for org {organization_id}")
                
                # Cache subscriber data
                self.radius_cache.cache_subscriber(str(subscriber.id), {
                    'phone': normalized_phone,
                    'name': name,
                    'organization_id': str(organization_id)
                }, ttl=86400)
            
            return subscriber, created
            
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error in get_or_create_subscriber: {e}", exc_info=True)
            raise BusinessError(f"Failed to get or create subscriber: {str(e)}")
    
    def check_subscriber_access(self, subscriber_id: UUID, organization_id: UUID, device_mac: str) -> Dict[str, Any]:
        """Check if subscriber can access internet"""
        try:
            # Check cache first for performance
            cache_key = f"access_check:{subscriber_id}:{device_mac}"
            cached_result = self.radius_cache.get_auth_data(cache_key)
            if cached_result:
                return cached_result
            
            subscriber = self.repository.get_by_id(subscriber_id, organization_id)
            if not subscriber:
                raise NotFoundError('Subscriber not found')
            
            if subscriber.status != 'active':
                return {
                    'allowed': False,
                    'reason': 'subscriber_inactive',
                    'message': 'Your account is inactive. Please contact support.'
                }
            
            # Get active subscription
            subscription = self.repository.get_active_subscription(subscriber_id, organization_id)
            if not subscription:
                return {
                    'allowed': False,
                    'reason': 'no_active_subscription',
                    'message': 'No active subscription. Please purchase a plan.'
                }
            
            # Check expiry
            if subscription.expiry_time < datetime.utcnow():
                return {
                    'allowed': False,
                    'reason': 'subscription_expired',
                    'message': 'Your subscription has expired. Please renew.'
                }
            
            # Get active sessions and devices
            active_sessions = self.session_service.get_active_sessions_by_subscriber(subscriber_id, organization_id)
            devices = self.repository.get_devices(subscriber_id, organization_id)
            
            # Find device
            device = next((d for d in devices if d.mac_address == device_mac), None)
            device_limit = subscription.device_limit or subscription.plan.device_limit
            
            # Count unique active devices
            active_device_macs = {s.device_mac for s in active_sessions if s.device_mac}
            active_device_count = len(active_device_macs)
            
            # Check if device already has an active session
            device_session = next((s for s in active_sessions if s.device_mac == device_mac), None)
            
            if device_session:
                result = {
                    'allowed': True,
                    'already_connected': True,
                    'session_id': str(device_session.id),
                    'message': 'Device already connected'
                }
            elif device:
                # Existing device but no active session
                if active_device_count >= device_limit:
                    # Check if we should disconnect oldest session
                    disconnect_oldest = current_app.config.get('DEVICE_LIMIT_BEHAVIOR', 'reject') == 'disconnect_oldest'
                    
                    if disconnect_oldest and active_sessions:
                        oldest_session = min(active_sessions, key=lambda s: s.start_time)
                        self.session_service.terminate_session(
                            oldest_session.id, 
                            organization_id, 
                            'device_limit_reached - oldest disconnected'
                        )
                        result = {
                            'allowed': True,
                            'replaced_session': True,
                            'message': 'Oldest session disconnected to allow new connection'
                        }
                    else:
                        result = {
                            'allowed': False,
                            'reason': 'device_limit_reached',
                            'message': f'Device limit reached ({device_limit} devices). Please disconnect another device first.'
                        }
                else:
                    result = {
                        'allowed': True,
                        'subscription': {
                            'id': str(subscription.id),
                            'plan_name': subscription.plan.name,
                            'expiry': subscription.expiry_time.isoformat(),
                            'bandwidth_up': subscription.bandwidth_up_mbps or subscription.plan.bandwidth_up_mbps,
                            'bandwidth_down': subscription.bandwidth_down_mbps or subscription.plan.bandwidth_down_mbps,
                            'device_limit': device_limit
                        }
                    }
            else:
                # New device
                if active_device_count >= device_limit:
                    result = {
                        'allowed': False,
                        'reason': 'device_limit_reached',
                        'message': f'Device limit reached ({device_limit} devices). Please remove a device first.'
                    }
                else:
                    result = {
                        'allowed': True,
                        'subscription': {
                            'id': str(subscription.id),
                            'plan_name': subscription.plan.name,
                            'expiry': subscription.expiry_time.isoformat(),
                            'bandwidth_up': subscription.bandwidth_up_mbps or subscription.plan.bandwidth_up_mbps,
                            'bandwidth_down': subscription.bandwidth_down_mbps or subscription.plan.bandwidth_down_mbps,
                            'device_limit': device_limit
                        }
                    }
            
            # Cache result for 5 seconds to prevent repeated checks
            if result.get('allowed'):
                self.radius_cache.set_auth_data(cache_key, result, ttl=5)
            
            return result
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error checking subscriber access: {e}", exc_info=True)
            raise BusinessError(f"Failed to check access: {str(e)}")
    
    def purchase_plan(self, organization_id: UUID, subscriber_id: UUID, plan_id: UUID, 
                      payment_method: str, payment_details: Dict[str, Any]) -> Dict[str, Any]:
        """Purchase a plan for subscriber"""
        try:
            # Get plan
            plan = self.plan_repository.get_by_id(plan_id, organization_id)
            if not plan:
                raise NotFoundError('Plan not found')
            
            if not plan.is_active:
                raise BusinessError('Plan is not active')
            
            # Get subscriber
            subscriber = self.repository.get_by_id(subscriber_id, organization_id)
            if not subscriber:
                raise NotFoundError('Subscriber not found')
            
            # Process payment
            payment_result = self.payment_service.process_payment(
                organization_id=organization_id,
                subscriber_id=subscriber_id,
                amount=float(plan.price),
                payment_method=payment_method,
                payment_details=payment_details,
                metadata={'plan_id': str(plan_id), 'plan_name': plan.name}
            )
            
            if payment_result.get('status') != 'success':
                logger.warning(f"Payment failed for subscriber {subscriber_id}: {payment_result}")
                return {
                    'success': False,
                    'message': payment_result.get('message', 'Payment failed'),
                    'payment_result': payment_result
                }
            
            # Deactivate old subscriptions
            old_subscriptions = self.repository.get_active_subscriptions(subscriber_id, organization_id)
            for old_sub in old_subscriptions:
                old_sub.status = 'expired'
            
            # Create new subscription
            subscription_data = {
                'organization_id': organization_id,
                'subscriber_id': subscriber_id,
                'plan_id': plan_id,
                'status': 'active',
                'start_time': datetime.utcnow(),
                'expiry_time': datetime.utcnow() + timedelta(days=plan.validity_days),
                'auto_renew': plan.auto_renew,
                'device_limit': plan.device_limit,
                'bandwidth_up_mbps': plan.bandwidth_up_mbps,
                'bandwidth_down_mbps': plan.bandwidth_down_mbps
            }
            
            subscription = Subscription(**subscription_data)
            db.session.add(subscription)
            
            # Update subscriber total spent
            subscriber.total_spent = (subscriber.total_spent or 0) + float(plan.price)
            db.session.commit()
            
            logger.info(f"Created subscription {subscription.id} for subscriber {subscriber_id}")
            
            # Send confirmation SMS
            try:
                from app.modules.organization.service import OrganizationService
                org_service = OrganizationService()
                sms_config = org_service.get_sms_config(organization_id)
                
                if sms_config:
                    expiry_date = subscription.expiry_time.strftime('%Y-%m-%d')
                    message = f"Payment of {plan.price} KES confirmed. Your {plan.name} plan is active until {expiry_date}. Enjoy!"
                    self.sms_service.send_sms(organization_id, subscriber.phone, message, sms_config)
            except Exception as e:
                logger.warning(f"Failed to send SMS confirmation: {e}")
            
            # Update RADIUS cache
            self.radius_cache.set_auth_data(
                username=subscriber.phone,
                data={
                    'password': str(subscription.id),
                    'organization_id': str(organization_id),
                    'subscriber_id': str(subscriber_id),
                    'plan_name': plan.name,
                    'bandwidth_up': subscription.bandwidth_up_mbps or plan.bandwidth_up_mbps,
                    'bandwidth_down': subscription.bandwidth_down_mbps or plan.bandwidth_down_mbps,
                    'expiry': subscription.expiry_time.timestamp(),
                    'status': 'active',
                    'device_limit': subscription.device_limit or plan.device_limit,
                    'session_timeout': plan.session_timeout_seconds or 86400,
                    'idle_timeout': plan.idle_timeout_seconds or 300
                },
                ttl=3600
            )
            
            return {
                'success': True,
                'subscription': {
                    'id': str(subscription.id),
                    'plan_name': plan.name,
                    'plan_id': str(plan_id),
                    'start_time': subscription.start_time.isoformat(),
                    'expiry_time': subscription.expiry_time.isoformat(),
                    'status': subscription.status,
                    'device_limit': subscription.device_limit or plan.device_limit,
                    'bandwidth_up': subscription.bandwidth_up_mbps or plan.bandwidth_up_mbps,
                    'bandwidth_down': subscription.bandwidth_down_mbps or plan.bandwidth_down_mbps
                },
                'payment': payment_result,
                'message': 'Plan purchased successfully'
            }
            
        except (NotFoundError, BusinessError, ValidationError):
            raise
        except Exception as e:
            logger.error(f"Error purchasing plan: {e}", exc_info=True)
            raise BusinessError(f"Failed to purchase plan: {str(e)}")
    
    def get_subscriber_stats(self, subscriber_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Get statistics for a subscriber"""
        try:
            subscriber = self.repository.get_by_id(subscriber_id, organization_id)
            if not subscriber:
                raise NotFoundError('Subscriber not found')
            
            # Get active sessions
            active_sessions = self.session_service.get_active_sessions_by_subscriber(subscriber_id, organization_id)
            
            # Get devices
            devices = self.repository.get_devices(subscriber_id, organization_id)
            
            # Get active subscription
            active_subscription = self.repository.get_active_subscription(subscriber_id, organization_id)
            
            # Get recent sessions from RADIUS accounting
            from app.modules.session.repository import RadiusAccountingRepository
            accounting_repo = RadiusAccountingRepository()
            recent_accounting = accounting_repo.get_user_accounting(
                subscriber.phone, 
                organization_id, 
                start_date=datetime.utcnow() - timedelta(days=30),
                limit=10
            )
            
            # Calculate total usage
            total_bytes = sum(
                (r.acct_input_octets or 0) + (r.acct_output_octets or 0) 
                for r in recent_accounting
            )
            
            # Get subscription history
            subscription_history = self.repository.get_subscription_history(subscriber_id, organization_id, limit=5)
            
            return {
                'subscriber': {
                    'id': str(subscriber.id),
                    'phone': subscriber.phone,
                    'first_name': subscriber.first_name,
                    'last_name': subscriber.last_name,
                    'email': subscriber.email,
                    'status': subscriber.status,
                    'total_spent': float(subscriber.total_spent or 0),
                    'created_at': subscriber.created_at.isoformat() if subscriber.created_at else None
                },
                'active_subscription': {
                    'id': str(active_subscription.id),
                    'plan_name': active_subscription.plan.name,
                    'plan_id': str(active_subscription.plan_id),
                    'start_time': active_subscription.start_time.isoformat(),
                    'expiry_time': active_subscription.expiry_time.isoformat(),
                    'bandwidth_up': active_subscription.bandwidth_up_mbps or active_subscription.plan.bandwidth_up_mbps,
                    'bandwidth_down': active_subscription.bandwidth_down_mbps or active_subscription.plan.bandwidth_down_mbps,
                    'device_limit': active_subscription.device_limit or active_subscription.plan.device_limit
                } if active_subscription else None,
                'active_sessions_count': len(active_sessions),
                'active_sessions': [
                    {
                        'id': str(s.id),
                        'session_type': s.session_type,
                        'device_mac': s.device_mac,
                        'ip_address': str(s.ip_address) if s.ip_address else None,
                        'start_time': s.start_time.isoformat(),
                        'expiry_time': s.expiry_time.isoformat(),
                        'bytes_in': s.bytes_in,
                        'bytes_out': s.bytes_out
                    } for s in active_sessions[:10]
                ],
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
                'recent_usage': [
                    {
                        'start_time': r.acct_start_time.isoformat() if r.acct_start_time else None,
                        'stop_time': r.acct_stop_time.isoformat() if r.acct_stop_time else None,
                        'bytes_in': r.acct_input_octets or 0,
                        'bytes_out': r.acct_output_octets or 0,
                        'session_time': r.acct_session_time or 0
                    } for r in recent_accounting
                ],
                'subscription_history': [
                    {
                        'id': str(sub.id),
                        'plan_name': sub.plan.name,
                        'start_time': sub.start_time.isoformat(),
                        'expiry_time': sub.expiry_time.isoformat(),
                        'status': sub.status
                    } for sub in subscription_history
                ],
                'total_usage_gb': round(total_bytes / (1024**3), 2),
                'total_usage_mb': round(total_bytes / (1024**2), 2)
            }
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error getting subscriber stats: {e}", exc_info=True)
            raise BusinessError(f"Failed to get subscriber statistics: {str(e)}")
    
    def renew_subscription(self, subscription_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Renew an existing subscription"""
        try:
            subscription = self.repository.get_subscription_by_id(subscription_id, organization_id)
            if not subscription:
                raise NotFoundError('Subscription not found')
            
            plan = subscription.plan
            subscriber = self.repository.get_by_id(subscription.subscriber_id, organization_id)
            
            if not subscriber:
                raise NotFoundError('Subscriber not found')
            
            # Calculate new expiry
            current_time = datetime.utcnow()
            base_time = max(subscription.expiry_time, current_time)
            new_expiry = base_time + timedelta(days=plan.validity_days)
            
            # Update subscription
            subscription.expiry_time = new_expiry
            subscription.status = 'active'
            subscription.start_time = current_time if subscription.expiry_time <= current_time else subscription.start_time
            
            db.session.commit()
            
            logger.info(f"Renewed subscription {subscription_id} until {new_expiry}")
            
            # Update cache
            self.radius_cache.set_auth_data(
                username=subscriber.phone,
                data={
                    'expiry': new_expiry.timestamp(),
                    'status': 'active'
                },
                ttl=3600
            )
            
            # Send notification
            try:
                from app.modules.organization.service import OrganizationService
                org_service = OrganizationService()
                sms_config = org_service.get_sms_config(organization_id)
                
                if sms_config:
                    message = f"Your {plan.name} subscription has been renewed until {new_expiry.strftime('%Y-%m-%d')}. Thank you!"
                    self.sms_service.send_sms(organization_id, subscriber.phone, message, sms_config)
            except Exception as e:
                logger.warning(f"Failed to send renewal SMS: {e}")
            
            return {
                'success': True,
                'subscription_id': str(subscription_id),
                'plan_name': plan.name,
                'old_expiry': base_time.isoformat(),
                'new_expiry': new_expiry.isoformat(),
                'message': 'Subscription renewed successfully'
            }
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error renewing subscription: {e}", exc_info=True)
            raise BusinessError(f"Failed to renew subscription: {str(e)}")
    
    def add_device(self, subscriber_id: UUID, organization_id: UUID, 
                   mac_address: str, device_name: str = None, 
                   device_type: str = None) -> Dict[str, Any]:
        """Add a device to subscriber"""
        try:
            subscriber = self.repository.get_by_id(subscriber_id, organization_id)
            if not subscriber:
                raise NotFoundError('Subscriber not found')
            
            # Check if device already exists
            existing_device = self.repository.get_device_by_mac(mac_address, organization_id)
            if existing_device:
                raise BusinessError(f'Device with MAC {mac_address} already exists')
            
            # Check device limit
            devices = self.repository.get_devices(subscriber_id, organization_id)
            active_subscription = self.repository.get_active_subscription(subscriber_id, organization_id)
            max_devices = active_subscription.device_limit or active_subscription.plan.device_limit if active_subscription else 5
            
            if len(devices) >= max_devices:
                raise BusinessError(f'Device limit reached ({max_devices} devices)')
            
            # Add device
            device_data = {
                'organization_id': organization_id,
                'subscriber_id': subscriber_id,
                'mac_address': mac_address,
                'device_name': device_name,
                'device_type': device_type,
                'is_primary': len(devices) == 0,  # First device is primary
                'is_active': True,
                'last_seen_at': datetime.utcnow()
            }
            
            device = self.repository.add_device(device_data)
            
            logger.info(f"Added device {mac_address} to subscriber {subscriber_id}")
            
            return {
                'success': True,
                'device': {
                    'id': str(device.id),
                    'mac_address': device.mac_address,
                    'device_name': device.device_name,
                    'device_type': device.device_type,
                    'is_primary': device.is_primary
                },
                'message': 'Device added successfully'
            }
            
        except (NotFoundError, BusinessError):
            raise
        except Exception as e:
            logger.error(f"Error adding device: {e}", exc_info=True)
            raise BusinessError(f"Failed to add device: {str(e)}")
    
    def remove_device(self, device_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Remove a device from subscriber"""
        try:
            device = self.repository.get_device_by_id(device_id, organization_id)
            if not device:
                raise NotFoundError('Device not found')
            
            # Check if device has active sessions
            active_sessions = self.session_service.get_active_sessions_by_device(
                device.mac_address, organization_id
            )
            
            if active_sessions:
                # Terminate active sessions first
                for session in active_sessions:
                    self.session_service.terminate_session(
                        session.id, organization_id, 'device_removed'
                    )
            
            # Remove device
            self.repository.remove_device(device_id, organization_id)
            
            logger.info(f"Removed device {device.mac_address} from subscriber {device.subscriber_id}")
            
            return {
                'success': True,
                'message': 'Device removed successfully'
            }
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error removing device: {e}", exc_info=True)
            raise BusinessError(f"Failed to remove device: {str(e)}")