"""
RADIUS Authentication Handler
==============================
Processes RADIUS Access-Request packets forwarded by FreeRADIUS via REST.

Authentication Paths:
    1. MAC-only auth (hotspot auto-connect):
       - Device connects to hotspot → MikroTik sends MAC as username, empty password
       - Handler looks up Device by MAC → finds Subscriber → checks Subscription → auto-accepts
       - If MAC unknown but phone auth exists, registers new device (within limit)
    
    2. Phone+password auth (M-Pesa self-remediation):
       - User enters M-Pesa code or phone in captive portal
       - Handler verifies payment, finds/creates subscription, registers MAC
       - Password = M-Pesa code or subscription ID
    
    3. PPPoE auth (username+password):
       - Traditional username/password via PPPoE dial-in
       - Handler validates credentials, checks subscription, returns accept/reject

Multi-Tenant Isolation:
    - Organization resolved from NAS IP (router → organization)
    - All subscriber/device lookups scoped by organization_id
    - Cache keys include organization_id: "radius:auth:{org_id}:{username}"
    - A user from Org A cannot connect to Org B's hotspot

FreeRADIUS rlm_rest Response Format (CORRECTED):
    - ACCEPT: Attributes returned flat at JSON top level
      Example: {"Session-Timeout": 86400, "Idle-Timeout": 300, "Mikrotik-Rate-Limit": "10M/10M"}
    - REJECT: {"Reply-Message": "No active subscription"}
    - ERROR:  {"Reply-Message": "Internal server error"}
"""

from flask import request, jsonify, Blueprint, current_app
from datetime import datetime, timedelta
from uuid import UUID
import hashlib
import re

from app.core.logging.logger import logger
from app.core.database.session import db
from app.modules.subscriber.service import SubscriberService
from app.modules.subscriber.repository import SubscriberRepository
from app.integrations.radius.radius_cache import RadiusCache
from app.integrations.radius.dictionary import MikroTikDictionary
from app.models.subscriber import Device
from app.models.router import Router
from app.models.organization import Organization

# Create blueprint for RADIUS auth endpoints
radius_auth_bp = Blueprint('radius_auth', __name__, url_prefix='/api/radius')


