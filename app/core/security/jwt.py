import jwt
from datetime import datetime, timedelta
from flask import current_app, g, request
from functools import wraps
from typing import Dict, Any, Optional, List
import hashlib
import hmac
import secrets

from app.core.exceptions.handlers import AuthenticationError, AuthorizationError


class JWTService:
    """JWT token management service with Redis-backed blacklist and versioning"""
    
    def __init__(self, app=None):
        self.app = app
        self.token_blacklist = None  # Redis client for blacklist
        self.user_token_versions = {}  # Fallback in-memory cache (only used if Redis unavailable)
        
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
        
        # Initialize Redis blacklist from app.extensions (set by extensions.py)
        if 'redis' in app.extensions and app.extensions['redis']:
            self.token_blacklist = app.extensions['redis']
            # Test Redis connection
            try:
                self.token_blacklist.ping()
                print(f"[JWTService] Redis connected successfully for blacklist operations")
            except Exception as e:
                print(f"[JWTService] Redis ping failed: {e}. Using in-memory fallback.")
                self.token_blacklist = None
        else:
            print(f"[JWTService] Redis not found in app.extensions. Using in-memory fallback for token blacklist.")
        
        if not self.secret_key:
            raise ValueError("JWT_SECRET_KEY must be configured")
        
        print(f"[JWTService] Initialized with access expiry: {self.access_expires}, refresh expiry: {self.refresh_expires}")
    
    def generate_access_token(self, user_id: str, email: str, organization_id: str = None,
                              role: str = None, permissions: List[str] = None,
                              session_id: str = None) -> str:
        """Generate JWT access token with security features"""
        
        # Get token version for user (for revocation)
        token_version = self._get_user_token_version(user_id)
        
        now = datetime.utcnow()
        
        payload = {
            # Standard claims
            'iss': current_app.config.get('JWT_ISSUER', 'isp-saas'),
            'sub': user_id,
            'aud': current_app.config.get('JWT_AUDIENCE', 'isp-saas-api'),
            'iat': now,
            'exp': now + self.access_expires,
            'jti': self._generate_token_id(user_id, session_id),
            
            # Custom claims
            'user_id': user_id,
            'email': email,
            'organization_id': organization_id,
            'role': role,
            'permissions': permissions or [],
            'type': 'access',
            'token_version': token_version,
            'session_id': session_id
        }
        
        # Add device fingerprint if available
        device_fingerprint = self._get_device_fingerprint()
        if device_fingerprint:
            payload['device_fp'] = device_fingerprint
        
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    def generate_refresh_token(self, user_id: str, session_id: str = None) -> str:
        """Generate refresh token with rotation support"""
        
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
        """Decode and validate JWT token with full security checks"""
        
        try:
            secret = self.secret_key if token_type == 'access' else self.refresh_secret_key
            
            # Configure validation options
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
                issuer=current_app.config.get('JWT_ISSUER', 'isp-saas')
            )
            
            # Validate token type
            if payload.get('type') != token_type:
                raise AuthenticationError(f'Invalid token type. Expected {token_type}')
            
            # Check if token is blacklisted
            jti = payload.get('jti')
            if jti and self._is_token_blacklisted(jti):
                raise AuthenticationError('Token has been revoked')
            
            # Validate token version matches user's current version
            user_id = payload.get('user_id')
            if user_id:
                current_version = self._get_user_token_version(user_id)
                token_version = payload.get('token_version', 0)
                if token_version != current_version:
                    raise AuthenticationError('Token version mismatch. Please re-authenticate')
            
            # Validate device fingerprint (if enabled)
            if current_app.config.get('JWT_ENFORCE_DEVICE_FINGERPRINT', False):
                if not self._validate_device_fingerprint(payload):
                    raise AuthenticationError('Device fingerprint mismatch')
            
            return payload
            
        except jwt.ExpiredSignatureError:
            raise AuthenticationError('Token has expired')
        except jwt.InvalidTokenError as e:
            raise AuthenticationError(f'Invalid token: {str(e)}')
    
    def refresh_access_token(self, refresh_token: str) -> Dict[str, str]:
        """Generate new access token from refresh token (with rotation)"""
        
        # Decode refresh token
        payload = self.decode_token(refresh_token, 'refresh')
        user_id = payload.get('user_id')
        session_id = payload.get('session_id')
        
        # Get current user from database
        from app.modules.auth.repository import UserRepository
        user_repo = UserRepository()
        user = user_repo.get_by_id(user_id)
        
        if not user or not user.is_active:
            raise AuthenticationError('User not found or inactive')
        
        # Generate new token pair (refresh token rotation)
        new_access_token = self.generate_access_token(
            user_id=str(user.id),
            email=user.email,
            organization_id=str(user.organization_id) if user.organization_id else None,
            role=user.role,
            permissions=user.permissions,
            session_id=session_id
        )
        
        new_refresh_token = self.generate_refresh_token(
            user_id=str(user.id),
            session_id=session_id
        )
        
        # Blacklist old refresh token (prevent replay attacks)
        old_jti = payload.get('jti')
        if old_jti:
            self._blacklist_token(old_jti, self.refresh_expires)
        
        return {
            'access_token': new_access_token,
            'refresh_token': new_refresh_token,
            'expires_in': int(self.access_expires.total_seconds())
        }
    
    def revoke_user_tokens(self, user_id: str, session_id: str = None):
        """Revoke all tokens for a user (logout all devices)"""
        
        # Increment token version (invalidates all existing tokens)
        new_version = self._increment_user_token_version(user_id)
        
        # If session_id provided, also mark that specific session as revoked
        if session_id:
            self._revoke_session(user_id, session_id)
        
        print(f"[JWTService] Revoked all tokens for user {user_id}, new version: {new_version}")
        return new_version
    
    def revoke_token(self, token: str):
        """Revoke a single token (logout current device)"""
        
        try:
            payload = self.decode_token(token, 'access', verify_exp=False)
            jti = payload.get('jti')
            
            if jti:
                # Get remaining TTL
                exp = payload.get('exp', 0)
                ttl = max(0, exp - datetime.utcnow().timestamp())
                
                self._blacklist_token(jti, timedelta(seconds=ttl))
                print(f"[JWTService] Revoked single token: {jti}")
        except Exception as e:
            print(f"[JWTService] Error revoking token: {e}")
    
    def _generate_token_id(self, user_id: str, session_id: str = None, prefix: str = 'at') -> str:
        """Generate unique token ID for blacklisting"""
        random_part = secrets.token_hex(16)
        if session_id:
            return f"{prefix}:{user_id}:{session_id}:{random_part}"
        return f"{prefix}:{user_id}:{random_part}"
    
    def _blacklist_token(self, jti: str, ttl: timedelta):
        """Add token to blacklist in Redis"""
        if self.token_blacklist:
            key = f"token_blacklist:{jti}"
            self.token_blacklist.setex(key, ttl, '1')
    
    def _is_token_blacklisted(self, jti: str) -> bool:
        """Check if token is blacklisted in Redis"""
        if not jti or not self.token_blacklist:
            return False
        
        try:
            key = f"token_blacklist:{jti}"
            return bool(self.token_blacklist.exists(key))
        except Exception:
            return False
    
    def _get_user_token_version(self, user_id: str) -> int:
        """Get current token version for user from Redis or fallback"""
        if self.token_blacklist:
            try:
                key = f"user_token_version:{user_id}"
                version = self.token_blacklist.get(key)
                if version:
                    return int(version)
            except Exception:
                pass
        
        # Fallback to in-memory cache
        return self.user_token_versions.get(user_id, 1)
    
    def _increment_user_token_version(self, user_id: str) -> int:
        """Increment token version (revokes all existing tokens)"""
        if self.token_blacklist:
            try:
                key = f"user_token_version:{user_id}"
                new_version = self.token_blacklist.incr(key)
                self.token_blacklist.expire(key, timedelta(days=30))
                return new_version
            except Exception:
                pass
        
        # Fallback to in-memory
        new_version = self.user_token_versions.get(user_id, 1) + 1
        self.user_token_versions[user_id] = new_version
        return new_version
    
    def _revoke_session(self, user_id: str, session_id: str):
        """Revoke specific session in Redis"""
        if self.token_blacklist:
            try:
                key = f"user_session:{user_id}:{session_id}"
                self.token_blacklist.setex(key, self.refresh_expires, 'revoked')
            except Exception:
                pass
    
    def _get_device_fingerprint(self) -> Optional[str]:
        """Generate device fingerprint from request"""
        if not request:
            return None
        
        # Combine various request attributes
        fingerprint_data = [
            request.user_agent.string if request.user_agent else '',
            request.headers.get('Accept-Language', ''),
            request.headers.get('Sec-CH-UA', ''),  # Client hints
        ]
        
        fingerprint = '|'.join(fingerprint_data)
        if fingerprint:
            return hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
        
        return None
    
    def _validate_device_fingerprint(self, payload: Dict) -> bool:
        """Validate device fingerprint matches"""
        current_fp = self._get_device_fingerprint()
        token_fp = payload.get('device_fp')
        
        if not token_fp or not current_fp:
            return True 
        
        # Constant-time comparison
        return hmac.compare_digest(token_fp, current_fp)


