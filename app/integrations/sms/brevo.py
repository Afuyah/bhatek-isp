"""Brevo transactional SMS provider.

Uses the same API key as the email service (``BREVO_API_KEY``), so enabling SMS
requires no second credential: only SMS credits on the Brevo account and an
approved sender name.

API: ``POST https://api.brevo.com/v3/transactionalSMS/sms``

    {
      "sender": "Bhatek",
      "recipient": "254712345678",     // international, digits only, no '+'
      "content": "...",
      "type": "transactional"
    }

Docs: https://developers.brevo.com/reference/sendtransacsms
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List

import requests
from flask import current_app

from app.core.logging.logger import logger
from app.integrations.sms.provider import SMSProvider

API_URL = 'https://api.brevo.com/v3/transactionalSMS/sms'
ACCOUNT_URL = 'https://api.brevo.com/v3/account'


class BrevoSMSProvider(SMSProvider):
    """Send SMS through Brevo's transactional SMS API."""

    def __init__(self, api_key: str, sender: str = 'Bhatek',
                 default_country_code: str = '254', timeout: int = 20):
        self.api_key = api_key
        self.sender = sender
        self.default_country_code = default_country_code.lstrip('+')
        self.timeout = timeout

    # ------------------------------------------------------------------ helpers
    def _format_phone(self, phone: str) -> str:
        """Normalise to international digits (254712345678)."""
        digits = re.sub(r'\D', '', str(phone or ''))
        if not digits:
            return ''
        if digits.startswith('00'):
            digits = digits[2:]
        if digits.startswith('0'):
            digits = self.default_country_code + digits[1:]
        elif len(digits) == 9:  # 712345678
            digits = self.default_country_code + digits
        return digits

    def _headers(self) -> Dict[str, str]:
        return {
            'api-key': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _post(self, phone: str, message: str, tag: str = None) -> Dict[str, Any]:
        recipient = self._format_phone(phone)
        if not recipient:
            return {'success': False, 'error': 'Invalid phone number', 'phone': phone}

        payload = {
            'sender': self.sender,
            'recipient': recipient,
            'content': message,
            'type': 'transactional',
        }
        if tag:
            payload['tag'] = tag[:20]

        try:
            response = requests.post(API_URL, json=payload,
                                     headers=self._headers(), timeout=self.timeout)

            if response.status_code in (200, 201, 202):
                body = response.json() if response.content else {}
                logger.info("SMS sent via Brevo to %s (messageId=%s)",
                            recipient[-4:], body.get('messageId'))
                return {
                    'success': True,
                    'provider': 'brevo',
                    'phone': recipient,
                    'message_id': body.get('messageId'),
                    'credits_used': body.get('usedCredits'),
                    'timestamp': time.time(),
                }

            if response.status_code in (401, 403):
                logger.error(
                    "Brevo rejected the SMS API key (%s): %s. The same BREVO_API_KEY "
                    "is used for email - check it is enabled for SMS.",
                    response.status_code, (response.text or '')[:200],
                )
            else:
                logger.error("Brevo SMS error %s: %s",
                             response.status_code, (response.text or '')[:200])

            return {
                'success': False,
                'provider': 'brevo',
                'phone': recipient,
                'status_code': response.status_code,
                'error': (response.text or '')[:300],
            }
        except Exception as exc:
            logger.error("Brevo SMS exception: %s", exc, exc_info=True)
            return {'success': False, 'provider': 'brevo', 'phone': phone, 'error': str(exc)}

    # ------------------------------------------------------------------ interface
    def send(self, phone: str, message: str, **kwargs) -> Dict[str, Any]:
        return self._post(phone, message, tag=kwargs.get('tag'))

    def send_bulk(self, phones: List[str], message: str, **kwargs) -> List[Dict[str, Any]]:
        # Brevo has no bulk SMS endpoint; send individually with a small gap so a
        # large campaign does not trip their rate limits.
        results = []
        for index, phone in enumerate(phones):
            results.append(self._post(phone, message, tag=kwargs.get('tag')))
            if index < len(phones) - 1:
                time.sleep(kwargs.get('delay', 0.2))
        return results

    def get_balance(self) -> Dict[str, Any]:
        """Brevo does not expose SMS credits over the API; report the account plan."""
        try:
            response = requests.get(ACCOUNT_URL, headers=self._headers(), timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'provider': 'brevo',
                    'plan': (data.get('plan') or [{}])[0].get('type') if data.get('plan') else None,
                    'sms_balance': None,
                    'note': 'SMS credits are only visible in the Brevo dashboard',
                }
            return {'success': False, 'provider': 'brevo', 'status_code': response.status_code}
        except Exception as exc:
            return {'success': False, 'provider': 'brevo', 'error': str(exc)}


def from_config() -> BrevoSMSProvider:
    """Build the provider from the current app configuration."""
    return BrevoSMSProvider(
        api_key=current_app.config.get('BREVO_API_KEY', ''),
        sender=current_app.config.get('BREVO_SMS_SENDER')
               or current_app.config.get('SMS_SENDER_ID')
               or 'Bhatek',
        default_country_code=current_app.config.get('SMS_DEFAULT_COUNTRY_CODE', '254'),
    )