class RadiusAuthHandler:
    """
    Handles RADIUS authentication with multi-tenant isolation.

    Three authentication modes:
        - 'mac'      : MAC address only, no password (hotspot auto-connect)
        - 'phone'    : Phone number + password/code (M-Pesa self-remediation)
        - 'pppoe'    : Username + password (PPPoE dial-in)

    Organization scoping is enforced at every step:
        1. NAS IP → Router → Organization (cached 1 hour)
        2. All DB queries include organization_id
        3. Cache keys include organization_id
        4. Cross-org authentication is impossible
    """

    # Cache TTLs (seconds)
    AUTH_CACHE_TTL = 300            # 5 minutes for accepted auth
    REJECT_CACHE_TTL = 30           # 30 seconds for rejected auth (prevent hammering)
    NAS_CACHE_TTL = 3600            # 1 hour for NAS→org resolution
    DEVICE_LIMIT_CACHE_TTL = 10     # 10 seconds for device count

    def __init__(self):
        self.subscriber_service = SubscriberService()
        self.subscriber_repo = SubscriberRepository()

    # =========================================================================
    # MAIN AUTHENTICATION ENTRY POINT
    # =========================================================================

    def authenticate(
        self,
        username: str,
        password: str = '',
        nas_ip: str = None,
        calling_station_id: str = None,
        called_station_id: str = None,
        organization_id: str = None,
        chap_challenge: str = None,
        chap_response: str = None,
    ) -> dict:
        """
        Authenticate a user against the subscriber database.

        This is THE main entry point called by the Flask route.
        It orchestrates the entire authentication flow:
            1. Resolve organization from NAS IP
            2. Determine auth mode (MAC/phone/PPPoE)
            3. Check rejection cache
            4. Dispatch to mode-specific handler
            5. Cache result
            6. Return accept/reject

        Args:
            username: MAC address, phone number, or PPPoE username
            password: Empty for MAC auth, M-Pesa code/subscription ID for phone,
                      plaintext password for PPPoE
            nas_ip: IP address of the MikroTik router (used to resolve organization)
            calling_station_id: Client's MAC address (from RADIUS)
            called_station_id: AP's MAC address (from RADIUS)
            organization_id: Pre-resolved organization UUID (if known)
            chap_challenge: CHAP challenge for PPPoE CHAP auth
            chap_response: CHAP response for PPPoE CHAP auth

        Returns:
            Dict with 'success' (bool), 'attributes' (dict on success),
            'error' (str on failure), 'subscriber_id', 'subscription_id', etc.
        """
        try:
            # Step 1: Resolve organization from NAS IP (critical for multi-tenancy)
            if not organization_id:
                organization_id = self._resolve_organization(nas_ip)

            if not organization_id:
                logger.warning(
                    f"Cannot resolve organization | "
                    f"username={username} nas_ip={nas_ip}"
                )
                return self._reject("Organization not found — router not registered")

            # Step 2: Determine authentication mode based on username format
            auth_mode = self._determine_auth_mode(username, password, calling_station_id)

            logger.debug(
                f"RADIUS auth attempt | mode={auth_mode} | "
                f"username={username} | nas={nas_ip} | org={organization_id}"
            )

            # Step 3: Check rejection cache first (prevent hammering)
            cache_key = self._cache_key('reject', organization_id, username)
            if RadiusCache.get_auth_data(cache_key):
                logger.debug(f"Reject cache hit for {username} in org {organization_id}")
                return self._reject("Authentication rejected — try again later")

            # Step 4: Authenticate based on determined mode
            if auth_mode == 'mac':
                result = self._auth_mac(
                    mac_address=username.upper(),
                    calling_station_id=calling_station_id,
                    organization_id=organization_id,
                    nas_ip=nas_ip,
                    called_station_id=called_station_id,
                )
            elif auth_mode == 'phone':
                result = self._auth_phone(
                    phone=username,
                    password=password,
                    calling_station_id=calling_station_id,
                    organization_id=organization_id,
                )
            elif auth_mode == 'pppoe':
                result = self._auth_pppoe(
                    username=username,
                    password=password,
                    organization_id=organization_id,
                    chap_challenge=chap_challenge,
                    chap_response=chap_response,
                )
            else:
                return self._reject("Unable to determine authentication mode")

            # Step 5: Cache result for performance
            if result.get('success'):
                # Cache successful auth for fast subsequent requests
                auth_cache_key = self._cache_key('auth', organization_id, username)
                RadiusCache.set_auth_data(auth_cache_key, result, ttl=self.AUTH_CACHE_TTL)
                logger.info(
                    f"RADIUS ACCEPT | mode={auth_mode} | "
                    f"user={username} | sub={result.get('subscriber_id')} | "
                    f"plan={result.get('plan_name')} | org={organization_id}"
                )
            else:
                # Cache rejection briefly to prevent hammering
                reject_cache_key = self._cache_key('reject', organization_id, username)
                RadiusCache.set_auth_data(
                    reject_cache_key,
                    {'reason': result.get('error'), 'reason_code': result.get('reason_code')},
                    ttl=self.REJECT_CACHE_TTL,
                )
                logger.warning(
                    f"RADIUS REJECT | mode={auth_mode} | "
                    f"user={username} | reason={result.get('error')} | "
                    f"code={result.get('reason_code')} | org={organization_id}"
                )

            return result

        except Exception as e:
            logger.error(
                f"RADIUS auth error for {username}: {e}", exc_info=True
            )
            return self._reject("Internal authentication error")

    # =========================================================================
    # AUTH MODE DETECTION
    # =========================================================================

    def _determine_auth_mode(
        self,
        username: str,
        password: str,
        calling_station_id: str = None,
    ) -> str:
        """
        Determine authentication mode based on username format.

        Detection logic:
            - MAC address format (AA:BB:CC:DD:EE:FF or AA-BB-CC-DD-EE-FF) → 'mac'
            - Phone number format (2547XXXXXXXX or 07XXXXXXXX) → 'phone'
            - Everything else → 'pppoe'

        The password field is NOT used for mode detection — it's validated
        by the specific auth handler after mode is determined.
        """
        if not username:
            return 'unknown'

        username = username.strip()

        # Check if MAC address (6 hex octets separated by : or -)
        mac_pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if re.match(mac_pattern, username):
            return 'mac'

        # Check if phone number (digits only, with optional + or 254 prefix)
        phone_clean = re.sub(r'[\+\-\s]', '', username)
        if phone_clean.isdigit() and len(phone_clean) >= 9 and len(phone_clean) <= 15:
            return 'phone'

        # Default to PPPoE username
        return 'pppoe'

    # =========================================================================
    # MAC AUTHENTICATION (HOTSPOT AUTO-CONNECT)
    # =========================================================================

    def _auth_mac(
        self,
        mac_address: str,
        calling_station_id: str,
        organization_id: str,
        nas_ip: str = None,
        called_station_id: str = None,
    ) -> dict:
        """
        Authenticate via MAC address only (no password required).

        This is the auto-connect path for hotspot users:
            1. User's device associates with WiFi AP
            2. MikroTik captures the MAC and sends RADIUS Access-Request
            3. Username = MAC address, Password = (empty)
            4. FreeRADIUS forwards to this handler via REST
            5. Handler looks up Device by MAC → finds Subscriber → checks Subscription
            6. If valid and device limit not exceeded → auto-accepts
            7. If invalid → rejects (user sees captive portal)

        Organization scoping ensures a MAC registered in Org A
        cannot connect to Org B's hotspot.
        """
        try:
            # Use calling_station_id as MAC if provided (it's the client's real MAC)
            effective_mac = (calling_station_id or mac_address).upper()

            # Check auth cache first for performance
            cache_key = self._cache_key('auth', organization_id, effective_mac)
            cached = RadiusCache.get_auth_data(cache_key)
            if cached:
                logger.debug(f"MAC auth cache hit for {effective_mac}")
                return cached

            # Step 1: Find device by MAC within this organization
            device = self.subscriber_repo.get_device_by_mac(
                effective_mac, UUID(organization_id)
            )

            if not device or not device.is_active:
                logger.info(
                    f"MAC auth: device {effective_mac} not registered "
                    f"in org {organization_id}"
                )
                return self._reject(
                    "Device not registered. Please register your device first.",
                    reason_code='device_not_found',
                )

            # Step 2: Get the subscriber who owns this device
            subscriber = self.subscriber_repo.get_by_id(
                device.subscriber_id, UUID(organization_id)
            )

            if not subscriber or subscriber.status != 'active':
                logger.info(
                    f"MAC auth: subscriber not active for device {effective_mac}"
                )
                return self._reject(
                    "Account inactive. Please contact support.",
                    reason_code='subscriber_inactive',
                )

            # Step 3: Get active subscription
            subscription = self.subscriber_repo.get_active_subscription(
                subscriber.id, UUID(organization_id)
            )

            if not subscription:
                logger.info(
                    f"MAC auth: no active subscription for {subscriber.display_name}"
                )
                return self._reject(
                    "No active subscription. Please purchase a plan.",
                    reason_code='no_subscription',
                )

            # Check if subscription has expired
            if subscription.expiry_time <= datetime.utcnow():
                logger.info(
                    f"MAC auth: subscription expired for {subscriber.display_name}"
                )
                return self._reject(
                    "Subscription expired. Please renew.",
                    reason_code='subscription_expired',
                )

            # Step 4: Enforce device limit
            device_limit = subscription.get_device_limit()
            active_device_count = self._count_active_devices(
                subscriber.id, UUID(organization_id)
            )

            if active_device_count > device_limit:
                logger.info(
                    f"MAC auth: device limit reached for {subscriber.display_name} "
                    f"({active_device_count}/{device_limit})"
                )
                return self._reject(
                    f"Device limit reached ({device_limit} devices). "
                    "Please disconnect another device first.",
                    reason_code='device_limit_reached',
                )

            # Step 5: Update device last seen timestamp
            self.subscriber_repo.update_device_last_seen(
                device.id, UUID(organization_id)
            )

            # Step 6: Build accept response with bandwidth, timeout attributes
            result = self._build_accept(
                subscriber=subscriber,
                subscription=subscription,
                session_type='hotspot',
            )

            # Add device-specific info
            result['device_id'] = str(device.id)
            result['device_mac'] = effective_mac

            logger.info(
                f"MAC auth ACCEPT | mac={effective_mac} | "
                f"sub={subscriber.display_name} | org={organization_id}"
            )
            return result

        except Exception as e:
            logger.error(f"MAC auth error for {mac_address}: {e}", exc_info=True)
            return self._reject("Authentication error")

    # =========================================================================
    # PHONE AUTHENTICATION (M-PESA SELF-REMEDIATION)
    # =========================================================================

    def _auth_phone(
        self,
        phone: str,
        password: str,
        calling_station_id: str,
        organization_id: str,
    ) -> dict:
        """
        Authenticate via phone number + password/code.

        This handles the self-remediation flow where a user:
            - Already paid via M-Pesa (has M-Pesa receipt code)
            - Enters their phone number and M-Pesa code in the captive portal
            - System verifies payment and auto-connects them

        Password can be:
            - M-Pesa receipt code (e.g., "QK8H3X9P") — verified against transactions
            - Subscription UUID — for hotspot users with active subscription
            - Transaction reference — any valid payment reference

        If the user doesn't exist yet but has a valid M-Pesa code,
        a subscriber is auto-created (get_or_create_by_phone).
        """
        try:
            # Normalize phone number to 254XXXXXXXXX format
            normalized_phone = self.subscriber_service.normalize_phone(phone)
            client_mac = (calling_station_id or '').upper()
            org_uuid = UUID(organization_id)

            # Step 1: Find subscriber by phone number
            subscriber = self.subscriber_repo.get_by_phone(
                normalized_phone, org_uuid
            )

            # Step 2: If no subscriber exists, check if this is an M-Pesa code
            # (new user who paid but hasn't been auto-registered yet)
            if not subscriber:
                if self._is_mpesa_code(password):
                    result = self._verify_mpesa_code(
                        code=password,
                        phone=normalized_phone,
                        organization_id=organization_id,
                        client_mac=client_mac,
                    )
                    if result.get('success'):
                        return result
                return self._reject(
                    "Phone number not found. Please check and try again.",
                    reason_code='phone_not_found',
                )

            # Step 3: Check subscriber status
            if subscriber.status != 'active':
                return self._reject(
                    "Account is inactive. Please contact support.",
                    reason_code='subscriber_inactive',
                )

            # Step 4: Check if password is an M-Pesa receipt code
            if self._is_mpesa_code(password):
                # Verify the M-Pesa payment
                result = self._verify_mpesa_code(
                    code=password,
                    phone=normalized_phone,
                    organization_id=organization_id,
                    client_mac=client_mac,
                    subscriber=subscriber,
                )
                if result.get('success'):
                    return result
                # If M-Pesa verification failed, fall through to other checks

            # Step 5: Try subscription-based auth (password = subscription UUID)
            subscription = self.subscriber_repo.get_active_subscription(
                subscriber.id, org_uuid
            )

            if subscription and str(subscription.id) == password:
                # Register the device MAC if provided
                if client_mac:
                    self._register_device_for_subscriber(
                        subscriber.id, org_uuid, client_mac
                    )
                return self._build_accept(
                    subscriber=subscriber,
                    subscription=subscription,
                    session_type='hotspot',
                )

            # Step 6: If subscription exists, check if password matches a transaction reference
            if subscription:
                transaction = self._find_transaction_by_reference(password, org_uuid)
                if transaction and transaction.subscriber_id == subscriber.id:
                    if client_mac:
                        self._register_device_for_subscriber(
                            subscriber.id, org_uuid, client_mac
                        )
                    return self._build_accept(
                        subscriber=subscriber,
                        subscription=subscription,
                        session_type='hotspot',
                    )

            # Step 7: Check for any recent successful payment (last 24 hours)
            recent_payment = self._find_recent_payment(normalized_phone, org_uuid)
            if recent_payment:
                # Create subscription from this payment
                subscription = self._create_subscription_from_payment(
                    subscriber, recent_payment, org_uuid
                )
                if subscription:
                    if client_mac:
                        self._register_device_for_subscriber(
                            subscriber.id, org_uuid, client_mac
                        )
                    return self._build_accept(
                        subscriber=subscriber,
                        subscription=subscription,
                        session_type='hotspot',
                    )

            # No valid auth method found
            return self._reject(
                "No active subscription found. Please purchase a plan.",
                reason_code='no_subscription',
            )

        except Exception as e:
            logger.error(f"Phone auth error for {phone}: {e}", exc_info=True)
            return self._reject("Authentication error")

    # =========================================================================
    # PPPoE AUTHENTICATION
    # =========================================================================

    def _auth_pppoe(
        self,
        username: str,
        password: str,
        organization_id: str,
        chap_challenge: str = None,
        chap_response: str = None,
    ) -> dict:
        """
        Authenticate via PPPoE username + password.

        Standard username/password authentication for PPPoE dial-in users.
        Supports both PAP (cleartext) and CHAP (challenge-handshake) protocols.

        PPPoE users are created by ISP admins, not auto-created.
        """
        try:
            org_uuid = UUID(organization_id)

            # Step 1: Find subscriber by username
            subscriber = self.subscriber_repo.get_by_username(username, org_uuid)

            if not subscriber or subscriber.subscriber_type != 'pppoe':
                logger.info(f"PPPoE auth: subscriber not found: {username}")
                return self._reject(
                    "Invalid username or password.",
                    reason_code='invalid_credentials',
                )

            if subscriber.status != 'active':
                return self._reject(
                    "Account is inactive. Please contact support.",
                    reason_code='subscriber_inactive',
                )

            # Step 2: Verify password (CHAP or PAP)
            password_valid = False

            if chap_challenge and chap_response:
                # CHAP authentication
                password_valid = self._verify_chap(
                    subscriber, chap_challenge, chap_response
                )
            else:
                # PAP authentication (cleartext)
                password_valid = self._verify_password(subscriber, password)

            if not password_valid:
                logger.info(f"PPPoE auth: invalid password for {username}")
                return self._reject(
                    "Invalid username or password.",
                    reason_code='invalid_credentials',
                )

            # Step 3: Check active subscription
            subscription = self.subscriber_repo.get_active_subscription(
                subscriber.id, org_uuid
            )

            if not subscription:
                return self._reject(
                    "No active subscription. Please contact support.",
                    reason_code='no_subscription',
                )

            if subscription.expiry_time <= datetime.utcnow():
                return self._reject(
                    "Subscription expired. Please renew.",
                    reason_code='subscription_expired',
                )

            # Step 4: Accept
            return self._build_accept(
                subscriber=subscriber,
                subscription=subscription,
                session_type='pppoe',
            )

        except Exception as e:
            logger.error(f"PPPoE auth error for {username}: {e}", exc_info=True)
            return self._reject("Authentication error")

    # =========================================================================
    # ORGANIZATION RESOLUTION (MULTI-TENANT BOUNDARY)
    # =========================================================================

    def _resolve_organization(
        self,
        nas_ip: str,
        called_station_id: str = None,
    ) -> str:
        """
        Resolve organization ID from NAS IP address.

        This is THE critical multi-tenancy boundary:
        - NAS IP identifies the MikroTik router
        - Router belongs to exactly one Organization
        - All subsequent authentication is scoped to that organization
        - A router from Org A cannot authenticate users for Org B

        Resolution order:
            1. Redis cache (1 hour TTL) — fast, avoids DB query
            2. Router table query — authoritative source
            3. Cache miss → query DB → populate cache

        Returns None if the NAS IP is not recognized (unregistered router).
        """
        if not nas_ip:
            return None

        # Try cache first (NAS→Org mapping is stable)
        nas_cache_key = f"nas:{nas_ip}"
        cached = RadiusCache.get_nas(nas_cache_key)
        if cached:
            org_id = cached.get('organization_id')
            if org_id:
                logger.debug(f"NAS cache hit: {nas_ip} → org {org_id}")
                return org_id

        # Query database for router by IP
        try:
            router = Router.query.filter_by(
                ip_address=nas_ip, is_active=True
            ).first()
            if router and router.organization_id:
                org_id = str(router.organization_id)
                # Cache for 1 hour
                RadiusCache.cache_nas(
                    nas_cache_key,
                    {
                        'organization_id': org_id,
                        'router_id': str(router.id),
                        'router_name': router.name,
                    },
                    ttl=self.NAS_CACHE_TTL,
                )
                logger.debug(
                    f"Resolved NAS {nas_ip} → org {org_id} "
                    f"(router: {router.name})"
                )
                return org_id
        except Exception as e:
            logger.error(f"Error resolving organization from NAS {nas_ip}: {e}")

        logger.warning(f"NAS {nas_ip} not found in router table — unregistered router")
        return None

    # =========================================================================
    # DEVICE REGISTRATION & LIMIT ENFORCEMENT
    # =========================================================================

    def _register_device_for_subscriber(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        mac_address: str,
    ) -> Optional[Device]:
        """
        Auto-register a device for a subscriber during authentication.

        This is called when:
            - A user connects with a new MAC address
            - The MAC is not already registered to this subscriber
            - Device limit has not been exceeded

        If the MAC already exists but belongs to another subscriber
        (device reuse scenario), it is reassigned to this subscriber.

        Returns:
            Device instance if registered, None if limit exceeded or error
        """
        try:
            mac = mac_address.upper()

            # Check if device already registered to this subscriber
            existing = self.subscriber_repo.get_device_by_subscriber_and_mac(
                subscriber_id, mac, organization_id
            )
            if existing:
                # Just update last seen
                existing.last_seen_at = datetime.utcnow()
                db.session.commit()
                logger.debug(f"Device {mac} already registered, updated last_seen")
                return existing

            # Check device limit before registering
            subscription = self.subscriber_repo.get_active_subscription(
                subscriber_id, organization_id
            )
            device_limit = subscription.get_device_limit() if subscription else 5
            current_devices = self.subscriber_repo.get_devices(
                subscriber_id, organization_id
            )

            if len(current_devices) >= device_limit:
                logger.info(
                    f"Device limit reached for sub {subscriber_id}: "
                    f"{len(current_devices)}/{device_limit}"
                )
                return None

            # Register the new device
            device_data = {
                'mac_address': mac,
                'device_type': 'unknown',
                'is_primary': len(current_devices) == 0,
                'is_active': True,
                'last_seen_at': datetime.utcnow(),
            }
            device = self.subscriber_repo.add_device(
                subscriber_id=subscriber_id,
                organization_id=organization_id,
                data=device_data,
            )

            logger.info(
                f"Auto-registered device {mac} for subscriber {subscriber_id}"
            )
            return device

        except Exception as e:
            logger.error(f"Error registering device {mac_address}: {e}", exc_info=True)
            return None

    def _count_active_devices(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
    ) -> int:
        """
        Count unique devices with active sessions for a subscriber.

        Uses Redis cache (10 second TTL) for fast counting.
        Falls back to database query on cache miss.

        Returns a high number (99) on error to fail closed (deny access).
        """
        try:
            # Try Redis cache first
            cache_key = f"device_count:{organization_id}:{subscriber_id}"
            cached = RadiusCache.get_auth_data(cache_key)
            if cached is not None:
                return int(cached)

            # Database query for distinct active device MACs
            from app.models.session import ActiveSession
            count = db.session.query(
                ActiveSession.device_mac
            ).filter(
                ActiveSession.subscriber_id == subscriber_id,
                ActiveSession.organization_id == organization_id,
                ActiveSession.status == 'active',
                ActiveSession.device_mac.isnot(None),
            ).distinct().count()

            # Cache for short period
            RadiusCache.set_auth_data(
                cache_key, count, ttl=self.DEVICE_LIMIT_CACHE_TTL,
            )

            return count

        except Exception as e:
            logger.error(f"Error counting active devices for sub {subscriber_id}: {e}")
            return 99  # Fail closed — deny if we can't count

    # =========================================================================
    # M-PESA INTEGRATION
    # =========================================================================

    def _is_mpesa_code(self, code: str) -> bool:
        """
        Check if a string looks like an M-Pesa receipt code.

        M-Pesa codes are typically 8-15 uppercase alphanumeric characters.
        Examples: "QK8H3X9P", "SHC4N6XKTJ", "QLB2M7R8F1"
        """
        if not code:
            return False
        return bool(re.match(r'^[A-Z0-9]{8,15}$', code.upper()))

    def _verify_mpesa_code(
        self,
        code: str,
        phone: str,
        organization_id: str,
        client_mac: str,
        subscriber=None,
    ) -> dict:
        """
        Verify an M-Pesa payment code and grant/deny access.

        This is the self-remediation flow:
            1. User enters M-Pesa receipt code in captive portal
            2. System looks up the transaction by receipt number
            3. Verifies it belongs to this phone number
            4. Ensures there's an active subscription (or creates one)
            5. Registers the device MAC
            6. Returns accept with bandwidth attributes

        Args:
            code: M-Pesa receipt code (e.g., "QK8H3X9P")
            phone: Normalized phone number
            organization_id: Organization UUID
            client_mac: Device MAC address to register
            subscriber: Optional pre-resolved subscriber

        Returns:
            Accept or reject dict
        """
        try:
            org_uuid = UUID(organization_id)

            # Step 1: Find transaction by M-Pesa receipt
            transaction = self._find_transaction_by_receipt(code, org_uuid)

            if not transaction:
                logger.info(f"M-Pesa code not found in transactions: {code}")
                return {
                    'success': False,
                    'error': 'M-Pesa code not found. Please check and try again.',
                    'reason_code': 'mpesa_not_found',
                }

            # Step 2: Verify transaction was successful
            if transaction.status != 'success':
                logger.info(f"M-Pesa transaction {code} status is {transaction.status}")
                return {
                    'success': False,
                    'error': 'Payment was not successful. Please contact support.',
                    'reason_code': 'payment_not_successful',
                }

            # Step 3: Find or create subscriber by phone
            if not subscriber:
                subscriber = self.subscriber_repo.get_by_phone(phone, org_uuid)
                if not subscriber:
                    # Auto-create subscriber for new M-Pesa users
                    subscriber, created = self.subscriber_service.get_or_create_hotspot_subscriber(
                        organization_id=org_uuid,
                        phone=phone,
                    )
                    logger.info(
                        f"{'Created' if created else 'Found'} subscriber {subscriber.id} "
                        f"for M-Pesa code {code}"
                    )

            # Step 4: Check for active subscription
            subscription = self.subscriber_repo.get_active_subscription(
                subscriber.id, org_uuid
            )

            if not subscription:
                # Try to create subscription from this transaction
                subscription = self._create_subscription_from_transaction(
                    subscriber, transaction, org_uuid
                )
                if subscription:
                    logger.info(
                        f"Created subscription {subscription.id} from M-Pesa "
                        f"transaction {code}"
                    )

            if not subscription:
                return {
                    'success': False,
                    'error': 'No active subscription. Payment may not have been processed yet.',
                    'reason_code': 'no_subscription',
                }

            if subscription.expiry_time <= datetime.utcnow():
                return {
                    'success': False,
                    'error': 'Subscription has expired. Please renew.',
                    'reason_code': 'subscription_expired',
                }

            # Step 5: Register the device MAC for auto-connect
            if client_mac:
                self._register_device_for_subscriber(
                    subscriber.id, org_uuid, client_mac
                )

            # Step 6: Build accept response
            return self._build_accept(
                subscriber=subscriber,
                subscription=subscription,
                session_type='hotspot',
            )

        except Exception as e:
            logger.error(f"Error verifying M-Pesa code {code}: {e}", exc_info=True)
            return {
                'success': False,
                'error': 'Error verifying payment. Please try again.',
                'reason_code': 'verification_error',
            }

    def _find_transaction_by_receipt(
        self,
        mpesa_receipt: str,
        organization_id: UUID,
    ):
        """
        Find a transaction by M-Pesa receipt number.

        The receipt is the code Safaricom gives after payment,
        e.g., "QK8H3X9P" from M-Pesa confirmation SMS.
        """
        try:
            from app.models.payment import Transaction
            return Transaction.query.filter_by(
                mpesa_receipt=mpesa_receipt.upper(),
                organization_id=organization_id,
            ).first()
        except Exception as e:
            logger.error(f"Error finding transaction by receipt {mpesa_receipt}: {e}")
            return None

    def _find_transaction_by_reference(
        self,
        reference: str,
        organization_id: UUID,
    ):
        """
        Find a transaction by internal reference number.

        Used when the password is a transaction_reference rather than
        an M-Pesa receipt code.
        """
        try:
            from app.models.payment import Transaction
            return Transaction.query.filter_by(
                transaction_reference=reference,
                organization_id=organization_id,
            ).first()
        except Exception as e:
            logger.error(f"Error finding transaction by reference {reference}: {e}")
            return None

    def _find_recent_payment(
        self,
        phone: str,
        organization_id: UUID,
    ):
        """
        Find any recent successful payment for a phone number.

        Searches the last 24 hours for successful transactions
        where the payment_details JSON contains the phone number.

        This catches cases where the payment webhook created a transaction
        but the subscription wasn't auto-created.
        """
        try:
            from app.models.payment import Transaction

            cutoff = datetime.utcnow() - timedelta(hours=24)

            return Transaction.query.filter(
                Transaction.organization_id == organization_id,
                Transaction.status == 'success',
                Transaction.created_at >= cutoff,
            ).filter(
                Transaction.payment_details.contains(phone)
            ).order_by(Transaction.created_at.desc()).first()
        except Exception as e:
            logger.error(f"Error finding recent payment for {phone}: {e}")
            return None

    def _create_subscription_from_payment(
        self,
        subscriber,
        transaction,
        organization_id: UUID,
    ):
        """
        Create a subscription from a verified payment transaction.

        Extracts the plan_id from transaction.custom_data if available.
        Otherwise, the subscription must be created by the payment webhook.
        """
        try:
            plan_id = None
            if transaction.custom_data:
                plan_id = transaction.custom_data.get('plan_id')

            if not plan_id:
                logger.warning(
                    f"No plan_id in transaction {transaction.id} custom_data"
                )
                return None

            return self.subscriber_service.create_subscription(
                subscriber_id=subscriber.id,
                organization_id=organization_id,
                plan_id=UUID(plan_id),
                auto_renew=False,
            )
        except Exception as e:
            logger.error(
                f"Error creating subscription from transaction "
                f"{transaction.id}: {e}"
            )
            return None

    def _create_subscription_from_transaction(
        self,
        subscriber,
        transaction,
        organization_id: UUID,
    ):
        """Alias for _create_subscription_from_payment."""
        return self._create_subscription_from_payment(
            subscriber, transaction, organization_id
        )

    # =========================================================================
    # PASSWORD VERIFICATION (PPPoE)
    # =========================================================================

    def _verify_password(self, subscriber, password: str) -> bool:
        """
        Verify cleartext password for PPPoE PAP authentication.

        Decrypts the stored encrypted password and compares.
        """
        try:
            if not subscriber.password_encrypted:
                logger.debug(f"No encrypted password for subscriber {subscriber.id}")
                return False

            from app.core.security.encryption import EncryptionService
            encryption = EncryptionService()
            decrypted = encryption.decrypt(subscriber.password_encrypted)
            return password == decrypted
        except Exception as e:
            logger.error(f"Password verification error for sub {subscriber.id}: {e}")
            return False

    def _verify_chap(
        self,
        subscriber,
        challenge: str,
        response: str,
    ) -> bool:
        """
        Verify CHAP authentication for PPPoE.

        CHAP uses MD5(challenge + password) instead of sending
        the password in cleartext.
        """
        try:
            if not subscriber.password_encrypted:
                return False

            from app.core.security.encryption import EncryptionService
            encryption = EncryptionService()
            password = encryption.decrypt(subscriber.password_encrypted)

            # CHAP response = MD5(challenge + password)
            expected = hashlib.md5(
                challenge.encode() + password.encode()
            ).hexdigest().upper()

            return response.upper() == expected
        except Exception as e:
            logger.error(f"CHAP verification error for sub {subscriber.id}: {e}")
            return False

    # =========================================================================
    # RESPONSE BUILDERS
    # =========================================================================

    def _build_accept(
        self,
        subscriber,
        subscription,
        session_type: str = 'hotspot',
    ) -> dict:
        """
        Build RADIUS Access-Accept response with all attributes.

        Includes:
            - Session-Timeout: Maximum session duration
            - Idle-Timeout: Disconnect after idle period
            - Mikrotik-Rate-Limit: Bandwidth limits (e.g., "10M/5M")
            - Mikrotik-Total-Limit: Data cap in bytes (if data-based plan)
            - Simultaneous-Use: Device limit enforcement
            - Mikrotik-Address-List: Firewall address list for paid users

        Prefers subscription overrides over plan defaults for bandwidth.
        """
        plan = subscription.plan
        attributes = {}

        # Session timeout (plan default or 24 hours)
        session_timeout = plan.session_timeout_seconds or 86400
        attributes['Session-Timeout'] = session_timeout

        # Idle timeout (plan default or 5 minutes)
        idle_timeout = plan.idle_timeout_seconds or 300
        attributes['Idle-Timeout'] = idle_timeout

        # Bandwidth limits (prefer subscription overrides over plan defaults)
        bw_up = subscription.bandwidth_up_mbps or plan.bandwidth_up_mbps or 0
        bw_down = subscription.bandwidth_down_mbps or plan.bandwidth_down_mbps or 0

        if bw_up > 0 or bw_down > 0:
            # If only one direction is set, use same value for both
            actual_up = bw_up if bw_up > 0 else bw_down
            actual_down = bw_down if bw_down > 0 else bw_up
            rate_limit = MikroTikDictionary.format_rate_limit(
                upload=actual_up,
                download=actual_down,
                unit="M",
            )
            attributes['Mikrotik-Rate-Limit'] = rate_limit

        # Data cap (for data-based plans, convert MB to bytes)
        if plan.validity_type == 'data_based' and plan.data_limit_mb:
            total_limit_bytes = int(plan.data_limit_mb) * 1024 * 1024
            attributes['Mikrotik-Total-Limit'] = total_limit_bytes

        # Device limit for MikroTik simultaneous-use enforcement
        device_limit = subscription.get_device_limit()
        attributes['Simultaneous-Use'] = str(device_limit)

        # Address list for firewall marking (allows QoS and access control)
        attributes['Mikrotik-Address-List'] = 'paid-users'

        return {
            'success': True,
            'attributes': attributes,
            'subscriber_id': str(subscriber.id),
            'subscription_id': str(subscription.id),
            'session_type': session_type,
            'device_limit': device_limit,
            'plan_name': plan.name,
            'expiry_time': subscription.expiry_time.isoformat(),
        }

    def _reject(self, reason: str, reason_code: str = None) -> dict:
        """
        Build RADIUS Access-Reject response.

        Includes a machine-readable reason_code for the captive portal
        to display appropriate messages to the user.
        """
        result = {
            'success': False,
            'error': reason,
        }
        if reason_code:
            result['reason_code'] = reason_code
        return result

    # =========================================================================
    # CACHE HELPERS
    # =========================================================================

    def _cache_key(self, prefix: str, organization_id: str, identifier: str) -> str:
        """
        Generate organization-scoped cache key.

        Format: "radius:{prefix}:{org_id}:{identifier}"

        This ensures multi-tenant isolation in Redis — a cache entry
        for Org A's user cannot collide with Org B's user.
        """
        return f"radius:{prefix}:{organization_id}:{identifier}"


