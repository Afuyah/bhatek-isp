"""Email transport behaviour.

The Brevo key on Fly was disabled, which surfaced two design gaps:
  * a rejected API key failed the send outright instead of trying SMTP
  * an outage was reported to the browser as 400 (client error) instead of 503
"""

import pytest


def _cfg(**overrides):
    config = {
        'api_key': 'test-key',
        'use_api': True,
        'smtp_host': 'smtp.example.com',
        'smtp_port': 587,
        'smtp_user': '',
        'smtp_password': '',
        'from_email': 'noreply@example.com',
        'from_name': 'Test',
    }
    config.update(overrides)
    return config


def test_falls_back_to_smtp_when_api_rejects(monkeypatch):
    from app.integrations.email import transport

    monkeypatch.setattr(transport, 'read_config',
                        lambda: _cfg(smtp_user='u', smtp_password='p'))
    monkeypatch.setattr(transport, '_send_via_brevo_api', lambda *a, **k: False)
    sent = []
    monkeypatch.setattr(transport, '_send_via_smtp',
                        lambda *a, **k: sent.append('smtp') or True)

    assert transport.send('a@b.c', 'subject', '<p>hi</p>') is True
    assert sent == ['smtp']


def test_returns_false_when_api_fails_and_no_smtp(monkeypatch):
    from app.integrations.email import transport

    monkeypatch.setattr(transport, 'read_config', lambda: _cfg())
    monkeypatch.setattr(transport, '_send_via_brevo_api', lambda *a, **k: False)

    assert transport.send('a@b.c', 'subject', '<p>hi</p>') is False


def test_mock_mode_without_credentials(monkeypatch):
    from app.integrations.email import transport

    monkeypatch.setattr(transport, 'read_config', lambda: _cfg(api_key='', use_api=False))
    assert transport.send('a@b.c', 'subject', '<p>hi</p>') is True


def test_verification_email_outage_returns_503(client, monkeypatch):
    """A mail outage is a server-side failure, not a bad request."""
    from app.integrations.email import brevo_service

    monkeypatch.setattr(brevo_service, 'send', lambda *a, **k: False)

    response = client.post('/api/v1/auth/send-verification',
                           json={'email': 'outage-test@example.com'})
    assert response.status_code == 503, response.get_data(as_text=True)[:300]
    assert response.get_json().get('error') == 'EMAIL_UNAVAILABLE'
