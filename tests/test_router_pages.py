"""Operator pages that need a router to exist.

The router detail page crashed in production with
``'Router' object has no attribute 'network_id'`` because the network layer was
removed from the model while the view still referenced it. These tests render the
pages that depend on a Router row, so that class of straggler is caught here.
"""

from uuid import UUID, uuid4

import pytest


def _make_router(app, org_id, **overrides):
    from app.core.database.session import db
    from app.models.router import Router

    # wireguard_ip and radius_secret are unique columns
    suffix = uuid4().hex[:6]
    address = f'10.0.99.{int(suffix, 16) % 200 + 20}'
    data = {
        'organization_id': UUID(str(org_id)),
        'name': f'Page Test Router {suffix}',
        'ip_address': address,
        'local_ip': address,
        'username': f'bhatek-{suffix}',
        'password_encrypted': 'not-a-real-ciphertext',
        'wireguard_ip': address,
        'status': 'online',
        'radius_secret': f'testsecret-{suffix}',
    }
    data.update(overrides)

    with app.app_context():
        router = Router(**data)
        db.session.add(router)
        db.session.commit()
        return str(router.id)


def test_router_detail_page_renders(auth_client, app, ids):
    router_id = _make_router(app, ids['org_id'])
    response = auth_client.get(
        f"/organization/{ids['org_id']}/routers/{router_id}",
        headers={'Accept': 'text/html'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    body = response.get_data(as_text=True)
    assert 'Page Test Router' in body


def test_router_configure_page_renders(auth_client, app, ids):
    router_id = _make_router(app, ids['org_id'])
    response = auth_client.get(
        f"/organization/{ids['org_id']}/routers/{router_id}/configure",
        headers={'Accept': 'text/html'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    body = response.get_data(as_text=True)
    # the port checkboxes and the WLAN toggle are the point of this page
    assert 'name="lan_ports"' in body
    assert 'name="use_wlan"' in body


def test_router_edit_page_renders(auth_client, app, ids):
    """The edit form used to contain a network <select> that no longer exists."""
    router_id = _make_router(app, ids['org_id'])
    response = auth_client.get(
        f"/organization/{ids['org_id']}/routers/{router_id}/edit",
        headers={'Accept': 'text/html'},
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:500]


def test_router_list_api_has_no_network_filter(client, app, ids):
    """The list endpoint used to accept network_id and pass it to the service."""
    login = client.post('/api/v1/auth/login',
                        json={'email': 'admin@acme.test', 'password': 'Passw0rd!x'})
    assert login.status_code == 200
    headers = {'Authorization': f"Bearer {login.get_json()['access_token']}"}

    ok = client.get('/api/v1/routers?status=online', headers=headers)
    assert ok.status_code == 200, ok.get_data(as_text=True)[:300]

    # an unknown query parameter must be ignored, not blow up
    ignored = client.get('/api/v1/routers?network_id=nonsense', headers=headers)
    assert ignored.status_code == 200, ignored.get_data(as_text=True)[:300]
