"""Security regressions.

Each test here corresponds to a defect that shipped in the original codebase.
They are cheap to run and they fail loudly if the defect returns.
"""

import json

RADIUS_PATHS = ['/api/radius/authenticate', '/api/radius/disconnect',
                '/api/radius/accounting', '/api/radius/accounting/start',
                '/api/radius/accounting/stop', '/api/radius/accounting/interim']


def test_radius_endpoints_require_secret(client):
    for path in RADIUS_PATHS:
        response = client.post(path, json={'username': 'x'})
        assert response.status_code in (401, 503), f"{path} accepted an unauthenticated call"


def test_radius_rejects_wrong_secret(client, app):
    app.config['RADIUS_REST_SECRET'] = 'correct-secret'
    try:
        response = client.post('/api/radius/authenticate', json={},
                               headers={'X-Radius-Secret': 'wrong-secret'})
        assert response.status_code == 401
    finally:
        app.config.pop('RADIUS_REST_SECRET', None)


def test_radius_accepts_correct_secret(client, app):
    app.config['RADIUS_REST_SECRET'] = 'correct-secret'
    try:
        response = client.post('/api/radius/authenticate', json={},
                               headers={'X-Radius-Secret': 'correct-secret'})
        # Passes the guard; the handler decides what an empty body means.
        assert response.status_code != 401
    finally:
        app.config.pop('RADIUS_REST_SECRET', None)


def test_password_hash_never_returned(client):
    response = client.post('/api/v1/auth/login',
                           json={'email': 'admin@acme.test', 'password': 'Passw0rd!x'})
    body = response.get_data(as_text=True)
    assert 'password_hash' not in body


def test_org_admin_does_not_get_wildcard_permissions(app):
    from app.modules.auth.permissions import ROLE_PERMISSIONS

    assert '*' not in ROLE_PERMISSIONS['org_admin']
    assert 'subscriber_create' in ROLE_PERMISSIONS['org_admin']


def test_org_admin_registration_uses_role_permissions(app):
    """Registration must not hand out '*' just because the user is an admin."""
    import inspect

    from app.modules.auth.service import AuthService

    source = inspect.getsource(AuthService)
    # The org_admin branch must reference ROLE_PERMISSIONS, not a literal wildcard.
    assert "ROLE_PERMISSIONS['org_admin']" in source


def test_unauthenticated_router_test_endpoint_is_not_public(app):
    """The onboarding wizard's connection test must not be anonymous."""
    from app.core.middleware import EXEMPT_PATHS

    assert '/api/v1/routers/test' not in EXEMPT_PATHS


def test_jwt_service_is_registered(app):
    """Nothing used to register it, so every token_required route raised."""
    with app.app_context():
        assert app.extensions.get('jwt_service') is not None


def test_authenticated_api_call_succeeds(client):
    login = client.post('/api/v1/auth/login',
                        json={'email': 'admin@acme.test', 'password': 'Passw0rd!x'})
    assert login.status_code == 200, login.get_data(as_text=True)[:300]
    token = login.get_json()['access_token']

    response = client.get('/api/v1/subscribers',
                          headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200, response.get_data(as_text=True)[:300]


def test_api_rejects_missing_token(client):
    response = client.get('/api/v1/subscribers')
    assert response.status_code in (401, 302)
