from flask import jsonify
import jwt
from datetime import datetime
import logging
from typing import Optional, List, Set

logger = logging.getLogger(__name__)


class AuthMiddleware:
    

    def __init__(self, app, secret_key=None, exempt_paths=None):
        """
        Initialize with WSGI app and configuration.

        Args:
            app: WSGI application to wrap
            secret_key: JWT signing secret (from app config if not provided)
            exempt_paths: Legacy exempt paths list (backward compatibility)
        """
        self.app = app
        self.secret_key = secret_key
        self.exempt_paths = exempt_paths or []

        # JWT configuration
        self.algorithm = 'HS256'
        self.expected_audience = 'isp-saas-api'
        self.expected_issuer = 'isp-saas'
        self.token_type = 'access'  # Only validate access tokens, not refresh tokens

        # =====================================================================
        # WEB ROUTE EXEMPTIONS — Prefix matching
        # These paths and everything under them are publicly accessible.
        # =====================================================================
        self.web_exempt_prefixes: List[str] = [
            '/static/',      # Static assets (CSS, JS, images)
            '/health',       # Health check endpoints
        ]

        # =====================================================================
        # WEB ROUTE EXEMPTIONS — Exact matching only
        # These specific pages are public, but their sub-paths require auth.
        # /organization     → public (org selection page)
        # /organization/{id}/routers  → AUTHENTICATED
        # =====================================================================
        self.web_exact_exempt: Set[str] = {
            # Auth pages
            '/',
            '/login',
            '/logout',
            '/register',
            '/register-success',
            '/verify-email',

            # Dashboard (checks session internally)
            '/dashboard',

            # Super admin login
            '/super-admin',

            # Organization selection page (NOT management pages)
            '/organization',
            '/organization/',

            # Public hotspot landing / captive portal
            '/hotspot',
            '/hotspot/',

            # Health check
            '/health',
        }

        # =====================================================================
        # API ROUTE EXEMPTIONS — Exact matching only
        # These API endpoints are public. Everything else under /api/ requires JWT.
        # =====================================================================
        self.api_exact_exempt: Set[str] = {
            # Health
            '/api/v1/health',

            # Authentication
            '/api/v1/auth/login',
            '/api/v1/auth/register',
            '/api/v1/auth/refresh',
            '/api/v1/auth/forgot-password',
            '/api/v1/auth/reset-password',

            # Email verification & registration
            '/api/v1/auth/send-verification',
            '/api/v1/auth/verify-email',
            '/api/v1/auth/register-organization',
            '/api/v1/auth/resend-verification',
            '/api/v1/auth/check-email',
            '/api/v1/auth/check-slug',

            # Router connection test (used during onboarding)
            '/api/v1/routers/test',

            # RADIUS endpoints — called by FreeRADIUS, not browsers
            '/api/radius/authenticate',
            '/api/radius/accounting',
            '/api/radius/accounting/start',
            '/api/radius/accounting/stop',
        }

        logger.info(
            f"AuthMiddleware initialized | "
            f"audience={self.expected_audience} | "
            f"issuer={self.expected_issuer} | "
            f"web_prefix_exempt={len(self.web_exempt_prefixes)} | "
            f"web_exact_exempt={len(self.web_exact_exempt)} | "
            f"api_exact_exempt={len(self.api_exact_exempt)}"
        )

    # -------------------------------------------------------------------------
    # WSGI CALLABLE
    # -------------------------------------------------------------------------

    def __call__(self, environ, start_response):
        """WSGI callable — main entry point for every request."""
        path = environ.get('PATH_INFO', '')

        logger.debug(f"AuthMiddleware checking path: {path}")

        # Check if this path is exempt from authentication
        if self._is_exempt_path(path):
            logger.debug(f"Path {path} is exempt from authentication")
            return self.app(environ, start_response)

        # For all non-exempt paths, require JWT authentication
        auth_header = environ.get('HTTP_AUTHORIZATION', '')
        token = self._extract_token(auth_header)

        if not token:
            logger.warning(f"No token provided for protected path: {path}")
            return self._unauthorized_response(
                start_response, "Missing or invalid authorization header"
            )

        # Validate the JWT token
        payload = self._validate_token(token)
        if not payload:
            logger.warning(f"Invalid token for protected path: {path}")
            return self._unauthorized_response(
                start_response, "Invalid or expired token"
            )

        # Inject user context into environ for downstream handlers
        environ['JWT_PAYLOAD'] = payload
        environ['USER_ID'] = payload.get('user_id')
        environ['ORGANIZATION_ID'] = payload.get('organization_id')
        environ['USER_ROLE'] = payload.get('role')
        environ['USER_EMAIL'] = payload.get('email')
        environ['USER_PERMISSIONS'] = payload.get('permissions', [])

        logger.debug(
            f"Authenticated user {payload.get('user_id')} "
            f"for path: {path}"
        )

        return self.app(environ, start_response)

    # -------------------------------------------------------------------------
    # PATH EXEMPTION LOGIC
    # -------------------------------------------------------------------------

    def _is_exempt_path(self, path: str) -> bool:
        """
        Determine if a path is exempt from authentication.

        Priority order (first match wins):
            1. API exact exemptions (highest priority — most secure)
            2. Web exact exemptions (specific public pages)
            3. Web prefix exemptions (static files, health checks)
            4. Legacy exempt_paths (backward compatibility)

        Critical rule: API routes (/api/*) are NEVER matched by web prefixes.
        This prevents accidental exposure of API endpoints.

        Args:
            path: Request path (e.g., '/organization/abc123/routers/create')

        Returns:
            True if path does not require authentication
        """
        # =====================================================================
        # STEP 1: API exact exemptions (highest priority)
        # API routes are EXACT MATCH ONLY — no prefix matching for security
        # =====================================================================
        if path.startswith('/api/'):
            # Try exact match
            if path in self.api_exact_exempt:
                logger.debug(f"Path {path} matched API exact exempt")
                return True

            # Try with trailing slash normalization
            normalized = path.rstrip('/')
            if normalized != path and normalized in self.api_exact_exempt:
                logger.debug(f"Path {path} matched API exempt (normalized)")
                return True

            # API routes not in exact exempt list → REQUIRE AUTHENTICATION
            return False

        # =====================================================================
        # STEP 2: Web exact exemptions
        # These are specific public pages — their sub-paths still require auth
        # =====================================================================
        if path in self.web_exact_exempt:
            logger.debug(f"Path {path} matched web exact exempt")
            return True

        # =====================================================================
        # STEP 3: Web prefix exemptions
        # Everything under these prefixes is public (static files, health)
        # =====================================================================
        for prefix in self.web_exempt_prefixes:
            if path.startswith(prefix):
                logger.debug(f"Path {path} matched web prefix exempt: {prefix}")
                return True

        # =====================================================================
        # STEP 4: Legacy exempt_paths (backward compatibility)
        # Handles paths passed via Flask app config
        # =====================================================================
        for exempt in self.exempt_paths:
            # Skip API routes in legacy list — already handled by exact match
            if exempt.startswith('/api/') and path != exempt:
                continue

            # Exact match
            if path == exempt:
                logger.debug(f"Path {path} matched legacy exempt exact: {exempt}")
                return True

            # Wildcard match (e.g., '/api/v1/routers/*/radius/retry')
            if '*' in exempt:
                import fnmatch
                if fnmatch.fnmatch(path, exempt):
                    logger.debug(f"Path {path} matched legacy wildcard: {exempt}")
                    return True

            # Prefix match for non-API routes only
            if not path.startswith('/api/') and not exempt.startswith('/api/'):
                if exempt.endswith('/') and path.startswith(exempt):
                    logger.debug(f"Path {path} matched legacy prefix: {exempt}")
                    return True
                if path.startswith(exempt + '/'):
                    logger.debug(f"Path {path} matched legacy prefix: {exempt}")
                    return True

        return False

    # -------------------------------------------------------------------------
    # TOKEN EXTRACTION & VALIDATION
    # -------------------------------------------------------------------------

    def _extract_token(self, auth_header: str) -> Optional[str]:
        """
        Extract Bearer token from Authorization header.

        Args:
            auth_header: Value of HTTP_AUTHORIZATION header

        Returns:
            JWT token string, or None if missing/invalid format
        """
        if not auth_header:
            return None
        if not auth_header.startswith('Bearer '):
            return None

        token = auth_header[7:]  # Remove 'Bearer ' prefix
        return token if token else None

    def _validate_token(self, token: str) -> Optional[dict]:
        """
        Validate JWT token with full signature, audience, and issuer checks.

        Validation steps:
            1. Cryptographic signature verification
            2. Expiration check (exp claim)
            3. Audience validation (aud claim)
            4. Issuer validation (iss claim)
            5. Token type check (must be 'access')
            6. Required claims check (exp, iat, sub)

        Args:
            token: JWT token string

        Returns:
            Decoded payload dictionary, or None if invalid
        """
        if not self.secret_key:
            logger.error("JWT_SECRET_KEY not configured in AuthMiddleware")
            return None

        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                audience=self.expected_audience,
                issuer=self.expected_issuer,
                options={
                    'verify_exp': True,
                    'verify_aud': True,
                    'verify_iss': True,
                    'require': ['exp', 'iat', 'sub'],
                }
            )

            # Validate token type (must be 'access', not 'refresh')
            token_type = payload.get('type')
            if token_type != self.token_type:
                logger.warning(
                    f"Invalid token type: '{token_type}'. "
                    f"Expected: '{self.token_type}'"
                )
                return None

            # Additional expiration check (defense in depth)
            exp = payload.get('exp')
            if exp and datetime.utcnow().timestamp() > exp:
                logger.warning("Token expired (timestamp check)")
                return None

            logger.debug(
                f"Token validated for user: {payload.get('user_id')} "
                f"({payload.get('email')})"
            )
            return payload

        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except jwt.InvalidAudienceError as e:
            logger.warning(
                f"Invalid audience: {e}. "
                f"Expected: '{self.expected_audience}'"
            )
            return None
        except jwt.InvalidIssuerError as e:
            logger.warning(
                f"Invalid issuer: {e}. "
                f"Expected: '{self.expected_issuer}'"
            )
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error validating token: {e}")
            return None

    # -------------------------------------------------------------------------
    # RESPONSE HELPERS
    # -------------------------------------------------------------------------

    def _unauthorized_response(self, start_response, message: str):
        """
        Return a 401 Unauthorized JSON response.

        Args:
            start_response: WSGI start_response callable
            message: Human-readable error message

        Returns:
            WSGI response body iterable
        """
        import json

        status = '401 Unauthorized'
        headers = [
            ('Content-Type', 'application/json'),
            ('WWW-Authenticate', 'Bearer realm="api"'),
        ]
        start_response(status, headers)

        body = json.dumps({
            'error': 'Unauthorized',
            'message': message,
        }).encode('utf-8')

        return [body]