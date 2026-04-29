from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, desc, func, or_
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta

from app.models.session import ActiveSession, RadiusAccounting
from app.core.database.session import db
from app.core.logging.logger import logger

class SessionRepository:
    """Data access layer for Session operations"""
    
    def __init__(self):
        self.model = ActiveSession
        self.accounting_model = RadiusAccounting
    
    def get_by_id(self, session_id: UUID, organization_id: UUID) -> Optional[ActiveSession]:
        """Get session by ID with tenant isolation"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == session_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_session_id(self, session_id: str, organization_id: UUID) -> Optional[ActiveSession]:
        """Get session by RADIUS session ID"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.session_id == session_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_session_id: {e}", exc_info=True)
            raise
    
    def get_active_by_subscriber(self, subscriber_id: UUID, organization_id: UUID) -> List[ActiveSession]:
        """Get active sessions for a subscriber"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.subscriber_id == subscriber_id,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_by_subscriber: {e}", exc_info=True)
            raise
    
    def get_active_by_device(self, device_mac: str, organization_id: UUID) -> List[ActiveSession]:
        """Get active sessions for a device MAC address"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.device_mac == device_mac,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_by_device: {e}", exc_info=True)
            raise
    
    def get_active_by_router(self, router_id: UUID, organization_id: UUID) -> List[ActiveSession]:
        """Get all active sessions on a router"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_by_router: {e}", exc_info=True)
            raise
    
    def get_active_by_access_point(self, ap_id: UUID, organization_id: UUID) -> List[ActiveSession]:
        """Get active sessions for an access point"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.access_point_id == ap_id,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_by_access_point: {e}", exc_info=True)
            raise
    
    def get_active_by_username(self, username: str, organization_id: UUID) -> List[ActiveSession]:
        """Get active sessions by username"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.username == username,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_by_username: {e}", exc_info=True)
            raise
    
    def get_all_active(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[ActiveSession]:
        """Get all active sessions for organization with pagination"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).order_by(desc(self.model.start_time)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_all_active: {e}", exc_info=True)
            raise
    
    def count_active(self, organization_id: UUID) -> int:
        """Count active sessions for organization"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_active: {e}", exc_info=True)
            return 0
    
    def count_active_by_router(self, router_id: UUID, organization_id: UUID) -> int:
        """Count active sessions on a router"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.status == 'active'
                )
            ).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_active_by_router: {e}", exc_info=True)
            return 0
    
    def get_recent_by_subscriber(self, subscriber_id: UUID, organization_id: UUID, limit: int = 10) -> List[ActiveSession]:
        """Get recent sessions for a subscriber"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.subscriber_id == subscriber_id,
                    self.model.organization_id == organization_id
                )
            ).order_by(desc(self.model.start_time)).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_recent_by_subscriber: {e}", exc_info=True)
            raise
    
    def get_sessions_by_date_range(self, organization_id: UUID, 
                                    start_date: datetime, 
                                    end_date: datetime,
                                    skip: int = 0, 
                                    limit: int = 100) -> List[ActiveSession]:
        """Get sessions within date range"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.start_time >= start_date,
                    self.model.start_time <= end_date
                )
            ).order_by(desc(self.model.start_time)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_sessions_by_date_range: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> ActiveSession:
        """Create new session"""
        try:
            session = self.model(**data)
            db.session.add(session)
            db.session.commit()
            logger.info(f"Created session: {session.id} for user {session.username}")
            return session
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, session_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[ActiveSession]:
        """Update session"""
        try:
            session = self.get_by_id(session_id, organization_id)
            if not session:
                return None
            
            for key, value in data.items():
                if hasattr(session, key) and value is not None:
                    setattr(session, key, value)
            
            db.session.commit()
            logger.debug(f"Updated session: {session_id}")
            return session
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def terminate(self, session_id: UUID, organization_id: UUID, cause: str) -> bool:
        """Terminate a session"""
        try:
            session = self.get_by_id(session_id, organization_id)
            if not session:
                return False
            
            session.status = 'terminated'
            session.termination_cause = cause
            session.session_end = datetime.utcnow()
            db.session.commit()
            logger.info(f"Terminated session: {session_id} due to {cause}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in terminate: {e}", exc_info=True)
            raise
    
    def update_stats(self, session_id: UUID, organization_id: UUID, 
                     bytes_in: int, bytes_out: int, session_time: int) -> bool:
        """Update session statistics"""
        try:
            session = self.get_by_id(session_id, organization_id)
            if session:
                session.bytes_in = bytes_in
                session.bytes_out = bytes_out
                session.session_time = session_time
                session.last_update = datetime.utcnow()
                db.session.commit()
                return True
            return False
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_stats: {e}", exc_info=True)
            raise
    
    def expire_expired_sessions(self, organization_id: UUID) -> int:
        """Mark expired sessions as expired"""
        try:
            expired = self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'active',
                    self.model.expiry_time <= datetime.utcnow()
                )
            ).update({
                'status': 'expired', 
                'termination_cause': 'session_expired',
                'session_end': datetime.utcnow()
            })
            db.session.commit()
            if expired:
                logger.info(f"Expired {expired} sessions for org {organization_id}")
            return expired
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in expire_expired_sessions: {e}", exc_info=True)
            raise


