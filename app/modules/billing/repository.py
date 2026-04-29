from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, or_, desc, func, between
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
from app.models.billing import Plan, Subscription, Invoice, InvoiceItem, Voucher, VoucherBatch, DiscountCoupon
from app.core.database.session import db
from app.core.logging.logger import logger

class PlanRepository:
    """Repository for Plan operations"""
    
    def __init__(self):
        self.model = Plan
    
    def get_by_id(self, plan_id: UUID, organization_id: UUID) -> Optional[Plan]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == plan_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, 
                            limit: int = 100, only_active: bool = True) -> List[Plan]:
        try:
            query = self.model.query.filter(self.model.organization_id == organization_id)
            if only_active:
                query = query.filter(self.model.is_active == True)
            return query.order_by(self.model.sort_order, self.model.created_at).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Plan:
        try:
            plan = self.model(**data)
            db.session.add(plan)
            db.session.commit()
            logger.info(f"Created plan: {plan.name}")
            return plan
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, plan_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Plan]:
        try:
            plan = self.get_by_id(plan_id, organization_id)
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
    
    def delete(self, plan_id: UUID, organization_id: UUID) -> bool:
        try:
            plan = self.get_by_id(plan_id, organization_id)
            if not plan:
                return False
            
            db.session.delete(plan)
            db.session.commit()
            logger.info(f"Deleted plan: {plan.name}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise


class SubscriptionRepository:
    """Repository for Subscription operations"""
    
    def __init__(self):
        self.model = Subscription
    
    def get_by_id(self, subscription_id: UUID, organization_id: UUID) -> Optional[Subscription]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == subscription_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_active_by_subscriber(self, subscriber_id: UUID, organization_id: UUID) -> Optional[Subscription]:
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
    
    def get_active_by_subscriber_phone(self, phone: str, organization_id: UUID) -> Optional[Subscription]:
        try:
            from app.modules.subscriber.models import Subscriber
            return self.model.query.join(Subscriber).filter(
                and_(
                    Subscriber.phone == phone,
                    Subscriber.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expiry_time > datetime.utcnow()
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_by_subscriber_phone: {e}", exc_info=True)
            raise
    
    def get_by_subscriber(self, subscriber_id: UUID, organization_id: UUID, 
                          skip: int = 0, limit: int = 50) -> List[Subscription]:
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
    
    def create(self, data: Dict[str, Any]) -> Subscription:
        try:
            subscription = self.model(**data)
            db.session.add(subscription)
            db.session.commit()
            logger.info(f"Created subscription: {subscription.id}")
            return subscription
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update_status(self, subscription_id: UUID, organization_id: UUID, 
                      status: str, reason: str = None) -> bool:
        try:
            subscription = self.get_by_id(subscription_id, organization_id)
            if not subscription:
                return False
            
            subscription.status = status
            if reason:
                subscription.cancellation_reason = reason
            if status in ['cancelled', 'expired']:
                subscription.cancelled_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"Updated subscription {subscription_id} status to {status}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_status: {e}", exc_info=True)
            raise
    
    def expire_expired_subscriptions(self, organization_id: UUID) -> int:
        try:
            expired = self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expiry_time <= datetime.utcnow()
                )
            ).update({'status': 'expired'})
            db.session.commit()
            return expired
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in expire_expired_subscriptions: {e}", exc_info=True)
            raise


class VoucherRepository:
    """Repository for Voucher operations"""
    
    def __init__(self):
        self.model = Voucher
    
    def get_by_code(self, code: str, organization_id: UUID) -> Optional[Voucher]:
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
        try:
            return self.model.query.filter(
                and_(
                    self.model.batch_id == batch_id,
                    self.model.organization_id == organization_id
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_batch: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Voucher:
        try:
            voucher = self.model(**data)
            db.session.add(voucher)
            db.session.commit()
            return voucher
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def create_batch(self, vouchers_data: List[Dict[str, Any]]) -> List[Voucher]:
        try:
            vouchers = [self.model(**data) for data in vouchers_data]
            db.session.add_all(vouchers)
            db.session.commit()
            return vouchers
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create_batch: {e}", exc_info=True)
            raise
    
    def use_voucher(self, voucher_id: UUID, subscriber_id: UUID, router_id: UUID) -> bool:
        try:
            voucher = self.model.query.get(voucher_id)
            if not voucher:
                return False
            
            voucher.usage_count += 1
            if voucher.usage_count >= voucher.max_uses:
                voucher.status = 'used'
            voucher.used_by_subscriber_id = subscriber_id
            voucher.used_at = datetime.utcnow()
            voucher.used_on_router_id = router_id
            
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in use_voucher: {e}", exc_info=True)
            raise

class VoucherBatchRepository:
    """Repository for VoucherBatch operations"""
    
    def __init__(self):
        self.model = VoucherBatch
    
    def get_by_id(self, batch_id: UUID, organization_id: UUID) -> Optional[VoucherBatch]:
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
    
    def create(self, data: Dict[str, Any]) -> VoucherBatch:
        try:
            batch = self.model(**data)
            db.session.add(batch)
            db.session.commit()
            return batch
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update_status(self, batch_id: UUID, organization_id: UUID, status: str) -> bool:
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
    
    def get_by_code(self, code: str, organization_id: UUID) -> Optional[DiscountCoupon]:
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
        try:
            now = datetime.utcnow()
            query = self.model.query.filter(
                and_(
                    self.model.code == code,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.valid_from <= now,
                    self.model.valid_to >= now
                )
            )
            
            coupon = query.first()
            if coupon and coupon.is_valid() and amount >= float(coupon.minimum_purchase):
                return coupon
            return None
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_valid_by_code: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> DiscountCoupon:
        try:
            coupon = self.model(**data)
            db.session.add(coupon)
            db.session.commit()
            return coupon
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def increment_usage(self, coupon_id: UUID) -> bool:
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