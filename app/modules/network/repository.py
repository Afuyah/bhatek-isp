from typing import Optional, List, Dict, Any
from uuid import UUID
from sqlalchemy import and_, or_, desc
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

from app.models.network import Network
from app.core.database.session import db
from app.core.logging.logger import logger


class NetworkRepository:
    """Data access layer"""
    
    def __init__(self):
        self.model = Network
    
    def get_by_id(self, network_id: UUID, organization_id: UUID) -> Optional[Network]:
        """Get network by ID with tenant isolation"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == network_id,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_slug(self, slug: str, organization_id: UUID) -> Optional[Network]:
        """Get network by slug within organization"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.slug == slug,
                    self.model.organization_id == organization_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_slug: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, 
                           limit: int = 100, filters: Dict = None) -> List[Network]:
        """Get all networks for an organization with pagination"""
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )
            
            if filters:
                if filters.get('type'):
                    query = query.filter(self.model.type == filters['type'])
                if filters.get('is_active') is not None:
                    query = query.filter(self.model.is_active == filters['is_active'])
                if filters.get('search'):
                    search = f"%{filters['search']}%"
                    query = query.filter(
                        or_(
                            self.model.name.ilike(search),
                            self.model.slug.ilike(search),
                            self.model.description.ilike(search)
                        )
                    )
            
            return query.order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def get_all_active(self, organization_id: UUID) -> List[Network]:
        """Get all active networks for an organization"""
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
    
    def create(self, data: Dict[str, Any]) -> Network:
        """Create new network"""
        try:
            network = self.model(**data)
            db.session.add(network)
            db.session.commit()
            logger.info(f"Created network: {network.name} (ID: {network.id})")
            return network
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, network_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Network]:
        """Update network"""
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
    
    def delete(self, network_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete network (soft delete by default)"""
        try:
            network = self.get_by_id(network_id, organization_id)
            if not network:
                return False
            
            # Check if network has associated routers
            if network.routers.count() > 0:
                logger.warning(f"Cannot delete network {network_id} with active routers")
                return False
            
            if soft_delete:
                network.is_active = False
            else:
                db.session.delete(network)
            
            db.session.commit()
            logger.info(f"Deleted network: {network_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise
    
    def count_by_organization(self, organization_id: UUID, is_active: bool = None) -> int:
        """Count networks in organization"""
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )
            if is_active is not None:
                query = query.filter(self.model.is_active == is_active)
            return query.count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_organization: {e}", exc_info=True)
            raise
    
    def get_network_stats(self, organization_id: UUID) -> Dict[str, Any]:
        """Get network statistics for organization"""
        try:
            total = self.model.query.filter(
                self.model.organization_id == organization_id
            ).count()
            
            active = self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).count()
            
            by_type = db.session.query(
                self.model.type,
                db.func.count(self.model.id).label('count')
            ).filter(
                self.model.organization_id == organization_id
            ).group_by(self.model.type).all()
            
            return {
                'total': total,
                'active': active,
                'inactive': total - active,
                'by_type': {item.type: item.count for item in by_type}
            }
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_network_stats: {e}", exc_info=True)
            raise
    
    def bulk_update_status(self, organization_id: UUID, network_ids: List[UUID], is_active: bool) -> int:
        """Bulk update network status"""
        try:
            updated = self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.id.in_(network_ids)
                )
            ).update({self.model.is_active: is_active}, synchronize_session=False)
            
            db.session.commit()
            logger.info(f"Bulk updated {updated} networks status to {is_active}")
            return updated
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in bulk_update_status: {e}", exc_info=True)
            raise