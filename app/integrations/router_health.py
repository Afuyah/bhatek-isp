"""
Router Health Check Utilities
==============================
Functions for checking MikroTik router connectivity and reporting errors.
Designed to be called from Celery tasks — no Flask request context required,
but an application context must be active (pushed by the task).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.logging.logger import logger


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_mikrotik_client():
    """Lazy-import MikroTikClient to avoid circular imports."""
    from app.integrations.mikrotik.client import MikroTikClient
    return MikroTikClient()


def _router_to_dict(router: Any) -> Dict[str, Any]:
    """Convert a Router model instance to the dict expected by MikroTikClient."""
    return {
        'id': str(router.id),
        'ip_address': str(router.ip_address) if router.ip_address else None,
        'api_port': router.api_port or 8728,
        'username': router.username,
        'password_encrypted': router.password_encrypted,
        'api_ssl': False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_router_connection(router: Any) -> Dict[str, Any]:
    """
    Test connectivity to a MikroTik router via the RouterOS API.

    Args:
        router: Router model instance.

    Returns:
        Dict with keys:
            - online (bool)
            - status ('online' | 'offline' | 'error')
            - response_time_ms (float | None)
            - error (str | None)
            - router_info (dict | None)  — populated when online
    """
    rd = _router_to_dict(router)

    if not rd.get('ip_address'):
        return {
            'online': False,
            'status': 'error',
            'response_time_ms': None,
            'error': 'Router has no IP address configured',
            'router_info': None,
        }

    try:
        client = _get_mikrotik_client()
        result = client.health_check(rd)

        if result.get('status') == 'healthy':
            return {
                'online': True,
                'status': 'online',
                'response_time_ms': result.get('response_time_ms'),
                'error': None,
                'router_info': {
                    'cpu_load': result.get('cpu_load'),
                    'uptime': result.get('uptime'),
                    'free_memory': result.get('free_memory'),
                    'total_memory': result.get('total_memory'),
                },
            }
        else:
            return {
                'online': False,
                'status': 'error',
                'response_time_ms': None,
                'error': result.get('error', 'Health check returned unhealthy'),
                'router_info': None,
            }

    except Exception as exc:
        error_msg = str(exc)
        logger.warning(
            f"Router health check failed for {router.name} "
            f"({router.ip_address}): {error_msg}"
        )
        return {
            'online': False,
            'status': 'offline' if 'timeout' in error_msg.lower() or
                                   'refused' in error_msg.lower() else 'error',
            'response_time_ms': None,
            'error': error_msg,
            'router_info': None,
        }


def get_router_status(router: Any) -> str:
    """
    Return the current status string for a router.

    Args:
        router: Router model instance.

    Returns:
        'online', 'offline', or 'error'
    """
    result = check_router_connection(router)
    return result['status']


def update_router_last_health_check(router: Any, status: str,
                                    error: Optional[str] = None) -> None:
    """
    Persist the health-check result back to the Router record.

    Args:
        router: Router model instance (will be mutated and committed).
        status: New status string ('online', 'offline', 'error').
        error: Optional error message to store in last_config_error.
    """
    try:
        from app.core.database.session import db

        router.status = status
        router.last_seen_at = datetime.utcnow() if status == 'online' else router.last_seen_at

        if error:
            router.last_config_error = error[:500]  # cap length
        elif status == 'online':
            router.last_config_error = None  # clear previous error on recovery

        db.session.commit()
        logger.debug(
            f"Updated router {router.name} health: status={status}"
        )
    except Exception as exc:
        logger.error(
            f"Failed to update router health check for {router.name}: {exc}",
            exc_info=True,
        )
        try:
            from app.core.database.session import db
            db.session.rollback()
        except Exception:
            pass


def send_router_error_email(router: Any, error_message: str) -> bool:
    """
    Send an admin email about a single router issue.

    Args:
        router: Router model instance.
        error_message: Human-readable description of the problem.

    Returns:
        True if the email was dispatched successfully.
    """
    try:
        admin_email = _get_admin_email(router)
        if not admin_email:
            logger.warning(
                f"No admin email found for router {router.name} — "
                "skipping error notification"
            )
            return False

        from app.integrations.email_service import send_router_error_email as _send
        return _send(
            admin_email=admin_email,
            router_list=[{
                'name': router.name,
                'ip_address': str(router.ip_address) if router.ip_address else 'N/A',
                'status': router.status,
                'error': error_message,
            }],
        )
    except Exception as exc:
        logger.error(
            f"Failed to send router error email for {router.name}: {exc}",
            exc_info=True,
        )
        return False


def _get_admin_email(router: Any) -> Optional[str]:
    """
    Resolve the admin email for a router's organization.

    Tries (in order):
        1. ADMIN_EMAIL env var
        2. Organization.email
        3. First admin User in the organization
    """
    # 1. Env override
    admin_email = os.environ.get('ADMIN_EMAIL', '')
    if admin_email:
        return admin_email

    # 2. Organization email
    try:
        if router.organization and router.organization.email:
            return router.organization.email
    except Exception:
        pass

    # 3. First admin user
    try:
        from app.models.auth import User
        admin = User.query.filter_by(
            organization_id=router.organization_id,
            is_active=True,
        ).first()
        if admin and admin.email:
            return admin.email
    except Exception:
        pass

    return None
