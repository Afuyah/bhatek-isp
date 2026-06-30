from flask import Flask, g, request
from flask_cors import CORS
from flask_migrate import Migrate
import time
from datetime import datetime
import json
from markupsafe import Markup

from celery import Celery
from celery.schedules import crontab

from app.core.security.jwt import JWTService
from app.core.config.settings import config
from app.core.database.session import db
from app.core.database.redis_client import redis_client
from app.core.logging.logger import setup_logging, logger
from app.core.exceptions.handlers import register_error_handlers

# Import all models
from app.models import *


def _make_celery(app: Flask) -> Celery:
    """
    Create and configure a Celery instance bound to the Flask app.

    The instance is stored at ``app.extensions['celery']`` so it can be
    retrieved from anywhere that has access to the current app.
    """
    celery = Celery(app.import_name)

    # Pull broker / backend from Flask config (set from env in settings.py)
    celery.conf.broker_url = app.config.get('CELERY_BROKER_URL')
    celery.conf.result_backend = app.config.get('CELERY_RESULT_BACKEND')
    celery.conf.task_serializer = app.config.get('CELERY_TASK_SERIALIZER', 'json')
    celery.conf.result_serializer = app.config.get('CELERY_RESULT_SERIALIZER', 'json')
    celery.conf.accept_content = app.config.get('CELERY_ACCEPT_CONTENT', ['json'])
    celery.conf.timezone = app.config.get('CELERY_TIMEZONE', 'UTC')
    celery.conf.task_track_started = app.config.get('CELERY_TASK_TRACK_STARTED', True)
    celery.conf.task_time_limit = app.config.get('CELERY_TASK_TIME_LIMIT', 300)

    # -----------------------------------------------------------------------
    # Beat schedule — convert the settings dict to proper Celery schedule
    # objects (crontab / timedelta).
    # -----------------------------------------------------------------------
    raw_schedule = app.config.get('CELERY_BEAT_SCHEDULE', {})
    beat_schedule = {}

    for name, entry in raw_schedule.items():
        sched = entry.get('schedule')
        if isinstance(sched, dict) and sched.get('type') == 'crontab':
            # Build a crontab object from the dict
            beat_schedule[name] = {
                'task': entry['task'],
                'schedule': crontab(
                    hour=sched.get('hour', '*'),
                    minute=sched.get('minute', '*'),
                    day_of_week=sched.get('day_of_week', '*'),
                    day_of_month=sched.get('day_of_month', '*'),
                    month_of_year=sched.get('month_of_year', '*'),
                ),
                'options': entry.get('options', {}),
            }
        else:
            # Numeric schedule (seconds interval)
            beat_schedule[name] = {
                'task': entry['task'],
                'schedule': sched,
                'options': entry.get('options', {}),
            }

    celery.conf.beat_schedule = beat_schedule

    # Register the task module so shared_task decorators are discovered
    celery.conf.imports = ('app.tasks',)

    # Store on app so it can be retrieved via current_app.extensions['celery']
    app.extensions['celery'] = celery

    logger.info("Celery initialized with broker: %s", celery.conf.broker_url)
    return celery


# Module-level Celery instance — used by ``celery -A app.celery_app worker``
# It is fully configured only after create_app() is called.
celery_app = Celery(__name__)


