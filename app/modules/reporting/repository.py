"""
Reporting Repository
====================
Direct database queries for reporting.
Most aggregation is done in ReportingService using SQLAlchemy directly,
but this module provides reusable query helpers.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func
from app.core.database.session import db
from app.core.logging.logger import logger


class ReportingRepository:
    """Reusable query helpers for reporting."""

    def get_revenue_by_day(
        self,
        organization_id: UUID,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        """Daily revenue aggregation."""
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
            logger.error(f"Revenue by day query error: {e}", exc_info=True)
            return []

    def get_subscriber_count_by_day(
        self,
        organization_id: UUID,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        """Daily new subscriber count."""
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
            logger.error(f"Subscriber count by day query error: {e}", exc_info=True)
            return []

