from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime
from sqlalchemy import and_, or_, desc
from sqlalchemy.exc import SQLAlchemyError

from app.models.router import Router, HotspotServer, PPPoeServer
from app.models.nas import NAS
from app.core.database.session import db
from app.core.logging.logger import logger

# ROUTER REPOSITORY
class RouterRepository:
   
    # Maximum length for error message storage
    MAX_ERROR_LENGTH = 500

    def __init__(self):
        self.model = Router
        self.nas_model = NAS
    # READ OPERATIONS
    def get_by_id(
        self,
        router_id: UUID,
        organization_id: UUID,
        include_inactive: bool = False
    ) -> Optional[Router]:
        """
        Get a single router by ID with tenant isolation.

        Args:
            router_id: Router UUID
            organization_id: Tenant organization UUID
            include_inactive: If True, also return deactivated routers

        Returns:
            Router instance or None

        Raises:
            SQLAlchemyError: On database failure
        """
        try:
            filters = [
                self.model.id == router_id,
                self.model.organization_id == organization_id,
            ]

            if not include_inactive:
                filters.append(self.model.is_active == True)

            return self.model.query.filter(and_(*filters)).first()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_id: router={router_id} "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_ip(
        self,
        ip_address: str,
        organization_id: UUID
    ) -> Optional[Router]:
        
        try:
            return self.model.query.filter(
                and_(
                    self.model.ip_address == ip_address,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                )
            ).first()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_ip: ip={ip_address} "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_radius_secret(
        self,
        radius_secret: str,
        organization_id: UUID
    ) -> Optional[Router]:
        
        try:
            return self.model.query.filter(
                and_(
                    self.model.radius_secret == radius_secret,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                )
            ).first()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_radius_secret: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_network(
        self,
        network_id: UUID,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100
    ) -> List[Router]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.network_id == network_id,
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                    )
                )
                .order_by(self.model.name)
                .offset(skip)
                .limit(limit)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_network: network={network_id} "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_organization(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: str = None,
        network_id: UUID = None,
        radius_config_status: str = None,
    ) -> List[Router]:
        
        try:
            filters = [
                self.model.organization_id == organization_id,
                self.model.is_active == True,
            ]

            if status:
                filters.append(self.model.status == status)

            if network_id:
                filters.append(self.model.network_id == network_id)

            if radius_config_status:
                filters.append(
                    self.model.radius_config_status == radius_config_status
                )

            return (
                self.model.query
                .filter(and_(*filters))
                .order_by(desc(self.model.created_at))
                .offset(skip)
                .limit(limit)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_organization: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_all_active(self, organization_id: UUID) -> List[Router]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                        self.model.status.in_(['online', 'unknown']),
                    )
                )
                .order_by(self.model.name)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_all_active: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_offline_routers(self, organization_id: UUID) -> List[Router]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                        self.model.status == 'offline',
                    )
                )
                .order_by(desc(self.model.last_seen_at))
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_offline_routers: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_routers_pending_radius_config(
        self,
        organization_id: UUID
    ) -> List[Router]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                        self.model.radius_config_status.in_(['pending', 'failed']),
                    )
                )
                .order_by(self.model.auto_config_attempts)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_routers_pending_radius_config: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_routers_with_issues(self, organization_id: UUID) -> List[Router]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                        or_(
                            self.model.status == 'offline',
                            self.model.status == 'error',
                            self.model.radius_config_status == 'failed',
                            self.model.auto_config_attempts > 3,
                        )
                    )
                )
                .order_by(desc(self.model.last_seen_at))
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_routers_with_issues: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise
    # CREATE OPERATIONS
    def create(self, data: Dict[str, Any]) -> Router:
        
        try:
            router = self.model(**data)
            db.session.add(router)
            db.session.commit()
            logger.info(
                f"Created router: {router.name} "
                f"(IP: {router.ip_address}, ID: {router.id})"
            )
            return router

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in create: router={data.get('name')} "
                f"ip={data.get('ip_address')} | {e}",
                exc_info=True
            )
            raise
    # UPDATE OPERATIONS
    def update(
        self,
        router_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any]
    ) -> Optional[Router]:
        
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                logger.warning(
                    f"Update failed: router {router_id} not found "
                    f"in org {organization_id}"
                )
                return None

            for key, value in data.items():
                if hasattr(router, key) and value is not None:
                    # Never overwrite password with empty string
                    if key == 'password_encrypted' and not value:
                        continue
                    setattr(router, key, value)

            db.session.commit()
            logger.info(f"Updated router: {router_id}")
            return router

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update: router={router_id} | {e}",
                exc_info=True
            )
            raise
    # RADIUS CONFIGURATION OPERATIONS
    def update_radius_config_status(
        self,
        router_id: UUID,
        organization_id: UUID,
        status: str,
        error: str = None
    ) -> bool:
        
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                logger.warning(
                    f"RADIUS status update failed: router {router_id} not found"
                )
                return False

            router.radius_config_status = status

            if status == 'configured':
                router.radius_configured_at = datetime.utcnow()
                router.auto_config_attempts = 0
                router.last_config_error = None
                logger.info(f"Router {router_id} RADIUS configured successfully")

            elif status == 'failed':
                router.auto_config_attempts = (router.auto_config_attempts or 0) + 1
                if error:
                    router.last_config_error = error[:self.MAX_ERROR_LENGTH]
                logger.warning(
                    f"Router {router_id} RADIUS config failed "
                    f"(attempt {router.auto_config_attempts}): {error}"
                )

            db.session.commit()
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update_radius_config_status: "
                f"router={router_id} | {e}",
                exc_info=True
            )
            raise

    def link_nas_entry(
        self,
        router_id: UUID,
        organization_id: UUID,
        nas_entry_id: UUID
    ) -> bool:
        
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                logger.warning(
                    f"NAS link failed: router {router_id} not found"
                )
                return False

            router.nas_entry_id = nas_entry_id
            db.session.commit()
            logger.info(f"Linked NAS {nas_entry_id} to router {router_id}")
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in link_nas_entry: "
                f"router={router_id} nas={nas_entry_id} | {e}",
                exc_info=True
            )
            raise
    # STATUS & HEALTH OPERATIONS
    def update_status(
        self,
        router_id: UUID,
        organization_id: UUID,
        status: str,
        error_message: str = None
    ) -> bool:
        
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False

            router.status = status
            router.last_seen_at = datetime.utcnow()

            if error_message:
                router.last_config_error = error_message[:self.MAX_ERROR_LENGTH]
                router.auto_config_attempts = (router.auto_config_attempts or 0) + 1
            else:
                # Clear error state on successful status update
                if status == 'online':
                    router.last_config_error = None
                    router.auto_config_attempts = 0

            db.session.commit()
            logger.debug(f"Router {router_id} status updated to '{status}'")
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update_status: "
                f"router={router_id} | {e}",
                exc_info=True
            )
            raise

    def update_health(
        self,
        router_id: UUID,
        organization_id: UUID,
        cpu_load: int = None,
        free_memory: int = None,
        total_memory: int = None,
        uptime: str = None
    ) -> bool:
        
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False

            # Store health metrics in settings JSON
            settings = router.settings or {}
            health_data = settings.get('health', {})

            if cpu_load is not None:
                health_data['cpu_load'] = cpu_load
            if free_memory is not None:
                health_data['free_memory'] = free_memory
            if total_memory is not None:
                health_data['total_memory'] = total_memory
            if uptime is not None:
                health_data['uptime'] = uptime

            health_data['last_checked_at'] = datetime.utcnow().isoformat()
            settings['health'] = health_data
            router.settings = settings

            # Update router status
            router.status = 'online'
            router.last_seen_at = datetime.utcnow()
            router.last_sync_at = datetime.utcnow()

            db.session.commit()
            logger.debug(
                f"Router {router_id} health updated: "
                f"CPU={cpu_load}%, Memory={free_memory}/{total_memory}"
            )
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update_health: "
                f"router={router_id} | {e}",
                exc_info=True
            )
            raise

    def update_discovery(
        self,
        router_id: UUID,
        organization_id: UUID,
        model: str = None,
        firmware_version: str = None,
        serial_number: str = None,
        capabilities: List[str] = None,
        discovery_method: str = 'api'
    ) -> bool:
        
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False

            if model:
                router.model = model
            if firmware_version:
                router.firmware_version = firmware_version

            # Store extended discovery data in settings JSON
            settings = router.settings or {}
            discovery = settings.get('discovery', {})

            if serial_number:
                discovery['serial_number'] = serial_number
            if capabilities:
                discovery['capabilities'] = capabilities

            discovery['method'] = discovery_method
            discovery['discovered_at'] = datetime.utcnow().isoformat()

            settings['discovery'] = discovery
            router.settings = settings

            router.last_sync_at = datetime.utcnow()

            db.session.commit()
            logger.info(
                f"Router {router_id} discovery updated: "
                f"model={model}, version={firmware_version}, "
                f"method={discovery_method}"
            )
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in update_discovery: "
                f"router={router_id} | {e}",
                exc_info=True
            )
            raise
    # DELETE OPERATIONS
    def delete(
        self,
        router_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True
    ) -> bool:
        
        try:
            router = self.get_by_id(router_id, organization_id, include_inactive=True)
            if not router:
                return False

            if soft_delete:
                router.is_active = False
                router.status = 'deactivated'

                # Deactivate linked NAS entry so FreeRADIUS rejects requests
                if router.nas_entry_id:
                    db.session.query(NAS).filter(
                        NAS.id == router.nas_entry_id
                    ).update({'is_active': False})

                logger.info(
                    f"Router {router_id} ({router.name}) deactivated"
                )
            else:
                # Hard delete — remove NAS entry first
                if router.nas_entry_id:
                    db.session.query(NAS).filter(
                        NAS.id == router.nas_entry_id
                    ).delete()

                db.session.delete(router)
                logger.info(
                    f"Router {router_id} ({router.name}) permanently deleted"
                )

            db.session.commit()
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in delete: router={router_id} | {e}",
                exc_info=True
            )
            raise
    # COUNT & STATISTICS
    def count_by_organization(
        self,
        organization_id: UUID,
        status: str = None,
        radius_config_status: str = None,
    ) -> int:
        
        try:
            filters = [
                self.model.organization_id == organization_id,
                self.model.is_active == True,
            ]

            if status:
                filters.append(self.model.status == status)
            if radius_config_status:
                filters.append(
                    self.model.radius_config_status == radius_config_status
                )

            return self.model.query.filter(and_(*filters)).count()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in count_by_organization: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def count_radius_configured(self, organization_id: UUID) -> int:
        """Count routers with successfully configured RADIUS."""
        return self.count_by_organization(
            organization_id, radius_config_status='configured'
        )

    def count_radius_pending(self, organization_id: UUID) -> int:
        """Count routers awaiting RADIUS configuration."""
        return self.count_by_organization(
            organization_id, radius_config_status='pending'
        )

    def count_radius_failed(self, organization_id: UUID) -> int:
        """Count routers with failed RADIUS configuration."""
        return self.count_by_organization(
            organization_id, radius_config_status='failed'
        )

