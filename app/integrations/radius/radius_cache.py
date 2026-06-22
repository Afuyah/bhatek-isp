"""
RADIUS Cache Service
====================
Redis-backed caching layer for RADIUS authentication, sessions, and accounting.

Multi-Tenant Isolation:
    All cache keys that relate to subscribers or auth MUST include
    organization_id to prevent cross-tenant data leaks.

Key Patterns:
    radius:auth:{org_id}:{username}         - Auth accept/reject cache
    radius:session:{session_id}             - Active session data
    radius:user:{org_id}:{username}         - User session index
    radius:device:{org_id}:{mac}            - Device session index
    radius:accounting:{acct_unique_id}      - Accounting dedup
    radius:nas:{nas_ip}                     - NAS→Org resolution
    radius:subscriber:{org_id}:{sub_id}     - Subscriber data
    radius:rate_limit:{key}                 - Rate limiting
    radius:device_count:{org_id}:{sub_id}   - Active device count

TTL Strategy:
    Auth accept: 5 minutes (300s)
    Auth reject: 30 seconds (prevents hammering)
    Session: 24 hours (86400s)
    NAS→Org: 1 hour (3600s)
    Accounting dedup: 24 hours
    Device count: 10 seconds (frequently invalidated)
    Rate limit: Configurable per use case
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import json
import hashlib

from app.core.database.redis_client import redis_client
from app.core.logging.logger import logger


class RadiusCache:
    """
    Redis-backed cache for RADIUS operations.

    All methods are classmethods — no instantiation needed.
    Keys are organization-scoped where applicable to enforce
    multi-tenant isolation.
    """

    # =========================================================================
    # KEY PREFIXES
    # =========================================================================

    PREFIX_AUTH = 'radius:auth:'
    PREFIX_REJECT = 'radius:reject:'
    PREFIX_SESSION = 'radius:session:'
    PREFIX_ACCOUNTING = 'radius:accounting:'
    PREFIX_USER_SESSIONS = 'radius:user:'
    PREFIX_NAS = 'radius:nas:'
    PREFIX_DEVICE = 'radius:device:'
    PREFIX_DEVICE_COUNT = 'radius:device_count:'
    PREFIX_RATE_LIMIT = 'radius:rate_limit:'
    PREFIX_SUBSCRIBER = 'radius:subscriber:'

    # =========================================================================
    # KEY GENERATION
    # =========================================================================

    @classmethod
    def _generate_key(cls, prefix: str, *identifiers: str) -> str:
        """
        Generate a cache key from prefix and identifiers.

        Joins identifiers with ':' for structured keys.
        Hashes individual identifiers if they exceed 100 chars.
        """
        parts = [prefix.rstrip(':')]
        for identifier in identifiers:
            if not identifier:
                continue
            if len(identifier) > 100:
                identifier = hashlib.md5(identifier.encode()).hexdigest()
            parts.append(str(identifier))
        return ':'.join(parts)

    # =========================================================================
    # AUTH CACHE (organization-scoped)
    # =========================================================================

    @classmethod
    def set_auth_data(
        cls,
        username: str,
        data: Dict[str, Any],
        ttl: int = 300,
        organization_id: str = None,
    ) -> bool:
        """
        Cache successful authentication data.

        Args:
            username: Subscriber identifier (MAC, phone, or username)
            data: Auth response data to cache
            ttl: Time-to-live in seconds (default 5 minutes)
            organization_id: Organization UUID for multi-tenant scoping

        Returns:
            True if cached successfully
        """
        try:
            # Build org-scoped key
            if organization_id:
                key = cls._generate_key(cls.PREFIX_AUTH, organization_id, username)
            else:
                key = cls._generate_key(cls.PREFIX_AUTH, username)

            # Serialize datetime objects
            serialized = cls._serialize(data)

            redis_client.setex(key, ttl, json.dumps(serialized))
            logger.debug(f"Cached auth data: {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to cache auth data for {username}: {e}")
            return False

    @classmethod
    def get_auth_data(
        cls,
        username: str,
        organization_id: str = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached authentication data.

        Args:
            username: Subscriber identifier
            organization_id: Organization UUID for multi-tenant scoping

        Returns:
            Cached auth data dict or None
        """
        try:
            if organization_id:
                key = cls._generate_key(cls.PREFIX_AUTH, organization_id, username)
            else:
                key = cls._generate_key(cls.PREFIX_AUTH, username)

            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get auth data for {username}: {e}")
            return None

    @classmethod
    def delete_auth_data(
        cls,
        username: str,
        organization_id: str = None,
    ) -> bool:
        """
        Delete cached authentication data.

        Clears both auth accept and reject cache entries.

        Args:
            username: Subscriber identifier
            organization_id: Organization UUID

        Returns:
            True if deleted
        """
        try:
            if organization_id:
                auth_key = cls._generate_key(cls.PREFIX_AUTH, organization_id, username)
                reject_key = cls._generate_key(cls.PREFIX_REJECT, organization_id, username)
            else:
                auth_key = cls._generate_key(cls.PREFIX_AUTH, username)
                reject_key = cls._generate_key(cls.PREFIX_REJECT, username)

            redis_client.delete(auth_key)
            redis_client.delete(reject_key)
            logger.debug(f"Deleted auth cache for {username}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete auth data for {username}: {e}")
            return False

    @classmethod
    def set_reject_data(
        cls,
        username: str,
        data: Dict[str, Any],
        ttl: int = 30,
        organization_id: str = None,
    ) -> bool:
        """
        Cache rejection data to prevent auth hammering.

        Short TTL (30s) prevents rapid retry while allowing legitimate
        retry after a brief wait.

        Args:
            username: Subscriber identifier
            data: Rejection reason data
            ttl: Time-to-live (default 30 seconds)
            organization_id: Organization UUID
        """
        try:
            if organization_id:
                key = cls._generate_key(cls.PREFIX_REJECT, organization_id, username)
            else:
                key = cls._generate_key(cls.PREFIX_REJECT, username)

            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"Failed to cache reject data: {e}")
            return False

    @classmethod
    def get_reject_data(
        cls,
        username: str,
        organization_id: str = None,
    ) -> Optional[Dict[str, Any]]:
        """Check if there's a cached rejection for this user."""
        try:
            if organization_id:
                key = cls._generate_key(cls.PREFIX_REJECT, organization_id, username)
            else:
                key = cls._generate_key(cls.PREFIX_REJECT, username)

            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get reject data: {e}")
            return None

    # =========================================================================
    # SESSION CACHE
    # =========================================================================

    @classmethod
    def set_session(cls, session_id: str, data: Dict[str, Any], ttl: int = 86400) -> bool:
        """Alias for cache_session."""
        return cls.cache_session(session_id, data, ttl)

    @classmethod
    def cache_session(
        cls,
        session_id: str,
        data: Dict[str, Any],
        ttl: int = 86400,
    ) -> bool:
        """
        Cache active session data.

        Also indexes by username and device MAC for fast lookups.
        Username index is org-scoped if organization_id is in data.

        Args:
            session_id: Unique session identifier
            data: Session data dict
            ttl: Time-to-live (default 24 hours)
        """
        try:
            # Cache the session itself
            key = cls._generate_key(cls.PREFIX_SESSION, session_id)
            serialized = cls._serialize(data)
            redis_client.setex(key, ttl, json.dumps(serialized))

            # Index by username (org-scoped)
            username = data.get('username')
            org_id = data.get('organization_id')
            if username:
                if org_id:
                    user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, org_id, username)
                else:
                    user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, username)
                redis_client.sadd(user_key, session_id)
                redis_client.expire(user_key, ttl)

            # Index by device MAC (org-scoped)
            device_mac = data.get('device_mac')
            if device_mac:
                if org_id:
                    device_key = cls._generate_key(cls.PREFIX_DEVICE, org_id, device_mac)
                else:
                    device_key = cls._generate_key(cls.PREFIX_DEVICE, device_mac)
                redis_client.sadd(device_key, session_id)
                redis_client.expire(device_key, ttl)

            logger.debug(f"Cached session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cache session {session_id}: {e}")
            return False

    @classmethod
    def get_session(cls, session_id: str) -> Optional[Dict[str, Any]]:
        """Get cached session data by session ID."""
        try:
            key = cls._generate_key(cls.PREFIX_SESSION, session_id)
            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get session {session_id}: {e}")
            return None

    @classmethod
    def delete_session(cls, session_id: str) -> bool:
        """
        Delete cached session and remove from indexes.

        Properly cleans up username and device MAC indexes.
        """
        try:
            session = cls.get_session(session_id)
            if session:
                username = session.get('username')
                device_mac = session.get('device_mac')
                org_id = session.get('organization_id')

                # Remove from username index
                if username:
                    if org_id:
                        user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, org_id, username)
                    else:
                        user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, username)
                    redis_client.srem(user_key, session_id)

                # Remove from device index
                if device_mac:
                    if org_id:
                        device_key = cls._generate_key(cls.PREFIX_DEVICE, org_id, device_mac)
                    else:
                        device_key = cls._generate_key(cls.PREFIX_DEVICE, device_mac)
                    redis_client.srem(device_key, session_id)

            # Delete session key
            key = cls._generate_key(cls.PREFIX_SESSION, session_id)
            redis_client.delete(key)
            logger.debug(f"Deleted session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False

    @classmethod
    def get_user_sessions(
        cls,
        username: str,
        organization_id: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all active sessions for a user.

        Args:
            username: Subscriber identifier
            organization_id: Organization UUID for scoping

        Returns:
            List of session data dicts
        """
        try:
            if organization_id:
                user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, organization_id, username)
            else:
                user_key = cls._generate_key(cls.PREFIX_USER_SESSIONS, username)

            session_ids = redis_client.smembers(user_key)

            sessions = []
            for sid in session_ids:
                sid = sid.decode() if isinstance(sid, bytes) else sid
                session = cls.get_session(sid)
                if session:
                    sessions.append(session)

            return sessions
        except Exception as e:
            logger.error(f"Failed to get user sessions for {username}: {e}")
            return []

    @classmethod
    def get_device_sessions(
        cls,
        device_mac: str,
        organization_id: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all active sessions for a device MAC.

        Args:
            device_mac: MAC address
            organization_id: Organization UUID for scoping

        Returns:
            List of session data dicts
        """
        try:
            if organization_id:
                device_key = cls._generate_key(cls.PREFIX_DEVICE, organization_id, device_mac)
            else:
                device_key = cls._generate_key(cls.PREFIX_DEVICE, device_mac)

            session_ids = redis_client.smembers(device_key)

            sessions = []
            for sid in session_ids:
                sid = sid.decode() if isinstance(sid, bytes) else sid
                session = cls.get_session(sid)
                if session:
                    sessions.append(session)

            return sessions
        except Exception as e:
            logger.error(f"Failed to get device sessions for {device_mac}: {e}")
            return []

    @classmethod
    def count_active_device_sessions(
        cls,
        organization_id: str,
        subscriber_id: str,
    ) -> int:
        """
        Count unique active device MACs for a subscriber.

        Uses Redis for speed — falls back to 0 on error.
        Cache TTL is short (10s) since this changes frequently.
        """
        try:
            cache_key = cls._generate_key(
                cls.PREFIX_DEVICE_COUNT, organization_id, subscriber_id
            )
            cached = redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
            return 0  # Cache miss — caller should compute and set
        except Exception as e:
            logger.error(f"Failed to count device sessions: {e}")
            return 0

    @classmethod
    def set_device_count(
        cls,
        organization_id: str,
        subscriber_id: str,
        count: int,
        ttl: int = 10,
    ) -> bool:
        """Cache active device count for a subscriber."""
        try:
            cache_key = cls._generate_key(
                cls.PREFIX_DEVICE_COUNT, organization_id, subscriber_id
            )
            redis_client.setex(cache_key, ttl, count)
            return True
        except Exception as e:
            logger.error(f"Failed to set device count: {e}")
            return False

    @classmethod
    def invalidate_device_count(
        cls,
        organization_id: str,
        subscriber_id: str,
    ) -> bool:
        """Invalidate cached device count when sessions change."""
        try:
            cache_key = cls._generate_key(
                cls.PREFIX_DEVICE_COUNT, organization_id, subscriber_id
            )
            redis_client.delete(cache_key)
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate device count: {e}")
            return False

    # =========================================================================
    # NAS CACHE (Organization resolution)
    # =========================================================================

    @classmethod
    def cache_nas(
        cls,
        nas_identifier: str,
        data: Dict[str, Any],
        ttl: int = 3600,
    ) -> bool:
        """
        Cache NAS (router) to organization mapping.

        Key format: radius:nas:{nas_ip}
        Data includes: organization_id, router_id, router_name

        TTL is 1 hour since NAS→Org mapping is stable.
        """
        try:
            key = cls._generate_key(cls.PREFIX_NAS, nas_identifier)
            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"Failed to cache NAS {nas_identifier}: {e}")
            return False

    @classmethod
    def get_nas(cls, nas_identifier: str) -> Optional[Dict[str, Any]]:
        """Get cached NAS information."""
        try:
            key = cls._generate_key(cls.PREFIX_NAS, nas_identifier)
            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get NAS {nas_identifier}: {e}")
            return None

    @classmethod
    def invalidate_nas(cls, nas_identifier: str) -> bool:
        """Invalidate NAS cache (call when router IP changes)."""
        try:
            key = cls._generate_key(cls.PREFIX_NAS, nas_identifier)
            redis_client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate NAS: {e}")
            return False

    # =========================================================================
    # SUBSCRIBER CACHE
    # =========================================================================

    @classmethod
    def cache_subscriber(
        cls,
        subscriber_id: str,
        data: Dict[str, Any],
        ttl: int = 300,
        organization_id: str = None,
    ) -> bool:
        """
        Cache subscriber data.

        Args:
            subscriber_id: Subscriber UUID
            data: Subscriber data
            ttl: Cache TTL (default 5 minutes)
            organization_id: Organization UUID for scoping
        """
        try:
            if organization_id:
                key = cls._generate_key(cls.PREFIX_SUBSCRIBER, organization_id, subscriber_id)
            else:
                key = cls._generate_key(cls.PREFIX_SUBSCRIBER, subscriber_id)
            redis_client.setex(key, ttl, json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"Failed to cache subscriber {subscriber_id}: {e}")
            return False

    @classmethod
    def get_subscriber(
        cls,
        subscriber_id: str,
        organization_id: str = None,
    ) -> Optional[Dict[str, Any]]:
        """Get cached subscriber data."""
        try:
            if organization_id:
                key = cls._generate_key(cls.PREFIX_SUBSCRIBER, organization_id, subscriber_id)
            else:
                key = cls._generate_key(cls.PREFIX_SUBSCRIBER, subscriber_id)
            data = redis_client.get(key)
            if data:
                return json.loads(data) if isinstance(data, str) else json.loads(data.decode())
            return None
        except Exception as e:
            logger.error(f"Failed to get subscriber {subscriber_id}: {e}")
            return None

    # =========================================================================
    # ACCOUNTING DEDUP
    # =========================================================================

    @classmethod
    def cache_accounting(
        cls,
        acct_unique_id: str,
        data: Dict[str, Any],
        ttl: int = 86400,
    ) -> bool:
        """
        Cache accounting record for duplicate detection.

        FreeRADIUS may resend accounting packets. This prevents
        double-processing of start/stop events.
        """
        try:
            key = cls._generate_key(cls.PREFIX_ACCOUNTING, acct_unique_id)
            redis_client.setex(key, ttl, json.dumps(cls._serialize(data)))
            return True
        except Exception as e:
            logger.error(f"Failed to cache accounting {acct_unique_id}: {e}")
            return False

    @classmethod
    def is_duplicate_accounting(cls, acct_unique_id: str) -> bool:
        """Check if accounting record was already processed."""
        try:
            if not acct_unique_id:
                return False
            key = cls._generate_key(cls.PREFIX_ACCOUNTING, acct_unique_id)
            return redis_client.exists(key) > 0
        except Exception as e:
            logger.error(f"Failed to check duplicate accounting: {e}")
            return False  # Process it on error (better than losing data)

    # =========================================================================
    # RATE LIMITING
    # =========================================================================

    @classmethod
    def check_rate_limit(
        cls,
        key: str,
        limit: int,
        period: int = 60,
    ) -> bool:
        """
        Check if an action is within rate limits.

        Uses Redis INCR with TTL for sliding window rate limiting.

        Args:
            key: Rate limit identifier (e.g., 'auth:login:{ip}')
            limit: Maximum allowed actions in the period
            period: Time window in seconds (default 60)

        Returns:
            True if under limit, False if exceeded
        """
        try:
            full_key = cls._generate_key(cls.PREFIX_RATE_LIMIT, key)
            current = redis_client.get(full_key)

            if current is None:
                redis_client.setex(full_key, period, 1)
                return True

            current = int(current)
            if current >= limit:
                logger.warning(f"Rate limit exceeded: {key} ({current}/{limit})")
                return False

            redis_client.incr(full_key)
            return True
        except Exception as e:
            logger.error(f"Rate limit check failed for {key}: {e}")
            return True  # Allow on error (fail open)

    # =========================================================================
    # RATE LIMIT UPDATE (CoA)
    # =========================================================================

    @classmethod
    def update_rate_limit(
        cls,
        username: str,
        rate_limit: str,
        organization_id: str = None,
    ) -> bool:
        """
        Update rate limit for all active sessions of a user.

        Used for CoA (Change of Authorization) when plan changes.
        """
        try:
            sessions = cls.get_user_sessions(username, organization_id)
            for session in sessions:
                session['rate_limit'] = rate_limit
                cls.cache_session(session.get('session_id'), session)

            logger.info(
                f"Updated rate limit for {username} to {rate_limit} "
                f"({len(sessions)} sessions)"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update rate limit for {username}: {e}")
            return False

    # =========================================================================
    # CACHE WARMUP
    # =========================================================================

    @classmethod
    def warmup_cache(
        cls,
        organization_id: str,
        subscribers: List[Dict[str, Any]],
    ) -> None:
        """
        Pre-warm the auth cache with active subscribers.

        Called on system startup or after cache flush to reduce
        database load during initial authentication requests.

        Args:
            organization_id: Organization UUID
            subscribers: List of subscriber dicts with active_subscription
        """
        try:
            count = 0
            for subscriber in subscribers:
                subscription = subscriber.get('active_subscription')
                if not subscription or not subscription.get('is_active'):
                    continue

                # Determine username based on subscriber type
                username = (
                    subscriber.get('phone') or
                    subscriber.get('username') or
                    subscriber.get('id')
                )
                if not username:
                    continue

                auth_data = {
                    'subscriber_id': subscriber.get('id'),
                    'organization_id': organization_id,
                    'plan_name': subscription.get('plan_name'),
                    'bandwidth_up': subscription.get('bandwidth_up', 0),
                    'bandwidth_down': subscription.get('bandwidth_down', 0),
                    'session_timeout': subscription.get('session_timeout', 86400),
                    'idle_timeout': subscription.get('idle_timeout', 300),
                    'expiry': subscription.get('expiry_time'),
                    'device_limit': subscription.get('device_limit', 1),
                    'status': 'active',
                }

                cls.set_auth_data(
                    username=username,
                    data=auth_data,
                    ttl=300,
                    organization_id=organization_id,
                )
                count += 1

            logger.info(
                f"Cache warmup complete: {count} subscribers "
                f"for org {organization_id}"
            )
        except Exception as e:
            logger.error(f"Cache warmup failed for org {organization_id}: {e}")

    # =========================================================================
    # CACHE INVALIDATION
    # =========================================================================

    @classmethod
    def clear_organization_cache(cls, organization_id: str) -> int:
        """
        Clear all cache entries for an organization.

        Uses SCAN instead of KEYS for production safety.
        Clears: auth, reject, user sessions, device indexes, subscribers.

        Args:
            organization_id: Organization UUID

        Returns:
            Number of keys deleted
        """
        try:
            patterns = [
                f"{cls.PREFIX_AUTH}{organization_id}:*",
                f"{cls.PREFIX_REJECT}{organization_id}:*",
                f"{cls.PREFIX_USER_SESSIONS}{organization_id}:*",
                f"{cls.PREFIX_DEVICE}{organization_id}:*",
                f"{cls.PREFIX_SUBSCRIBER}{organization_id}:*",
                f"{cls.PREFIX_DEVICE_COUNT}{organization_id}:*",
            ]

            total_deleted = 0

            for pattern in patterns:
                # Use SCAN for production safety (avoid KEYS blocking)
                cursor = 0
                while True:
                    cursor, keys = redis_client.scan(
                        cursor=cursor, match=pattern, count=100
                    )
                    if keys:
                        total_deleted += redis_client.delete(*keys)
                    if cursor == 0:
                        break

            logger.info(
                f"Cleared {total_deleted} cache entries for org {organization_id}"
            )
            return total_deleted

        except Exception as e:
            logger.error(
                f"Failed to clear organization cache for {organization_id}: {e}"
            )
            return 0

    @classmethod
    def invalidate_subscriber_cache(
        cls,
        organization_id: str,
        username: str,
    ) -> bool:
        """
        Invalidate all cache entries for a specific subscriber.

        Called when subscription changes, device added/removed,
        or subscriber status changes.
        """
        try:
            # Clear auth cache
            cls.delete_auth_data(username, organization_id)

            # Clear active sessions
            sessions = cls.get_user_sessions(username, organization_id)
            for session in sessions:
                cls.delete_session(session.get('session_id'))

            # Clear subscriber cache
            sub_key = cls._generate_key(
                cls.PREFIX_SUBSCRIBER, organization_id, username
            )
            redis_client.delete(sub_key)

            logger.debug(
                f"Invalidated cache for {username} in org {organization_id}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate subscriber cache: {e}")
            return False

    # =========================================================================
    # HELPERS
    # =========================================================================

    @classmethod
    def _serialize(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Serialize data for JSON storage.

        Converts datetime objects to ISO format strings.
        Handles nested dicts and lists.
        """
        if not isinstance(data, dict):
            return data

        serialized = {}
        for key, value in data.items():
            if isinstance(value, datetime):
                serialized[key] = value.isoformat()
            elif isinstance(value, dict):
                serialized[key] = cls._serialize(value)
            elif isinstance(value, list):
                serialized[key] = [
                    cls._serialize(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                serialized[key] = value
        return serialized

    @classmethod
    def ping(cls) -> bool:
        """Check if Redis is reachable."""
        try:
            return redis_client.ping()
        except Exception:
            return False


# Backward compatibility alias
RedisCache = RadiusCache