"""
Email Service Utilities
=======================
Standalone email helpers for Celery tasks (plan expiration reminders,
router error alerts, billing reports).  These functions are intentionally
decoupled from Flask's request context so they can be called safely from
background workers.
"""

from __future__ import annotations

from typing import Any, Dict, List


from app.core.logging.logger import logger
from app.integrations.email.transport import send as send_email


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------









# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_expiration_reminder_email(subscriber: Any, days_remaining: int) -> bool:
    """
    Send a plan-expiration reminder to a subscriber.

    Args:
        subscriber: Subscriber model instance (needs .email, .display_name).
        days_remaining: Number of days until the subscription expires.

    Returns:
        True if the email was dispatched successfully.
    """
    to_email = subscriber.email
    if not to_email:
        logger.warning(
            f"Subscriber {subscriber.id} has no email — skipping reminder"
        )
        return False

    name = subscriber.display_name
    subject = f"⚠️ Your internet plan expires in {days_remaining} day(s)"

    urgency_color = "#ef4444" if days_remaining <= 2 else "#f59e0b"
    urgency_label = "URGENT" if days_remaining <= 2 else "Reminder"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Plan Expiration Reminder</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   background-color: #f7fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
            .header {{ background: {urgency_color}; padding: 32px 24px;
                       text-align: center; border-radius: 12px 12px 0 0; }}
            .header h1 {{ color: white; margin: 0; font-size: 22px; font-weight: 700; }}
            .content {{ background: white; padding: 32px 24px;
                        border-radius: 0 0 12px 12px;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
            .days-badge {{ display: inline-block; background: {urgency_color};
                           color: white; font-size: 36px; font-weight: 800;
                           padding: 12px 24px; border-radius: 8px; margin: 16px 0; }}
            .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚠️ Plan Expiration {urgency_label}</h1>
            </div>
            <div class="content">
                <p>Hello <strong>{name}</strong>,</p>
                <p>Your internet plan is expiring soon. Please renew to avoid service interruption.</p>
                <div style="text-align: center;">
                    <div class="days-badge">{days_remaining} day(s) left</div>
                </div>
                <p>Contact your ISP provider or visit the portal to renew your plan before it expires.</p>
                <p style="color: #718096; font-size: 13px;">
                    If you have already renewed, please disregard this message.
                </p>
            </div>
            <div class="footer">
                <p>&copy; 2024 Bhatek Solution. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

    text_content = (
        f"Hello {name},\n\n"
        f"Your internet plan expires in {days_remaining} day(s).\n"
        "Please renew to avoid service interruption.\n\n"
        "© 2024 Bhatek Solution."
    )

    return send_email(to_email, subject, html_content, text_content)


def send_router_error_email(admin_email: str, router_list: List[Dict[str, Any]]) -> bool:
    """
    Send an admin notification listing routers with errors or offline status.

    Args:
        admin_email: Destination admin email address.
        router_list: List of dicts with keys: name, ip_address, status, error.

    Returns:
        True if the email was dispatched successfully.
    """
    if not admin_email:
        logger.warning("No admin email provided for router error notification")
        return False

    count = len(router_list)
    subject = f"🚨 Router Alert: {count} router(s) need attention"

    rows = ""
    for r in router_list:
        status_color = "#ef4444" if r.get('status') == 'offline' else "#f59e0b"
        rows += f"""
        <tr>
            <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0;">
                <strong>{r.get('name', 'Unknown')}</strong>
            </td>
            <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0;">
                {r.get('ip_address', 'N/A')}
            </td>
            <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0;">
                <span style="color:{status_color}; font-weight:600;">
                    {r.get('status', 'unknown').upper()}
                </span>
            </td>
            <td style="padding:8px 12px; border-bottom:1px solid #e2e8f0; color:#718096; font-size:12px;">
                {r.get('error', '')}
            </td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Router Alert</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   background-color: #f7fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 680px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #ef4444; padding: 28px 24px;
                       text-align: center; border-radius: 12px 12px 0 0; }}
            .header h1 {{ color: white; margin: 0; font-size: 22px; font-weight: 700; }}
            .content {{ background: white; padding: 28px 24px;
                        border-radius: 0 0 12px 12px;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
            th {{ background: #f7fafc; padding: 10px 12px; text-align: left;
                  font-size: 12px; color: #4a5568; text-transform: uppercase;
                  letter-spacing: 0.05em; }}
            .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🚨 Router Health Alert</h1>
            </div>
            <div class="content">
                <p>The following <strong>{count} router(s)</strong> require immediate attention:</p>
                <table>
                    <thead>
                        <tr>
                            <th>Router Name</th>
                            <th>IP Address</th>
                            <th>Status</th>
                            <th>Error</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
                <p style="margin-top:20px; color:#718096; font-size:13px;">
                    This alert was generated automatically by the ISP monitoring system.
                    Please investigate and resolve these issues promptly.
                </p>
            </div>
            <div class="footer">
                <p>&copy; 2024 Bhatek Solution. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

    text_lines = [f"Router Health Alert — {count} router(s) need attention\n"]
    for r in router_list:
        text_lines.append(
            f"  • {r.get('name')} ({r.get('ip_address')}) — "
            f"{r.get('status', 'unknown').upper()}: {r.get('error', '')}"
        )
    text_content = "\n".join(text_lines)

    return send_email(admin_email, subject, html_content, text_content)


def send_billing_report_email(admin_email: str, report_data: Dict[str, Any],
                               period: str) -> bool:
    """
    Send a billing summary report to an admin.

    Args:
        admin_email: Destination admin email address.
        report_data: Dict containing revenue, invoice counts, subscription stats, etc.
        period: 'daily', 'weekly', or 'monthly'.

    Returns:
        True if the email was dispatched successfully.
    """
    if not admin_email:
        logger.warning("No admin email provided for billing report")
        return False

    period_label = period.capitalize()
    period_range = report_data.get('period_range', '')
    subject = f"📊 {period_label} Billing Report — {period_range}"

    revenue = report_data.get('total_revenue', 0)
    invoice_count = report_data.get('invoice_count', 0)
    paid_count = report_data.get('paid_count', 0)
    new_subs = report_data.get('new_subscriptions', 0)
    renewals = report_data.get('renewals', 0)
    expired = report_data.get('expired_subscriptions', 0)
    vouchers_redeemed = report_data.get('vouchers_redeemed', 0)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{period_label} Billing Report</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   background-color: #f7fafc; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                       padding: 28px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
            .header h1 {{ color: white; margin: 0; font-size: 22px; font-weight: 700; }}
            .header p {{ color: rgba(255,255,255,0.85); margin: 6px 0 0; font-size: 14px; }}
            .content {{ background: white; padding: 28px 24px;
                        border-radius: 0 0 12px 12px;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
            .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
                          margin: 20px 0; }}
            .stat-card {{ background: #f7fafc; border-radius: 8px; padding: 16px;
                          text-align: center; }}
            .stat-value {{ font-size: 28px; font-weight: 800; color: #2d3748; }}
            .stat-label {{ font-size: 12px; color: #718096; margin-top: 4px;
                           text-transform: uppercase; letter-spacing: 0.05em; }}
            .revenue {{ color: #10b981; }}
            .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📊 {period_label} Billing Report</h1>
                <p>{period_range}</p>
            </div>
            <div class="content">
                <div class="stat-grid">
                    <div class="stat-card">
                        <div class="stat-value revenue">KES {revenue:,.2f}</div>
                        <div class="stat-label">Total Revenue</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{invoice_count}</div>
                        <div class="stat-label">Invoices Generated</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{paid_count}</div>
                        <div class="stat-label">Invoices Paid</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{new_subs}</div>
                        <div class="stat-label">New Subscriptions</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{renewals}</div>
                        <div class="stat-label">Renewals</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{expired}</div>
                        <div class="stat-label">Expired Subscriptions</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">{vouchers_redeemed}</div>
                        <div class="stat-label">Vouchers Redeemed</div>
                    </div>
                </div>
                <p style="color:#718096; font-size:13px; margin-top:16px;">
                    This report was generated automatically by the ISP billing system.
                </p>
            </div>
            <div class="footer">
                <p>&copy; 2024 Bhatek Solution. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

    text_content = (
        f"{period_label} Billing Report — {period_range}\n\n"
        f"  Revenue:              KES {revenue:,.2f}\n"
        f"  Invoices Generated:   {invoice_count}\n"
        f"  Invoices Paid:        {paid_count}\n"
        f"  New Subscriptions:    {new_subs}\n"
        f"  Renewals:             {renewals}\n"
        f"  Expired:              {expired}\n"
        f"  Vouchers Redeemed:    {vouchers_redeemed}\n"
    )

    return send_email(admin_email, subject, html_content, text_content)