# ============================================================================
# DECORATORS
# ============================================================================

def token_required(f):
    """Decorator to require valid JWT token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            raise AuthenticationError('Missing or invalid authorization header')
        
        token = auth_header.split(' ')[1]
        
        # Use JWT service to validate
        jwt_service = current_app.extensions.get('jwt_service')
        if not jwt_service:
            raise RuntimeError("JWT service not initialized")
        
        payload = jwt_service.decode_token(token, 'access')
        
        # Set user context in Flask g object
        g.user_id = payload.get('user_id')
        g.user_email = payload.get('email')
        g.organization_id = payload.get('organization_id')
        g.user_role = payload.get('role')
        g.user_permissions = payload.get('permissions', [])
        g.token_payload = payload
        
        return f(*args, **kwargs)
    return decorated


def optional_token(f):
    """Optional authentication: attach user if valid token is provided, but don't require it"""
    
    @wraps(f)
    def decorated(*args, **kwargs):
        # Always initialize to None (prevents leakage from previous requests)
        g.user_id = None
        g.user_email = None
        g.organization_id = None
        g.user_role = None
        g.user_permissions = []

        auth_header = request.headers.get('Authorization', '')

        if auth_header.startswith('Bearer '):
            parts = auth_header.split()

            if len(parts) == 2:
                token = parts[1]

                jwt_service = current_app.extensions.get('jwt_service')

                if jwt_service:
                    try:
                        payload = jwt_service.decode_token(token, 'access')

                        # Attach user context
                        g.user_id = payload.get('user_id')
                        g.user_email = payload.get('email')
                        g.organization_id = payload.get('organization_id')
                        g.user_role = payload.get('role')
                        g.user_permissions = payload.get('permissions', [])

                    except Exception as e:
                        # Optional auth: ignore invalid tokens but log them
                        current_app.logger.debug(f"Optional token validation failed: {str(e)}")

        return f(*args, **kwargs)

    return decorated