# HOTSPOT SERVER REPOSITORY
class HotspotServerRepository:
    

    def __init__(self):
        self.model = HotspotServer
    # READ OPERATIONS
    def get_by_id(
        self,
        hotspot_id: UUID,
        organization_id: UUID
    ) -> Optional[HotspotServer]:
        
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == hotspot_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                )
            ).first()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in HotspotServer.get_by_id: "
                f"id={hotspot_id} org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_router(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> List[HotspotServer]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.router_id == router_id,
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                    )
                )
                .order_by(self.model.name)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in HotspotServer.get_by_router: "
                f"router={router_id} org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_organization(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100
    ) -> List[HotspotServer]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                    )
                )
                .order_by(desc(self.model.created_at))
                .offset(skip)
                .limit(limit)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in HotspotServer.get_by_organization: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_router_and_hotspot_id(
        self,
        router_id: UUID,
        organization_id: UUID,
        hotspot_id: str
    ) -> Optional[HotspotServer]:
        
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.hotspot_id == hotspot_id,
                )
            ).first()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in HotspotServer.get_by_router_and_hotspot_id: "
                f"router={router_id} hotspot_id={hotspot_id} | {e}",
                exc_info=True
            )
            raise
    # CREATE & UPDATE
    def create(self, data: Dict[str, Any]) -> HotspotServer:
        
        try:
            hotspot = self.model(**data)
            db.session.add(hotspot)
            db.session.commit()
            logger.info(
                f"Created hotspot server: {hotspot.name} "
                f"(ID: {hotspot.id})"
            )
            return hotspot

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in HotspotServer.create: "
                f"name={data.get('name')} | {e}",
                exc_info=True
            )
            raise

    def update(
        self,
        hotspot_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any]
    ) -> Optional[HotspotServer]:
        
        try:
            hotspot = self.get_by_id(hotspot_id, organization_id)
            if not hotspot:
                logger.warning(
                    f"HotspotServer update failed: {hotspot_id} not found"
                )
                return None

            for key, value in data.items():
                if hasattr(hotspot, key) and value is not None:
                    setattr(hotspot, key, value)

            db.session.commit()
            logger.info(f"Updated hotspot server: {hotspot_id}")
            return hotspot

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in HotspotServer.update: "
                f"id={hotspot_id} | {e}",
                exc_info=True
            )
            raise
    # DELETE
    def delete(
        self,
        hotspot_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True
    ) -> bool:
        
        try:
            hotspot = self.get_by_id(hotspot_id, organization_id)
            if not hotspot:
                return False

            if soft_delete:
                hotspot.is_active = False
            else:
                db.session.delete(hotspot)

            db.session.commit()
            logger.info(
                f"Hotspot server {hotspot_id} "
                f"{'deactivated' if soft_delete else 'deleted'}"
            )
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in HotspotServer.delete: "
                f"id={hotspot_id} | {e}",
                exc_info=True
            )
            raise

