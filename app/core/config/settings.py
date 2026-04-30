import os
from datetime import timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

class Config:
    """Base configuration with security-focused defaults"""
    # Flask Core
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = False
    TESTING = False
    
    # Domain Configuration 
    BASE_DOMAIN = os.environ.get('BASE_DOMAIN', 'localhost:5000')
    # Remove port for domain-based operations
    DOMAIN = BASE_DOMAIN.split(':')[0] if ':' in BASE_DOMAIN else BASE_DOMAIN
    BASE_URL = os.environ.get('BASE_URL', f'http://{BASE_DOMAIN}')
    API_URL = os.environ.get('API_URL', f'{BASE_URL}/api')
    
    SESSION_COOKIE_NAME = 'isp_session'
    SESSION_COOKIE_PATH = '/'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_DOMAIN = None  # Set per environment
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    
    # JWT Configuration
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')
    JWT_REFRESH_SECRET_KEY = os.environ.get('JWT_REFRESH_SECRET_KEY', os.environ.get('JWT_SECRET_KEY'))
    JWT_ALGORITHM = os.environ.get('JWT_ALGORITHM', 'HS256')
    
    # Token lifetimes
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        seconds=int(os.environ.get('JWT_ACCESS_TOKEN_EXPIRES_SECONDS', 900)) 
    )
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        seconds=int(os.environ.get('JWT_REFRESH_TOKEN_EXPIRES_SECONDS', 604800)) 
    )
    
    # Security claims
    JWT_ISSUER = os.environ.get('JWT_ISSUER', 'isp-saas')
    JWT_AUDIENCE = os.environ.get('JWT_AUDIENCE', 'isp-saas-api')
    
    # Device fingerprinting (optional security enhancement)
    JWT_ENFORCE_DEVICE_FINGERPRINT = os.environ.get('JWT_ENFORCE_DEVICE_FINGERPRINT', 'False').lower() == 'true'
    
    # Database Configuration
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': int(os.environ.get('DATABASE_POOL_SIZE', 20)),
        'max_overflow': int(os.environ.get('DATABASE_MAX_OVERFLOW', 40)),
        'pool_pre_ping': True,
        'pool_recycle': 3600,
        'pool_timeout': 30,
        'pool_use_lifo': True,  # Use LIFO for better performance
        'echo_pool': os.environ.get('SQL_ECHO_POOL', 'False').lower() == 'true'
    }
    
    # Redis Configuration
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    # Connection pool settings
    REDIS_MAX_CONNECTIONS = int(os.environ.get('REDIS_MAX_CONNECTIONS', 50))
    REDIS_SOCKET_TIMEOUT = int(os.environ.get('REDIS_SOCKET_TIMEOUT', 5))
    REDIS_SOCKET_CONNECT_TIMEOUT = int(os.environ.get('REDIS_SOCKET_CONNECT_TIMEOUT', 5))
    REDIS_RETRY_ON_TIMEOUT = os.environ.get('REDIS_RETRY_ON_TIMEOUT', 'True').lower() == 'true'
    REDIS_HEALTH_CHECK_INTERVAL = int(os.environ.get('REDIS_HEALTH_CHECK_INTERVAL', 30))
    
    # Database separation for different concerns
    REDIS_DBS = {
        'cache': int(os.environ.get('REDIS_CACHE_DB', 0)),
        'session': int(os.environ.get('REDIS_SESSION_DB', 1)),
        'rate_limit': int(os.environ.get('REDIS_RATE_LIMIT_DB', 2)),
        'token_blacklist': int(os.environ.get('REDIS_TOKEN_BLACKLIST_DB', 3)),
        'queue': int(os.environ.get('REDIS_QUEUE_DB', 4))
    }
    
    @classmethod
    def get_redis_url_for_db(cls, db_name: str) -> str:
        """Get Redis URL for specific database"""
        db_num = cls.REDIS_DBS.get(db_name, 0)
        # Parse base URL and replace database number
        base_url = cls.REDIS_URL
        if '/db' in base_url:
            return base_url.rsplit('/', 1)[0] + f'/{db_num}'
        return f"{base_url.rstrip('/')}/{db_num}"
    
    # Rate Limiting Configuration
    RATELIMIT_ENABLED = os.environ.get('RATELIMIT_ENABLED', 'True').lower() == 'true'
    RATELIMIT_DEFAULT = os.environ.get('RATELIMIT_DEFAULT', '100/hour')
    RATELIMIT_STORAGE_URL = os.environ.get('RATELIMIT_STORAGE_URL')
    
    # Per-endpoint rate limits
    RATELIMITS = {
        'auth_login': {'limit': 5, 'window': 300},      # 5 per 5 minutes
        'auth_register': {'limit': 3, 'window': 3600},   # 3 per hour
        'auth_forgot_password': {'limit': 3, 'window': 1800},  # 3 per 30 min
        'api_search': {'limit': 50, 'window': 60},       # 50 per minute
        'api_webhook': {'limit': 500, 'window': 60},     # 500 per minute
    }
    
    # CORS Configuration
    @classmethod
    def get_cors_origins(cls) -> List[str]:
        """Get CORS allowed origins (environment-aware)"""
        origins = os.environ.get('CORS_ORIGINS', '')
        if origins:
            return [origin.strip() for origin in origins.split(',')]
        
        # Default per environment
        if cls.DEBUG:
            return ['http://localhost:3000', 'http://localhost:5000']
        return []  # In production, must be explicitly set
    
    CORS_ALLOW_CREDENTIALS = True
    CORS_MAX_AGE = 86400  # 24 hours
    CORS_ALLOW_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS']
    CORS_ALLOW_HEADERS = [
        'Content-Type',
        'Authorization',
        'X-Request-ID',
        'X-Correlation-ID',
        'X-Tenant-ID'  
    ]
    CORS_EXPOSE_HEADERS = [
        'X-Request-ID',
        'X-RateLimit-Limit',
        'X-RateLimit-Remaining',
        'X-RateLimit-Reset'
    ]
    
    # Security Headers Configuration
    SECURITY_HEADERS = {
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
    }
    
    # Content Security Policy (adjust for your CDN needs)
    CSP_POLICY = (
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
    
    # Encryption Configuration
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY')
    ENCRYPTION_PREVIOUS_KEYS = os.environ.get('ENCRYPTION_PREVIOUS_KEYS', '')
    
    @classmethod
    def get_encryption_keys(cls) -> Dict[str, str]:
        """Get current and previous encryption keys for rotation"""
        keys = {}
        if cls.ENCRYPTION_KEY:
            keys['current'] = cls.ENCRYPTION_KEY
        
        if cls.ENCRYPTION_PREVIOUS_KEYS:
            for i, key in enumerate(cls.ENCRYPTION_PREVIOUS_KEYS.split(',')):
                if key.strip():
                    keys[f'previous_{i+1}'] = key.strip()
        
        return keys
    
    # Caching Configuration
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get('CACHE_DEFAULT_TIMEOUT', 300))  # 5 minutes
    CACHE_KEY_PREFIX = os.environ.get('CACHE_KEY_PREFIX', 'isp:')


    # Brevo Email Configuration
    BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
    BREVO_USE_API = os.environ.get('BREVO_USE_API', 'true').lower() == 'true'
    SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp-relay.brevo.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USER = os.environ.get('SMTP_USER', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    FROM_EMAIL = os.environ.get('FROM_EMAIL')
    FROM_NAME = os.environ.get('FROM_NAME', 'Bhatek ISP')
    BASE_URL = os.environ.get('BASE_URL')
    

    
    # Email Configuration
    SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USER = os.environ.get('SMTP_USER', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'True').lower() == 'true'
    SMTP_USE_SSL = os.environ.get('SMTP_USE_SSL', 'False').lower() == 'true'
    FROM_EMAIL = os.environ.get('FROM_EMAIL', 'noreply@isp.com')
    FROM_NAME = os.environ.get('FROM_NAME', 'Bhatek Solution')
    EMAIL_ASYNC_MODE = os.environ.get('EMAIL_ASYNC_MODE', 'true').lower() == 'true'
    
    # Celery Configuration
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', REDIS_URL)
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', REDIS_URL)
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TIMEZONE = os.environ.get('TIMEZONE', 'UTC')
    CELERY_TASK_TRACK_STARTED = True
    CELERY_TASK_TIME_LIMIT = int(os.environ.get('CELERY_TASK_TIME_LIMIT', 300))
    
    # Logging Configuration
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT = os.environ.get('LOG_FORMAT', 'json')  # 'json' or 'text'
    LOG_FILE = os.environ.get('LOG_FILE', 'logs/isp.log')
    LOG_MAX_BYTES = int(os.environ.get('LOG_MAX_BYTES', 10485760))  # 10MB
    LOG_BACKUP_COUNT = int(os.environ.get('LOG_BACKUP_COUNT', 10))
    
    # Sensitive fields to mask in logs
    LOG_SENSITIVE_FIELDS = [
        'password', 'token', 'authorization', 'credit_card', 
        'ssn', 'api_key', 'secret'
    ]
    
    # M-Pesa Configuration
    MPESA_ENVIRONMENT = os.environ.get('MPESA_ENVIRONMENT', 'sandbox')  # 'sandbox' or 'production'
    MPESA_CONSUMER_KEY = os.environ.get('MPESA_CONSUMER_KEY', '')
    MPESA_CONSUMER_SECRET = os.environ.get('MPESA_CONSUMER_SECRET', '')
    MPESA_PASSKEY = os.environ.get('MPESA_PASSKEY', '')
    MPESA_SHORTCODE = os.environ.get('MPESA_SHORTCODE', '')
    MPESA_CALLBACK_BASE_URL = os.environ.get('MPESA_CALLBACK_BASE_URL', f'{BASE_URL}/api/mpesa/callback')
    
    # SMS Configuration
    SMS_PROVIDER = os.environ.get('SMS_PROVIDER', 'africa_talking')  # 'africa_talking', 'twilio', 'messagebird'
    SMS_API_KEY = os.environ.get('SMS_API_KEY', '')
    SMS_API_SECRET = os.environ.get('SMS_API_SECRET', '')
    SMS_SENDER_ID = os.environ.get('SMS_SENDER_ID', 'Bhatek Solution')
    
    # MikroTik Configuration
    MIKROTIK_API_TIMEOUT = int(os.environ.get('MIKROTIK_API_TIMEOUT', 30))
    MIKROTIK_API_RETRIES = int(os.environ.get('MIKROTIK_API_RETRIES', 3))
    MIKROTIK_API_PORT = int(os.environ.get('MIKROTIK_API_PORT', 8728))
    MIKROTIK_API_SSL_PORT = int(os.environ.get('MIKROTIK_API_SSL_PORT', 8729))
    
    # Business Rules
    DEVICE_LIMIT_BEHAVIOR = os.environ.get('DEVICE_LIMIT_BEHAVIOR', 'reject')  # 'reject' or 'warn'
    MAX_CONCURRENT_SESSIONS = int(os.environ.get('MAX_CONCURRENT_SESSIONS', 5))
    MAX_USERS_PER_ORGANIZATION = int(os.environ.get('MAX_USERS_PER_ORGANIZATION', 100))
    
    # Monitoring & Health Checks
    HEALTH_CHECK_PATH = '/health'
    METRICS_PATH = '/metrics'
    HEALTH_CHECK_INTERVAL = int(os.environ.get('HEALTH_CHECK_INTERVAL', 30))


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False
    LOG_LEVEL = 'DEBUG'
    
    # Development-specific settings
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_DOMAIN = None
    
    # CORS for local development
    @classmethod
    def get_cors_origins(cls) -> List[str]:
        return ['http://localhost:3000', 'http://localhost:5000', 'http://127.0.0.1:5000']
    
    # Less strict security for development
    CSP_POLICY = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' *; "
        "style-src 'self' 'unsafe-inline' *; "
        "img-src * data:; "
        "connect-src *; "
    )
    
    # Rate limiting disabled or relaxed in development
    RATELIMIT_ENABLED = os.environ.get('RATELIMIT_ENABLED', 'False').lower() == 'true'
    
    # Development database options
    SQLALCHEMY_ENGINE_OPTIONS = {
        **Config.SQLALCHEMY_ENGINE_OPTIONS,
        'echo': os.environ.get('SQL_ECHO', 'True').lower() == 'true',
        'pool_size': 5,
    }


