import jwt
from datetime import datetime, timedelta
from flask import current_app, g, request
from functools import wraps
from typing import Dict, Any, Optional, List
import hashlib
import hmac

from app.core.exceptions.handlers import AuthenticationError, AuthorizationError


class JWTService:
    """JWT token management service for generating tokens only"""
    
    def __init__(self, app=None):
        self.app = app
        self.token_blacklist = None
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app
        self.secret_key = app.config.get('JWT_SECRET_KEY')
        self.refresh_secret_key = app.config.get('JWT_REFRESH_SECRET_KEY', self.secret_key)
        self.access_expires = app.config.get('JWT_ACCESS_TOKEN_EXPIRES', timedelta(minutes=15))
        self.refresh_expires = app.config.get('JWT_REFRESH_TOKEN_EXPIRES', timedelta(days=7))
        self.algorithm = app.config.get('JWT_ALGORITHM', 'HS256')
        
        # Initialize Redis blacklist if available
        if 'redis' in app.extensions:
            self.token_blacklist = app.extensions['redis']
        
        if not self.secret_key:
            raise ValueError("JWT_SECRET_KEY must be configured")
    
    def generate_access_token(self, user_id: str, email: str, organization_id: str = None,
                              role: str = None, permissions: List[str] = None,
                              session_id: str = None) -> str:
        """Generate JWT access token"""
        
        token_version = self._get_user_token_version(user_id)
        
        now = datetime.utcnow()
        
        payload = {
            'iss': current_app.config.get('JWT_ISSUER', 'isp-saas'),
            'sub': user_id,
            'aud': current_app.config.get('JWT_AUDIENCE', 'isp-saas-api'),
            'iat': now,
            'exp': now + self.access_expires,
            'jti': self._generate_token_id(user_id, session_id),
            'user_id': user_id,
            'email': email,
            'organization_id': organization_id,
            'role': role,
            'permissions': permissions or [],
            'type': 'access',
            'token_version': token_version,
            'session_id': session_id
        }
        
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    def generate_refresh_token(self, user_id: str, session_id: str = None) -> str:
        """Generate refresh token"""
        
        now = datetime.utcnow()
        
        payload = {
            'iss': current_app.config.get('JWT_ISSUER', 'isp-saas'),
            'sub': user_id,
            'aud': current_app.config.get('JWT_AUDIENCE', 'isp-saas-api'),
            'iat': now,
            'exp': now + self.refresh_expires,
            'jti': self._generate_token_id(user_id, session_id, prefix='rt'),
            'user_id': user_id,
            'type': 'refresh',
            'session_id': session_id,
            'token_version': self._get_user_token_version(user_id)
        }
        
        return jwt.encode(payload, self.refresh_secret_key, algorithm=self.algorithm)
    
    def decode_token(self, token: str, token_type: str = 'access', 
                     verify_exp: bool = True, verify_signature: bool = True) -> Dict[str, Any]:
        """Decode and validate JWT token"""
        
        try:
            secret = self.secret_key if token_type == 'access' else self.refresh_secret_key
            
            options = {
                'verify_exp': verify_exp,
                'verify_signature': verify_signature,
                'require': ['exp', 'iat', 'sub']
            }
            
            payload = jwt.decode(
                token,
                secret,
                algorithms=[self.algorithm],
                options=options,
                audience=current_app.config.get('JWT_AUDIENCE', 'isp-saas-api'),
                issuer=current_app.config.get('JWT_ISSUER', 'isp-saas-api')
            )
            
            if payload.get('type') != token_type:
                raise AuthenticationError(f'Invalid token type. Expected {token_type}')
            
            if self._is_token_blacklisted(payload.get('jti')):
                raise AuthenticationError('Token has been revoked')
            
            return payload
            
        except jwt.ExpiredSignatureError:
            raise AuthenticationError('Token has expired')
        except jwt.InvalidTokenError as e:
            raise AuthenticationError(f'Invalid token: {str(e)}')
    
    def _generate_token_id(self, user_id: str, session_id: str = None, prefix: str = 'at') -> str:
        """Generate unique token ID"""
        import secrets
        random_part = secrets.token_hex(16)
        if session_id:
            return f"{prefix}:{user_id}:{session_id}:{random_part}"
        return f"{prefix}:{user_id}:{random_part}"
    
    def _blacklist_token(self, jti: str, ttl: timedelta):
        """Add token to blacklist"""
        if self.token_blacklist:
            key = f"token_blacklist:{jti}"
            self.token_blacklist.setex(key, ttl, '1')
    
    def _is_token_blacklisted(self, jti: str) -> bool:
        """Check if token is blacklisted"""
        if not jti or not self.token_blacklist:
            return False
        key = f"token_blacklist:{jti}"
        return bool(self.token_blacklist.exists(key))
    
    def _get_user_token_version(self, user_id: str) -> int:
        """Get current token version for user"""
        if self.token_blacklist:
            key = f"user_token_version:{user_id}"
            version = self.token_blacklist.get(key)
            if version:
                return int(version)
        return 1
    
    def revoke_user_tokens(self, user_id: str) -> int:
        """Revoke all tokens for a user"""
        if self.token_blacklist:
            key = f"user_token_version:{user_id}"
            new_version = self.token_blacklist.incr(key)
            self.token_blacklist.expire(key, timedelta(days=30))
            return new_version
        return 1


