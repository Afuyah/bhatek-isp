"""
Subscriber Repository
=====================
Data access layer for Subscriber, Device, and Plan operations.
All queries enforce organization_id for multi-tenant isolation.
"""

from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, or_, desc, func, distinct
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta

from app.models.subscriber import Subscriber, Device
from app.models.billing import Subscription, Plan
from app.core.database.session import db
from app.core.logging.logger import logger


class SubscriberRepository:
    """Data access layer for Subscriber operations with tenant isolation."""

    def __init__(self):
        self.model = Subscriber
        self.device_model = Device
        self.subscription_model = Subscription

    # =========================================================================
    # SUBSCRIBER CRUD
    # =========================================================================

    def get_by_id(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        include_inactive: bool = False,
    ) -> Optional[Subscriber]:
        """Get subscriber by ID with organization isolation."""
        try:
            filters = [
                self.model.id == subscriber_id,
                self.model.organization_id == organization_id,
            ]
            if not include_inactive:
                filters.append(self.model.status == 'active')

            return self.model.query.filter(and_(*filters)).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise

    def get_by_phone(
        self, phone: str, organization_id: UUID
    ) -> Optional[Subscriber]:
        """Get subscriber by phone number (hotspot users)."""
        try:
            return self.model.query.filter(
                and_(
                    self.model.phone == phone,
                    self.model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_phone: {e}", exc_info=True)
            raise

    def get_by_username(
        self, username: str, organization_id: UUID
    ) -> Optional[Subscriber]:
        """Get subscriber by username (PPPoE users)."""
        try:
            return self.model.query.filter(
                and_(
                    self.model.username == username,
                    self.model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_username: {e}", exc_info=True)
            raise

    def get_by_login_credential(
        self, credential: str, organization_id: UUID
    ) -> Optional[Subscriber]:
        """
        Get subscriber by phone OR username.

        Used by RADIUS auth for phone-based and PPPoE authentication.
        Does NOT search by MAC — MAC lookup goes through Device table.
        """
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    or_(
                        self.model.phone == credential,
                        self.model.username == credential,
                    ),
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_login_credential: {e}", exc_info=True
            )
            raise

    def get_by_organization(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
        filters: Dict = None,
        subscriber_type: str = None,
    ) -> List[Subscriber]:
        """Get all subscribers for an organization with optional filters."""
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )

            if subscriber_type:
                query = query.filter(
                    self.model.subscriber_type == subscriber_type
                )

            if filters:
                if filters.get('status'):
                    query = query.filter(
                        self.model.status == filters['status']
                    )

                if filters.get('search'):
                    search = f"%{filters['search']}%"
                    query = query.filter(
                        or_(
                            self.model.phone.ilike(search),
                            self.model.username.ilike(search),
                            self.model.first_name.ilike(search),
                            self.model.last_name.ilike(search),
                            self.model.email.ilike(search),
                        )
                    )

                # Filter: has active subscription
                if filters.get('has_active_subscription') is True:
                    subquery = (
                        db.session.query(Subscription.subscriber_id)
                        .filter(
                            and_(
                                Subscription.organization_id == organization_id,
                                Subscription.status == 'active',
                                Subscription.expiry_time > datetime.utcnow(),
                            )
                        )
                        .subquery()
                    )
                    query = query.filter(self.model.id.in_(subquery))

                elif filters.get('has_active_subscription') is False:
                    subquery = (
                        db.session.query(Subscription.subscriber_id)
                        .filter(
                            and_(
                                Subscription.organization_id == organization_id,
                                Subscription.status == 'active',
                                Subscription.expiry_time > datetime.utcnow(),
                            )
                        )
                        .subquery()
                    )
                    query = query.filter(~self.model.id.in_(subquery))

            return (
                query.order_by(desc(self.model.created_at))
                .offset(skip)
                .limit(limit)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_organization: {e}", exc_info=True
            )
            raise

    def get_hotspot_users(
        self, organization_id: UUID, skip: int = 0, limit: int = 100
    ) -> List[Subscriber]:
        """Get all active hotspot users."""
        try:
            return (
                self.model.query.filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.subscriber_type == 'hotspot',
                        self.model.status == 'active',
                    )
                )
                .order_by(desc(self.model.created_at))
                .offset(skip)
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_hotspot_users: {e}", exc_info=True
            )
            raise

    def get_pppoe_users(
        self, organization_id: UUID, skip: int = 0, limit: int = 100
    ) -> List[Subscriber]:
        """Get all active PPPoE users."""
        try:
            return (
                self.model.query.filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.subscriber_type == 'pppoe',
                        self.model.status == 'active',
                    )
                )
                .order_by(desc(self.model.created_at))
                .offset(skip)
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_pppoe_users: {e}", exc_info=True
            )
            raise

    def get_or_create_by_phone(
        self, phone: str, organization_id: UUID
    ) -> Subscriber:
        """
        Get existing subscriber by phone or auto-create new one.

        Used in M-Pesa payment flow when a new user pays.
        """
        try:
            subscriber = self.get_by_phone(phone, organization_id)
            if not subscriber:
                subscriber_data = {
                    'organization_id': organization_id,
                    'phone': phone,
                    'subscriber_type': 'hotspot',
                    'status': 'active',
                }
                subscriber = self.create(subscriber_data)
                logger.info(f"Auto-created hotspot subscriber: {phone}")
            return subscriber
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_or_create_by_phone: {e}", exc_info=True
            )
            raise

    # =========================================================================
    # COUNT & STATISTICS
    # =========================================================================

    def count_by_organization(
        self,
        organization_id: UUID,
        include_inactive: bool = False,
        subscriber_type: str = None,
    ) -> int:
        """Count subscribers in organization with optional filters."""
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )
            if not include_inactive:
                query = query.filter(self.model.status == 'active')
            if subscriber_type:
                query = query.filter(
                    self.model.subscriber_type == subscriber_type
                )
            return query.count()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in count_by_organization: {e}", exc_info=True
            )
            raise

    def count_active_sessions(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> int:
        """Count active sessions for a subscriber."""
        try:
            from app.models.session import ActiveSession

            return (
                ActiveSession.query.filter(
                    and_(
                        ActiveSession.subscriber_id == subscriber_id,
                        ActiveSession.organization_id == organization_id,
                        ActiveSession.status == 'active',
                    )
                ).count()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in count_active_sessions: {e}", exc_info=True
            )
            raise

    # =========================================================================
    # CREATE / UPDATE / DELETE
    # =========================================================================

    def create(self, data: Dict[str, Any]) -> Subscriber:
        """Create a new subscriber."""
        try:
            subscriber = self.model(**data)
            db.session.add(subscriber)
            db.session.commit()
            logger.info(
                f"Created {subscriber.subscriber_type} subscriber: "
                f"{subscriber.display_name}"
            )
            return subscriber
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise

    def update(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Optional[Subscriber]:
        """Update a subscriber."""
        try:
            subscriber = self.get_by_id(
                subscriber_id, organization_id, include_inactive=True
            )
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

    def update_last_active(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> bool:
        """Update last active timestamp."""
        try:
            subscriber = self.get_by_id(
                subscriber_id, organization_id, include_inactive=True
            )
            if not subscriber:
                return False

            subscriber.last_active_at = datetime.utcnow()
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update_last_active: {e}", exc_info=True
            )
            raise

    def update_total_spent(
        self, subscriber_id: UUID, organization_id: UUID, amount: float
    ) -> bool:
        """Add to total spent."""
        try:
            subscriber = self.get_by_id(
                subscriber_id, organization_id, include_inactive=True
            )
            if not subscriber:
                return False

            subscriber.total_spent = (subscriber.total_spent or 0) + amount
            db.session.commit()
            logger.info(
                f"Updated total spent for subscriber {subscriber_id}: +{amount}"
            )
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update_total_spent: {e}", exc_info=True
            )
            raise

    def delete(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True,
    ) -> bool:
        """Delete or deactivate subscriber."""
        try:
            subscriber = self.get_by_id(
                subscriber_id, organization_id, include_inactive=True
            )
            if not subscriber:
                return False

            if soft_delete:
                subscriber.status = 'deleted'
            else:
                db.session.delete(subscriber)

            db.session.commit()
            logger.info(f"Deleted subscriber: {subscriber_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise

    # =========================================================================
    # DEVICE MANAGEMENT
    # =========================================================================

    def get_devices(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> List[Device]:
        """Get all active devices for a subscriber."""
        try:
            return (
                self.device_model.query.filter(
                    and_(
                        self.device_model.subscriber_id == subscriber_id,
                        self.device_model.organization_id == organization_id,
                        self.device_model.is_active == True,
                    )
                ).all()
            )
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_devices: {e}", exc_info=True)
            raise

    def get_device_by_id(
        self, device_id: UUID, organization_id: UUID
    ) -> Optional[Device]:
        """Get device by ID with organization isolation."""
        try:
            return self.device_model.query.filter(
                and_(
                    self.device_model.id == device_id,
                    self.device_model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_device_by_id: {e}", exc_info=True
            )
            raise

    def get_device_by_mac(
        self, mac_address: str, organization_id: UUID
    ) -> Optional[Device]:
        """
        Get device by MAC address within an organization.

        Used by RADIUS auth handler for MAC auto-connect lookup.
        Returns the device even if inactive (caller checks is_active).
        """
        try:
            return self.device_model.query.filter(
                and_(
                    self.device_model.mac_address == mac_address.upper(),
                    self.device_model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_device_by_mac: {e}", exc_info=True
            )
            raise

    def get_device_by_subscriber_and_mac(
        self,
        subscriber_id: UUID,
        mac_address: str,
        organization_id: UUID,
    ) -> Optional[Device]:
        """Get device by subscriber and MAC address."""
        try:
            return self.device_model.query.filter(
                and_(
                    self.device_model.subscriber_id == subscriber_id,
                    self.device_model.mac_address == mac_address.upper(),
                    self.device_model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_device_by_subscriber_and_mac: {e}",
                exc_info=True,
            )
            raise

    def count_active_devices(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> int:
        """Count active devices for a subscriber."""
        try:
            return (
                self.device_model.query.filter(
                    and_(
                        self.device_model.subscriber_id == subscriber_id,
                        self.device_model.organization_id == organization_id,
                        self.device_model.is_active == True,
                    )
                ).count()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in count_active_devices: {e}", exc_info=True
            )
            raise

    def add_device(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Device:
        """
        Add a device to a subscriber.

        If the MAC already exists (device reuse), reassigns it to the
        new subscriber and reactivates it.
        """
        try:
            mac = data.get('mac_address', '').upper()
            existing = self.get_device_by_mac(mac, organization_id)

            if existing:
                # Reassign existing device
                existing.subscriber_id = subscriber_id
                existing.is_active = True
                existing.last_seen_at = datetime.utcnow()
                if data.get('device_name'):
                    existing.device_name = data['device_name']
                if data.get('device_type'):
                    existing.device_type = data['device_type']
                db.session.commit()
                logger.info(
                    f"Reassigned device {mac} to subscriber {subscriber_id}"
                )
                return existing

            # Create new device
            data['subscriber_id'] = subscriber_id
            data['organization_id'] = organization_id
            data['mac_address'] = mac
            device = self.device_model(**data)
            db.session.add(device)
            db.session.commit()
            logger.info(
                f"Added device {mac} for subscriber {subscriber_id}"
            )
            return device
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in add_device: {e}", exc_info=True)
            raise

    def update_device_last_seen(
        self, device_id: UUID, organization_id: UUID
    ) -> bool:
        """Update device last seen timestamp."""
        try:
            device = self.device_model.query.filter(
                and_(
                    self.device_model.id == device_id,
                    self.device_model.organization_id == organization_id,
                )
            ).first()
            if not device:
                return False

            device.last_seen_at = datetime.utcnow()
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update_device_last_seen: {e}", exc_info=True
            )
            raise

    def update_device(
        self,
        device_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Optional[Device]:
        """Update a device."""
        try:
            device = self.device_model.query.filter(
                and_(
                    self.device_model.id == device_id,
                    self.device_model.organization_id == organization_id,
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

    def remove_device(
        self,
        device_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True,
    ) -> bool:
        """Remove (deactivate) a device."""
        try:
            device = self.device_model.query.filter(
                and_(
                    self.device_model.id == device_id,
                    self.device_model.organization_id == organization_id,
                )
            ).first()
            if not device:
                return False

            if soft_delete:
                device.is_active = False
            else:
                db.session.delete(device)

            db.session.commit()
            logger.info(f"Removed device {device_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in remove_device: {e}", exc_info=True
            )
            raise

    # =========================================================================
    # SUBSCRIPTION MANAGEMENT
    # =========================================================================

    def get_active_subscription(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> Optional[Subscription]:
        """
        Get active subscription for a subscriber.

        Active = status=='active' AND expiry_time > now.
        """
        try:
            return (
                self.subscription_model.query.filter(
                    and_(
                        self.subscription_model.subscriber_id == subscriber_id,
                        self.subscription_model.organization_id == organization_id,
                        self.subscription_model.status == 'active',
                        self.subscription_model.expiry_time > datetime.utcnow(),
                    )
                ).first()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_active_subscription: {e}", exc_info=True
            )
            raise

    def get_subscription_by_id(
        self, subscription_id: UUID, organization_id: UUID
    ) -> Optional[Subscription]:
        """Get subscription by ID with organization isolation."""
        try:
            return self.subscription_model.query.filter(
                and_(
                    self.subscription_model.id == subscription_id,
                    self.subscription_model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_subscription_by_id: {e}", exc_info=True
            )
            raise

    def get_subscription_history(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        limit: int = 10,
    ) -> List[Subscription]:
        """Get subscription history for a subscriber."""
        try:
            return (
                self.subscription_model.query.filter(
                    and_(
                        self.subscription_model.subscriber_id == subscriber_id,
                        self.subscription_model.organization_id == organization_id,
                    )
                )
                .order_by(desc(self.subscription_model.created_at))
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_subscription_history: {e}", exc_info=True
            )
            raise

    def has_active_subscription(
        self, subscriber_id: UUID, organization_id: UUID
    ) -> bool:
        """Check if subscriber has an active subscription."""
        return (
            self.get_active_subscription(subscriber_id, organization_id)
            is not None
        )

    # =========================================================================
    # STATISTICS & REPORTING
    # =========================================================================

    def get_active_subscribers_count(self, organization_id: UUID) -> int:
        """Count subscribers with active subscriptions."""
        try:
            return (
                db.session.query(
                    func.count(distinct(self.subscription_model.subscriber_id))
                )
                .filter(
                    and_(
                        self.subscription_model.organization_id == organization_id,
                        self.subscription_model.status == 'active',
                        self.subscription_model.expiry_time > datetime.utcnow(),
                    )
                )
                .scalar()
            ) or 0
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_active_subscribers_count: {e}",
                exc_info=True,
            )
            raise

    def get_subscriber_stats(self, organization_id: UUID) -> Dict[str, Any]:
        """Get subscriber statistics for dashboard."""
        try:
            total = self.count_by_organization(organization_id)
            active_subs = self.get_active_subscribers_count(organization_id)
            hotspot_count = self.count_by_organization(
                organization_id, subscriber_type='hotspot'
            )
            pppoe_count = self.count_by_organization(
                organization_id, subscriber_type='pppoe'
            )

            return {
                'total': total,
                'active_subscriptions': active_subs,
                'hotspot_users': hotspot_count,
                'pppoe_users': pppoe_count,
                'inactive': total - active_subs,
            }
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_subscriber_stats: {e}", exc_info=True
            )
            raise

    def get_recent_subscribers(
        self, organization_id: UUID, limit: int = 10
    ) -> List[Subscriber]:
        """Get recently created subscribers."""
        try:
            return (
                self.model.query.filter(
                    self.model.organization_id == organization_id
                )
                .order_by(desc(self.model.created_at))
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_recent_subscribers: {e}", exc_info=True
            )
            raise

    def get_subscribers_expiring_soon(
        self, organization_id: UUID, days: int = 3
    ) -> List[Subscriber]:
        """Get subscribers whose subscriptions expire within N days."""
        try:
            expiry_threshold = datetime.utcnow() + timedelta(days=days)

            sub_ids = (
                db.session.query(self.subscription_model.subscriber_id)
                .filter(
                    and_(
                        self.subscription_model.organization_id == organization_id,
                        self.subscription_model.status == 'active',
                        self.subscription_model.expiry_time <= expiry_threshold,
                        self.subscription_model.expiry_time > datetime.utcnow(),
                    )
                )
                .subquery()
            )

            return (
                self.model.query.filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.id.in_(sub_ids),
                    )
                ).all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_subscribers_expiring_soon: {e}",
                exc_info=True,
            )
            raise


class PlanRepository:
    """Data access layer for Plan operations."""

    def __init__(self):
        self.model = Plan

    def get_by_id(
        self, plan_id: UUID, organization_id: UUID
    ) -> Optional[Plan]:
        """Get plan by ID with organization isolation."""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == plan_id,
                    self.model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in Plan.get_by_id: {e}", exc_info=True)
            raise

    def get_by_organization(
        self,
        organization_id: UUID,
        is_active: bool = True,
        plan_type: str = None,
    ) -> List[Plan]:
        """Get all plans for an organization."""
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )
            if is_active:
                query = query.filter(self.model.is_active == True)
            if plan_type:
                query = query.filter(self.model.plan_type == plan_type)
            return query.order_by(self.model.sort_order, self.model.price).all()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in Plan.get_by_organization: {e}", exc_info=True
            )
            raise

    def get_hotspot_plans(self, organization_id: UUID) -> List[Plan]:
        """Get active hotspot plans."""
        return self.get_by_organization(organization_id, plan_type='hotspot')

    def get_pppoe_plans(self, organization_id: UUID) -> List[Plan]:
        """Get active PPPoE plans."""
        return self.get_by_organization(organization_id, plan_type='pppoe')

    def get_public_plans(self, organization_id: UUID) -> List[Plan]:
        """Get public plans for customer-facing portal."""
        try:
            return (
                self.model.query.filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                        self.model.is_public == True,
                    )
                )
                .order_by(self.model.sort_order, self.model.price)
                .all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in Plan.get_public_plans: {e}", exc_info=True
            )
            raise

    def create(self, data: Dict[str, Any]) -> Plan:
        """Create a new plan."""
        try:
            plan = self.model(**data)
            db.session.add(plan)
            db.session.commit()
            logger.info(f"Created plan: {plan.name}")
            return plan
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in Plan.create: {e}", exc_info=True)
            raise

    def update(
        self, plan_id: UUID, organization_id: UUID, data: Dict[str, Any]
    ) -> Optional[Plan]:
        """Update a plan."""
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
            logger.error(f"Database error in Plan.update: {e}", exc_info=True)
            raise

    def delete(
        self,
        plan_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True,
    ) -> bool:
        """Delete or deactivate a plan."""
        try:
            plan = self.get_by_id(plan_id, organization_id)
            if not plan:
                return False

            # Check for active subscriptions
            has_active = (
                db.session.query(Subscription)
                .filter(
                    and_(
                        Subscription.plan_id == plan_id,
                        Subscription.organization_id == organization_id,
                        Subscription.status == 'active',
                        Subscription.expiry_time > datetime.utcnow(),
                    )
                )
                .first()
                is not None
            )

            if has_active:
                logger.warning(
                    f"Cannot delete plan {plan_id} — has active subscriptions"
                )
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
            logger.error(f"Database error in Plan.delete: {e}", exc_info=True)
            raise