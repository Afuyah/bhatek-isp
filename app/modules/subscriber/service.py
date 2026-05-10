from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID
from datetime import datetime, timedelta
import re
import secrets

from flask import current_app
from sqlalchemy import func
 
from app.modules.subscriber.repository import SubscriberRepository, PlanRepository
from app.models.subscriber import Subscriber
from app.models.billing import Subscription
from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError, ValidationError
from app.core.database.session import db


class SubscriberService:
    """Business logic for subscriber management """
    
    def __init__(self):
        self.repository = SubscriberRepository()
        self.plan_repository = PlanRepository()
        self.encryption = EncryptionService()
        
        # Lazy imports for optional dependencies
        self._session_service = None
        self._payment_service = None
        self._sms_service = None
        self._radius_cache = None
        self._radius_sync_service = None
    
    @property
    def session_service(self):
        """Lazy load session service"""
        if self._session_service is None:
            from app.modules.session.service import SessionService
            self._session_service = SessionService()
        return self._session_service
    
    @property
    def payment_service(self):
        """Lazy load payment service"""
        if self._payment_service is None:
            from app.modules.payment.service import PaymentService
            self._payment_service = PaymentService()
        return self._payment_service
    
    @property
    def sms_service(self):
        """Lazy load SMS service"""
        if self._sms_service is None:
            from app.integrations.sms.provider import SMSService
            self._sms_service = SMSService()
        return self._sms_service
    
    @property
    def radius_cache(self):
        """Lazy load RADIUS cache"""
        if self._radius_cache is None:
            from app.integrations.radius.radius_cache import RadiusCache
            self._radius_cache = RadiusCache()
        return self._radius_cache
    
    @property
    def radius_sync_service(self):
        """Lazy load RADIUS sync service"""
        if self._radius_sync_service is None:
            from app.modules.radius.service import RadiusSyncService
            self._radius_sync_service = RadiusSyncService()
        return self._radius_sync_service
    
# PHONE NUMBER UTILITIES

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
    
