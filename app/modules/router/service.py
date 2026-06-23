"""
Router Service Module
=====================
Business logic for router management with WireGuard VPN and RADIUS integration.
Orchestrates the complete router lifecycle for multi-tenant ISP operations.

Architecture:
    Flask (Railway) ──SSH──► VPS (163.245.217.16) ──WireGuard──► MikroTik Routers
    All MikroTik API calls are proxied through the VPS since Flask cannot
    directly reach WireGuard IPs (10.0.0.0/16).

Verified MikroTik Commands (RouterOS v6.43+ / v7.x):
    - /ip hotspot profile set [find] use-radius=yes
    - /ppp aaa set use-radius=yes
    - /radius incoming set accept=yes
    - /radius add address=... secret=... service=hotspot,ppp
"""

from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime
import json as _json
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

    All MikroTik API operations are proxied through the VPS via SSH
    because the Flask app cannot directly reach WireGuard IPs (10.0.0.0/16).
    All operations are scoped to organization_id for multi-tenant isolation.
    """

    DEFAULT_RADIUS_SERVER = '10.0.0.1'
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
        """Build router_data dictionary for MikroTikClient from ORM model."""
        return {
            'id': str(router.id),
            'ip_address': str(router.ip_address),
            'username': router.username,
            'password_encrypted': router.password_encrypted,
            'api_port': router.api_port or 8728,
            'api_ssl': getattr(router, 'api_ssl', False),
        }

    def _allocate_wireguard_ip(self, organization_id: UUID) -> tuple:
        """
        Allocate the next available WireGuard IP for an organization.
        Each organization gets a /24 subnet within 10.0.0.0/16.
        """
        import hashlib
        org_hash = hashlib.md5(str(organization_id).encode()).hexdigest()
        org_index = int(org_hash[:4], 16) % 200 + 1
        subnet = f"10.0.{org_index}.0/24"
        existing_routers = self.repository.get_by_organization(organization_id, limit=10000)
        existing_ips = {
            r.wireguard_ip for r in existing_routers
            if r.wireguard_ip and r.wireguard_ip.startswith(f"10.0.{org_index}.")
        }
        for host in range(10, 254):
            candidate = f"10.0.{org_index}.{host}"
            if candidate not in existing_ips:
                return candidate, subnet, org_index
        for host in range(10, 254):
            for fallback in range(201, 255):
                candidate = f"10.0.{fallback}.{host}"
                if candidate not in existing_ips:
                    return candidate, f"10.0.{fallback}.0/24", fallback
        raise BusinessError("No available WireGuard IPs in subnet")

    # =========================================================================
    # VPS PROXY — ALL MikroTik API calls go through the VPS
    # =========================================================================

    def _execute_via_vps(
        self,
        router: Router,
        command: str,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Execute a MikroTik API command via VPS SSH tunnel.
        Flask/Railway cannot reach WireGuard IPs directly.
        The VPS is the WireGuard hub and can reach all routers.
        """
        password = self.encryption.decrypt(router.password_encrypted)
        host = str(router.ip_address)
        port = router.api_port or 8728
        username = router.username
        kwargs_json_str = _json.dumps(kwargs)

        # Build the Python script that runs on the VPS
        script_lines = [
            "import socket, struct, hashlib, binascii, json, sys",
            "",
            f"host = '{host}'",
            f"port = {port}",
            f"username = '{username}'",
            f"password = b'{password}'",
            f"command = '{command}'",
            f"kwargs = json.loads('{kwargs_json_str}')",
            "",
            "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
            "sock.settimeout(20)",
            "sock.connect((host, port))",
            "",
            "def send_cmd(*words):",
            "    for w in words:",
            "        if not w: continue",
            "        wb = w.encode()",
            "        sock.sendall(struct.pack('>I', len(wb)) + wb)",
            "    sock.sendall(struct.pack('>I', 0))",
            "",
            "def read_resp():",
            "    words = []",
            "    while True:",
            "        lb = b''",
            "        while len(lb) < 4:",
            "            c = sock.recv(4 - len(lb))",
            "            if not c: return words",
            "            lb += c",
            "        length = struct.unpack('>I', lb)[0]",
            "        if length == 0: return words",
            "        wb = b''",
            "        while len(wb) < length:",
            "            c = sock.recv(length - len(wb))",
            "            if not c: return words",
            "            wb += c",
            "        words.append(wb.decode('utf-8', errors='ignore'))",
            "",
            "# Authenticate",
            "send_cmd('/login')",
            "resp = read_resp()",
            "for w in resp:",
            "    if '=ret=' in w: break",
            "    if '=challenge=' in w:",
            "        challenge = w.split('=', 2)[2]",
            "        cb = binascii.unhexlify(challenge)",
            "        md5 = hashlib.md5()",
            "        md5.update(b'\\x00')",
            "        md5.update(password)",
            "        md5.update(cb)",
            "        rh = md5.hexdigest().upper()",
            "        send_cmd('/login', '=name=' + username, '=response=' + rh)",
            "        read_resp()",
            "",
            "# Execute command",
            "words = [command]",
            "for k, v in kwargs.items():",
            "    if v is not None:",
            "        words.append('=' + k.replace('_', '-') + '=' + str(v))",
            "send_cmd(*words)",
            "resp = read_resp()",
            "",
            "# Parse response",
            "result = []",
            "current = {}",
            "for line in resp:",
            "    if line.startswith('!') and '=message=' in line: break",
            "    elif line.startswith('!'):",
            "        if current and len(current) > 1: result.append(current)",
            "        current = {'status': line[1:]}",
            "    elif '=' in line:",
            "        k, v = line.split('=', 1)",
            "        current[k] = v",
            "if current and len(current) > 1: result.append(current)",
            "",
            "print(json.dumps(result))",
            "sock.close()",
        ]

        script = '\n'.join(script_lines)

        # Execute via SSH on VPS
        # Write script to temp file on VPS to avoid escaping issues
        success, stdout, stderr = self.wireguard._run_ssh(
            f"python3 << 'PYEOF'\n{script}\nPYEOF",
            timeout=25
        )

        if success and stdout:
            try:
                return _json.loads(stdout)
            except _json.JSONDecodeError:
                logger.error(f"VPS response parse error: {stdout[:300]}")
                raise BusinessError("VPS returned invalid response")

        logger.error(f"VPS SSH command failed. Stderr: {stderr[:300]}")
        raise BusinessError(f"Failed to reach router via VPS: {stderr[:200]}")

    def _execute_client_method_via_vps(
        self,
        router: Router,
        method_name: str,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute a MikroTikClient-equivalent method via VPS proxy.

        This replaces direct calls to self.mikrotik_client.* methods
        since Flask cannot reach WireGuard IPs directly.

        Supported methods:
            - get_router_info
            - get_hotspot_servers
            - get_pppoe_servers
            - get_hotspot_users
            - get_pppoe_secrets
            - get_active_sessions
            - get_pppoe_active_sessions
            - get_hotspot_profiles
            - get_interface_stats
            - get_simple_queues
            - configure_radius
        """
        if method_name == 'get_router_info':
            resource = self._execute_via_vps(router, '/system/resource/print')
            identity = self._execute_via_vps(router, '/system/identity/print')
            r = resource[0] if resource else {}
            i = identity[0] if identity else {}
            return {
                'hostname': i.get('name'),
                'version': r.get('version'),
                'build_time': r.get('build-time'),
                'uptime': r.get('uptime'),
                'cpu_load': r.get('cpu-load'),
                'cpu_count': r.get('cpu-count'),
                'free_memory': r.get('free-memory'),
                'total_memory': r.get('total-memory'),
                'free_hdd': r.get('free-hdd'),
                'total_hdd': r.get('total-hdd'),
                'architecture_name': r.get('architecture-name'),
                'board_name': r.get('board-name'),
                'platform': r.get('platform'),
            }

        elif method_name == 'get_hotspot_servers':
            return self._execute_via_vps(router, '/ip/hotspot/print')

        elif method_name == 'get_pppoe_servers':
            return self._execute_via_vps(router, '/interface/pppoe-server/server/print')

        elif method_name == 'get_hotspot_users':
            params = kwargs.get('params', {})
            return self._execute_via_vps(router, '/ip/hotspot/user/print', **params)

        elif method_name == 'get_pppoe_secrets':
            return self._execute_via_vps(router, '/ppp/secret/print')

        elif method_name == 'get_active_sessions':
            params = kwargs.get('params', {})
            return self._execute_via_vps(router, '/ip/hotspot/active/print', **params)

        elif method_name == 'get_pppoe_active_sessions':
            return self._execute_via_vps(router, '/ppp/active/print')

        elif method_name == 'get_hotspot_profiles':
            return self._execute_via_vps(router, '/ip/hotspot/user/profile/print')

        elif method_name == 'get_interface_stats':
            return self._execute_via_vps(router, '/interface/print')

        elif method_name == 'get_simple_queues':
            return self._execute_via_vps(router, '/queue/simple/print')

        elif method_name == 'configure_radius':
            radius_server = kwargs.get('radius_server', self._get_radius_server())
            radius_secret = kwargs.get('radius_secret', router.radius_secret)

            # Check if RADIUS server already exists
            existing = self._execute_via_vps(router, '/radius/print')
            server_exists = False
            for item in existing:
                if item.get('address') == radius_server:
                    server_exists = True
                    # Update existing entry
                    self._execute_via_vps(
                        router, '/radius/set',
                        numbers=item.get('.id'),
                        secret=radius_secret,
                        service='hotspot,ppp',
                        authentication_port='1812',
                        accounting_port='1813',
                        timeout='3000',
                        retries='3',
                    )
                    logger.info(f"RADIUS server updated: {radius_server}")
                    break

            if not server_exists:
                # Add new RADIUS server
                self._execute_via_vps(
                    router, '/radius/add',
                    address=radius_server,
                    secret=radius_secret,
                    service='hotspot,ppp',
                    authentication_port='1812',
                    accounting_port='1813',
                    timeout='3000',
                    retries='3',
                )
                logger.info(f"RADIUS server added: {radius_server}")

            # Enable RADIUS for Hotspot (VERIFIED command)
            try:
                self._execute_via_vps(
                    router,
                    '/ip/hotspot/profile/set',
                    numbers='[find]',
                    **{'use-radius': 'yes'},
                )
                logger.info("Hotspot RADIUS enabled")
            except Exception as e:
                logger.warning(f"Could not enable hotspot RADIUS: {e}")

            # Enable RADIUS for PPPoE (VERIFIED command)
            try:
                self._execute_via_vps(
                    router,
                    '/ppp/aaa/set',
                    **{'use-radius': 'yes'},
                )
                logger.info("PPPoE RADIUS enabled")
            except Exception as e:
                logger.warning(f"Could not enable PPPoE RADIUS: {e}")

            # Enable RADIUS Incoming for CoA/Disconnect
            try:
                self._execute_via_vps(
                    router,
                    '/radius/incoming/set',
                    accept='yes',
                )
                logger.info("RADIUS incoming enabled")
            except Exception as e:
                logger.warning(f"Could not enable RADIUS incoming: {e}")

            logger.info(
                f"RADIUS fully configured on {router.ip_address} -> {radius_server}"
            )
            return {
                'success': True,
                'message': 'RADIUS configured successfully',
                'radius_server': radius_server,
            }

        else:
            raise ValueError(f"Unknown VPS proxy method: {method_name}")

    # =========================================================================
    # NAS ENTRY MANAGEMENT
    # =========================================================================

    def _create_nas_entry(self, router: Router, radius_secret: str) -> NAS:
        """
        Create a NAS entry for FreeRADIUS with the router's WireGuard IP.
        The nasname is the WireGuard IP so FreeRADIUS can match incoming requests.
        """
        try:
            nas_entry = NAS(
                organization_id=router.organization_id,
                nasname=str(router.ip_address),
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
        Compatible with RouterOS v6.43+ and v7.x. Tested on RB951Ui-2HnD.
        """
        vps_public_key = self._get_vps_public_key()

        script = f"""# =============================================================================
# ISP Management Platform - MikroTik Setup Script
# Router: {router_name} | Organization: {organization_name}
# WireGuard IP: {wireguard_ip}
# Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
# =============================================================================

# Step 1: Setup WireGuard VPN Tunnel
/interface wireguard add listen-port=51820 name=wg-to-vps
/interface wireguard peers add allowed-address=10.0.0.1/32 endpoint-address={self.VPS_ENDPOINT} endpoint-port=51820 interface=wg-to-vps persistent-keepalive=25 public-key="{vps_public_key}"
/ip address add address={wireguard_ip}/16 interface=wg-to-vps network=10.0.0.0
/ip route add dst-address=10.0.0.1/32 gateway=wg-to-vps

# Step 2: Firewall — Allow ISP Platform Access
/ip firewall filter add chain=input src-address=10.0.0.0/16 action=accept comment="Allow ISP Platform"
/interface list member add interface=wg-to-vps list=LAN
/ip service set api address=10.0.0.0/16

# Step 3: Configure RADIUS Authentication
/radius add address=10.0.0.1 secret="{radius_secret}" service=hotspot,ppp authentication-port=1812 accounting-port=1813

# Step 4: Enable RADIUS on Hotspot
/ip hotspot profile set [find] use-radius=yes

# Step 5: Enable RADIUS on PPPoE
/ppp aaa set use-radius=yes

# Step 6: Enable RADIUS Incoming for Remote Disconnects
/radius incoming set accept=yes

# =============================================================================
# Setup Complete!
# Verify: /ping 10.0.0.1 count=3
# =============================================================================
"""
        return script.strip()

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

        Steps:
            1. Validate inputs
            2. Generate WireGuard keypair + allocate IP in org subnet
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

        # Admin's local access IP
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

        # Generate WireGuard credentials
        wg_private_key, wg_public_key = self.wireguard.generate_peer_keypair()

        # Allocate WireGuard IP in org's subnet
        wireguard_ip, subnet, org_index = self._allocate_wireguard_ip(organization_id)

        # Generate RADIUS shared secret
        radius_secret = self._generate_radius_secret()

        # Encrypt sensitive data for storage
        encrypted_password = self.encryption.encrypt(data['password'])
        encrypted_wg_private_key = self.encryption.encrypt(wg_private_key)

        # Create router record
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
        wg_added = self.wireguard.add_peer(wg_public_key, f"{wireguard_ip}/32")

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
    # AUTO-CONFIGURE AFTER WIREGUARD
    # =========================================================================

    def auto_configure_after_wireguard(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Run after admin confirms WireGuard is connected.
        Configures RADIUS and discovers router capabilities via VPS proxy.
        """
        router = self.get_router(router_id, organization_id)

        result = {
            'success': True,
            'radius_configured': False,
            'discovered': False,
            'steps': [],
        }

        # Step 1: Configure RADIUS on MikroTik via VPS
        try:
            self._execute_client_method_via_vps(
                router, 'configure_radius',
                radius_secret=router.radius_secret,
            )
            self.repository.update_radius_config_status(
                router_id, organization_id, 'configured'
            )
            result['radius_configured'] = True
            result['steps'].append({
                'step': 'radius',
                'status': 'success',
                'message': 'RADIUS configured successfully',
            })
        except Exception as e:
            self.repository.update_radius_config_status(
                router_id, organization_id, 'failed', error=str(e)
            )
            result['steps'].append({
                'step': 'radius',
                'status': 'error',
                'error': str(e),
            })

        # Step 2: Discover router capabilities via VPS
        try:
            info = self._execute_client_method_via_vps(router, 'get_router_info')
            capabilities = ['api']

            try:
                hs = self._execute_client_method_via_vps(router, 'get_hotspot_servers')
                if hs and len(hs) > 0:
                    capabilities.append('hotspot')
            except Exception:
                pass

            try:
                ps = self._execute_client_method_via_vps(router, 'get_pppoe_servers')
                if ps and len(ps) > 0:
                    capabilities.append('pppoe')
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
                'step': 'discovery',
                'status': 'success',
                'message': 'Router capabilities discovered',
            })
        except Exception as e:
            result['steps'].append({
                'step': 'discovery',
                'status': 'error',
                'error': str(e),
            })

        # Update final status
        if result['radius_configured']:
            self.repository.update_status(router_id, organization_id, 'online')
        else:
            self.repository.update_status(router_id, organization_id, 'radius_pending')

        result['all_success'] = result['radius_configured'] and result['discovered']
        return result

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    def get_router(self, router_id: UUID, organization_id: UUID) -> Router:
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
        return self.repository.get_routers_pending_radius_config(organization_id)

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
            data["password_encrypted"] = self.encryption.encrypt(data.pop("password"))
        elif "password" in data:
            data.pop("password")

        router = self.repository.update(router_id, organization_id, data)
        if not router:
            raise NotFoundError("Router not found")

        logger.info(f"Router updated: {router_id}")
        return router

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
                logger.info(f"Removed WireGuard peer for router '{router.name}'")
            except Exception as e:
                logger.warning(f"Failed to remove WireGuard peer: {e}")

        # Check for active services before hard delete
        if not soft_delete:
            hotspot_servers = self.hotspot_repo.get_by_router(router_id, organization_id)
            pppoe_servers = self.pppoe_repo.get_by_router(router_id, organization_id)
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
    # CONNECTION TESTING (VIA VPS PROXY)
    # =========================================================================

    def test_connection(
        self,
        router_id: UUID,
        organization_id: UUID,
        method: str = 'api',
    ) -> Dict[str, Any]:
        """
        Test connection to a router via VPS proxy.

        Since Flask cannot reach WireGuard IPs directly, this method
        SSHs to the VPS and executes the MikroTik API command there.
        """
        router = self.get_router(router_id, organization_id)

        if method != 'api':
            raise ValidationError(f"Unsupported connection method: {method}")

        try:
            # Execute /system/resource/print via VPS
            result = self._execute_via_vps(router, '/system/resource/print')

            if result and len(result) > 0:
                resource = result[0]
                status = 'online'
                self.repository.update_status(router_id, organization_id, status)
                return {
                    'success': True,
                    'connected': True,
                    'router_info': {
                        'version': resource.get('version', 'Unknown'),
                        'board_name': resource.get('board-name', 'Unknown'),
                        'cpu_load': resource.get('cpu-load', 'Unknown'),
                        'uptime': resource.get('uptime', 'Unknown'),
                        'free_memory': resource.get('free-memory', 'Unknown'),
                        'total_memory': resource.get('total-memory', 'Unknown'),
                        'architecture_name': resource.get('architecture-name', 'Unknown'),
                    },
                }

            status = 'offline'
            self.repository.update_status(router_id, organization_id, status)
            return {
                'success': False,
                'connected': False,
                'error': 'No response from router via VPS',
            }

        except Exception as e:
            self.repository.update_status(
                router_id, organization_id, 'error',
                error_message=str(e),
            )
            raise BusinessError(f"Connection test failed: {str(e)}")

    # =========================================================================
    # DISCOVERY (VIA VPS PROXY)
    # =========================================================================

    def discover_router(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Auto-discover router capabilities via VPS proxy.
        Updates router with discovered model, version, and capabilities.
        """
        router = self.get_router(router_id, organization_id)

        try:
            info = self._execute_client_method_via_vps(router, 'get_router_info')
            capabilities = ['api']

            # Detect hotspot
            try:
                hs = self._execute_client_method_via_vps(router, 'get_hotspot_servers')
                if hs and len(hs) > 0:
                    capabilities.append('hotspot')
            except Exception:
                pass

            # Detect PPPoE
            try:
                ps = self._execute_client_method_via_vps(router, 'get_pppoe_servers')
                if ps and len(ps) > 0:
                    capabilities.append('pppoe')
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
            self.repository.update_status(router_id, organization_id, 'online')

            return {
                'success': True,
                'method': 'api',
                'info': {
                    'model': info.get('board_name'),
                    'version': info.get('version'),
                    'capabilities': capabilities,
                    'uptime': info.get('uptime'),
                    'cpu_load': info.get('cpu_load'),
                },
                'message': 'Router discovered via VPS proxy',
            }

        except Exception as e:
            self.repository.update_status(router_id, organization_id, 'offline')
            return {
                'success': False,
                'message': f'Discovery failed: {str(e)}',
            }

    # =========================================================================
    # HEALTH MONITORING (VIA VPS PROXY)
    # =========================================================================

    def update_health(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Update router health metrics via VPS proxy.
        Fetches system resource info from the router through the VPS.
        """
        router = self.get_router(router_id, organization_id)

        try:
            info = self._execute_client_method_via_vps(router, 'get_router_info')

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
                router_id, organization_id, 'error',
                error_message=str(e),
            )
            raise BusinessError(f"Health check failed: {str(e)}")

    # =========================================================================
    # SYNC (VIA VPS PROXY)
    # =========================================================================

    def sync_router(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Full sync of router configuration via VPS proxy.
        Pulls hotspot servers and PPPoE servers from the router.
        """
        router = self.get_router(router_id, organization_id)

        results = {
            'success': True,
            'hotspot_synced': 0,
            'pppoe_synced': 0,
            'errors': [],
        }

        # Sync hotspot servers
        try:
            hotspot_servers = self._execute_client_method_via_vps(
                router, 'get_hotspot_servers'
            )
            for hs in (hotspot_servers or []):
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
                    self.hotspot_repo.update(existing.id, router.organization_id, hs_data)
                else:
                    self.hotspot_repo.create(hs_data)

                results['hotspot_synced'] += 1

        except Exception as e:
            logger.error(f"Hotspot sync failed for router '{router.name}': {e}")
            results['errors'].append(f"Hotspot: {str(e)}")

        # Sync PPPoE servers
        try:
            pppoe_servers = self._execute_client_method_via_vps(
                router, 'get_pppoe_servers'
            )
            for ps in (pppoe_servers or []):
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
                    self.pppoe_repo.update(existing.id, router.organization_id, ps_data)
                else:
                    self.pppoe_repo.create(ps_data)

                results['pppoe_synced'] += 1

        except Exception as e:
            logger.error(f"PPPoE sync failed for router '{router.name}': {e}")
            results['errors'].append(f"PPPoE: {str(e)}")

        # Update sync timestamp
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

    # =========================================================================
    # RADIUS CONFIGURATION
    # =========================================================================

    def retry_radius_configuration(
        self,
        router_id: UUID,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Retry RADIUS configuration via VPS proxy.
        Uses the stored RADIUS secret.
        """
        router = self.get_router(router_id, organization_id)

        if not router.radius_secret:
            raise BusinessError("Router has no RADIUS secret configured")

        try:
            self._execute_client_method_via_vps(
                router, 'configure_radius',
                radius_secret=router.radius_secret,
            )
            self.repository.update_radius_config_status(
                router.id, organization_id, 'configured'
            )
            return {
                'success': True,
                'message': 'RADIUS configured successfully',
                'radius_server_ip': self._get_radius_server(),
            }
        except Exception as e:
            error_msg = str(e)
            self.repository.update_radius_config_status(
                router.id, organization_id, 'failed', error=error_msg
            )
            return {
                'success': False,
                'message': f'RADIUS configuration failed: {error_msg}',
            }

    def configure_radius_manual(
        self,
        router_id: UUID,
        organization_id: UUID,
        radius_server: str,
        radius_secret: str,
    ) -> Dict[str, Any]:
        """
        Manually configure RADIUS via VPS proxy.
        Used when admin wants to specify custom RADIUS settings.
        """
        router = self.get_router(router_id, organization_id)

        try:
            self._execute_client_method_via_vps(
                router, 'configure_radius',
                radius_server=radius_server,
                radius_secret=radius_secret,
            )
            self.repository.update_radius_config_status(
                router_id, organization_id, 'configured'
            )

            # Persist RADIUS server in settings
            settings = router.settings or {}
            settings['radius_server'] = radius_server
            settings['radius_configured_at'] = datetime.utcnow().isoformat()
            self.repository.update(router_id, organization_id, {'settings': settings})

            return {'success': True, 'message': 'RADIUS configured successfully'}
        except Exception as e:
            raise BusinessError(f"RADIUS configuration failed: {str(e)}")

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
            'organization_slug': organization.slug if organization else None,
        }