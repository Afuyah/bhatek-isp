from flask_redis import FlaskRedis
import json
from typing import Optional, Any
import redis

# Initialize FlaskRedis
redis_client = FlaskRedis()


class RedisClient:
    """Redis client wrapper for application-specific operations"""
    
    @staticmethod
    def get_raw_client():
        """
        Get the raw Redis client for advanced operations (JWTService, etc.)
        FlaskRedis stores the raw connection as `_client` or `connection`
        """
        # FlaskRedis uses different internal attribute names depending on version
        if hasattr(redis_client, '_client'):
            return redis_client._client
        elif hasattr(redis_client, 'connection'):
            return redis_client.connection
        elif hasattr(redis_client, 'client'):
            return redis_client.client
        else:
            # Fallback: try to get from redis_client itself if it's already a client
            return redis_client if hasattr(redis_client, 'ping') else None
    
    @staticmethod
    def _decode_value(value):
        """Decode bytes to string if needed"""
        if isinstance(value, bytes):
            return value.decode('utf-8')
        return value
    
    @staticmethod
    def get(key: str) -> Optional[str]:
        """Get string value (auto-decodes bytes)"""
        try:
            data = redis_client.get(key)
            return RedisClient._decode_value(data) if data else None
        except Exception as e:
            print(f"Redis get error: {e}")
            return None
    
    @staticmethod
    def get_json(key: str) -> Optional[Any]:
        """Get and parse JSON from Redis"""
        try:
            data = redis_client.get(key)
            if data:
                # Decode bytes to string if needed
                if isinstance(data, bytes):
                    data = data.decode('utf-8')
                return json.loads(data)
            return None
        except Exception as e:
            print(f"Redis get_json error: {e}")
            return None
    
    @staticmethod
    def set(key: str, value: str, ex: int = None) -> bool:
        """Set string value with optional expiration"""
        try:
            if ex:
                return redis_client.setex(key, ex, value)
            return redis_client.set(key, value)
        except Exception as e:
            print(f"Redis set error: {e}")
            return False
    
    @staticmethod
    def set_json(key: str, value: Any, ex: int = None) -> bool:
        """Set JSON value in Redis"""
        try:
            json_str = json.dumps(value)
            if ex:
                return redis_client.setex(key, ex, json_str)
            return redis_client.set(key, json_str)
        except Exception as e:
            print(f"Redis set_json error: {e}")
            return False
    
    @staticmethod
    def exists(key: str) -> bool:
        """Check if key exists in Redis"""
        try:
            return bool(redis_client.exists(key))
        except Exception as e:
            print(f"Redis exists error: {e}")
            return False
    
    @staticmethod
    def delete(key: str) -> bool:
        """Delete a specific key"""
        try:
            return bool(redis_client.delete(key))
        except Exception as e:
            print(f"Redis delete error: {e}")
            return False
    
    @staticmethod
    def delete_pattern(pattern: str) -> int:
        """Delete keys matching pattern"""
        try:
            keys = redis_client.keys(pattern)
            if keys:
                return redis_client.delete(*keys)
            return 0
        except Exception as e:
            print(f"Redis delete_pattern error: {e}")
            return 0
    
    @staticmethod
    def increment(key: str, amount: int = 1) -> int:
        """Increment counter"""
        try:
            return redis_client.incr(key, amount)
        except Exception as e:
            print(f"Redis increment error: {e}")
            return 0
    
    @staticmethod
    def expire(key: str, seconds: int) -> bool:
        """Set expiration on key"""
        try:
            return redis_client.expire(key, seconds)
        except Exception as e:
            print(f"Redis expire error: {e}")
            return False
    
    @staticmethod
    def ping() -> bool:
        """Test Redis connection"""
        try:
            return redis_client.ping()
        except Exception as e:
            print(f"Redis ping error: {e}")
            return False


# Helper function to get raw Redis client for extensions
def get_redis_connection():
    """Get raw Redis connection for JWTService and other extensions"""
    raw_client = RedisClient.get_raw_client()
    
    # Test if it's a valid redis client
    if raw_client and hasattr(raw_client, 'ping'):
        try:
            raw_client.ping()
            return raw_client
        except Exception:
            return None
    
    return None


# Optional: Monkey-patch FlaskRedis to expose ping, exists, etc. if missing
if not hasattr(redis_client, 'exists'):
    redis_client.exists = RedisClient.exists

if not hasattr(redis_client, 'ping'):
    redis_client.ping = RedisClient.ping

if not hasattr(redis_client, 'setex'):
    # FlaskRedis already has setex in most versions
    pass