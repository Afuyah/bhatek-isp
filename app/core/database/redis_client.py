from flask_redis import FlaskRedis
import json
from typing import Optional, Any

redis_client = FlaskRedis()

class RedisClient:
    """Redis client wrapper"""
    
    @staticmethod
    def get_json(key: str) -> Optional[Any]:
        """Get and parse JSON from Redis"""
        data = redis_client.get(key)
        return json.loads(data) if data else None
    
    @staticmethod
    def set_json(key: str, value: Any, ex: int = None) -> bool:
        """Set JSON value in Redis"""
        return redis_client.setex(key, ex, json.dumps(value)) if ex else redis_client.set(key, json.dumps(value))
    
    @staticmethod
    def delete_pattern(pattern: str) -> int:
        """Delete keys matching pattern"""
        keys = redis_client.keys(pattern)
        if keys:
            return redis_client.delete(*keys)
        return 0
    
    @staticmethod
    def increment(key: str, amount: int = 1) -> int:
        """Increment counter"""
        return redis_client.incr(key, amount)
    
    @staticmethod
    def expire(key: str, seconds: int) -> bool:
        """Set expiration on key"""
        return redis_client.expire(key, seconds)