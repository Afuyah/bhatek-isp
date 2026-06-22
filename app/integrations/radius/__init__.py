"""
RADIUS Integration Package
==========================
Handles RADIUS authentication, accounting, caching, and FreeRADIUS
database synchronization for multi-tenant ISP management.

Primary Auth Path: REST API (RadiusAuthHandler)
Fallback Path:     SQL tables (RadiusSyncService → radcheck/radreply/radusergroup)

Multi-tenant isolation is enforced at all layers.
"""

# Dictionary & Utilities
from app.integrations.radius.dictionary import MikroTikDictionary

# Cache
from app.integrations.radius.radius_cache import RadiusCache, RedisCache

# Sync Service (DB tables fallback)
from app.integrations.radius.radius_sync_service import RadiusSyncService

# Auth Handler (REST API — primary path)
from app.integrations.radius.radius_auth_handler import (
    RadiusAuthHandler,
    radius_auth_bp,
)

# Accounting Handler (REST API)
from app.integrations.radius.radius_accounting_handler import (
    RadiusAccountingHandler,
)

# Accounting Routes (separate blueprint)
from app.integrations.radius.radius_accounting_routes import (
    radius_accounting_bp,
)

__all__ = [
    # Dictionary
    'MikroTikDictionary',

    # Cache
    'RadiusCache',
    'RedisCache',

    # Sync
    'RadiusSyncService',

    # Auth
    'RadiusAuthHandler',
    'radius_auth_bp',

    # Accounting
    'RadiusAccountingHandler',
    'radius_accounting_bp',
]