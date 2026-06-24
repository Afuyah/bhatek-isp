from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID
from datetime import datetime, timedelta
import random
import string
import hashlib
import secrets

from app.modules.billing.repository import (
    PlanRepository,
    SubscriptionRepository,
    VoucherRepository,
    VoucherBatchRepository,
    DiscountCouponRepository,
)
from app.models.billing import (
    Plan, Subscription, Voucher, VoucherBatch,
    DiscountCoupon, Invoice, InvoiceItem,
)
from app.core.logging.logger import logger
from app.core.exceptions.handlers import (
    NotFoundError, BusinessError, ValidationError,
)
from app.core.database.session import db


class BillingService:
    """
    Complete billing service for plans, subscriptions, vouchers, and discounts.

    Voucher Concepts:
        - voucher.expires_at: When the voucher CODE can no longer be redeemed
        - subscription.expiry_time: When the internet ACCESS ends
        - A voucher CAN override the plan's validity (e.g., 2-hour voucher on a 30-day plan)
        - Activation types:
            - 'immediate': Subscription starts immediately on redemption
            - 'first_use': Subscription clock starts on redemption
            - 'scheduled': Subscription starts at a future date
    """

    # Maximum batch size for voucher generation
    MAX_BATCH_SIZE = 1000

    # Characters used for voucher codes (excluding confusing ones)
    VOUCHER_CHARS = string.ascii_uppercase + string.digits
    VOUCHER_CHARS = VOUCHER_CHARS.translate(
        str.maketrans('', '', '0O1IL5S8B')
    )
    VOUCHER_CODE_LENGTH = 12

    def __init__(self):
        self.plan_repo = PlanRepository()
        self.subscription_repo = SubscriptionRepository()
        self.voucher_repo = VoucherRepository()
        self.voucher_batch_repo = VoucherBatchRepository()
        self.discount_repo = DiscountCouponRepository()

        # Lazy-loaded dependencies
        self._subscriber_repo = None
        self._radius_sync_service = None
        self._radius_cache = None

    # LAZY DEPENDENCIES

    @property
    def subscriber_repo(self):
        if self._subscriber_repo is None:
            from app.modules.subscriber.repository import SubscriberRepository
            self._subscriber_repo = SubscriberRepository()
        return self._subscriber_repo

    @property
    def radius_sync_service(self):
        if self._radius_sync_service is None:
            from app.integrations.radius.radius_sync_service import RadiusSyncService
            self._radius_sync_service = RadiusSyncService()
        return self._radius_sync_service

    @property
    def radius_cache(self):
        if self._radius_cache is None:
            from app.integrations.radius.radius_cache import RadiusCache
            self._radius_cache = RadiusCache
        return self._radius_cache

    # PLAN MANAGEMENT

    def create_plan(
        self,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Plan:
        """
        Create a new plan with dynamic validity validation.

        Required fields based on validity_type:
            - time_based: validity_value + validity_unit
            - data_based: data_limit_mb
            - unlimited: nothing extra
        """
        validity_type = data.get('validity_type', 'time_based')

        # Validate based on validity type
        if validity_type == 'time_based':
            if not data.get('validity_value') or data['validity_value'] < 1:
                raise ValidationError(
                    "Validity value must be at least 1 for time-based plans"
                )
            if not data.get('validity_unit'):
                raise ValidationError(
                    "Validity unit is required for time-based plans "
                    "(minutes, hours, days, months, years)"
                )

        elif validity_type == 'data_based':
            if not data.get('data_limit_mb') or data['data_limit_mb'] < 1:
                raise ValidationError(
                    "Data limit must be at least 1 MB for data-based plans"
                )

        # Validate pricing
        if not data.get('price') or float(data['price']) <= 0:
            raise ValidationError("Plan price must be greater than 0")

        # Validate device limit
        if data.get('device_limit', 1) < 1:
            raise ValidationError("Device limit must be at least 1")

        data['organization_id'] = organization_id

        plan = self.plan_repo.create(data)
        logger.info(
            f"Created plan '{plan.name}' | type={plan.plan_type} | "
            f"validity={plan.validity_display} | price={plan.price} | "
            f"org={organization_id}"
        )
        return plan

    def get_plan(
        self,
        plan_id: UUID,
        organization_id: UUID,
    ) -> Plan:
        """Get a plan by ID with tenant isolation."""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        return plan

    def get_plans(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
        only_active: bool = True,
        plan_type: str = None,
    ) -> List[Plan]:
        """Get all plans for an organization with optional filters."""
        return self.plan_repo.get_by_organization(
            organization_id, skip, limit, only_active, plan_type
        )

    def get_public_plans(self, organization_id: UUID) -> List[Plan]:
        """
        Get plans visible on the captive portal.

        Only returns active, public plans sorted by price.
        Filters by hotspot-compatible types (hotspot, both).
        """
        plans = self.plan_repo.get_public_plans(organization_id)
        # Filter to only hotspot-compatible plans for captive portal
        return [
            p for p in plans
            if p.plan_type in ('hotspot', 'both')
        ]

    def get_plans_by_type(
        self,
        organization_id: UUID,
        plan_type: str,
    ) -> List[Plan]:
        """Get plans filtered by type (hotspot, pppoe, both)."""
        return self.plan_repo.get_by_plan_type(organization_id, plan_type)

    def update_plan(
        self,
        plan_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Plan:
        """
        Update a plan. Changes take effect for NEW subscriptions only.
        Existing subscriptions retain their original plan settings.
        """
        plan = self.plan_repo.update(plan_id, organization_id, data)
        if not plan:
            raise NotFoundError("Plan not found")

        # Invalidate RADIUS cache for subscribers on this plan
        # so they get updated attributes on next auth
        self._invalidate_plan_cache(plan_id, organization_id)

        logger.info(f"Updated plan '{plan.name}' | org={organization_id}")
        return plan

    def delete_plan(
        self,
        plan_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True,
    ) -> bool:
        """
        Delete or deactivate a plan.

        Soft delete: Sets is_active=False. Existing subscriptions continue.
        Hard delete: Removes only if no active subscriptions exist.
        """
        if not soft_delete:
            # Check for active subscriptions
            active_subs = self.subscription_repo.get_by_plan(
                plan_id, organization_id
            )
            active_count = sum(
                1 for s in active_subs
                if s.status == 'active' and s.expiry_time > datetime.utcnow()
            )
            if active_count > 0:
                raise BusinessError(
                    f"Cannot delete plan with {active_count} active subscriptions. "
                    "Deactivate it instead."
                )

        result = self.plan_repo.delete(plan_id, organization_id, soft_delete)
        logger.info(
            f"Plan {plan_id} {'deactivated' if soft_delete else 'deleted'} | "
            f"org={organization_id}"
        )
        return result

    def _invalidate_plan_cache(
        self,
        plan_id: UUID,
        organization_id: UUID,
    ) -> None:
        """Invalidate RADIUS cache for subscribers on a plan."""
        try:
            subscriptions = self.subscription_repo.get_by_plan(
                plan_id, organization_id
            )
            for sub in subscriptions:
                subscriber = self.subscriber_repo.get_by_id(
                    sub.subscriber_id, organization_id
                )
                if subscriber and subscriber.phone:
                    self.radius_cache.delete_auth_data(
                        subscriber.phone, str(organization_id)
                    )
        except Exception as e:
            logger.warning(f"Failed to invalidate plan cache: {e}")

    # SUBSCRIPTION MANAGEMENT

    def create_subscription(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        plan_id: UUID,
        auto_renew: bool = False,
        device_limit: int = None,
        bandwidth_up_mbps: int = None,
        bandwidth_down_mbps: int = None,
    ) -> Subscription:
        """
        Create a new subscription for a subscriber.

        Verifies the plan is active before creating.
        Deactivates any existing active subscription (replaced by new one).
        Syncs subscriber to RADIUS.
        """
        # Verify plan exists and is active
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        if not plan.is_active:
            raise BusinessError(
                f"Plan '{plan.name}' is no longer available"
            )

        # Verify subscriber exists
        subscriber = self.subscriber_repo.get_by_id(
            subscriber_id, organization_id
        )
        if not subscriber:
            raise NotFoundError("Subscriber not found")

        # Deactivate any existing active subscription
        old_sub = self.subscription_repo.get_active_by_subscriber(
            subscriber_id, organization_id
        )
        if old_sub:
            self.subscription_repo.update_status(
                old_sub.id, organization_id, 'expired', 'replaced_by_new'
            )
            # Remove old RADIUS entries
            try:
                self.radius_sync_service.remove_subscriber_from_radius(
                    subscriber
                )
            except Exception as e:
                logger.warning(f"Failed to remove old RADIUS entries: {e}")

        # Calculate expiry using plan's validity
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
            'device_limit': device_limit or plan.device_limit,
            'bandwidth_up_mbps': bandwidth_up_mbps or plan.bandwidth_up_mbps,
            'bandwidth_down_mbps': bandwidth_down_mbps or plan.bandwidth_down_mbps,
            'billing_cycle': plan.billing_cycle,
        }

        subscription = self.subscription_repo.create(subscription_data)

        # Sync to RADIUS
        try:
            if subscriber.subscriber_type == 'hotspot':
                self.radius_sync_service.sync_hotspot_user_to_radius(
                    subscriber, subscription, plan
                )
            else:
                password = None
                if subscriber.password_encrypted:
                    from app.core.security.encryption import EncryptionService
                    password = EncryptionService().decrypt(
                        subscriber.password_encrypted
                    )
                self.radius_sync_service.sync_pppoe_user_to_radius(
                    subscriber, password, subscription, plan
                )
        except Exception as e:
            logger.warning(f"RADIUS sync failed for new subscription: {e}")

        # Update RADIUS cache
        try:
            username = subscriber.phone or subscriber.username
            if username:
                self.radius_cache.set_auth_data(
                    username=username,
                    data={
                        'subscriber_id': str(subscriber_id),
                        'organization_id': str(organization_id),
                        'plan_name': plan.name,
                        'bandwidth_up': subscription.get_bandwidth_up(),
                        'bandwidth_down': subscription.get_bandwidth_down(),
                        'session_timeout': plan.session_timeout_seconds or 86400,
                        'idle_timeout': plan.idle_timeout_seconds or 300,
                        'expiry': subscription.expiry_time.isoformat(),
                        'device_limit': subscription.get_device_limit(),
                        'status': 'active',
                    },
                    ttl=300,
                    organization_id=str(organization_id),
                )
        except Exception as e:
            logger.warning(f"RADIUS cache update failed: {e}")

        logger.info(
            f"Created subscription {subscription.id} | "
            f"subscriber={subscriber_id} | plan={plan.name} | "
            f"expires={expiry_time.isoformat()} | org={organization_id}"
        )
        return subscription

    def get_subscription(
        self,
        subscription_id: UUID,
        organization_id: UUID,
    ) -> Subscription:
        """Get subscription by ID with tenant isolation."""
        subscription = self.subscription_repo.get_by_id(
            subscription_id, organization_id
        )
        if not subscription:
            raise NotFoundError("Subscription not found")
        return subscription

    def get_active_subscription(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
    ) -> Optional[Subscription]:
        """
        Get active subscription for a subscriber.

        Active = status=='active' AND expiry_time > now.
        """
        return self.subscription_repo.get_active_by_subscriber(
            subscriber_id, organization_id
        )

    def get_subscriber_subscriptions(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        limit: int = 50,
    ) -> List[Subscription]:
        """Get subscription history for a subscriber."""
        return self.subscription_repo.get_by_subscriber(
            subscriber_id, organization_id, limit=limit
        )

    def renew_subscription(
        self,
        subscription_id: UUID,
        organization_id: UUID,
    ) -> Subscription:
        """
        Renew an existing subscription by extending its expiry.

        The new expiry is calculated from the plan's validity period.
        If the subscription already expired, it starts from now.
        """
        subscription = self.subscription_repo.get_by_id(
            subscription_id, organization_id, include_inactive=True
        )
        if not subscription:
            raise NotFoundError("Subscription not found")

        plan = subscription.plan
        if not plan.is_active:
            raise BusinessError(
                f"Plan '{plan.name}' is no longer active. Cannot renew."
            )

        # Calculate new expiry
        current_time = datetime.utcnow()
        base_time = max(subscription.expiry_time, current_time)
        new_expiry = base_time + plan.validity_timedelta

        # Update subscription
        subscription.expiry_time = new_expiry
        subscription.status = 'active'
        subscription.cancelled_at = None
        subscription.cancellation_reason = None
        db.session.commit()

        # Sync updated expiry to RADIUS
        subscriber = self.subscriber_repo.get_by_id(
            subscription.subscriber_id, organization_id
        )
        if subscriber:
            try:
                self.radius_sync_service.update_subscription_in_radius(
                    subscriber, subscription, plan
                )
            except Exception as e:
                logger.warning(f"RADIUS update on renew failed: {e}")

        logger.info(
            f"Renewed subscription {subscription_id} | "
            f"new_expiry={new_expiry.isoformat()} | org={organization_id}"
        )
        return subscription

    def cancel_subscription(
        self,
        subscription_id: UUID,
        organization_id: UUID,
        reason: str = None,
    ) -> bool:
        """
        Cancel a subscription immediately.

        Removes RADIUS access and invalidates cache.
        """
        subscription = self.subscription_repo.get_by_id(
            subscription_id, organization_id
        )
        if not subscription:
            raise NotFoundError("Subscription not found")

        if subscription.status != 'active':
            raise BusinessError(
                f"Cannot cancel subscription with status '{subscription.status}'"
            )

        # Update status
        self.subscription_repo.update_status(
            subscription_id, organization_id, 'cancelled', reason
        )

        # Remove from RADIUS
        subscriber = self.subscriber_repo.get_by_id(
            subscription.subscriber_id, organization_id
        )
        if subscriber:
            try:
                self.radius_sync_service.remove_subscriber_from_radius(
                    subscriber
                )
            except Exception as e:
                logger.warning(f"RADIUS removal on cancel failed: {e}")

            # Invalidate cache
            username = subscriber.phone or subscriber.username
            if username:
                self.radius_cache.delete_auth_data(
                    username, str(organization_id)
                )

        logger.info(
            f"Cancelled subscription {subscription_id} | "
            f"reason={reason} | org={organization_id}"
        )
        return True

    # VOUCHER MANAGEMENT

    def _generate_voucher_code(self) -> str:
        """
        Generate a unique, human-readable voucher code.

        Format: XXXX-XXXX-XXXX (12 characters, hyphenated)
        Excludes confusing characters: 0, O, 1, I, L, 5, S, 8, B
        Retries if collision occurs (database unique constraint).
        """
        for _ in range(10):  # Retry up to 10 times for uniqueness
            code = ''.join(
                random.choices(self.VOUCHER_CHARS, k=self.VOUCHER_CODE_LENGTH)
            )
            formatted = f"{code[:4]}-{code[4:8]}-{code[8:12]}"

            # Check uniqueness
            existing = self.voucher_repo.get_by_code(formatted, None)
            if not existing:
                return formatted

        # Fallback: use secrets for guaranteed uniqueness
        code = secrets.token_hex(6).upper()[:12]
        return f"{code[:4]}-{code[4:8]}-{code[8:12]}"

    def _generate_voucher_password(self, code: str) -> str:
        """Generate a password hash for voucher verification."""
        return hashlib.sha256(
            f"{code}{secrets.token_hex(8)}".encode()
        ).hexdigest()[:12].upper()

    def _calculate_voucher_expiry(
        self,
        plan: Plan,
        validity_value: int = None,
        validity_unit: str = None,
        custom_expires_at: datetime = None,
    ) -> datetime:
        """
        Calculate when a voucher CODE expires (can no longer be redeemed).

        Priority:
            1. custom_expires_at (explicit date)
            2. validity_value + validity_unit (voucher overrides plan)
            3. Plan's validity period (default)
        """
        if custom_expires_at:
            return custom_expires_at

        now = datetime.utcnow()

        if validity_value and validity_unit:
            unit = validity_unit.lower()
            value = int(validity_value)

            if unit == 'minutes':
                return now + timedelta(minutes=value)
            elif unit == 'hours':
                return now + timedelta(hours=value)
            elif unit == 'days':
                return now + timedelta(days=value)
            elif unit == 'months':
                return now + timedelta(days=value * 30)
            elif unit == 'years':
                return now + timedelta(days=value * 365)

        # Default: use plan validity
        return plan.calculate_expiry()

    def _calculate_subscription_expiry_from_voucher(
        self,
        voucher: Voucher,
    ) -> datetime:
        """
        Calculate subscription expiry when redeeming a voucher.

        This is the ACCESS expiry, not the voucher code expiry.
        Uses the voucher's validity override if set, otherwise the plan's validity.

        For 'first_use' activation, the clock starts at redemption time.
        """
        plan = voucher.plan
        now = datetime.utcnow()

        # If voucher has custom validity, use it
        if voucher.validity_value and voucher.validity_unit:
            unit = voucher.validity_unit.lower()
            value = int(voucher.validity_value)

            if unit == 'minutes':
                return now + timedelta(minutes=value)
            elif unit == 'hours':
                return now + timedelta(hours=value)
            elif unit == 'days':
                return now + timedelta(days=value)
            elif unit == 'months':
                return now + timedelta(days=value * 30)
            elif unit == 'years':
                return now + timedelta(days=value * 365)

        # Default: use plan validity
        return plan.calculate_expiry(now)

    def create_voucher(
        self,
        organization_id: UUID,
        plan_id: UUID,
        max_uses: int = 1,
        validity_value: int = None,
        validity_unit: str = None,
        activation_type: str = 'immediate',
        custom_expires_at: datetime = None,
        created_by: UUID = None,
    ) -> Voucher:
        """
        Create a single voucher.

        The voucher can override the plan's validity:
            - validity_value + validity_unit: Custom validity for subscriptions
            - If not provided, the plan's default validity is used

        Activation types:
            - 'immediate': Ready to use immediately
            - 'first_use': Clock starts on redemption
            - 'scheduled': Activates at a future date
        """
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        if not plan.is_active:
            raise BusinessError(
                f"Plan '{plan.name}' is not active. Cannot create vouchers."
            )

        # Validate activation type
        if activation_type not in ('immediate', 'first_use', 'scheduled'):
            raise ValidationError(
                f"Invalid activation type: {activation_type}. "
                "Use 'immediate', 'first_use', or 'scheduled'."
            )

        # Calculate voucher code expiry
        expires_at = self._calculate_voucher_expiry(
            plan, validity_value, validity_unit, custom_expires_at
        )

        code = self._generate_voucher_code()

        voucher_data = {
            'organization_id': organization_id,
            'plan_id': plan_id,
            'code': code,
            'password_hash': self._generate_voucher_password(code),
            'max_uses': max_uses,
            'expires_at': expires_at,
            'validity_value': validity_value,
            'validity_unit': validity_unit,
            'activation_type': activation_type,
            'price_paid': float(plan.price),
            'created_by': created_by,
            'status': 'active',
        }

        voucher = self.voucher_repo.create(voucher_data)

        logger.info(
            f"Created voucher {code} | plan={plan.name} | "
            f"validity={voucher.validity_display} | "
            f"activation={activation_type} | org={organization_id}"
        )
        return voucher

    def create_voucher_batch(
        self,
        organization_id: UUID,
        plan_id: UUID,
        batch_name: str,
        quantity: int,
        validity_value: int = None,
        validity_unit: str = None,
        expires_in_days: int = None,
        activation_type: str = 'immediate',
        created_by: UUID = None,
    ) -> VoucherBatch:
        """
        Create a batch of vouchers with dynamic validity.

        Args:
            quantity: Number of vouchers (max 1000)
            validity_value + validity_unit: Override plan validity
            expires_in_days: Legacy — how long until voucher codes expire
            activation_type: 'immediate', 'first_use', or 'scheduled'
        """
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        if not plan.is_active:
            raise BusinessError("Plan is not active")

        if quantity < 1:
            raise ValidationError("Quantity must be at least 1")
        if quantity > self.MAX_BATCH_SIZE:
            raise ValidationError(
                f"Maximum batch size is {self.MAX_BATCH_SIZE} vouchers"
            )

        # Calculate voucher code expiry
        if expires_in_days:
            expiry_delta = timedelta(days=expires_in_days)
        elif validity_value and validity_unit:
            expiry_delta = self._calculate_validity_delta(
                validity_value, validity_unit
            )
        else:
            expiry_delta = plan.validity_timedelta

        voucher_expires_at = datetime.utcnow() + expiry_delta

        # Create batch record
        batch_data = {
            'organization_id': organization_id,
            'plan_id': plan_id,
            'batch_name': batch_name,
            'quantity': quantity,
            'price_per_voucher': float(plan.price),
            'total_amount': float(plan.price) * quantity,
            'validity_value': validity_value,
            'validity_unit': validity_unit,
            'expires_in_days': expires_in_days or expiry_delta.days,
            'created_by': created_by,
            'status': 'generated',
            'generated_at': datetime.utcnow(),
        }

        batch = self.voucher_batch_repo.create(batch_data)

        # Generate unique voucher codes
        vouchers_data = []
        used_codes = set()

        for _ in range(quantity):
            code = self._generate_voucher_code()

            # Ensure uniqueness within batch
            retries = 0
            while code in used_codes and retries < 5:
                code = self._generate_voucher_code()
                retries += 1

            used_codes.add(code)

            vouchers_data.append({
                'organization_id': organization_id,
                'plan_id': plan_id,
                'batch_id': batch.id,
                'code': code,
                'password_hash': self._generate_voucher_password(code),
                'max_uses': 1,
                'expires_at': voucher_expires_at,
                'validity_value': validity_value,
                'validity_unit': validity_unit,
                'activation_type': activation_type,
                'price_paid': float(plan.price),
                'created_by': created_by,
                'status': 'active',
            })

        self.voucher_repo.create_batch(vouchers_data)

        logger.info(
            f"Created voucher batch '{batch_name}' | "
            f"quantity={quantity} | plan={plan.name} | "
            f"validity={validity_value} {validity_unit if validity_unit else 'plan default'} | "
            f"org={organization_id}"
        )
        return batch

    def _calculate_validity_delta(
        self,
        value: int,
        unit: str,
    ) -> timedelta:
        """Convert validity value + unit to timedelta."""
        unit = unit.lower()
        value = int(value)

        if unit == 'minutes':
            return timedelta(minutes=value)
        elif unit == 'hours':
            return timedelta(hours=value)
        elif unit == 'days':
            return timedelta(days=value)
        elif unit == 'months':
            return timedelta(days=value * 30)
        elif unit == 'years':
            return timedelta(days=value * 365)
        else:
            return timedelta(days=30)

    def redeem_voucher(
        self,
        organization_id: UUID,
        voucher_code: str,
        subscriber_id: UUID,
        router_id: UUID = None,
        device_mac: str = None,
    ) -> Dict[str, Any]:
        """
        Redeem a voucher for a subscriber.

        Steps:
            1. Validate voucher code
            2. Verify subscriber exists
            3. Handle first-use activation (clock starts now)
            4. Calculate subscription expiry from voucher validity
            5. Deactivate old subscriptions
            6. Create new subscription
            7. Mark voucher as used
            8. Sync to RADIUS
            9. Register device MAC if provided

        Returns:
            Dict with subscription details and expiry
        """
        # Normalize code (remove hyphens, uppercase)
        normalized_code = voucher_code.upper().replace('-', '').replace(' ', '')

        # Validate voucher
        voucher = self.voucher_repo.get_valid_by_code(
            normalized_code, organization_id
        )
        if not voucher:
            # Check if voucher exists but is expired/used
            existing = self.voucher_repo.get_by_code(
                normalized_code, organization_id
            )
            if existing:
                if existing.status == 'used':
                    raise BusinessError("This voucher has already been used")
                elif existing.status == 'expired':
                    raise BusinessError("This voucher has expired")
                elif existing.expires_at <= datetime.utcnow():
                    raise BusinessError("This voucher code has expired")
                elif existing.usage_count >= existing.max_uses:
                    raise BusinessError("This voucher has reached its usage limit")
            raise BusinessError("Invalid voucher code")

        # Verify subscriber
        subscriber = self.subscriber_repo.get_by_id(
            subscriber_id, organization_id
        )
        if not subscriber:
            raise NotFoundError("Subscriber not found")

        # Handle first-use activation
        if voucher.activation_type == 'first_use' and not voucher.activated_at:
            self.voucher_repo.activate_voucher(voucher.id, datetime.utcnow())
            # Reload voucher to get updated state
            voucher = self.voucher_repo.get_by_id(voucher.id, organization_id)

        # Calculate subscription expiry from voucher
        subscription_expiry = self._calculate_subscription_expiry_from_voucher(
            voucher
        )

        plan = voucher.plan

        # Deactivate old subscriptions
        old_sub = self.subscription_repo.get_active_by_subscriber(
            subscriber_id, organization_id
        )
        if old_sub:
            self.subscription_repo.update_status(
                old_sub.id, organization_id, 'expired', 'replaced_by_voucher'
            )

        # Create subscription
        subscription_data = {
            'organization_id': organization_id,
            'subscriber_id': subscriber_id,
            'plan_id': plan.id,
            'status': 'active',
            'start_time': datetime.utcnow(),
            'expiry_time': subscription_expiry,
            'auto_renew': False,
            'device_limit': plan.device_limit,
            'bandwidth_up_mbps': plan.bandwidth_up_mbps,
            'bandwidth_down_mbps': plan.bandwidth_down_mbps,
        }

        subscription = self.subscription_repo.create(subscription_data)

        # Mark voucher as used
        self.voucher_repo.use_voucher(voucher.id, subscriber_id, router_id)

        # Update batch status if applicable
        if voucher.batch_id:
            self._update_batch_status(voucher.batch_id, organization_id)

        # Sync to RADIUS
        try:
            if subscriber.subscriber_type == 'hotspot':
                self.radius_sync_service.sync_hotspot_user_to_radius(
                    subscriber, subscription, plan
                )
            else:
                password = None
                if subscriber.password_encrypted:
                    from app.core.security.encryption import EncryptionService
                    password = EncryptionService().decrypt(
                        subscriber.password_encrypted
                    )
                self.radius_sync_service.sync_pppoe_user_to_radius(
                    subscriber, password, subscription, plan
                )
        except Exception as e:
            logger.warning(f"RADIUS sync on voucher redeem failed: {e}")

        # Register device MAC if provided
        if device_mac:
            try:
                self.radius_sync_service.sync_device_mac_to_radius(
                    subscriber, device_mac, subscription
                )
            except Exception as e:
                logger.warning(f"Device MAC sync failed: {e}")

        # Invalidate RADIUS cache
        username = subscriber.phone or subscriber.username
        if username:
            self.radius_cache.delete_auth_data(
                username, str(organization_id)
            )

        logger.info(
            f"Redeemed voucher {voucher.code} | subscriber={subscriber_id} | "
            f"plan={plan.name} | expires={subscription_expiry.isoformat()} | "
            f"org={organization_id}"
        )

        return {
            'success': True,
            'subscription_id': str(subscription.id),
            'plan_name': plan.name,
            'plan_type': plan.plan_type,
            'expiry_time': subscription_expiry.isoformat(),
            'bandwidth_up': subscription.get_bandwidth_up(),
            'bandwidth_down': subscription.get_bandwidth_down(),
            'device_limit': subscription.get_device_limit(),
            'voucher_code': voucher.code,
            'message': 'Voucher redeemed successfully',
        }

    def _update_batch_status(
        self,
        batch_id: UUID,
        organization_id: UUID,
    ) -> None:
        """Update batch status based on voucher usage."""
        try:
            vouchers = self.voucher_repo.get_by_batch(batch_id, organization_id)
            total = len(vouchers)
            used = sum(1 for v in vouchers if v.status == 'used')
            expired = sum(1 for v in vouchers if v.status == 'expired')

            if used + expired >= total:
                self.voucher_batch_repo.update_status(
                    batch_id, organization_id, 'exhausted'
                )
            elif used > 0:
                self.voucher_batch_repo.update_status(
                    batch_id, organization_id, 'partially_used'
                )
        except Exception as e:
            logger.warning(f"Failed to update batch status: {e}")

    def get_voucher_info(
        self,
        voucher_code: str,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Get voucher information WITHOUT redeeming it.

        Used on the captive portal to show what a voucher provides
        before the user commits to redeeming it.
        """
        normalized_code = voucher_code.upper().replace('-', '').replace(' ', '')
        voucher = self.voucher_repo.get_by_code(normalized_code, organization_id)

        if not voucher:
            raise NotFoundError("Voucher not found")

        plan = voucher.plan

        return {
            'code': voucher.code,
            'plan_name': plan.name if plan else None,
            'plan_id': str(voucher.plan_id),
            'is_valid': voucher.is_valid(),
            'status': voucher.status,
            'expires_at': voucher.expires_at.isoformat() if voucher.expires_at else None,
            'usage_count': voucher.usage_count,
            'max_uses': voucher.max_uses,
            'validity_display': voucher.validity_display,
            'activation_type': voucher.activation_type,
            'plan_details': {
                'bandwidth_up_mbps': plan.bandwidth_up_mbps if plan else 0,
                'bandwidth_down_mbps': plan.bandwidth_down_mbps if plan else 0,
                'device_limit': plan.device_limit if plan else 1,
                'validity_type': plan.validity_type if plan else None,
            } if plan else None,
        }

    def void_voucher(
        self,
        voucher_id: UUID,
        organization_id: UUID,
    ) -> bool:
        """
        Manually void a voucher (admin action).

        Voided vouchers cannot be redeemed.
        """
        voucher = self.voucher_repo.get_by_id(
            voucher_id, organization_id, include_all=True
        )
        if not voucher:
            raise NotFoundError("Voucher not found")

        if voucher.status == 'used':
            raise BusinessError("Cannot void a voucher that has already been used")

        voucher.status = 'void'
        db.session.commit()

        logger.info(
            f"Voucher {voucher.code} voided | org={organization_id}"
        )
        return True

    def get_voucher_batch(
        self,
        batch_id: UUID,
        organization_id: UUID,
    ) -> VoucherBatch:
        """Get voucher batch by ID."""
        batch = self.voucher_batch_repo.get_by_id(batch_id, organization_id)
        if not batch:
            raise NotFoundError("Voucher batch not found")
        return batch

    def get_batch_vouchers(
        self,
        batch_id: UUID,
        organization_id: UUID,
    ) -> List[Voucher]:
        """Get all vouchers in a batch."""
        return self.voucher_repo.get_by_batch(batch_id, organization_id)

    def validate_voucher(
        self,
        code: str,
        organization_id: UUID,
    ) -> Optional[Voucher]:
        """
        Validate a voucher code without redeeming.

        Returns the voucher if valid, None otherwise.
        Used by PaymentService for voucher payment method.
        """
        normalized = code.upper().replace('-', '').replace(' ', '')
        return self.voucher_repo.get_valid_by_code(normalized, organization_id)

    # DISCOUNT COUPONS

    def create_coupon(
        self,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> DiscountCoupon:
        """Create a discount coupon."""
        if data.get('discount_type') == 'percentage':
            if not 0 < float(data['discount_value']) <= 100:
                raise ValidationError("Percentage discount must be between 0 and 100")
        elif data.get('discount_type') == 'fixed':
            if float(data['discount_value']) <= 0:
                raise ValidationError("Fixed discount must be greater than 0")

        data['organization_id'] = organization_id
        coupon = self.discount_repo.create(data)
        logger.info(
            f"Created coupon {coupon.code} | type={coupon.discount_type} | "
            f"value={coupon.discount_value} | org={organization_id}"
        )
        return coupon

    def validate_coupon(
        self,
        coupon_code: str,
        organization_id: UUID,
        amount: float,
    ) -> Dict[str, Any]:
        """
        Validate a coupon code and calculate discount.

        Returns discount details or raises BusinessError if invalid.
        """
        coupon = self.discount_repo.get_valid_by_code(
            coupon_code, organization_id, amount
        )
        if not coupon:
            # Check why it failed
            existing = self.discount_repo.get_by_code(
                coupon_code, organization_id
            )
            if existing:
                if not existing.is_active:
                    raise BusinessError("This coupon is no longer active")
                now = datetime.utcnow()
                if existing.valid_from > now:
                    raise BusinessError("This coupon is not yet valid")
                if existing.valid_to < now:
                    raise BusinessError("This coupon has expired")
                if existing.usage_limit and existing.used_count >= existing.usage_limit:
                    raise BusinessError("This coupon has reached its usage limit")
                if amount < float(existing.minimum_purchase):
                    raise BusinessError(
                        f"Minimum purchase of {existing.minimum_purchase} required"
                    )
            raise BusinessError("Invalid coupon code")

        discount_amount = coupon.calculate_discount(amount)

        return {
            'valid': True,
            'code': coupon.code,
            'discount_type': coupon.discount_type,
            'discount_value': float(coupon.discount_value),
            'discount_amount': round(discount_amount, 2),
            'final_amount': round(amount - discount_amount, 2),
            'description': coupon.description,
        }

    def apply_coupon(
        self,
        coupon_code: str,
        organization_id: UUID,
    ) -> None:
        """Mark a coupon as used (increment usage count)."""
        coupon = self.discount_repo.get_by_code(coupon_code, organization_id)
        if coupon:
            self.discount_repo.increment_usage(coupon.id)

    def get_coupons(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> List[DiscountCoupon]:
        """Get all coupons for an organization."""
        return self.discount_repo.get_by_organization(
            organization_id, skip, limit
        )

    # SCHEDULED MAINTENANCE

    def run_expiry_checks(
        self,
        organization_id: UUID = None,
    ) -> Dict[str, Any]:
        """
        Run all expiry checks. Should be called by a scheduled job (Celery beat).

        Expires:
            1. Subscriptions past their expiry_time
            2. Vouchers past their expires_at
            3. Updates voucher batch statuses

        If organization_id is provided, only checks that org.
        Otherwise checks all organizations.

        Returns:
            Dict with counts of expired items
        """
        result = {
            'subscriptions_expired': 0,
            'vouchers_expired': 0,
            'errors': [],
        }

        # Expire subscriptions
        try:
            count = self.subscription_repo.expire_expired_subscriptions(
                organization_id
            )
            result['subscriptions_expired'] = count
            if count > 0:
                logger.info(f"Expired {count} subscriptions")

                # Remove expired subscribers from RADIUS
                self._remove_expired_from_radius(organization_id)
        except Exception as e:
            logger.error(f"Failed to expire subscriptions: {e}")
            result['errors'].append(f"Subscriptions: {str(e)}")

        # Expire vouchers
        try:
            count = self.voucher_repo.mark_expired_vouchers(organization_id)
            result['vouchers_expired'] = count
            if count > 0:
                logger.info(f"Marked {count} vouchers as expired")
        except Exception as e:
            logger.error(f"Failed to expire vouchers: {e}")
            result['errors'].append(f"Vouchers: {str(e)}")

        return result

    def _remove_expired_from_radius(
        self,
        organization_id: UUID = None,
    ) -> None:
        """
        Remove RADIUS entries for subscribers with no active subscription.

        Called after bulk expiry to ensure expired users can't connect.
        """
        try:
            from app.models.subscriber import Subscriber

            if organization_id:
                subscribers = Subscriber.query.filter_by(
                    organization_id=organization_id, status='active'
                ).all()
            else:
                subscribers = Subscriber.query.filter_by(status='active').all()

            for subscriber in subscribers:
                active_sub = self.subscription_repo.get_active_by_subscriber(
                    subscriber.id, subscriber.organization_id
                )
                if not active_sub:
                    try:
                        self.radius_sync_service.remove_subscriber_from_radius(
                            subscriber
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to clean up RADIUS for expired: {e}")

    def get_expiring_soon(
        self,
        organization_id: UUID,
        days: int = 3,
    ) -> List[Subscription]:
        """Get subscriptions expiring within N days."""
        return self.subscription_repo.get_expiring_soon(organization_id, days)

    def get_expiring_in_hours(
        self,
        organization_id: UUID,
        hours: int = 24,
    ) -> List[Subscription]:
        """Get subscriptions expiring within N hours (for short plans)."""
        return self.subscription_repo.get_expiring_in_hours(
            organization_id, hours
        )

    # INVOICE MANAGEMENT

    def generate_invoice(
        self,
        organization_id: UUID,
        subscriber_id: UUID,
        subscription_id: UUID = None,
        plan_id: UUID = None,
        invoice_type: str = 'subscription',
        notes: str = None,
    ) -> Invoice:
        """Generate an invoice for a subscription or purchase."""
        invoice_number = (
            f"INV-{datetime.utcnow().strftime('%Y%m%d')}-"
            f"{secrets.token_hex(4).upper()}"
        )

        plan = None
        amount = 0
        description = "Payment"

        if subscription_id:
            subscription = self.subscription_repo.get_by_id(
                subscription_id, organization_id
            )
            if subscription:
                plan = subscription.plan
                amount = float(plan.price) if plan else 0
                description = f"{plan.name} - {plan.billing_cycle} subscription"
        elif plan_id:
            plan = self.plan_repo.get_by_id(plan_id, organization_id)
            if plan:
                amount = float(plan.price)
                description = f"{plan.name} - {plan.billing_cycle}"

        invoice = Invoice(
            organization_id=organization_id,
            invoice_number=invoice_number,
            invoice_type=invoice_type,
            subscriber_id=subscriber_id,
            subscription_id=subscription_id,
            plan_id=plan_id,
            subtotal=amount,
            total=amount,
            issue_date=datetime.utcnow(),
            due_date=datetime.utcnow() + timedelta(days=7),
            status='draft',
            notes=notes,
            currency='KES',
        )

        db.session.add(invoice)
        db.session.flush()

        # Add line item
        item = InvoiceItem(
            invoice_id=invoice.id,
            description=description,
            quantity=1,
            unit_price=amount,
            total=amount,
        )
        db.session.add(item)
        db.session.commit()

        logger.info(
            f"Generated invoice {invoice_number} | "
            f"subscriber={subscriber_id} | amount={amount}"
        )
        return invoice

    # DASHBOARD STATS

    def get_billing_stats(
        self,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """Get billing dashboard statistics."""
        total_plans = self.plan_repo.count_by_organization(organization_id)
        active_plans = self.plan_repo.count_by_organization(
            organization_id, is_active=True
        )

        active_vouchers = self.voucher_repo.count_by_status(
            organization_id, 'active'
        )
        used_vouchers = self.voucher_repo.count_by_status(
            organization_id, 'used'
        )

        return {
            'plans': {
                'total': total_plans,
                'active': active_plans,
                'inactive': total_plans - active_plans,
            },
            'vouchers': {
                'active': active_vouchers,
                'used': used_vouchers,
            },
        }