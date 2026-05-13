# app/modules/router/repository.py
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime
from sqlalchemy import and_, or_, desc, update
from sqlalchemy.exc import SQLAlchemyError

from app.models.router import Router, HotspotServer, PPPoeServer
from app.models.nas import NAS
from app.core.database.session import db
from app.core.logging.logger import logger


class RouterRepository:
    """Data access layer for Router operations with tenant isolation"""
    
    def __init__(self):
        self.model = Router
        self.nas_model = NAS
    
    # BASIC CRUD OPERATIONS
    
    def get_by_id(self, router_id: UUID, organization_id: UUID, include_inactive: bool = False) -> Optional[Router]:
        """Get router by ID with organization isolation"""
        try:
            filters = [
                self.model.id == router_id,
                self.model.organization_id == organization_id
            ]
            
            if not include_inactive:
                filters.append(self.model.is_active == True)
            
            return self.model.query.filter(and_(*filters)).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_ip(self, ip_address: str, organization_id: UUID) -> Optional[Router]:
        """Get router by IP address within organization"""
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
    
    def get_by_radius_secret(self, radius_secret: str, organization_id: UUID) -> Optional[Router]:
        """Get router by RADIUS shared secret"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.radius_secret == radius_secret,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_radius_secret: {e}", exc_info=True)
            raise
    
    def get_by_network(self, network_id: UUID, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[Router]:
        """Get all routers belonging to a specific network"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.network_id == network_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).order_by(self.model.name).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_network: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100, 
                           status: str = None, network_id: UUID = None,
                           radius_config_status: str = None) -> List[Router]:
        """Get all routers for an organization with optional filters"""
        try:
            filters = [
                self.model.organization_id == organization_id,
                self.model.is_active == True
            ]
            
            if status:
                filters.append(self.model.status == status)
            
            if network_id:
                filters.append(self.model.network_id == network_id)
            
            if radius_config_status:
                filters.append(self.model.radius_config_status == radius_config_status)
            
            return self.model.query.filter(
                and_(*filters)
            ).order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def get_all_active(self, organization_id: UUID) -> List[Router]:
        """Get all active routers for dropdown/selection"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.status == 'online'
                )
            ).order_by(self.model.name).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_all_active: {e}", exc_info=True)
            raise
    
    def get_offline_routers(self, organization_id: UUID) -> List[Router]:
        """Get all offline routers for monitoring"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.status == 'offline'
                )
            ).order_by(desc(self.model.last_seen_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_offline_routers: {e}", exc_info=True)
            raise
    
    def get_routers_pending_radius_config(self, organization_id: UUID) -> List[Router]:
        """Get routers that need RADIUS configuration"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    self.model.radius_config_status.in_(['pending', 'failed'])
                )
            ).order_by(self.model.auto_config_attempts).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_routers_pending_radius_config: {e}", exc_info=True)
            raise
    
    # CREATE OPERATIONS (with RADIUS fields)
    
    def create(self, data: Dict[str, Any]) -> Router:
        """Create a new router with RADIUS fields"""
        try:
            router = self.model(**data)
            db.session.add(router)
            db.session.commit()
            logger.info(f"Created router: {router.name} (IP: {router.ip_address})")
            return router
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    # UPDATE OPERATIONS (with RADIUS fields)
    
    def update(self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[Router]:
        """Update a router including RADIUS fields"""
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return None
            
            for key, value in data.items():
                if hasattr(router, key) and value is not None:
                    # Skip password if empty string
                    if key == 'password_encrypted' and not value:
                        continue
                    setattr(router, key, value)
            
            db.session.commit()
            logger.info(f"Updated router: {router_id}")
            return router
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def update_radius_config_status(self, router_id: UUID, organization_id: UUID, 
                                      status: str, error: str = None) -> bool:
        """Update RADIUS configuration status"""
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False
            
            router.radius_config_status = status
            
            if status == 'configured':
                router.radius_configured_at = datetime.utcnow()
                router.auto_config_attempts = 0
                router.last_config_error = None
            elif status == 'failed' and error:
                router.auto_config_attempts = (router.auto_config_attempts or 0) + 1
                router.last_config_error = error[:500]  # Truncate

            db.session.commit()
            logger.info(f"Router {router_id} RADIUS config status: {status}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_radius_config_status: {e}", exc_info=True)
            raise
    
    def link_nas_entry(self, router_id: UUID, organization_id: UUID, nas_entry_id: UUID) -> bool:
        """Link a NAS entry to this router"""
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False
            
            router.nas_entry_id = nas_entry_id
            db.session.commit()
            logger.info(f"Linked NAS entry {nas_entry_id} to router {router_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in link_nas_entry: {e}", exc_info=True)
            raise
    
    # STATUS & HEALTH OPERATIONS
    
    def update_status(self, router_id: UUID, organization_id: UUID, status: str, 
                      error_message: str = None) -> bool:
        """Update router status and timestamp"""
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False
            
            router.status = status
            router.last_seen_at = datetime.utcnow()
            
            if error_message:
                router.last_error = error_message[:500]  # Truncate to 500 chars
                router.sync_attempts = (router.sync_attempts or 0) + 1
            else:
                router.last_error = None
                router.sync_attempts = 0
            
            db.session.commit()
            logger.debug(f"Router {router_id} status updated to {status}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_status: {e}", exc_info=True)
            raise
    
    def update_health(self, router_id: UUID, organization_id: UUID, 
                      cpu_usage: int, memory_usage: int, uptime_seconds: int) -> bool:
        """Update router health metrics"""
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False
            
            router.cpu_usage = cpu_usage
            router.memory_usage = memory_usage
            router.uptime_seconds = uptime_seconds
            router.last_seen_at = datetime.utcnow()
            router.status = 'online'
            router.last_error = None
            
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_health: {e}", exc_info=True)
            raise
    
    def update_discovery(self, router_id: UUID, organization_id: UUID, 
                         model: str, routeros_version: str, serial_number: str,
                         capabilities: List[str], discovered_method: str) -> bool:
        """Update auto-discovered router information"""
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False
            
            if model:
                router.model = model
            if routeros_version:
                router.routeros_version = routeros_version
            if serial_number:
                router.serial_number = serial_number
            if capabilities:
                router.capabilities = capabilities
            router.discovered_method = discovered_method
            router.discovered_at = datetime.utcnow()
            router.last_sync_success = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"Router {router_id} discovery updated via {discovered_method}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_discovery: {e}", exc_info=True)
            raise
    
    # DELETE OPERATIONS
    
    def delete(self, router_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate a router"""
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False
            
            if soft_delete:
                router.is_active = False
                router.status = 'deactivated'
                # Also deactivate the NAS entry if exists
                if router.nas_entry_id:
                    db.session.query(NAS).filter(NAS.id == router.nas_entry_id).update(
                        {'is_active': False}
                    )
            else:
                # Hard delete - also delete associated NAS entry
                if router.nas_entry_id:
                    db.session.query(NAS).filter(NAS.id == router.nas_entry_id).delete()
                db.session.delete(router)
            
            db.session.commit()
            logger.info(f"Router {router_id} {'deactivated' if soft_delete else 'deleted'}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise
    
    # COUNT & STATISTICS
    
    def count_by_organization(self, organization_id: UUID, status: str = None,
                               radius_config_status: str = None) -> int:
        """Count routers in organization with optional filters"""
        try:
            filters = [
                self.model.organization_id == organization_id,
                self.model.is_active == True
            ]
            if status:
                filters.append(self.model.status == status)
            if radius_config_status:
                filters.append(self.model.radius_config_status == radius_config_status)
            
            return self.model.query.filter(and_(*filters)).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_organization: {e}", exc_info=True)
            raise
    
    def count_radius_configured(self, organization_id: UUID) -> int:
        """Count routers with RADIUS successfully configured"""
        return self.count_by_organization(organization_id, radius_config_status='configured')
    
    def count_radius_pending(self, organization_id: UUID) -> int:
        """Count routers pending RADIUS configuration"""
        return self.count_by_organization(organization_id, radius_config_status='pending')
    
    # ISSUE DETECTION
    
    def get_routers_with_issues(self, organization_id: UUID) -> List[Router]:
        """Get routers that need attention (offline or high error count)"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                    or_(
                        self.model.status == 'offline',
                        self.model.status == 'error',
                        self.model.sync_attempts > 3,
                        self.model.radius_config_status == 'failed'
                    )
                )
            ).order_by(desc(self.model.last_seen_at)).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_routers_with_issues: {e}", exc_info=True)
            raise


class HotspotServerRepository:
    """Data access layer for HotspotServer operations"""
    
    def __init__(self):
        self.model = HotspotServer
    
    def get_by_id(self, hotspot_id: UUID, organization_id: UUID) -> Optional[HotspotServer]:
        """Get hotspot server by ID with tenant isolation"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == hotspot_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_router(self, router_id: UUID, organization_id: UUID) -> List[HotspotServer]:
        """Get all hotspot servers on a specific router"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).order_by(self.model.name).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_router: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[HotspotServer]:
        """Get all hotspot servers for an organization"""
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
    
    def get_by_router_and_hotspot_id(self, router_id: UUID, organization_id: UUID, hotspot_id: str) -> Optional[HotspotServer]:
        """Get hotspot server by router ID and hotspot ID"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.hotspot_id == hotspot_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_router_and_hotspot_id: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> HotspotServer:
        """Create a new hotspot server"""
        try:
            hotspot = self.model(**data)
            db.session.add(hotspot)
            db.session.commit()
            logger.info(f"Created hotspot server: {hotspot.name}")
            return hotspot
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, hotspot_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[HotspotServer]:
        """Update a hotspot server"""
        try:
            hotspot = self.get_by_id(hotspot_id, organization_id)
            if not hotspot:
                return None
            
            for key, value in data.items():
                if hasattr(hotspot, key) and value is not None:
                    setattr(hotspot, key, value)
            
            db.session.commit()
            logger.info(f"Updated hotspot server: {hotspot_id}")
            return hotspot
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def delete(self, hotspot_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate a hotspot server"""
        try:
            hotspot = self.get_by_id(hotspot_id, organization_id)
            if not hotspot:
                return False
            
            if soft_delete:
                hotspot.is_active = False
            else:
                db.session.delete(hotspot)
            
            db.session.commit()
            logger.info(f"Hotspot server {hotspot_id} deleted")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise


class PPPoeServerRepository:
    """Data access layer for PPPoeServer operations"""
    
    def __init__(self):
        self.model = PPPoeServer
    
    def get_by_id(self, pppoe_id: UUID, organization_id: UUID) -> Optional[PPPoeServer]:
        """Get PPPoE server by ID with tenant isolation"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == pppoe_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_router(self, router_id: UUID, organization_id: UUID) -> List[PPPoeServer]:
        """Get all PPPoE servers on a specific router"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True
                )
            ).order_by(self.model.name).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_router: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[PPPoeServer]:
        """Get all PPPoE servers for an organization"""
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
    
    def get_by_router_and_name(self, router_id: UUID, organization_id: UUID, name: str) -> Optional[PPPoeServer]:
        """Get PPPoE server by router ID and name"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.name == name
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_router_and_name: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> PPPoeServer:
        """Create a new PPPoE server"""
        try:
            pppoe = self.model(**data)
            db.session.add(pppoe)
            db.session.commit()
            logger.info(f"Created PPPoE server: {pppoe.name}")
            return pppoe
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, pppoe_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Optional[PPPoeServer]:
        """Update a PPPoE server"""
        try:
            pppoe = self.get_by_id(pppoe_id, organization_id)
            if not pppoe:
                return None
            
            for key, value in data.items():
                if hasattr(pppoe, key) and value is not None:
                    setattr(pppoe, key, value)
            
            db.session.commit()
            logger.info(f"Updated PPPoE server: {pppoe_id}")
            return pppoe
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def delete(self, pppoe_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate a PPPoE server"""
        try:
            pppoe = self.get_by_id(pppoe_id, organization_id)
            if not pppoe:
                return False
            
            if soft_delete:
                pppoe.is_active = False
            else:
                db.session.delete(pppoe)
            
            db.session.commit()
            logger.info(f"PPPoE server {pppoe_id} deleted")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise