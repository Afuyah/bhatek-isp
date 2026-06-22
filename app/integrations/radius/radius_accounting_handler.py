"""
RADIUS Accounting Handler
=========================
Processes RADIUS Accounting-Request packets forwarded by FreeRADIUS via REST.

Handles session lifecycle:
    - Start (1): Create ActiveSession + RadiusAccounting record
    - Interim-Update (3): Update data counters in real-time
    - Stop (2): Close session, accumulate data usage, update subscription totals

Multi-Tenant Isolation:
    - Organization resolved from NAS IP (router → organization)
    - All session queries scoped by organization_id
    - Cache keys include organization_id

Aggressive Session Tracking:
    - Every session start/stop is recorded immediately
    - Interim updates refresh data counters
    - Subscription.total_data_used_mb is updated on session stop
    - Duplicate detection prevents double-counting
"""

from datetime import datetime
from uuid import UUID
from typing import Optional

from app.core.logging.logger import logger
from app.core.database.session import db
from app.integrations.radius.radius_cache import RadiusCache
from app.models.session import ActiveSession, RadiusAccounting
from app.models.router import Router
from app.models.subscriber import Subscriber
from app.models.billing import Subscription


class RadiusAccountingHandler:
    """
    Handles RADIUS accounting requests from MikroTik routers.

    Accounting Status Types:
        1: Start         — Session began
        2: Stop          — Session ended
        3: Interim-Update — Periodic update (alive, data counters)
        4: Accounting-On  — NAS booted
        5: Accounting-Off — NAS shutting down
    """

    # Cache TTLs
    SESSION_CACHE_TTL = 86400     # 24 hours for session data
    ACCOUNTING_DEDUP_TTL = 86400  # 24 hours for duplicate detection
    NAS_CACHE_TTL = 3600          # 1 hour for NAS→org resolution

    def __init__(self):
        self.cache = RadiusCache

    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================

    def process_accounting(self, data: dict) -> dict:
        """
        Process a RADIUS accounting request.

        Args:
            data: Dictionary with RADIUS accounting attributes:
                - username: Subscriber identifier
                - acct_status_type: 1=Start, 2=Stop, 3=Interim
                - acct_session_id: Unique session identifier
                - acct_unique_id: Globally unique accounting ID
                - nas_ip_address: Router IP
                - framed_ip_address: Assigned IP
                - calling_station_id: Client MAC
                - called_station_id: AP MAC
                - acct_input_octets: Bytes received
                - acct_output_octets: Bytes sent
                - acct_session_time: Session duration in seconds
                - acct_terminate_cause: Reason for disconnect

        Returns:
            Dict with 'result' ('ok'/'fail') and relevant details
        """
        try:
            # Extract required fields
            username = (data.get('username') or '').strip()
            acct_status_type = int(data.get('acct_status_type', 0))
            session_id = (data.get('acct_session_id') or '').strip()
            acct_unique_id = (data.get('acct_unique_id') or '').strip()
            nas_ip = data.get('nas_ip_address')
            framed_ip = data.get('framed_ip_address')
            calling_station_id = data.get('calling_station_id')
            called_station_id = data.get('called_station_id')

            # Validate required fields
            if not username or not session_id:
                logger.warning(
                    f"Accounting: missing required fields | "
                    f"username={username} session_id={session_id}"
                )
                return {'result': 'fail', 'reason': 'Missing required fields'}

            # Duplicate detection via acct_unique_id
            if acct_unique_id and self.cache.is_duplicate_accounting(acct_unique_id):
                logger.debug(f"Accounting: duplicate packet for {username}, ignoring")
                return {'result': 'ok', 'duplicate': True}

            # Resolve organization from NAS IP (multi-tenant boundary)
            organization_id = self._resolve_organization(nas_ip)
            if not organization_id:
                logger.warning(
                    f"Accounting: cannot resolve organization | "
                    f"nas_ip={nas_ip} username={username}"
                )
                return {'result': 'fail', 'reason': 'Organization not found'}

            # Dispatch by status type
            if acct_status_type == 1:
                result = self._process_start(
                    username=username,
                    session_id=session_id,
                    acct_unique_id=acct_unique_id,
                    organization_id=organization_id,
                    nas_ip=nas_ip,
                    framed_ip=framed_ip,
                    calling_station_id=calling_station_id,
                    called_station_id=called_station_id,
                    data=data,
                )
            elif acct_status_type == 2:
                result = self._process_stop(
                    username=username,
                    session_id=session_id,
                    acct_unique_id=acct_unique_id,
                    organization_id=organization_id,
                    data=data,
                )
            elif acct_status_type == 3:
                result = self._process_interim(
                    username=username,
                    session_id=session_id,
                    organization_id=organization_id,
                    data=data,
                )
            elif acct_status_type in (4, 5):
                # NAS boot/shutdown — log and acknowledge
                logger.info(
                    f"Accounting: NAS event type={acct_status_type} | "
                    f"nas_ip={nas_ip}"
                )
                result = {'result': 'ok', 'nas_event': True}
            else:
                logger.debug(
                    f"Accounting: unhandled status type {acct_status_type}"
                )
                result = {'result': 'ok', 'ignored': True}

            # Cache for duplicate prevention
            if acct_unique_id and result.get('result') == 'ok':
                self.cache.cache_accounting(
                    acct_unique_id, data, ttl=self.ACCOUNTING_DEDUP_TTL
                )

            return result

        except Exception as e:
            logger.error(f"Accounting: processing error: {e}", exc_info=True)
            return {'result': 'fail', 'reason': str(e)}

    # =========================================================================
    # ORGANIZATION RESOLUTION
    # =========================================================================

    def _resolve_organization(self, nas_ip: str) -> Optional[UUID]:
        """
        Resolve organization ID from NAS IP address.

        Queries the Router table to find which organization owns this NAS.
        Results are cached in Redis for 1 hour since NAS→Org mapping is stable.
        """
        if not nas_ip:
            return None

        # Try cache first
        nas_cache_key = f"nas:{nas_ip}"
        cached = self.cache.get_nas(nas_cache_key)
        if cached:
            org_id = cached.get('organization_id')
            if org_id:
                return UUID(org_id)

        # Query database
        try:
            router = Router.query.filter_by(
                ip_address=nas_ip, is_active=True
            ).first()
            if router and router.organization_id:
                self.cache.cache_nas(
                    nas_cache_key,
                    {
                        'organization_id': str(router.organization_id),
                        'router_id': str(router.id),
                        'router_name': router.name,
                    },
                    ttl=self.NAS_CACHE_TTL,
                )
                return router.organization_id
        except Exception as e:
            logger.error(f"Accounting: error resolving NAS {nas_ip}: {e}")

        return None

    # =========================================================================
    # SESSION START
    # =========================================================================

    def _process_start(
        self,
        username: str,
        session_id: str,
        acct_unique_id: str,
        organization_id: UUID,
        nas_ip: str,
        framed_ip: str,
        calling_station_id: str,
        called_station_id: str,
        data: dict,
    ) -> dict:
        """
        Process accounting start — create active session record.

        Creates both:
            - ActiveSession (real-time tracking)
            - RadiusAccounting (historical audit trail)

        Also resolves the subscriber and subscription for linking.
        """
        try:
            # Resolve subscriber — try MAC first, then phone, then username
            subscriber = self._find_subscriber(username, organization_id)

            if not subscriber:
                logger.warning(
                    f"Accounting start: subscriber not found for {username} "
                    f"in org {organization_id}"
                )
                # Still create the accounting record without subscriber link
                self._create_accounting_record(
                    username=username,
                    session_id=session_id,
                    acct_unique_id=acct_unique_id,
                    organization_id=organization_id,
                    nas_ip=nas_ip,
                    framed_ip=framed_ip,
                    calling_station_id=calling_station_id,
                    called_station_id=called_station_id,
                    status_type='start',
                )
                return {'result': 'ok', 'warning': 'Subscriber not found'}

            # Get active subscription
            subscription = self._find_active_subscription(
                subscriber.id, organization_id
            )

            # Check for existing session (duplicate start)
            existing = ActiveSession.query.filter_by(
                session_id=session_id,
                username=username,
                status='active',
            ).first()

            if existing:
                existing.last_update = datetime.utcnow()
                db.session.commit()
                logger.debug(f"Accounting: session {session_id} already active, updated")
                return {'result': 'ok', 'session_exists': True}

            # Determine session type
            session_type = (
                'hotspot' if subscriber.subscriber_type == 'hotspot' else 'pppoe'
            )

            # Create ActiveSession
            active_session = ActiveSession(
                organization_id=organization_id,
                subscriber_id=subscriber.id,
                subscription_id=subscription.id if subscription else None,
                session_type=session_type,
                session_id=session_id,
                username=username,
                device_mac=(calling_station_id or '').upper(),
                ip_address=framed_ip,
                called_station_id=called_station_id,
                calling_station_id=calling_station_id,
                start_time=datetime.utcnow(),
                last_update=datetime.utcnow(),
                expiry_time=subscription.expiry_time if subscription else None,
                status='active',
            )
            db.session.add(active_session)

            # Create RadiusAccounting record
            radius_acct = RadiusAccounting(
                organization_id=organization_id,
                session_id=session_id,
                username=username,
                nas_ip_address=nas_ip,
                framed_ip_address=framed_ip,
                called_station_id=called_station_id,
                calling_station_id=calling_station_id,
                acct_status_type='start',
                acct_start_time=datetime.utcnow(),
                acct_unique_id=acct_unique_id,
            )
            db.session.add(radius_acct)

            # Update subscriber last active timestamp
            subscriber.last_active_at = datetime.utcnow()

            db.session.commit()

            # Cache session for fast lookups
            self.cache.cache_session(
                session_id,
                {
                    'username': username,
                    'subscriber_id': str(subscriber.id),
                    'subscription_id': str(subscription.id) if subscription else None,
                    'device_mac': (calling_station_id or '').upper(),
                    'session_id': session_id,
                    'session_type': session_type,
                    'organization_id': str(organization_id),
                    'start_time': datetime.utcnow().isoformat(),
                },
                ttl=self.SESSION_CACHE_TTL,
            )

            # Invalidate device count cache (new device is now active)
            if calling_station_id:
                cache_key = (
                    f"device_count:{organization_id}:{subscriber.id}"
                )
                self.cache.delete_auth_data(cache_key)

            logger.info(
                f"Accounting START | user={username} | "
                f"session={session_id} | type={session_type} | "
                f"sub={subscriber.id} | org={organization_id}"
            )
            return {'result': 'ok', 'session_started': True}

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Accounting start error for {username}: {e}", exc_info=True
            )
            return {'result': 'fail', 'reason': str(e)}

    # =========================================================================
    # SESSION STOP
    # =========================================================================

    def _process_stop(
        self,
        username: str,
        session_id: str,
        acct_unique_id: str,
        organization_id: UUID,
        data: dict,
    ) -> dict:
        """
        Process accounting stop — close the active session.

        Updates:
            - ActiveSession: status → 'stopped', usage counters
            - RadiusAccounting: stop time, usage, terminate cause
            - Subscription.total_data_used_mb: accumulated data
        """
        try:
            input_octets = int(data.get('acct_input_octets', 0))
            output_octets = int(data.get('acct_output_octets', 0))
            session_time = int(data.get('acct_session_time', 0))
            terminate_cause = data.get('acct_terminate_cause')

            # Update ActiveSession
            active_session = ActiveSession.query.filter_by(
                session_id=session_id,
                username=username,
                status='active',
            ).first()

            if active_session:
                active_session.status = 'stopped'
                active_session.last_update = datetime.utcnow()
                active_session.bytes_in = input_octets
                active_session.bytes_out = output_octets
                active_session.session_time = session_time

                if terminate_cause:
                    active_session.termination_cause = (
                        self._map_terminate_cause(int(terminate_cause))
                    )

                # Update subscription data usage
                if active_session.subscription_id:
                    self._update_subscription_data_usage(
                        active_session.subscription_id,
                        organization_id,
                        input_octets,
                        output_octets,
                    )
            else:
                logger.warning(
                    f"Accounting stop: no active session found | "
                    f"user={username} session={session_id}"
                )

            # Update RadiusAccounting record
            radius_acct = RadiusAccounting.query.filter_by(
                session_id=session_id,
                username=username,
                acct_stop_time=None,
            ).first()

            if radius_acct:
                radius_acct.acct_status_type = 'stop'
                radius_acct.acct_stop_time = datetime.utcnow()
                radius_acct.acct_input_octets = input_octets
                radius_acct.acct_output_octets = output_octets
                radius_acct.acct_session_time = session_time
                if terminate_cause:
                    radius_acct.acct_terminate_cause = terminate_cause

            db.session.commit()

            # Remove from cache
            self.cache.delete_session(session_id)

            # Invalidate device count cache
            if active_session and active_session.subscriber_id:
                cache_key = (
                    f"device_count:{organization_id}:{active_session.subscriber_id}"
                )
                self.cache.delete_auth_data(cache_key)

            # Calculate MB for logging
            total_mb = round((input_octets + output_octets) / (1024 * 1024), 2)

            logger.info(
                f"Accounting STOP | user={username} | "
                f"session={session_id} | "
                f"data={total_mb}MB | time={session_time}s | "
                f"cause={self._map_terminate_cause(int(terminate_cause)) if terminate_cause else 'unknown'}"
            )
            return {'result': 'ok', 'session_stopped': True}

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Accounting stop error for {username}: {e}", exc_info=True
            )
            return {'result': 'fail', 'reason': str(e)}

    # =========================================================================
    # INTERIM UPDATE
    # =========================================================================

    def _process_interim(
        self,
        username: str,
        session_id: str,
        organization_id: UUID,
        data: dict,
    ) -> dict:
        """
        Process interim update — refresh session counters.

        FreeRADIUS sends these periodically (typically every 5 minutes)
        while a session is active. Used for real-time usage display.
        """
        try:
            input_octets = int(data.get('acct_input_octets', 0))
            output_octets = int(data.get('acct_output_octets', 0))
            session_time = int(data.get('acct_session_time', 0))

            # Update ActiveSession
            active_session = ActiveSession.query.filter_by(
                session_id=session_id,
                username=username,
                status='active',
            ).first()

            if active_session:
                active_session.bytes_in = input_octets
                active_session.bytes_out = output_octets
                active_session.session_time = session_time
                active_session.last_update = datetime.utcnow()

            # Update RadiusAccounting
            radius_acct = RadiusAccounting.query.filter_by(
                session_id=session_id,
                username=username,
                acct_stop_time=None,
            ).first()

            if radius_acct:
                radius_acct.acct_input_octets = input_octets
                radius_acct.acct_output_octets = output_octets
                radius_acct.acct_session_time = session_time

            db.session.commit()

            # Update session cache
            cached = self.cache.get_session(session_id)
            if cached:
                cached['bytes_in'] = input_octets
                cached['bytes_out'] = output_octets
                cached['session_time'] = session_time
                cached['last_update'] = datetime.utcnow().isoformat()
                self.cache.cache_session(session_id, cached, ttl=self.SESSION_CACHE_TTL)

            logger.debug(
                f"Accounting INTERIM | user={username} | "
                f"session={session_id} | "
                f"in={input_octets} out={output_octets} time={session_time}s"
            )
            return {'result': 'ok', 'updated': True}

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Accounting interim error for {username}: {e}", exc_info=True
            )
            return {'result': 'fail', 'reason': str(e)}

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _find_subscriber(
        self,
        username: str,
        organization_id: UUID,
    ) -> Optional[Subscriber]:
        """
        Find subscriber by username (MAC, phone, or PPPoE username).

        Tries in order:
            1. MAC address → Device table → Subscriber
            2. Phone number → Subscriber.phone
            3. PPPoE username → Subscriber.username
        """
        import re

        # Try MAC address lookup
        mac_pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if re.match(mac_pattern, username):
            from app.models.subscriber import Device
            device = Device.query.filter_by(
                mac_address=username.upper(),
                organization_id=organization_id,
                is_active=True,
            ).first()
            if device:
                return Subscriber.query.filter_by(
                    id=device.subscriber_id,
                    organization_id=organization_id,
                ).first()

        # Try phone number
        phone_clean = re.sub(r'[\+\-\s]', '', username)
        if phone_clean.isdigit() and len(phone_clean) >= 9:
            subscriber = Subscriber.query.filter_by(
                phone=phone_clean, organization_id=organization_id
            ).first()
            if subscriber:
                return subscriber

        # Try PPPoE username
        subscriber = Subscriber.query.filter_by(
            username=username, organization_id=organization_id
        ).first()
        if subscriber:
            return subscriber

        # Try subscriber ID directly
        try:
            subscriber = Subscriber.query.filter_by(
                id=UUID(username), organization_id=organization_id
            ).first()
            if subscriber:
                return subscriber
        except ValueError:
            pass

        return None

    def _find_active_subscription(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
    ) -> Optional[Subscription]:
        """Find active subscription for a subscriber."""
        return Subscription.query.filter_by(
            subscriber_id=subscriber_id,
            organization_id=organization_id,
            status='active',
        ).filter(
            Subscription.expiry_time > datetime.utcnow()
        ).first()

    def _update_subscription_data_usage(
        self,
        subscription_id: UUID,
        organization_id: UUID,
        input_octets: int,
        output_octets: int,
    ) -> None:
        """Accumulate data usage on the subscription."""
        try:
            total_bytes = input_octets + output_octets
            total_mb = total_bytes / (1024 * 1024)

            subscription = Subscription.query.filter_by(
                id=subscription_id,
                organization_id=organization_id,
            ).first()

            if subscription:
                current = float(subscription.total_data_used_mb or 0)
                subscription.total_data_used_mb = current + total_mb
                db.session.commit()
                logger.debug(
                    f"Updated subscription {subscription_id} data usage: "
                    f"+{round(total_mb, 2)}MB (total: {round(current + total_mb, 2)}MB)"
                )
        except Exception as e:
            logger.error(f"Error updating subscription data usage: {e}")

    def _create_accounting_record(
        self,
        username: str,
        session_id: str,
        acct_unique_id: str,
        organization_id: UUID,
        nas_ip: str,
        framed_ip: str,
        calling_station_id: str,
        called_station_id: str,
        status_type: str,
    ) -> None:
        """Create a RadiusAccounting record without subscriber link."""
        try:
            record = RadiusAccounting(
                organization_id=organization_id,
                session_id=session_id,
                username=username,
                nas_ip_address=nas_ip,
                framed_ip_address=framed_ip,
                called_station_id=called_station_id,
                calling_station_id=calling_station_id,
                acct_status_type=status_type,
                acct_start_time=datetime.utcnow(),
                acct_unique_id=acct_unique_id,
            )
            db.session.add(record)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating accounting record: {e}")

    def _map_terminate_cause(self, cause_code: int) -> str:
        """Map RADIUS terminate cause code to human-readable string."""
        causes = {
            1: 'user_request',
            2: 'lost_carrier',
            3: 'lost_service',
            4: 'idle_timeout',
            5: 'session_timeout',
            6: 'admin_reset',
            7: 'admin_reboot',
            8: 'port_error',
            9: 'nas_error',
            10: 'nas_request',
            11: 'nas_reboot',
            12: 'port_unneeded',
            13: 'port_preempted',
            14: 'port_suspended',
            15: 'service_unavailable',
            16: 'callback',
            17: 'user_error',
            18: 'host_request',
        }
        return causes.get(cause_code, f'unknown_{cause_code}')