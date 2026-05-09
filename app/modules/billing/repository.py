from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, or_, desc, func, between
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta

from app.models.billing import Plan, Subscription, Invoice, InvoiceItem, Voucher, VoucherBatch, DiscountCoupon
from app.core.database.session import db
from app.core.logging.logger import logger


class PlanRepository:
    """Repository for Plan operations with dynamic validity support"""
    
    def __init__(self):
        self.model = Plan
    
    def get_by_id(self, plan_id: UUID, organization_id: UUID, include_inactive: bool = False) -> Optional[Plan]:
        """Get plan by ID with organization isolation"""
        try:
            filters = [
                self.model.id == plan_id,
                self.model.organization_id == organization_id
            ]
            if not include_inactive:
                filters.append(self.model.is_active == True)
            
            return self.model.query.filter(and_(*filters)).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, 
                            limit: int = 100, only_active: bool = True,
                            plan_type: str = None) -> List[Plan]:
        """Get all plans for an organization with filters"""
        try:
            filters = [self.model.organization_id == organization_id]
            if only_active:
                filters.append(self.model.is_active == True)
            if plan_type:
                filters.append(self.model.plan_type == plan_type)
            
            return self.model.query.filter(
                and_(*filters)
            ).order_by(self.model.sort_order, self.model.created_at).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def get_by_plan_type(self, organization_id: UUID, plan_type: str) -> List[Plan]:
        """Get plans by type (hotspot, pppoe, both)"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.plan_type == plan_type,
                    self.model.is_active == True
                )
            ).order_by(self.model.price).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_plan_type: {e}", exc_info=True)
            raise
    
    def get_public_plans(self, organization_id: UUID) -> List[Plan]:
        """Get public-facing plans (for hotspot portal)"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.is_public == True
                )
            ).order_by(self.model.price).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_public_plans: {e}", exc_info=True)
            raise
    
    def get_time_based_plans(self, organization_id: UUID) -> List[Plan]:
        """Get time-based plans (for voucher generation)"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.validity_type == 'time_based'
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_time_based_plans: {e}", exc_info=True)
            raise
    
    def get_data_based_plans(self, organization_id: UUID) -> List[Plan]:
        """Get data-based plans"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.validity_type == 'data_based'
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_data_based_plans: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Plan:
        """Create a new plan"""
        try:
            plan = self.model(**data)
            db.session.add(plan)
            db.session.commit()
            logger.info(f"Created plan: {plan.name} (Type: {plan.plan_type}, Validity: {plan.validity_display})")
            return plan
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, plan_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Plan]:
        """Update a plan"""
        try:
            plan = self.get_by_id(plan_id, organization_id, include_inactive=True)
            if not plan:
                return None
            
            for key, value in data.items():
                if hasattr(plan, key) and value is not None:
                    setattr(plan, key, value)
            
            db.session.commit()
            logger.info(f"Updated plan: {plan.name}")
            return plan
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def delete(self, plan_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate a plan"""
        try:
            plan = self.get_by_id(plan_id, organization_id, include_inactive=True)
            if not plan:
                return False
            
            # Check if plan has active subscriptions
            if plan.subscriptions and plan.subscriptions.filter(Subscription.status == 'active').count() > 0:
                if soft_delete:
                    plan.is_active = False
                    logger.warning(f"Plan {plan_id} deactivated due to active subscriptions")
                else:
                    logger.warning(f"Cannot delete plan {plan_id} with active subscriptions")
                    return False
            
            if soft_delete:
                plan.is_active = False
            else:
                db.session.delete(plan)
            
            db.session.commit()
            logger.info(f"Plan {plan_id} {'deactivated' if soft_delete else 'deleted'}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise
    
    def count_by_organization(self, organization_id: UUID, is_active: bool = None) -> int:
        """Count plans in organization"""
        try:
            filters = [self.model.organization_id == organization_id]
            if is_active is not None:
                filters.append(self.model.is_active == is_active)
            return self.model.query.filter(and_(*filters)).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_organization: {e}", exc_info=True)
            raise


class SubscriptionRepository:
    """Repository for Subscription operations with tenant isolation"""
    
    def __init__(self):
        self.model = Subscription
    
    def get_by_id(self, subscription_id: UUID, organization_id: UUID, include_inactive: bool = False) -> Optional[Subscription]:
        """Get subscription by ID with organization isolation"""
        try:
            filters = [
                self.model.id == subscription_id,
                self.model.organization_id == organization_id
            ]
            if not include_inactive:
                filters.append(self.model.status == 'active')
            
            return self.model.query.filter(and_(*filters)).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_active_by_subscriber(self, subscriber_id: UUID, organization_id: UUID) -> Optional[Subscription]:
        """Get active subscription for a subscriber"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.subscriber_id == subscriber_id,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expiry_time > datetime.utcnow()
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_by_subscriber: {e}", exc_info=True)
            raise
    
    def get_by_subscriber(self, subscriber_id: UUID, organization_id: UUID, 
                          skip: int = 0, limit: int = 50) -> List[Subscription]:
        """Get all subscriptions for a subscriber (history)"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.subscriber_id == subscriber_id,
                    self.model.organization_id == organization_id
                )
            ).order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_subscriber: {e}", exc_info=True)
            raise
    
    def get_by_plan(self, plan_id: UUID, organization_id: UUID, 
                    skip: int = 0, limit: int = 100) -> List[Subscription]:
        """Get all subscriptions for a plan"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.plan_id == plan_id,
                    self.model.organization_id == organization_id
                )
            ).order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_plan: {e}", exc_info=True)
            raise
    
    def get_expiring_soon(self, organization_id: UUID, days: int = 3) -> List[Subscription]:
        """Get subscriptions expiring within specified days"""
        try:
            now = datetime.utcnow()
            expiry_threshold = now + timedelta(days=days)
            
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expiry_time <= expiry_threshold,
                    self.model.expiry_time > now
                )
            ).order_by(self.model.expiry_time).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_expiring_soon: {e}", exc_info=True)
            raise
    
    def get_expiring_in_hours(self, organization_id: UUID, hours: int = 24) -> List[Subscription]:
        """Get subscriptions expiring within specified hours (for minute/hour plans)"""
        try:
            now = datetime.utcnow()
            expiry_threshold = now + timedelta(hours=hours)
            
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expiry_time <= expiry_threshold,
                    self.model.expiry_time > now
                )
            ).order_by(self.model.expiry_time).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_expiring_in_hours: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Subscription:
        """Create a new subscription"""
        try:
            subscription = self.model(**data)
            db.session.add(subscription)
            db.session.commit()
            logger.info(f"Created subscription for subscriber {subscription.subscriber_id}")
            return subscription
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, subscription_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Subscription]:
        """Update a subscription"""
        try:
            subscription = self.get_by_id(subscription_id, organization_id, include_inactive=True)
            if not subscription:
                return None
            
            for key, value in data.items():
                if hasattr(subscription, key) and value is not None:
                    setattr(subscription, key, value)
            
            db.session.commit()
            logger.info(f"Updated subscription: {subscription_id}")
            return subscription
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def update_status(self, subscription_id: UUID, organization_id: UUID, 
                      status: str, reason: str = None) -> bool:
        """Update subscription status"""
        try:
            subscription = self.get_by_id(subscription_id, organization_id, include_inactive=True)
            if not subscription:
                return False
            
            subscription.status = status
            if reason:
                subscription.cancellation_reason = reason
            if status in ['cancelled', 'expired']:
                subscription.cancelled_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"Subscription {subscription_id} status updated to {status}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_status: {e}", exc_info=True)
            raise
    
    def renew_subscription(self, subscription_id: UUID, organization_id: UUID, 
                           new_expiry: datetime) -> Optional[Subscription]:
        """Renew an existing subscription"""
        try:
            subscription = self.get_by_id(subscription_id, organization_id, include_inactive=True)
            if not subscription:
                return None
            
            subscription.expiry_time = new_expiry
            subscription.status = 'active'
            subscription.cancelled_at = None
            subscription.cancellation_reason = None
            
            db.session.commit()
            logger.info(f"Subscription {subscription_id} renewed until {new_expiry}")
            return subscription
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in renew_subscription: {e}", exc_info=True)
            raise
    
    def expire_expired_subscriptions(self, organization_id: UUID = None) -> int:
        """Expire all subscriptions that have passed expiry date"""
        try:
            query = self.model.query.filter(
                and_(
                    self.model.status == 'active',
                    self.model.expiry_time <= datetime.utcnow()
                )
            )
            if organization_id:
                query = query.filter(self.model.organization_id == organization_id)
            
            count = query.update({'status': 'expired'}, synchronize_session=False)
            db.session.commit()
            
            if count > 0:
                logger.info(f"Expired {count} subscriptions")
            return count
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in expire_expired_subscriptions: {e}", exc_info=True)
            raise
    
    def count_by_subscriber(self, subscriber_id: UUID, organization_id: UUID) -> int:
        """Count subscriptions for a subscriber"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.subscriber_id == subscriber_id,
                    self.model.organization_id == organization_id
                )
            ).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_subscriber: {e}", exc_info=True)
            raise

class VoucherRepository:
    """Repository for Voucher operations with dynamic validity support"""
    
    def __init__(self):
        self.model = Voucher
    
    def get_by_id(self, voucher_id: UUID, organization_id: UUID, include_all: bool = False) -> Optional[Voucher]:
        """Get voucher by ID with organization isolation"""
        try:
            filters = [
                self.model.id == voucher_id,
                self.model.organization_id == organization_id
            ]
            if not include_all:
                filters.append(self.model.status == 'active')
            
            return self.model.query.filter(and_(*filters)).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_code(self, code: str, organization_id: UUID) -> Optional[Voucher]:
        """Get voucher by code"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.code == code,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_code: {e}", exc_info=True)
            raise
    
    def get_valid_by_code(self, code: str, organization_id: UUID) -> Optional[Voucher]:
        """Get valid (unused, not expired) voucher by code"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.code == code,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expires_at > datetime.utcnow(),
                    self.model.usage_count < self.model.max_uses
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_valid_by_code: {e}", exc_info=True)
            raise
    
    def get_by_batch(self, batch_id: UUID, organization_id: UUID) -> List[Voucher]:
        """Get all vouchers in a batch"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.batch_id == batch_id,
                    self.model.organization_id == organization_id
                )
            ).order_by(self.model.code).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_batch: {e}", exc_info=True)
            raise
    
    def get_single_vouchers(self, organization_id: UUID) -> List[Voucher]:
        """Get vouchers not belonging to any batch"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.batch_id.is_(None)
                )
            ).order_by(desc(self.model.created_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_single_vouchers: {e}", exc_info=True)
            raise
    
    def get_by_subscriber(self, subscriber_id: UUID, organization_id: UUID) -> List[Voucher]:
        """Get all vouchers used by a subscriber"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.used_by_subscriber_id == subscriber_id,
                    self.model.organization_id == organization_id
                )
            ).order_by(desc(self.model.used_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_subscriber: {e}", exc_info=True)
            raise
    
    def get_unused_by_plan(self, plan_id: UUID, organization_id: UUID) -> List[Voucher]:
        """Get unused vouchers for a specific plan"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.plan_id == plan_id,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.used_by_subscriber_id.is_(None)
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_unused_by_plan: {e}", exc_info=True)
            raise
    
    def get_by_status(self, organization_id: UUID, status: str) -> List[Voucher]:
        """Get vouchers by status"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == status
                )
            ).order_by(desc(self.model.created_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_status: {e}", exc_info=True)
            raise
    
    def count_by_status(self, organization_id: UUID, status: str) -> int:
        """Count vouchers by status"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == status
                )
            ).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_status: {e}", exc_info=True)
            raise
    
    def get_expired_vouchers(self, organization_id: UUID) -> List[Voucher]:
        """Get all expired vouchers"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expires_at < datetime.utcnow()
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_expired_vouchers: {e}", exc_info=True)
            raise
    
    def mark_expired_vouchers(self, organization_id: UUID) -> int:
        """Mark all expired vouchers as expired"""
        try:
            count = self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expires_at < datetime.utcnow()
                )
            ).update({'status': 'expired'}, synchronize_session=False)
            db.session.commit()
            if count > 0:
                logger.info(f"Marked {count} vouchers as expired")
            return count
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in mark_expired_vouchers: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Voucher:
        """Create a single voucher"""
        try:
            voucher = self.model(**data)
            db.session.add(voucher)
            db.session.commit()
            logger.info(f"Created voucher: {voucher.code}")
            return voucher
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def create_batch(self, vouchers_data: List[Dict[str, Any]]) -> List[Voucher]:
        """Create multiple vouchers in batch"""
        try:
            vouchers = [self.model(**data) for data in vouchers_data]
            db.session.add_all(vouchers)
            db.session.commit()
            logger.info(f"Created batch of {len(vouchers)} vouchers")
            return vouchers
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create_batch: {e}", exc_info=True)
            raise
    
    def use_voucher(self, voucher_id: UUID, subscriber_id: UUID, router_id: UUID = None) -> bool:
        """Mark a voucher as used"""
        try:
            voucher = self.get_by_id(voucher_id, None, include_all=True)
            if not voucher:
                return False
            
            voucher.usage_count += 1
            if voucher.usage_count >= voucher.max_uses:
                voucher.status = 'used'
            voucher.used_by_subscriber_id = subscriber_id
            voucher.used_at = datetime.utcnow()
            if router_id:
                voucher.used_on_router_id = router_id
            
            db.session.commit()
            logger.info(f"Voucher {voucher.code} used by subscriber {subscriber_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in use_voucher: {e}", exc_info=True)
            raise
    
    def activate_voucher(self, voucher_id: UUID, activation_time: datetime = None) -> bool:
        """Activate a voucher (for first-use activation)"""
        try:
            voucher = self.get_by_id(voucher_id, None, include_all=True)
            if not voucher:
                return False
            
            if voucher.activation_type == 'first_use' and not voucher.activated_at:
                voucher.activated_at = activation_time or datetime.utcnow()
                # Update expiry based on activation
                voucher.expires_at = voucher.calculate_expiry_from_activation(voucher.activated_at)
                db.session.commit()
                logger.info(f"Voucher {voucher.code} activated at {voucher.activated_at}")
            
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in activate_voucher: {e}", exc_info=True)
            raise
    
    def delete(self, voucher_id: UUID, organization_id: UUID) -> bool:
        """Delete a voucher"""
        try:
            voucher = self.get_by_id(voucher_id, organization_id, include_all=True)
            if not voucher:
                return False
            
            db.session.delete(voucher)
            db.session.commit()
            logger.info(f"Deleted voucher: {voucher_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise


class VoucherBatchRepository:
    """Repository for VoucherBatch operations with dynamic validity"""
    
    def __init__(self):
        self.model = VoucherBatch
    
    def get_by_id(self, batch_id: UUID, organization_id: UUID) -> Optional[VoucherBatch]:
        """Get batch by ID"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == batch_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 50) -> List[VoucherBatch]:
        """Get all batches for an organization"""
        try:
            return self.model.query.filter(
                self.model.organization_id == organization_id
            ).order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def get_by_status(self, organization_id: UUID, status: str) -> List[VoucherBatch]:
        """Get batches by status"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == status
                )
            ).order_by(desc(self.model.created_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_status: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> VoucherBatch:
        """Create a voucher batch"""
        try:
            batch = self.model(**data)
            db.session.add(batch)
            db.session.commit()
            logger.info(f"Created voucher batch: {batch.batch_name}")
            return batch
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update_status(self, batch_id: UUID, organization_id: UUID, status: str) -> bool:
        """Update batch status"""
        try:
            batch = self.get_by_id(batch_id, organization_id)
            if not batch:
                return False
            batch.status = status
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_status: {e}", exc_info=True)
            raise


class DiscountCouponRepository:
    """Repository for DiscountCoupon operations"""
    
    def __init__(self):
        self.model = DiscountCoupon
    
    def get_by_id(self, coupon_id: UUID, organization_id: UUID) -> Optional[DiscountCoupon]:
        """Get coupon by ID"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == coupon_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_code(self, code: str, organization_id: UUID) -> Optional[DiscountCoupon]:
        """Get coupon by code"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.code == code,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_code: {e}", exc_info=True)
            raise
    
    def get_valid_by_code(self, code: str, organization_id: UUID, amount: float = 0) -> Optional[DiscountCoupon]:
        """Get valid coupon by code (checks dates and usage)"""
        try:
            now = datetime.utcnow()
            coupon = self.model.query.filter(
                and_(
                    self.model.code == code,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.valid_from <= now,
                    self.model.valid_to >= now
                )
            ).first()
            
            if coupon and coupon.is_valid() and amount >= float(coupon.minimum_purchase):
                return coupon
            return None
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_valid_by_code: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 50) -> List[DiscountCoupon]:
        """Get all coupons for an organization"""
        try:
            return self.model.query.filter(
                self.model.organization_id == organization_id
            ).order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def get_active_coupons(self, organization_id: UUID) -> List[DiscountCoupon]:
        """Get all active coupons (valid now)"""
        try:
            now = datetime.utcnow()
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.valid_from <= now,
                    self.model.valid_to >= now
                )
            ).order_by(self.model.valid_to).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_coupons: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> DiscountCoupon:
        """Create a discount coupon"""
        try:
            coupon = self.model(**data)
            db.session.add(coupon)
            db.session.commit()
            logger.info(f"Created discount coupon: {coupon.code}")
            return coupon
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, coupon_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[DiscountCoupon]:
        """Update a discount coupon"""
        try:
            coupon = self.get_by_id(coupon_id, organization_id)
            if not coupon:
                return None
            
            for key, value in data.items():
                if hasattr(coupon, key) and value is not None:
                    setattr(coupon, key, value)
            
            db.session.commit()
            logger.info(f"Updated discount coupon: {coupon.code}")
            return coupon
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def increment_usage(self, coupon_id: UUID) -> bool:
        """Increment coupon usage count"""
        try:
            coupon = self.model.query.get(coupon_id)
            if coupon:
                coupon.used_count += 1
                db.session.commit()
                return True
            return False
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in increment_usage: {e}", exc_info=True)
            raise
    
    def delete(self, coupon_id: UUID, organization_id: UUID) -> bool:
        """Delete a discount coupon"""
        try:
            coupon = self.get_by_id(coupon_id, organization_id)
            if not coupon:
                return False
            
            db.session.delete(coupon)
            db.session.commit()
            logger.info(f"Deleted discount coupon: {coupon_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise