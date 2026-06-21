"""
MikroTik Integration Package
=============================
Production-ready MikroTik RouterOS API client for ISP management.

Provides:
    - MikroTikClient: High-level client with connection management, retry,
      RADIUS configuration, and full hotspot/PPPoE management.
    - MikroTikConnection: Low-level wire protocol implementation.
    - MikroTikAPIError: Base exception hierarchy.
    - MikroTikRouter, HotspotUser, PPPoESecret: Typed data models.
"""

from app.integrations.mikrotik.client import (
    MikroTikClient,
    MikroTikConnection,
    MikroTikAPIError,
    MikroTikConnectionError,
    MikroTikAuthError,
    MikroTikCommandError,
)
from app.integrations.mikrotik.models import (
    MikroTikRouter,
    HotspotUser,
    PPPoESecret,
    HotspotActiveSession,
    PPPoEActiveSession,
    HotspotProfile,
    InterfaceStats,
    RouterHealth,
)

__all__ = [
    # Client
    'MikroTikClient',
    'MikroTikConnection',

    # Exceptions
    'MikroTikAPIError',
    'MikroTikConnectionError',
    'MikroTikAuthError',
    'MikroTikCommandError',

    # Data models
    'MikroTikRouter',
    'HotspotUser',
    'PPPoESecret',
    'HotspotActiveSession',
    'PPPoEActiveSession',
    'HotspotProfile',
    'InterfaceStats',
    'RouterHealth',
]