# ============================================================================
# DECORATORS - These work with the AuthMiddleware
# ============================================================================

def token_required(f):
    """
    Decorator to require valid JWT token.
    AuthMiddleware already validates the token and sets environ variables.
    This decorator just transfers them to Flask's g object.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check if user context was set by middleware
        if not hasattr(request, 'environ'):
            raise AuthenticationError('Authentication required')
        
        # Get user info from request environ (set by AuthMiddleware)
        user_id = request.environ.get('USER_ID')
        
        if not user_id:
            raise AuthenticationError('Authentication required. Please provide a valid token.')
        
        # Transfer to Flask's g object for easy access in views
        g.user_id = user_id
        g.user_email = request.environ.get('USER_EMAIL')
        g.organization_id = request.environ.get('ORGANIZATION_ID')
        g.user_role = request.environ.get('USER_ROLE')
        g.user_permissions = request.environ.get('USER_PERMISSIONS', [])
        
        return f(*args, **kwargs)
    
    return decorated


def optional_token(f):
    """Optional authentication: attach user if valid token is provided"""
    
    @wraps(f)
    def decorated(*args, **kwargs):
        # Initialize to None
        g.user_id = None
        g.user_email = None
        g.organization_id = None
        g.user_role = None
        g.user_permissions = []
        
        # Check if user context was set by middleware
        user_id = request.environ.get('USER_ID')
        
        if user_id:
            # Transfer to Flask's g object
            g.user_id = user_id
            g.user_email = request.environ.get('USER_EMAIL')
            g.organization_id = request.environ.get('ORGANIZATION_ID')
            g.user_role = request.environ.get('USER_ROLE')
            g.user_permissions = request.environ.get('USER_PERMISSIONS', [])
        
        return f(*args, **kwargs)
    
    return decorated


def permission_required(permission: str):
    """Decorator to require specific permission"""
    def decorator(f):
        @wraps(f)
        @token_required
        def decorated(*args, **kwargs):
            if not hasattr(g, 'user_permissions') or not g.user_permissions:
                raise AuthorizationError('No permissions assigned')
            
            if permission not in g.user_permissions:
                raise AuthorizationError(f'Permission required: {permission}')
            
            return f(*args, **kwargs)
        return decorated
    return decorator


def role_required(roles: List[str]):
    """Decorator to require specific role"""
    def decorator(f):
        @wraps(f)
        @token_required
        def decorated(*args, **kwargs):
            if not hasattr(g, 'user_role') or not g.user_role:
                raise AuthorizationError('No role assigned')
            
            if g.user_role not in roles:
                raise AuthorizationError(f'Role required: {", ".join(roles)}')
            
            return f(*args, **kwargs)
        return decorated
    return decorator


def organization_required(f):
    """Decorator to require organization context"""
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if not hasattr(g, 'organization_id') or not g.organization_id:
            raise AuthorizationError('Organization context required')
        
        return f(*args, **kwargs)
    return decorated