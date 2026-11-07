"""Authentication for the FreeRADIUS REST endpoints.

FreeRADIUS calls these endpoints server-to-server with ``rlm_rest``. They were
previously open: anyone who could reach the URL could authenticate users, forge
accounting records, or disconnect sessions.

The shared secret is configured with ``RADIUS_REST_SECRET`` and sent by
FreeRADIUS as the ``X-Radius-Secret`` header::

    # /etc/freeradius/3.0/mods-config/rest/rest
    authenticate {
        uri = "http://app:8080/api/radius/authenticate"
        method = 'post'
        body = 'json'
        header = "X-Radius-Secret: ${radius_rest_secret}"
    }

If the secret is not configured the endpoints refuse to serve (503) rather than
silently allowing anonymous access.
"""

from __future__ import annotations

import hmac
import os
from functools import wraps

from flask import current_app, jsonify, request

from app.core.logging.logger import logger

HEADER = 'X-Radius-Secret'


def _expected_secret():
    return current_app.config.get('RADIUS_REST_SECRET') or os.environ.get('RADIUS_REST_SECRET')


def radius_secret_required(view):
    """Reject RADIUS callbacks that do not present the shared secret."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        expected = _expected_secret()
        if not expected:
            logger.error(
                "RADIUS_REST_SECRET is not configured; refusing %s from %s",
                request.path, request.remote_addr,
            )
            return jsonify({'result': 'fail', 'error': 'radius integration not configured'}), 503

        provided = request.headers.get(HEADER, '')
        # compare_digest keeps the comparison constant-time.
        if not hmac.compare_digest(provided, str(expected)):
            logger.warning(
                "Rejected RADIUS request to %s with an invalid secret from %s",
                request.path, request.remote_addr,
            )
            return jsonify({'result': 'fail', 'error': 'unauthorized'}), 401

        return view(*args, **kwargs)

    return wrapper
