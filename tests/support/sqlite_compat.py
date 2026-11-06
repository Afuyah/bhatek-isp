"""SQLite compatibility shims for the test suite.

The models are written for PostgreSQL and use three dialect-specific column
types (``UUID``, ``INET``, ``MACADDR``). That is fine in production, but it
means ``db.create_all()`` cannot run against SQLite — and SQLite is the only
database available in environments without a Postgres server.

Importing this module teaches those three types to compile to portable
equivalents *for the SQLite dialect only*. Nothing here affects PostgreSQL:
the production dialect never consults these compilers.

    from tests.support.sqlite_compat import enable_sqlite_types
    enable_sqlite_types()
"""

from sqlalchemy.dialects.postgresql import INET, MACADDR, UUID
from sqlalchemy.ext.compiler import compiles

_enabled = False


def enable_sqlite_types():
    """Register SQLite compilation for the Postgres-only column types."""
    global _enabled
    if _enabled:
        return

    @compiles(UUID, 'sqlite')
    def _uuid_sqlite(type_, compiler, **kw):
        return 'CHAR(36)'

    @compiles(INET, 'sqlite')
    def _inet_sqlite(type_, compiler, **kw):
        return 'VARCHAR(45)'

    @compiles(MACADDR, 'sqlite')
    def _macaddr_sqlite(type_, compiler, **kw):
        return 'VARCHAR(17)'

    _enabled = True
