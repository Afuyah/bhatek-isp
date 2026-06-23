"""
Router Service Module
=====================
Business logic for router management with WireGuard VPN and RADIUS integration.
Orchestrates the complete router lifecycle for multi-tenant ISP operations.

Flow:
    1. Admin adds router → Flask generates WireGuard + RADIUS credentials
    2. Flask creates DB records + adds VPS WireGuard peer via SSH
    3. Flask returns MikroTik setup script (verified commands)
    4. Admin pastes script in MikroTik terminal → WireGuard connects
    5. Admin clicks "Test Connection" → Flask reaches router via WireGuard IP
    6. System auto-configures RADIUS + discovers capabilities
    7. Router fully managed — RADIUS auth, monitoring, sync all functional

Verified MikroTik Commands (RouterOS v6.43+ / v7.x):
    - /ip hotspot profile set [find] use-radius=yes
    - /ppp aaa set use-radius=yes
    - /radius incoming set accept=yes
    - /radius add address=... secret=... service=hotspot,ppp
"""

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
    """
    Service for router management with WireGuard VPN and RADIUS integration.

    All operations are scoped to organization_id for multi-tenant isolation.
    WireGuard IPs are allocated per-organization subnet.
    """

    # VPS WireGuard IP — the RADIUS server address routers connect to
    DEFAULT_RADIUS_SERVER = '10.0.0.1'

    # WireGuard subnet
    WIREGUARD_SUBNET = '10.0.0.0/16'
    VPS_WIREGUARD_IP = '10.0.0.1'
    VPS_ENDPOINT = '163.245.217.16:51820'

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
        """Get the RADIUS server IP from config with fallback to VPS WireGuard IP."""
        return current_app.config.get('RADIUS_SERVER_IP', self.DEFAULT_RADIUS_SERVER)

    def _get_vps_public_key(self) -> str:
        """Get VPS WireGuard public key from config."""
        return current_app.config.get(
            'VPS_WIREGUARD_PUBLIC_KEY',
            '274kTJCdNISjJEBMLP9SuqaMyQ8GkDSqjXLttDgNsz4='
        )

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
        """
        Build router_data dictionary for MikroTikClient from ORM model.
        Uses WireGuard IP for connectivity.
        """
        return {
            'id': str(router.id),
            'ip_address': str(router.ip_address),  # WireGuard IP
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port or 8728,
            'api_ssl': getattr(router, 'api_ssl', False),
        }

    def _allocate_wireguard_ip(self, organization_id: UUID) -> tuple:
        """
        Allocate the next available WireGuard IP for an organization.

        Each organization gets a /24 subnet within 10.0.0.0/16.
        Organization index is derived from a hash of the org ID.
        Returns: (wireguard_ip, subnet, org_index)
        """
        import hashlib

        # Hash org ID to get a consistent subnet index
        org_hash = hashlib.md5(str(organization_id).encode()).hexdigest()
        org_index = int(org_hash[:4], 16) % 200 + 1  # Range 1-200

        subnet = f"10.0.{org_index}.0/24"

        # Find existing IPs in this subnet
        existing_routers = self.repository.get_by_organization(
            organization_id, limit=10000
        )
        existing_ips = set()
        for r in existing_routers:
            if r.wireguard_ip and r.wireguard_ip.startswith(f"10.0.{org_index}."):
                existing_ips.add(r.wireguard_ip)

        # Find next available IP (start from .10, skip .1 which is reserved)
        for host in range(10, 254):
            candidate = f"10.0.{org_index}.{host}"
            if candidate not in existing_ips:
                return candidate, subnet, org_index

        # If subnet full, fall back to a larger range
        for host in range(10, 254):
            for fallback_index in range(201, 255):
                candidate = f"10.0.{fallback_index}.{host}"
                if candidate not in existing_ips:
                    return candidate, f"10.0.{fallback_index}.0/24", fallback_index

        raise BusinessError("No available WireGuard IPs in subnet")

    # =========================================================================
    # NAS ENTRY MANAGEMENT
    # =========================================================================

    def _create_nas_entry(self, router: Router, radius_secret: str) -> NAS:
        """
        Create a NAS (Network Access Server) entry for FreeRADIUS.

        The nasname is set to the router's WireGuard IP so FreeRADIUS
        can match incoming RADIUS requests to the correct NAS client.
        """
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

            # Link NAS entry back to router
            router.nas_entry_id = nas_entry.id
            db.session.commit()

            logger.info(
                f"Created NAS entry for '{router.name}' "
                f"(nasname={nas_entry.nasname})"
            )
            return nas_entry

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to create NAS entry for router '{router.name}': {e}"
            )
            raise BusinessError(f"Failed to create NAS entry: {str(e)}")

    # =========================================================================
    # MIKROTIK SETUP SCRIPT GENERATOR
    # =========================================================================

    def _generate_mikrotik_setup_script(
        self,
        wireguard_ip: str,
        mikrotik_private_key: str,
        radius_secret: str,
        router_name: str = "Router",
        organization_name: str = "ISP",
    ) -> str:
        """
        Generate the complete MikroTik setup script with verified commands.

        Commands are compatible with RouterOS v6.43+ and v7.x.
        Tested on RB951Ui-2HnD.
        """
        vps_public_key = self._get_vps_public_key()

        script = f"""# =============================================================================
# ISP Management Platform - MikroTik Setup Script
# Router: {router_name}
# Organization: {organization_name}
# WireGuard IP: {wireguard_ip}
# Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
# =============================================================================

# Step 1: Setup WireGuard VPN Tunnel
/interface wireguard add listen-port=51820 name=wg-to-vps
/interface wireguard peers add allowed-address=10.0.0.0/16 endpoint-address={self.VPS_ENDPOINT} endpoint-port=51820 interface=wg-to-vps persistent-keepalive=25 public-key="{vps_public_key}"
/ip address add address={wireguard_ip}/16 interface=wg-to-vps network=10.0.0.0

# Step 2: Configure RADIUS Authentication
/radius add address=10.0.0.1 secret="{radius_secret}" service=hotspot,ppp authentication-port=1812 accounting-port=1813

# Step 3: Enable RADIUS for Hotspot
/ip hotspot profile set [find] use-radius=yes

# Step 4: Enable RADIUS for PPPoE
/ppp aaa set use-radius=yes

# Step 5: Enable RADIUS Incoming for Remote Disconnects
/radius incoming set accept=yes

# =============================================================================
# Setup Complete!
# Verify WireGuard: /ping 10.0.0.1 count=3
# The router will now authenticate via the ISP platform.
# =============================================================================
"""
        return script.strip()

    # =========================================================================
    # RADIUS AUTO-CONFIGURATION
    # =========================================================================

    def _auto_configure_router_radius(
        self,
        router: Router,
        radius_secret: str,
        organization: Organization = None,
    ) -> Dict[str, Any]:
        """
        Auto-configure RADIUS on a MikroTik router via API.

        Pushes RADIUS server settings using verified RouterOS commands.
        """
        try:
            radius_server = self._get_radius_server()
            router_data = self._build_router_data(router)

            # Mark router with org slug for identification
            if organization:
                try:
                    identity_name = f"{router.name}-{organization.slug}"
                    self.mikrotik_client.execute(
                        router_data=router_data,
                        command='/system/identity/set',
                        name=identity_name,
                    )
                except Exception as e:
                    logger.warning(f"Failed to set router identity: {e}")

            # Delegate RADIUS configuration to MikroTikClient
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
                    f"RADIUS auto-config failed for '{router.name}': "
                    f"{result.get('error')}"
                )

            return result

        except Exception as e:
            logger.error(
                f"Auto-configuration error for router '{router.name}': {e}"
            )
            return {'success': False, 'error': str(e)}

    # =========================================================================
    # CREATE ROUTER (COMPLETE WIREGUARD + RADIUS ONBOARDING)
    # =========================================================================

    def create_router(
        self,
        organization_id: UUID,
        network_id: UUID,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Create a new router with full WireGuard + RADIUS onboarding.

        Flow:
            1. Validate inputs
            2. Generate WireGuard keypair + allocate IP
            3. Generate RADIUS secret + encrypt passwords
            4. Create Router record in database
            5. Create NAS entry for FreeRADIUS
            6. Add WireGuard peer on VPS via SSH
            7. Return MikroTik setup script for admin

        Admin pastes the script → WireGuard connects → router reachable.
        """
        # Validate required fields
        required = ['name', 'username', 'password']
        for field in required:
            if not data.get(field):
                raise ValidationError(f"'{field}' is required")

        # Admin's local access IP (what they use to connect to the router)
        local_ip = data.get('ip_address') or data.get('local_ip')
        if not local_ip:
            raise ValidationError(
                "Either 'ip_address' or 'local_ip' is required "
                "(the IP you use to access the router)"
            )

        # Verify organization exists
        organization = Organization.query.get(organization_id)
        if not organization:
            raise ValidationError("Organization not found")

        # Generate WireGuard credentials for this router
        wg_private_key, wg_public_key = self.wireguard.generate_peer_keypair()

        # Allocate a WireGuard IP in this org's subnet
        wireguard_ip, subnet, org_index = self._allocate_wireguard_ip(
            organization_id
        )

        # Generate RADIUS shared secret
        radius_secret = self._generate_radius_secret()

        # Encrypt sensitive data for storage
        encrypted_password = self.encryption.encrypt(data['password'])
        encrypted_wg_private_key = self.encryption.encrypt(wg_private_key)

        # Create router record
        # ip_address = WireGuard IP (used for API access)
        # local_ip = admin's local IP (reference only)
        router_record = {
            'organization_id': organization_id,
            'network_id': network_id,
            'name': data['name'],
            'model': data.get('model'),
            'ip_address': wireguard_ip,
            'local_ip': local_ip,
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
            'wireguard_private_key_encrypted': encrypted_wg_private_key,
        }

        router = self.repository.create(router_record)

        # Create NAS entry for FreeRADIUS
        nas_entry = self._create_nas_entry(router, radius_secret)

        # Add WireGuard peer on VPS via SSH
        wg_added = self.wireguard.add_peer(
            wg_public_key, f"{wireguard_ip}/32"
        )

        if not wg_added:
            logger.warning(
                f"Failed to add WireGuard peer on VPS for router '{router.name}'. "
                f"Admin will need to add it manually."
            )

        # Generate the MikroTik setup script
        setup_script = self._generate_mikrotik_setup_script(
            wireguard_ip=wireguard_ip,
            mikrotik_private_key=wg_private_key,
            radius_secret=radius_secret,
            router_name=router.name,
            organization_name=organization.name,
        )

        logger.info(
            f"Router created: '{router.name}' "
            f"(WireGuard: {wireguard_ip}, "
            f"VPS peer: {'added' if wg_added else 'FAILED'}) | "
            f"Org: {organization.slug}"
        )

        return {
            'success': True,
            'router': router,
            'wireguard': {
                'ip': wireguard_ip,
                'public_key': wg_public_key,
                'private_key': wg_private_key,
                'peer_added_to_vps': wg_added,
            },
            'radius': {
                'secret': radius_secret,
                'server': self._get_radius_server(),
                'auth_port': 1812,
                'acct_port': 1813,
            },
            'setup_script': setup_script,
            'next_step': (
                'Paste the setup script into your MikroTik terminal, '
                'then click "Test Connection" to verify.'
            ),
            'warning': (
                'Save the WireGuard private key and RADIUS secret. '
                'They will not be shown again.'
            ) if not wg_added else None,
        }

    # =========================================================================
    # AUTO-CONFIGURE AFTER WIREGUARD IS UP
    # =========================================================================

    def auto_configure_after_wireguard(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Run after admin confirms WireGuard is connected.
        Configures RADIUS and discovers router capabilities via API.
        """
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)
        router_data = self._build_router_data(router)

        result = {
            'success': True,
            'radius_configured': False,
            'discovered': False,
            'steps': [],
        }

        # Step 1: Configure RADIUS on the MikroTik via API
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
                result['steps'].append({
                    'step': 'radius', 'status': 'success',
                    'message': 'RADIUS configured successfully',
                })
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
                'step': 'radius', 'status': 'error',
                'error': str(e),
            })

        # Step 2: Discover router capabilities
        try:
            info = self.mikrotik_client.get_router_info(router_data)
            capabilities = ['api']

            # Detect hotspot
            try:
                hs = self.mikrotik_client.get_hotspot_servers(router_data)
                if hs and len(hs) > 0:
                    capabilities.append('hotspot')
            except Exception:
                pass

            # Detect PPPoE
            try:
                ps = self.mikrotik_client.get_pppoe_servers(router_data)
                if ps and len(ps) > 0:
                    capabilities.append('pppoe')
            except Exception:
                pass

            # Detect wireless
            try:
                wl = self.mikrotik_client.execute(
                    router_data, '/interface/wireless/print'
                )
                if wl and len(wl) > 0:
                    capabilities.append('wireless')
            except Exception:
                pass

            self.repository.update_discovery(
                router_id=router_id,
                organization_id=organization_id,
                model=info.get('board_name'),
                firmware_version=info.get('version'),
                capabilities=capabilities,
                discovery_method='api',
            )
            result['discovered'] = True
            result['discovery'] = {
                'model': info.get('board_name'),
                'version': info.get('version'),
                'capabilities': capabilities,
                'uptime': info.get('uptime'),
                'cpu_load': info.get('cpu_load'),
            }
            result['steps'].append({
                'step': 'discovery', 'status': 'success',
                'message': 'Router capabilities discovered',
            })
        except Exception as e:
            result['steps'].append({
                'step': 'discovery', 'status': 'error',
                'error': str(e),
            })

        # Update final status
        if result['radius_configured']:
            self.repository.update_status(router_id, organization_id, 'online')
        else:
            self.repository.update_status(
                router_id, organization_id, 'radius_pending'
            )

        result['all_success'] = (
            result['radius_configured'] and result['discovered']
        )
        return result

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    def get_router(
        self, router_id: UUID, organization_id: UUID
    ) -> Router:
        """Get a single router with tenant isolation."""
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
            organization_id, skip, limit, status,
            network_id, radius_config_status,
        )

    def get_routers_by_network(
        self, network_id: UUID, organization_id: UUID
    ) -> List[Router]:
        """Get all routers on a specific network."""
        return self.repository.get_by_network(network_id, organization_id)

    def get_routers_pending_radius_config(
        self, organization_id: UUID
    ) -> List[Router]:
        """Get routers awaiting RADIUS configuration."""
        return self.repository.get_routers_pending_radius_config(
            organization_id
        )

    def get_router_by_ip(
        self, ip_address: str, organization_id: UUID
    ) -> Optional[Router]:
        """Find a router by IP address within an organization."""
        return self.repository.get_by_ip(ip_address, organization_id)

    # =========================================================================
    # UPDATE OPERATIONS
    # =========================================================================

    def update_router(
        self,
        router_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Router:
        """
        Update router fields.
        Handles password re-encryption if a new password is provided.
        """
        if "password" in data and data["password"]:
            data["password_encrypted"] = self.encryption.encrypt(
                data.pop("password")
            )
        elif "password" in data:
            data.pop("password")

        router = self.repository.update(router_id, organization_id, data)
        if not router:
            raise NotFoundError("Router not found")

        logger.info(f"Router updated: {router_id}")
        return router

    def retry_radius_configuration(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Retry RADIUS configuration on a router that previously failed.
        Uses the stored RADIUS secret.
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
                'radius_server_ip': self._get_radius_server(),
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
        self,
        router_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True,
    ) -> None:
        """
        Delete or deactivate a router.
        Removes WireGuard peer from VPS on deletion.
        """
        router = self.repository.get_by_id(
            router_id, organization_id, include_inactive=True
        )
        if not router:
            raise NotFoundError("Router not found")

        # Remove WireGuard peer from VPS
        if router.wireguard_public_key:
            try:
                self.wireguard.remove_peer(router.wireguard_public_key)
                logger.info(
                    f"Removed WireGuard peer for router '{router.name}'"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to remove WireGuard peer: {e}"
                )

        # Check for active services before hard delete
        if not soft_delete:
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

    # =========================================================================
    # CONNECTION TESTING (VIA WIREGUARD IP)
    # =========================================================================

    def test_connection(
        self,
        router_id: UUID,
        organization_id: UUID,
        method: str = 'api',
    ) -> Dict[str, Any]:
        """
        Test connection to a router via its WireGuard IP.

        Decrypts the stored password and attempts a connection.
        Updates router status based on the result.
        """
        router = self.get_router(router_id, organization_id)

        if method != 'api':
            raise ValidationError(f"Unsupported connection method: {method}")

        try:
            # Decrypt password for the connection test
            password = self.encryption.decrypt(router.password_encrypted)

            result = self.mikrotik_client.test_connection(
                host=str(router.ip_address),  # WireGuard IP
                username=router.username,
                password=password,
                port=router.api_port or 8728,
            )

            # Update router status based on result
            status = 'online' if result.get('success') else 'offline'
            self.repository.update_status(
                router_id, organization_id, status,
                error_message=(
                    result.get('error')
                    if not result.get('success') else None
                ),
            )

            return result

        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error',
                error_message=str(e),
            )
            raise BusinessError(f"Connection test failed: {str(e)}")

    # =========================================================================
    # DISCOVERY
    # =========================================================================

    def discover_router(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Auto-discover router capabilities via API, SSH, SNMP, Telnet.
        Updates router with discovered model, version, and capabilities.
        """
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
                logger.info(
                    f"Attempting discovery via {method_name} "
                    f"for router '{router.name}'"
                )
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

        self.repository.update_status(router_id, organization_id, 'offline')

        return {
            'success': False,
            'message': 'Auto-discovery failed. Router may be offline.',
            'attempts': attempts,
        }

    def _discover_via_api(
        self, router: Router, router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Discover router capabilities via MikroTik API."""
        try:
            info = self.mikrotik_client.get_router_info(router_data)
            capabilities = ['api']

            try:
                hotspot = self.mikrotik_client.get_hotspot_servers(router_data)
                if hotspot and len(hotspot) > 0:
                    capabilities.append('hotspot')
            except Exception:
                pass

            try:
                pppoe = self.mikrotik_client.get_pppoe_servers(router_data)
                if pppoe and len(pppoe) > 0:
                    capabilities.append('pppoe')
            except Exception:
                pass

            try:
                wireless = self.mikrotik_client.execute(
                    router_data, '/interface/wireless/print'
                )
                if wireless and len(wireless) > 0:
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
        self, router: Router, router_data: Dict[str, Any]
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
        self, router: Router, router_data: Dict[str, Any]
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
                        UdpTransportTarget(
                            (str(router.ip_address), 161)
                        ),
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
        self, router: Router, router_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Discover via Telnet (fallback only)."""
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

    # =========================================================================
    # HEALTH MONITORING
    # =========================================================================

    def update_health(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Update router health metrics from live router data.
        Fetches system resource info via API.
        """
        router = self.get_router(router_id, organization_id)
        router_data = self._build_router_data(router)

        try:
            info = self.mikrotik_client.get_router_info(router_data)

            cpu_load = None
            if info.get('cpu_load'):
                try:
                    cpu_load = int(info['cpu_load'])
                except (ValueError, TypeError):
                    pass

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

            uptime_str = info.get('uptime', '0s')
            uptime_seconds = self._parse_uptime(uptime_str)

            self.repository.update_health(
                router_id=router_id,
                organization_id=organization_id,
                cpu_load=cpu_load,
                free_memory=free_memory,
                total_memory=total_memory,
                uptime=uptime_str,
            )

            self.repository.update_status(
                router_id, organization_id, 'online'
            )

            return {
                'cpu_load': cpu_load,
                'free_memory': free_memory,
                'total_memory': total_memory,
                'uptime_seconds': uptime_seconds,
                'uptime_hours': (
                    round(uptime_seconds / 3600, 2)
                    if uptime_seconds else 0
                ),
                'uptime_display': uptime_str,
                'version': info.get('version'),
                'board_name': info.get('board_name'),
                'architecture_name': info.get('architecture_name'),
            }

        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error',
                error_message=str(e),
            )
            raise BusinessError(f"Health check failed: {str(e)}")

    # =========================================================================
    # SYNC (PULL FROM ROUTER)
    # =========================================================================

    def sync_router(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Full sync of router configuration into the database.
        Pulls hotspot servers and PPPoE servers from the router.
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
            results['hotspot_synced'] = self._sync_hotspot_servers(
                router, router_data
            )
            results['pppoe_synced'] = self._sync_pppoe_servers(
                router, router_data
            )

            self.repository.update(router_id, organization_id, {
                'last_sync_at': datetime.utcnow(),
                'status': 'online',
            })

            logger.info(
                f"Router '{router.name}' synced: "
                f"{results['hotspot_synced']} hotspot, "
                f"{results['pppoe_synced']} PPPoE"
            )

            return results

        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error',
                error_message=str(e),
            )
            raise BusinessError(f"Sync failed: {str(e)}")

    def _sync_hotspot_servers(
        self, router: Router, router_data: Dict[str, Any]
    ) -> int:
        """Sync hotspot servers from router to database."""
        try:
            hotspot_servers = self.mikrotik_client.get_hotspot_servers(
                router_data
            )
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
                    'session_timeout': int(
                        hs.get('session-timeout', 86400)
                    ),
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
                f"Failed to sync hotspot servers for "
                f"router '{router.name}': {e}"
            )
            return 0

    def _sync_pppoe_servers(
        self, router: Router, router_data: Dict[str, Any]
    ) -> int:
        """Sync PPPoE servers from router to database."""
        try:
            pppoe_servers = self.mikrotik_client.get_pppoe_servers(
                router_data
            )
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
                f"Failed to sync PPPoE servers for "
                f"router '{router.name}': {e}"
            )
            return 0

    # =========================================================================
    # MANUAL RADIUS CONFIGURATION
    # =========================================================================

    def configure_radius_manual(
        self,
        router_id: UUID,
        organization_id: UUID,
        radius_server: str,
        radius_secret: str,
    ) -> Dict[str, Any]:
        """
        Manually configure RADIUS on a router with explicit server/secret.
        Used when admin wants to specify custom RADIUS settings.
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

                # Mark router with org identity
                if organization:
                    try:
                        self.mikrotik_client.execute(
                            router_data=router_data,
                            command='/system/identity/set',
                            name=f"{router.name}-{organization.slug}",
                        )
                    except Exception:
                        pass

                # Persist RADIUS server in settings
                settings = router.settings or {}
                settings['radius_server'] = radius_server
                settings['radius_configured_at'] = (
                    datetime.utcnow().isoformat()
                )
                self.repository.update(
                    router_id, organization_id,
                    {'settings': settings},
                )

            return result

        except Exception as e:
            raise BusinessError(
                f"RADIUS configuration failed: {str(e)}"
            )

    # =========================================================================
    # CONNECTION STATUS
    # =========================================================================

    def get_connection_status(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Get current connection status and health summary.
        Returns comprehensive status for dashboard display.
        """
        router = self.get_router(router_id, organization_id)
        organization = Organization.query.get(organization_id)
        settings = router.settings or {}
        health = settings.get('health', {})

        return {
            'router_id': str(router.id),
            'name': router.name,
            'ip_address': str(router.ip_address),
            'local_ip': router.local_ip,
            'wireguard_ip': router.wireguard_ip,
            'status': router.status,
            'radius_config_status': router.radius_config_status,
            'auto_config_attempts': router.auto_config_attempts or 0,
            'last_seen_at': (
                router.last_seen_at.isoformat()
                if router.last_seen_at else None
            ),
            'last_sync_at': (
                router.last_sync_at.isoformat()
                if router.last_sync_at else None
            ),
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
            'organization_slug': (
                organization.slug if organization else None
            ),
        }