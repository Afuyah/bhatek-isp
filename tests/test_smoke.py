"""Smoke tests: does every page render, and do the cross-cutting behaviours hold?

These are the tests that would have caught the bugs found by hand:
a browser getting JSON on 404, an undefined template variable raising, and a
string user id reaching a UUID column.
"""

import gzip

import pytest

PUBLIC_PAGES = ['/', '/login', '/register', '/verify-email?token=abc',
                '/register-success']


@pytest.mark.parametrize('path', PUBLIC_PAGES)
def test_public_pages_render(client, path):
    response = client.get(path, headers={'Accept': 'text/html'})
    assert response.status_code == 200
    assert b'<html' in response.get_data()


def test_health_endpoint(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.get_json()['status'] == 'healthy'


def test_browser_gets_html_404(client):
    response = client.get('/nope-not-a-page', headers={'Accept': 'text/html'})
    assert response.status_code == 404
    assert response.mimetype == 'text/html'


def test_api_gets_json_404(client):
    response = client.get('/api/v1/nope', headers={'Accept': 'application/json'})
    assert response.status_code in (401, 404)
    assert response.mimetype == 'application/json'


def test_html_is_gzipped(client):
    raw = client.get('/login', headers={'Accept': 'text/html'})
    compressed = client.get('/login', headers={'Accept': 'text/html',
                                               'Accept-Encoding': 'gzip'})
    assert compressed.headers.get('Content-Encoding') == 'gzip'
    assert gzip.decompress(compressed.get_data()) == raw.get_data()


def test_static_assets_are_cacheable(client):
    response = client.get('/static/css/app.css')
    assert response.status_code == 200
    assert 'immutable' in response.headers.get('Cache-Control', '')


OPERATOR_PAGES = [
    '/dashboard',
    '/organization/{org_id}/dashboard',
    '/organization/{org_id}/networks/',
    '/organization/{org_id}/networks/create',
    '/organization/{org_id}/networks/stats',
    '/organization/{org_id}/subscribers/',
    '/organization/{org_id}/subscribers/hotspot',
    '/organization/{org_id}/subscribers/pppoe',
    '/organization/{org_id}/subscribers/create/hotspot',
    '/organization/{org_id}/routers/',
    '/organization/{org_id}/routers/health',
    '/organization/{org_id}/routers/create',
    '/organization/{org_id}/billing/dashboard',
    '/organization/{org_id}/billing/plans',
    '/organization/{org_id}/billing/subscriptions',
    '/organization/{org_id}/billing/invoices',
    '/organization/{org_id}/billing/vouchers',
    '/organization/{org_id}/reports',
]


@pytest.mark.parametrize('template', OPERATOR_PAGES)
def test_operator_pages_render(auth_client, ids, template):
    response = auth_client.get(template.format(**ids), headers={'Accept': 'text/html'})
    # /dashboard redirects super admins to the super-admin view by design.
    assert response.status_code in (200, 302)
    if response.status_code == 200:
        assert b'<html' in response.get_data()


def test_anonymous_cannot_reach_operator_pages(client, ids):
    response = client.get(f"/organization/{ids['org_id']}/subscribers/",
                          headers={'Accept': 'text/html'})
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_stream_endpoints_require_auth(client, ids):
    # The operator stream is session-protected...
    response = client.get(f"/organization/{ids['org_id']}/routers/health/stream",
                          headers={'Accept': 'text/html'})
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_captive_portal_stream_is_public(client):
    # ...while the hotspot payment stream is deliberately public: anonymous
    # customers must be able to watch their own payment settle.
    response = client.get('/hotspot/acme/status/11111111-1111-1111-1111-111111111111/stream')
    assert response.status_code == 200
    assert response.mimetype == 'text/event-stream'


def test_stream_routes_registered(app):
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert any('health/stream' in r for r in rules)
    assert any('/hotspot/<org_slug>/status/<transaction_id>/stream' in r for r in rules)


def test_every_template_endpoint_exists(app):
    import pathlib
    import re

    known = set(app.view_functions)
    templates = pathlib.Path(app.root_path) / 'templates'
    missing = {}
    for tpl in templates.rglob('*.html'):
        text = tpl.read_text(encoding='utf-8', errors='replace')
        for name in re.findall(r"url_for\(\s*['\"]([A-Za-z0-9_.]+)['\"]", text):
            if name != 'static' and name not in known:
                missing.setdefault(name, set()).add(tpl.name)
    assert not missing, f"unresolved endpoints: {missing}"
