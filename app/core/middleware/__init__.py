
from flask import Flask
from app.core.middleware.auth import AuthMiddleware
from app.core.middleware.rate_limit import RateLimiter
from app.core.middleware.request_id import RequestIDMiddleware
from app.core.middleware.security import SecurityHeadersMiddleware, CORSMiddleware


def register_middlewares(app: Flask):
    
    # 1. Request ID (MUST BE FIRST)
    app.wsgi_app = RequestIDMiddleware(app)
    
    # 2. Security Headers
    app.wsgi_app = SecurityHeadersMiddleware(app)
    
    # 3. CORS
    app.wsgi_app = CORSMiddleware(app)
    
    # 4. Authentication
    app.wsgi_app = AuthMiddleware(app)
    
    # 5. Rate Limiter (uses @app.before_request, not WSGI)
    RateLimiter(app)
    
    return app


# Helper function to check execution order
def get_middleware_stack(app: Flask) -> list:
    """Debug function to see middleware execution order"""
    stack = []
    current = app.wsgi_app
    while hasattr(current, '__class__'):
        stack.append(current.__class__.__name__)
        if hasattr(current, 'app'):
            current = current.app
        else:
            break
    return stack