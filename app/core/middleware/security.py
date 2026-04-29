from flask import request, g, jsonify
import re

class SecurityHeadersMiddleware:
    """Add security headers to all responses"""
    
    def __init__(self, app=None):
        self.app = app
        
        # Default CSP policy
        self.csp_policy = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self' https: wss:; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "base-uri 'self'; "
            "upgrade-insecure-requests"
        )
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app
        
        # Allow CSP to be configured from app config
        if 'CSP_POLICY' in app.config:
            self.csp_policy = app.config['CSP_POLICY']
        
        app.wsgi_app = self
    
    def __call__(self, environ, start_response):
        """Add security headers to response"""
        def custom_start_response(status, headers, exc_info=None):
            # Add security headers
            security_headers = self._get_security_headers()
            
            for key, value in security_headers:
                # Don't duplicate existing headers
                if not any(h[0].lower() == key.lower() for h in headers):
                    headers.append((key, value))
            
            return start_response(status, headers, exc_info)
        
        return self.app(environ, custom_start_response)
    
    def _get_security_headers(self):
        """Get all security headers"""
        headers = [
            # Strict Transport Security (HSTS) - enforce HTTPS
            ('Strict-Transport-Security', 'max-age=31536000; includeSubDomains; preload'),
            
            # Content Security Policy (CSP)
            ('Content-Security-Policy', self.csp_policy),
            
            # XSS Protection (legacy, but good to have)
            ('X-XSS-Protection', '1; mode=block'),
            
            # Prevent MIME type sniffing
            ('X-Content-Type-Options', 'nosniff'),
            
            # Clickjacking protection
            ('X-Frame-Options', 'DENY'),
            
            # Referrer policy
            ('Referrer-Policy', 'strict-origin-when-cross-origin'),
            
            # Permissions policy (formerly Feature-Policy)
            ('Permissions-Policy', 'geolocation=(), microphone=(), camera=(), payment=()'),
            
            # Cross-Origin Embedder Policy
            ('Cross-Origin-Embedder-Policy', 'require-corp'),
            
            # Cross-Origin Opener Policy
            ('Cross-Origin-Opener-Policy', 'same-origin'),
            
            # Cross-Origin Resource Policy
            ('Cross-Origin-Resource-Policy', 'same-origin'),
        ]
        
        # Add cache control for sensitive endpoints
        if self._is_sensitive_path():
            headers.append(('Cache-Control', 'no-store, no-cache, must-revalidate, private'))
            headers.append(('Pragma', 'no-cache'))
        
        return headers
    
    def _is_sensitive_path(self) -> bool:
        """Check if current path is sensitive (should not be cached)"""
        sensitive_paths = [
            '/api/auth',
            '/api/users/me',
            '/api/billing',
            '/admin'
        ]
        
        path = request.environ.get('PATH_INFO', '')
        return any(path.startswith(sensitive) for sensitive in sensitive_paths)


class CORSMiddleware:
    """CORS middleware with proper configuration"""
    
    def __init__(self, app=None):
        self.app = app
        self.allowed_origins = []
        self.allowed_methods = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS']
        self.allowed_headers = ['Content-Type', 'Authorization', 'X-Request-ID']
        self.expose_headers = ['X-Request-ID', 'X-RateLimit-Limit', 'X-RateLimit-Remaining']
        self.max_age = 86400  # 24 hours
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app
        self.allowed_origins = app.config.get('CORS_ALLOWED_ORIGINS', ['http://localhost:3000'])
        
        app.wsgi_app = self
    
    def __call__(self, environ, start_response):
        """Handle CORS headers"""
        # Handle preflight requests
        if environ.get('REQUEST_METHOD') == 'OPTIONS':
            return self._handle_preflight(start_response)
        
        def custom_start_response(status, headers, exc_info=None):
            # Add CORS headers
            origin = self._get_origin(environ)
            
            if self._is_origin_allowed(origin):
                cors_headers = [
                    ('Access-Control-Allow-Origin', origin),
                    ('Access-Control-Allow-Credentials', 'true'),
                    ('Access-Control-Expose-Headers', ', '.join(self.expose_headers)),
                    ('Vary', 'Origin')
                ]
                
                for key, value in cors_headers:
                    if not any(h[0].lower() == key.lower() for h in headers):
                        headers.append((key, value))
            
            return start_response(status, headers, exc_info)
        
        return self.app(environ, custom_start_response)
    
    def _handle_preflight(self, start_response):
        """Handle CORS preflight OPTIONS request"""
        headers = [
            ('Access-Control-Allow-Origin', '*'),
            ('Access-Control-Allow-Methods', ', '.join(self.allowed_methods)),
            ('Access-Control-Allow-Headers', ', '.join(self.allowed_headers)),
            ('Access-Control-Allow-Credentials', 'true'),
            ('Access-Control-Max-Age', str(self.max_age)),
            ('Content-Length', '0')
        ]
        
        start_response('204 No Content', headers)
        return [b'']
    
    def _get_origin(self, environ) -> str:
        """Get Origin header from request"""
        return environ.get('HTTP_ORIGIN', '')
    
    def _is_origin_allowed(self, origin: str) -> bool:
        """Check if origin is allowed"""
        if not origin:
            return False
        
        # Allow localhost in development
        if origin in self.allowed_origins:
            return True
        
        # Check wildcard patterns
        for allowed in self.allowed_origins:
            if allowed.endswith('*'):
                if origin.startswith(allowed[:-1]):
                    return True
        
        return False