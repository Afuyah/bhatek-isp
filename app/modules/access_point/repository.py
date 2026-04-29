from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, desc
from sqlalchemy.exc import SQLAlchemyError
from app.models.access_point import AccessPoint
from app.core.database.session import db
from app.core.logging.logger import logger

class AccessPointRepository:
    """Data access layer for AccessPoint operations"""
    
    def __init__(self):
        self.model = AccessPoint
    
    def get_by_id(self, ap_id: UUID, organization_id: UUID) -> Optional[AccessPoint]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == ap_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_mac(self, mac_address: str, organization_id: UUID) -> Optional[AccessPoint]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.mac_address == mac_address,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_mac: {e}", exc_info=True)
            raise
    
    def get_by_router(self, router_id: UUID, organization_id: UUID) -> List[AccessPoint]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_router: {e}", exc_info=True)
            raise
    
    def get_by_hotspot(self, hotspot_server_id: UUID, organization_id: UUID) -> List[AccessPoint]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.hotspot_server_id == hotspot_server_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_hotspot: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[AccessPoint]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> AccessPoint:
        try:
            ap = self.model(**data)
            db.session.add(ap)
            db.session.commit()
            logger.info(f"Created access point: {ap.name}")
            return ap
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, ap_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[AccessPoint]:
        try:
            ap = self.get_by_id(ap_id, organization_id)
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