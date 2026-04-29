from flask import request, g, jsonify, current_app
from functools import wraps
import time
import redis
from typing import Dict, Tuple, Optional

class RateLimiter:
    """Rate limiting middleware"""
    
    def __init__(self, app=None):
        self.default_limit = 100
        self.default_window = 3600  # 1 hour
        self.rate_limits = {
            'POST:/api/auth/login': {'limit': 7, 'window': 300},  # 5 per 5 minutes
            'POST:/api/auth/register': {'limit': 10, 'window': 3600},  # 10 per hour
            'POST:/api/auth/forgot-password': {'limit': 5, 'window': 1800},  # 5 per 30 min
            'GET:/api/search': {'limit': 50, 'window': 60},  # 50 per minute
            'POST:/api/webhook': {'limit': 1000, 'window': 60},  # 1000 per minute
        }
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app
        self.redis_client = app.extensions.get('redis')
        
        if not self.redis_client:
            raise RuntimeError("Redis client not configured. Initialize Redis first.")
        
        @app.before_request
        def check_rate_limit():
            return self._check_rate_limit()
    
    def _check_rate_limit(self):
        """Check if request exceeds rate limit"""
        # Skip rate limiting for health checks
        if request.path.startswith('/health'):
            return None
        
        # Get rate limit for this endpoint
        rate_config = self._get_rate_limit_config()
        if not rate_config:
            return None  # No rate limit configured for this endpoint
        
        limit = rate_config['limit']
        window = rate_config['window']
        
        # Get identifier (user_id or IP for anonymous)
        identifier = self._get_identifier()
        
        # Create atomic key
        key = f"rate_limit:{identifier}:{request.method}:{request.path}"
        
        # Use Redis Lua script for atomic rate limiting
        current, ttl = self._atomic_increment(key, window)
        
        # Set rate limit headers
        g.rate_limit_limit = limit
        g.rate_limit_remaining = max(0, limit - current)
        g.rate_limit_reset = ttl
        
        if current > limit:
            return self._rate_limit_exceeded_response(limit, ttl)
        
        return None
    
    def _get_rate_limit_config(self) -> Optional[Dict]:
        """Get rate limit config for current endpoint"""
        key = f"{request.method}:{request.path}"
        
        # Check exact match first
        if key in self.rate_limits:
            return self.rate_limits[key]
        
        # Check wildcard patterns
        for pattern, config in self.rate_limits.items():
            if pattern.endswith('*'):
                if request.path.startswith(pattern[:-1]):
                    return config
        
        # Use default if configured
        if hasattr(self, 'default_limit'):
            return {'limit': self.default_limit, 'window': self.default_window}
        
        return None
    
    def _get_identifier(self) -> str:
        """Get unique identifier for rate limiting"""
        # Authenticated user - use user_id
        if hasattr(g, 'user_id') and g.user_id:
            return f"user:{g.user_id}"
        
        # Authenticated organization - use org_id
        if hasattr(g, 'organization_id') and g.organization_id:
            return f"org:{g.organization_id}"
        
        # Anonymous - use IP address (with fallback)
        return f"ip:{self._get_client_ip()}"
    
    def _get_client_ip(self) -> str:
        """Get real client IP address (handles proxies)"""
        # Check for forwarded headers (only trust configured proxies)
        trusted_proxies = current_app.config.get('TRUSTED_PROXIES', [])
        
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded and self._is_trusted_proxy(request.remote_addr, trusted_proxies):
            # Take first IP if multiple (client IP is first)
            return forwarded.split(',')[0].strip()
        
        # Check CloudFlare header
        cf_connecting = request.headers.get('CF-Connecting-IP')
        if cf_connecting:
            return cf_connecting
        
        # Check Real IP header
        real_ip = request.headers.get('X-Real-IP')
        if real_ip and self._is_trusted_proxy(request.remote_addr, trusted_proxies):
            return real_ip
        
        # Fallback to remote addr
        return request.remote_addr or 'unknown'
    
    def _is_trusted_proxy(self, ip: str, trusted_proxies: list) -> bool:
        """Check if IP is a trusted proxy"""
        if not trusted_proxies:
            return False
        # Simple check - could be expanded for CIDR ranges
        return ip in trusted_proxies
    
    def _atomic_increment(self, key: str, window: int) -> Tuple[int, int]:
        """Atomic increment with TTL using Redis"""
        try:
            # Lua script for atomic operation
            lua_script = """
                local current = redis.call('INCR', KEYS[1])
                if current == 1 then
                    redis.call('EXPIRE', KEYS[1], ARGV[1])
                end
                local ttl = redis.call('TTL', KEYS[1])
                return {current, ttl}
            """
            
            result = self.redis_client.eval(lua_script, 1, key, window)
            return int(result[0]), int(result[1])
            
        except redis.RedisError as e:
            current_app.logger.error(f"Redis error in rate limiting: {str(e)}")
            # Fail open - allow request
            return 0, window
        except Exception as e:
            current_app.logger.error(f"Unexpected error in rate limiting: {str(e)}")
            return 0, window
    
    def _rate_limit_exceeded_response(self, limit: int, ttl: int):
        """Return 429 rate limit exceeded response"""
        response = jsonify({
            'error': 'Rate limit exceeded',
            'message': f'Too many requests. Limit: {limit} requests per window.',
            'retry_after': ttl
        })
        response.status_code = 429
        response.headers['Retry-After'] = str(ttl)
        response.headers['X-RateLimit-Limit'] = str(limit)
        response.headers['X-RateLimit-Remaining'] = '0'
        response.headers['X-RateLimit-Reset'] = str(int(time.time()) + ttl)
        return response


# Decorator for manual rate limiting on specific routes
def rate_limit(limit: int, window: int):
    """Decorator to apply custom rate limit to a route"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # Store rate limit config for middleware to use
            g.custom_rate_limit = {'limit': limit, 'window': window}
            return f(*args, **kwargs)
        return decorated
    return decorator