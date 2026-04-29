from typing import Dict, Any, List, Optional, Tuple
from uuid import UUID
from datetime import datetime, timedelta
from flask import current_app

from app.modules.session.repository import SessionRepository, RadiusAccountingRepository
from app.models.session import ActiveSession
from app.integrations.radius.redius_cache import RadiusCache
from app.integrations.mikrotik.client import MikroTikClient
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError
from app.core.security.encryption import EncryptionService

class SessionService:
    """Complete session management service"""
    
    def __init__(self):
        self.repository = SessionRepository()
        self.radius_accounting_repo = RadiusAccountingRepository()
        self.mikrotik_client = MikroTikClient()
        self.encryption = EncryptionService()
        self.radius_cache = RadiusCache()
    
    def create_session(self, data: Dict[str, Any]) -> ActiveSession:
        """Create new session with RADIUS cache integration"""
        # Ensure required fields
        if 'last_update' not in data:
            data['last_update'] = datetime.utcnow()
        if 'status' not in data:
            data['status'] = 'active'
        
        session = self.repository.create(data)
        
        # Cache in Redis for RADIUS
        cache_data = {
            'username': session.username,
            'organization_id': str(session.organization_id),
            'subscriber_id': str(session.subscriber_id) if session.subscriber_id else None,
            'device_mac': str(session.device_mac) if session.device_mac else None,
            'ip_address': str(session.ip_address) if session.ip_address else None,
            'start_time': session.start_time.timestamp(),
            'expiry_time': session.expiry_time.timestamp(),
        }
        
        ttl = int((session.expiry_time - datetime.utcnow()).total_seconds())
        if ttl > 0:
            self.radius_cache.cache_session(str(session.id), cache_data, ttl)
        
        logger.info(f"Created session {session.id} for user {session.username}")
        return session
    
    def get_session(self, session_id: UUID, organization_id: UUID) -> Optional[ActiveSession]:
        """Get session by ID"""
        return self.repository.get_by_id(session_id, organization_id)
    
    def get_active_sessions_by_subscriber(self, subscriber_id: UUID, organization_id: UUID) -> List[ActiveSession]:
        """Get active sessions for a subscriber"""
        return self.repository.get_active_by_subscriber(subscriber_id, organization_id)
    
    def get_active_sessions_by_device(self, device_mac: str, organization_id: UUID) -> List[ActiveSession]:
        """Get active sessions for a device"""
        return self.repository.get_active_by_device(device_mac, organization_id)
    
    def get_active_sessions_by_username(self, username: str, organization_id: UUID) -> List[ActiveSession]:
        """Get active sessions by username"""
        return self.repository.get_active_by_username(username, organization_id)
    
    def terminate_session(self, session_id: UUID, organization_id: UUID, cause: str) -> bool:
        """Terminate a session with router disconnect"""
        session = self.repository.get_by_id(session_id, organization_id)
        if not session:
            raise NotFoundError("Session not found")
        
        # Attempt router disconnect
        try:
            if session.router_id:
                from app.modules.router.repository import RouterRepository
                router_repo = RouterRepository()
                router = router_repo.get_by_id(session.router_id, organization_id)
                if router:
                    password = self.encryption.decrypt(router.password_encrypted)
                    
                    self.mikrotik_client.disconnect_user(
                        host=str(router.ip_address),
                        username=router.username,
                        password=password,
                        target_username=session.username,
                        port=router.api_port
                    )
        except Exception as e:
            logger.warning(f"Router disconnect failed for session {session_id}: {e}")
        
        # Update database
        self.repository.terminate(session_id, organization_id, cause)
        
        # Remove from RADIUS cache
        self.radius_cache.delete_session(str(session_id))
        
        logger.info(f"Terminated session {session_id}: {cause}")
        return True
    
    def terminate_user_sessions(self, username: str, organization_id: UUID, cause: str) -> int:
        """Terminate all sessions for a user"""
        sessions = self.repository.get_active_by_username(username, organization_id)
        
        for session in sessions:
            self.terminate_session(session.id, organization_id, cause)
        
        logger.info(f"Terminated {len(sessions)} sessions for user {username}")
        return len(sessions)
    
    def update_session_stats(self, session_id: UUID, organization_id: UUID,
                             bytes_in: int, bytes_out: int, session_time: int) -> bool:
        """Update session statistics"""
        return self.repository.update_stats(session_id, organization_id, bytes_in, bytes_out, session_time)
    
    def sync_router_sessions(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Synchronize sessions from MikroTik router"""
        from app.modules.router.repository import RouterRepository
        router_repo = RouterRepository()
        router = router_repo.get_by_id(router_id, organization_id)
        
        if not router:
            raise NotFoundError("Router not found")
        
        password = self.encryption.decrypt(router.password_encrypted)
        
        # Get sessions from router
        router_sessions = self.mikrotik_client.get_active_sessions(
            host=str(router.ip_address),
            username=router.username,
            password=password,
            port=router.api_port
        )
        
        # Get local sessions
        local_sessions = self.repository.get_active_by_router(router_id, organization_id)
        local_map = {s.session_id: s for s in local_sessions if s.session_id}
        
        created = 0
        updated = 0
        terminated = 0
        
        # Track seen session IDs
        seen_session_ids = set()
        
        for rs in router_sessions:
            session_id = rs.get('session_id') or rs.get('.id')
            if not session_id:
                continue
            
            seen_session_ids.add(session_id)
            
            if session_id in local_map:
                # Update existing session
                self.repository.update_stats(
                    local_map[session_id].id,
                    organization_id,
                    rs.get('bytes_in', 0),
                    rs.get('bytes_out', 0),
                    rs.get('session_time', 0)
                )
                updated += 1
            else:
                # Create new session
                try:
                    session_data = {
                        'organization_id': organization_id,
                        'router_id': router_id,
                        'username': rs.get('username') or rs.get('user'),
                        'device_mac': rs.get('mac_address') or rs.get('caller_id'),
                        'ip_address': rs.get('ip_address') or rs.get('address'),
                        'session_id': session_id,
                        'session_type': 'hotspot',
                        'start_time': datetime.utcnow(),
                        'last_update': datetime.utcnow(),
                        'expiry_time': datetime.utcnow() + timedelta(hours=24),
                        'status': 'active'
                    }
                    self.repository.create(session_data)
                    created += 1
                except Exception as e:
                    logger.warning(f"Failed to create session from router: {e}")
        
        # Terminate local sessions not seen on router
        for session_id, session in local_map.items():
            if session_id not in seen_session_ids:
                self.repository.terminate(session.id, organization_id, 'router_sync')
                self.radius_cache.delete_session(str(session.id))
                terminated += 1
        
        result = {
            'router_id': str(router_id),
            'router_sessions': len(router_sessions),
            'local_sessions': len(local_sessions),
            'created': created,
            'updated': updated,
            'terminated': terminated,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        logger.info(f"Router sync completed for {router_id}: {result}")
        return result
    
    def cleanup_expired_sessions(self, organization_id: UUID) -> int:
        """Clean up expired sessions"""
        expired_count = self.repository.expire_expired_sessions(organization_id)
        
        if expired_count > 0:
            logger.info(f"Cleaned up {expired_count} expired sessions for org {organization_id}")
        
        return expired_count
    
    def get_session_stats(self, organization_id: UUID) -> Dict[str, Any]:
        """Get session statistics for organization"""
        active_count = self.repository.count_active(organization_id)
        
        # Get recent session stats (last 24 hours)
        day_ago = datetime.utcnow() - timedelta(days=1)
        
        from app.modules.session.models import ActiveSession
        recent_stats = ActiveSession.query.filter(
            ActiveSession.organization_id == organization_id,
            ActiveSession.start_time >= day_ago
        ).with_entities(
            func.count(ActiveSession.id).label('total'),
            func.sum(ActiveSession.bytes_in + ActiveSession.bytes_out).label('total_bytes')
        ).first()
        
        return {
            'active_sessions': active_count,
            'last_24h_sessions': recent_stats.total or 0,
            'last_24h_bytes': recent_stats.total_bytes or 0,
            'last_24h_gb': round((recent_stats.total_bytes or 0) / (1024**3), 2)
        }
    
    def process_radius_accounting(self, accounting_data: Dict[str, Any], organization_id: UUID) -> Dict[str, Any]:
        """Process RADIUS accounting packet"""
        try:
            acct_unique_id = accounting_data.get('acct_unique_id')
            
            # Check for duplicate
            if acct_unique_id:
                existing = self.radius_accounting_repo.get_by_unique_id(acct_unique_id, organization_id)
                if existing:
                    logger.warning(f"Duplicate RADIUS accounting packet: {acct_unique_id}")
                    return {'success': False, 'reason': 'duplicate'}
            
            # Create accounting record
            record_data = {
                'organization_id': organization_id,
                'session_id': accounting_data.get('session_id'),
                'username': accounting_data.get('username'),
                'nas_ip_address': accounting_data.get('nas_ip_address'),
                'framed_ip_address': accounting_data.get('framed_ip_address'),
                'called_station_id': accounting_data.get('called_station_id'),
                'calling_station_id': accounting_data.get('calling_station_id'),
                'acct_status_type': accounting_data.get('acct_status_type'),
                'acct_start_time': accounting_data.get('acct_start_time'),
                'acct_stop_time': accounting_data.get('acct_stop_time'),
                'acct_input_octets': accounting_data.get('acct_input_octets', 0),
                'acct_output_octets': accounting_data.get('acct_output_octets', 0),
                'acct_session_time': accounting_data.get('acct_session_time', 0),
                'acct_terminate_cause': accounting_data.get('acct_terminate_cause'),
                'acct_unique_id': acct_unique_id
            }
            
            record = self.radius_accounting_repo.create(record_data)
            
            # Update active session if this is a stop record
            if accounting_data.get('acct_status_type') == 'Stop':
                active_session = self.repository.get_by_session_id(
                    accounting_data.get('session_id'), 
                    organization_id
                )
                if active_session:
                    self.terminate_session(
                        active_session.id, 
                        organization_id, 
                        accounting_data.get('acct_terminate_cause', 'radius_stop')
                    )
            
            return {
                'success': True, 
                'record_id': str(record.id),
                'unique_id': acct_unique_id
            }
            
        except Exception as e:
            logger.error(f"Error processing RADIUS accounting: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    def get_user_usage(self, username: str, organization_id: UUID, days: int = 30) -> Dict[str, Any]:
        """Get usage statistics for a user"""
        try:
            start_date = datetime.utcnow() - timedelta(days=days)
            
            records = self.radius_accounting_repo.get_user_accounting(
                username, organization_id, start_date, None, 1000
            )
            
            total_bytes_in = sum(r.acct_input_octets or 0 for r in records)
            total_bytes_out = sum(r.acct_output_octets or 0 for r in records)
            total_time = sum(r.acct_session_time or 0 for r in records)
            
            # Get active sessions
            active_sessions = self.repository.get_active_by_username(username, organization_id)
            
            return {
                'username': username,
                'period_days': days,
                'total_bytes_in': total_bytes_in,
                'total_bytes_out': total_bytes_out,
                'total_bytes_gb': round((total_bytes_in + total_bytes_out) / (1024**3), 2),
                'total_session_time_seconds': total_time,
                'total_session_time_hours': round(total_time / 3600, 2),
                'session_count': len(records),
                'active_sessions': len(active_sessions),
                'active_session_details': [s.to_dict() for s in active_sessions]
            }
        except Exception as e:
            logger.error(f"Error getting user usage: {e}", exc_info=True)
            raise
    
    def get_organization_usage(self, organization_id: UUID, days: int = 30) -> Dict[str, Any]:
        """Get usage statistics for organization"""
        try:
            start_date = datetime.utcnow() - timedelta(days=days)
            end_date = datetime.utcnow()
            
            usage = self.radius_accounting_repo.get_organization_usage(
                organization_id, start_date, end_date
            )
            
            # Add active sessions count
            active_count = self.repository.count_active(organization_id)
            usage['active_sessions'] = active_count
            usage['period_days'] = days
            
            return usage
        except Exception as e:
            logger.error(f"Error getting organization usage: {e}", exc_info=True)
            raise