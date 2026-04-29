from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, desc
from sqlalchemy.exc import SQLAlchemyError

from app.models.network import Network
from app.core.database.session import db
from app.core.logging.logger import logger

class NetworkRepository:
    def __init__(self):
        self.model = Network
    
    def _apply_tenant_filter(self, query, organization_id: UUID):
        """Apply organization isolation"""
        return query.filter(self.model.organization_id == organization_id)
    
    def get_by_id(self, network_id: UUID, organization_id: UUID) -> Optional[Network]:
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == network_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[Network]:
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
    
    def create(self, data: Dict[str, Any]) -> Network:
        try:
            network = self.model(**data)
            db.session.add(network)
            db.session.commit()
            logger.info(f"Created network: {network.name}")
            return network
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, network_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Network]:
        try:
            network = self.get_by_id(network_id, organization_id)
            if not network:
                return None
            
            for key, value in data.items():
                if hasattr(network, key) and value is not None:
                    setattr(network, key, value)
            
            db.session.commit()
            logger.info(f"Updated network: {network_id}")
            return network
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def delete(self, network_id: UUID, organization_id: UUID) -> bool:
        try:
            network = self.get_by_id(network_id, organization_id)
            if not network:
                return False
            
            network.is_active = False
            db.session.commit()
            logger.info(f"Deleted network: {network_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise