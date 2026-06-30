"""
Celery Background Tasks
=======================
All periodic and on-demand background tasks for the ISP management platform.

Task groups
-----------
Plan Management
    check_plan_expiration       — daily @ 02:00 UTC
    disconnect_expired_plans    — daily @ 03:00 UTC
    auto_renew_plans            — daily @ 04:00 UTC

Router Health
    sync_router_health          — every 30 minutes
    check_router_errors         — every hour

Session & RADIUS Cleanup
    cleanup_expired_sessions    — every hour
    cleanup_radius_cache        — every 2 hours
    expire_subscriptions_cascade — every 15 minutes (full cascade: RADIUS + sessions + MikroTik)

Billing Reports
    generate_daily_billing_report   — daily  @ 23:59 UTC
    generate_weekly_billing_report  — Monday @ 23:59 UTC
    generate_monthly_billing_report — 1st of month @ 23:59 UTC

Usage
-----
    # Start worker
    celery -A app.celery_app worker --loglevel=info

    # Start beat scheduler
    celery -A app.celery_app beat --loglevel=info
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


# ===========================================================================
# HELPERS
# ===========================================================================

def _get_admin_email() -> str:
    """Return the platform-wide admin email from config / env."""
    try:
        from flask import current_app
        return current_app.config.get('ADMIN_EMAIL', os.environ.get('ADMIN_EMAIL', ''))
    except RuntimeError:
        return os.environ.get('ADMIN_EMAIL', '')


# ===========================================================================
# PLAN MANAGEMENT TASKS
# ===========================================================================

@shared_task(
    bind=True,
    name='app.tasks.check_plan_expiration',
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
)
def check_plan_expiration(self) -> Dict[str, Any]:
    """
    Find subscriptions expiring in exactly 5 days and send reminder emails.

    Runs daily at 02:00 UTC.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.models.billing import Subscription
            from app.models.subscriber import Subscriber
            from app.integrations.email_service import send_expiration_reminder_email

            now = datetime.utcnow()
            window_start = now + timedelta(days=4, hours=23)   # ~5 days from now
            window_end = now + timedelta(days=5, hours=1)

            subscriptions = Subscription.query.filter(
                Subscription.status == 'active',
                Subscription.expiry_time >= window_start,
                Subscription.expiry_time <= window_end,
            ).all()

            sent = 0
            failed = 0

            for sub in subscriptions:
                try:
                    subscriber = Subscriber.query.get(sub.subscriber_id)
                    if subscriber and subscriber.email:
                        days_remaining = sub.days_remaining()
                        success = send_expiration_reminder_email(
                            subscriber, days_remaining
                        )
                        if success:
                            sent += 1
                        else:
                            failed += 1
                except Exception as exc:
                    logger.warning(
                        f"Failed to send reminder for subscription {sub.id}: {exc}"
                    )
                    failed += 1

            result = {
                'checked': len(subscriptions),
                'reminders_sent': sent,
                'failed': failed,
                'timestamp': now.isoformat(),
            }
            logger.info(f"check_plan_expiration completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"check_plan_expiration failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.send_expiration_reminder',
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_expiration_reminder(self, subscriber_id: str, days_remaining: int) -> bool:
    """
    Send a single expiration reminder email.

    Called by check_plan_expiration for each expiring subscription.
    Can also be triggered manually for a specific subscriber.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.models.subscriber import Subscriber
            from app.integrations.email_service import send_expiration_reminder_email

            subscriber = Subscriber.query.get(subscriber_id)
            if not subscriber:
                logger.warning(f"Subscriber {subscriber_id} not found")
                return False

            return send_expiration_reminder_email(subscriber, days_remaining)

        except Exception as exc:
            logger.error(f"send_expiration_reminder failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.disconnect_expired_plans',
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
)
def disconnect_expired_plans(self) -> Dict[str, Any]:
    """
    Disconnect all plans that have passed their expiry_time.

    Steps:
        1. Find active subscriptions with expiry_time <= now
        2. Set status = 'disconnected'
        3. Call MikroTik API to disconnect the subscriber
        4. Remove RADIUS entries

    Runs daily at 03:00 UTC.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.core.database.session import db
            from app.models.billing import Subscription
            from app.models.subscriber import Subscriber
            from app.integrations.mikrotik.client import MikroTikClient
            from app.integrations.radius.radius_sync_service import RadiusSyncService

            now = datetime.utcnow()

            expired_subs = Subscription.query.filter(
                Subscription.status == 'active',
                Subscription.expiry_time <= now,
            ).all()

            mikrotik = MikroTikClient()
            radius_sync = RadiusSyncService()

            disconnected = 0
            errors = 0

            for sub in expired_subs:
                try:
                    subscriber = Subscriber.query.get(sub.subscriber_id)

                    # 1. Update subscription status
                    sub.status = 'disconnected'
                    sub.cancelled_at = now
                    sub.cancellation_reason = 'expired'

                    # 2. Remove from RADIUS
                    if subscriber:
                        try:
                            radius_sync.remove_subscriber_from_radius(subscriber)
                        except Exception as radius_exc:
                            logger.warning(
                                f"RADIUS removal failed for {subscriber.id}: {radius_exc}"
                            )

                        # 3. Disconnect from MikroTik (best-effort)
                        try:
                            _disconnect_from_mikrotik(mikrotik, subscriber)
                        except Exception as mt_exc:
                            logger.warning(
                                f"MikroTik disconnect failed for {subscriber.id}: {mt_exc}"
                            )

                        # 4. Terminate active sessions
                        try:
                            from app.models.session import ActiveSession
                            from app.integrations.radius.radius_cache import RadiusCache
                            active_sessions = ActiveSession.query.filter_by(
                                subscriber_id=subscriber.id,
                                status='active',
                            ).all()
                            for sess in active_sessions:
                                sess.status = 'expired'
                                sess.termination_cause = 'subscription_expired'
                                if sess.session_id:
                                    RadiusCache.delete_session(sess.session_id)
                        except Exception as sess_exc:
                            logger.warning(
                                f"Session termination failed for {subscriber.id}: {sess_exc}"
                            )

                        # 5. Invalidate RADIUS cache
                        try:
                            from app.integrations.radius.radius_cache import RadiusCache
                            username = (
                                subscriber.phone
                                if subscriber.subscriber_type == 'hotspot'
                                else subscriber.username
                            )
                            if username:
                                RadiusCache.delete_auth_data(
                                    username, str(subscriber.organization_id)
                                )
                        except Exception:
                            pass

                    db.session.commit()
                    disconnected += 1

                except Exception as exc:
                    db.session.rollback()
                    logger.error(
                        f"Failed to disconnect subscription {sub.id}: {exc}",
                        exc_info=True,
                    )
                    errors += 1

            result = {
                'expired_found': len(expired_subs),
                'disconnected': disconnected,
                'errors': errors,
                'timestamp': now.isoformat(),
            }
            logger.info(f"disconnect_expired_plans completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"disconnect_expired_plans failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


def _disconnect_from_mikrotik(mikrotik_client: Any, subscriber: Any) -> None:
    """
    Best-effort MikroTik disconnect for a subscriber across all their routers.

    Tries hotspot disconnect first, then PPPoE.
    """
    from app.models.router import Router

    routers = Router.query.filter_by(
        organization_id=subscriber.organization_id,
        is_active=True,
    ).all()

    login_name = subscriber.login_username

    for router in routers:
        rd = {
            'id': str(router.id),
            'ip_address': str(router.ip_address) if router.ip_address else None,
            'api_port': router.api_port or 8728,
            'username': router.username,
            'password_encrypted': router.password_encrypted,
        }
        try:
            if subscriber.subscriber_type == 'hotspot':
                mikrotik_client.disconnect_hotspot_user(rd, login_name)
            else:
                mikrotik_client.disconnect_pppoe_user(rd, login_name)
        except Exception:
            pass  # Best-effort; RADIUS removal is the authoritative disconnect


@shared_task(
    bind=True,
    name='app.tasks.auto_renew_plans',
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
)
def auto_renew_plans(self) -> Dict[str, Any]:
    """
    Auto-renew subscriptions where auto_renew=True and expiry is within 24 hours.

    Creates a new subscription starting from the current expiry time so there
    is no gap in service.

    Runs daily at 04:00 UTC.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.core.database.session import db
            from app.models.billing import Subscription
            from app.models.subscriber import Subscriber

            now = datetime.utcnow()
            renewal_window = now + timedelta(hours=24)

            # Find active subscriptions with auto_renew=True expiring within 24 h
            candidates = Subscription.query.filter(
                Subscription.status == 'active',
                Subscription.auto_renew == True,  # noqa: E712
                Subscription.expiry_time <= renewal_window,
                Subscription.expiry_time > now,
            ).all()

            renewed = 0
            skipped = 0
            errors = 0

            for sub in candidates:
                try:
                    plan = sub.plan
                    if not plan or not plan.is_active:
                        logger.warning(
                            f"Plan {sub.plan_id} inactive — skipping auto-renew "
                            f"for subscription {sub.id}"
                        )
                        skipped += 1
                        continue

                    # Extend from current expiry (no gap)
                    new_expiry = sub.expiry_time + plan.validity_timedelta

                    sub.expiry_time = new_expiry
                    sub.status = 'active'
                    sub.cancelled_at = None
                    sub.cancellation_reason = None

                    db.session.commit()

                    # Sync updated expiry to RADIUS
                    try:
                        from app.integrations.radius.radius_sync_service import (
                            RadiusSyncService,
                        )
                        from app.integrations.radius.radius_cache import RadiusCache
                        subscriber = Subscriber.query.get(sub.subscriber_id)
                        if subscriber:
                            RadiusSyncService().update_subscription_in_radius(
                                subscriber, sub, plan
                            )
                            # Invalidate auth cache so next auth picks up new expiry
                            username = (
                                subscriber.phone
                                if subscriber.subscriber_type == 'hotspot'
                                else subscriber.username
                            )
                            if username:
                                RadiusCache.delete_auth_data(
                                    username, str(subscriber.organization_id)
                                )
                    except Exception as radius_exc:
                        logger.warning(
                            f"RADIUS update failed on auto-renew for {sub.id}: "
                            f"{radius_exc}"
                        )

                    renewed += 1
                    logger.info(
                        f"Auto-renewed subscription {sub.id} until "
                        f"{new_expiry.isoformat()}"
                    )

                except Exception as exc:
                    db.session.rollback()
                    logger.error(
                        f"Auto-renew failed for subscription {sub.id}: {exc}",
                        exc_info=True,
                    )
                    errors += 1

            result = {
                'candidates': len(candidates),
                'renewed': renewed,
                'skipped': skipped,
                'errors': errors,
                'timestamp': now.isoformat(),
            }
            logger.info(f"auto_renew_plans completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"auto_renew_plans failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


# ===========================================================================
# ROUTER HEALTH TASKS
# ===========================================================================

@shared_task(
    bind=True,
    name='app.tasks.sync_router_health',
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def sync_router_health(self) -> Dict[str, Any]:
    """
    Check every active router's connection status via the MikroTik API
    and update router.status + router.last_seen_at in the database.

    Runs every 30 minutes.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.models.router import Router
            from app.integrations.router_health import (
                check_router_connection,
                update_router_last_health_check,
            )

            routers = Router.query.filter_by(is_active=True).all()

            online = 0
            offline = 0
            errors = 0

            for router in routers:
                try:
                    result = check_router_connection(router)
                    update_router_last_health_check(
                        router,
                        status=result['status'],
                        error=result.get('error'),
                    )
                    if result['status'] == 'online':
                        online += 1
                    elif result['status'] == 'offline':
                        offline += 1
                    else:
                        errors += 1
                except Exception as exc:
                    logger.warning(
                        f"Health sync failed for router {router.name}: {exc}"
                    )
                    errors += 1

            result = {
                'total': len(routers),
                'online': online,
                'offline': offline,
                'errors': errors,
                'timestamp': datetime.utcnow().isoformat(),
            }
            logger.info(f"sync_router_health completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"sync_router_health failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.check_router_errors',
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def check_router_errors(self) -> Dict[str, Any]:
    """
    Identify routers with 'offline' or 'error' status and send an admin
    email listing all problematic routers.

    Runs every hour.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.models.router import Router
            from app.integrations.email_service import send_router_error_email

            problem_routers = Router.query.filter(
                Router.is_active == True,  # noqa: E712
                Router.status.in_(['offline', 'error', 'unknown']),
            ).all()

            if not problem_routers:
                logger.info("check_router_errors: all routers healthy")
                return {
                    'problematic': 0,
                    'email_sent': False,
                    'timestamp': datetime.utcnow().isoformat(),
                }

            router_list = []
            for r in problem_routers:
                router_list.append({
                    'name': r.name,
                    'ip_address': str(r.ip_address) if r.ip_address else 'N/A',
                    'status': r.status,
                    'error': r.last_config_error or '',
                })

            admin_email = _get_admin_email()
            email_sent = False

            if admin_email:
                email_sent = send_router_error_email(admin_email, router_list)
            else:
                logger.warning(
                    "check_router_errors: ADMIN_EMAIL not configured — "
                    "skipping notification"
                )

            result = {
                'problematic': len(problem_routers),
                'email_sent': email_sent,
                'routers': [r['name'] for r in router_list],
                'timestamp': datetime.utcnow().isoformat(),
            }
            logger.info(f"check_router_errors completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"check_router_errors failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


# ===========================================================================
# BILLING REPORT TASKS
# ===========================================================================

def _build_billing_report(start_date: datetime, end_date: datetime,
                           period: str) -> Dict[str, Any]:
    """
    Aggregate billing data between start_date and end_date.

    Returns a report dict suitable for send_billing_report_email().
    """
    from app.models.billing import Invoice, Subscription, Voucher
    from app.core.database.session import db
    from sqlalchemy import func

    # Revenue from paid invoices
    revenue_row = db.session.query(
        func.coalesce(func.sum(Invoice.total), 0)
    ).filter(
        Invoice.status == 'paid',
        Invoice.paid_at >= start_date,
        Invoice.paid_at < end_date,
    ).scalar()
    total_revenue = float(revenue_row or 0)

    # Invoice counts
    invoice_count = Invoice.query.filter(
        Invoice.issue_date >= start_date,
        Invoice.issue_date < end_date,
    ).count()

    paid_count = Invoice.query.filter(
        Invoice.status == 'paid',
        Invoice.paid_at >= start_date,
        Invoice.paid_at < end_date,
    ).count()

    # New subscriptions
    new_subs = Subscription.query.filter(
        Subscription.start_time >= start_date,
        Subscription.start_time < end_date,
    ).count()

    # Renewals (subscriptions with invoice_type='renewal' in the period)
    renewals = Invoice.query.filter(
        Invoice.invoice_type == 'renewal',
        Invoice.issue_date >= start_date,
        Invoice.issue_date < end_date,
    ).count()

    # Expired subscriptions
    expired = Subscription.query.filter(
        Subscription.status.in_(['expired', 'disconnected']),
        Subscription.cancelled_at >= start_date,
        Subscription.cancelled_at < end_date,
    ).count()

    # Vouchers redeemed
    vouchers_redeemed = Voucher.query.filter(
        Voucher.status == 'used',
        Voucher.used_at >= start_date,
        Voucher.used_at < end_date,
    ).count()

    period_fmt = {
        'daily': '%Y-%m-%d',
        'weekly': '%Y-%m-%d',
        'monthly': '%B %Y',
    }.get(period, '%Y-%m-%d')

    period_range = (
        f"{start_date.strftime(period_fmt)}"
        if period == 'monthly'
        else f"{start_date.strftime('%Y-%m-%d')} – {(end_date - timedelta(seconds=1)).strftime('%Y-%m-%d')}"
    )

    return {
        'period': period,
        'period_range': period_range,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'total_revenue': total_revenue,
        'invoice_count': invoice_count,
        'paid_count': paid_count,
        'new_subscriptions': new_subs,
        'renewals': renewals,
        'expired_subscriptions': expired,
        'vouchers_redeemed': vouchers_redeemed,
    }


@shared_task(
    bind=True,
    name='app.tasks.generate_daily_billing_report',
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def generate_daily_billing_report(self) -> Dict[str, Any]:
    """
    Aggregate billing data for the current day and email the admin.

    Runs daily at 23:59 UTC.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.integrations.email_service import send_billing_report_email

            now = datetime.utcnow()
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)

            report = _build_billing_report(start, end, 'daily')

            admin_email = _get_admin_email()
            email_sent = False
            if admin_email:
                email_sent = send_billing_report_email(admin_email, report, 'daily')

            report['email_sent'] = email_sent
            logger.info(f"generate_daily_billing_report completed: {report}")
            return report

        except Exception as exc:
            logger.error(f"generate_daily_billing_report failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.generate_weekly_billing_report',
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def generate_weekly_billing_report(self) -> Dict[str, Any]:
    """
    Aggregate billing data for the current ISO week and email the admin.

    Runs every Monday at 23:59 UTC.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.integrations.email_service import send_billing_report_email

            now = datetime.utcnow()
            # Monday of the current week
            start = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end = start + timedelta(weeks=1)

            report = _build_billing_report(start, end, 'weekly')

            admin_email = _get_admin_email()
            email_sent = False
            if admin_email:
                email_sent = send_billing_report_email(admin_email, report, 'weekly')

            report['email_sent'] = email_sent
            logger.info(f"generate_weekly_billing_report completed: {report}")
            return report

        except Exception as exc:
            logger.error(f"generate_weekly_billing_report failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.cleanup_expired_sessions',
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def cleanup_expired_sessions(self) -> Dict[str, Any]:
    """
    Remove sessions that have passed their expiry_time and are still
    marked 'active'.  Also purges stale RADIUS cache entries.

    Runs every hour.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.core.database.session import db
            from app.models.session import ActiveSession
            from datetime import datetime

            now = datetime.utcnow()

            # Find expired-but-still-active sessions
            expired = ActiveSession.query.filter(
                ActiveSession.status == 'active',
                ActiveSession.expiry_time <= now,
            ).all()

            cleaned = 0
            errors = 0

            for sess in expired:
                try:
                    sess.status = 'expired'
                    sess.termination_cause = 'subscription_expired'
                    db.session.commit()

                    # Purge from RADIUS cache
                    try:
                        from app.integrations.radius.radius_cache import RadiusCache
                        RadiusCache.delete_session(sess.session_id)
                    except Exception:
                        pass

                    cleaned += 1
                except Exception as exc:
                    db.session.rollback()
                    logger.warning(f"Session cleanup failed for {sess.id}: {exc}")
                    errors += 1

            result = {
                'expired_found': len(expired),
                'cleaned': cleaned,
                'errors': errors,
                'timestamp': now.isoformat(),
            }
            logger.info(f"cleanup_expired_sessions completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"cleanup_expired_sessions failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.cleanup_radius_cache',
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def cleanup_radius_cache(self) -> Dict[str, Any]:
    """
    Invalidate RADIUS auth cache for subscribers whose subscriptions
    have expired.  Ensures FreeRADIUS re-checks the database on next
    auth attempt rather than serving a stale cached ACCEPT.

    Runs every 2 hours.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.models.billing import Subscription
            from app.models.subscriber import Subscriber
            from app.integrations.radius.radius_cache import RadiusCache
            from datetime import datetime

            now = datetime.utcnow()

            # Subscriptions that expired in the last 2 hours (cache may still be warm)
            recently_expired = Subscription.query.filter(
                Subscription.status.in_(['expired', 'disconnected', 'cancelled']),
                Subscription.expiry_time >= now - timedelta(hours=2),
                Subscription.expiry_time <= now,
            ).all()

            invalidated = 0
            for sub in recently_expired:
                try:
                    subscriber = Subscriber.query.get(sub.subscriber_id)
                    if subscriber:
                        username = (
                            subscriber.phone
                            if subscriber.subscriber_type == 'hotspot'
                            else subscriber.username
                        )
                        if username:
                            RadiusCache.delete_auth_data(
                                username, str(subscriber.organization_id)
                            )
                            invalidated += 1
                except Exception as exc:
                    logger.warning(f"Cache invalidation failed for sub {sub.id}: {exc}")

            result = {
                'recently_expired': len(recently_expired),
                'cache_invalidated': invalidated,
                'timestamp': now.isoformat(),
            }
            logger.info(f"cleanup_radius_cache completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"cleanup_radius_cache failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.expire_subscriptions_cascade',
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
)
def expire_subscriptions_cascade(self) -> Dict[str, Any]:
    """
    Full cascade expiry: for each expired active subscription —
        1. Mark subscription as 'expired'
        2. Remove from RADIUS (radcheck / radreply / radusergroup)
        3. Terminate active sessions
        4. Disconnect from MikroTik (best-effort)
        5. Invalidate RADIUS cache
        6. Send expiry notification email

    Runs every 15 minutes to catch near-real-time expirations.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.core.database.session import db
            from app.models.billing import Subscription
            from app.models.subscriber import Subscriber
            from app.models.session import ActiveSession
            from app.integrations.radius.radius_sync_service import RadiusSyncService
            from app.integrations.radius.radius_cache import RadiusCache
            from app.integrations.mikrotik.client import MikroTikClient

            now = datetime.utcnow()

            expired_subs = Subscription.query.filter(
                Subscription.status == 'active',
                Subscription.expiry_time <= now,
            ).all()

            radius_sync = RadiusSyncService()
            mikrotik = MikroTikClient()
            processed = 0
            errors = 0

            for sub in expired_subs:
                try:
                    subscriber = Subscriber.query.get(sub.subscriber_id)

                    # 1. Mark expired
                    sub.status = 'expired'
                    sub.cancelled_at = now
                    sub.cancellation_reason = 'expired'
                    db.session.commit()

                    if subscriber:
                        username = (
                            subscriber.phone
                            if subscriber.subscriber_type == 'hotspot'
                            else subscriber.username
                        )

                        # 2. Remove from RADIUS
                        try:
                            radius_sync.remove_subscriber_from_radius(subscriber)
                        except Exception as re:
                            logger.warning(f"RADIUS removal failed for {subscriber.id}: {re}")

                        # 3. Terminate active sessions
                        try:
                            active_sessions = ActiveSession.query.filter_by(
                                subscriber_id=subscriber.id,
                                status='active',
                            ).all()
                            for sess in active_sessions:
                                sess.status = 'expired'
                                sess.termination_cause = 'subscription_expired'
                                if sess.session_id:
                                    RadiusCache.delete_session(sess.session_id)
                            db.session.commit()
                        except Exception as se:
                            logger.warning(f"Session termination failed for {subscriber.id}: {se}")

                        # 4. Disconnect from MikroTik (best-effort)
                        try:
                            _disconnect_from_mikrotik(mikrotik, subscriber)
                        except Exception:
                            pass

                        # 5. Invalidate RADIUS cache
                        try:
                            if username:
                                RadiusCache.delete_auth_data(
                                    username, str(subscriber.organization_id)
                                )
                        except Exception:
                            pass

                        # 6. Send expiry notification
                        try:
                            from app.integrations.email_service import send_expiration_reminder_email
                            if subscriber.email:
                                send_expiration_reminder_email(subscriber, 0)
                        except Exception:
                            pass

                    processed += 1

                except Exception as exc:
                    db.session.rollback()
                    logger.error(f"Cascade expiry failed for sub {sub.id}: {exc}", exc_info=True)
                    errors += 1

            result = {
                'expired_found': len(expired_subs),
                'processed': processed,
                'errors': errors,
                'timestamp': now.isoformat(),
            }
            logger.info(f"expire_subscriptions_cascade completed: {result}")
            return result

        except Exception as exc:
            logger.error(f"expire_subscriptions_cascade failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name='app.tasks.generate_monthly_billing_report',
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def generate_monthly_billing_report(self) -> Dict[str, Any]:
    """
    Aggregate billing data for the current calendar month and email the admin.

    Runs on the 1st of each month at 23:59 UTC.
    """
    from app import create_app
    app = create_app()

    with app.app_context():
        try:
            from app.integrations.email_service import send_billing_report_email

            now = datetime.utcnow()
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # First day of next month
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)

            report = _build_billing_report(start, end, 'monthly')

            admin_email = _get_admin_email()
            email_sent = False
            if admin_email:
                email_sent = send_billing_report_email(admin_email, report, 'monthly')

            report['email_sent'] = email_sent
            logger.info(f"generate_monthly_billing_report completed: {report}")
            return report

        except Exception as exc:
            logger.error(f"generate_monthly_billing_report failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)
