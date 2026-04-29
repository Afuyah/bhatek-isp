from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, or_, desc
from sqlalchemy.exc import SQLAlchemyError

from app.models.router import Router, HotspotServer, PPPoeServer
from app.core.database.session import db
from app.core.logging.logger import logger

class RouterRepository:
    """Data access layer for Router operations"""
    
    def __init__(self):
        self.model = Router
    
    def get_by_id(self, router_id: UUID, organization_id: UUID) -> Optional[Router]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_ip(self, ip_address: str, organization_id: UUID) -> Optional[Router]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.ip_address == ip_address,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_ip: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[Router]:
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
    
    def create(self, data: Dict[str, Any]) -> Router:
        try:
            router = self.model(**data)
            db.session.add(router)
            db.session.commit()
            logger.info(f"Created router: {router.name}")
            return router
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Router]:
        try:
            router = self.get_by_id(router_id, organization_id)
            if not router:
                return None
            
            for key, value in data.items():
                if hasattr(router, key) and value is not None:
                    setattr(router, key, value)
            
            db.session.commit()
            logger.info(f"Updated router: {router_id}")
            return router
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def update_status(self, router_id: UUID, organization_id: UUID, status: str):
        try:
            router = self.get_by_id(router_id, organization_id)
            if router:
                router.status = status
                router.last_seen_at = db.func.now()
                db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_status: {e}", exc_info=True)
            raise

class HotspotServerRepository:
    """Data access layer for HotspotServer operations"""
    
    def __init__(self):
        self.model = HotspotServer
    
    def get_by_router(self, router_id: UUID, organization_id: UUID) -> List[HotspotServer]:
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