"""Tenant resolution.

An organization is identified by its subdomain of ``APP_DOMAIN``:

    bhat.bhalinks.com   → organization whose subdomain (or slug) is "bhat"
    bhalinks.com        → apex: marketing / login / super-admin
    <anything>.fly.dev  → infrastructure host, no tenant

The legacy UUID path (``/organization/<uuid>/...``) is still understood so old
links and bookmarks keep working, but it is never generated again.
"""

from __future__ import annotations

import re

from flask import current_app, g, has_request_context, request

from app.core.logging.logger import logger

#: Subdomain labels we must never treat as a tenant.
RESERVED_SUBDOMAINS = frozenset({
    'www', 'app', 'api', 'admin', 'static', 'assets', 'cdn', 'mail', 'smtp',
    'status', 'docs', 'help', 'support', 'billing', 'dashboard', 'login',
    'signup', 'auth', 'portal', 'provision', 'onboard',
})

_LABEL = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')


def normalize_subdomain(value: str) -> str:
    """Lowercase, hyphenate and trim a candidate subdomain."""
    value = (value or '').strip().lower()
    value = re.sub(r'[^a-z0-9-]+', '-', value)
    value = value.strip('-')
    return value[:63]


def apex_domain() -> str:
    return (current_app.config.get('APP_DOMAIN')
            or current_app.config.get('DOMAIN')
            or '').split(':')[0].lower()


def split_host(host: str):
    """Return (subdomain, apex) for a Host header, or (None, None)."""
    if not host:
        return None, None
    host = host.split(':')[0].strip().lower().rstrip('.')
    apex = apex_domain()
    if not apex or host == apex:
        return None, apex
    suffix = '.' + apex
    if not host.endswith(suffix):
        return None, apex
    label = host[:-len(suffix)]
    if not label or '.' in label or label in RESERVED_SUBDOMAINS or not _LABEL.match(label):
        return None, apex
    return label, apex


def resolve_organization(host: str = None):
    """Return the Organization for this host, or None for the apex/app host."""
    from app.models.organization import Organization

    label, _apex = split_host(host if host is not None else (
        request.host if has_request_context() else ''))
    if not label or not current_app.config.get('ORG_SUBDOMAIN_ENABLED', True):
        return None

    org = Organization.query.filter_by(subdomain=label).first()
    if org is None:
        org = Organization.query.filter_by(slug=label).first()
    if org is not None and getattr(org, 'status', 'active') != 'active':
        logger.warning("Tenant %s is not active (status=%s)", label, org.status)
        return None
    return org


def current_organization():
    """The organization bound to the current request (may be None)."""
    if not has_request_context():
        return None
    return g.get('organization')


def organization_host(org) -> str:
    """The canonical host for an organization."""
    apex = apex_domain()
    if org is None or not apex:
        return request.host if has_request_context() else ''
    label = getattr(org, 'subdomain', None) or normalize_subdomain(getattr(org, 'slug', '') or '')
    return f"{label}.{apex}" if label else apex


def organization_url(org, path: str = '/') -> str:
    """Absolute URL for a path inside an organization."""
    scheme = current_app.config.get('PREFERRED_URL_SCHEME', 'https')
    if not path.startswith('/'):
        path = '/' + path
    return f"{scheme}://{organization_host(org)}{path}"
