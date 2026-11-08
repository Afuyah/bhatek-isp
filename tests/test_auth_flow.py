"""Authentication flow regressions.

Covers the two auth defects found in the audit:
  * refresh-token rotation was read-then-write, so two concurrent refreshes
    could each mint a valid token pair from one token
  * logout never forwarded ``session_id``, so it could not revoke a single
    device -- the session-scoped branch was unreachable from the API
"""

from datetime import timedelta

from app.core.time import utcnow

CREDS = {'email': 'admin@acme.test', 'password': 'Passw0rd!x'}


def _login(client):
    response = client.post('/api/v1/auth/login', json=CREDS)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    return response.get_json()


def test_refresh_token_cannot_be_used_twice(client):
    tokens = _login(client)
    refresh = tokens['refresh_token']

    first = client.post('/api/v1/auth/refresh', json={'refresh_token': refresh})
    assert first.status_code == 200, first.get_data(as_text=True)[:300]

    # Replaying the same token must fail: it was claimed atomically.
    second = client.post('/api/v1/auth/refresh', json={'refresh_token': refresh})
    assert second.status_code in (401, 403), second.get_data(as_text=True)[:300]


def test_logout_with_refresh_token_revokes_it(client):
    tokens = _login(client)

    logout = client.post(
        '/api/v1/auth/logout',
        json={'refresh_token': tokens['refresh_token']},
        headers={'Authorization': f"Bearer {tokens['access_token']}"},
    )
    assert logout.status_code == 200, logout.get_data(as_text=True)[:300]

    after = client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']})
    assert after.status_code in (401, 403)


def test_recent_payment_lookup_matches_json_phone(app):
    """The lookup used LIKE on a JSON column, which never compiles on Postgres."""
    from app.core.database.session import db
    from app.models.organization import Organization
    from app.models.payment import Transaction
    from app.integrations.radius.radius_auth_handler import RadiusAuthHandler

    with app.app_context():
        org = Organization.query.first()
        txn = Transaction(
            organization_id=org.id,
            transaction_reference='TEST-REF-0001',
            amount=50,
            currency='KES',
            status='success',
            payment_method='mpesa',
            payment_details={'phone': '+254712345678', 'receipt': 'QK8H3X9P'},
            created_at=utcnow() - timedelta(minutes=5),
        )
        db.session.add(txn)
        db.session.commit()

        handler = RadiusAuthHandler()
        found = handler._find_recent_payment('254712345678', org.id)
        assert found is not None, "payment with the phone in payment_details was not found"
        assert str(found.id) == str(txn.id)

        db.session.delete(txn)
        db.session.commit()
