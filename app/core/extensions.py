
from flask import Flask
from typing import Optional

from app.core.security.jwt import JWTService
from app.core.database.redis_client import redis_client
from app.core.logging.logger import logger


class ExtensionManager:
    """Manages all Flask extensions and services"""
    
    def __init__(self):
        self._jwt_service: Optional[JWTService] = None
        self._initialized = False
    
    def init_app(self, app: Flask):
        """Initialize all extensions with the Flask app"""
        
        if self._initialized:
            logger.warning("Extensions already initialized, skipping...")
            return
        
        logger.info("Initializing application extensions...")
        
        
        # 1. Register Redis client in app.extensions for JWTService to find
        if redis_client and hasattr(redis_client, 'client') and redis_client.client:
            app.extensions['redis'] = redis_client.client
            logger.info("Redis client registered in app.extensions['redis']")
        else:
            logger.warning("Redis client not available or not connected. JWT blacklist will use in-memory fallback.")
            app.extensions['redis'] = None
        
        # 2. Initialize JWT Service
        self._jwt_service = JWTService(app)
        app.extensions['jwt_service'] = self._jwt_service
        logger.info("JWT Service initialized and registered in app.extensions['jwt_service']")
        
        # 3. Future extensions can be added here
        # Example: 
        # - Celery
        # - Caching service
        # - Rate limiter service
        
        self._initialized = True
        logger.info("All extensions initialized successfully")
    
    @property
    def jwt_service(self) -> Optional[JWTService]:
        """Get JWT service instance"""
        return self._jwt_service


# Create singleton instance
extensions = ExtensionManager()