# PPPoE SERVER REPOSITORY
class PPPoeServerRepository:
    
    def __init__(self):
        self.model = PPPoeServer
    # READ OPERATIONS
    def get_by_id(
        self,
        pppoe_id: UUID,
        organization_id: UUID
    ) -> Optional[PPPoeServer]:
        
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == pppoe_id,
                    self.model.organization_id == organization_id,
                    self.model.is_active == True,
                )
            ).first()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in PPPoeServer.get_by_id: "
                f"id={pppoe_id} org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_router(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> List[PPPoeServer]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.router_id == router_id,
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                    )
                )
                .order_by(self.model.name)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in PPPoeServer.get_by_router: "
                f"router={router_id} org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_organization(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100
    ) -> List[PPPoeServer]:
        
        try:
            return (
                self.model.query
                .filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.is_active == True,
                    )
                )
                .order_by(desc(self.model.created_at))
                .offset(skip)
                .limit(limit)
                .all()
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in PPPoeServer.get_by_organization: "
                f"org={organization_id} | {e}",
                exc_info=True
            )
            raise

    def get_by_router_and_name(
        self,
        router_id: UUID,
        organization_id: UUID,
        name: str
    ) -> Optional[PPPoeServer]:
        
        try:
            return self.model.query.filter(
                and_(
                    self.model.router_id == router_id,
                    self.model.organization_id == organization_id,
                    self.model.name == name,
                )
            ).first()

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in PPPoeServer.get_by_router_and_name: "
                f"router={router_id} name={name} | {e}",
                exc_info=True
            )
            raise
    # CREATE & UPDATE
    def create(self, data: Dict[str, Any]) -> PPPoeServer:
        
        try:
            pppoe = self.model(**data)
            db.session.add(pppoe)
            db.session.commit()
            logger.info(
                f"Created PPPoE server: {pppoe.name} "
                f"(ID: {pppoe.id})"
            )
            return pppoe

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in PPPoeServer.create: "
                f"name={data.get('name')} | {e}",
                exc_info=True
            )
            raise

    def update(
        self,
        pppoe_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any]
    ) -> Optional[PPPoeServer]:
        
        try:
            pppoe = self.get_by_id(pppoe_id, organization_id)
            if not pppoe:
                logger.warning(
                    f"PPPoeServer update failed: {pppoe_id} not found"
                )
                return None

            for key, value in data.items():
                if hasattr(pppoe, key) and value is not None:
                    setattr(pppoe, key, value)

            db.session.commit()
            logger.info(f"Updated PPPoE server: {pppoe_id}")
            return pppoe

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in PPPoeServer.update: "
                f"id={pppoe_id} | {e}",
                exc_info=True
            )
            raise
    # DELETE
    def delete(
        self,
        pppoe_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True
    ) -> bool:
        
        try:
            pppoe = self.get_by_id(pppoe_id, organization_id)
            if not pppoe:
                return False

            if soft_delete:
                pppoe.is_active = False
            else:
                db.session.delete(pppoe)

            db.session.commit()
            logger.info(
                f"PPPoE server {pppoe_id} "
                f"{'deactivated' if soft_delete else 'deleted'}"
            )
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in PPPoeServer.delete: "
                f"id={pppoe_id} | {e}",
                exc_info=True
            )
            raise