# =============================================================================
# FLASK ROUTES
# =============================================================================

@radius_auth_bp.route('/authenticate', methods=['POST'])
def radius_authenticate():
    """
    POST /api/radius/authenticate

    Called by FreeRADIUS rlm_rest module for every authentication request.

    Request body (sent by FreeRADIUS rlm_rest):
        {
            "username": "AA:BB:CC:DD:EE:FF" or "254712345678" or "john_doe",
            "password": "" or "mpesa_code" or "plaintext_password",
            "nas_ip_address": "192.168.88.1",
            "calling_station_id": "AA:BB:CC:DD:EE:FF",
            "called_station_id": "CC:DD:EE:FF:AA:BB"
        }

    Response format (parsed by FreeRADIUS rlm_rest):
        ACCEPT: RADIUS attributes returned as flat JSON keys
            {"Session-Timeout": 86400, "Idle-Timeout": 300, "Mikrotik-Rate-Limit": "10M/10M"}
        REJECT: Reply-Message attribute
            {"Reply-Message": "No active subscription"}
    """
    try:
        # Parse request — handles both JSON and form-encoded data
        data = request.get_json(silent=True) or request.form or {}

        username = (data.get('username') or '').strip()
        password = data.get('password', '')
        nas_ip = data.get('nas_ip_address')
        calling_station_id = data.get('calling_station_id')
        called_station_id = data.get('called_station_id')
        organization_slug = data.get('organization_slug')
        chap_challenge = data.get('chap_challenge')
        chap_response = data.get('chap_response')

        if not username:
            return jsonify({'Reply-Message': 'Missing username'}), 200

        handler = RadiusAuthHandler()

        # Pre-resolve organization if slug provided in request
        # (alternative to NAS IP resolution)
        organization_id = None
        if organization_slug:
            org = Organization.query.filter_by(
                slug=organization_slug, status='active'
            ).first()
            if org:
                organization_id = str(org.id)

        # Run authentication
        result = handler.authenticate(
            username=username,
            password=password,
            nas_ip=nas_ip,
            calling_station_id=calling_station_id,
            called_station_id=called_station_id,
            organization_id=organization_id,
            chap_challenge=chap_challenge,
            chap_response=chap_response,
        )

        if result.get('success'):
            # SUCCESS: Return RADIUS attributes flat at JSON top level
            # This is the format FreeRADIUS rlm_rest expects
            response_data = {}
            response_data.update(result.get('attributes', {}))
            return jsonify(response_data), 200
        else:
            # REJECTION: Return Reply-Message
            return jsonify({
                'Reply-Message': result.get('error', 'Access denied'),
            }), 200

    except Exception as e:
        logger.error(f"RADIUS authenticate endpoint error: {e}", exc_info=True)
        return jsonify({'Reply-Message': 'Internal server error'}), 200


@radius_auth_bp.route('/disconnect', methods=['POST'])
def radius_disconnect():
    """
    POST /api/radius/disconnect

    Called to clear cached auth data for a user.
    Used when:
        - Subscription is cancelled
        - Device is removed
        - Admin forces disconnect

    Clears both auth accept cache and reject cache.
    """
    try:
        data = request.get_json(silent=True) or request.form or {}
        username = (data.get('username') or '').strip()
        organization_id = data.get('organization_id')

        if not username:
            return jsonify({'result': 'fail', 'reason': 'Missing username'}), 400

        # Clear all cached data for this user
        if organization_id:
            RadiusCache.delete_auth_data(
                f"radius:auth:{organization_id}:{username}"
            )
            RadiusCache.delete_auth_data(
                f"radius:reject:{organization_id}:{username}"
            )
        else:
            # Fallback — clear without org scope
            RadiusCache.delete_auth_data(username)

        logger.info(
            f"RADIUS disconnect: user={username} org={organization_id}"
        )
        return jsonify({'result': 'ack'}), 200

    except Exception as e:
        logger.error(f"RADIUS disconnect error: {e}", exc_info=True)
        return jsonify({'result': 'fail', 'reason': str(e)}), 500