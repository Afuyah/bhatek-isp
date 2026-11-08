"""Brevo SMS integration.

Brevo exposes transactional SMS under the same API key as email, so enabling SMS
needs only credits and an approved sender name — no second credential.
"""

import pytest

from app.integrations.sms.brevo import BrevoSMSProvider


@pytest.mark.parametrize('raw, expected', [
    ('0712345678', '254712345678'),
    ('+254712345678', '254712345678'),
    ('254712345678', '254712345678'),
    ('712345678', '254712345678'),
    ('00254712345678', '254712345678'),
    (' 0712 345 678 ', '254712345678'),
    ('', ''),
])
def test_phone_normalisation(raw, expected):
    assert BrevoSMSProvider(api_key='k')._format_phone(raw) == expected


class _Response:
    def __init__(self, status=201, payload=None, text=''):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.content = b'{}' if payload is not None else b''

    def json(self):
        return self._payload


def test_send_posts_expected_payload(monkeypatch):
    from app.integrations.sms import brevo

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({'url': url, 'json': json, 'headers': headers})
        return _Response(201, {'messageId': 42, 'usedCredits': 1})

    monkeypatch.setattr(brevo.requests, 'post', fake_post)

    provider = BrevoSMSProvider(api_key='test-key', sender='Bhatek')
    result = provider.send('0712345678', 'Hello')

    assert result['success'] is True
    assert result['message_id'] == 42
    assert captured['json'] == {
        'sender': 'Bhatek',
        'recipient': '254712345678',
        'content': 'Hello',
        'type': 'transactional',
    }
    assert captured['headers']['api-key'] == 'test-key'
    assert captured['url'].endswith('/transactionalSMS/sms')


def test_auth_failure_is_reported_not_raised(monkeypatch):
    from app.integrations.sms import brevo

    monkeypatch.setattr(brevo.requests, 'post',
                        lambda *a, **k: _Response(401, text='API Key is not enabled'))
    result = BrevoSMSProvider(api_key='bad').send('0712345678', 'Hello')
    assert result['success'] is False
    assert result['status_code'] == 401


def test_invalid_phone_is_rejected_without_a_request(monkeypatch):
    from app.integrations.sms import brevo

    called = []
    monkeypatch.setattr(brevo.requests, 'post', lambda *a, **k: called.append(1))
    result = BrevoSMSProvider(api_key='k').send('', 'Hello')
    assert result['success'] is False
    assert called == []


def test_service_selects_brevo_by_config(app):
    from app.integrations.sms import get_sms_service

    app.config['SMS_PROVIDER'] = 'brevo'
    with app.app_context():
        service = get_sms_service()
        provider = service.get_provider('brevo')
        assert provider.__class__.__name__ == 'BrevoSMSProvider'


def test_send_sms_never_raises(app, monkeypatch):
    """A broken provider must not propagate into a payment flow."""
    from app.integrations.sms import send_sms
    import app.integrations.sms as sms_pkg

    def boom():
        raise RuntimeError('provider exploded')

    monkeypatch.setattr(sms_pkg, 'get_sms_service', boom)
    with app.app_context():
        result = send_sms('org-1', '0712345678', 'Hello')
    assert result['success'] is False
    assert 'exploded' in result['error']
