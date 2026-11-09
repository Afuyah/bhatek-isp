"""Zero-touch router onboarding via a single-use capability link.

The operator supplies a description; the platform generates the VPN address,
API credentials and RADIUS secret, and hands back a *link*. The MikroTik fetches
and imports it, so the script is never displayed.
"""

import pytest

CREDS = {'email': 'admin@acme.test', 'password': 'Passw0rd!x'}


def _auth(client):
    response = client.post('/api/v1/auth/login', json=CREDS)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    return {'Authorization': f"Bearer {response.get_json()['access_token']}"}


def _create(client, headers, payload):
    return client.post('/api/v1/routers', json=payload, headers=headers)


def _path_from(url):
    return '/' + url.split('://', 1)[1].split('/', 1)[1]


def test_description_alone_provisions_a_router(client):
    headers = _auth(client)
    response = _create(client, headers, {'description': 'Ruiru hotspot - main street'})

    assert response.status_code in (200, 201), response.get_data(as_text=True)[:500]
    body = response.get_json()
    assert body['success'] is True

    # The script must NOT be in the response - only a link to it.
    assert 'setup_script' not in body
    command = body['onboarding']['command']
    assert '/onboard/' in command and '.rsc' in command
    assert '/import' in command

    api = body['api_access']
    assert api['username'].startswith('bhatek-')

    router = body['router']
    assert router['wireguard_ip'].startswith('10.0.')
    assert router['username'] == api['username']


def test_link_serves_the_script(client):
    headers = _auth(client)
    body = _create(client, headers, {'description': 'Fetch test'}).get_json()

    response = client.get(_path_from(body['onboarding']['url']))
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert response.mimetype == 'text/plain'
    assert 'no-store' in response.headers.get('Cache-Control', '')

    script = response.get_data(as_text=True)
    for fragment in ('/interface wireguard add', '/radius add address=10.0.0.1',
                     'use-radius=yes', 'walled-garden'):
        assert fragment in script, f"missing {fragment!r}"


def test_unknown_token_is_404(client):
    assert client.get('/onboard/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.rsc').status_code == 404


def test_used_link_is_rejected(client, app):
    headers = _auth(client)
    body = _create(client, headers, {'description': 'Single use'}).get_json()
    path = _path_from(body['onboarding']['url'])
    assert client.get(path).status_code == 200

    # Simulate a fetch beyond the re-fetch grace window.
    from datetime import timedelta
    from app.core.database.session import db
    from app.core.time import utcnow
    from uuid import UUID
    from app.models.router import Router

    with app.app_context():
        router = Router.query.filter_by(id=UUID(body['router']['id'])).first()
        router.onboarding_used_at = utcnow() - timedelta(minutes=30)
        db.session.commit()

    assert client.get(path).status_code == 410


def test_expired_link_is_rejected(client, app):
    headers = _auth(client)
    body = _create(client, headers, {'description': 'Expiry'}).get_json()
    path = _path_from(body['onboarding']['url'])

    from datetime import timedelta
    from app.core.database.session import db
    from app.core.time import utcnow
    from uuid import UUID
    from app.models.router import Router

    with app.app_context():
        router = Router.query.filter_by(id=UUID(body['router']['id'])).first()
        router.onboarding_token_expires_at = utcnow() - timedelta(hours=1)
        db.session.commit()

    assert client.get(path).status_code == 410


def test_regenerate_invalidates_the_previous_link(client):
    headers = _auth(client)
    body = _create(client, headers, {'description': 'Regenerate'}).get_json()
    old_path = _path_from(body['onboarding']['url'])

    response = client.post(f"/api/v1/routers/{body['router']['id']}/onboarding-link",
                           headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    new_url = response.get_json()['onboarding']['url']
    assert new_url != body['onboarding']['url']

    # Old capability is gone; the new one works.
    assert client.get(old_path).status_code == 404
    assert client.get(_path_from(new_url)).status_code == 200


def test_description_is_required(client):
    headers = _auth(client)
    assert _create(client, headers, {}).status_code == 400
    assert _create(client, headers, {'description': 'ab'}).status_code == 400


def test_script_uses_configured_endpoint_and_restricted_services(client):
    headers = _auth(client)
    body = _create(client, headers, {'description': 'Script hygiene'}).get_json()
    script = client.get(_path_from(body['onboarding']['url'])).get_data(as_text=True)

    assert '163.245.217.16' not in script
    assert '/ip service set api address=10.0.0.0/16' in script
    assert 'address=0.0.0.0/0' not in script
    assert 'isp.bhatek.space' not in script
    # API credentials generated at creation are embedded for the router.
    assert body['api_access']['username'] in script
