from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import json
import hashlib

from app.core.database.redis_client import redis_client
from app.core.logging.logger import logger

class RadiusCache:

    # Key prefixes
    PREFIX_AUTH = 'radius:auth:'
    PREFIX_SESSION = 'radius:session:'
    PREFIX_ACCOUNTING = 'radius:accounting:'
    PREFIX_USER_SESSIONS = 'radius:user:'
    PREFIX_NAS = 'radius:nas:'
    PREFIX_DEVICE = 'radius:device:'
    PREFIX_RATE_LIMIT = 'radius:rate_limit:'
    PREFIX_SUBSCRIBER = 'radius:subscriber:'
    
    @classmethod
    def _generate_key(cls, prefix: str, identifier: str) -> str:
        """Generate a cache key with optional hashing for long identifiers"""
        if len(identifier) > 100:
            identifier = hashlib.md5(identifier.encode()).hexdigest()
        return f"{prefix}{identifier}"
    
    @classmethod
    def set_auth_data(cls, username: str, data: Dict[str, Any], ttl: int = 3600) -> bool:
       
        try:
            key = cls._generate_key(cls.PREFIX_AUTH, username)
            
            # Ensure expiry is timestamp
            if 'expiry' in data and isinstance(data['expiry'], datetime):
                data['expiry'] = data['expiry'].timestamp()
            
            redis_client.setex(key, ttl, json.dumps(data))
            logger.debug(f"Cached auth data for {username}")
            return True
        except Exception as e:
            logger.error(f"Failed to cache auth data: {e}")
            return False
    
    @classmethod
    def get_auth_data(cls, username: str) -> Optional[Dict[str, Any]]:
        """Get cached authentication data"""
        try:
            key = cls._generate_key(cls.PREFIX_AUTH, username)
            data = redis_client.get(key)
            if data:
                result = json.loads(data) if isinstance(data, str) else json.loads(data.decode())
                return result
            return None
        except Exception as e:
            logger.error(f"Failed to get auth data: {e}")
            return None
    
    @classmethod
    def delete_auth_data(cls, username: str) -> bool:
        """Delete cached authentication data"""
        try:
            key = cls._generate_key(cls.PREFIX_AUTH, username)
            redis_client.delete(key)
            logger.debug(f"Deleted auth data for {username}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete auth data: {e}")
            return False
    
    @classmethod
    def set_session(cls, session_id: str, data: Dict[str, Any], ttl: int = 86400) -> bool:
        """
        Cache active session data (alias for cache_session)
        """
        return cls.cache_session(session_id, data, ttl)
    
    @classmethod
    def cache_session(cls, session_id: str, data: Dict[str, Any], ttl: int = 86400) -> bool:
        
        try:
            key = cls._generate_key(cls.PREFIX_SESSION, session_id)
            redis_client.setex(key, ttl, json.dumps(data))
            
            # Also index by username
            username = data.get('username')
            if username:
                user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, username)
                redis_client.sadd(user_key, session_id)
                redis_client.expire(user_key, ttl)
            
            # Index by device MAC
            device_mac = data.get('device_mac')
            if device_mac:
                device_key = cls._generate_key(cls.PREFIX_DEVICE, device_mac)
                redis_client.sadd(device_key, session_id)
                redis_client.expire(device_key, ttl)
            
            logger.debug(f"Cached session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cache session: {e}")
            return False
    
    @classmethod
    def get_session(cls, session_id: str) -> Optional[Dict[str, Any]]:
        """Get cached session data"""
        try:
            key = cls._generate_key(cls.PREFIX_SESSION, session_id)
            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None
    
    @classmethod
    def delete_session(cls, session_id: str) -> bool:
        """Delete cached session data"""
        try:
            session = cls.get_session(session_id)
            if session:
                # Remove from username index
                username = session.get('username')
                if username:
                    user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, username)
                    redis_client.srem(user_key, session_id)
                
                # Remove from device index
                device_mac = session.get('device_mac')
                if device_mac:
                    device_key = cls._generate_key(cls.PREFIX_DEVICE, device_mac)
                    redis_client.srem(device_key, session_id)
            
            # Delete session
            key = cls._generate_key(cls.PREFIX_SESSION, session_id)
            redis_client.delete(key)
            logger.debug(f"Deleted session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session: {e}")
            return False
    
    @classmethod
    def get_user_sessions(cls, username: str) -> List[Dict[str, Any]]:
        """Get all active sessions for a user"""
        try:
            user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, username)
            session_ids = redis_client.smembers(user_key)
            
            sessions = []
            for session_id in session_ids:
                session_id = session_id.decode() if isinstance(session_id, bytes) else session_id
                session = cls.get_session(session_id)
                if session:
                    sessions.append(session)
            
            return sessions
        except Exception as e:
            logger.error(f"Failed to get user sessions: {e}")
            return []
    
    @classmethod
    def get_device_sessions(cls, device_mac: str) -> List[Dict[str, Any]]:
        """Get all active sessions for a device"""
        try:
            device_key = cls._generate_key(cls.PREFIX_DEVICE, device_mac)
            session_ids = redis_client.smembers(device_key)
            
            sessions = []
            for session_id in session_ids:
                session_id = session_id.decode() if isinstance(session_id, bytes) else session_id
                session = cls.get_session(session_id)
                if session:
                    sessions.append(session)
            
            return sessions
        except Exception as e:
            logger.error(f"Failed to get device sessions: {e}")
            return []
    
    @classmethod
    def cache_subscriber(cls, subscriber_id: str, data: Dict[str, Any], ttl: int = 3600) -> bool:
        """Cache subscriber data"""
        try:
            key = cls._generate_key(cls.PREFIX_SUBSCRIBER, subscriber_id)
            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"Failed to cache subscriber: {e}")
            return False
    
    @classmethod
    def get_subscriber(cls, subscriber_id: str) -> Optional[Dict[str, Any]]:
        """Get cached subscriber data"""
        try:
            key = cls._generate_key(cls.PREFIX_SUBSCRIBER, subscriber_id)
            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get subscriber: {e}")
            return None
    
    @classmethod
    def check_rate_limit(cls, key: str, limit: int, period: int = 60) -> bool:
        """
        Check rate limit for an action
        Returns True if under limit, False if exceeded
        """
        try:
            full_key = cls._generate_key(cls.PREFIX_RATE_LIMIT, key)
            current = redis_client.get(full_key)
            
            if current is None:
                redis_client.setex(full_key, period, 1)
                return True
            
            current = int(current)
            if current >= limit:
                logger.warning(f"Rate limit exceeded for {key}: {current}/{limit}")
                return False
            
            redis_client.incr(full_key)
            return True
        except Exception as e:
            logger.error(f"Failed to check rate limit: {e}")
            return True  # Allow on error
    
    @classmethod
    def cache_nas(cls, nas_ip: str, data: Dict[str, Any], ttl: int = 3600) -> bool:
        """Cache NAS (router) information"""
        try:
            key = cls._generate_key(cls.PREFIX_NAS, nas_ip)
            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"Failed to cache NAS: {e}")
            return False
    
    @classmethod
    def get_nas(cls, nas_ip: str) -> Optional[Dict[str, Any]]:
        """Get cached NAS information"""
        try:
            key = cls._generate_key(cls.PREFIX_NAS, nas_ip)
            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get NAS: {e}")
            return None
    
    @classmethod
    def update_rate_limit(cls, username: str, rate_limit: str) -> bool:
        """Update rate limit for active user (CoA)"""
        try:
            sessions = cls.get_user_sessions(username)
            for session in sessions:
                session['rate_limit'] = rate_limit
                cls.cache_session(session.get('session_id'), session)
            
            logger.info(f"Updated rate limit for {username} to {rate_limit}")
            return True
        except Exception as e:
            logger.error(f"Failed to update rate limit: {e}")
            return False
    
    @classmethod
    def cache_accounting(cls, acct_unique_id: str, data: Dict[str, Any], ttl: int = 86400) -> bool:
        """Cache accounting record for deduplication"""
        try:
            key = cls._generate_key(cls.PREFIX_ACCOUNTING, acct_unique_id)
            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"Failed to cache accounting: {e}")
            return False
    
    @classmethod
    def is_duplicate_accounting(cls, acct_unique_id: str) -> bool:
        """Check if accounting record has been processed"""
        try:
            key = cls._generate_key(cls.PREFIX_ACCOUNTING, acct_unique_id)
            return redis_client.exists(key) > 0
        except Exception as e:
            logger.error(f"Failed to check duplicate accounting: {e}")
            return False
    
    @classmethod
    def warmup_cache(cls, organization_id: str, subscribers: List[Dict[str, Any]]) -> None:
        """Warm up cache with subscriber data"""
        try:
            count = 0
            for subscriber in subscribers:
                subscription = subscriber.get('active_subscription')
                if subscription and subscription.get('is_active'):
                    auth_data = {
                        'password': subscriber.get('id'),  # Use subscriber ID as password
                        'organization_id': organization_id,
                        'subscriber_id': subscriber.get('id'),
                        'plan_name': subscription.get('plan_name'),
                        'bandwidth_up': subscription.get('bandwidth_up', 0),
                        'bandwidth_down': subscription.get('bandwidth_down', 0),
                        'expiry': subscription.get('expiry_time'),
                        'status': 'active',
                        'device_limit': subscription.get('device_limit', 1)
                    }
                    cls.set_auth_data(subscriber.get('phone'), auth_data)
                    count += 1
            
            logger.info(f"Warmed up cache for {count} subscribers")
        except Exception as e:
            logger.error(f"Failed to warm up cache: {e}")
    
    @classmethod
    def clear_organization_cache(cls, organization_id: str) -> int:
        """Clear all cache entries for an organization"""
        try:
            patterns = [
                f"{cls.PREFIX_AUTH}*",
                f"{cls.PREFIX_SESSION}*",
                f"{cls.PREFIX_USER_SESSIONS}*",
                f"{cls.PREFIX_DEVICE}*",
                f"{cls.PREFIX_SUBSCRIBER}*"
            ]
            
            total_deleted = 0
            for pattern in patterns:
                keys = redis_client.keys(pattern)
                if keys:
                    total_deleted += redis_client.delete(*keys)
            
            logger.info(f"Cleared {total_deleted} cache entries for org {organization_id}")
            return total_deleted
        except Exception as e:
            logger.error(f"Failed to clear organization cache: {e}")
            return 0


# Alias for backward compatibility
RedisCache = RadiusCache