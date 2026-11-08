"""Configuration-level regression tests.

Covers the database URL normalisation: SQLAlchemy 2.1 resolves a bare
``postgresql://`` URL to the psycopg (v3) driver, but the project installs
``psycopg2-binary``. In production that mismatch crashed the container with
``ModuleNotFoundError: No module named 'psycopg'``.
"""

import pytest

from app.core.config.settings import _normalize_database_url


@pytest.mark.parametrize('raw, expected', [
    ('postgresql://u:p@h:5432/db', 'postgresql+psycopg2://u:p@h:5432/db'),
    ('postgres://u:p@h:5432/db', 'postgresql+psycopg2://u:p@h:5432/db'),
    ('postgresql+psycopg2://u:p@h/db', 'postgresql+psycopg2://u:p@h/db'),
    ('sqlite:///instance/test.db', 'sqlite:///instance/test.db'),
    ('', ''),
    (None, None),
])
def test_database_url_normalisation(raw, expected):
    assert _normalize_database_url(raw) == expected


def test_production_config_uses_psycopg2(app):
    """Whatever URL the platform supplies, the SQLAlchemy URL must be explicit."""
    url = app.config['SQLALCHEMY_DATABASE_URI']
    if url.startswith('postgresql'):
        assert url.startswith('postgresql+psycopg2://'), url