class ProductionConfig(Config):
    """Production configuration with strict security"""
    DEBUG = False
    TESTING = False
    LOG_LEVEL = 'INFO'
    
    # Production security
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_DOMAIN = f".{Config.DOMAIN}" if Config.DOMAIN != 'localhost' else None
    SESSION_COOKIE_SAMESITE = 'Strict'
    
    # Validate required production settings
    @classmethod
    def validate(cls):
        """Validate required production configuration"""
        required_settings = [
            ('SECRET_KEY', 'must be set and not default'),
            ('JWT_SECRET_KEY', 'must be set'),
            ('ENCRYPTION_KEY', 'must be set'),
            ('DATABASE_URL', 'must be set'),
            ('REDIS_URL', 'must be set'),
        ]
        
        for setting, message in required_settings:
            value = getattr(cls, setting, None)
            if not value:
                raise ValueError(f"{setting} {message}")
            
            # Check for default development values
            if setting == 'SECRET_KEY' and value == 'dev-secret-key-change-in-production':
                raise ValueError(f"{setting} must be changed from default")
        
        # Validate JWT settings
        if cls.JWT_SECRET_KEY == cls.JWT_REFRESH_SECRET_KEY:
            import warnings
            warnings.warn("JWT_SECRET_KEY and JWT_REFRESH_SECRET_KEY should be different for better security")
    
    # Production CORS (must be explicitly configured)
    @classmethod
    def get_cors_origins(cls) -> List[str]:
        origins = os.environ.get('CORS_ORIGINS', '')
        if not origins:
            raise ValueError("CORS_ORIGINS must be set in production")
        return [origin.strip() for origin in origins.split(',')]
    
    # Stronger security headers for production
    SECURITY_HEADERS = {
        **Config.SECURITY_HEADERS,
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
    }
    
    # Production database pool
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': int(os.environ.get('DATABASE_POOL_SIZE', 50)),
        'max_overflow': int(os.environ.get('DATABASE_MAX_OVERFLOW', 100)),
        'pool_pre_ping': True,
        'pool_recycle': 3600,
        'pool_timeout': 60,
        'pool_use_lifo': True,
    }


