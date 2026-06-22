
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
from app.integrations.wireguard.manager import WireGuardManager
from app.core.database.session import db


class RouterService:
    """Service for router management with WireGuard VPN and RADIUS integration."""

    # Default RADIUS server — VPS WireGuard IP
    DEFAULT_RADIUS_SERVER = '10.0.0.1'

    def __init__(self):
        self.repository = RouterRepository()
        self.hotspot_repo = HotspotServerRepository()
        self.pppoe_repo = PPPoeServerRepository()
        self.encryption = EncryptionService()
        self.mikrotik_client = MikroTikClient()
        self.wireguard = WireGuardManager()

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _generate_radius_secret(self) -> str:
        """Generate a cryptographically strong RADIUS shared secret."""
        return secrets.token_urlsafe(32)

    def _get_radius_server(self) -> str:
        """Get the RADIUS server IP from config with fallback."""
        return current_app.config.get('RADIUS_SERVER_IP', self.DEFAULT_RADIUS_SERVER)

    def _parse_uptime(self, uptime_str: str) -> int:
        """Parse MikroTik uptime string to total seconds."""
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
        """Build router_data dict for MikroTikClient from ORM model."""
        return {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port or 8728,
            'api_ssl': getattr(router, 'api_ssl', False),
        }

    def _get_org_existing_wireguard_ips(self, organization_id: UUID) -> List[str]:
        """Get all WireGuard IPs already assigned in this organization."""
        routers = self.repository.get_by_organization(organization_id, limit=10000)
        return [r.wireguard_ip for r in routers if r.wireguard_ip]

    # =========================================================================
    # NAS ENTRY MANAGEMENT
    # =========================================================================

    def _create_nas_entry(self, router: Router, radius_secret: str) -> NAS:
        """Create a NAS entry for FreeRADIUS with the router's WireGuard IP."""
        try:
            nas_entry = NAS(
                organization_id=router.organization_id,
                nasname=str(router.ip_address),  # WireGuard IP
                shortname=router.name,
                type='mikrotik',
                secret=radius_secret,
                description=f"Auto-created for router {router.name}",
                router_id=router.id,
                is_active=True,
            )
            db.session.add(nas_entry)
            db.session.flush()
            router.nas_entry_id = nas_entry.id
            db.session.commit()
            logger.info(
                f"Created NAS entry for '{router.name}' "
                f"(nasname={nas_entry.nasname})"
            )
            return nas_entry
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create NAS entry for '{router.name}': {e}")
            raise BusinessError(f"Failed to create NAS entry: {str(e)}")

    # =========================================================================
    # ORGANIZATION MARKING ON ROUTER
    # =========================================================================

    def _mark_router_with_org_slug(
        self, router: Router, organization: Organization
    ) -> Dict[str, Any]:
        """Mark the MikroTik router with organization slug for identification."""
        results = {'identity_set': False, 'dns_name_set': False}
        router_data = self._build_router_data(router)

        try:
            identity_name = f"{router.name}-{organization.slug}"
            self.mikrotik_client.execute(
                router_data=router_data,
                command='/system/identity/set',
                name=identity_name,
            )
            results['identity_set'] = True
        except Exception as e:
            logger.warning(f"Failed to set router identity: {e}")

        try:
            dns_name = f"{organization.slug}.hotspot.local"
            self.mikrotik_client.execute(
                router_data=router_data,
                command='/ip/hotspot/profile/set',
                **{'dns-name': dns_name},
            )
            results['dns_name_set'] = True
        except Exception as e:
            logger.warning(f"Failed to set hotspot dns-name: {e}")

        return results

    # =========================================================================
    # RADIUS AUTO-CONFIGURATION
    # =========================================================================

    def _auto_configure_router_radius(
        self, router: Router, radius_secret: str, organization: Organization = None
    ) -> Dict[str, Any]:
        """Auto-configure RADIUS on a MikroTik router via API."""
        try:
            radius_server = self._get_radius_server()
            router_data = self._build_router_data(router)

            if organization:
                self._mark_router_with_org_slug(router, organization)

            result = self.mikrotik_client.configure_radius(
                router_data=router_data,
                radius_server=radius_server,
                radius_secret=radius_secret,
                radius_port=1812,
                radius_acct_port=1813,
            )

            if result.get('success'):
                logger.info(
                    f"RADIUS auto-configured on '{router.name}' → {radius_server}"
                )
            else:
                logger.warning(
                    f"RADIUS auto-config failed for '{router.name}': "
                    f"{result.get('error')}"
                )

            return result
        except Exception as e:
            logger.error(f"Auto-configuration error for '{router.name}': {e}")
            return {'success': False, 'error': str(e)}

    # =========================================================================
    # CREATE ROUTER (WIREGUARD-INTEGRATED)
    # =========================================================================

    def create_router(
        self, organization_id: UUID, network_id: UUID, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new router with WireGuard VPN + RADIUS configuration.

        Steps:
            1. Validate inputs
            2. Generate WireGuard keypair + allocate IP in org subnet
            3. Generate RADIUS secret + encrypt passwords
            4. Create Router record (ip_address = WireGuard IP)
            5. Create NAS entry
            6. Add WireGuard peer on VPS via SSH
            7. Return stepped MikroTik setup script for admin

        Admin pastes the script → clicks "Test Connection" → system auto-configures.
        """
        # Validate required fields
        required = ['name', 'username', 'password']
        for field in required:
            if not data.get(field):
                raise ValidationError(f"'{field}' is required")

        local_ip = data.get('ip_address') or data.get('local_ip')
        if not local_ip:
            raise ValidationError("Either 'ip_address' or 'local_ip' is required")

        organization = Organization.query.get(organization_id)
        if not organization:
            raise ValidationError("Organization not found")

        # Generate WireGuard credentials
        wg_private_key, wg_public_key = self.wireguard.generate_peer_keypair()

        # Allocate WireGuard IP in org's subnet
        existing_ips = self._get_org_existing_wireguard_ips(organization_id)
        wireguard_ip, subnet, org_index = self.wireguard.allocate_ip(
            str(organization_id), existing_ips
        )

        # Generate RADIUS secret
        radius_secret = self._generate_radius_secret()

        # Encrypt sensitive data
        encrypted_password = self.encryption.encrypt(data['password'])
        encrypted_wg_key = self.encryption.encrypt(wg_private_key)

        # Create router record — ip_address = WireGuard IP for API access
        router_data = {
            'organization_id': organization_id,
            'network_id': network_id,
            'name': data['name'],
            'model': data.get('model'),
            'ip_address': wireguard_ip,        # PRIMARY: WireGuard IP
            'local_ip': local_ip,               # REFERENCE: admin's local IP
            'api_port': data.get('api_port', 8728),
            'username': data['username'],
            'password_encrypted': encrypted_password,
            'location': data.get('location'),
            'description': data.get('description'),
            'is_active': True,
            'status': 'pending_wireguard',
            'radius_secret': radius_secret,
            'radius_config_status': 'pending',
            'auto_config_attempts': 0,
            'wireguard_ip': wireguard_ip,
            'wireguard_public_key': wg_public_key,
            'wireguard_private_key_encrypted': encrypted_wg_key,
        }

        router = self.repository.create(router_data)

        # Create NAS entry
        nas_entry = self._create_nas_entry(router, radius_secret)

        # Add WireGuard peer on VPS via SSH
        wg_added = self.wireguard.add_peer(wg_public_key, f"{wireguard_ip}/32")

        # Generate stepped MikroTik setup script for admin
        setup_script = self.wireguard.generate_mikrotik_setup_script(
            wireguard_ip=wireguard_ip,
            mikrotik_private_key=wg_private_key,
            radius_secret=radius_secret,
            include_radius=True,
        )

        logger.info(
            f"Router created: '{router.name}' "
            f"(WireGuard IP: {wireguard_ip}, "
            f"VPS peer: {'added' if wg_added else 'FAILED'}) | "
            f"Org: {organization.slug}"
        )

        return {
            'success': True,
            'router': router,
            'wireguard': {
                'ip': wireguard_ip,
                'public_key': wg_public_key,
                'private_key': wg_private_key,  # Shown once
                'peer_added_to_vps': wg_added,
            },
            'radius': {
                'secret': radius_secret,
                'server': self._get_radius_server(),
            },
            'setup_script': setup_script,
            'next_step': (
                'Paste the WireGuard script into your MikroTik terminal, '
                'then click Test Connection.'
            ),
        }

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    def get_router(self, router_id: UUID, organization_id: UUID) -> Router:
        router = self.repository.get_by_id(router_id, organization_id)
        if not router:
            raise NotFoundError("Router not found")
        return router

    def get_routers_by_organization(
        self, organization_id: UUID, skip: int = 0, limit: int = 100,
        status: str = None, network_id: UUID = None,
        radius_config_status: str = None,
    ) -> List[Router]:
        return self.repository.get_by_organization(
            organization_id, skip, limit, status, network_id, radius_config_status
        )

    def get_routers_by_network(
        self, network_id: UUID, organization_id: UUID
    ) -> List[Router]:
        return self.repository.get_by_network(network_id, organization_id)

    def get_routers_pending_radius_config(
        self, organization_id: UUID
    ) -> List[Router]:
        return self.repository.get_routers_pending_radius_config(organization_id)

    def get_router_by_ip(
        self, ip_address: str, organization_id: UUID
    ) -> Optional[Router]:
        return self.repository.get_by_ip(ip_address, organization_id)

    # =========================================================================
    # UPDATE OPERATIONS
    # =========================================================================

    def update_router(
        self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]
    ) -> Router:
        if "password" in data and data["password"]:
            data["password_encrypted"] = self.encryption.encrypt(data.pop("password"))
        elif "password" in data:
            data.pop("password")

        router = self.repository.update(router_id, organization_id, data)
        if not router:
            raise NotFoundError("Router not found")
        logger.info(f"Router updated: {router_id}")
        return router

    def retry_radius_configuration(
        self, router_id: UUID, organization_id: UUID
    ) -> Dict[str, Any]:
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
            }
        else:
            error_msg = config_result.get('error', 'Unknown error')
            self.repository.update_radius_config_status(
                router.id, organization_id, 'failed', error=error_msg
            )
            return {
                'success': False,
                'message': f'RADIUS configuration failed: {error_msg}',
            }

    # =========================================================================
    # DELETE OPERATIONS
    # =========================================================================

    def delete_router(
        self, router_id: UUID, organization_id: UUID, soft_delete: bool = True
    ) -> None:
        router = self.repository.get_by_id(
            router_id, organization_id, include_inactive=True
        )
        if not router:
            raise NotFoundError("Router not found")

        # Remove WireGuard peer from VPS
        if router.wireguard_public_key:
            self.wireguard.remove_peer(router.wireguard_public_key)

        if not soft_delete:
            hotspot_count = len(
                self.hotspot_repo.get_by_router(router_id, organization_id)
            )
            pppoe_count = len(
                self.pppoe_repo.get_by_router(router_id, organization_id)
            )
            if hotspot_count > 0 or pppoe_count > 0:
                raise BusinessError(
                    "Cannot delete router with active services."
                )

        self.repository.delete(router_id, organization_id, soft_delete)
        logger.info(
            f"Router {'deactivated' if soft_delete else 'deleted'}: {router_id}"
        )

    # =========================================================================
    # CONNECTION TESTING (uses WireGuard IP)
    # =========================================================================

    def test_connection(
        self, router_id: UUID, organization_id: UUID, method: str = 'api'
    ) -> Dict[str, Any]:
        """Test connection to router via its WireGuard IP."""
        router = self.get_router(router_id, organization_id)

        if method != 'api':
            raise ValidationError(f"Unsupported method: {method}")

        try:
            password = self.encryption.decrypt(router.password_encrypted)
            result = self.mikrotik_client.test_connection(
                host=str(router.ip_address),  # WireGuard IP
                username=router.username,
                password=password,
                port=router.api_port or 8728,
            )

            status = 'online' if result.get('success') else 'offline'
            self.repository.update_status(
                router_id, organization_id, status,
                error_message=result.get('error') if not result.get('success') else None,
            )

            return result
        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error', error_message=str(e)
            )
            raise BusinessError(f"Connection test failed: {str(e)}")

    # =========================================================================
    # AUTO-CONFIGURE AFTER WIREGUARD IS UP
    # =========================================================================

    def auto_configure_after_wireguard(
        self, router_id: UUID, organization_id: UUID
    ) -> Dict[str, Any]:
        """
        Run after admin confirms WireGuard is working.
        Configures RADIUS and discovers router capabilities.
        """
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)

        result = {
            'success': True,
            'radius_configured': False,
            'discovered': False,
            'steps': [],
        }

        # Step 1: Configure RADIUS on MikroTik via API
        try:
            radius_result = self.mikrotik_client.configure_radius(
                router_data=router_data,
                radius_server=self._get_radius_server(),
                radius_secret=router.radius_secret,
            )
            if radius_result.get('success'):
                self.repository.update_radius_config_status(
                    router_id, organization_id, 'configured'
                )
                result['radius_configured'] = True
                result['steps'].append({'step': 'radius', 'status': 'success'})
            else:
                self.repository.update_radius_config_status(
                    router_id, organization_id, 'failed',
                    error=radius_result.get('error'),
                )
                result['steps'].append({
                    'step': 'radius', 'status': 'failed',
                    'error': radius_result.get('error'),
                })
        except Exception as e:
            result['steps'].append({
                'step': 'radius', 'status': 'error', 'error': str(e),
            })

        # Step 2: Discover router capabilities
        try:
            discovery = self._discover_via_api(router, router_data)
            if discovery.get('success'):
                self.repository.update_discovery(
                    router_id=router_id,
                    organization_id=organization_id,
                    model=discovery.get('model'),
                    firmware_version=discovery.get('version'),
                    capabilities=discovery.get('capabilities'),
                    discovery_method='api',
                )
                result['discovered'] = True
                result['discovery'] = discovery
                result['steps'].append({'step': 'discovery', 'status': 'success'})
        except Exception as e:
            result['steps'].append({
                'step': 'discovery', 'status': 'error', 'error': str(e),
            })

        # Update status
        if result['radius_configured']:
            self.repository.update_status(router_id, organization_id, 'online')
        else:
            self.repository.update_status(router_id, organization_id, 'radius_pending')

        result['all_success'] = result['radius_configured'] and result['discovered']
        return result

    # =========================================================================
    # DISCOVERY
    # =========================================================================

    def discover_router(
        self, router_id: UUID, organization_id: UUID
    ) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)

        discovery_methods = [
            ('api', self._discover_via_api),
            ('ssh', self._discover_via_ssh),
            ('snmp', self._discover_via_snmp),
            ('telnet', self._discover_via_telnet),
        ]

        attempts = []
        for method_name, method_func in discovery_methods:
            try:
                result = method_func(router, router_data)
                if result.get('success'):
                    self.repository.update_discovery(
                        router_id=router_id,
                        organization_id=organization_id,
                        model=result.get('model'),
                        firmware_version=result.get('version'),
                        capabilities=result.get('capabilities'),
                        discovery_method=method_name,
                    )
                    self.repository.update_status(router_id, organization_id, 'online')
                    return {
                        'success': True, 'method': method_name,
                        'info': result,
                        'message': f'Router discovered via {method_name}',
                    }
                attempts.append({
                    'method': method_name,
                    'error': result.get('error', 'Unknown error'),
                })
            except Exception as e:
                attempts.append({'method': method_name, 'error': str(e)})

        self.repository.update_status(router_id, organization_id, 'offline')
        return {
            'success': False,
            'message': 'Auto-discovery failed.',
            'attempts': attempts,
        }

    def _discover_via_api(
        self, router: Router, router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            info = self.mikrotik_client.get_router_info(router_data)
            capabilities = ['api']
            try:
                hs = self.mikrotik_client.get_hotspot_servers(router_data)
                if hs and len(hs) > 0:
                    capabilities.append('hotspot')
            except Exception:
                pass
            try:
                ps = self.mikrotik_client.get_pppoe_servers(router_data)
                if ps and len(ps) > 0:
                    capabilities.append('pppoe')
            except Exception:
                pass
            return {
                'success': True,
                'model': info.get('board_name'),
                'version': info.get('version'),
                'capabilities': capabilities,
                'uptime': info.get('uptime'),
                'cpu_load': info.get('cpu_load'),
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _discover_via_ssh(
        self, router: Router, router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            import paramiko
            password = self.encryption.decrypt(router.password_encrypted)
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=str(router.ip_address),
                username=router.username, password=password,
                port=router.ssh_port or 22, timeout=10,
            )
            stdin, stdout, stderr = ssh.exec_command('/system resource print')
            output = stdout.read().decode()
            model = version = None
            for line in output.split('\n'):
                if 'board-name:' in line:
                    model = line.split(':')[1].strip()
                elif 'version:' in line:
                    version = line.split(':')[1].strip()
            ssh.close()
            return {
                'success': True, 'model': model, 'version': version,
                'capabilities': ['ssh', 'api'],
            }
        except ImportError:
            return {'success': False, 'error': 'SSH requires paramiko'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _discover_via_snmp(
        self, router: Router, router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            from pysnmp.hlapi import (
                getCmd, CommunityData, UdpTransportTarget,
                ObjectIdentity, ObjectType,
            )
            community = 'public'
            oids = {
                'model': '1.3.6.1.4.1.14988.1.1.1.1.0',
                'version': '1.3.6.1.4.1.14988.1.1.1.2.0',
            }
            result = {}
            for key, oid in oids.items():
                errorIndication, errorStatus, errorIndex, varBinds = next(
                    getCmd(
                        CommunityData(community),
                        UdpTransportTarget((str(router.ip_address), 161)),
                        0, ObjectType(ObjectIdentity(oid)),
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
            return {'success': False, 'error': 'SNMP requires pysnmp'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _discover_via_telnet(
        self, router: Router, router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            import telnetlib
            password = self.encryption.decrypt(router.password_encrypted)
            tn = telnetlib.Telnet(str(router.ip_address), port=23, timeout=10)
            tn.read_until(b"Login: ")
            tn.write(router.username.encode('ascii') + b"\n")
            tn.read_until(b"Password: ")
            tn.write(password.encode('ascii') + b"\n")
            tn.write(b"/system resource print\nquit\n")
            output = tn.read_all().decode()
            tn.close()
            model = version = None
            for line in output.split('\n'):
                if 'board-name:' in line:
                    model = line.split(':')[1].strip()
                elif 'version:' in line:
                    version = line.split(':')[1].strip()
            return {
                'success': True, 'model': model, 'version': version,
                'capabilities': ['telnet'],
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # =========================================================================
    # HEALTH MONITORING
    # =========================================================================

    def update_health(
        self, router_id: UUID, organization_id: UUID
    ) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)
        try:
            info = self.mikrotik_client.get_router_info(router_data)
            cpu_load = int(info.get('cpu_load', 0)) if info.get('cpu_load') else None
            free_mem = int(info.get('free_memory', 0)) if info.get('free_memory') else None
            total_mem = int(info.get('total_memory', 0)) if info.get('total_memory') else None
            uptime_str = info.get('uptime', '0s')
            uptime_seconds = self._parse_uptime(uptime_str)

            self.repository.update_health(
                router_id=router_id, organization_id=organization_id,
                cpu_load=cpu_load, free_memory=free_mem,
                total_memory=total_mem, uptime=uptime_str,
            )
            self.repository.update_status(router_id, organization_id, 'online')

            return {
                'cpu_load': cpu_load, 'free_memory': free_mem,
                'total_memory': total_mem, 'uptime_seconds': uptime_seconds,
                'uptime_hours': round(uptime_seconds / 3600, 2) if uptime_seconds else 0,
                'uptime_display': uptime_str,
                'version': info.get('version'),
                'board_name': info.get('board_name'),
            }
        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error', error_message=str(e)
            )
            raise BusinessError(f"Health check failed: {str(e)}")

    # =========================================================================
    # SYNC
    # =========================================================================

    def sync_router(
        self, router_id: UUID, organization_id: UUID
    ) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)
        results = {
            'success': True, 'hotspot_synced': 0,
            'pppoe_synced': 0, 'errors': [],
        }
        try:
            results['hotspot_synced'] = self._sync_hotspot_servers(router, router_data)
            results['pppoe_synced'] = self._sync_pppoe_servers(router, router_data)
            self.repository.update(router_id, organization_id, {
                'last_sync_at': datetime.utcnow(), 'status': 'online',
            })
            return results
        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error', error_message=str(e)
            )
            raise BusinessError(f"Sync failed: {str(e)}")

    def _sync_hotspot_servers(
        self, router: Router, router_data: Dict[str, Any]
    ) -> int:
        try:
            servers = self.mikrotik_client.get_hotspot_servers(router_data)
            count = 0
            for hs in servers:
                name = hs.get('name', '')
                if not name:
                    continue
                existing = self.hotspot_repo.get_by_router_and_hotspot_id(
                    router.id, router.organization_id, name
                )
                hs_data = {
                    'organization_id': router.organization_id,
                    'router_id': router.id, 'name': name, 'hotspot_id': name,
                    'interface': hs.get('interface'),
                    'address_pool': hs.get('address-pool'),
                    'idle_timeout': int(hs.get('idle-timeout', 300)),
                    'session_timeout': int(hs.get('session-timeout', 86400)),
                    'is_active': hs.get('disabled') != 'true',
                }
                if existing:
                    self.hotspot_repo.update(existing.id, router.organization_id, hs_data)
                else:
                    self.hotspot_repo.create(hs_data)
                count += 1
            return count
        except Exception as e:
            logger.error(f"Hotspot sync failed: {e}")
            return 0

    def _sync_pppoe_servers(
        self, router: Router, router_data: Dict[str, Any]
    ) -> int:
        try:
            servers = self.mikrotik_client.get_pppoe_servers(router_data)
            count = 0
            for ps in servers:
                name = ps.get('name', '')
                if not name:
                    continue
                existing = self.pppoe_repo.get_by_router_and_name(
                    router.id, router.organization_id, name
                )
                ps_data = {
                    'organization_id': router.organization_id,
                    'router_id': router.id, 'name': name,
                    'interface': ps.get('interface'),
                    'service_name': ps.get('service-name'),
                    'mtu': int(ps.get('mtu', 1492)),
                    'max_sessions': int(ps.get('max-sessions', 100)),
                    'is_active': ps.get('disabled') != 'true',
                }
                if existing:
                    self.pppoe_repo.update(existing.id, router.organization_id, ps_data)
                else:
                    self.pppoe_repo.create(ps_data)
                count += 1
            return count
        except Exception as e:
            logger.error(f"PPPoE sync failed: {e}")
            return 0

    # =========================================================================
    # MANUAL RADIUS CONFIGURATION
    # =========================================================================

    def configure_radius_manual(
        self, router_id: UUID, organization_id: UUID,
        radius_server: str, radius_secret: str,
    ) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)
        router_data = self._build_router_data(router)
        try:
            result = self.mikrotik_client.configure_radius(
                router_data=router_data,
                radius_server=radius_server,
                radius_secret=radius_secret,
                radius_port=1812, radius_acct_port=1813,
            )
            if result.get('success'):
                self.repository.update_radius_config_status(
                    router_id, organization_id, 'configured'
                )
                if organization:
                    self._mark_router_with_org_slug(router, organization)
            return result
        except Exception as e:
            raise BusinessError(f"RADIUS configuration failed: {str(e)}")

    # =========================================================================
    # CONNECTION STATUS
    # =========================================================================

    def get_connection_status(
        self, router_id: UUID, organization_id: UUID
    ) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        settings = router.settings or {}
        health = settings.get('health', {})
        return {
            'router_id': str(router.id), 'name': router.name,
            'ip_address': str(router.ip_address), 'local_ip': router.local_ip,
            'status': router.status,
            'wireguard_ip': router.wireguard_ip,
            'radius_config_status': router.radius_config_status,
            'auto_config_attempts': router.auto_config_attempts or 0,
            'last_seen_at': router.last_seen_at.isoformat() if router.last_seen_at else None,
            'last_sync_at': router.last_sync_at.isoformat() if router.last_sync_at else None,
            'is_active': router.is_active,
            'health': health,
            'has_error': bool(router.last_config_error),
            'last_error': router.last_config_error,
        }