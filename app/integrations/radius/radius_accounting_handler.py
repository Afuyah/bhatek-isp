"""
RADIUS Accounting Handler - Processes RADIUS Accounting-Request packets from MikroTik routers
Tracks session start/stop and data usage

This file contains ONLY the handler logic, not Flask routes.
The routes are defined in the session module to avoid duplication.
"""
from flask import current_app
from datetime import datetime
from uuid import UUID

from app.core.logging.logger import logger
from app.core.database.session import db
from app.integrations.radius.radius_cache import RadiusCache
from app.models.session import ActiveSession, RadiusAccounting


class RadiusAccountingHandler:
    """
    Handles RADIUS accounting requests from MikroTik routers
    This class is called by the session module's accounting endpoint
    """
    
    def __init__(self):
        self.cache = RadiusCache
    
    def process_accounting(self, data: dict) -> dict:
        """
        Process RADIUS accounting request
        
        Accounting Status Types:
        - 1: Start
        - 2: Stop
        - 3: Interim-Update (alive)
        - 4: Accounting-On
        - 5: Accounting-Off
        - 7: Accounting-On (for NAS reboot)
        - 8: Accounting-Off (for NAS shutdown)
        
        This is the main entry point called from the session controller.
        """
        try:
            # Extract basic info
            username = data.get('username')
            acct_status_type = int(data.get('acct_status_type', 0))
            session_id = data.get('acct_session_id')
            acct_unique_id = data.get('acct_unique_id')
            nas_ip = data.get('nas_ip_address')
            framed_ip = data.get('framed_ip_address')
            calling_station_id = data.get('calling_station_id')  # Client MAC
            called_station_id = data.get('called_station_id')    # AP MAC/SSID
            
            if not username or not session_id:
                logger.warning("Missing username or session_id in accounting")
                return {'result': 'fail', 'reason': 'Missing required fields'}
            
            # Check for duplicate accounting (prevent double processing)
            if acct_unique_id and self.cache.is_duplicate_accounting(acct_unique_id):
                logger.info(f"Duplicate accounting packet for {username}, ignoring")
                return {'result': 'ok', 'duplicate': True}
            
            # Determine organization from NAS IP
            organization_id = self._resolve_organization(nas_ip, called_station_id)
            if not organization_id:
                logger.warning(f"Cannot resolve organization for NAS {nas_ip}")
                return {'result': 'fail', 'reason': 'Organization not found'}
            
            # Process based on status type
            if acct_status_type == 1:  # Start
                result = self._process_start(
                    username=username,
                    session_id=session_id,
                    acct_unique_id=acct_unique_id,
                    organization_id=organization_id,
                    nas_ip=nas_ip,
                    framed_ip=framed_ip,
                    calling_station_id=calling_station_id,
                    called_station_id=called_station_id,
                    data=data
                )
            elif acct_status_type == 2:  # Stop
                result = self._process_stop(
                    username=username,
                    session_id=session_id,
                    acct_unique_id=acct_unique_id,
                    organization_id=organization_id,
                    data=data
                )
            elif acct_status_type == 3:  # Interim-Update
                result = self._process_interim(
                    username=username,
                    session_id=session_id,
                    organization_id=organization_id,
                    data=data
                )
            else:
                logger.info(f"Unhandled accounting status type: {acct_status_type}")
                result = {'result': 'ok', 'ignored': True}
            
            # Cache accounting record to prevent duplicates
            if acct_unique_id and result.get('result') == 'ok':
                self.cache.cache_accounting(acct_unique_id, data, ttl=86400)
            
            return result
            
        except Exception as e:
            logger.error(f"Accounting processing error: {e}", exc_info=True)
            return {'result': 'fail', 'reason': str(e)}
    
    def _resolve_organization(self, nas_ip: str, called_station_id: str) -> UUID:
        """Resolve organization ID from NAS IP or hotspot domain"""
        if nas_ip:
            cached_org = self.cache.get_nas(nas_ip)
            if cached_org:
                return UUID(cached_org.get('organization_id'))
        
        # Query database for router by IP
        from app.models.router import Router
        router = Router.query.filter_by(ip_address=nas_ip).first()
        if router:
            org_id = router.organization_id
            self.cache.cache_nas(nas_ip, {'organization_id': str(org_id)}, ttl=3600)
            return org_id
        
        return None
    
    def _process_start(self, username: str, session_id: str, acct_unique_id: str,
                       organization_id: UUID, nas_ip: str, framed_ip: str,
                       calling_station_id: str, called_station_id: str,
                       data: dict) -> dict:
        """Process accounting start - create active session"""
        try:
            # Find subscriber
            from app.modules.subscriber.service import SubscriberService
            subscriber_service = SubscriberService()
            subscriber = subscriber_service.repository.get_by_login_credential(username, organization_id)
            
            if not subscriber:
                logger.warning(f"Subscriber not found for {username}")
                return {'result': 'fail', 'reason': 'Subscriber not found'}
            
            # Get active subscription
            subscription = subscriber_service.get_active_subscription(subscriber.id, organization_id)
            
            if not subscription:
                logger.warning(f"No active subscription for {username}")
                return {'result': 'fail', 'reason': 'No active subscription'}
            
            # Check if session already exists (avoid duplicates)
            existing_session = ActiveSession.query.filter_by(
                session_id=session_id,
                username=username,
                status='active'
            ).first()
            
            if existing_session:
                logger.info(f"Session {session_id} already exists, updating")
                existing_session.last_update = datetime.utcnow()
                db.session.commit()
                return {'result': 'ok', 'session_exists': True}
            
            # Create active session record
            active_session = ActiveSession(
                organization_id=organization_id,
                subscriber_id=subscriber.id,
                subscription_id=subscription.id,
                session_type='hotspot' if subscriber.subscriber_type == 'hotspot' else 'pppoe',
                session_id=session_id,
                username=username,
                device_mac=calling_station_id,
                ip_address=framed_ip,
                called_station_id=called_station_id,
                calling_station_id=calling_station_id,
                start_time=datetime.utcnow(),
                last_update=datetime.utcnow(),
                expiry_time=subscription.expiry_time,
                status='active'
            )
            db.session.add(active_session)
            
            # Create RADIUS accounting record
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
                acct_unique_id=acct_unique_id
            )
            db.session.add(radius_acct)
            
            db.session.commit()
            
            # Update subscriber last active
            subscriber.last_active_at = datetime.utcnow()
            db.session.commit()
            
            # Cache session
            self.cache.cache_session(session_id, {
                'username': username,
                'subscriber_id': str(subscriber.id),
                'device_mac': calling_station_id,
                'session_id': session_id,
                'start_time': datetime.utcnow().isoformat()
            })
            
            logger.info(f"Session started for {username}: {session_id}")
            return {'result': 'ok', 'session_started': True}
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error processing accounting start: {e}", exc_info=True)
            return {'result': 'fail', 'reason': str(e)}
    
    def _process_stop(self, username: str, session_id: str, acct_unique_id: str,
                      organization_id: UUID, data: dict) -> dict:
        """Process accounting stop - close active session"""
        try:
            # Find active session
            active_session = ActiveSession.query.filter(
                ActiveSession.session_id == session_id,
                ActiveSession.username == username,
                ActiveSession.status == 'active'
            ).first()
            
            if active_session:
                # Update session end info
                active_session.status = 'stopped'
                active_session.last_update = datetime.utcnow()
                
                # Update usage data
                input_octets = int(data.get('acct_input_octets', 0))
                output_octets = int(data.get('acct_output_octets', 0))
                session_time = int(data.get('acct_session_time', 0))
                
                active_session.bytes_in = input_octets
                active_session.bytes_out = output_octets
                active_session.session_time = session_time
                
                # Update termination cause if provided
                terminate_cause = data.get('acct_terminate_cause')
                if terminate_cause:
                    active_session.termination_cause = self._map_terminate_cause(int(terminate_cause))
            else:
                logger.warning(f"Active session not found for {username}: {session_id}")
            
            # Update RADIUS accounting record
            radius_acct = RadiusAccounting.query.filter_by(
                session_id=session_id,
                username=username,
                acct_stop_time=None
            ).first()
            
            if radius_acct:
                radius_acct.acct_status_type = 'stop'
                radius_acct.acct_stop_time = datetime.utcnow()
                radius_acct.acct_input_octets = int(data.get('acct_input_octets', 0))
                radius_acct.acct_output_octets = int(data.get('acct_output_octets', 0))
                radius_acct.acct_session_time = int(data.get('acct_session_time', 0))
                radius_acct.acct_terminate_cause = data.get('acct_terminate_cause')
            else:
                logger.warning(f"Radius accounting record not found for {username}: {session_id}")
            
            db.session.commit()
            
            # Remove from cache
            self.cache.delete_session(session_id)
            
            logger.info(f"Session stopped for {username}: {session_id}")
            return {'result': 'ok', 'session_stopped': True}
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error processing accounting stop: {e}", exc_info=True)
            return {'result': 'fail', 'reason': str(e)}
    
    def _process_interim(self, username: str, session_id: str,
                         organization_id: UUID, data: dict) -> dict:
        """Process interim update - update session usage"""
        try:
            # Find active session
            active_session = ActiveSession.query.filter(
                ActiveSession.session_id == session_id,
                ActiveSession.username == username,
                ActiveSession.status == 'active'
            ).first()
            
            if active_session:
                # Update usage data
                active_session.bytes_in = int(data.get('acct_input_octets', 0))
                active_session.bytes_out = int(data.get('acct_output_octets', 0))
                active_session.session_time = int(data.get('acct_session_time', 0))
                active_session.last_update = datetime.utcnow()
                
                # Update RADIUS accounting record
                radius_acct = RadiusAccounting.query.filter_by(
                    session_id=session_id,
                    username=username,
                    acct_stop_time=None
                ).first()
                
                if radius_acct:
                    radius_acct.acct_input_octets = int(data.get('acct_input_octets', 0))
                    radius_acct.acct_output_octets = int(data.get('acct_output_octets', 0))
                    radius_acct.acct_session_time = int(data.get('acct_session_time', 0))
                
                db.session.commit()
                logger.debug(f"Interim update for {username}: {session_id}")
            else:
                logger.debug(f"Active session not found for interim update: {username}:{session_id}")
            
            return {'result': 'ok', 'updated': True}
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error processing interim update: {e}", exc_info=True)
            return {'result': 'fail', 'reason': str(e)}
    
    def _map_terminate_cause(self, cause_code: int) -> str:
        """Map RADIUS terminate cause code to string"""
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
            18: 'host_request'
        }
        return causes.get(cause_code, 'unknown')