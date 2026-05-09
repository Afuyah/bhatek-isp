from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, or_, desc
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

from app.models.access_point import AccessPoint
from app.core.database.session import db
from app.core.logging.logger import logger


class AccessPointRepository:
    """Data access layer for AccessPoint operations with tenant isolation"""
    
    def __init__(self):
        self.model = AccessPoint
    
    def get_by_id(self, ap_id: UUID, organization_id: UUID, include_inactive: bool = False) -> Optional[AccessPoint]:
        """Get access point by ID with organization isolation"""
        try:
            filters = [
                self.model.id == ap_id,
                self.model.organization_id == organization_id
            ]
            
            if not include_inactive:
                filters.append(self.model.is_active == True)
            
            return self.model.query.filter(and_(*filters)).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_mac(self, mac_address: str, organization_id: UUID) -> Optional[AccessPoint]:
        """Get access point by MAC address within organization"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.mac_address == mac_address,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_mac: {e}", exc_info=True)
            raise
    
    def get_by_router(self, router_id: UUID, organization_id: UUID, 
                      skip: int = 0, limit: int = 100) -> List[AccessPoint]:
        """Get all access points for a specific router"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).order_by(self.model.name).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_router: {e}", exc_info=True)
            raise
    
    def get_by_hotspot(self, hotspot_server_id: UUID, organization_id: UUID) -> List[AccessPoint]:
        """Get all access points for a specific hotspot server"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.hotspot_server_id == hotspot_server_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).order_by(self.model.name).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_hotspot: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100,
                            status: str = None, router_id: UUID = None) -> List[AccessPoint]:
        """Get all access points for an organization with optional filters"""
        try:
            filters = [
                self.model.organization_id == organization_id,
                self.model.is_active == True
            ]
            
            if status:
                filters.append(self.model.status == status)
            
            if router_id:
                filters.append(self.model.router_id == router_id)
            
            return self.model.query.filter(
                and_(*filters)
            ).order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def get_all_active(self, organization_id: UUID) -> List[AccessPoint]:
        """Get all active access points for dropdown/selection"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).order_by(self.model.name).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_all_active: {e}", exc_info=True)
            raise
    
    def get_online_aps(self, organization_id: UUID) -> List[AccessPoint]:
        """Get all online access points for monitoring"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.status == 'online'
                )
            ).order_by(self.model.name).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_online_aps: {e}", exc_info=True)
            raise
    
    def get_offline_aps(self, organization_id: UUID) -> List[AccessPoint]:
        """Get all offline access points for monitoring alerts"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.status == 'offline'
                )
            ).order_by(desc(self.model.last_seen_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_offline_aps: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> AccessPoint:
        """Create a new access point"""
        try:
            ap = self.model(**data)
            db.session.add(ap)
            db.session.commit()
            logger.info(f"Created access point: {ap.name} (MAC: {ap.mac_address})")
            return ap
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, ap_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[AccessPoint]:
        """Update an access point"""
        try:
            ap = self.get_by_id(ap_id, organization_id, include_inactive=True)
            if not ap:
                return None
            
            for key, value in data.items():
                if hasattr(ap, key) and value is not None:
                    setattr(ap, key, value)
            
            db.session.commit()
            logger.info(f"Updated access point: {ap_id}")
            return ap
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def update_status(self, ap_id: UUID, organization_id: UUID, status: str, 
                      error_message: str = None) -> bool:
        """Update access point status and timestamp"""
        try:
            ap = self.get_by_id(ap_id, organization_id, include_inactive=True)
            if not ap:
                return False
            
            ap.status = status
            ap.last_seen_at = datetime.utcnow()
            
            if error_message:
                ap.description = error_message[:500] if error_message else None
            
            db.session.commit()
            logger.debug(f"Access point {ap_id} status updated to {status}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_status: {e}", exc_info=True)
            raise
    
    def delete(self, ap_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate an access point"""
        try:
            ap = self.get_by_id(ap_id, organization_id, include_inactive=True)
            if not ap:
                return False
            
            if soft_delete:
                ap.is_active = False
                ap.status = 'deactivated'
            else:
                db.session.delete(ap)
            
            db.session.commit()
            logger.info(f"Access point {ap_id} {'deactivated' if soft_delete else 'deleted'}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise
    
    def count_by_organization(self, organization_id: UUID, status: str = None) -> int:
        """Count access points in organization"""
        try:
            filters = [
                self.model.organization_id == organization_id,
                self.model.is_active == True
            ]
            if status:
                filters.append(self.model.status == status)
            
            return self.model.query.filter(and_(*filters)).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_organization: {e}", exc_info=True)
            raise
    
    def count_by_router(self, router_id: UUID, organization_id: UUID) -> int:
        """Count access points belonging to a specific router"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_router: {e}", exc_info=True)
            raise
    
    def get_aps_with_issues(self, organization_id: UUID) -> List[AccessPoint]:
        """Get access points that need attention (offline or error)"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.status.in_(['offline', 'error'])
                )
            ).order_by(desc(self.model.last_seen_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_aps_with_issues: {e}", exc_info=True)
            raise