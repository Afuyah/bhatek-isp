# app/modules/router/service.py
from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime
import secrets
import re

from flask import current_app

from app.modules.router.repository import RouterRepository, HotspotServerRepository, PPPoeServerRepository
from app.models.router import Router, HotspotServer, PPPoeServer
from app.models.nas import NAS
from app.models.organization import Organization

from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError
from app.integrations.mikrotik.client import MikroTikClient

from app.core.database.session import db


class RouterService:
    """Service for router management with multiple discovery methods"""

    def __init__(self):
        self.repository = RouterRepository()
        self.hotspot_repo = HotspotServerRepository()
        self.pppoe_repo = PPPoeServerRepository()
        self.encryption = EncryptionService()
        self.mikrotik_client = MikroTikClient()

    # ==========================================================================
    # HELPER METHODS
    # ==========================================================================
    
    def _generate_radius_secret(self) -> str:
        """Generate a strong unique RADIUS shared secret"""
        return secrets.token_urlsafe(32)
    
    def _parse_uptime(self, uptime_str: str) -> int:
        """Parse MikroTik uptime string to seconds"""
        seconds = 0
        
        if not uptime_str:
            return 0
        
        # Pattern: "1w2d3h4m5s" or "2d3h4m5s" or "3h4m5s" etc.
        weeks = re.search(r'(\d+)w', uptime_str)
        days = re.search(r'(\d+)d', uptime_str)
        hours = re.search(r'(\d+)h', uptime_str)
        minutes = re.search(r'(\d+)m', uptime_str)
        secs = re.search(r'(\d+)s', uptime_str)
        
        if weeks:
            seconds += int(weeks.group(1)) * 7 * 24 * 3600
        if days:
            seconds += int(days.group(1)) * 24 * 3600
        if hours:
            seconds += int(hours.group(1)) * 3600
        if minutes:
            seconds += int(minutes.group(1)) * 60
        if secs:
            seconds += int(secs.group(1))
        
        return seconds
    
    def _create_nas_entry(self, router: Router, radius_secret: str) -> NAS:
        """Create a NAS entry for FreeRADIUS"""
        try:
            nas_entry = NAS(
                organization_id=router.organization_id,
                nasname='0.0.0.0',  # Matches any IP (for dynamic routers)
                shortname=router.name,
                type='mikrotik',
                secret=radius_secret,
                description=f"Auto-created for router {router.name}",
                router_id=router.id,
                is_active=True
            )
            db.session.add(nas_entry)
            db.session.flush()
            
            # Link NAS entry to router
            router.nas_entry_id = nas_entry.id
            db.session.commit()
            
            logger.info(f"Created NAS entry for router {router.name}")
            return nas_entry
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create NAS entry: {e}")
            raise BusinessError(f"Failed to create NAS entry: {str(e)}")
    
    def _store_org_slug_on_router(self, router: Router, organization: Organization) -> Dict[str, Any]:
        """Store organization slug on the MikroTik router"""
        results = {
            'comment_set': False,
            'dns_name_set': False,
            'variable_set': False,
            'hotspot_address_set': False
        }
        
        router_data = {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port
        }
        
        # Method 1: Store in router's comment
        try:
            self.mikrotik_client.execute(
                router_data=router_data,
                command='/system/comment/set',
                comment=f"org_slug:{organization.slug}"
            )
            results['comment_set'] = True
            logger.info(f"Set router comment with org_slug: {organization.slug}")
        except Exception as e:
            logger.warning(f"Failed to set router comment: {e}")
        
        # Method 2: Set in hotspot profile dns-name
        try:
            dns_name = f"{organization.slug}.hotspot.local"
            self.mikrotik_client.execute(
                router_data=router_data,
                command='/ip/hotspot/profile/set',
                **{'dns-name': dns_name}
            )
            results['dns_name_set'] = True
            logger.info(f"Set hotspot dns-name: {dns_name}")
        except Exception as e:
            logger.warning(f"Failed to set hotspot dns-name: {e}")
        
        return results
    
    def _auto_configure_router_radius(self, router: Router, radius_secret: str, organization: Organization = None) -> Dict[str, Any]:
        """Auto-configure RADIUS on MikroTik router"""
        try:
            radius_server_ip = current_app.config.get('RADIUS_SERVER_IP', '163.245.217.16')
            
            router_data = {
                'id': str(router.id),
                'ip_address': str(router.ip_address),
                'username': router.username,
                'password_encrypted': router.password_encrypted,
                'api_port': router.api_port
            }
            
            # Store organization slug on router
            if organization:
                self._store_org_slug_on_router(router, organization)
            
            # Configure RADIUS on MikroTik
            result = self.mikrotik_client.configure_radius(
                router_data=router_data,
                radius_server=radius_server_ip,
                radius_secret=radius_secret,
                radius_port=1812,
                radius_acct_port=1813
            )
            
            if result.get('success'):
                logger.info(f"Auto-configured RADIUS on router {router.name}")
                
                # Enable hotspot RADIUS
                try:
                    self.mikrotik_client.execute(
                        router_data=router_data,
                        command='/ip/hotspot/set',
                        radius='yes'
                    )
                except Exception as e:
                    logger.warning(f"Failed to enable hotspot RADIUS: {e}")
                
                # Enable PPPoE RADIUS
                try:
                    self.mikrotik_client.execute(
                        router_data=router_data,
                        command='/ppp/set',
                        **{'use-radius': 'yes'}
                    )
                except Exception as e:
                    logger.warning(f"Failed to enable PPPoE RADIUS: {e}")
                
                return {'success': True, 'message': 'RADIUS configured automatically'}
            
            return {'success': False, 'error': result.get('error', 'Unknown error')}
            
        except Exception as e:
            logger.error(f"Auto-configuration error for router {router.name}: {e}")
            return {'success': False, 'error': str(e)}

    # ==========================================================================
    # CREATE OPERATIONS
    # ==========================================================================
        
    def create_router(self, organization_id: UUID, network_id: UUID, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new router with automatic RADIUS configuration"""
        required = ['name', 'ip_address', 'username', 'password']
        for field in required:
            if not data.get(field):
                raise ValidationError(f"{field} is required")

        organization = Organization.query.get(organization_id)
        if not organization:
            raise ValidationError("Organization not found")

        radius_secret = self._generate_radius_secret()
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
            'status': 'pending',
            'radius_secret': radius_secret,
            'radius_config_status': 'pending',
            'auto_config_attempts': 0
        }

        router = self.repository.create(router_data)
        nas_entry = self._create_nas_entry(router, radius_secret)
        
        auto_configured = False
        config_error = None
        
        try:
            test_result = self.test_connection(router.id, organization_id)
            
            if test_result.get('success'):
                config_result = self._auto_configure_router_radius(router, radius_secret, organization)
                
                if config_result.get('success'):
                    auto_configured = True
                    self.repository.update_radius_config_status(
                        router.id, organization_id, 'configured'
                    )
                    router.radius_config_status = 'configured'
                    router.radius_configured_at = datetime.utcnow()
                    router.status = 'online'
                    db.session.commit()
                else:
                    config_error = config_result.get('error')
                    self.repository.update_radius_config_status(
                        router.id, organization_id, 'failed', error=config_error
                    )
                    router.radius_config_status = 'failed'
                    router.status = 'radius_pending'
                    db.session.commit()
            else:
                config_error = test_result.get('error', 'Connection test failed')
                router.status = 'offline'
                db.session.commit()
                
        except Exception as e:
            config_error = str(e)
            logger.error(f"Auto-configuration failed for router {router.name}: {e}")
            self.repository.update_radius_config_status(
                router.id, organization_id, 'failed', error=config_error
            )
            router.radius_config_status = 'failed'
            db.session.commit()
        
        response = {
            'router': router,
            'auto_configured': auto_configured,
            'radius_secret': radius_secret,
            'radius_server_ip': current_app.config.get('RADIUS_SERVER_IP', '163.245.217.16'),
            'organization_slug': organization.slug,
            'organization_id': str(organization_id),
            'organization_name': organization.name
        }
        
        if not auto_configured:
            response['manual_config_instructions'] = {
                'command': f'/radius add address={current_app.config.get("RADIUS_SERVER_IP", "163.245.217.16")} secret={radius_secret} service=hotspot,ppp',
                'additional_commands': [
                    '/ip hotspot set radius=yes',
                    '/ppp set use-radius=yes',
                    f'/system comment set comment="org_slug:{organization.slug}"',
                    f'/ip hotspot profile set default dns-name="{organization.slug}.hotspot.local"'
                ],
                'message': 'Please run these commands on your MikroTik router to complete RADIUS configuration'
            }
            if config_error:
                response['error'] = config_error
        
        logger.info(f"Router created: {router.name} (Auto-configured: {auto_configured}) | Org: {organization.slug}")
        return response

    # ==========================================================================
    # READ OPERATIONS
    # ==========================================================================
        
    def get_router(self, router_id: UUID, organization_id: UUID) -> Router:
        router = self.repository.get_by_id(router_id, organization_id)
        if not router:
            raise NotFoundError("Router not found")
        return router

    def get_routers_by_organization(self, organization_id: UUID, skip: int = 0, 
                                     limit: int = 100, status: str = None,
                                     network_id: UUID = None,
                                     radius_config_status: str = None) -> List[Router]:
        return self.repository.get_by_organization(
            organization_id, skip, limit, status, network_id, radius_config_status
        )

    def get_routers_by_network(self, network_id: UUID, organization_id: UUID) -> List[Router]:
        return self.repository.get_by_network(network_id, organization_id)
    
    def get_routers_pending_radius_config(self, organization_id: UUID) -> List[Router]:
        return self.repository.get_routers_pending_radius_config(organization_id)
    
    def get_router_by_ip(self, ip_address: str, organization_id: UUID) -> Optional[Router]:
        return self.repository.get_by_ip(ip_address, organization_id)

    # ==========================================================================
    # UPDATE OPERATIONS
    # ==========================================================================
        
    def update_router(self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Router:
        if "password" in data and data["password"]:
            data["password_encrypted"] = self.encryption.encrypt(data.pop("password"))
        elif "password" in data:
            data.pop("password")

        router = self.repository.update(router_id, organization_id, data)
        if not router:
            raise NotFoundError("Router not found")

        logger.info(f"Router updated: {router_id}")
        return router
    
    def retry_radius_configuration(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)
        
        if not router.radius_secret:
            raise BusinessError("Router has no RADIUS secret configured")
        
        config_result = self._auto_configure_router_radius(router, router.radius_secret, organization)
        
        if config_result.get('success'):
            self.repository.update_radius_config_status(router.id, organization_id, 'configured')
            return {
                'success': True,
                'message': 'RADIUS configured successfully',
                'radius_secret': router.radius_secret,
                'organization_slug': organization.slug if organization else None
            }
        else:
            error_msg = config_result.get('error', 'Unknown error')
            self.repository.update_radius_config_status(router.id, organization_id, 'failed', error=error_msg)
            return {
                'success': False,
                'message': f'RADIUS configuration failed: {error_msg}',
                'manual_config_instructions': {
                    'command': f'/radius add address={current_app.config.get("RADIUS_SERVER_IP", "163.245.217.16")} secret={router.radius_secret} service=hotspot,ppp',
                    'additional_commands': [
                        '/ip hotspot set radius=yes',
                        '/ppp set use-radius=yes',
                        f'/system comment set comment="org_slug:{organization.slug}"' if organization else ''
                    ]
                }
            }

    # ==========================================================================
    # DELETE OPERATIONS
    # ==========================================================================
        
    def delete_router(self, router_id: UUID, organization_id: UUID, soft_delete: bool = True):
        router = self.repository.get_by_id(router_id, organization_id, include_inactive=True)
        if not router:
            raise NotFoundError("Router not found")

        if not soft_delete:
            hotspot_count = self.hotspot_repo.get_by_router(router_id, organization_id).count()
            pppoe_count = self.pppoe_repo.get_by_router(router_id, organization_id).count()
            
            if hotspot_count > 0 or pppoe_count > 0:
                raise BusinessError("Cannot delete router with active services. Remove services first or use soft delete.")

        self.repository.delete(router_id, organization_id, soft_delete)
        logger.info(f"Router {'deactivated' if soft_delete else 'deleted'}: {router_id}")

    # ==========================================================================
    # MULTIPLE DISCOVERY METHODS (CORRECTED)
    # ==========================================================================
    
    def discover_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """
        Auto-discover router capabilities using multiple methods.
        Methods attempted in order: API, SSH, SNMP, Telnet
        """
        router = self.get_router(router_id, organization_id)
        
        router_data = {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port
        }
        
        discovery_methods = [
            ('api', self._discover_via_api),
            ('ssh', self._discover_via_ssh),
            ('snmp', self._discover_via_snmp),
            ('telnet', self._discover_via_telnet),
        ]
        
        results = []
        
        for method_name, method_func in discovery_methods:
            try:
                logger.info(f"Attempting discovery via {method_name} for router {router.name}")
                result = method_func(router, router_data)
                
                if result.get('success'):
                    # Update router with discovered info
                    update_data = {
                        'model': result.get('model'),
                        'routeros_version': result.get('version'),
                        'serial_number': result.get('serial_number'),
                        'status': 'online',
                        'last_seen_at': datetime.utcnow()
                    }
                    
                    # Update capabilities if returned
                    if result.get('capabilities'):
                        update_data['capabilities'] = result.get('capabilities')
                    
                    self.repository.update(router_id, organization_id, update_data)
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
        self.repository.update_status(router_id, organization_id, 'offline')
        
        return {
            'success': False,
            'message': 'Auto-discovery failed. Router added in offline mode.',
            'attempts': results
        }
    
    def _discover_via_api(self, router: Router, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """Discover via MikroTik API"""
        try:
            # Get router system info
            info = self.mikrotik_client.get_router_info(router_data)
            
            # Detect capabilities
            capabilities = ['api']
            
            # Check for hotspot
            try:
                hotspot_result = self.mikrotik_client.execute(router_data, '/ip/hotspot/print')
                if hotspot_result and len(hotspot_result) > 0:
                    capabilities.append('hotspot')
            except:
                pass
            
            # Check for PPPoE
            try:
                pppoe_result = self.mikrotik_client.execute(router_data, '/interface/pppoe-server/server/print')
                if pppoe_result and len(pppoe_result) > 0:
                    capabilities.append('pppoe')
            except:
                pass
            
            # Check for wireless
            try:
                wireless_result = self.mikrotik_client.execute(router_data, '/interface/wireless/print')
                if wireless_result and len(wireless_result) > 0:
                    capabilities.append('wireless')
            except:
                pass
            
            return {
                'success': True,
                'model': info.get('board_name'),
                'version': info.get('version'),
                'serial_number': None,
                'capabilities': capabilities,
                'uptime': info.get('uptime'),
                'cpu_load': info.get('cpu_load'),
                'free_memory': info.get('free_memory'),
                'total_memory': info.get('total_memory')
            }
        except Exception as e:
            raise Exception(f"API discovery failed: {str(e)}")
    
    def _discover_via_ssh(self, router: Router, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """Discover via SSH (requires paramiko library)"""
        try:
            import paramiko
            from scp import SCPClient
            
            password = self.encryption.decrypt(router.password_encrypted)
            
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port or 22,
                timeout=10
            )
            
            # Get system resource info
            stdin, stdout, stderr = ssh.exec_command('/system resource print')
            output = stdout.read().decode()
            
            # Parse output
            model = None
            version = None
            
            for line in output.split('\n'):
                if 'board-name:' in line:
                    model = line.split(':')[1].strip()
                elif 'version:' in line:
                    version = line.split(':')[1].strip()
            
            ssh.close()
            
            return {
                'success': True,
                'model': model,
                'version': version,
                'capabilities': ['ssh', 'api'],
                'discovered_via': 'ssh'
            }
        except ImportError:
            raise Exception("SSH discovery requires paramiko library")
        except Exception as e:
            raise Exception(f"SSH discovery failed: {str(e)}")
    
    def _discover_via_snmp(self, router: Router, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """Discover via SNMP (requires pysnmp library)"""
        try:
            from pysnmp.hlapi import getCmd, CommunityData, UdpTransportTarget, ObjectIdentity, ObjectType
            
            # Default SNMP community (router should have SNMP enabled)
            community = 'public'
            
            # OIDs for MikroTik
            oids = {
                'model': '1.3.6.1.4.1.14988.1.1.1.1.0',  # sysDescr
                'version': '1.3.6.1.4.1.14988.1.1.1.2.0',  # sysVersion
                'serial': '1.3.6.1.4.1.14988.1.1.1.3.0'    # sysSerial
            }
            
            result = {}
            
            for key, oid in oids.items():
                errorIndication, errorStatus, errorIndex, varBinds = next(
                    getCmd(
                        CommunityData(community),
                        UdpTransportTarget((str(router.ip_address), 161)),
                        0,
                        ObjectType(ObjectIdentity(oid))
                    )
                )
                
                if not errorIndication and not errorStatus:
                    for varBind in varBinds:
                        result[key] = str(varBind[1])
            
            if not result:
                raise Exception("No SNMP response")
            
            return {
                'success': True,
                'model': result.get('model'),
                'version': result.get('version'),
                'serial_number': result.get('serial'),
                'capabilities': ['snmp'],
                'discovered_via': 'snmp'
            }
        except ImportError:
            raise Exception("SNMP discovery requires pysnmp library")
        except Exception as e:
            raise Exception(f"SNMP discovery failed: {str(e)}")
    
    def _discover_via_telnet(self, router: Router, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """Discover via Telnet (fallback, not recommended for production)"""
        try:
            import telnetlib
            
            password = self.encryption.decrypt(router.password_encrypted)
            
            tn = telnetlib.Telnet(str(router.ip_address), port=23, timeout=10)
            tn.read_until(b"Login: ")
            tn.write(router.username.encode('ascii') + b"\n")
            tn.read_until(b"Password: ")
            tn.write(password.encode('ascii') + b"\n")
            
            tn.write(b"/system resource print\n")
            tn.write(b"quit\n")
            
            output = tn.read_all().decode()
            tn.close()
            
            # Parse output
            model = None
            version = None
            
            for line in output.split('\n'):
                if 'board-name:' in line:
                    model = line.split(':')[1].strip()
                elif 'version:' in line:
                    version = line.split(':')[1].strip()
            
            return {
                'success': True,
                'model': model,
                'version': version,
                'capabilities': ['telnet'],
                'discovered_via': 'telnet'
            }
        except Exception as e:
            raise Exception(f"Telnet discovery failed: {str(e)}")

    # ==========================================================================
    # CONNECTION TEST
    # ==========================================================================
        
    def test_connection(self, router_id: UUID, organization_id: UUID, method: str = 'api') -> Dict[str, Any]:
        """Test connection to router using specified method"""
        router = self.get_router(router_id, organization_id)
        
        router_data = {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port
        }

        try:
            if method == 'api':
                # Decrypt password for testing
                password = self.encryption.decrypt(router.password_encrypted)
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
                self.discover_router(router_id, organization_id)

            return result

        except Exception as e:
            self.repository.update_status(router_id, organization_id, "error", error_message=str(e))
            raise BusinessError(f"Connection test failed: {str(e)}")

    # ==========================================================================
    # HEALTH MONITORING (CORRECTED)
    # ==========================================================================
        
    def update_health(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Update router health metrics (CPU, memory, uptime)"""
        router = self.get_router(router_id, organization_id)
        
        router_data = {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port
        }

        try:
            # Get router system info
            info = self.mikrotik_client.get_router_info(router_data)
            
            # Parse uptime
            uptime_str = info.get('uptime', '0s')
            uptime_seconds = self._parse_uptime(uptime_str)
            
            # Get CPU load
            cpu_load = info.get('cpu_load')
            if cpu_load:
                try:
                    cpu_load = int(cpu_load)
                except:
                    cpu_load = 0
            else:
                cpu_load = 0
            
            # Calculate memory usage
            free_memory = info.get('free_memory')
            total_memory = info.get('total_memory')
            memory_usage = 0
            if free_memory and total_memory:
                try:
                    free = int(free_memory)
                    total = int(total_memory)
                    memory_usage = int(((total - free) / total) * 100)
                except:
                    pass
            
            # Update repository (using the correct method names)
            self.repository.update_health(
                router_id=router_id,
                organization_id=organization_id,
                cpu_usage=cpu_load,
                memory_usage=memory_usage,
                uptime_seconds=uptime_seconds
            )
            
            # Update last seen and status
            self.repository.update_status(router_id, organization_id, 'online')
            
            return {
                'cpu_load': cpu_load,
                'memory_usage': memory_usage,
                'uptime_seconds': uptime_seconds,
                'uptime_hours': round(uptime_seconds / 3600, 2),
                'version': info.get('version'),
                'board_name': info.get('board_name'),
                'free_memory': free_memory,
                'total_memory': total_memory
            }

        except Exception as e:
            self.repository.update_status(router_id, organization_id, "error", error_message=str(e))
            raise BusinessError(f"Health check failed: {str(e)}")

    # ==========================================================================
    # SYNC (PULL FROM ROUTER)
    # ==========================================================================
        
    def sync_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Full sync of router configuration"""
        router = self.get_router(router_id, organization_id)
        
        router_data = {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port
        }

        results = {
            'success': True,
            'hotspot_synced': 0,
            'pppoe_synced': 0,
            'errors': []
        }

        try:
            # Sync hotspot servers
            hotspot_count = self._sync_hotspot_servers(router, router_data)
            results['hotspot_synced'] = hotspot_count

            # Sync PPPoE servers
            pppoe_count = self._sync_pppoe_servers(router, router_data)
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

    def _sync_hotspot_servers(self, router: Router, router_data: Dict[str, Any]) -> int:
        """Sync hotspot servers from router to database"""
        try:
            hotspot_servers = self.mikrotik_client.get_hotspot_servers(router_data)

            count = 0
            for hs in hotspot_servers:
                existing = self.hotspot_repo.get_by_router_and_hotspot_id(
                    router.id, router.organization_id, hs.get("name")
                )

                if existing:
                    self.hotspot_repo.update(existing.id, router.organization_id, {
                        'interface': hs.get("interface"),
                        'is_active': hs.get("disabled") != "true"
                    })
                else:
                    self.hotspot_repo.create({
                        'organization_id': router.organization_id,
                        'router_id': router.id,
                        'name': hs.get("name"),
                        'hotspot_id': hs.get("name"),
                        'interface': hs.get("interface"),
                        'is_active': hs.get("disabled") != "true"
                    })
                count += 1

            return count

        except Exception as e:
            logger.error(f"Failed to sync hotspot servers for router {router.id}: {e}")
            return 0

    def _sync_pppoe_servers(self, router: Router, router_data: Dict[str, Any]) -> int:
        """Sync PPPoE servers from router to database"""
        try:
            pppoe_servers = self.mikrotik_client.get_pppoe_servers(router_data)

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

    # ==========================================================================
    # MANUAL RADIUS CONFIGURATION
    # ==========================================================================
        
    def configure_radius_manual(self, router_id: UUID, organization_id: UUID, 
                                radius_server: str, radius_secret: str) -> Dict[str, Any]:
        """Manually configure RADIUS on a router"""
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)

        try:
            router_data = {
                'id': str(router.id),
                'ip_address': str(router.ip_address),
                'username': router.username,
                'password_encrypted': router.password_encrypted,
                'api_port': router.api_port
            }
            
            result = self.mikrotik_client.configure_radius(
                router_data=router_data,
                radius_server=radius_server,
                radius_secret=radius_secret,
                radius_port=1812,
                radius_acct_port=1813
            )

            if result.get('success'):
                self.repository.update_radius_config_status(router_id, organization_id, 'configured')
                
                if organization:
                    self._store_org_slug_on_router(router, organization)
                
                settings = router.settings or {}
                settings['radius_configured'] = True
                settings['radius_server'] = radius_server
                self.repository.update(router_id, organization_id, {'settings': settings})

            return result

        except Exception as e:
            raise BusinessError(f"RADIUS configuration failed: {str(e)}")

    # ==========================================================================
    # UTILITY
    # ==========================================================================
        
    def get_connection_status(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Get current connection status and health summary"""
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)
        
        return {
            'router_id': str(router.id),
            'name': router.name,
            'status': router.status,
            'radius_config_status': router.radius_config_status,
            'last_seen_at': router.last_seen_at.isoformat() if router.last_seen_at else None,
            'cpu_usage': getattr(router, 'cpu_usage', None),
            'memory_usage': getattr(router, 'memory_usage', None),
            'uptime_seconds': getattr(router, 'uptime_seconds', None),
            'is_active': router.is_active,
            'has_error': bool(getattr(router, 'last_error', None)),
            'organization_slug': organization.slug if organization else None
        }