from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID
from datetime import datetime, timedelta
import random
import string
import hashlib

from app.modules.billing.repository import (
    PlanRepository, SubscriptionRepository, VoucherRepository,
    VoucherBatchRepository, DiscountCouponRepository
)
from app.models.billing import Plan, Subscription, Voucher, VoucherBatch,  DiscountCoupon, Invoice, InvoiceItem

from app.modules.payment.service import PaymentService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError
from app.core.database.session import db
from app.integrations.sms.provider import SMSService
from app.integrations.radius.redius_cache import RadiusCache
from app.modules.subscriber.repository import SubscriberRepository

class BillingService:
    """Complete billing service for plans, subscriptions, vouchers, and discounts"""
    
    def __init__(self):
        self.plan_repo = PlanRepository()
        self.subscription_repo = SubscriptionRepository()
        self.voucher_repo = VoucherRepository()
        self.voucher_batch_repo = VoucherBatchRepository()
        self.discount_repo = DiscountCouponRepository()
        self.subscriber_repo = SubscriberRepository()
        self.payment_service = PaymentService()
        self.sms_service = SMSService()
        self.radius_cache = RadiusCache()
    
    # Plan Management     
    def create_plan(self, organization_id: UUID, data: Dict[str, Any]) -> Plan:
        """Create a new plan"""
        try:
            data['organization_id'] = organization_id
            plan = self.plan_repo.create(data)
            logger.info(f"Created plan {plan.name} for org {organization_id}")
            return plan
        except Exception as e:
            logger.error(f"Error creating plan: {e}", exc_info=True)
            raise BusinessError(f"Failed to create plan: {str(e)}")
    
    def get_plan(self, plan_id: UUID, organization_id: UUID) -> Optional[Plan]:
        """Get plan by ID"""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        return plan
    
    def get_plans(self, organization_id: UUID, skip: int = 0, limit: int = 100, 
                  only_active: bool = True) -> List[Plan]:
        """Get all plans for organization"""
        return self.plan_repo.get_by_organization(organization_id, skip, limit, only_active)
    
    def update_plan(self, plan_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Plan:
        """Update a plan"""
        plan = self.plan_repo.update(plan_id, organization_id, data)
        if not plan:
            raise NotFoundError("Plan not found")
        logger.info(f"Updated plan {plan.name}")
        return plan
    
    def delete_plan(self, plan_id: UUID, organization_id: UUID) -> bool:
        """Delete a plan (soft delete by setting is_active=False)"""
        return self.plan_repo.update(plan_id, organization_id, {'is_active': False}) is not None
    
    # Subscription Management     
    def create_subscription(self, organization_id: UUID, subscriber_id: UUID, 
                            plan_id: UUID, auto_renew: bool = False) -> Subscription:
        """Create a new subscription for a subscriber"""
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
        
        # Create new subscription
        subscription_data = {
            'organization_id': organization_id,
            'subscriber_id': subscriber_id,
            'plan_id': plan_id,
            'status': 'active',
            'start_time': datetime.utcnow(),
            'expiry_time': datetime.utcnow() + timedelta(days=plan.validity_days),
            'auto_renew': auto_renew,
            'device_limit': plan.device_limit,
            'bandwidth_up_mbps': plan.bandwidth_up_mbps,
            'bandwidth_down_mbps': plan.bandwidth_down_mbps
        }
        
        subscription = self.subscription_repo.create(subscription_data)
        
        # Update RADIUS cache
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
        
        logger.info(f"Created subscription {subscription.id} for subscriber {subscriber_id}")
        return subscription
    
    def get_subscription(self, subscription_id: UUID, organization_id: UUID) -> Optional[Subscription]:
        """Get subscription by ID"""
        subscription = self.subscription_repo.get_by_id(subscription_id, organization_id)
        if not subscription:
            raise NotFoundError("Subscription not found")
        return subscription
    
    def cancel_subscription(self, subscription_id: UUID, organization_id: UUID, reason: str = None) -> bool:
        """Cancel a subscription"""
        return self.subscription_repo.update_status(subscription_id, organization_id, 'cancelled', reason)
    
    def renew_subscription(self, subscription_id: UUID, organization_id: UUID) -> Subscription:
        """Renew an existing subscription"""
        subscription = self.subscription_repo.get_by_id(subscription_id, organization_id)
        if not subscription:
            raise NotFoundError("Subscription not found")
        
        plan = subscription.plan
        new_expiry = max(subscription.expiry_time, datetime.utcnow()) + timedelta(days=plan.validity_days)
        subscription.expiry_time = new_expiry
        subscription.status = 'active'
        db.session.commit()
        
        logger.info(f"Renewed subscription {subscription_id} until {new_expiry}")
        return subscription
    
    # Voucher Management     
    def generate_voucher_code(self) -> str:
        """Generate a unique voucher code"""
        chars = string.ascii_uppercase + string.digits
        code = ''.join(random.choices(chars, k=12))
        return code
    
    def generate_voucher_password(self, code: str) -> str:
        """Generate a password for voucher"""
        return hashlib.md5(code.encode()).hexdigest()[:8].upper()
    
    def create_voucher(self, organization_id: UUID, plan_id: UUID, 
                       max_uses: int = 1, expires_in_days: int = 30,
                       created_by: UUID = None) -> Voucher:
        """Create a single voucher"""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        
        code = self.generate_voucher_code()
        
        voucher_data = {
            'organization_id': organization_id,
            'plan_id': plan_id,
            'code': code,
            'password_hash': self.generate_voucher_password(code),
            'max_uses': max_uses,
            'expires_at': datetime.utcnow() + timedelta(days=expires_in_days),
            'price_paid': plan.price,
            'created_by': created_by,
            'status': 'active'
        }
        
        voucher = self.voucher_repo.create(voucher_data)
        logger.info(f"Created voucher {code} for plan {plan.name}")
        return voucher
    
    def create_voucher_batch(self, organization_id: UUID, plan_id: UUID, 
                              batch_name: str, quantity: int, 
                              expires_in_days: int = 30,
                              created_by: UUID = None) -> VoucherBatch:
        """Create a batch of vouchers"""
        plan = self.plan_repo.get_by_id(plan_id, organization_id)
        if not plan:
            raise NotFoundError("Plan not found")
        
        # Create batch record
        batch_data = {
            'organization_id': organization_id,
            'plan_id': plan_id,
            'batch_name': batch_name,
            'quantity': quantity,
            'price_per_voucher': plan.price,
            'total_amount': plan.price * quantity,
            'expires_in_days': expires_in_days,
            'created_by': created_by,
            'status': 'generated'
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
                'expires_at': datetime.utcnow() + timedelta(days=expires_in_days),
                'price_paid': plan.price,
                'created_by': created_by,
                'status': 'active'
            })
        
        self.voucher_repo.create_batch(vouchers_data)
        
        logger.info(f"Created voucher batch {batch_name} with {quantity} vouchers")
        return batch
    
    def redeem_voucher(self, organization_id: UUID, voucher_code: str, 
                       subscriber_id: UUID, device_mac: str) -> Dict[str, Any]:
        """Redeem a voucher for a subscriber"""
        voucher = self.voucher_repo.get_valid_by_code(voucher_code, organization_id)
        if not voucher:
            raise BusinessError("Invalid or expired voucher code")
        
        subscriber = self.subscriber_repo.get_by_id(subscriber_id, organization_id)
        if not subscriber:
            raise NotFoundError("Subscriber not found")
        
        # Check if subscriber already has an active session with this voucher
        from app.modules.session.models import ActiveSession
        existing_session = ActiveSession.query.filter(
            ActiveSession.voucher_id == voucher.id,
            ActiveSession.status == 'active'
        ).first()
        
        if existing_session:
            return {
                'success': True,
                'already_connected': True,
                'session_id': str(existing_session.id),
                'message': 'Voucher already active on this device'
            }
        
        # Create subscription from voucher
        plan = voucher.plan
        
        # Create subscription
        subscription_data = {
            'organization_id': organization_id,
            'subscriber_id': subscriber_id,
            'plan_id': plan.id,
            'status': 'active',
            'start_time': datetime.utcnow(),
            'expiry_time': voucher.expires_at,
            'auto_renew': False,
            'device_limit': plan.device_limit,
            'bandwidth_up_mbps': plan.bandwidth_up_mbps,
            'bandwidth_down_mbps': plan.bandwidth_down_mbps
        }
        
        subscription = self.subscription_repo.create(subscription_data)
        
        # Mark voucher as used
        self.voucher_repo.use_voucher(voucher.id, subscriber_id, None)
        
        # Update RADIUS cache
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
        
        logger.info(f"Redeemed voucher {voucher_code} for subscriber {subscriber_id}")
        
        return {
            'success': True,
            'subscription_id': str(subscription.id),
            'plan_name': plan.name,
            'expiry_time': subscription.expiry_time.isoformat(),
            'message': 'Voucher redeemed successfully'
        }
    
    def get_voucher_info(self, voucher_code: str, organization_id: UUID) -> Dict[str, Any]:
        """Get voucher information without redeeming"""
        voucher = self.voucher_repo.get_by_code(voucher_code, organization_id)
        if not voucher:
            raise NotFoundError("Voucher not found")
        
        return {
            'code': voucher.code,
            'plan_name': voucher.plan.name,
            'plan_id': str(voucher.plan_id),
            'is_valid': voucher.is_valid(),
            'expires_at': voucher.expires_at.isoformat(),
            'usage_count': voucher.usage_count,
            'max_uses': voucher.max_uses
        }
    
    # Discount Coupons     
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
    
    # Invoice Management     
    def generate_invoice_for_subscription(self, subscription_id: UUID, organization_id: UUID) -> Invoice:
        """Generate an invoice for a subscription"""
        from app.modules.billing.models import Invoice, InvoiceItem
        import secrets
        
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
            billing_period_end=subscription.expiry_time
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