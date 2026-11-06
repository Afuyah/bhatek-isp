"""Test fixtures.

The suite runs against SQLite by default so it works with no external services.
``tests/support/sqlite_compat`` teaches the three PostgreSQL-only column types to
compile for SQLite; everything else is the real application.

Set ``TEST_DATABASE_URL`` to a Postgres URL to run the same suite against the
production dialect.
"""

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret')
os.environ.setdefault(
    'TEST_DATABASE_URL',
    'sqlite:///' + (ROOT / 'instance' / 'test.db').as_posix(),
)

from tests.support.sqlite_compat import enable_sqlite_types  # noqa: E402

enable_sqlite_types()

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.core.database.session import db  # noqa: E402
from app.models import (  # noqa: E402
    Network, Organization, OrganizationUser, Plan, User,
)

#: radacct declares an Integer autoincrement column inside a composite primary
#: key, which SQLite rejects. Nothing under test needs FreeRADIUS accounting.
SKIP_TABLES = {'radacct'}


@pytest.fixture(scope='session')
def app():
    application = create_app('testing')
    application.config.update(TESTING=True)

    with application.app_context():
        db.drop_all()
        tables = [t for n, t in db.metadata.tables.items() if n not in SKIP_TABLES]
        db.metadata.create_all(bind=db.engine, tables=tables)
        _seed()

    yield application

    with application.app_context():
        db.session.remove()


def _seed():
    org = Organization(
        name='Acme ISP', slug='acme', business_type='isp', email='ops@acme.test',
        phone='254700000001', status='active', currency='KES', timezone='Africa/Nairobi',
    )
    db.session.add(org)
    db.session.flush()

    user = User(
        email='admin@acme.test', phone='254700000001', first_name='Ada',
        organization_id=org.id, role='org_admin', is_active=True,
        is_super_admin=True, permissions=['*'],
    )
    user.set_password('Passw0rd!x')
    db.session.add(user)
    db.session.flush()

    # The web decorators check membership through organization_users, not the
    # user's organization_id column.
    db.session.add(OrganizationUser(
        organization_id=org.id, user_id=user.id, role='org_admin', is_primary=True,
    ))
    db.session.add(Network(
        organization_id=org.id, name='Main', slug='main', type='hotspot', is_active=True,
    ))
    db.session.add(Plan(
        organization_id=org.id, name='Daily 1GB', plan_type='hotspot', price=50,
        billing_cycle='daily', validity_type='time_based', validity_value=1,
        validity_unit='DAYS', is_active=True, is_public=True,
    ))
    db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ids(app):
    with app.app_context():
        return {
            'user_id': str(User.query.first().id),
            'org_id': str(Organization.query.first().id),
        }


@pytest.fixture
def auth_client(client, ids):
    with client.session_transaction() as sess:
        sess['user_id'] = ids['user_id']
    return client