class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DEBUG = True
    LOG_LEVEL = 'WARNING'
    
    # Disable security for testing
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_DOMAIN = None
    
    # Test database
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'TEST_DATABASE_URL',
        'postgresql://postgres:postgres@localhost:5432/isp_test'
    )
    
    # Test Redis
    REDIS_URL = os.environ.get('TEST_REDIS_URL', 'redis://localhost:6379/15')
    
    # Disable rate limiting for tests
    RATELIMIT_ENABLED = False
    
    # Use weaker JWT for testing (shorter expiry)
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=5)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(hours=1)
    
    # Test CORS
    @classmethod
    def get_cors_origins(cls) -> List[str]:
        return ['*']
    
    # Don't encrypt in tests (or use test key)
    ENCRYPTION_KEY = os.environ.get('TEST_ENCRYPTION_KEY', 'test-key-for-testing-only-32bytes!')
    
    # Smaller connection pools for tests
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 5,
        'max_overflow': 10,
        'pool_pre_ping': True,
    }


class StagingConfig(ProductionConfig):
    """Staging configuration (production-like but with some relaxations)"""
    DEBUG = True  # Allow debugging in staging
    LOG_LEVEL = 'DEBUG'
    
    # Staging CORS (allow more origins)
    @classmethod
    def get_cors_origins(cls) -> List[str]:
        origins = os.environ.get('CORS_ORIGINS', '')
        if not origins:
            # Default staging origins
            return ['https://staging.isp.com', 'https://app.staging.isp.com']
        return [origin.strip() for origin in origins.split(',')]
    
    # Staging doesn't require full validation
    @classmethod
    def validate(cls):
        """Skip validation for staging"""
        pass


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'staging': StagingConfig,
    'default': DevelopmentConfig
}


# Helper function to get config by name
def get_config(config_name: str = None):
    """Get configuration class by name"""
    if not config_name:
        config_name = os.environ.get('FLASK_ENV', 'development')
    
    config_class = config.get(config_name, config['default'])
    
    # Validate production config
    if config_name == 'production':
        config_class.validate()
    
    return config_class