"""Identifier coercion.

UUIDs arrive as strings from three places that cannot carry a real ``UUID``
object: the session cookie, URL path parameters, and JSON request bodies. The
PostgreSQL ``UUID`` column type, however, binds with a processor that expects an
object with ``.hex`` — so passing a raw string raises ``AttributeError`` inside
the driver rather than returning "not found".

Every repository that accepts an id from an untrusted boundary should normalise
it through :func:`as_uuid` first.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID


def as_uuid(value) -> Optional[UUID]:
    """Return ``value`` as a UUID, or ``None`` if it cannot be one.

    Accepts UUID instances, UUID strings, and bytes-free representations such as
    the 32-character hex form.
    """
    if value is None or isinstance(value, UUID):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return UUID(bytes=bytes(value))
        except (ValueError, TypeError):
            return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
