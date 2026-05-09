from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime

from flask import current_app

from app.modules.router.repository import RouterRepository, HotspotServerRepository, PPPoeServerRepository
from app.models.router import Router, HotspotServer, PPPoeServer

from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError
from app.integrations.mikrotik.client import MikroTikClient

from app.core.database.session import db


class RouterService:
    """Service for router management with multi-method discovery and RADIUS support"""

    def __init__(self):
        self.repository = RouterRepository()
        self.hotspot_repo = HotspotServerRepository()
        self.pppoe_repo = PPPoeServerRepository()
        self.encryption = EncryptionService()
        self.mikrotik_client = MikroTikClient()

    # CREATE
        
    def create_router(self, organization_id: UUID, network_id: UUID, data: Dict[str, Any]) -> Router:
        """
        Create a new router with encrypted credentials.
        Can be added offline or with immediate connection test.
        """
        # Validate required fields
        required = ['name', 'ip_address', 'username', 'password']
        for field in required:
            if not data.get(field):
                raise ValidationError(f"{field} is required")

        # Encrypt password
        encrypted_password = self.encryption.encrypt(data['password'])

        router_data = {
            'organization_id': organization_id,
            'network_id': network_id,
            'name': data['name'],
            'model': data.get('model'),
            'ip_address': data['ip_address'],
            'api_port': data.get('api_port', 8728),
            'username': data['username'],
            'password_encrypted': encrypted_password,
            'location': data.get('location'),
            'description': data.get('description'),
            'is_active': data.get('is_active', True),
            'status': 'unknown'
        }

        router = self.repository.create(router_data)

        # Attempt auto-discovery in background (non-blocking)
        # For immediate feedback, caller should call discover() separately

        logger.info(f"Router created: {router.name} (ID: {router.id})")
        return router

    # READ
        
    def get_router(self, router_id: UUID, organization_id: UUID) -> Router:
        """Get router by ID with tenant isolation"""
        router = self.repository.get_by_id(router_id, organization_id)
        if not router:
            raise NotFoundError("Router not found")
        return router

    def get_routers_by_organization(self, organization_id: UUID, skip: int = 0, 
                                     limit: int = 100, status: str = None,
                                     network_id: UUID = None) -> List[Router]:
        """Get all routers for an organization with filters"""
        return self.repository.get_by_organization(organization_id, skip, limit, status, network_id)

    def get_routers_by_network(self, network_id: UUID, organization_id: UUID) -> List[Router]:
        """Get all routers in a specific network"""
        return self.repository.get_by_network(network_id, organization_id)

    # UPDATE
        
    def update_router(self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Router:
        """Update router information"""
        # Handle password separately (encrypt before update)
        if "password" in data and data["password"]:
            data["password_encrypted"] = self.encryption.encrypt(data.pop("password"))
        elif "password" in data:
            data.pop("password")  # Remove empty password

        router = self.repository.update(router_id, organization_id, data)
        if not router:
            raise NotFoundError("Router not found")

        logger.info(f"Router updated: {router_id}")
        return router

    # DELETE
        
    def delete_router(self, router_id: UUID, organization_id: UUID, soft_delete: bool = True):
        """Soft or hard delete a router"""
        router = self.repository.get_by_id(router_id, organization_id, include_inactive=True)
        if not router:
            raise NotFoundError("Router not found")

        # Check if router has active hotspot or PPPoE servers
        if not soft_delete:
            hotspot_count = self.hotspot_repo.get_by_router(router_id, organization_id).count()
            pppoe_count = self.pppoe_repo.get_by_router(router_id, organization_id).count()
            
            if hotspot_count > 0 or pppoe_count > 0:
                raise BusinessError("Cannot delete router with active services. Remove services first or use soft delete.")

        self.repository.delete(router_id, organization_id, soft_delete)
        logger.info(f"Router {'deactivated' if soft_delete else 'deleted'}: {router_id}")

    # DISCOVERY & CONNECTION (MULTI-METHOD)
        
    def discover_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """
        Auto-discover router capabilities using multiple methods.
        Priority: API > SSH > Telnet > Manual fallback
        """
        router = self.get_router(router_id, organization_id)
        password = self.encryption.decrypt(router.password_encrypted)
        
        discovery_methods = [
            ('api', self._discover_via_api),
            ('ssh', self._discover_via_ssh),
        ]
        
        results = []
        
        for method_name, method_func in discovery_methods:
            try:
                logger.info(f"Attempting discovery via {method_name} for router {router.name}")
                result = method_func(router, password)
                
                if result.get('success'):
                    # Update router with discovered info
                    self.repository.update_discovery(
                        router_id=router_id,
                        organization_id=organization_id,
                        model=result.get('model'),
                        routeros_version=result.get('version'),
                        serial_number=result.get('serial_number'),
                        capabilities=result.get('capabilities', []),
                        discovered_method=method_name
                    )
                    
                    # Update status to online
                    self.repository.update_status(router_id, organization_id, 'online')
                    
                    return {
                        'success': True,
                        'method': method_name,
                        'info': result,
                        'message': f'Router discovered via {method_name}'
                    }
                    
            except Exception as e:
                logger.warning(f"Discovery via {method_name} failed: {e}")
                results.append({'method': method_name, 'error': str(e)})
                continue
        
        # All discovery methods failed
        self.repository.update_status(router_id, organization_id, 'offline', 
                                      error_message="Auto-discovery failed. Router added in offline mode.")
        
        return {
            'success': False,
            'message': 'Auto-discovery failed. Router added in offline mode.',
            'attempts': results
        }
    
    def _discover_via_api(self, router: Router, password: str) -> Dict[str, Any]:
        """Discover via MikroTik REST API"""
        try:
            info = self.mikrotik_client.get_router_info(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port
            )
            
            # Test capabilities
            capabilities = ['api']
            if self.mikrotik_client.has_hotspot(router.ip_address, router.username, password, router.api_port):
                capabilities.append('hotspot')
            if self.mikrotik_client.has_pppoe(router.ip_address, router.username, password, router.api_port):
                capabilities.append('pppoe')
            if self.mikrotik_client.has_wireless(router.ip_address, router.username, password, router.api_port):
                capabilities.append('wireless')
            
            return {
                'success': True,
                'model': info.get('model'),
                'version': info.get('version'),
                'serial_number': info.get('serial_number'),
                'capabilities': capabilities
            }
        except Exception as e:
            raise Exception(f"API discovery failed: {e}")
    
    def _discover_via_ssh(self, router: Router, password: str) -> Dict[str, Any]:
        """Discover via SSH command (fallback)"""
        # Placeholder for SSH implementation
        # This would use paramiko to run '/system identity print' etc.
        raise Exception("SSH discovery not yet implemented")

    # CONNECTION TEST
        
    def test_connection(self, router_id: UUID, organization_id: UUID, 
                    method: str = 'api') -> Dict[str, Any]:
        """Test connection to router using specified method"""
        router = self.get_router(router_id, organization_id)
        password = self.encryption.decrypt(router.password_encrypted)

        try:
            if method == 'api':
                result = self.mikrotik_client.test_connection(
                    host=str(router.ip_address),
                    username=router.username,
                    password=password,
                    port=router.api_port
                )
            else:
                raise ValidationError(f"Unsupported connection method: {method}")

            # Update status based on result
            status = "online" if result.get("success") else "offline"
            self.repository.update_status(router_id, organization_id, status)
            
            # If successful, also try to discover capabilities
            if result.get("success"):
                # ✅ REMOVED 'await' - discover_router is not async
                self.discover_router(router_id, organization_id)

            return result

        except Exception as e:
            self.repository.update_status(router_id, organization_id, "error", error_message=str(e))
            raise BusinessError(f"Connection test failed: {str(e)}")

    # HEALTH MONITORING
        
    def update_health(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Update router health metrics (CPU, memory, uptime)"""
        router = self.get_router(router_id, organization_id)
        password = self.encryption.decrypt(router.password_encrypted)

        try:
            health = self.mikrotik_client.get_health_metrics(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port
            )

            self.repository.update_health(
                router_id=router_id,
                organization_id=organization_id,
                cpu_usage=health.get('cpu', 0),
                memory_usage=health.get('memory', 0),
                uptime_seconds=health.get('uptime', 0)
            )

            return health

        except Exception as e:
            self.repository.update_status(router_id, organization_id, "error", error_message=str(e))
            raise BusinessError(f"Health check failed: {str(e)}")

    # SYNC (PULL FROM ROUTER)
        
    def sync_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """
        Full sync of router configuration (hotspot servers, PPPoE servers, etc.)
        """
        router = self.get_router(router_id, organization_id)
        password = self.encryption.decrypt(router.password_encrypted)

        results = {
            'success': True,
            'hotspot_synced': 0,
            'pppoe_synced': 0,
            'errors': []
        }

        try:
            # Sync hotspot servers
            hotspot_count = self._sync_hotspot_servers(router, password)
            results['hotspot_synced'] = hotspot_count

            # Sync PPPoE servers
            pppoe_count = self._sync_pppoe_servers(router, password)
            results['pppoe_synced'] = pppoe_count

            # Update last sync timestamp
            self.repository.update(router_id, organization_id, {
                'last_sync_at': datetime.utcnow(),
                'status': 'online'
            })

            return results

        except Exception as e:
            self.repository.update_status(router_id, organization_id, "error", error_message=str(e))
            raise BusinessError(f"Sync failed: {str(e)}")

    def _sync_hotspot_servers(self, router: Router, password: str) -> int:
        """Sync hotspot servers from router to database"""
        try:
            hotspot_servers = self.mikrotik_client.get_hotspot_servers(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port
            )

            count = 0
            for hs in hotspot_servers:
                existing = self.hotspot_repo.get_by_router_and_hotspot_id(
                    router.id, router.organization_id, hs.get("name")
                )

                if existing:
                    # Update existing
                    self.hotspot_repo.update(existing.id, router.organization_id, {
                        'interface': hs.get("interface"),
                        'is_active': hs.get("disabled") != "true"
                    })
                else:
                    # Create new
                    self.hotspot_repo.create({
                        'organization_id': router.organization_id,
                        'router_id': router.id,
                        'name': hs.get("name"),
                        'interface': hs.get("interface"),
                        'is_active': hs.get("disabled") != "true"
                    })
                count += 1

            return count

        except Exception as e:
            logger.error(f"Failed to sync hotspot servers for router {router.id}: {e}")
            return 0

    def _sync_pppoe_servers(self, router: Router, password: str) -> int:
        """Sync PPPoE servers from router to database"""
        try:
            pppoe_servers = self.mikrotik_client.get_pppoe_servers(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port
            )

            count = 0
            for ps in pppoe_servers:
                existing = self.pppoe_repo.get_by_router_and_name(
                    router.id, router.organization_id, ps.get("name")
                )

                if existing:
                    self.pppoe_repo.update(existing.id, router.organization_id, {
                        'interface': ps.get("interface"),
                        'mtu': ps.get("mtu", 1492),
                        'is_active': ps.get("disabled") != "true"
                    })
                else:
                    self.pppoe_repo.create({
                        'organization_id': router.organization_id,
                        'router_id': router.id,
                        'name': ps.get("name"),
                        'interface': ps.get("interface"),
                        'mtu': ps.get("mtu", 1492),
                        'is_active': ps.get("disabled") != "true"
                    })
                count += 1

            return count

        except Exception as e:
            logger.error(f"Failed to sync PPPoE servers for router {router.id}: {e}")
            return 0

    # RADIUS CONFIGURATION
        
    def configure_radius(self, router_id: UUID, organization_id: UUID, 
                         radius_server: str, radius_secret: str) -> Dict[str, Any]:
        """
        Configure RADIUS settings on router (for hotspot/PPPoE authentication)
        """
        router = self.get_router(router_id, organization_id)
        password = self.encryption.decrypt(router.password_encrypted)

        try:
            result = self.mikrotik_client.configure_radius(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port,
                radius_server=radius_server,
                radius_secret=radius_secret
            )

            if result.get('success'):
                # Optionally store RADIUS config in organization settings
                logger.info(f"RADIUS configured on router {router.name}")
                
                # Update router settings to mark RADIUS as configured
                settings = router.settings or {}
                settings['radius_configured'] = True
                settings['radius_server'] = radius_server
                self.repository.update(router_id, organization_id, {'settings': settings})

            return result

        except Exception as e:
            raise BusinessError(f"RADIUS configuration failed: {str(e)}")

    # UTILITY
        
    def get_connection_status(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Get current connection status and health summary"""
        router = self.get_router(router_id, organization_id)
        
        return {
            'router_id': str(router.id),
            'name': router.name,
            'status': router.status,
            'last_seen_at': router.last_seen_at.isoformat() if router.last_seen_at else None,
            'cpu_usage': router.cpu_usage,
            'memory_usage': router.memory_usage,
            'uptime_seconds': router.uptime_seconds,
            'is_active': router.is_active,
            'has_error': bool(router.last_error)
        }