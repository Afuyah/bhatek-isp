from flask import jsonify
import jwt
from datetime import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class AuthMiddleware:
    """JWT authentication middleware for WSGI wrapping"""
    
    def __init__(self, app, secret_key=None, exempt_paths=None):
        """Initialize with WSGI app and configuration"""
        self.app = app
        self.secret_key = secret_key
        self.exempt_paths = exempt_paths or []
        
        # Get JWT configuration from app config if available
        self.algorithm = 'HS256'
        self.expected_audience = 'isp-saas-api'  # Match JWTService default
        self.expected_issuer = 'isp-saas'  # Match JWTService default
        self.token_type = 'access'  # We only validate access tokens
        
        # Separate web and API paths for better handling
        self.web_exempt_prefixes = [
            '/',
            '/login',
            '/logout',
            '/register',
            '/register-success',
            '/verify-email',
            '/dashboard',
            '/super-admin',
            '/organization/',
            '/hotspot/',
            '/static/',
            '/health'
        ]
        
        self.api_exact_exempt = [
            '/api/v1/health',
            '/api/v1/auth/login',
            '/api/v1/auth/register',
            '/api/v1/auth/refresh',
            '/api/v1/auth/forgot-password',
            '/api/v1/auth/reset-password',
        ]
        
        logger.info(f"AuthMiddleware initialized with audience: {self.expected_audience}")
        logger.debug(f"Web exempt prefixes: {self.web_exempt_prefixes}")
        logger.debug(f"API exact exempt: {self.api_exact_exempt}")
    
    def __call__(self, environ, start_response):
        """WSGI callable"""
        path = environ.get('PATH_INFO', '')
        
        # Log for debugging
        logger.debug(f"AuthMiddleware checking path: {path}")
        
        # Check if this path is exempt from authentication
        if self._is_exempt_path(path):
            logger.debug(f"Path {path} is exempt from authentication")
            return self.app(environ, start_response)
        
        # For all non-exempt paths, require authentication
        # Extract and validate token
        auth_header = environ.get('HTTP_AUTHORIZATION', '')
        token = self._extract_token(auth_header)
        
        if not token:
            logger.warning(f"No token provided for protected path: {path}")
            return self._unauthorized_response(start_response, "Missing or invalid authorization header")
        
        # Validate token
        payload = self._validate_token(token)
        if not payload:
            logger.warning(f"Invalid token for protected path: {path}")
            return self._unauthorized_response(start_response, "Invalid or expired token")
        
        # Set user context in environ for downstream
        environ['JWT_PAYLOAD'] = payload
        environ['USER_ID'] = payload.get('user_id')
        environ['ORGANIZATION_ID'] = payload.get('organization_id')
        environ['USER_ROLE'] = payload.get('role')
        environ['USER_EMAIL'] = payload.get('email')
        environ['USER_PERMISSIONS'] = payload.get('permissions', [])
        
        return self.app(environ, start_response)
    
    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from authentication"""
        
        # Check exact matches for API routes FIRST (highest priority)
        if path in self.api_exact_exempt:
            logger.debug(f"Path {path} matched exact API exempt")
            return True
        
        # Also check API exact matches with trailing slash variation
        if path.rstrip('/') in self.api_exact_exempt:
            logger.debug(f"Path {path} matched API exempt (with slash normalization)")
            return True
        
        # For web routes (non-API), check prefix matches
        # This prevents API routes from being matched by web prefixes
        if not path.startswith('/api/'):
            for prefix in self.web_exempt_prefixes:
                # Exact match for web routes
                if path == prefix:
                    logger.debug(f"Path {path} matched exact web exempt: {prefix}")
                    return True
                
                # Prefix match for directories (with or without trailing slash)
                if prefix.endswith('/'):
                    if path.startswith(prefix):
                        logger.debug(f"Path {path} matched web exempt prefix: {prefix}")
                        return True
                else:
                    # For non-trailing-slash prefixes, match exactly or with slash
                    if path == prefix or path.startswith(prefix + '/'):
                        logger.debug(f"Path {path} matched web exempt prefix: {prefix}")
                        return True
        
        # Check legacy exempt_paths if provided (backward compatibility)
        for exempt in self.exempt_paths:
            # Skip API routes in legacy list - they should be handled by exact match only
            if exempt.startswith('/api/') and path != exempt:
                continue
                
            # Exact match
            if path == exempt:
                logger.debug(f"Path {path} matched legacy exempt exact: {exempt}")
                return True
            
            # For non-API routes, check prefix matches
            if not path.startswith('/api/') and not exempt.startswith('/api/'):
                if exempt.endswith('/') and path.startswith(exempt):
                    logger.debug(f"Path {path} matched legacy exempt prefix: {exempt}")
                    return True
                if path.startswith(exempt):
                    # Make sure we don't accidentally match /org with /api/v1/org
                    if exempt == '/organization' and path.startswith('/api/'):
                        continue
                    logger.debug(f"Path {path} matched legacy exempt startswith: {exempt}")
                    return True
        
        return False
    
    def _extract_token(self, auth_header: str) -> Optional[str]:
        """Extract Bearer token from Authorization header"""
        if not auth_header or not auth_header.startswith('Bearer '):
            return None
        return auth_header.split(' ')[1]
    
    def _validate_token(self, token: str) -> Optional[dict]:
        """Validate JWT token with signature verification - matches JWTService"""
        if not self.secret_key:
            logger.error("JWT_SECRET_KEY not configured in AuthMiddleware")
            return None
        
        try:
            # Decode with audience and issuer validation (matching JWTService)
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
                    'require': ['exp', 'iat', 'sub']
                }
            )
            
            # Check token type
            if payload.get('type') != self.token_type:
                logger.warning(f"Invalid token type: {payload.get('type')}. Expected {self.token_type}")
                return None
            
            # Check if token is expired (additional check)
            exp = payload.get('exp')
            if exp and datetime.utcnow().timestamp() > exp:
                logger.warning("Token expired (timestamp check)")
                return None
            
            logger.debug(f"Token validated successfully for user: {payload.get('user_id')}")
            return payload
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidAudienceError as e:
            logger.warning(f"Invalid audience: {str(e)}. Expected: {self.expected_audience}")
            return None
        except jwt.InvalidIssuerError as e:
            logger.warning(f"Invalid issuer: {str(e)}. Expected: {self.expected_issuer}")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error validating token: {str(e)}")
            return None
    
    def _unauthorized_response(self, start_response, message: str):
        """Return 401 Unauthorized response"""
        status = '401 Unauthorized'
        headers = [
            ('Content-Type', 'application/json'),
            ('WWW-Authenticate', 'Bearer realm="api"')
        ]
        start_response(status, headers)
        
        import json
        return [json.dumps({'error': message}).encode('utf-8')]