def permission_required(permission: str):
    """Decorator to require specific permission"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not hasattr(g, 'user_permissions'):
                raise AuthorizationError('Authentication required')
            
            if permission not in g.user_permissions:
                raise AuthorizationError(f'Permission required: {permission}')
            
            return f(*args, **kwargs)
        return decorated
    return decorator


def role_required(roles: List[str]):
    """Decorator to require specific role"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not hasattr(g, 'user_role'):
                raise AuthorizationError('Authentication required')
            
            if g.user_role not in roles:
                raise AuthorizationError(f'Role required: {", ".join(roles)}. Current role: {g.user_role}')
            
            return f(*args, **kwargs)
        return decorated
    return decorator


def organization_required(f):
    """Decorator to require organization context and validate access"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(g, 'organization_id') or not g.organization_id:
            raise AuthorizationError('Organization context required')
        
        # Validate user belongs to this organization
        if hasattr(g, 'user_id') and g.user_id:
            # Check organization membership
            from app.modules.organization.repository import OrganizationRepository
            org_repo = OrganizationRepository()
            
            if not org_repo.user_belongs_to_organization(g.user_id, g.organization_id):
                # Check if user is super admin (bypass organization check)
                if not hasattr(g, 'user_role') or g.user_role != 'super_admin':
                    raise AuthorizationError('You do not have access to this organization')
        
        return f(*args, **kwargs)
    return decorated