"""
Billing Period Service
======================
Provides invoice and subscription statistics filtered by daily, weekly,
and monthly billing periods.  Used by both the API routes and the Celery
billing-report tasks.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func

from app.core.database.session import db
from app.core.logging.logger import logger
from app.models.billing import Invoice, Subscription, Voucher


class BillingPeriodService:
    """
    Billing analytics service with period-based filtering.

    Supported periods
    -----------------
    daily   — a single calendar day
    weekly  — an ISO week (Monday–Sunday)
    monthly — a calendar month
    """

    # ------------------------------------------------------------------
    # Period resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_daily(date: Optional[datetime] = None) -> Tuple[datetime, datetime]:
        """Return (start, end) for a single day (UTC midnight boundaries)."""
        if date is None:
            date = datetime.utcnow()
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end

    @staticmethod
    def _resolve_weekly(
        week: Optional[int] = None,
        year: Optional[int] = None,
    ) -> Tuple[datetime, datetime]:
        """
        Return (start, end) for an ISO week.

        If week/year are omitted the current week is used.
        """
        now = datetime.utcnow()
        if week is None or year is None:
            # Monday of the current week
            start = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            # ISO week: find the Monday of week `week` in `year`
            jan4 = datetime(year, 1, 4)  # Jan 4 is always in week 1
            week1_monday = jan4 - timedelta(days=jan4.weekday())
            start = week1_monday + timedelta(weeks=week - 1)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)

        end = start + timedelta(weeks=1)
        return start, end

    @staticmethod
    def _resolve_monthly(
        month: Optional[int] = None,
        year: Optional[int] = None,
    ) -> Tuple[datetime, datetime]:
        """Return (start, end) for a calendar month."""
        now = datetime.utcnow()
        if month is None:
            month = now.month
        if year is None:
            year = now.year

        start = datetime(year, month, 1, 0, 0, 0)
        _, last_day = calendar.monthrange(year, month)
        end = datetime(year, month, last_day, 23, 59, 59) + timedelta(seconds=1)
        return start, end

    def _get_period_bounds(
        self,
        period: str,
        date: Optional[datetime] = None,
        week: Optional[int] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> Tuple[datetime, datetime]:
        """Dispatch to the correct resolver based on period string."""
        if period == 'daily':
            return self._resolve_daily(date)
        elif period == 'weekly':
            return self._resolve_weekly(week, year)
        elif period == 'monthly':
            return self._resolve_monthly(month, year)
        else:
            raise ValueError(
                f"Invalid period '{period}'. Use 'daily', 'weekly', or 'monthly'."
            )

    # ------------------------------------------------------------------
    # Invoice queries
    # ------------------------------------------------------------------

    def get_invoices_by_period(
        self,
        period: str = 'daily',
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        organization_id: Optional[Any] = None,
        week: Optional[int] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        date: Optional[datetime] = None,
    ) -> List[Invoice]:
        """
        Return invoices whose issue_date falls within the specified period.

        Args:
            period:          'daily', 'weekly', or 'monthly'.
            start_date:      Override period start (UTC datetime).
            end_date:        Override period end (UTC datetime).
            organization_id: Filter to a single tenant (optional).
            week:            ISO week number (weekly period only).
            year:            Year (weekly / monthly periods).
            month:           Month number 1–12 (monthly period only).
            date:            Specific date (daily period only).

        Returns:
            List of Invoice model instances ordered by issue_date desc.
        """
        try:
            if start_date and end_date:
                s, e = start_date, end_date
            else:
                s, e = self._get_period_bounds(period, date, week, year, month)

            filters = [
                Invoice.issue_date >= s,
                Invoice.issue_date < e,
            ]
            if organization_id:
                filters.append(Invoice.organization_id == organization_id)

            return (
                Invoice.query.filter(and_(*filters))
                .order_by(Invoice.issue_date.desc())
                .all()
            )
        except Exception as exc:
            logger.error(f"get_invoices_by_period error: {exc}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Revenue aggregation
    # ------------------------------------------------------------------

    def get_revenue_by_period(
        self,
        period: str = 'daily',
        organization_id: Optional[Any] = None,
        week: Optional[int] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Calculate revenue (sum of paid invoice totals) for the period.

        Returns:
            Dict with keys: period, start, end, total_revenue, invoice_count,
            paid_count, pending_count, currency.
        """
        try:
            s, e = self._get_period_bounds(period, date, week, year, month)

            base_filters = [
                Invoice.issue_date >= s,
                Invoice.issue_date < e,
            ]
            if organization_id:
                base_filters.append(Invoice.organization_id == organization_id)

            # Total revenue from paid invoices
            revenue = db.session.query(
                func.coalesce(func.sum(Invoice.total), 0)
            ).filter(
                and_(*base_filters, Invoice.status == 'paid')
            ).scalar()

            invoice_count = Invoice.query.filter(and_(*base_filters)).count()
            paid_count = Invoice.query.filter(
                and_(*base_filters, Invoice.status == 'paid')
            ).count()
            pending_count = Invoice.query.filter(
                and_(*base_filters, Invoice.status.in_(['draft', 'sent']))
            ).count()

            return {
                'period': period,
                'start': s.isoformat(),
                'end': e.isoformat(),
                'total_revenue': float(revenue or 0),
                'invoice_count': invoice_count,
                'paid_count': paid_count,
                'pending_count': pending_count,
                'currency': 'KES',
            }
        except Exception as exc:
            logger.error(f"get_revenue_by_period error: {exc}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Subscription statistics
    # ------------------------------------------------------------------

    def get_subscription_stats_by_period(
        self,
        period: str = 'daily',
        organization_id: Optional[Any] = None,
        week: Optional[int] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Return subscription statistics for the period.

        Counts:
            - new_subscriptions  (start_time in period)
            - expired            (cancelled_at in period, status expired/disconnected)
            - active_at_end      (active at the end of the period)
            - auto_renew_enabled (active subs with auto_renew=True)

        Returns:
            Dict with the above keys plus period metadata.
        """
        try:
            s, e = self._get_period_bounds(period, date, week, year, month)

            org_filter = (
                [Subscription.organization_id == organization_id]
                if organization_id else []
            )

            new_subs = Subscription.query.filter(
                and_(
                    *org_filter,
                    Subscription.start_time >= s,
                    Subscription.start_time < e,
                )
            ).count()

            expired = Subscription.query.filter(
                and_(
                    *org_filter,
                    Subscription.status.in_(['expired', 'disconnected']),
                    Subscription.cancelled_at >= s,
                    Subscription.cancelled_at < e,
                )
            ).count()

            active_at_end = Subscription.query.filter(
                and_(
                    *org_filter,
                    Subscription.status == 'active',
                    Subscription.expiry_time > e,
                )
            ).count()

            auto_renew_enabled = Subscription.query.filter(
                and_(
                    *org_filter,
                    Subscription.status == 'active',
                    Subscription.auto_renew == True,  # noqa: E712
                )
            ).count()

            return {
                'period': period,
                'start': s.isoformat(),
                'end': e.isoformat(),
                'new_subscriptions': new_subs,
                'expired_subscriptions': expired,
                'active_at_period_end': active_at_end,
                'auto_renew_enabled': auto_renew_enabled,
            }
        except Exception as exc:
            logger.error(
                f"get_subscription_stats_by_period error: {exc}", exc_info=True
            )
            raise

    # ------------------------------------------------------------------
    # Voucher statistics
    # ------------------------------------------------------------------

    def get_voucher_stats_by_period(
        self,
        period: str = 'daily',
        organization_id: Optional[Any] = None,
        week: Optional[int] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Return voucher redemption statistics for the period.

        Counts:
            - redeemed   (used_at in period)
            - expired    (expires_at in period, status expired)
            - created    (created_at in period)

        Returns:
            Dict with the above keys plus period metadata.
        """
        try:
            s, e = self._get_period_bounds(period, date, week, year, month)

            org_filter = (
                [Voucher.organization_id == organization_id]
                if organization_id else []
            )

            redeemed = Voucher.query.filter(
                and_(
                    *org_filter,
                    Voucher.status == 'used',
                    Voucher.used_at >= s,
                    Voucher.used_at < e,
                )
            ).count()

            expired = Voucher.query.filter(
                and_(
                    *org_filter,
                    Voucher.status == 'expired',
                    Voucher.expires_at >= s,
                    Voucher.expires_at < e,
                )
            ).count()

            created = Voucher.query.filter(
                and_(
                    *org_filter,
                    Voucher.created_at >= s,
                    Voucher.created_at < e,
                )
            ).count()

            # Revenue from redeemed vouchers in the period
            revenue = db.session.query(
                func.coalesce(func.sum(Voucher.price_paid), 0)
            ).filter(
                and_(
                    *org_filter,
                    Voucher.status == 'used',
                    Voucher.used_at >= s,
                    Voucher.used_at < e,
                )
            ).scalar()

            return {
                'period': period,
                'start': s.isoformat(),
                'end': e.isoformat(),
                'vouchers_redeemed': redeemed,
                'vouchers_expired': expired,
                'vouchers_created': created,
                'voucher_revenue': float(revenue or 0),
                'currency': 'KES',
            }
        except Exception as exc:
            logger.error(
                f"get_voucher_stats_by_period error: {exc}", exc_info=True
            )
            raise
