from flask import Flask, g, request
from flask_cors import CORS
from flask_migrate import Migrate
import time
from app.core.security.jwt import JWTService
from app.core.config.settings import config
from app.core.database.session import db
from app.core.database.redis_client import redis_client
from app.core.logging.logger import setup_logging, logger
from app.core.exceptions.handlers import register_error_handlers

# Import all models from the centralized models folder
from app.models import *


def create_app(config_name=None):
    """Application factory"""
    app = Flask(__name__)

    # Load configuration
    if config_name is None:
        config_name = 'development'

    app.config.from_object(config[config_name])

    # Initialize extensions
    db.init_app(app)
    Migrate(app, db)
    redis_client.init_app(app)
    
    # Configure CORS properly
    cors_origins = app.config.get('CORS_ORIGINS', ['http://localhost:3000'])
    if cors_origins == '*':
        CORS(app, origins='*')
    else:
        CORS(app, origins=cors_origins)

    # Setup logging
    setup_logging(app)

    # Register error handlers first
    register_error_handlers(app)

    # Middleware registration order (LIFO - Last In First Out)
    # These are applied from bottom to top, so order them from outermost to innermost
    # Starting from app.wsgi_app (innermost) and wrapping outward
    
    # 1. Rate Limiter (using before_request, not WSGI middleware)
    from app.core.middleware.rate_limit import RateLimiter
    RateLimiter(app)
    
    # 2. Request ID (outermost - should be first to capture all requests)
    from app.core.middleware.request_id import RequestIDMiddleware
    
    # 3. Tenant middleware (for backward compatibility, but will be deprecated)
    from app.core.middleware.tenant import TenantMiddleware
    
    # 4. Auth middleware (innermost - runs closest to the app)
    from app.core.middleware.auth import AuthMiddleware
    
    # Get JWT secret from config
    jwt_secret_key = app.config.get('JWT_SECRET_KEY')
    if not jwt_secret_key:
        logger.warning("JWT_SECRET_KEY not set. Authentication will fail for API endpoints!")
    
    # Define exempt paths for web interface (no JWT required)
    exempt_paths = [
        # Health checks
        '/health',
        '/api/v1/health',
        
        # Web routes (no JWT required - uses session instead)
        '/',
        '/login',
        '/logout',
        '/register',
        '/register-success',
        '/verify-email',
        '/dashboard',
        '/super-admin',
        '/organization',
        
        # Hotspot routes (public)
        '/hotspot',
        
        # Static files
        '/static',
        '/favicon.ico',
        
        # API auth endpoints (no JWT required for login/register)
        '/api/v1/auth/login',
        '/api/v1/auth/register',
        '/api/v1/auth/refresh',
        '/api/v1/auth/forgot-password',
        '/api/v1/auth/reset-password',
    ]
    
    # Wrap in correct order (last wrapped = first executed)
    # Execution order: Auth -> Tenant -> RequestID -> app
    wsgi_app = app.wsgi_app
    
    # Start with innermost (Auth) - will only protect API routes
    # We need to pass the secret key and exempt paths
    # But first, let's create a wrapper that only applies auth to API paths
    
    # Apply middlewares
    wsgi_app = RequestIDMiddleware(wsgi_app)
    wsgi_app = TenantMiddleware(wsgi_app)
    
    # Apply Auth middleware with config
    wsgi_app = AuthMiddleware(
        wsgi_app, 
        secret_key=jwt_secret_key, 
        exempt_paths=exempt_paths
    )
    
    app.wsgi_app = wsgi_app

    # Request/Response hooks
    @app.before_request
    def before_request():
        g.start_time = time.time()
        
        # Get request ID from middleware
        if hasattr(request, 'environ'):
            request_id = request.environ.get('REQUEST_ID')
            if request_id:
                g.request_id = request_id
        
        # Get user context from auth middleware (only for API requests)
        user_id = request.environ.get('USER_ID')
        if user_id:
            g.user_id = user_id
        
        org_id = request.environ.get('ORGANIZATION_ID')
        if org_id:
            g.organization_id = org_id
        
        # Don't log health checks too loudly
        if not request.path.startswith('/health'):
            logger.debug(f"Request started: {request.method} {request.path}")

    @app.after_request
    def after_request(response):
        if hasattr(g, 'start_time'):
            duration = time.time() - g.start_time
            response.headers['X-Response-Time'] = str(int(duration * 1000))
            if not request.path.startswith('/health'):
                logger.debug(f"Request completed in {duration:.3f}s")

        if hasattr(g, 'request_id'):
            response.headers['X-Request-ID'] = g.request_id

        return response

    # Register blueprints
    from app.modules.auth.routes import auth_bp
    from app.modules.organization.routes import org_bp
    from app.modules.network.routes import network_bp
    from app.modules.router.routes import router_bp
    from app.modules.access_point.routes import ap_bp
    from app.modules.subscriber.routes import subscriber_bp
    from app.modules.billing.routes import billing_bp
    from app.modules.payment.routes import payment_bp
    from app.modules.session.routes import session_bp
    from app.modules.web import web_bp
    
    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp, url_prefix='/api/v1/auth')
    app.register_blueprint(org_bp, url_prefix='/api/v1/organizations')
    app.register_blueprint(network_bp, url_prefix='/api/v1/networks')
    app.register_blueprint(router_bp, url_prefix='/api/v1/routers')
    app.register_blueprint(ap_bp, url_prefix='/api/v1/access-points')
    app.register_blueprint(subscriber_bp, url_prefix='/api/v1/subscribers')
    app.register_blueprint(billing_bp, url_prefix='/api/v1/billing')
    app.register_blueprint(payment_bp, url_prefix='/api/v1/payments')
    app.register_blueprint(session_bp, url_prefix='/api/v1/sessions')

    # Health check
    @app.route('/health')
    @app.route('/api/v1/health')
    def health():
        return {
            'status': 'healthy',
            'service': 'isp-management-platform',
            'version': '1.0.0',
            'timestamp': time.time()
        }

    return app