"""
Subscriber Service
==================
Business logic for subscriber management, authentication,
device management, and RADIUS synchronization.

Multi-Tenant: All operations scoped by organization_id.
RADIUS Sync: Automatically syncs subscribers, subscriptions,
             and devices to FreeRADIUS radcheck/radreply tables.
"""

from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID
from datetime import datetime, timedelta
from decimal import Decimal
import re

from flask import current_app

from app.modules.subscriber.repository import SubscriberRepository, PlanRepository
from app.models.subscriber import Subscriber
from app.models.billing import Subscription
from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import (
    BusinessError, NotFoundError, ValidationError,
)
from app.core.database.session import db


class SubscriberService:
    """Business logic for subscriber management with RADIUS integration."""

    def __init__(self):
        self.repository = SubscriberRepository()
        self.plan_repository = PlanRepository()
        self.encryption = EncryptionService()

        # Lazy-loaded dependencies
        self._session_service = None
        self._payment_service = None
        self._sms_service = None
        self._radius_cache = None
        self._radius_sync_service = None

    # =========================================================================
    # LAZY DEPENDENCIES
    # =========================================================================

    @property
    def session_service(self):
        if self._session_service is None:
            from app.modules.session.service import SessionService
            self._session_service = SessionService()
        return self._session_service

    @property
    def payment_service(self):
        if self._payment_service is None:
            from app.modules.payment.service import PaymentService
            self._payment_service = PaymentService()
        return self._payment_service

    @property
    def sms_service(self):
        if self._sms_service is None:
            from app.integrations.sms.provider import SMSService
            self._sms_service = SMSService()
        return self._sms_service

    @property
    def radius_cache(self):
        if self._radius_cache is None:
            from app.integrations.radius.radius_cache import RadiusCache
            self._radius_cache = RadiusCache
        return self._radius_cache

    @property
    def radius_sync_service(self):
        if self._radius_sync_service is None:
            from app.integrations.radius.radius_sync_service import RadiusSyncService
            self._radius_sync_service = RadiusSyncService()
        return self._radius_sync_service

    # =========================================================================
    # PHONE NUMBER UTILITIES
    # =========================================================================

    def normalize_phone(self, phone: str) -> str:
        """Normalize phone number to 254 format."""
        if not phone:
            return phone
        phone = re.sub(r'\D', '', phone)
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif phone.startswith('7') or phone.startswith('1'):
            phone = '254' + phone
        return phone

    def validate_phone(self, phone: str) -> bool:
        """Validate Kenyan phone number format."""
        if not phone:
            raise ValidationError('Phone number is required')

        normalized = self.normalize_phone(phone)
        pattern = r'^254[17]\d{8}$'
        if not re.match(pattern, normalized):
            raise ValidationError(
                'Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX'
            )
        return True

    # =========================================================================
    # SUBSCRIBER CRUD
    # =========================================================================

    def get_subscriber(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> Subscriber:
        """Get subscriber by ID with tenant isolation."""
        subscriber = self.repository.get_by_id(subscriber_id, organization_id)
        if not subscriber:
            raise NotFoundError('Subscriber not found')
        return subscriber

    def get_subscriber_by_phone(
        self, phone: str, organization_id: UUID
    ) -> Optional[Subscriber]:
        """Get subscriber by phone number."""
        return self.repository.get_by_phone(
            self.normalize_phone(phone), organization_id
        )

    def get_subscriber_by_username(
        self, username: str, organization_id: UUID
    ) -> Optional[Subscriber]:
        """Get subscriber by username (PPPoE)."""
        return self.repository.get_by_username(username, organization_id)

    def get_subscriber_by_mac(
        self, mac_address: str, organization_id: UUID
    ) -> Optional[Subscriber]:
        """
        Get subscriber by device MAC address.

        Used by the RADIUS auth handler for MAC auto-connect.
        """
        device = self.repository.get_device_by_mac(
            mac_address.upper(), organization_id
        )
        if device and device.is_active:
            return self.repository.get_by_id(
                device.subscriber_id, organization_id
            )
        return None

    def get_or_create_hotspot_subscriber(
        self,
        organization_id: UUID,
        phone: str,
        name: str = None,
    ) -> Tuple[Subscriber, bool]:
        """
        Get existing hotspot subscriber or create new one.

        Used in M-Pesa payment flow when a new user pays.
        Returns (subscriber, created) tuple.
        """
        try:
            self.validate_phone(phone)
            normalized_phone = self.normalize_phone(phone)

            subscriber = self.repository.get_by_phone(
                normalized_phone, organization_id
            )
            created = False

            if not subscriber:
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
                    'total_spent': 0,
                }
                subscriber = self.repository.create(data)
                created = True
                logger.info(
                    f"Created hotspot subscriber: {normalized_phone} "
                    f"for org {organization_id}"
                )

            return subscriber, created

        except ValidationError:
            raise
        except Exception as e:
            logger.error(
                f"Error in get_or_create_hotspot_subscriber: {e}", exc_info=True
            )
            raise BusinessError(
                f"Failed to get or create subscriber: {str(e)}"
            )

    def create_pppoe_subscriber(
        self,
        organization_id: UUID,
        username: str,
        password: str,
        plan_id: UUID,
        phone: str = None,
        first_name: str = None,
        last_name: str = None,
    ) -> Subscriber:
        """Create a new PPPoE subscriber (admin-created)."""
        try:
            existing = self.repository.get_by_username(username, organization_id)
            if existing:
                raise ValidationError(f'Username "{username}" already exists')

            if phone:
                phone = self.normalize_phone(phone)

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
                'total_spent': 0,
            }

            subscriber = self.repository.create(data)
            logger.info(
                f"Created PPPoE subscriber: {username} for org {organization_id}"
            )

            if plan_id:
                self.create_subscription(
                    subscriber.id, organization_id, plan_id, auto_renew=False
                )

            # Sync to RADIUS
            self.radius_sync_service.sync_pppoe_user_to_radius(
                subscriber, password
            )

            return subscriber

        except ValidationError:
            raise
        except Exception as e:
            logger.error(
                f"Error creating PPPoE subscriber: {e}", exc_info=True
            )
            raise BusinessError(
                f"Failed to create PPPoE subscriber: {str(e)}"
            )

    def update_subscriber(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Subscriber:
        """Update subscriber information."""
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)

            # Validate phone uniqueness
            if 'phone' in data and data['phone']:
                phone = self.normalize_phone(data['phone'])
                existing = self.repository.get_by_phone(phone, organization_id)
                if existing and existing.id != subscriber_id:
                    raise ValidationError('Phone number already in use')
                data['phone'] = phone

            # Validate username uniqueness
            if 'username' in data and data['username']:
                existing = self.repository.get_by_username(
                    data['username'], organization_id
                )
                if existing and existing.id != subscriber_id:
                    raise ValidationError('Username already in use')

            # Encrypt new password
            password_changed = False
            new_password = None
            if 'password' in data and data['password']:
                new_password = data.pop('password')
                data['password_encrypted'] = self.encryption.encrypt(new_password)
                password_changed = True

            updated = self.repository.update(subscriber_id, organization_id, data)

            # Sync to RADIUS if password changed
            if password_changed and subscriber.subscriber_type == 'pppoe':
                subscription = self.repository.get_active_subscription(
                    subscriber_id, organization_id
                )
                self.radius_sync_service.sync_pppoe_user_to_radius(
                    updated, new_password, subscription
                )

            # Invalidate cache
            self._invalidate_subscriber_cache(subscriber, organization_id)

            logger.info(f"Updated subscriber: {subscriber_id}")
            return updated

        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            logger.error(f"Error updating subscriber: {e}", exc_info=True)
            raise BusinessError(f"Failed to update subscriber: {str(e)}")

    def delete_subscriber(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True,
    ) -> bool:
        """Delete or deactivate subscriber."""
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)

            # Terminate all active sessions
            try:
                active_sessions = (
                    self.session_service.get_active_sessions_by_subscriber(
                        subscriber_id, organization_id
                    )
                )
                for session in active_sessions:
                    self.session_service.terminate_session(
                        session.id, organization_id, 'subscriber_deleted'
                    )
            except Exception as e:
                logger.warning(f"Error terminating sessions: {e}")

            # Remove from RADIUS
            self.radius_sync_service.remove_subscriber_from_radius(subscriber)

            # Invalidate cache
            self._invalidate_subscriber_cache(subscriber, organization_id)

            result = self.repository.delete(
                subscriber_id, organization_id, soft_delete
            )
            logger.info(f"Deleted subscriber: {subscriber_id}")
            return result

        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error deleting subscriber: {e}", exc_info=True)
            raise BusinessError(f"Failed to delete subscriber: {str(e)}")

    # =========================================================================
    # SUBSCRIPTION MANAGEMENT
    # =========================================================================

    def create_subscription(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        plan_id: UUID,
        auto_renew: bool = False,
    ) -> Subscription:
        """
        Create a new subscription for a subscriber.

        Automatically:
            - Deactivates old subscriptions
            - Syncs to RADIUS (phone + all device MACs)
            - Invalidates auth cache
        """
        try:
            plan = self.plan_repository.get_by_id(plan_id, organization_id)
            if not plan:
                raise NotFoundError('Plan not found')

            subscriber = self.get_subscriber(subscriber_id, organization_id)

            # Deactivate old subscriptions
            old_sub = self.repository.get_active_subscription(
                subscriber_id, organization_id
            )
            if old_sub:
                old_sub.status = 'expired'
                old_sub.cancellation_reason = 'replaced_by_new'

            # Calculate expiry
            expiry_time = plan.calculate_expiry()

            # Create subscription
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
                'billing_cycle': plan.billing_cycle,
            }

            subscription = Subscription(**subscription_data)
            db.session.add(subscription)

            # Update total spent
            current_spent = (
                Decimal(str(subscriber.total_spent))
                if subscriber.total_spent else Decimal('0')
            )
            plan_price = Decimal(str(plan.price))
            subscriber.total_spent = current_spent + plan_price

            db.session.commit()

            logger.info(
                f"Created subscription {subscription.id} "
                f"for subscriber {subscriber_id}"
            )

            # Sync to RADIUS
            if subscriber.subscriber_type == 'hotspot':
                self.radius_sync_service.sync_hotspot_user_to_radius(
                    subscriber, subscription, plan
                )
            else:
                password = (
                    self.encryption.decrypt(subscriber.password_encrypted)
                    if subscriber.password_encrypted else None
                )
                self.radius_sync_service.sync_pppoe_user_to_radius(
                    subscriber, password, subscription, plan
                )

            # Invalidate cache so next auth uses fresh data
            self._invalidate_subscriber_cache(subscriber, organization_id)

            return subscription

        except (NotFoundError, ValidationError):
            db.session.rollback()
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating subscription: {e}", exc_info=True)
            raise BusinessError(f"Failed to create subscription: {str(e)}")

    def get_active_subscription(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> Optional[Subscription]:
        """Get active subscription for a subscriber."""
        return self.repository.get_active_subscription(
            subscriber_id, organization_id
        )

    def renew_subscription(
        self, subscription_id: UUID, organization_id: UUID
    ) -> Dict[str, Any]:
        """Renew an existing subscription."""
        try:
            subscription = self.repository.get_subscription_by_id(
                subscription_id, organization_id
            )
            if not subscription:
                raise NotFoundError('Subscription not found')

            plan = subscription.plan
            subscriber = self.get_subscriber(
                subscription.subscriber_id, organization_id
            )

            current_time = datetime.utcnow()
            base_time = max(subscription.expiry_time, current_time)
            new_expiry = base_time + plan.validity_timedelta

            subscription.expiry_time = new_expiry
            subscription.status = 'active'
            db.session.commit()

            logger.info(
                f"Renewed subscription {subscription_id} until {new_expiry}"
            )

            # Update RADIUS
            self.radius_sync_service.update_subscription_in_radius(
                subscriber, subscription, plan
            )

            # Invalidate cache
            self._invalidate_subscriber_cache(subscriber, organization_id)

            return {
                'success': True,
                'subscription_id': str(subscription_id),
                'plan_name': plan.name,
                'old_expiry': base_time.isoformat(),
                'new_expiry': new_expiry.isoformat(),
                'message': 'Subscription renewed successfully',
            }

        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error renewing subscription: {e}", exc_info=True)
            raise BusinessError(f"Failed to renew subscription: {str(e)}")

    def cancel_subscription(
        self,
        subscription_id: UUID,
        organization_id: UUID,
        reason: str = None,
    ) -> bool:
        """Cancel a subscription."""
        try:
            subscription = self.repository.get_subscription_by_id(
                subscription_id, organization_id
            )
            if not subscription:
                raise NotFoundError('Subscription not found')

            subscription.status = 'cancelled'
            subscription.cancelled_at = datetime.utcnow()
            subscription.cancellation_reason = reason
            db.session.commit()

            # Remove from RADIUS
            subscriber = self.get_subscriber(
                subscription.subscriber_id, organization_id
            )
            self.radius_sync_service.remove_subscriber_from_radius(subscriber)

            # Invalidate cache
            self._invalidate_subscriber_cache(subscriber, organization_id)

            logger.info(f"Cancelled subscription {subscription_id}")
            return True

        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error cancelling subscription: {e}", exc_info=True)
            raise BusinessError(f"Failed to cancel subscription: {str(e)}")

    # =========================================================================
    # AUTHENTICATION (used by RADIUS auth handler)
    # =========================================================================

    def authenticate_subscriber(
        self,
        credential: str,
        password: str,
        organization_id: UUID,
    ) -> Optional[Subscriber]:
        """
        Authenticate subscriber for RADIUS.

        Handles:
            - Hotspot: credential=phone, password=subscription_id
            - PPPoE: credential=username, password=cleartext

        Note: MAC-only auth is handled directly by RadiusAuthHandler.
        This method is for password-based auth paths.
        """
        try:
            subscriber = self.repository.get_by_login_credential(
                credential, organization_id
            )
            if not subscriber:
                logger.debug(
                    f"Auth failed: subscriber not found for {credential}"
                )
                return None

            if subscriber.status != 'active':
                logger.debug(
                    f"Auth failed: subscriber {credential} is {subscriber.status}"
                )
                return None

            if subscriber.subscriber_type == 'hotspot':
                # Hotspot: password = subscription ID
                active_sub = self.repository.get_active_subscription(
                    subscriber.id, organization_id
                )
                if not active_sub:
                    logger.debug(
                        f"Auth failed: no active subscription for {credential}"
                    )
                    return None

                if password != str(active_sub.id):
                    logger.debug(
                        f"Auth failed: invalid password for {credential}"
                    )
                    return None

                if active_sub.expiry_time <= datetime.utcnow():
                    logger.debug(
                        f"Auth failed: subscription expired for {credential}"
                    )
                    return None

            else:
                # PPPoE: verify cleartext password
                if not subscriber.password_encrypted:
                    logger.debug(
                        f"Auth failed: no password set for {credential}"
                    )
                    return None

                decrypted = self.encryption.decrypt(
                    subscriber.password_encrypted
                )
                if password != decrypted:
                    logger.debug(
                        f"Auth failed: invalid password for {credential}"
                    )
                    return None

                active_sub = self.repository.get_active_subscription(
                    subscriber.id, organization_id
                )
                if not active_sub or active_sub.expiry_time <= datetime.utcnow():
                    logger.debug(
                        f"Auth failed: no active subscription for {credential}"
                    )
                    return None

            return subscriber

        except Exception as e:
            logger.error(f"Error authenticating subscriber: {e}", exc_info=True)
            return None

    def check_subscriber_access(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        device_mac: str,
    ) -> Dict[str, Any]:
        """
        Check if subscriber can access internet on a specific device.

        Enforces device limits and subscription validity.
        Used by RADIUS auth handler during MAC auth.
        """
        try:
            cache_key = f"access_check:{organization_id}:{subscriber_id}:{device_mac}"
            cached = self.radius_cache.get_auth_data(cache_key)
            if cached:
                return cached

            subscriber = self.get_subscriber(subscriber_id, organization_id)

            if subscriber.status != 'active':
                return self._access_denied(
                    'subscriber_inactive',
                    'Your account is inactive. Please contact support.',
                )

            subscription = self.repository.get_active_subscription(
                subscriber_id, organization_id
            )
            if not subscription:
                return self._access_denied(
                    'no_active_subscription',
                    'No active subscription. Please purchase a plan.',
                )

            if subscription.expiry_time < datetime.utcnow():
                return self._access_denied(
                    'subscription_expired',
                    'Your subscription has expired. Please renew.',
                )

            # Count active devices
            active_sessions = (
                self.session_service.get_active_sessions_by_subscriber(
                    subscriber_id, organization_id
                )
            )
            active_device_macs = {
                s.device_mac for s in active_sessions if s.device_mac
            }
            active_device_count = len(active_device_macs)
            device_limit = subscription.get_device_limit()

            # Check if this device already connected
            device_session = next(
                (s for s in active_sessions if s.device_mac == device_mac),
                None,
            )

            if device_session:
                result = {
                    'allowed': True,
                    'already_connected': True,
                    'session_id': str(device_session.id),
                    'message': 'Device already connected',
                }
            elif active_device_count >= device_limit:
                result = self._access_denied(
                    'device_limit_reached',
                    f'Device limit reached ({device_limit} devices). '
                    'Please disconnect another device first.',
                )
            else:
                result = {
                    'allowed': True,
                    'subscription': {
                        'id': str(subscription.id),
                        'plan_name': subscription.plan.name,
                        'expiry': subscription.expiry_time.isoformat(),
                        'bandwidth_up': subscription.get_bandwidth_up(),
                        'bandwidth_down': subscription.get_bandwidth_down(),
                        'device_limit': device_limit,
                    },
                }

            # Cache briefly
            self.radius_cache.set_auth_data(
                cache_key, result, ttl=5,
                organization_id=str(organization_id),
            )

            return result

        except NotFoundError:
            raise
        except Exception as e:
            logger.error(
                f"Error checking subscriber access: {e}", exc_info=True
            )
            raise BusinessError(f"Failed to check access: {str(e)}")

    def _access_denied(self, reason: str, message: str) -> Dict[str, Any]:
        """Build access denied response."""
        return {
            'allowed': False,
            'reason': reason,
            'message': message,
        }

    # =========================================================================
    # DEVICE MANAGEMENT
    # =========================================================================

    def add_device(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        mac_address: str,
        device_name: str = None,
        device_type: str = None,
    ) -> Dict[str, Any]:
        """
        Add a device to a subscriber.

        Automatically syncs the device MAC to RADIUS for auto-connect.
        """
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            mac = mac_address.upper().strip()

            # Check if device already exists
            existing = self.repository.get_device_by_mac(mac, organization_id)
            if existing:
                if existing.subscriber_id == subscriber_id:
                    # Update last seen
                    self.repository.update_device_last_seen(
                        existing.id, organization_id
                    )
                    return {
                        'success': True,
                        'device': existing.to_dict(),
                        'message': 'Device already registered',
                    }
                raise BusinessError(
                    f'Device with MAC {mac} is already registered '
                    'to another account'
                )

            # Check device limit
            devices = self.repository.get_devices(subscriber_id, organization_id)
            subscription = self.repository.get_active_subscription(
                subscriber_id, organization_id
            )
            max_devices = subscription.get_device_limit() if subscription else 5

            if len(devices) >= max_devices:
                raise BusinessError(
                    f'Device limit reached ({max_devices} devices)'
                )

            # Create device
            device_data = {
                'mac_address': mac,
                'device_name': device_name,
                'device_type': device_type,
                'is_primary': len(devices) == 0,
                'is_active': True,
                'last_seen_at': datetime.utcnow(),
            }

            device = self.repository.add_device(
                subscriber_id=subscriber_id,
                organization_id=organization_id,
                data=device_data,
            )

            # Sync device MAC to RADIUS for auto-connect
            if subscription:
                self.radius_sync_service.sync_device_mac_to_radius(
                    subscriber, mac, subscription
                )

            # Invalidate device count cache
            self.radius_cache.invalidate_device_count(
                str(organization_id), str(subscriber_id)
            )

            logger.info(
                f"Added device {mac} to subscriber {subscriber_id}"
            )

            return {
                'success': True,
                'device': device.to_dict(),
                'message': 'Device added successfully',
            }

        except (NotFoundError, BusinessError):
            raise
        except Exception as e:
            logger.error(f"Error adding device: {e}", exc_info=True)
            raise BusinessError(f"Failed to add device: {str(e)}")

    def remove_device(
        self,
        device_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Remove a device from a subscriber.

        Terminates active sessions and removes from RADIUS.
        """
        try:
            device = self.repository.get_device_by_id(device_id, organization_id)
            if not device:
                raise NotFoundError('Device not found')

            # Terminate active sessions for this device
            try:
                active_sessions = (
                    self.session_service.get_active_sessions_by_device(
                        device.mac_address, organization_id
                    )
                )
                for session in active_sessions:
                    self.session_service.terminate_session(
                        session.id, organization_id, 'device_removed'
                    )
            except Exception as e:
                logger.warning(f"Error terminating device sessions: {e}")

            # Remove from RADIUS
            self.radius_sync_service.remove_device_mac_from_radius(
                device.mac_address, organization_id
            )

            # Invalidate cache
            self.radius_cache.invalidate_device_count(
                str(organization_id), str(device.subscriber_id)
            )
            self.radius_cache.delete_auth_data(
                device.mac_address, str(organization_id)
            )

            self.repository.remove_device(device_id, organization_id)

            logger.info(
                f"Removed device {device.mac_address} "
                f"from subscriber {device.subscriber_id}"
            )

            return {
                'success': True,
                'message': 'Device removed successfully',
            }

        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error removing device: {e}", exc_info=True)
            raise BusinessError(f"Failed to remove device: {str(e)}")

    def get_devices(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
    ) -> List[Dict[str, Any]]:
        """Get all devices for a subscriber."""
        try:
            devices = self.repository.get_devices(subscriber_id, organization_id)
            return [d.to_dict() for d in devices]
        except Exception as e:
            logger.error(f"Error getting devices: {e}", exc_info=True)
            raise BusinessError(f"Failed to get devices: {str(e)}")

    # =========================================================================
    # STATISTICS & LISTING
    # =========================================================================

    def get_subscriber_stats(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """Get detailed statistics for a subscriber."""
        try:
            subscriber = self.get_subscriber(subscriber_id, organization_id)
            active_sessions = (
                self.session_service.get_active_sessions_by_subscriber(
                    subscriber_id, organization_id
                )
            )
            devices = self.repository.get_devices(subscriber_id, organization_id)
            subscription = self.repository.get_active_subscription(
                subscriber_id, organization_id
            )
            history = self.repository.get_subscription_history(
                subscriber_id, organization_id, limit=10
            )

            total_bytes_in = sum(s.bytes_in or 0 for s in active_sessions)
            total_bytes_out = sum(s.bytes_out or 0 for s in active_sessions)

            return {
                'subscriber': subscriber.to_dict(),
                'active_subscription': (
                    subscription.to_dict(include_plan=True)
                    if subscription else None
                ),
                'active_sessions_count': len(active_sessions),
                'active_sessions': [
                    s.to_dict() for s in active_sessions[:10]
                ],
                'devices': [d.to_dict() for d in devices],
                'subscription_history': [
                    s.to_dict(include_plan=True) for s in history
                ],
                'total_usage_gb': round(
                    (total_bytes_in + total_bytes_out) / (1024 ** 3), 2
                ),
                'total_usage_mb': round(
                    (total_bytes_in + total_bytes_out) / (1024 ** 2), 2
                ),
            }

        except NotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error getting subscriber stats: {e}", exc_info=True)
            raise BusinessError(
                f"Failed to get subscriber statistics: {str(e)}"
            )

    def get_organization_subscribers(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
        filters: Dict = None,
        subscriber_type: str = None,
    ) -> List[Subscriber]:
        """Get all subscribers for an organization."""
        try:
            return self.repository.get_by_organization(
                organization_id, skip, limit, filters, subscriber_type
            )
        except Exception as e:
            logger.error(
                f"Error getting organization subscribers: {e}", exc_info=True
            )
            raise BusinessError(f"Failed to get subscribers: {str(e)}")

    def get_hotspot_users(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Subscriber]:
        """Get all hotspot users."""
        return self.repository.get_hotspot_users(organization_id, skip, limit)

    def get_pppoe_users(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Subscriber]:
        """Get all PPPoE users."""
        return self.repository.get_pppoe_users(organization_id, skip, limit)

    def get_subscriber_dashboard_stats(self, organization_id: UUID) -> Dict[str, Any]:
        """Get dashboard statistics for subscribers"""
        try:
            stats = self.repository.get_subscriber_stats(organization_id)
            recent = self.repository.get_recent_subscribers(organization_id, 5)
            expiring = self.repository.get_subscribers_expiring_soon(organization_id, 7)

            # Build expiring_soon list safely
            expiring_list = []
            for s in expiring:
                active_sub = self.repository.get_active_subscription(s.id, organization_id)
                expiring_list.append({
                    'id': str(s.id),
                    'phone': s.phone,
                    'name': s.get_full_name(),
                    'expiry': active_sub.expiry_time.isoformat() if active_sub else None,
                })

            return {
                **stats,
                'recent_subscribers': [s.to_dict() for s in recent],
                'expiring_soon_count': len(expiring),
                'expiring_soon': expiring_list,
            }
        except Exception as e:
            logger.error(f"Error getting dashboard stats: {e}", exc_info=True)
            raise BusinessError(f"Failed to get dashboard statistics: {str(e)}")

    # =========================================================================
    # CACHE INVALIDATION
    # =========================================================================

    def _invalidate_subscriber_cache(
        self,
        subscriber: Subscriber,
        organization_id: UUID,
    ) -> None:
        """
        Invalidate all cached data for a subscriber.

        Called whenever subscription, device, or status changes.
        """
        try:
            org_str = str(organization_id)

            # Invalidate by phone
            if subscriber.phone:
                self.radius_cache.delete_auth_data(subscriber.phone, org_str)

            # Invalidate by username
            if subscriber.username:
                self.radius_cache.delete_auth_data(subscriber.username, org_str)

            # Invalidate all device MACs
            devices = self.repository.get_devices(subscriber.id, organization_id)
            for device in devices:
                self.radius_cache.delete_auth_data(
                    device.mac_address, org_str
                )

            # Invalidate device count
            self.radius_cache.invalidate_device_count(
                org_str, str(subscriber.id)
            )

            logger.debug(
                f"Invalidated cache for subscriber {subscriber.id}"
            )
        except Exception as e:
            logger.warning(f"Error invalidating subscriber cache: {e}")