# SUBSCRIBER CRUD OPERATIONS

    def get_subscriber(self, subscriber_id: UUID, organization_id: UUID) -> Subscriber:
        """Get subscriber by ID"""
        subscriber = self.repository.get_by_id(subscriber_id, organization_id)
        if not subscriber:
            raise NotFoundError('Subscriber not found')
        return subscriber
    
    def get_subscriber_by_phone(self, phone: str, organization_id: UUID) -> Optional[Subscriber]:
        """Get subscriber by phone number"""
        return self.repository.get_by_phone(self.normalize_phone(phone), organization_id)
    
    def get_subscriber_by_username(self, username: str, organization_id: UUID) -> Optional[Subscriber]:
        """Get subscriber by username (for PPPoE)"""
        return self.repository.get_by_username(username, organization_id)
    
    def get_or_create_hotspot_subscriber(self, organization_id: UUID, phone: str, 
                                          name: str = None) -> Tuple[Subscriber, bool]:
        """Get existing hotspot subscriber or create new one (for M-Pesa flow)"""
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
                    'subscriber_type': 'hotspot',
                    'first_name': first_name,
                    'last_name': last_name,
                    'status': 'active',
                    'total_spent': 0
                }
                subscriber = self.repository.create(data)
                created = True
                logger.info(f"Created new hotspot subscriber: {normalized_phone} for org {organization_id}")
            
            return subscriber, created
            
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error in get_or_create_hotspot_subscriber: {e}", exc_info=True)
            raise BusinessError(f"Failed to get or create subscriber: {str(e)}")
    
    def create_pppoe_subscriber(self, organization_id: UUID, username: str, password: str,
                                 plan_id: UUID, phone: str = None, 
                                 first_name: str = None, last_name: str = None) -> Subscriber:
        """Create a new PPPoE subscriber (admin created)"""
        try:
            # Validate username uniqueness
            existing = self.repository.get_by_username(username, organization_id)
            if existing:
                raise ValidationError(f'Username "{username}" already exists')
            
            # Normalize phone if provided
            if phone:
                phone = self.normalize_phone(phone)
            
            # Encrypt password
            encrypted_password = self.encryption.encrypt(password)
            
            data = {
                'organization_id': organization_id,
                'subscriber_type': 'pppoe',
                'username': username,
                'password_encrypted': encrypted_password,
                'phone': phone,
                'first_name': first_name,
                'last_name': last_name,
                'status': 'active',
                'total_spent': 0
            }
            
            subscriber = self.repository.create(data)
            logger.info(f"Created new PPPoE subscriber: {username} for org {organization_id}")
            
            # Create subscription for the PPPoE user
            if plan_id:
                self.create_subscription(subscriber.id, organization_id, plan_id, auto_renew=False)
            
            # Sync to RADIUS
            self.radius_sync_service.sync_pppoe_user_to_radius(subscriber, password)
            
            return subscriber
            
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error creating PPPoE subscriber: {e}", exc_info=True)
            raise BusinessError(f"Failed to create PPPoE subscriber: {str(e)}")
    
    def update_subscriber(self, subscriber_id: UUID, organization_id: UUID, 
                           data: Dict[str, Any]) -> Subscriber:
        """Update subscriber information"""
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            
            # If phone is being updated, validate uniqueness
            if 'phone' in data and data['phone']:
                phone = self.normalize_phone(data['phone'])
                existing = self.repository.get_by_phone(phone, organization_id)
                if existing and existing.id != subscriber_id:
                    raise ValidationError('Phone number already in use')
                data['phone'] = phone
            
            # If username is being updated (PPPoE), validate uniqueness
            if 'username' in data and data['username']:
                existing = self.repository.get_by_username(data['username'], organization_id)
                if existing and existing.id != subscriber_id:
                    raise ValidationError('Username already in use')
            
            # If password is being updated (PPPoE), encrypt it
            if 'password' in data and data['password']:
                data['password_encrypted'] = self.encryption.encrypt(data.pop('password'))
            
            updated_subscriber = self.repository.update(subscriber_id, organization_id, data)
            
            # If password changed, sync to RADIUS
            if 'password_encrypted' in data and subscriber.subscriber_type == 'pppoe':
                self.radius_sync_service.sync_pppoe_user_to_radius(
                    updated_subscriber, 
                    self.encryption.decrypt(data['password_encrypted'])
                )
            
            logger.info(f"Updated subscriber: {subscriber_id}")
            return updated_subscriber
            
        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            logger.error(f"Error updating subscriber: {e}", exc_info=True)
            raise BusinessError(f"Failed to update subscriber: {str(e)}")
    
    def delete_subscriber(self, subscriber_id: UUID, organization_id: UUID, 
                          soft_delete: bool = True) -> bool:
        """Delete or deactivate subscriber"""
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            
            # Terminate all active sessions
            active_sessions = self.session_service.get_active_sessions_by_subscriber(
                subscriber_id, organization_id
            )
            for session in active_sessions:
                self.session_service.terminate_session(
                    session.id, organization_id, 'subscriber_deleted'
                )
            
            # Remove from RADIUS
            self.radius_sync_service.remove_subscriber_from_radius(subscriber)
            
            result = self.repository.delete(subscriber_id, organization_id, soft_delete)
            logger.info(f"Deleted subscriber: {subscriber_id}")
            return result
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error deleting subscriber: {e}", exc_info=True)
            raise BusinessError(f"Failed to delete subscriber: {str(e)}")
    
