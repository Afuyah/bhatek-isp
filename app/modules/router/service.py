from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime
import secrets
import re

from flask import current_app

from app.modules.router.repository import (
    RouterRepository,
    HotspotServerRepository,
    PPPoeServerRepository,
)
from app.models.router import Router, HotspotServer, PPPoeServer
from app.models.nas import NAS
from app.models.organization import Organization

from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import (
    NotFoundError,
    BusinessError,
    ValidationError,
)
from app.integrations.mikrotik.client import MikroTikClient
from app.core.database.session import db


class RouterService:
    

    # Default RADIUS server — overridden by app config
    DEFAULT_RADIUS_SERVER = '163.245.217.16'

    def __init__(self):
        self.repository = RouterRepository()
        self.hotspot_repo = HotspotServerRepository()
        self.pppoe_repo = PPPoeServerRepository()
        self.encryption = EncryptionService()
        self.mikrotik_client = MikroTikClient()

    # HELPERS

    def _generate_radius_secret(self) -> str:
        
        return secrets.token_urlsafe(32)

    def _get_radius_server(self) -> str:
        """Get the RADIUS server IP from config with fallback."""
        return current_app.config.get('RADIUS_SERVER_IP', self.DEFAULT_RADIUS_SERVER)

    def _parse_uptime(self, uptime_str: str) -> int:
        
        if not uptime_str:
            return 0

        seconds = 0

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

    def _build_router_data(self, router: Router) -> Dict[str, Any]:
        
        return {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port or 8728,
            'api_ssl': getattr(router, 'api_ssl', False),
        }

    # NAS ENTRY MANAGEMENT

    def _create_nas_entry(self, router: Router, radius_secret: str) -> NAS:
        
        try:
            nas_entry = NAS(
                organization_id=router.organization_id,
                nasname=str(router.ip_address),       # ✅ Router's actual IP
                shortname=router.name,
                type='mikrotik',
                secret=radius_secret,
                description=f"Auto-created for router {router.name}",
                router_id=router.id,
                is_active=True,
            )
            db.session.add(nas_entry)
            db.session.flush()  

            # Link NAS entry back to the router
            router.nas_entry_id = nas_entry.id
            db.session.commit()

            logger.info(
                f"Created NAS entry for router '{router.name}' "
                f"(nasname={nas_entry.nasname})"
            )
            return nas_entry

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to create NAS entry for router "
                f"'{router.name}': {e}"
            )
            raise BusinessError(f"Failed to create NAS entry: {str(e)}")

    # ORGANIZATION MARKING ON ROUTER

    def _mark_router_with_org_slug(
        self,
        router: Router,
        organization: Organization
    ) -> Dict[str, Any]:
       
        results = {
            'identity_set': False,
            'dns_name_set': False,
        }

        router_data = self._build_router_data(router)

        # Method 1: Set system identity to include org slug
        try:
            identity_name = f"{router.name}-{organization.slug}"
            self.mikrotik_client.execute(
                router_data=router_data,
                command='/system/identity/set',
                name=identity_name,
            )
            results['identity_set'] = True
            logger.info(
                f"Set router identity to '{identity_name}' "
                f"for org '{organization.slug}'"
            )
        except Exception as e:
            logger.warning(f"Failed to set router identity: {e}")

        # Method 2: Set hotspot DNS name for captive portal identification
        try:
            dns_name = f"{organization.slug}.hotspot.local"
            self.mikrotik_client.execute(
                router_data=router_data,
                command='/ip/hotspot/profile/set',
                **{'dns-name': dns_name},
            )
            results['dns_name_set'] = True
            logger.info(f"Set hotspot dns-name to '{dns_name}'")
        except Exception as e:
            logger.warning(f"Failed to set hotspot dns-name: {e}")

        return results

    # RADIUS AUTO-CONFIGURATION

    def _auto_configure_router_radius(
        self,
        router: Router,
        radius_secret: str,
        organization: Organization = None,
    ) -> Dict[str, Any]:
        
        try:
            radius_server = self._get_radius_server()
            router_data = self._build_router_data(router)

            # Mark router with organization slug (best effort)
            if organization:
                self._mark_router_with_org_slug(router, organization)

            # Delegate RADIUS configuration to the MikroTik client
            # configure_radius() handles:
            #   - Adding/updating /radius entry
            #   - Enabling /ip/hotspot/set radius=yes
            #   - Enabling /ppp/set use-radius=yes
            #   - Enabling /radius/incoming/set accept=yes
            result = self.mikrotik_client.configure_radius(
                router_data=router_data,
                radius_server=radius_server,
                radius_secret=radius_secret,
                radius_port=1812,
                radius_acct_port=1813,
            )

            if result.get('success'):
                logger.info(
                    f"RADIUS auto-configured on router '{router.name}' "
                    f"→ {radius_server}"
                )
            else:
                logger.warning(
                    f"RADIUS auto-config failed for router '{router.name}': "
                    f"{result.get('error')}"
                )

            return result

        except Exception as e:
            logger.error(
                f"Auto-configuration error for router '{router.name}': {e}"
            )
            return {'success': False, 'error': str(e)}

    # CREATE OPERATIONS

    def create_router(
        self,
        organization_id: UUID,
        network_id: UUID,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        # Validate required fields
        required = ['name', 'ip_address', 'username', 'password']
        for field in required:
            if not data.get(field):
                raise ValidationError(f"'{field}' is required")

        # Verify organization exists
        organization = Organization.query.get(organization_id)
        if not organization:
            raise ValidationError("Organization not found")

        # Generate security credentials
        radius_secret = self._generate_radius_secret()
        encrypted_password = self.encryption.encrypt(data['password'])

        # Prepare router data
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
            'is_active': True,
            'status': 'pending',
            'radius_secret': radius_secret,
            'radius_config_status': 'pending',
            'auto_config_attempts': 0,
        }

        # Create the router record
        router = self.repository.create(router_data)

        # Create NAS entry with the router's actual IP
        nas_entry = self._create_nas_entry(router, radius_secret)

        # Attempt connection test and auto-configuration
        auto_configured = False
        config_error = None

        try:
            test_result = self.test_connection(router.id, organization_id)

            if test_result.get('success'):
                # Router is reachable — configure RADIUS
                config_result = self._auto_configure_router_radius(
                    router, radius_secret, organization
                )

                if config_result.get('success'):
                    auto_configured = True
                    self.repository.update_radius_config_status(
                        router.id, organization_id, 'configured'
                    )
                    self.repository.update_status(
                        router.id, organization_id, 'online'
                    )
                else:
                    config_error = config_result.get('error')
                    self.repository.update_radius_config_status(
                        router.id, organization_id, 'failed', error=config_error
                    )
                    self.repository.update_status(
                        router.id, organization_id, 'radius_pending'
                    )
            else:
                config_error = test_result.get('error', 'Connection test failed')
                self.repository.update_status(
                    router.id, organization_id, 'offline'
                )

        except Exception as e:
            config_error = str(e)
            logger.error(
                f"Auto-configuration failed for router "
                f"'{router.name}': {e}"
            )
            self.repository.update_radius_config_status(
                router.id, organization_id, 'failed', error=config_error
            )

        # Build response
        response = {
            'router': router,
            'auto_configured': auto_configured,
            'radius_secret': radius_secret,
            'radius_server_ip': self._get_radius_server(),
            'organization_slug': organization.slug,
            'organization_id': str(organization_id),
            'organization_name': organization.name,
        }

        if not auto_configured:
            response['manual_config_instructions'] = self._build_manual_instructions(
                radius_secret, organization
            )
            if config_error:
                response['error'] = config_error

        logger.info(
            f"Router created: '{router.name}' "
            f"(IP: {router.ip_address}, "
            f"Auto-configured: {auto_configured}) | "
            f"Org: {organization.slug}"
        )
        return response

    def _build_manual_instructions(
        self,
        radius_secret: str,
        organization: Organization,
    ) -> Dict[str, Any]:
        """
        Build manual RADIUS configuration instructions for the ISP admin.

        Used when auto-configuration fails (router unreachable).
        """
        radius_server = self._get_radius_server()

        return {
            'command': (
                f'/radius add address={radius_server} '
                f'secret={radius_secret} service=hotspot,ppp'
            ),
            'additional_commands': [
                '/ip hotspot set radius=yes',
                '/ppp set use-radius=yes',
                '/radius incoming set accept=yes',
                f'/system identity set name="router-{organization.slug}"',
                f'/ip hotspot profile set default dns-name="{organization.slug}.hotspot.local"',
            ],
            'message': (
                'Please run these commands on your MikroTik router via '
                'Winbox terminal or SSH to complete RADIUS configuration.'
            ),
        }

    # READ OPERATIONS

    def get_router(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> Router:
        
        router = self.repository.get_by_id(router_id, organization_id)
        if not router:
            raise NotFoundError("Router not found")
        return router

    def get_routers_by_organization(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: str = None,
        network_id: UUID = None,
        radius_config_status: str = None,
    ) -> List[Router]:
        """Get all routers for an organization with optional filters."""
        return self.repository.get_by_organization(
            organization_id, skip, limit, status, network_id, radius_config_status
        )

    def get_routers_by_network(
        self,
        network_id: UUID,
        organization_id: UUID
    ) -> List[Router]:
        """Get all routers on a specific network."""
        return self.repository.get_by_network(network_id, organization_id)

    def get_routers_pending_radius_config(
        self,
        organization_id: UUID
    ) -> List[Router]:
        """Get routers awaiting RADIUS configuration."""
        return self.repository.get_routers_pending_radius_config(organization_id)

    def get_router_by_ip(
        self,
        ip_address: str,
        organization_id: UUID
    ) -> Optional[Router]:
        """Find a router by IP address within an organization."""
        return self.repository.get_by_ip(ip_address, organization_id)

    # UPDATE OPERATIONS

    def update_router(
        self,
        router_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any]
    ) -> Router:
        
        # Re-encrypt password if provided
        if "password" in data and data["password"]:
            data["password_encrypted"] = self.encryption.encrypt(
                data.pop("password")
            )
        elif "password" in data:
            data.pop("password")  # Remove empty password

        router = self.repository.update(router_id, organization_id, data)
        if not router:
            raise NotFoundError("Router not found")

        logger.info(f"Router updated: {router_id}")
        return router

    def retry_radius_configuration(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> Dict[str, Any]:
        """
        Retry RADIUS configuration on a router that previously failed.

        Uses the existing radius_secret — does not generate a new one.
        Updates the RADIUS config status based on the result.

        Returns:
            Dict with success flag and either confirmation or manual instructions
        """
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)

        if not router.radius_secret:
            raise BusinessError("Router has no RADIUS secret configured")

        config_result = self._auto_configure_router_radius(
            router, router.radius_secret, organization
        )

        if config_result.get('success'):
            self.repository.update_radius_config_status(
                router.id, organization_id, 'configured'
            )
            return {
                'success': True,
                'message': 'RADIUS configured successfully',
                'radius_secret': router.radius_secret,
                'organization_slug': organization.slug if organization else None,
            }
        else:
            error_msg = config_result.get('error', 'Unknown error')
            self.repository.update_radius_config_status(
                router.id, organization_id, 'failed', error=error_msg
            )
            return {
                'success': False,
                'message': f'RADIUS configuration failed: {error_msg}',
                'manual_config_instructions': self._build_manual_instructions(
                    router.radius_secret, organization
                ) if organization else None,
            }

    # DELETE OPERATIONS

    def delete_router(
        self,
        router_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True
    ) -> None:
        """
        Delete or deactivate a router.

        Soft delete (default): Deactivates router and its NAS entry.
        Hard delete: Permanently removes router and NAS entry.
                      Requires no active hotspot/PPPoE servers.

        Raises:
            NotFoundError: If router doesn't exist
            BusinessError: If hard-deleting with active services
        """
        router = self.repository.get_by_id(
            router_id, organization_id, include_inactive=True
        )
        if not router:
            raise NotFoundError("Router not found")

        if not soft_delete:
            # Check for active services before hard delete
            hotspot_servers = self.hotspot_repo.get_by_router(
                router_id, organization_id
            )
            pppoe_servers = self.pppoe_repo.get_by_router(
                router_id, organization_id
            )

            if len(hotspot_servers) > 0 or len(pppoe_servers) > 0:
                raise BusinessError(
                    "Cannot delete router with active services. "
                    "Remove services first or use soft delete."
                )

        self.repository.delete(router_id, organization_id, soft_delete)
        logger.info(
            f"Router {router_id} "
            f"{'deactivated' if soft_delete else 'permanently deleted'}"
        )

    # CONNECTION TESTING

    def test_connection(
        self,
        router_id: UUID,
        organization_id: UUID,
        method: str = 'api'
    ) -> Dict[str, Any]:
        
        router = self.get_router(router_id, organization_id)

        if method != 'api':
            raise ValidationError(f"Unsupported connection method: {method}")

        try:
            # Decrypt password for the connection test
            password = self.encryption.decrypt(router.password_encrypted)

            result = self.mikrotik_client.test_connection(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port or 8728,
            )

            # Update router status based on result
            status = 'online' if result.get('success') else 'offline'
            self.repository.update_status(
                router_id,
                organization_id,
                status,
                error_message=result.get('error') if not result.get('success') else None,
            )

            return result

        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error', error_message=str(e)
            )
            raise BusinessError(f"Connection test failed: {str(e)}")

    # DISCOVERY

    def discover_router(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> Dict[str, Any]:
        
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)

        # Discovery methods in priority order
        discovery_methods = [
            ('api', self._discover_via_api),
            ('ssh', self._discover_via_ssh),
            ('snmp', self._discover_via_snmp),
            ('telnet', self._discover_via_telnet),
        ]

        attempts = []

        for method_name, method_func in discovery_methods:
            try:
                logger.info(
                    f"Attempting discovery via {method_name} "
                    f"for router '{router.name}'"
                )
                result = method_func(router, router_data)

                if result.get('success'):
                    # Update router with discovered information
                    self.repository.update_discovery(
                        router_id=router_id,
                        organization_id=organization_id,
                        model=result.get('model'),
                        firmware_version=result.get('version'),
                        capabilities=result.get('capabilities'),
                        discovery_method=method_name,
                    )

                    self.repository.update_status(
                        router_id, organization_id, 'online'
                    )

                    return {
                        'success': True,
                        'method': method_name,
                        'info': result,
                        'message': f'Router discovered via {method_name}',
                    }

                attempts.append({
                    'method': method_name,
                    'error': result.get('error', 'Unknown error'),
                })

            except Exception as e:
                logger.warning(f"Discovery via {method_name} failed: {e}")
                attempts.append({'method': method_name, 'error': str(e)})
                continue

        # All methods failed
        self.repository.update_status(router_id, organization_id, 'offline')

        return {
            'success': False,
            'message': 'Auto-discovery failed. Router added in offline mode.',
            'attempts': attempts,
        }

    def _discover_via_api(
        self,
        router: Router,
        router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Discover router capabilities via MikroTik API.

        Detects: hotspot, PPPoE, wireless capabilities plus system info.
        """
        try:
            info = self.mikrotik_client.get_router_info(router_data)
            capabilities = ['api']

            # Check for hotspot servers
            try:
                hotspot_result = self.mikrotik_client.get_hotspot_servers(
                    router_data
                )
                if hotspot_result and len(hotspot_result) > 0:
                    capabilities.append('hotspot')
            except Exception:
                pass

            # Check for PPPoE servers
            try:
                pppoe_result = self.mikrotik_client.get_pppoe_servers(
                    router_data
                )
                if pppoe_result and len(pppoe_result) > 0:
                    capabilities.append('pppoe')
            except Exception:
                pass

            # Check for wireless interfaces
            try:
                wireless_result = self.mikrotik_client.execute(
                    router_data, '/interface/wireless/print'
                )
                if wireless_result and len(wireless_result) > 0:
                    capabilities.append('wireless')
            except Exception:
                pass

            return {
                'success': True,
                'model': info.get('board_name'),
                'version': info.get('version'),
                'capabilities': capabilities,
                'uptime': info.get('uptime'),
                'cpu_load': info.get('cpu_load'),
                'free_memory': info.get('free_memory'),
                'total_memory': info.get('total_memory'),
            }

        except Exception as e:
            return {'success': False, 'error': f"API discovery failed: {str(e)}"}

    def _discover_via_ssh(
        self,
        router: Router,
        router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Discover via SSH (requires paramiko)."""
        try:
            import paramiko

            password = self.encryption.decrypt(router.password_encrypted)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.ssh_port or 22,
                timeout=10,
            )

            stdin, stdout, stderr = ssh.exec_command(
                '/system resource print'
            )
            output = stdout.read().decode()

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
            }

        except ImportError:
            return {
                'success': False,
                'error': 'SSH discovery requires paramiko library',
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"SSH discovery failed: {str(e)}",
            }

    def _discover_via_snmp(
        self,
        router: Router,
        router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Discover via SNMP (requires pysnmp)."""
        try:
            from pysnmp.hlapi import (
                getCmd, CommunityData, UdpTransportTarget,
                ObjectIdentity, ObjectType,
            )

            community = 'public'
            oids = {
                'model': '1.3.6.1.4.1.14988.1.1.1.1.0',
                'version': '1.3.6.1.4.1.14988.1.1.1.2.0',
                'serial': '1.3.6.1.4.1.14988.1.1.1.3.0',
            }

            result = {}
            for key, oid in oids.items():
                errorIndication, errorStatus, errorIndex, varBinds = next(
                    getCmd(
                        CommunityData(community),
                        UdpTransportTarget((str(router.ip_address), 161)),
                        0,
                        ObjectType(ObjectIdentity(oid)),
                    )
                )

                if not errorIndication and not errorStatus:
                    for varBind in varBinds:
                        result[key] = str(varBind[1])

            if not result:
                return {'success': False, 'error': 'No SNMP response'}

            return {
                'success': True,
                'model': result.get('model'),
                'version': result.get('version'),
                'capabilities': ['snmp'],
            }

        except ImportError:
            return {
                'success': False,
                'error': 'SNMP discovery requires pysnmp library',
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"SNMP discovery failed: {str(e)}",
            }

    def _discover_via_telnet(
        self,
        router: Router,
        router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Discover via Telnet (fallback only, not recommended)."""
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
            }

        except Exception as e:
            return {
                'success': False,
                'error': f"Telnet discovery failed: {str(e)}",
            }

    # HEALTH MONITORING

    def update_health(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> Dict[str, Any]:
        """
        Update router health metrics from live router data.

        Fetches system resource info via API and stores health data
        in the router's settings JSON field.

        Returns:
            Dict with CPU, memory, uptime, version, and board info

        Raises:
            BusinessError: If health check fails
        """
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)

        try:
            info = self.mikrotik_client.get_router_info(router_data)

            # Parse CPU load
            cpu_load = None
            if info.get('cpu_load'):
                try:
                    cpu_load = int(info['cpu_load'])
                except (ValueError, TypeError):
                    pass

            # Parse memory
            free_memory = None
            total_memory = None
            if info.get('free_memory'):
                try:
                    free_memory = int(info['free_memory'])
                except (ValueError, TypeError):
                    pass
            if info.get('total_memory'):
                try:
                    total_memory = int(info['total_memory'])
                except (ValueError, TypeError):
                    pass

            # Parse uptime
            uptime_str = info.get('uptime', '0s')
            uptime_seconds = self._parse_uptime(uptime_str)

            # Update router health (uses settings JSON in repo)
            self.repository.update_health(
                router_id=router_id,
                organization_id=organization_id,
                cpu_load=cpu_load,
                free_memory=free_memory,
                total_memory=total_memory,
                uptime=uptime_str,
            )

            self.repository.update_status(router_id, organization_id, 'online')

            return {
                'cpu_load': cpu_load,
                'free_memory': free_memory,
                'total_memory': total_memory,
                'uptime_seconds': uptime_seconds,
                'uptime_hours': round(uptime_seconds / 3600, 2) if uptime_seconds else 0,
                'uptime_display': uptime_str,
                'version': info.get('version'),
                'board_name': info.get('board_name'),
                'architecture_name': info.get('architecture_name'),
            }

        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error', error_message=str(e)
            )
            raise BusinessError(f"Health check failed: {str(e)}")

    # SYNC (PULL FROM ROUTER)

    def sync_router(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> Dict[str, Any]:
        """
        Full sync of router configuration into the database.

        Pulls hotspot servers and PPPoE servers from the router
        and creates/updates corresponding database records.

        Returns:
            Dict with sync counts and any errors
        """
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)

        results = {
            'success': True,
            'hotspot_synced': 0,
            'pppoe_synced': 0,
            'errors': [],
        }

        try:
            # Sync hotspot servers
            hotspot_count = self._sync_hotspot_servers(router, router_data)
            results['hotspot_synced'] = hotspot_count

            # Sync PPPoE servers
            pppoe_count = self._sync_pppoe_servers(router, router_data)
            results['pppoe_synced'] = pppoe_count

            # Update sync timestamp
            self.repository.update(router_id, organization_id, {
                'last_sync_at': datetime.utcnow(),
                'status': 'online',
            })

            logger.info(
                f"Router '{router.name}' synced: "
                f"{hotspot_count} hotspot, {pppoe_count} PPPoE"
            )

            return results

        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error', error_message=str(e)
            )
            raise BusinessError(f"Sync failed: {str(e)}")

    def _sync_hotspot_servers(
        self,
        router: Router,
        router_data: Dict[str, Any]
    ) -> int:
        """Sync hotspot servers from router to database."""
        try:
            hotspot_servers = self.mikrotik_client.get_hotspot_servers(router_data)
            count = 0

            for hs in hotspot_servers:
                hotspot_name = hs.get('name', '')
                if not hotspot_name:
                    continue

                existing = self.hotspot_repo.get_by_router_and_hotspot_id(
                    router.id, router.organization_id, hotspot_name
                )

                hs_data = {
                    'organization_id': router.organization_id,
                    'router_id': router.id,
                    'name': hotspot_name,
                    'hotspot_id': hotspot_name,
                    'interface': hs.get('interface'),
                    'address_pool': hs.get('address-pool'),
                    'idle_timeout': int(hs.get('idle-timeout', 300)),
                    'session_timeout': int(hs.get('session-timeout', 86400)),
                    'is_active': hs.get('disabled') != 'true',
                }

                if existing:
                    self.hotspot_repo.update(
                        existing.id, router.organization_id, hs_data
                    )
                else:
                    self.hotspot_repo.create(hs_data)

                count += 1

            return count

        except Exception as e:
            logger.error(
                f"Failed to sync hotspot servers for router "
                f"'{router.name}': {e}"
            )
            return 0

    def _sync_pppoe_servers(
        self,
        router: Router,
        router_data: Dict[str, Any]
    ) -> int:
        """Sync PPPoE servers from router to database."""
        try:
            pppoe_servers = self.mikrotik_client.get_pppoe_servers(router_data)
            count = 0

            for ps in pppoe_servers:
                server_name = ps.get('name', '')
                if not server_name:
                    continue

                existing = self.pppoe_repo.get_by_router_and_name(
                    router.id, router.organization_id, server_name
                )

                ps_data = {
                    'organization_id': router.organization_id,
                    'router_id': router.id,
                    'name': server_name,
                    'interface': ps.get('interface'),
                    'service_name': ps.get('service-name'),
                    'mtu': int(ps.get('mtu', 1492)),
                    'max_sessions': int(ps.get('max-sessions', 100)),
                    'is_active': ps.get('disabled') != 'true',
                }

                if existing:
                    self.pppoe_repo.update(
                        existing.id, router.organization_id, ps_data
                    )
                else:
                    self.pppoe_repo.create(ps_data)

                count += 1

            return count

        except Exception as e:
            logger.error(
                f"Failed to sync PPPoE servers for router "
                f"'{router.name}': {e}"
            )
            return 0

    # MANUAL RADIUS CONFIGURATION

    def configure_radius_manual(
        self,
        router_id: UUID,
        organization_id: UUID,
        radius_server: str,
        radius_secret: str
    ) -> Dict[str, Any]:
        """
        Manually configure RADIUS on a router with explicit server/secret.

        Used when the ISP admin wants to specify a custom RADIUS server
        or re-configure with different credentials.

        Args:
            router_id: Router UUID
            organization_id: Tenant organization UUID
            radius_server: RADIUS server IP/hostname
            radius_secret: RADIUS shared secret

        Returns:
            Dict with success/failure and details
        """
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)
        router_data = self._build_router_data(router)

        try:
            result = self.mikrotik_client.configure_radius(
                router_data=router_data,
                radius_server=radius_server,
                radius_secret=radius_secret,
                radius_port=1812,
                radius_acct_port=1813,
            )

            if result.get('success'):
                self.repository.update_radius_config_status(
                    router_id, organization_id, 'configured'
                )

                if organization:
                    self._mark_router_with_org_slug(router, organization)

                # Persist the RADIUS server in router settings
                settings = router.settings or {}
                settings['radius_server'] = radius_server
                settings['radius_configured_at'] = datetime.utcnow().isoformat()
                self.repository.update(
                    router_id, organization_id, {'settings': settings}
                )

            return result

        except Exception as e:
            raise BusinessError(f"RADIUS configuration failed: {str(e)}")

    # CONNECTION STATUS

    def get_connection_status(
        self,
        router_id: UUID,
        organization_id: UUID
    ) -> Dict[str, Any]:
        """
        Get current connection status and health summary.

        Returns a comprehensive status snapshot for dashboard display.
        """
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)

        # Extract health data from settings JSON if available
        settings = router.settings or {}
        health = settings.get('health', {})

        return {
            'router_id': str(router.id),
            'name': router.name,
            'ip_address': str(router.ip_address),
            'status': router.status,
            'radius_config_status': router.radius_config_status,
            'auto_config_attempts': router.auto_config_attempts or 0,
            'last_seen_at': router.last_seen_at.isoformat() if router.last_seen_at else None,
            'last_sync_at': router.last_sync_at.isoformat() if router.last_sync_at else None,
            'is_active': router.is_active,
            'health': {
                'cpu_load': health.get('cpu_load'),
                'free_memory': health.get('free_memory'),
                'total_memory': health.get('total_memory'),
                'uptime': health.get('uptime'),
                'last_checked_at': health.get('last_checked_at'),
            },
            'has_error': bool(router.last_config_error),
            'last_error': router.last_config_error,
            'organization_slug': organization.slug if organization else None,
        }