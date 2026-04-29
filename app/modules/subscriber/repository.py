from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, or_, desc, func
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

from app.models.subscriber import Subscriber, Device
from app.models.billing import Subscription, Plan
from app.core.database.session import db
from app.core.logging.logger import logger

class SubscriberRepository:
    """Data access layer for Subscriber operations"""
    
    def __init__(self):
        self.model = Subscriber
        self.device_model = Device
        self.subscription_model = Subscription
    
    def get_by_id(self, subscriber_id: UUID, organization_id: UUID) -> Optional[Subscriber]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == subscriber_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_phone(self, phone: str, organization_id: UUID) -> Optional[Subscriber]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.phone == phone,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_phone: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100, filters: Dict = None) -> List[Subscriber]:
        try:
            query = self.model.query.filter(self.model.organization_id == organization_id)
            
            if filters:
                if filters.get('status'):
                    query = query.filter(self.model.status == filters['status'])
                if filters.get('search'):
                    search = f"%{filters['search']}%"
                    query = query.filter(
                        or_(
                            self.model.phone.ilike(search),
                            self.model.first_name.ilike(search),
                            self.model.last_name.ilike(search),
                            self.model.email.ilike(search)
                        )
                    )
                if filters.get('has_active_subscription') is True:
                    # Get subscribers with active subscription
                    subquery = db.session.query(Subscription.subscriber_id).filter(
                        and_(
                            Subscription.organization_id == organization_id,
                            Subscription.status == 'active',
                            Subscription.expiry_time > datetime.utcnow()
                        )
                    ).subquery()
                    query = query.filter(self.model.id.in_(subquery))
                elif filters.get('has_active_subscription') is False:
                    # Get subscribers without active subscription
                    subquery = db.session.query(Subscription.subscriber_id).filter(
                        and_(
                            Subscription.organization_id == organization_id,
                            Subscription.status == 'active',
                            Subscription.expiry_time > datetime.utcnow()
                        )
                    ).subquery()
                    query = query.filter(~self.model.id.in_(subquery))
            
            return query.order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def count_by_organization(self, organization_id: UUID, include_inactive: bool = False) -> int:
        try:
            query = self.model.query.filter(self.model.organization_id == organization_id)
            if not include_inactive:
                query = query.filter(self.model.status == 'active')
            return query.count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_organization: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Subscriber:
        try:
            subscriber = self.model(**data)
            db.session.add(subscriber)
            db.session.commit()
            logger.info(f"Created subscriber: {subscriber.phone}")
            return subscriber
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, subscriber_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Subscriber]:
        try:
            subscriber = self.get_by_id(subscriber_id, organization_id)
            if not subscriber:
                return None
            
            for key, value in data.items():
                if hasattr(subscriber, key) and value is not None:
                    setattr(subscriber, key, value)
            
            db.session.commit()
            logger.info(f"Updated subscriber: {subscriber_id}")
            return subscriber
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def delete(self, subscriber_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate subscriber"""
        try:
            subscriber = self.get_by_id(subscriber_id, organization_id)
            if not subscriber:
                return False
            
            if soft_delete:
                subscriber.status = 'deleted'
                subscriber.is_active = False
            else:
                db.session.delete(subscriber)
            
            db.session.commit()
            logger.info(f"Deleted subscriber: {subscriber_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise
    
    def get_devices(self, subscriber_id: UUID, organization_id: UUID) -> List[Device]:
        try:
            return self.device_model.query.filter(
                and_(
                    self.device_model.subscriber_id == subscriber_id,
                    self.device_model.organization_id == organization_id,
                    self.device_model.is_active == True
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_devices: {e}", exc_info=True)
            raise
    
    def get_device_by_mac(self, mac_address: str, organization_id: UUID) -> Optional[Device]:
        try:
            return self.device_model.query.filter(
                and_(
                    self.device_model.mac_address == mac_address,
                    self.device_model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_device_by_mac: {e}", exc_info=True)
            raise
    
    def add_device(self, subscriber_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Device:
        try:
            data['subscriber_id'] = subscriber_id
            data['organization_id'] = organization_id
            device = self.device_model(**data)
            db.session.add(device)
            db.session.commit()
            logger.info(f"Added device {device.mac_address} for subscriber {subscriber_id}")
            return device
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in add_device: {e}", exc_info=True)
            raise
    
    def update_device(self, device_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Device]:
        try:
            device = self.device_model.query.filter(
                and_(
                    self.device_model.id == device_id,
                    self.device_model.organization_id == organization_id
                )
            ).first()
            if not device:
                return None
            
            for key, value in data.items():
                if hasattr(device, key) and value is not None:
                    setattr(device, key, value)
            
            db.session.commit()
            logger.info(f"Updated device {device_id}")
            return device
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_device: {e}", exc_info=True)
            raise
    
    def remove_device(self, device_id: UUID, organization_id: UUID) -> bool:
        """Remove device (soft delete)"""
        try:
            device = self.device_model.query.filter(
                and_(
                    self.device_model.id == device_id,
                    self.device_model.organization_id == organization_id
                )
            ).first()
            if not device:
                return False
            
            device.is_active = False
            db.session.commit()
            logger.info(f"Removed device {device_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in remove_device: {e}", exc_info=True)
            raise
    
    def get_active_subscription(self, subscriber_id: UUID, organization_id: UUID) -> Optional[Subscription]:
        try:
            return self.subscription_model.query.filter(
                and_(
                    self.subscription_model.subscriber_id == subscriber_id,
                    self.subscription_model.organization_id == organization_id,
                    self.subscription_model.status == 'active',
                    self.subscription_model.expiry_time > datetime.utcnow()
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_subscription: {e}", exc_info=True)
            raise
    
    def get_subscription_history(self, subscriber_id: UUID, organization_id: UUID, limit: int = 10) -> List[Subscription]:
        """Get subscription history for a subscriber"""
        try:
            return self.subscription_model.query.filter(
                and_(
                    self.subscription_model.subscriber_id == subscriber_id,
                    self.subscription_model.organization_id == organization_id
                )
            ).order_by(desc(self.subscription_model.created_at)).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_subscription_history: {e}", exc_info=True)
            raise
    
    def get_active_subscribers_count(self, organization_id: UUID) -> int:
        """Count subscribers with active subscriptions"""
        try:
            return db.session.query(func.count(distinct(self.subscription_model.subscriber_id))).filter(
                and_(
                    self.subscription_model.organization_id == organization_id,
                    self.subscription_model.status == 'active',
                    self.subscription_model.expiry_time > datetime.utcnow()
                )
            ).scalar() or 0
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_subscribers_count: {e}", exc_info=True)
            raise


class PlanRepository:
    """Data access layer for Plan operations"""
    
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
    
    def get_by_organization(self, organization_id: UUID, is_active: bool = True, plan_type: str = None) -> List[Plan]:
        try:
            query = self.model.query.filter(self.model.organization_id == organization_id)
            if is_active:
                query = query.filter(self.model.is_active == True)
            if plan_type:
                query = query.filter(self.model.plan_type == plan_type)
            return query.order_by(self.model.sort_order, self.model.price).all()
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
            logger.info(f"Updated plan: {plan_id}")
            return plan
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def delete(self, plan_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate plan"""
        try:
            plan = self.get_by_id(plan_id, organization_id)
            if not plan:
                return False
            
            # Check if plan has active subscriptions
            has_active_subs = db.session.query(Subscription).filter(
                and_(
                    Subscription.plan_id == plan_id,
                    Subscription.organization_id == organization_id,
                    Subscription.status == 'active',
                    Subscription.expiry_time > datetime.utcnow()
                )
            ).first() is not None
            
            if has_active_subs:
                logger.warning(f"Cannot delete plan {plan_id} with active subscriptions")
                return False
            
            if soft_delete:
                plan.is_active = False
            else:
                db.session.delete(plan)
            
            db.session.commit()
            logger.info(f"Deleted plan: {plan_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise
    
    def get_public_plans(self, organization_id: UUID) -> List[Plan]:
        """Get public plans for customer portal"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.is_public == True
                )
            ).order_by(self.model.sort_order, self.model.price).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_public_plans: {e}", exc_info=True)
            raise