# SUBSCRIPTION MANAGEMENT

    def create_subscription(self, subscriber_id: UUID, organization_id: UUID,
                            plan_id: UUID, auto_renew: bool = False) -> Subscription:
        """Create a new subscription for a subscriber"""
        try:
            plan = self.plan_repository.get_by_id(plan_id, organization_id)
            if not plan:
                raise NotFoundError('Plan not found')
            
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            
            # Deactivate old subscriptions
            old_sub = self.repository.get_active_subscription(subscriber_id, organization_id)
            if old_sub:
                old_sub.status = 'expired'
                old_sub.cancellation_reason = 'replaced_by_new'
            
            # Calculate expiry using plan's dynamic validity
            expiry_time = plan.calculate_expiry()
            
            # Create new subscription
            subscription_data = {
                'organization_id': organization_id,
                'subscriber_id': subscriber_id,
                'plan_id': plan_id,
                'status': 'active',
                'start_time': datetime.utcnow(),
                'expiry_time': expiry_time,
                'auto_renew': auto_renew,
                'device_limit': plan.device_limit,
                'bandwidth_up_mbps': plan.bandwidth_up_mbps,
                'bandwidth_down_mbps': plan.bandwidth_down_mbps,
                'billing_cycle': plan.billing_cycle
            }
            
            subscription = Subscription(**subscription_data)
            db.session.add(subscription)
            
            # Update total spent
            subscriber.total_spent = (subscriber.total_spent or 0) + float(plan.price)
            
            db.session.commit()
            
            logger.info(f"Created subscription {subscription.id} for subscriber {subscriber_id}")
            
            # Sync to RADIUS based on subscriber type
            if subscriber.subscriber_type == 'hotspot':
                self.radius_sync_service.sync_hotspot_user_to_radius(
                    subscriber, subscription, plan
                )
            else:
                # For PPPoE, password should already be set
                password = self.encryption.decrypt(subscriber.password_encrypted) if subscriber.password_encrypted else None
                self.radius_sync_service.sync_pppoe_user_to_radius(subscriber, password, subscription, plan)
            
            return subscription
            
        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating subscription: {e}", exc_info=True)
            raise BusinessError(f"Failed to create subscription: {str(e)}")
    
    def get_active_subscription(self, subscriber_id: UUID, organization_id: UUID) -> Optional[Subscription]:
        """Get active subscription for a subscriber"""
        return self.repository.get_active_subscription(subscriber_id, organization_id)
    
    def renew_subscription(self, subscription_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Renew an existing subscription"""
        try:
            subscription = self.repository.get_subscription_by_id(subscription_id, organization_id)
            if not subscription:
                raise NotFoundError('Subscription not found')
            
            plan = subscription.plan
            subscriber = self.get_subscriber(subscription.subscriber_id, organization_id)
            
            # Calculate new expiry
            current_time = datetime.utcnow()
            base_time = max(subscription.expiry_time, current_time)
            new_expiry = base_time + plan.validity_timedelta
            
            # Update subscription
            subscription.expiry_time = new_expiry
            subscription.status = 'active'
            
            db.session.commit()
            
            logger.info(f"Renewed subscription {subscription_id} until {new_expiry}")
            
            # Update RADIUS sync
            self.radius_sync_service.update_subscription_in_radius(subscriber, subscription, plan)
            
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
    
    def cancel_subscription(self, subscription_id: UUID, organization_id: UUID, 
                            reason: str = None) -> bool:
        """Cancel a subscription"""
        try:
            subscription = self.repository.get_subscription_by_id(subscription_id, organization_id)
            if not subscription:
                raise NotFoundError('Subscription not found')
            
            subscription.status = 'cancelled'
            subscription.cancelled_at = datetime.utcnow()
            subscription.cancellation_reason = reason
            db.session.commit()
            
            # Remove from RADIUS
            subscriber = self.get_subscriber(subscription.subscriber_id, organization_id)
            self.radius_sync_service.remove_subscriber_from_radius(subscriber)
            
            logger.info(f"Cancelled subscription {subscription_id}")
            return True
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error cancelling subscription: {e}", exc_info=True)
            raise BusinessError(f"Failed to cancel subscription: {str(e)}")
    
# ACCESS CONTROL & AUTHENTICATION

    def authenticate_subscriber(self, credential: str, password: str, 
                                 organization_id: UUID) -> Optional[Subscriber]:
        """Authenticate subscriber for RADIUS (handles both phone and username)"""
        try:
            # Find subscriber by phone or username
            subscriber = self.repository.get_by_login_credential(credential, organization_id)
            if not subscriber:
                logger.warning(f"Authentication failed: subscriber not found for {credential}")
                return None
            
            # Check status
            if subscriber.status != 'active':
                logger.warning(f"Authentication failed: subscriber {credential} is {subscriber.status}")
                return None
            
            # Check based on subscriber type
            if subscriber.subscriber_type == 'hotspot':
                # Hotspot users authenticate with phone number, password is subscription ID
                active_sub = self.repository.get_active_subscription(subscriber.id, organization_id)
                if not active_sub:
                    logger.warning(f"Authentication failed: no active subscription for {credential}")
                    return None
                
                # Password should match subscription ID (or a generated token)
                if password != str(active_sub.id):
                    logger.warning(f"Authentication failed: invalid password for {credential}")
                    return None
                
                # Check expiry
                if active_sub.expiry_time <= datetime.utcnow():
                    logger.warning(f"Authentication failed: subscription expired for {credential}")
                    return None
                
            else:  # PPPoE user
                # Decrypt and verify password
                if subscriber.password_encrypted:
                    decrypted = self.encryption.decrypt(subscriber.password_encrypted)
                    if password != decrypted:
                        logger.warning(f"Authentication failed: invalid password for {credential}")
                        return None
                else:
                    logger.warning(f"Authentication failed: no password set for {credential}")
                    return None
                
                # Check active subscription for PPPoE
                active_sub = self.repository.get_active_subscription(subscriber.id, organization_id)
                if not active_sub or active_sub.expiry_time <= datetime.utcnow():
                    logger.warning(f"Authentication failed: no active subscription for {credential}")
                    return None
            
            return subscriber
            
        except Exception as e:
            logger.error(f"Error authenticating subscriber: {e}", exc_info=True)
            return None
    
    def check_subscriber_access(self, subscriber_id: UUID, organization_id: UUID, 
                                 device_mac: str) -> Dict[str, Any]:
        """Check if subscriber can access internet"""
        try:
            # Check cache first for performance
            cache_key = f"access_check:{subscriber_id}:{device_mac}"
            cached_result = self.radius_cache.get_auth_data(cache_key)
            if cached_result:
                return cached_result
            
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            
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
            device_limit = subscription.get_device_limit()
            
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
            elif active_device_count >= device_limit:
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
                        'bandwidth_up': subscription.get_bandwidth_up(),
                        'bandwidth_down': subscription.get_bandwidth_down(),
                        'device_limit': device_limit
                    }
                }
            
            # Cache result for 5 seconds
            if result.get('allowed'):
                self.radius_cache.set_auth_data(cache_key, result, ttl=5)
            
            return result
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error checking subscriber access: {e}", exc_info=True)
            raise BusinessError(f"Failed to check access: {str(e)}")
    
# DEVICE MANAGEMENT

    def add_device(self, subscriber_id: UUID, organization_id: UUID, 
                   mac_address: str, device_name: str = None, 
                   device_type: str = None) -> Dict[str, Any]:
        """Add a device to subscriber"""
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            
            # Check if device already exists
            existing_device = self.repository.get_device_by_mac(mac_address, organization_id)
            if existing_device:
                if existing_device.subscriber_id == subscriber_id:
                    return {
                        'success': True,
                        'device': existing_device.to_dict(),
                        'message': 'Device already registered'
                    }
                raise BusinessError(f'Device with MAC {mac_address} is already registered to another account')
            
            # Check device limit
            devices = self.repository.get_devices(subscriber_id, organization_id)
            active_subscription = self.repository.get_active_subscription(subscriber_id, organization_id)
            max_devices = active_subscription.get_device_limit() if active_subscription else 5
            
            if len(devices) >= max_devices:
                raise BusinessError(f'Device limit reached ({max_devices} devices)')
            
            # Add device
            device_data = {
                'organization_id': organization_id,
                'subscriber_id': subscriber_id,
                'mac_address': mac_address.upper(),
                'device_name': device_name,
                'device_type': device_type,
                'is_primary': len(devices) == 0,
                'is_active': True,
                'last_seen_at': datetime.utcnow()
            }
            
            device = self.repository.add_device(device_data)
            
            logger.info(f"Added device {mac_address} to subscriber {subscriber_id}")
            
            return {
                'success': True,
                'device': device.to_dict(),
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
                for session in active_sessions:
                    self.session_service.terminate_session(
                        session.id, organization_id, 'device_removed'
                    )
            
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
    
    def get_devices(self, subscriber_id: UUID, organization_id: UUID) -> List[Dict[str, Any]]:
        """Get all devices for a subscriber"""
        try:
            devices = self.repository.get_devices(subscriber_id, organization_id)
            return [d.to_dict() for d in devices]
        except Exception as e:
            logger.error(f"Error getting devices: {e}", exc_info=True)
            raise BusinessError(f"Failed to get devices: {str(e)}")
    
# STATISTICS & LISTING

    def get_subscriber_stats(self, subscriber_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Get detailed statistics for a subscriber"""
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            
            # Get active sessions
            active_sessions = self.session_service.get_active_sessions_by_subscriber(subscriber_id, organization_id)
            
            # Get devices
            devices = self.repository.get_devices(subscriber_id, organization_id)
            
            # Get active subscription
            active_subscription = self.repository.get_active_subscription(subscriber_id, organization_id)
            
            # Get subscription history
            subscription_history = self.repository.get_subscription_history(subscriber_id, organization_id, limit=10)
            
            # Calculate total data usage from sessions
            total_bytes_in = sum(s.bytes_in or 0 for s in active_sessions)
            total_bytes_out = sum(s.bytes_out or 0 for s in active_sessions)
            
            return {
                'subscriber': subscriber.to_dict(),
                'active_subscription': active_subscription.to_dict(include_plan=True) if active_subscription else None,
                'active_sessions_count': len(active_sessions),
                'active_sessions': [s.to_dict() for s in active_sessions[:10]],
                'devices': [d.to_dict() for d in devices],
                'subscription_history': [s.to_dict(include_plan=True) for s in subscription_history],
                'total_usage_gb': round((total_bytes_in + total_bytes_out) / (1024**3), 2),
                'total_usage_mb': round((total_bytes_in + total_bytes_out) / (1024**2), 2)
            }
            
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error getting subscriber stats: {e}", exc_info=True)
            raise BusinessError(f"Failed to get subscriber statistics: {str(e)}")
    
    def get_organization_subscribers(self, organization_id: UUID, skip: int = 0, limit: int = 100,
                                      filters: Dict = None, subscriber_type: str = None) -> List[Subscriber]:
        """Get all subscribers for an organization"""
        try:
            return self.repository.get_by_organization(organization_id, skip, limit, filters, subscriber_type)
        except Exception as e:
            logger.error(f"Error getting organization subscribers: {e}", exc_info=True)
            raise BusinessError(f"Failed to get subscribers: {str(e)}")
    
    def get_hotspot_users(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[Subscriber]:
        """Get all hotspot users"""
        try:
            return self.repository.get_hotspot_users(organization_id, skip, limit)
        except Exception as e:
            logger.error(f"Error getting hotspot users: {e}", exc_info=True)
            raise BusinessError(f"Failed to get hotspot users: {str(e)}")
    
    def get_pppoe_users(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[Subscriber]:
        """Get all PPPoE users"""
        try:
            return self.repository.get_pppoe_users(organization_id, skip, limit)
        except Exception as e:
            logger.error(f"Error getting PPPoE users: {e}", exc_info=True)
            raise BusinessError(f"Failed to get PPPoE users: {str(e)}")
    
    def get_subscriber_dashboard_stats(self, organization_id: UUID) -> Dict[str, Any]:
        """Get dashboard statistics for subscribers"""
        try:
            stats = self.repository.get_subscriber_stats(organization_id)
            
            # Get recent subscribers
            recent = self.repository.get_recent_subscribers(organization_id, 5)
            
            # Get expiring soon
            expiring = self.repository.get_subscribers_expiring_soon(organization_id, 7)
            
            return {
                **stats,
                'recent_subscribers': [s.to_dict() for s in recent],
                'expiring_soon_count': len(expiring),
                'expiring_soon': [{
                    'id': str(s.id),
                    'phone': s.phone,
                    'name': s.get_full_name(),
                    'expiry': self.repository.get_active_subscription(s.id, organization_id).expiry_time.isoformat()
                    if self.repository.get_active_subscription(s.id, organization_id) else None
                } for s in expiring]
            }
        except Exception as e:
            logger.error(f"Error getting dashboard stats: {e}", exc_info=True)
            raise BusinessError(f"Failed to get dashboard statistics: {str(e)}")