def create_app(config_name=None):
    """Application factory — creates and configures the Flask app."""
    app = Flask(__name__)

    # -------------------------------------------------------------------------
    # CONFIGURATION
    # -------------------------------------------------------------------------
    if config_name is None:
        config_name = 'development'

    app.config.from_object(config[config_name])

    # -------------------------------------------------------------------------
    # EXTENSIONS
    # -------------------------------------------------------------------------
    db.init_app(app)
    Migrate(app, db)
    redis_client.init_app(app)

    # -------------------------------------------------------------------------
    # CELERY
    # -------------------------------------------------------------------------
    celery = _make_celery(app)
    # Update the module-level instance so ``celery -A app.celery_app`` works
    celery_app.config_from_object(celery.conf)
    celery_app.conf.update(celery.conf)

    # CORS
    cors_origins = app.config.get('CORS_ORIGINS', ['http://localhost:3000'])
    if cors_origins == '*':
        CORS(app, origins='*')
    else:
        CORS(app, origins=cors_origins)

    # Logging
    setup_logging(app)

    # Error handlers
    register_error_handlers(app)

    # -------------------------------------------------------------------------
    # TEMPLATE FILTERS
    # -------------------------------------------------------------------------

    @app.template_filter('datetimeformat')
    def datetimeformat(value, format='%b %d, %Y'):
        if value is None:
            return 'N/A'
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                return value[:19] if len(value) >= 19 else value
        if hasattr(value, 'strftime'):
            return value.strftime(format)
        return str(value)[:19] if len(str(value)) >= 19 else str(value)

    @app.template_filter('format_date')
    def format_date(value, format='%Y-%m-%d'):
        if value is None:
            return 'N/A'
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                return value[:10] if len(value) >= 10 else value
        if hasattr(value, 'strftime'):
            return value.strftime(format)
        return str(value)[:10]

    @app.template_filter('format_datetime')
    def format_datetime(value, format='%Y-%m-%d %H:%M'):
        if value is None:
            return 'N/A'
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                return value[:16] if len(value) >= 16 else value
        if hasattr(value, 'strftime'):
            return value.strftime(format)
        return str(value)[:16]

    @app.template_filter('escapejs')
    def escapejs_filter(value):
        if not value:
            return ''
        return Markup(json.dumps(str(value))[1:-1])

    # -------------------------------------------------------------------------
    # MIDDLEWARE (LIFO — last wrapped = first executed)
    # Execution order: Auth → Tenant → RequestID → Flask App
    # -------------------------------------------------------------------------

    # Rate Limiter (runs via before_request)
    from app.core.middleware.rate_limit import RateLimiter
    RateLimiter(app)

    # Request ID
    from app.core.middleware.request_id import RequestIDMiddleware

    # Tenant middleware
    from app.core.middleware.tenant import TenantMiddleware

    # Auth middleware
    from app.core.middleware.auth import AuthMiddleware

    jwt_secret_key = app.config.get('JWT_SECRET_KEY')
    if not jwt_secret_key:
        logger.warning(
            "JWT_SECRET_KEY not set. Authentication will fail!"
        )

    # Exempt paths — public routes (no JWT required)
    exempt_paths = [
        # Health checks
        '/health',
        '/api/v1/health',

        # Web routes (public pages)
        '/',
        '/login',
        '/logout',
        '/register',
        '/register-success',
        '/verify-email',
        '/dashboard',
        '/super-admin',

        # Public hotspot captive portal
        '/hotspot',
        '/hotspot/',

        # Static files
        '/static',
        '/favicon.ico',

        # API auth endpoints
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

        # Router connection test (onboarding wizard)
        '/api/v1/routers/test',

        # =====================================================================
        # RADIUS ENDPOINTS — called by FreeRADIUS (no JWT)
        # =====================================================================
        '/api/radius/authenticate',
        '/api/radius/disconnect',
        '/api/radius/accounting',
        '/api/radius/accounting/start',
        '/api/radius/accounting/stop',
        '/api/radius/accounting/interim',

        # Router RADIUS operations (AJAX from web UI — wildcard handled by middleware)
        '/api/v1/routers/*/radius/retry',
        '/api/v1/routers/*/radius/secret/generate',
        '/api/v1/routers/*/radius/regenerate',
    ]

    # Apply middleware stack
    wsgi_app = app.wsgi_app
    wsgi_app = RequestIDMiddleware(wsgi_app)
    wsgi_app = TenantMiddleware(wsgi_app)
    wsgi_app = AuthMiddleware(
        wsgi_app,
        secret_key=jwt_secret_key,
        exempt_paths=exempt_paths,
    )
    app.wsgi_app = wsgi_app

    # -------------------------------------------------------------------------
    # REQUEST / RESPONSE HOOKS
    # -------------------------------------------------------------------------

    @app.before_request
    def before_request():
        g.start_time = time.time()

        if hasattr(request, 'environ'):
            request_id = request.environ.get('REQUEST_ID')
            if request_id:
                g.request_id = request_id

        user_id = request.environ.get('USER_ID')
        if user_id:
            g.user_id = user_id

        org_id = request.environ.get('ORGANIZATION_ID')
        if org_id:
            g.organization_id = org_id

        if not request.path.startswith('/health'):
            logger.debug(f"Request: {request.method} {request.path}")

    @app.after_request
    def after_request(response):
        if hasattr(g, 'start_time'):
            duration = time.time() - g.start_time
            response.headers['X-Response-Time'] = str(int(duration * 1000))
            if not request.path.startswith('/health'):
                logger.debug(f"Response: {duration:.3f}s")

        if hasattr(g, 'request_id'):
            response.headers['X-Request-ID'] = g.request_id

        return response

    # -------------------------------------------------------------------------
    # API BLUEPRINTS
    # -------------------------------------------------------------------------

    from app.modules.auth.routes import auth_bp
    from app.modules.organization.routes import org_bp
    from app.modules.network.routes import network_bp
    from app.modules.router.routes import router_bp
    from app.modules.access_point.routes import ap_bp
    from app.modules.subscriber.routes import subscriber_bp
    from app.modules.billing.routes import billing_bp
    from app.modules.payment.routes import payment_bp
    from app.modules.session.routes import session_bp
    from app.modules.hotspot.routes import hotspot_bp
    
    app.register_blueprint(hotspot_bp)
    app.register_blueprint(auth_bp, url_prefix='/api/v1/auth')
    app.register_blueprint(org_bp, url_prefix='/api/v1/organizations')
    app.register_blueprint(network_bp, url_prefix='/api/v1/networks')
    app.register_blueprint(router_bp, url_prefix='/api/v1/routers')
    app.register_blueprint(ap_bp, url_prefix='/api/v1/access-points')
    app.register_blueprint(subscriber_bp, url_prefix='/api/v1/subscribers')
    app.register_blueprint(billing_bp, url_prefix='/api/v1/billing')
    app.register_blueprint(payment_bp, url_prefix='/api/v1/payments')
    app.register_blueprint(session_bp, url_prefix='/api/v1/sessions')

    # -------------------------------------------------------------------------
    # WEB BLUEPRINTS
    # -------------------------------------------------------------------------

    from app.modules.web import web_bp
    from app.modules.network.web_routes import network_web_bp
    from app.modules.router.web_routes import router_web_bp
    from app.modules.access_point.web_routes import ap_web_bp
    from app.modules.billing.web_routes import billing_web_bp
    from app.modules.subscriber.web_routes import subscriber_web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(network_web_bp)
    app.register_blueprint(router_web_bp)
    app.register_blueprint(ap_web_bp)
    app.register_blueprint(billing_web_bp)
    app.register_blueprint(subscriber_web_bp)

    # -------------------------------------------------------------------------
    # RADIUS BLUEPRINTS — called by FreeRADIUS
    # -------------------------------------------------------------------------

    from app.integrations.radius.radius_auth_handler import radius_auth_bp
    from app.integrations.radius.radius_accounting_routes import radius_accounting_bp

    app.register_blueprint(radius_auth_bp)          # /api/radius/authenticate, /api/radius/disconnect
    app.register_blueprint(radius_accounting_bp)    # /api/radius/accounting, /api/radius/accounting/start, /stop, /interim

    # -------------------------------------------------------------------------
    # HEALTH CHECK
    # -------------------------------------------------------------------------

    @app.route('/health')
    @app.route('/api/v1/health')
    def health():
        return {
            'status': 'healthy',
            'service': 'isp-management-platform',
            'version': '1.0.0',
            'timestamp': time.time(),
        }

    return app