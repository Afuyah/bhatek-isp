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
        
        # Log the exempt paths for debugging
        logger.info(f"AuthMiddleware initialized with exempt paths: {self.exempt_paths}")
    
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
        
        return self.app(environ, start_response)
    
    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from authentication"""
        for exempt in self.exempt_paths:
            # Exact match
            if path == exempt:
                return True
            # Path starts with exempt (for directories like /static)
            if exempt.endswith('/') and path.startswith(exempt):
                return True
            # Path matches pattern
            if path.startswith(exempt):
                return True
        return False
    
    def _extract_token(self, auth_header: str) -> Optional[str]:
        """Extract Bearer token from Authorization header"""
        if not auth_header or not auth_header.startswith('Bearer '):
            return None
        return auth_header.split(' ')[1]
    
    def _validate_token(self, token: str) -> Optional[dict]:
        """Validate JWT token with signature verification"""
        if not self.secret_key:
            logger.error("JWT_SECRET_KEY not configured in AuthMiddleware")
            return None
        
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=['HS256'],
                options={'verify_exp': True}
            )
            
            # Check token type
            if payload.get('type') != 'access':
                logger.warning(f"Invalid token type: {payload.get('type')}")
                return None
            
            return payload
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
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