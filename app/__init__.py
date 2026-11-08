"""Application package — factory and process-level wiring only.

Everything with real behaviour now lives in a focused module:

* configuration        ``app.core.config.settings``
* middleware pipeline  ``app.core.middleware``
* blueprints           ``app.core.blueprints``
* template filters     ``app.core.template_filters``
* compression/caching  ``app.core.performance``
* errors, logging, security, database — their own ``app.core.*`` packages

``create_app()`` should read as a list of steps, not an implementation.
"""

import os
import time

from celery import Celery
from celery.schedules import crontab
from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate

from app.core.blueprints import register_blueprints
from app.core.config.settings import config
from app.core.database.redis_client import redis_client
from app.core.database.session import db
from app.core.exceptions.handlers import register_error_handlers
from app.core.logging.logger import logger, setup_logging
from app.core.middleware import install_middleware_stack
from app.core.performance import register_performance
from app.core.template_filters import register_template_filters

# Importing the models registers their metadata before Alembic or create_all runs.
from app.models import *  # noqa: F401,F403


def _make_celery(app: Flask) -> Celery:
    """Create a Celery instance bound to the Flask app's configuration."""
    celery = Celery(app.import_name)

    celery.conf.broker_url = app.config.get('CELERY_BROKER_URL')
    celery.conf.result_backend = app.config.get('CELERY_RESULT_BACKEND')
    celery.conf.task_serializer = app.config.get('CELERY_TASK_SERIALIZER', 'json')
    celery.conf.result_serializer = app.config.get('CELERY_RESULT_SERIALIZER', 'json')
    celery.conf.accept_content = app.config.get('CELERY_ACCEPT_CONTENT', ['json'])
    celery.conf.timezone = app.config.get('CELERY_TIMEZONE', 'UTC')
    celery.conf.task_track_started = app.config.get('CELERY_TASK_TRACK_STARTED', True)
    celery.conf.task_time_limit = app.config.get('CELERY_TASK_TIME_LIMIT', 300)

    # Beat schedule: settings holds plain dicts, Celery wants crontab objects.
    beat_schedule = {}
    for name, entry in app.config.get('CELERY_BEAT_SCHEDULE', {}).items():
        schedule = entry.get('schedule')
        if isinstance(schedule, dict) and schedule.get('type') == 'crontab':
            beat_schedule[name] = {
                'task': entry['task'],
                'schedule': crontab(
                    hour=schedule.get('hour', '*'),
                    minute=schedule.get('minute', '*'),
                    day_of_week=schedule.get('day_of_week', '*'),
                    day_of_month=schedule.get('day_of_month', '*'),
                    month_of_year=schedule.get('month_of_year', '*'),
                ),
                'options': entry.get('options', {}),
            }
        else:
            beat_schedule[name] = {
                'task': entry['task'],
                'schedule': schedule,
                'options': entry.get('options', {}),
            }
    celery.conf.beat_schedule = beat_schedule
    celery.conf.imports = ('app.tasks',)

    app.extensions['celery'] = celery
    logger.info("Celery initialized with broker: %s", celery.conf.broker_url)
    return celery


#: Module-level instance so ``celery -A app.celery_app`` works. It is fully
#: configured by :func:`create_app`.
celery_app = Celery(__name__)


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    Migrate(app, db)
    redis_client.init_app(app)

    # Services that other modules look up through app.extensions.
    # Nothing registered these before, so token_required() raised
    # "JWT service not initialized" on every authenticated API request.
    from app.core.security.jwt import JWTService

    app.extensions.setdefault('redis', redis_client)
    app.extensions['jwt_service'] = JWTService(app)


def _init_celery(app: Flask) -> None:
    celery = _make_celery(app)
    celery_app.config_from_object(celery.conf)
    celery_app.conf.update(celery.conf)


def _init_cors(app: Flask) -> None:
    origins = app.config.get('CORS_ORIGINS', ['http://localhost:3000'])
    CORS(app, origins='*' if origins == '*' else origins)


def _register_request_hooks(app: Flask) -> None:
    @app.before_request
    def start_timer():
        from flask import g, request

        g.start_time = time.time()

        environ = request.environ
        if environ.get('REQUEST_ID'):
            g.request_id = environ['REQUEST_ID']
        if environ.get('USER_ID'):
            g.user_id = environ['USER_ID']
        if environ.get('ORGANIZATION_ID'):
            g.organization_id = environ['ORGANIZATION_ID']

        if not request.path.startswith('/health'):
            logger.debug("Request: %s %s", request.method, request.path)

    @app.after_request
    def add_timing_headers(response):
        from flask import g, request

        if hasattr(g, 'start_time'):
            duration = time.time() - g.start_time
            response.headers['X-Response-Time'] = str(int(duration * 1000))
            if not request.path.startswith('/health'):
                logger.debug("Response: %.3fs", duration)

        if hasattr(g, 'request_id'):
            response.headers['X-Request-ID'] = g.request_id

        return response


def _register_health_check(app: Flask) -> None:
    @app.route('/health')
    @app.route('/api/v1/health')
    def health():
        return {
            'status': 'healthy',
            'service': 'isp-management-platform',
            'version': '1.0.0',
            'timestamp': time.time(),
        }


def create_app(config_name=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)

    config_name = config_name or os.environ.get('FLASK_ENV', 'development')
    if config_name not in config:
        logger.warning("Unknown config %r, falling back to development", config_name)
        config_name = 'development'
    app.config.from_object(config[config_name])

    _init_extensions(app)
    _init_celery(app)
    _init_cors(app)

    setup_logging(app)
    register_error_handlers(app)
    register_template_filters(app)

    install_middleware_stack(app)
    _register_request_hooks(app)

    register_blueprints(app)
    _register_health_check(app)
    register_performance(app)

    jwt_secret = app.config.get('JWT_SECRET_KEY')
    if not jwt_secret:
        logger.warning("JWT_SECRET_KEY not set. Authentication will fail!")
    elif len(jwt_secret) < 32:
        # HS256 keys shorter than the hash output are below RFC 7518 guidance.
        logger.warning(
            "JWT_SECRET_KEY is only %d bytes; use at least 32 for HS256.", len(jwt_secret)
        )

    return app
