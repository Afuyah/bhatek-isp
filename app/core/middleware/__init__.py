"""WSGI middleware stack.

Order matters. Wrapping is applied inside-out, so the last wrapper installed is
the first to see a request:

    gzip -> auth -> tenant -> request_id -> Flask

``gzip`` is outermost so it compresses whatever the app produces (and passes
``text/event-stream`` straight through — see ``app.core.performance``).
``auth`` runs before Flask so unauthenticated API calls never build a session.

An earlier version of this package exposed ``register_middlewares()``, which
wrapped the Flask *app object* instead of ``app.wsgi_app``; that re-entered
``Flask.__call__`` and recursed. It was dead code and has been replaced by the
single entry point below.
"""

from flask import Flask

from app.core.middleware.auth import AuthMiddleware
from app.core.middleware.rate_limit import RateLimiter
from app.core.middleware.request_id import RequestIDMiddleware
from app.core.middleware.tenant import TenantMiddleware
from app.core.performance import GzipMiddleware

#: Paths that never require a JWT. Everything else under /api/ does.
EXEMPT_PATHS = [
    # Health checks
    '/health',
    '/api/v1/health',

    # Public web pages
    '/',
    '/login',
    '/logout',
    '/register',
    '/register-success',
    '/verify-email',
    '/dashboard',
    '/super-admin',
    '/reports',

    # Captive portal
    '/hotspot',
    '/hotspot/',

    # Static assets
    '/static',
    '/favicon.ico',

    # API auth
    '/api/v1/auth/login',
    '/api/v1/auth/register',
    '/api/v1/auth/refresh',
    '/api/v1/auth/forgot-password',
    '/api/v1/auth/reset-password',

    # Registration / email verification
    '/api/v1/auth/send-verification',
    '/api/v1/auth/verify-email',
    '/api/v1/auth/register-organization',
    '/api/v1/auth/resend-verification',
    '/api/v1/auth/check-email',
    '/api/v1/auth/check-slug',

    # FreeRADIUS rlm_rest callbacks
    '/api/radius/authenticate',
    '/api/radius/disconnect',
    '/api/radius/accounting',
    '/api/radius/accounting/start',
    '/api/radius/accounting/stop',
    '/api/radius/accounting/interim',

    # Router RADIUS operations issued from the web UI (wildcards resolved
    # inside AuthMiddleware's matcher)
    '/api/v1/routers/*/radius/retry',
    '/api/v1/routers/*/radius/secret/generate',
    '/api/v1/routers/*/radius/regenerate',
    '/api/v1/routers/*/radius/test',
    '/api/v1/routers/*/full-sync',
]


def install_middleware_stack(app: Flask) -> Flask:
    """Attach the request pipeline to ``app``."""
    RateLimiter(app)  # before_request hook, not a WSGI wrapper

    wsgi_app = app.wsgi_app
    wsgi_app = RequestIDMiddleware(wsgi_app)
    wsgi_app = TenantMiddleware(wsgi_app)
    wsgi_app = AuthMiddleware(
        wsgi_app,
        secret_key=app.config.get('JWT_SECRET_KEY'),
        exempt_paths=EXEMPT_PATHS,
    )
    wsgi_app = GzipMiddleware(wsgi_app)

    app.wsgi_app = wsgi_app
    return app
