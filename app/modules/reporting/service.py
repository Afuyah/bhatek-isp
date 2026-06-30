"""
Reporting Service
=================
Comprehensive reporting and analytics for the ISP management platform.

Covers:
    - Revenue reports (daily/weekly/monthly/custom)
    - Subscriber reports (active, expired, by plan, by router)
    - Router reports (health, performance, subscriber count)
    - Payment reports (by method, by subscriber, by plan)
    - Usage reports (data, sessions, bandwidth)
    - ISP-wide reports (all organizations)
    - Per-plan reports (subscriber count, revenue, churn)
    - Chart data endpoints (trends, distributions)
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, case, distinct
from sqlalchemy.orm import joinedload

from app.core.database.session import db
from app.core.logging.logger import logger


class ReportingService:
    """
    Aggregates data across all modules for reporting and dashboard display.
    All queries are organization-scoped unless explicitly requesting ISP-wide data.
    """

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _date_range(period: str, start_date: Optional[datetime] = None,
                    end_date: Optional[datetime] = None,
                    month: int = None, year: int = None,
                    week: int = None) -> Tuple[datetime, datetime]:
        """Resolve (start, end) for a named period or explicit range."""
        now = datetime.utcnow()
        if start_date and end_date:
            return start_date, end_date
        if period == 'daily':
            s = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return s, s + timedelta(days=1)
        if period == 'weekly':
            s = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0)
            return s, s + timedelta(weeks=1)
        if period == 'monthly':
            m = month or now.month
            y = year or now.year
            s = datetime(y, m, 1)
            _, last = calendar.monthrange(y, m)
            return s, datetime(y, m, last, 23, 59, 59) + timedelta(seconds=1)
        if period == 'yearly':
            y = year or now.year
            return datetime(y, 1, 1), datetime(y + 1, 1, 1)
        # default: last 30 days
        return now - timedelta(days=30), now

    # =========================================================================
    # DASHBOARD OVERVIEW
    # =========================================================================

    def get_dashboard_overview(self, organization_id: UUID) -> Dict[str, Any]:
        """
        Return high-level KPIs for the organization dashboard.
        Includes active subscribers, revenue, routers, sessions.
        """
        try:
            from app.models.billing import Subscription, Plan
            from app.models.subscriber import Subscriber
            from app.models.router import Router
            from app.models.session import ActiveSession
            from app.models.payment import Transaction

            now = datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            # Active subscribers
            active_subscribers = Subscriber.query.filter_by(
                organization_id=organization_id, status='active'
            ).count()

            # Active subscriptions
            active_subscriptions = Subscription.query.filter(
                Subscription.organization_id == organization_id,
                Subscription.status == 'active',
                Subscription.expiry_time > now,
            ).count()

            # Monthly revenue (successful transactions)
            monthly_revenue = db.session.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            ).filter(
                Transaction.organization_id == organization_id,
                Transaction.status == 'success',
                Transaction.created_at >= month_start,
            ).scalar() or 0

            # Active routers
            total_routers = Router.query.filter_by(
                organization_id=organization_id, is_active=True
            ).count()
            online_routers = Router.query.filter_by(
                organization_id=organization_id, is_active=True, status='online'
            ).count()

            # Active sessions
            active_sessions = ActiveSession.query.filter_by(
                organization_id=organization_id, status='active'
            ).count()

            # Expiring soon (next 7 days)
            expiring_soon = Subscription.query.filter(
                Subscription.organization_id == organization_id,
                Subscription.status == 'active',
                Subscription.expiry_time > now,
                Subscription.expiry_time <= now + timedelta(days=7),
            ).count()

            # New subscribers this month
            new_subscribers_month = Subscriber.query.filter(
                Subscriber.organization_id == organization_id,
                Subscriber.created_at >= month_start,
            ).count()

            # Previous month for growth calculation
            prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
            prev_month_revenue = db.session.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            ).filter(
                Transaction.organization_id == organization_id,
                Transaction.status == 'success',
                Transaction.created_at >= prev_month_start,
                Transaction.created_at < month_start,
            ).scalar() or 0

            revenue_growth = 0
            if prev_month_revenue > 0:
                revenue_growth = round(
                    ((float(monthly_revenue) - float(prev_month_revenue)) / float(prev_month_revenue)) * 100, 1
                )

            return {
                'active_subscribers': active_subscribers,
                'active_subscriptions': active_subscriptions,
                'monthly_revenue': float(monthly_revenue),
                'revenue_growth_pct': revenue_growth,
                'total_routers': total_routers,
                'online_routers': online_routers,
                'offline_routers': total_routers - online_routers,
                'active_sessions': active_sessions,
                'expiring_soon': expiring_soon,
                'new_subscribers_month': new_subscribers_month,
                'currency': 'KES',
                'generated_at': now.isoformat(),
            }
        except Exception as e:
            logger.error(f"Dashboard overview error: {e}", exc_info=True)
            raise

    # =========================================================================
    # REVENUE REPORTS
    # =========================================================================

    def get_revenue_report(
        self,
        organization_id: UUID,
        period: str = 'monthly',
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        month: int = None,
        year: int = None,
    ) -> Dict[str, Any]:
        """Revenue breakdown for a period."""
        try:
            from app.models.payment import Transaction
            from app.models.billing import Plan

            s, e = self._date_range(period, start_date, end_date, month, year)

            base = [
                Transaction.organization_id == organization_id,
                Transaction.status == 'success',
                Transaction.created_at >= s,
                Transaction.created_at < e,
            ]

            total = db.session.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            ).filter(and_(*base)).scalar() or 0

            count = Transaction.query.filter(and_(*base)).count()

            # By payment method
            by_method = db.session.query(
                Transaction.payment_method,
                func.count(Transaction.id).label('count'),
                func.coalesce(func.sum(Transaction.amount), 0).label('total'),
            ).filter(and_(*base)).group_by(Transaction.payment_method).all()

            # Daily trend within period
            daily_trend = self._revenue_daily_trend(organization_id, s, e)

            return {
                'period': period,
                'start': s.isoformat(),
                'end': e.isoformat(),
                'total_revenue': float(total),
                'transaction_count': count,
                'average_transaction': round(float(total) / count, 2) if count else 0,
                'by_payment_method': [
                    {'method': r.payment_method, 'count': r.count, 'total': float(r.total)}
                    for r in by_method
                ],
                'daily_trend': daily_trend,
                'currency': 'KES',
            }
        except Exception as e:
            logger.error(f"Revenue report error: {e}", exc_info=True)
            raise

    def _revenue_daily_trend(
        self, organization_id: UUID, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """Daily revenue aggregation within a date range."""
        try:
            from app.models.payment import Transaction
            from sqlalchemy import cast, Date

            rows = db.session.query(
                cast(Transaction.created_at, Date).label('day'),
                func.count(Transaction.id).label('count'),
                func.coalesce(func.sum(Transaction.amount), 0).label('total'),
            ).filter(
                Transaction.organization_id == organization_id,
                Transaction.status == 'success',
                Transaction.created_at >= start,
                Transaction.created_at < end,
            ).group_by(cast(Transaction.created_at, Date)).order_by('day').all()

            return [
                {'date': str(r.day), 'count': r.count, 'total': float(r.total)}
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"Daily trend error: {e}")
            return []

    # =========================================================================
    # SUBSCRIBER REPORTS
    # =========================================================================

    def get_subscriber_report(
        self,
        organization_id: UUID,
        period: str = 'monthly',
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Subscriber statistics for a period."""
        try:
            from app.models.subscriber import Subscriber
            from app.models.billing import Subscription

            s, e = self._date_range(period, start_date, end_date)
            now = datetime.utcnow()

            total = Subscriber.query.filter_by(organization_id=organization_id).count()
            active = Subscriber.query.filter_by(
                organization_id=organization_id, status='active'
            ).count()
            new_in_period = Subscriber.query.filter(
                Subscriber.organization_id == organization_id,
                Subscriber.created_at >= s,
                Subscriber.created_at < e,
            ).count()

            # By type
            hotspot = Subscriber.query.filter_by(
                organization_id=organization_id, subscriber_type='hotspot'
            ).count()
            pppoe = Subscriber.query.filter_by(
                organization_id=organization_id, subscriber_type='pppoe'
            ).count()

            # Active subscriptions by plan
            by_plan = db.session.query(
                Subscription.plan_id,
                func.count(Subscription.id).label('count'),
            ).filter(
                Subscription.organization_id == organization_id,
                Subscription.status == 'active',
                Subscription.expiry_time > now,
            ).group_by(Subscription.plan_id).all()

            # Resolve plan names
            from app.models.billing import Plan
            plan_map = {
                str(p.id): p.name
                for p in Plan.query.filter_by(organization_id=organization_id).all()
            }

            # Growth trend
            growth_trend = self._subscriber_growth_trend(organization_id, s, e)

            return {
                'period': period,
                'start': s.isoformat(),
                'end': e.isoformat(),
                'total_subscribers': total,
                'active_subscribers': active,
                'inactive_subscribers': total - active,
                'new_in_period': new_in_period,
                'hotspot_subscribers': hotspot,
                'pppoe_subscribers': pppoe,
                'by_plan': [
                    {
                        'plan_id': str(r.plan_id),
                        'plan_name': plan_map.get(str(r.plan_id), 'Unknown'),
                        'count': r.count,
                    }
                    for r in by_plan
                ],
                'growth_trend': growth_trend,
            }
        except Exception as e:
            logger.error(f"Subscriber report error: {e}", exc_info=True)
            raise

    def _subscriber_growth_trend(
        self, organization_id: UUID, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """Daily new subscriber count within a date range."""
        try:
            from app.models.subscriber import Subscriber
            from sqlalchemy import cast, Date

            rows = db.session.query(
                cast(Subscriber.created_at, Date).label('day'),
                func.count(Subscriber.id).label('count'),
            ).filter(
                Subscriber.organization_id == organization_id,
                Subscriber.created_at >= start,
                Subscriber.created_at < end,
            ).group_by(cast(Subscriber.created_at, Date)).order_by('day').all()

            return [{'date': str(r.day), 'new_subscribers': r.count} for r in rows]
        except Exception as e:
            logger.warning(f"Growth trend error: {e}")
            return []

    # =========================================================================
    # ROUTER REPORTS
    # =========================================================================

    def get_router_report(self, organization_id: UUID) -> Dict[str, Any]:
        """Router health and performance summary."""
        try:
            from app.models.router import Router
            from app.models.session import ActiveSession
            from app.models.billing import Subscription
            from app.models.subscriber import Subscriber

            routers = Router.query.filter_by(
                organization_id=organization_id, is_active=True
            ).all()

            router_data = []
            for r in routers:
                # Active sessions on this router
                sessions = ActiveSession.query.filter_by(
                    organization_id=organization_id,
                    router_id=r.id,
                    status='active',
                ).count()

                router_data.append({
                    'id': str(r.id),
                    'name': r.name,
                    'status': r.status,
                    'wireguard_ip': r.wireguard_ip,
                    'local_ip': r.local_ip,
                    'radius_config_status': r.radius_config_status,
                    'active_sessions': sessions,
                    'last_seen_at': r.last_seen_at.isoformat() if r.last_seen_at else None,
                    'cpu_load': None,
                    'uptime': None,
                })

            status_counts = {}
            for r in routers:
                status_counts[r.status] = status_counts.get(r.status, 0) + 1

            return {
                'total_routers': len(routers),
                'status_breakdown': status_counts,
                'routers': router_data,
                'generated_at': datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Router report error: {e}", exc_info=True)
            raise

    # =========================================================================
    # PLAN DISTRIBUTION
    # =========================================================================

    def get_plan_distribution(self, organization_id: UUID) -> List[Dict[str, Any]]:
        """Active subscription count per plan (for pie/donut charts)."""
        try:
            from app.models.billing import Subscription, Plan

            now = datetime.utcnow()
            rows = db.session.query(
                Subscription.plan_id,
                func.count(Subscription.id).label('count'),
            ).filter(
                Subscription.organization_id == organization_id,
                Subscription.status == 'active',
                Subscription.expiry_time > now,
            ).group_by(Subscription.plan_id).all()

            plan_map = {
                str(p.id): {'name': p.name, 'price': float(p.price), 'type': p.plan_type}
                for p in Plan.query.filter_by(organization_id=organization_id).all()
            }

            return [
                {
                    'plan_id': str(r.plan_id),
                    'plan_name': plan_map.get(str(r.plan_id), {}).get('name', 'Unknown'),
                    'plan_type': plan_map.get(str(r.plan_id), {}).get('type', 'unknown'),
                    'price': plan_map.get(str(r.plan_id), {}).get('price', 0),
                    'subscriber_count': r.count,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Plan distribution error: {e}", exc_info=True)
            raise

    # =========================================================================
    # USAGE / SESSION REPORTS
    # =========================================================================

    def get_usage_report(
        self,
        organization_id: UUID,
        period: str = 'monthly',
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Data usage and session statistics for a period."""
        try:
            from app.models.session import ActiveSession, RadiusAccounting

            s, e = self._date_range(period, start_date, end_date)

            # Session counts
            total_sessions = ActiveSession.query.filter(
                ActiveSession.organization_id == organization_id,
                ActiveSession.start_time >= s,
                ActiveSession.start_time < e,
            ).count()

            active_now = ActiveSession.query.filter_by(
                organization_id=organization_id, status='active'
            ).count()

            # Data usage from sessions
            usage = db.session.query(
                func.coalesce(func.sum(ActiveSession.bytes_in), 0).label('bytes_in'),
                func.coalesce(func.sum(ActiveSession.bytes_out), 0).label('bytes_out'),
                func.coalesce(func.sum(ActiveSession.session_time), 0).label('session_time'),
            ).filter(
                ActiveSession.organization_id == organization_id,
                ActiveSession.start_time >= s,
                ActiveSession.start_time < e,
            ).first()

            total_bytes = (usage.bytes_in or 0) + (usage.bytes_out or 0)

            return {
                'period': period,
                'start': s.isoformat(),
                'end': e.isoformat(),
                'total_sessions': total_sessions,
                'active_sessions_now': active_now,
                'total_bytes_in': usage.bytes_in or 0,
                'total_bytes_out': usage.bytes_out or 0,
                'total_bytes': total_bytes,
                'total_gb': round(total_bytes / (1024 ** 3), 3),
                'total_session_hours': round((usage.session_time or 0) / 3600, 2),
            }
        except Exception as e:
            logger.error(f"Usage report error: {e}", exc_info=True)
            raise

    # =========================================================================
    # PAYMENT METHOD DISTRIBUTION
    # =========================================================================

    def get_payment_method_distribution(
        self,
        organization_id: UUID,
        period: str = 'monthly',
    ) -> List[Dict[str, Any]]:
        """Payment method breakdown for charts."""
        try:
            from app.models.payment import Transaction

            s, e = self._date_range(period)

            rows = db.session.query(
                Transaction.payment_method,
                func.count(Transaction.id).label('count'),
                func.coalesce(func.sum(Transaction.amount), 0).label('total'),
            ).filter(
                Transaction.organization_id == organization_id,
                Transaction.status == 'success',
                Transaction.created_at >= s,
                Transaction.created_at < e,
            ).group_by(Transaction.payment_method).all()

            return [
                {'method': r.payment_method, 'count': r.count, 'total': float(r.total)}
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Payment method distribution error: {e}", exc_info=True)
            raise

    # =========================================================================
    # CHURN RATE
    # =========================================================================

    def get_churn_rate(
        self,
        organization_id: UUID,
        months: int = 6,
    ) -> List[Dict[str, Any]]:
        """Monthly churn rate for the last N months."""
        try:
            from app.models.billing import Subscription

            now = datetime.utcnow()
            result = []

            for i in range(months - 1, -1, -1):
                # Month boundaries
                target = now - timedelta(days=i * 30)
                m_start = target.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                if m_start.month == 12:
                    m_end = m_start.replace(year=m_start.year + 1, month=1)
                else:
                    m_end = m_start.replace(month=m_start.month + 1)

                # Active at start of month
                active_start = Subscription.query.filter(
                    Subscription.organization_id == organization_id,
                    Subscription.status == 'active',
                    Subscription.start_time < m_start,
                    Subscription.expiry_time > m_start,
                ).count()

                # Churned during month
                churned = Subscription.query.filter(
                    Subscription.organization_id == organization_id,
                    Subscription.status.in_(['expired', 'disconnected', 'cancelled']),
                    Subscription.cancelled_at >= m_start,
                    Subscription.cancelled_at < m_end,
                ).count()

                churn_rate = round((churned / active_start * 100), 2) if active_start > 0 else 0

                result.append({
                    'month': m_start.strftime('%Y-%m'),
                    'month_label': m_start.strftime('%b %Y'),
                    'active_start': active_start,
                    'churned': churned,
                    'churn_rate_pct': churn_rate,
                })

            return result
        except Exception as e:
            logger.error(f"Churn rate error: {e}", exc_info=True)
            raise

    # =========================================================================
    # ISP-WIDE REPORTS (super admin)
    # =========================================================================

    def get_isp_wide_overview(self) -> Dict[str, Any]:
        """Platform-wide statistics across all organizations."""
        try:
            from app.models.organization import Organization
            from app.models.subscriber import Subscriber
            from app.models.billing import Subscription
            from app.models.router import Router
            from app.models.payment import Transaction
            from app.models.session import ActiveSession

            now = datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            total_orgs = Organization.query.filter_by(is_active=True).count()
            total_subscribers = Subscriber.query.filter_by(status='active').count()
            total_routers = Router.query.filter_by(is_active=True).count()
            online_routers = Router.query.filter_by(is_active=True, status='online').count()
            active_sessions = ActiveSession.query.filter_by(status='active').count()

            active_subs = Subscription.query.filter(
                Subscription.status == 'active',
                Subscription.expiry_time > now,
            ).count()

            monthly_revenue = db.session.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            ).filter(
                Transaction.status == 'success',
                Transaction.created_at >= month_start,
            ).scalar() or 0

            # Per-org breakdown
            org_breakdown = db.session.query(
                Organization.id,
                Organization.name,
                func.count(distinct(Subscriber.id)).label('subscribers'),
            ).outerjoin(
                Subscriber, and_(
                    Subscriber.organization_id == Organization.id,
                    Subscriber.status == 'active',
                )
            ).filter(Organization.is_active == True).group_by(
                Organization.id, Organization.name
            ).all()

            return {
                'total_organizations': total_orgs,
                'total_active_subscribers': total_subscribers,
                'total_active_subscriptions': active_subs,
                'total_routers': total_routers,
                'online_routers': online_routers,
                'active_sessions': active_sessions,
                'monthly_revenue': float(monthly_revenue),
                'currency': 'KES',
                'organizations': [
                    {
                        'id': str(o.id),
                        'name': o.name,
                        'active_subscribers': o.subscribers,
                    }
                    for o in org_breakdown
                ],
                'generated_at': now.isoformat(),
            }
        except Exception as e:
            logger.error(f"ISP-wide overview error: {e}", exc_info=True)
            raise

    # =========================================================================
    # RECENT ACTIVITY
    # =========================================================================

    def get_recent_activity(
        self,
        organization_id: UUID,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Recent events: new subscribers, payments, expirations."""
        try:
            from app.models.subscriber import Subscriber
            from app.models.payment import Transaction
            from app.models.billing import Subscription

            now = datetime.utcnow()

            # Recent new subscribers
            new_subs = Subscriber.query.filter_by(
                organization_id=organization_id
            ).order_by(Subscriber.created_at.desc()).limit(limit).all()

            # Recent payments
            recent_payments = Transaction.query.filter(
                Transaction.organization_id == organization_id,
                Transaction.status == 'success',
            ).order_by(Transaction.created_at.desc()).limit(limit).all()

            # Recently expired subscriptions
            recently_expired = Subscription.query.filter(
                Subscription.organization_id == organization_id,
                Subscription.status.in_(['expired', 'disconnected']),
                Subscription.cancelled_at >= now - timedelta(days=7),
            ).order_by(Subscription.cancelled_at.desc()).limit(limit).all()

            return {
                'new_subscribers': [
                    {
                        'id': str(s.id),
                        'name': s.display_name if hasattr(s, 'display_name') else s.phone,
                        'type': s.subscriber_type,
                        'created_at': s.created_at.isoformat() if s.created_at else None,
                    }
                    for s in new_subs
                ],
                'recent_payments': [
                    {
                        'id': str(t.id),
                        'amount': float(t.amount),
                        'method': t.payment_method,
                        'reference': t.transaction_reference,
                        'created_at': t.created_at.isoformat() if t.created_at else None,
                    }
                    for t in recent_payments
                ],
                'recently_expired': [
                    {
                        'id': str(s.id),
                        'subscriber_id': str(s.subscriber_id),
                        'plan_id': str(s.plan_id),
                        'expired_at': s.cancelled_at.isoformat() if s.cancelled_at else None,
                    }
                    for s in recently_expired
                ],
                'generated_at': now.isoformat(),
            }
        except Exception as e:
            logger.error(f"Recent activity error: {e}", exc_info=True)
            raise

    # =========================================================================
    # BANDWIDTH USAGE TREND
    # =========================================================================

    def get_bandwidth_trend(
        self,
        organization_id: UUID,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Daily bandwidth usage for the last N days."""
        try:
            from app.models.session import ActiveSession
            from sqlalchemy import cast, Date

            start = datetime.utcnow() - timedelta(days=days)

            rows = db.session.query(
                cast(ActiveSession.start_time, Date).label('day'),
                func.coalesce(func.sum(ActiveSession.bytes_in), 0).label('bytes_in'),
                func.coalesce(func.sum(ActiveSession.bytes_out), 0).label('bytes_out'),
                func.count(ActiveSession.id).label('sessions'),
            ).filter(
                ActiveSession.organization_id == organization_id,
                ActiveSession.start_time >= start,
            ).group_by(cast(ActiveSession.start_time, Date)).order_by('day').all()

            return [
                {
                    'date': str(r.day),
                    'bytes_in': r.bytes_in,
                    'bytes_out': r.bytes_out,
                    'total_gb': round((r.bytes_in + r.bytes_out) / (1024 ** 3), 3),
                    'sessions': r.sessions,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Bandwidth trend error: {e}", exc_info=True)
            raise
