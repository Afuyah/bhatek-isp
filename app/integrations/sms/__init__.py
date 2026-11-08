"""SMS integration.

Provider selection lives here so callers never import a concrete provider:

    from app.integrations.sms import send_sms
    send_sms(organization_id, phone, "Your code is 1234")

``send_sms`` is deliberately best-effort: an SMS outage must never break a
payment, a voucher redemption or a login flow.
"""

from __future__ import annotations

from flask import current_app

from app.core.logging.logger import logger
from app.integrations.sms.provider import SMSService

DEFAULT_PROVIDER = 'brevo'


def _build_provider(app):
    """Instantiate the provider named by ``SMS_PROVIDER``."""
    name = (app.config.get('SMS_PROVIDER') or DEFAULT_PROVIDER).lower()

    if name == 'brevo':
        from app.integrations.sms.brevo import from_config
        return name, from_config()

    if name in ('africa_talking', 'africastalking'):
        from app.integrations.sms.africa_talking import AfricaTalkingProvider
        return 'africa_talking', AfricaTalkingProvider(
            api_key=app.config.get('SMS_API_KEY', ''),
            username=app.config.get('SMS_API_SECRET', 'sandbox') or 'sandbox',
            sender_id=app.config.get('SMS_SENDER_ID'),
            environment=app.config.get('SMS_ENVIRONMENT', 'production'),
        )

    if name == 'twilio':
        from app.integrations.sms.twilio import TwilioProvider
        return 'twilio', TwilioProvider(
            account_sid=app.config.get('SMS_API_KEY', ''),
            auth_token=app.config.get('SMS_API_SECRET', ''),
            from_number=app.config.get('SMS_SENDER_ID'),
        )

    raise ValueError(f"Unknown SMS provider: {name!r}")


def get_sms_service() -> SMSService:
    """Return the app's configured :class:`SMSService` (built once)."""
    app = current_app._get_current_object()
    service = app.extensions.get('sms_service')
    if service is None:
        service = SMSService()
        name, provider = _build_provider(app)
        service.register_provider(name, provider)
        app.extensions['sms_service'] = service
        logger.info("SMS service initialised with provider: %s", name)
    return service


def send_sms(organization_id, phone: str, message: str, provider: str = None) -> dict:
    """Send one SMS. Never raises; returns the provider result or an error dict."""
    try:
        return get_sms_service().send_sms(str(organization_id), phone, message, provider)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("SMS dispatch failed for %s: %s", str(phone)[-4:], exc, exc_info=True)
        return {'success': False, 'error': str(exc), 'phone': phone}
