from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID
from datetime import datetime, timedelta
import random
import string
import hashlib
import secrets

from app.modules.billing.repository import (
    PlanRepository, SubscriptionRepository, VoucherRepository,
    VoucherBatchRepository, DiscountCouponRepository
)
from app.models.billing import Plan, Subscription, Voucher, VoucherBatch, DiscountCoupon, Invoice, InvoiceItem
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError
from app.core.database.session import db

# Lazy imports for optional dependencies
class BillingService:
    """Complete billing service for plans, subscriptions, vouchers, and discounts"""
    
    def __init__(self):
        self.plan_repo = PlanRepository()
        self.subscription_repo = SubscriptionRepository()
        self.voucher_repo = VoucherRepository()
        self.voucher_batch_repo = VoucherBatchRepository()
        self.discount_repo = DiscountCouponRepository()
        
        # Lazy imports for optional dependencies
        self._subscriber_repo = None
        self._payment_service = None
        self._sms_service = None
        self._radius_cache = None
    
    @property
    def subscriber_repo(self):
        """Lazy load subscriber repository"""
        if self._subscriber_repo is None:
            from app.modules.subscriber.repository import SubscriberRepository
            self._subscriber_repo = SubscriberRepository()
        return self._subscriber_repo
    
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
    
    # ==========================================================================
    # Plan Management
    # ==========================================================================
    
    def create_plan(self, organization_id: UUID, data: Dict[str, Any]) -> Plan:
        """Create a new plan with dynamic validity support"""
        try:
            # Validate dynamic validity fields
            validity_type = data.get('validity_type')
            
            if validity_type == 'time_based':
                if not data.get('validity_value'):
                    raise ValidationError("Validity value is required for time-based plans")
                if not data.get('validity_unit'):
                    raise ValidationError("Validity unit is required for time-based plans")
            elif validity_type == 'data_based':
                if not data.get('data_limit_mb'):
                    raise ValidationError("Data limit is required for data-based plans")
            
            data['organization_id'] = organization_id
            plan = self.plan_repo.create(data)
            logger.info(f"Created plan {plan.name} for org {organization_id} (Validity: {plan.validity_display})")
            return plan
        except Exception as e:
            logger.error(f"Error creating plan: {e}", exc_info=True)
            raise BusinessError(f"Failed to create plan: {str(e)}")
    
    def get_plan(self, plan_id: UUID, organization_id: UUID) -> Plan:
        """Get plan by ID"""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        return plan
    
    def get_plans(self, organization_id: UUID, skip: int = 0, limit: int = 100, 
                  only_active: bool = True, plan_type: str = None) -> List[Plan]:
        """Get all plans for organization with optional type filter"""
        return self.plan_repo.get_by_organization(organization_id, skip, limit, only_active, plan_type)
    
    def get_public_plans(self, organization_id: UUID) -> List[Plan]:
        """Get public plans for hotspot portal"""
        return self.plan_repo.get_public_plans(organization_id)
    
    def update_plan(self, plan_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Plan:
        """Update a plan"""
        plan = self.plan_repo.update(plan_id, organization_id, data)
        if not plan:
            raise NotFoundError("Plan not found")
        logger.info(f"Updated plan {plan.name}")
        return plan
    
    def delete_plan(self, plan_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete a plan (soft delete by default)"""
        return self.plan_repo.delete(plan_id, organization_id, soft_delete)
    
    # ==========================================================================
    # Subscription Management
    # ==========================================================================
    
    def create_subscription(self, organization_id: UUID, subscriber_id: UUID, 
                            plan_id: UUID, auto_renew: bool = False) -> Subscription:
        """Create a new subscription for a subscriber using dynamic validity"""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        
        subscriber = self.subscriber_repo.get_by_id(subscriber_id, organization_id)
        if not subscriber:
            raise NotFoundError("Subscriber not found")
        
        # Deactivate old subscriptions
        old_sub = self.subscription_repo.get_active_by_subscriber(subscriber_id, organization_id)
        if old_sub:
            self.subscription_repo.update_status(old_sub.id, organization_id, 'expired', 'replaced_by_new')
        
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
        
        subscription = self.subscription_repo.create(subscription_data)
        
        # Update RADIUS cache (if available)
        try:
            self.radius_cache.set_auth_data(
                username=subscriber.phone,
                data={
                    'password': str(subscription.id),
                    'organization_id': str(organization_id),
                    'subscriber_id': str(subscriber_id),
                    'plan_name': plan.name,
                    'bandwidth_up': plan.bandwidth_up_mbps,
                    'bandwidth_down': plan.bandwidth_down_mbps,
                    'expiry': subscription.expiry_time.timestamp(),
                    'device_limit': plan.device_limit
                },
                ttl=3600
            )
        except Exception as e:
            logger.warning(f"Failed to update RADIUS cache: {e}")
        
        logger.info(f"Created subscription {subscription.id} for subscriber {subscriber_id} (expires: {expiry_time})")
        return subscription
    
    def get_subscription(self, subscription_id: UUID, organization_id: UUID) -> Subscription:
        """Get subscription by ID"""
        subscription = self.subscription_repo.get_by_id(subscription_id, organization_id)
        if not subscription:
            raise NotFoundError("Subscription not found")
        return subscription
    
    def get_active_subscription(self, subscriber_id: UUID, organization_id: UUID) -> Optional[Subscription]:
        """Get active subscription for a subscriber"""
        return self.subscription_repo.get_active_by_subscriber(subscriber_id, organization_id)
    
    def cancel_subscription(self, subscription_id: UUID, organization_id: UUID, reason: str = None) -> bool:
        """Cancel a subscription"""
        return self.subscription_repo.update_status(subscription_id, organization_id, 'cancelled', reason)
    
    def renew_subscription(self, subscription_id: UUID, organization_id: UUID) -> Subscription:
        """Renew an existing subscription"""
        subscription = self.subscription_repo.get_by_id(subscription_id, organization_id)
        if not subscription:
            raise NotFoundError("Subscription not found")
        
        plan = subscription.plan
        new_expiry = max(subscription.expiry_time, datetime.utcnow()) + plan.validity_timedelta
        subscription.expiry_time = new_expiry
        subscription.status = 'active'
        db.session.commit()
        
        logger.info(f"Renewed subscription {subscription_id} until {new_expiry}")
        return subscription
    
    # ==========================================================================
    # Voucher Management
    # ==========================================================================
    
    def generate_voucher_code(self) -> str:
        """Generate a unique voucher code"""
        chars = string.ascii_uppercase + string.digits
        # Remove confusing characters
        chars = chars.replace('0', '').replace('O', '').replace('1', '').replace('I', '')
        code = ''.join(random.choices(chars, k=12))
        # Format as XXXX-XXXX-XXXX
        return f"{code[:4]}-{code[4:8]}-{code[8:12]}"
    
    def generate_voucher_password(self, code: str) -> str:
        """Generate a password for voucher"""
        return hashlib.md5(code.replace('-', '').encode()).hexdigest()[:8].upper()
    
    def create_voucher(self, organization_id: UUID, plan_id: UUID, 
                       max_uses: int = 1,
                       validity_value: int = None, validity_unit: str = None,
                       activation_type: str = 'immediate',
                       custom_expires_at: datetime = None,
                       created_by: UUID = None) -> Voucher:
        """Create a single voucher with dynamic validity"""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        
        code = self.generate_voucher_code()
        
        # Calculate expiry
        if custom_expires_at:
            expires_at = custom_expires_at
        elif validity_value and validity_unit:
            # Override plan validity
            now = datetime.utcnow()
            unit = validity_unit
            value = validity_value
            
            if unit == 'minutes':
                expires_at = now + timedelta(minutes=value)
            elif unit == 'hours':
                expires_at = now + timedelta(hours=value)
            elif unit == 'days':
                expires_at = now + timedelta(days=value)
            elif unit == 'months':
                expires_at = now + timedelta(days=value * 30)
            elif unit == 'years':
                expires_at = now + timedelta(days=value * 365)
            else:
                expires_at = plan.calculate_expiry()
        else:
            expires_at = plan.calculate_expiry()
        
        voucher_data = {
            'organization_id': organization_id,
            'plan_id': plan_id,
            'code': code,
            'password_hash': self.generate_voucher_password(code),
            'max_uses': max_uses,
            'expires_at': expires_at,
            'validity_value': validity_value,
            'validity_unit': validity_unit,
            'activation_type': activation_type,
            'price_paid': plan.price,
            'created_by': created_by,
            'status': 'active'
        }
        
        voucher = self.voucher_repo.create(voucher_data)
        logger.info(f"Created voucher {code} for plan {plan.name} (validity: {validity_value} {validity_unit if validity_unit else 'plan default'})")
        return voucher
    
    def create_voucher_batch(self, organization_id: UUID, plan_id: UUID, 
                              batch_name: str, quantity: int,
                              validity_value: int = None, validity_unit: str = None,
                              expires_in_days: int = None,
                              created_by: UUID = None) -> VoucherBatch:
        """Create a batch of vouchers with dynamic validity"""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        
        if quantity > 1000:
            raise ValidationError("Maximum batch size is 1000 vouchers")
        
        # Determine expiry for vouchers
        if expires_in_days:
            expiry_delta = timedelta(days=expires_in_days)
        elif validity_value and validity_unit:
            if validity_unit == 'minutes':
                expiry_delta = timedelta(minutes=validity_value)
            elif validity_unit == 'hours':
                expiry_delta = timedelta(hours=validity_value)
            elif validity_unit == 'days':
                expiry_delta = timedelta(days=validity_value)
            elif validity_unit == 'months':
                expiry_delta = timedelta(days=validity_value * 30)
            elif validity_unit == 'years':
                expiry_delta = timedelta(days=validity_value * 365)
            else:
                expiry_delta = plan.validity_timedelta
        else:
            expiry_delta = plan.validity_timedelta
        
        # Create batch record
        batch_data = {
            'organization_id': organization_id,
            'plan_id': plan_id,
            'batch_name': batch_name,
            'quantity': quantity,
            'price_per_voucher': plan.price,
            'total_amount': float(plan.price) * quantity,
            'validity_value': validity_value,
            'validity_unit': validity_unit,
            'expires_in_days': expires_in_days or (expiry_delta.days if expiry_delta.days > 0 else 30),
            'created_by': created_by,
            'status': 'generated',
            'generated_at': datetime.utcnow()
        }
        
        batch = self.voucher_batch_repo.create(batch_data)
        
        # Generate vouchers
        vouchers_data = []
        for _ in range(quantity):
            code = self.generate_voucher_code()
            vouchers_data.append({
                'organization_id': organization_id,
                'plan_id': plan_id,
                'batch_id': batch.id,
                'code': code,
                'password_hash': self.generate_voucher_password(code),
                'max_uses': 1,
                'expires_at': datetime.utcnow() + expiry_delta,
                'validity_value': validity_value,
                'validity_unit': validity_unit,
                'price_paid': plan.price,
                'created_by': created_by,
                'status': 'active'
            })
        
        self.voucher_repo.create_batch(vouchers_data)
        
        logger.info(f"Created voucher batch {batch_name} with {quantity} vouchers (validity: {validity_value} {validity_unit if validity_unit else plan.validity_display})")
        return batch
    
    def redeem_voucher(self, organization_id: UUID, voucher_code: str, 
                       subscriber_id: UUID, router_id: UUID = None) -> Dict[str, Any]:
        """Redeem a voucher for a subscriber"""
        # Normalize code
        voucher_code = voucher_code.upper().replace('-', '')
        
        voucher = self.voucher_repo.get_valid_by_code(voucher_code, organization_id)
        if not voucher:
            raise BusinessError("Invalid or expired voucher code")
        
        subscriber = self.subscriber_repo.get_by_id(subscriber_id, organization_id)
        if not subscriber:
            raise NotFoundError("Subscriber not found")
        
        # Activate voucher if needed (first-use activation)
        if voucher.activation_type == 'first_use' and not voucher.activated_at:
            self.voucher_repo.activate_voucher(voucher.id, datetime.utcnow())
            voucher = self.voucher_repo.get_by_id(voucher.id, organization_id)
        
        # Create subscription from voucher
        plan = voucher.plan
        
        # Calculate expiry based on voucher's validity
        if voucher.validity_value and voucher.validity_unit:
            # Use voucher's custom validity
            if voucher.activated_at:
                expiry_time = voucher.calculate_expiry_from_activation(voucher.activated_at)
            else:
                expiry_time = voucher.calculate_expiry_from_activation()
        else:
            expiry_time = voucher.expires_at
        
        # Create subscription
        subscription_data = {
            'organization_id': organization_id,
            'subscriber_id': subscriber_id,
            'plan_id': plan.id,
            'status': 'active',
            'start_time': datetime.utcnow(),
            'expiry_time': expiry_time,
            'auto_renew': False,
            'device_limit': plan.device_limit,
            'bandwidth_up_mbps': plan.bandwidth_up_mbps,
            'bandwidth_down_mbps': plan.bandwidth_down_mbps
        }
        
        subscription = self.subscription_repo.create(subscription_data)
        
        # Mark voucher as used
        self.voucher_repo.use_voucher(voucher.id, subscriber_id, router_id)
        
        logger.info(f"Redeemed voucher {voucher_code} for subscriber {subscriber_id} (expires: {expiry_time})")
        
        return {
            'success': True,
            'subscription_id': str(subscription.id),
            'plan_name': plan.name,
            'expiry_time': expiry_time.isoformat(),
            'message': 'Voucher redeemed successfully'
        }
    
    def get_voucher_info(self, voucher_code: str, organization_id: UUID) -> Dict[str, Any]:
        """Get voucher information without redeeming"""
        voucher_code = voucher_code.upper().replace('-', '')
        voucher = self.voucher_repo.get_by_code(voucher_code, organization_id)
        if not voucher:
            raise NotFoundError("Voucher not found")
        
        return {
            'code': voucher.code,
            'plan_name': voucher.plan.name,
            'plan_id': str(voucher.plan_id),
            'is_valid': voucher.is_valid(),
            'expires_at': voucher.expires_at.isoformat() if voucher.expires_at else None,
            'usage_count': voucher.usage_count,
            'max_uses': voucher.max_uses,
            'validity_display': voucher.validity_display,
            'activation_type': voucher.activation_type
        }
    
    def get_voucher_batch(self, batch_id: UUID, organization_id: UUID) -> VoucherBatch:
        """Get voucher batch by ID"""
        batch = self.voucher_batch_repo.get_by_id(batch_id, organization_id)
        if not batch:
            raise NotFoundError("Voucher batch not found")
        return batch
    
    # ==========================================================================
    # Discount Coupons
    # ==========================================================================
    
    def create_coupon(self, organization_id: UUID, data: Dict[str, Any]) -> DiscountCoupon:
        """Create a discount coupon"""
        data['organization_id'] = organization_id
        coupon = self.discount_repo.create(data)
        logger.info(f"Created coupon {coupon.code} for org {organization_id}")
        return coupon
    
    def validate_coupon(self, coupon_code: str, organization_id: UUID, amount: float) -> Dict[str, Any]:
        """Validate a coupon code"""
        coupon = self.discount_repo.get_valid_by_code(coupon_code, organization_id, amount)
        if not coupon:
            raise BusinessError("Invalid or expired coupon code")
        
        discount_amount = coupon.calculate_discount(amount)
        
        return {
            'valid': True,
            'code': coupon.code,
            'discount_type': coupon.discount_type,
            'discount_value': float(coupon.discount_value),
            'discount_amount': discount_amount,
            'final_amount': amount - discount_amount,
            'description': coupon.description
        }
    
    def get_coupons(self, organization_id: UUID, skip: int = 0, limit: int = 50) -> List[DiscountCoupon]:
        """Get all coupons for an organization"""
        return self.discount_repo.get_by_organization(organization_id, skip, limit)
    
    # ==========================================================================
    # Invoice Management
    # ==========================================================================
    
    def generate_invoice_for_subscription(self, subscription_id: UUID, organization_id: UUID) -> Invoice:
        """Generate an invoice for a subscription"""
        subscription = self.subscription_repo.get_by_id(subscription_id, organization_id)
        if not subscription:
            raise NotFoundError("Subscription not found")
        
        # Generate invoice number
        invoice_number = f"INV-{datetime.utcnow().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"
        
        invoice = Invoice(
            organization_id=organization_id,
            invoice_number=invoice_number,
            invoice_type='subscription',
            subscriber_id=subscription.subscriber_id,
            subscription_id=subscription.id,
            plan_id=subscription.plan_id,
            subtotal=subscription.plan.price,
            total=subscription.plan.price,
            issue_date=datetime.utcnow(),
            due_date=datetime.utcnow() + timedelta(days=7),
            status='draft',
            billing_period_start=subscription.start_time,
            billing_period_end=subscription.expiry_time,
            currency='KES'
        )
        
        db.session.add(invoice)
        db.session.flush()
        
        # Add invoice item
        item = InvoiceItem(
            invoice_id=invoice.id,
            description=f"{subscription.plan.name} - {subscription.plan.billing_cycle} subscription",
            quantity=1,
            unit_price=subscription.plan.price,
            total=subscription.plan.price
        )
        db.session.add(item)
        db.session.commit()
        
        logger.info(f"Generated invoice {invoice_number} for subscription {subscription_id}")
        return invoice