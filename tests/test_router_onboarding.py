"""Zero-touch router onboarding.

The operator supplies a description; the platform generates the VPN address,
API credentials and RADIUS secret, and returns a script for the MikroTik.
"""

import pytest

CREDS = {'email': 'admin@acme.test', 'password': 'Passw0rd!x'}


def _auth(client):
    response = client.post('/api/v1/auth/login', json=CREDS)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    return {'Authorization': f"Bearer {response.get_json()['access_token']}"}


def _create(client, headers, payload):
    return client.post('/api/v1/routers', json=payload, headers=headers)


def test_description_alone_provisions_a_router(client):
    headers = _auth(client)
    response = _create(client, headers, {'description': 'Ruiru hotspot - main street'})

    assert response.status_code in (200, 201), response.get_data(as_text=True)[:500]
    body = response.get_json()
    assert body['success'] is True

    script = body['setup_script']
    # The script must carry everything the router needs...
    for fragment in ('/interface wireguard add', '/radius add address=10.0.0.1',
                     'secret=', 'use-radius=yes', 'walled-garden'):
        assert fragment in script, f"missing {fragment!r} in script"

    # ...including the generated API user, so nothing is typed by hand.
    api = body['api_access']
    assert api['username'].startswith('bhatek-')
    assert api['password']
    assert api['username'] in script and api['password'] in script

    router = body['router']
    assert router['wireguard_ip'].startswith('10.0.')
    assert router['username'] == api['username']


def test_description_is_required(client):
    headers = _auth(client)
    response = _create(client, headers, {})
    assert response.status_code == 400, response.get_data(as_text=True)[:300]


def test_short_description_is_rejected(client):
    headers = _auth(client)
    response = _create(client, headers, {'description': 'ab'})
    assert response.status_code == 400, response.get_data(as_text=True)[:300]


def test_script_uses_configured_endpoint_not_a_hardcoded_ip(client):
    """The old generator shipped a stale VPS address baked into the script."""
    headers = _auth(client)
    body = _create(client, headers, {'description': 'Stale IP regression'}).get_json()
    script = body['setup_script']
    assert '163.245.217.16' not in script


def test_services_are_restricted_to_the_tunnel(client):
    """API/Winbox must not be exposed to the whole internet."""
    headers = _auth(client)
    script = _create(client, headers, {'description': 'Exposure regression'}).get_json()['setup_script']
    assert '/ip service set api address=10.0.0.0/16' in script
    assert 'address=0.0.0.0/0' not in script


def test_walled_garden_uses_the_configured_portal_host(client, app):
    headers = _auth(client)
    script = _create(client, headers, {'description': 'Portal host'}).get_json()['setup_script']
    portal = app.config['BASE_DOMAIN']
    assert f'dst-host={portal}' in script
    assert 'isp.bhatek.space' not in script