class RadiusAccountingRepository:
    """Repository for RADIUS accounting records"""
    
    def __init__(self):
        self.model = RadiusAccounting
    
    def create(self, data: Dict[str, Any]) -> RadiusAccounting:
        """Create a new RADIUS accounting record"""
        try:
            record = self.model(**data)
            db.session.add(record)
            db.session.commit()
            logger.debug(f"Created RADIUS accounting record: {record.acct_unique_id}")
            return record
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create RADIUS accounting: {e}", exc_info=True)
            raise
    
    def get_by_unique_id(self, unique_id: str, organization_id: UUID) -> Optional[RadiusAccounting]:
        """Get accounting record by unique ID"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.acct_unique_id == unique_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_unique_id: {e}", exc_info=True)
            raise
    
    def get_session_accounting(self, session_id: str, organization_id: UUID) -> List[RadiusAccounting]:
        """Get all accounting records for a session"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.session_id == session_id,
                    self.model.organization_id == organization_id
                )
            ).order_by(self.model.acct_start_time).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_session_accounting: {e}", exc_info=True)
            raise
    
    def get_user_accounting(self, username: str, organization_id: UUID, 
                            start_date: datetime = None, end_date: datetime = None,
                            limit: int = 100) -> List[RadiusAccounting]:
        """Get accounting records for a user with date range"""
        try:
            query = self.model.query.filter(
                and_(
                    self.model.username == username,
                    self.model.organization_id == organization_id
                )
            )
            
            if start_date:
                query = query.filter(self.model.acct_start_time >= start_date)
            if end_date:
                query = query.filter(self.model.acct_start_time <= end_date)
            
            return query.order_by(desc(self.model.acct_start_time)).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_user_accounting: {e}", exc_info=True)
            raise
    
    def get_organization_usage(self, organization_id: UUID, 
                                start_date: datetime, 
                                end_date: datetime) -> Dict[str, Any]:
        """Get aggregated usage statistics for an organization"""
        try:
            result = self.model.query.with_entities(
                func.sum(self.model.acct_input_octets).label('total_input'),
                func.sum(self.model.acct_output_octets).label('total_output'),
                func.sum(self.model.acct_session_time).label('total_time'),
                func.count(self.model.id).label('record_count'),
                func.count(func.distinct(self.model.username)).label('unique_users')
            ).filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.acct_start_time >= start_date,
                    self.model.acct_start_time <= end_date,
                    self.model.acct_status_type == 'Stop'
                )
            ).first()
            
            return {
                'total_input_bytes': result.total_input or 0,
                'total_output_bytes': result.total_output or 0,
                'total_bytes': (result.total_input or 0) + (result.total_output or 0),
                'total_gb': round(((result.total_input or 0) + (result.total_output or 0)) / (1024**3), 2),
                'total_session_time_seconds': result.total_time or 0,
                'total_session_time_hours': round((result.total_time or 0) / 3600, 2),
                'record_count': result.record_count or 0,
                'unique_users': result.unique_users or 0
            }
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_organization_usage: {e}", exc_info=True)
            raise
    
    def cleanup_old_records(self, days_to_keep: int = 90) -> int:
        """Delete accounting records older than specified days"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
            deleted = self.model.query.filter(
                self.model.acct_start_time < cutoff_date
            ).delete()
            db.session.commit()
            logger.info(f"Deleted {deleted} old RADIUS accounting records")
            return deleted
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in cleanup_old_records: {e}", exc_info=True)
            raise