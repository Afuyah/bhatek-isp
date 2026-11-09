"""Tenancy: organizations are addressed by subdomain.

bhat.bhalinks.com  -> organization 'bhat'
bhalinks.com       -> apex (no tenant)
*.fly.dev          -> infrastructure host (no tenant)
"""

import pytest

from app.core.tenancy import normalize_subdomain, split_host


@pytest.fixture
def domain(app):
    app.config['APP_DOMAIN'] = 'bhalinks.com'
    app.config['ORG_SUBDOMAIN_ENABLED'] = True
    with app.app_context():
        yield app


def test_subdomain_is_extracted(domain):
    assert split_host('bhat.bhalinks.com')[0] == 'bhat'
    assert split_host('acme.bhalinks.com')[0] == 'acme'


def test_port_is_ignored(domain):
    assert split_host('bhat.bhalinks.com:8080')[0] == 'bhat'


def test_apex_has_no_tenant(domain):
    assert split_host('bhalinks.com')[0] is None


def test_deep_subdomains_are_not_tenants(domain):
    assert split_host('a.b.bhalinks.com')[0] is None


def test_reserved_labels_are_not_tenants(domain):
    for label in ('www', 'api', 'admin', 'portal', 'provision'):
        assert split_host(f'{label}.bhalinks.com')[0] is None


def test_other_domains_are_ignored(domain):
    assert split_host('bhatek-isp.fly.dev')[0] is None
    assert split_host('evil.com')[0] is None


def test_normalize_subdomain():
    assert normalize_subdomain('Bhat Links') == 'bhat-links'
    assert normalize_subdomain('  --ACME--  ') == 'acme'
    assert normalize_subdomain('') == ''
