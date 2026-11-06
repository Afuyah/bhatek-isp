"""Shared email transport - the only place that talks to Brevo or SMTP.

Used by both:
* ``app.integrations.email.brevo_service.BrevoEmailService`` (request flows)
* ``app.integrations.email_service`` (Celery tasks / CLI, app context only)

Configuration comes from the Flask app when available and falls back to
environment variables, so it works unchanged from a background worker.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import requests

from app.core.logging.logger import logger


def read_config() -> Dict[str, Any]:
    """Pull email config from Flask app config (works inside app context)."""
    try:
        from flask import current_app
        return {
            'api_key': current_app.config.get('BREVO_API_KEY', ''),
            'use_api': current_app.config.get('BREVO_USE_API', True),
            'smtp_host': current_app.config.get('SMTP_HOST', 'smtp-relay.brevo.com'),
            'smtp_port': current_app.config.get('SMTP_PORT', 587),
            'smtp_user': current_app.config.get('SMTP_USER', ''),
            'smtp_password': current_app.config.get('SMTP_PASSWORD', ''),
            'from_email': current_app.config.get('FROM_EMAIL', 'noreply@isp.com'),
            'from_name': current_app.config.get('FROM_NAME', 'Bhatek ISP'),
        }
    except RuntimeError:
        # No app context — fall back to env vars
        import os
        return {
            'api_key': os.environ.get('BREVO_API_KEY', ''),
            'use_api': os.environ.get('BREVO_USE_API', 'true').lower() == 'true',
            'smtp_host': os.environ.get('SMTP_HOST', 'smtp-relay.brevo.com'),
            'smtp_port': int(os.environ.get('SMTP_PORT', 587)),
            'smtp_user': os.environ.get('SMTP_USER', ''),
            'smtp_password': os.environ.get('SMTP_PASSWORD', ''),
            'from_email': os.environ.get('FROM_EMAIL', 'noreply@isp.com'),
            'from_name': os.environ.get('FROM_NAME', 'Bhatek ISP'),
        }


def _send_via_brevo_api(cfg: Dict, to_email: str, subject: str,
                         html_content: str, text_content: Optional[str]) -> bool:
    try:
        payload: Dict[str, Any] = {
            "sender": {"name": cfg['from_name'], "email": cfg['from_email']},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_content,
        }
        if text_content:
            payload["textContent"] = text_content

        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json=payload,
            headers={
                "accept": "application/json",
                "api-key": cfg['api_key'],
                "content-type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code == 201:
            logger.info(f"Email sent via Brevo API to {to_email}")
            return True
        logger.error(f"Brevo API error {resp.status_code}: {resp.text}")
        return False
    except Exception as exc:
        logger.error(f"Brevo API exception: {exc}", exc_info=True)
        return False


def _send_via_smtp(cfg: Dict, to_email: str, subject: str,
                   html_content: str, text_content: Optional[str]) -> bool:
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        if text_content:
            msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'], timeout=30) as srv:
            srv.starttls()
            srv.login(cfg['smtp_user'], cfg['smtp_password'])
            srv.send_message(msg)

        logger.info(f"Email sent via SMTP to {to_email}")
        return True
    except Exception as exc:
        logger.error(f"SMTP error: {exc}", exc_info=True)
        return False


def send(to_email: str, subject: str, html_content: str,
         text_content: Optional[str] = None) -> bool:
    """Send one email: Brevo API, then SMTP, then mock (returns True)."""
    cfg = read_config()
    if cfg['use_api'] and cfg['api_key']:
        return _send_via_brevo_api(cfg, to_email, subject, html_content, text_content)
    if cfg['smtp_user'] and cfg['smtp_password']:
        return _send_via_smtp(cfg, to_email, subject, html_content, text_content)
    logger.info("[EMAIL_MOCK] To=%s | Subject=%s", to_email, subject)
    return True
