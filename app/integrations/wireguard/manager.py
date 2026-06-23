import subprocess
import tempfile
import os
from typing import Tuple, Optional, Dict, Any, List

from flask import current_app

from app.core.logging.logger import logger


class WireGuardManager:
    """
    Manages WireGuard peers on the VPS via SSH.

    Uses paramiko for SSH connectivity.
    All config values are lazy-loaded to avoid
    'Working outside of application context' errors.

    IP Allocation:
        - Each organization gets a /24 subnet within 10.0.0.0/16
        - Organization index derived from org UUID hash
        - IPs start from .10 (reserve .1 for gateway)
        - Allocated IPs tracked in database to prevent duplicates
    """

    # WireGuard network constants
    VPS_IP = "10.0.0.1"
    VPS_SUBNET = "10.0.0.0/16"
    ORG_SUBNET_PREFIX = "10.0"
    ORG_SUBNET_MASK = 24

    def __init__(self):
        """Initialize with lazy-loaded config."""
        self._host = None
        self._user = None
        self._private_key = None
        self._vps_public_key = None
        self._vps_endpoint = None

    # =========================================================================
    # LAZY PROPERTIES
    # =========================================================================

    @property
    def host(self) -> str:
        if self._host is None:
            self._host = current_app.config.get('VPS_HOST', '163.245.217.16')
        return self._host

    @property
    def user(self) -> str:
        if self._user is None:
            self._user = current_app.config.get('VPS_SSH_USER', 'root')
        return self._user

    @property
    def private_key(self) -> str:
        if self._private_key is None:
            self._private_key = current_app.config.get('VPS_SSH_PRIVATE_KEY', '')
        return self._private_key

    @property
    def vps_public_key(self) -> str:
        if self._vps_public_key is None:
            self._vps_public_key = current_app.config.get(
                'VPS_WIREGUARD_PUBLIC_KEY',
                '274kTJCdNISjJEBMLP9SuqaMyQ8GkDSqjXLttDgNsz4='
            )
        return self._vps_public_key

    @property
    def vps_endpoint(self) -> str:
        if self._vps_endpoint is None:
            self._vps_endpoint = current_app.config.get(
                'VPS_WIREGUARD_ENDPOINT', '163.245.217.16:51820'
            )
        return self._vps_endpoint

    # =========================================================================
    # SSH CONNECTION
    # =========================================================================

    def _run_ssh(self, command: str, timeout: int = 15) -> Tuple[bool, str, str]:
        """
        Execute a command on the VPS via SSH using paramiko.

        Args:
            command: Shell command to execute
            timeout: Connection timeout in seconds

        Returns:
            Tuple of (success: bool, stdout: str, stderr: str)
        """
        if not self.private_key:
            logger.error("VPS_SSH_PRIVATE_KEY not configured")
            return False, '', 'SSH private key not configured'

        try:
            import paramiko

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.key', delete=False
            ) as f:
                f.write(self.private_key)
                key_path = f.name

            os.chmod(key_path, 0o600)

            try:
                pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=self.host,
                    username=self.user,
                    pkey=pkey,
                    timeout=timeout,
                )

                stdin, stdout, stderr = client.exec_command(command)
                stdout_str = stdout.read().decode('utf-8', errors='ignore').strip()
                stderr_str = stderr.read().decode('utf-8', errors='ignore').strip()
                exit_status = stdout.channel.recv_exit_status()
                client.close()

                success = exit_status == 0
                if not success and stderr_str:
                    logger.warning(
                        f"SSH command failed (exit {exit_status}): {command}\n"
                        f"stderr: {stderr_str}"
                    )

                return success, stdout_str, stderr_str

            finally:
                if os.path.exists(key_path):
                    os.unlink(key_path)

        except ImportError:
            logger.error("paramiko not installed — cannot manage WireGuard via SSH")
            return False, '', 'paramiko not installed'
        except Exception as e:
            logger.error(f"SSH connection failed to {self.host}: {e}")
            return False, '', str(e)

    # =========================================================================
    # KEY GENERATION
    # =========================================================================

    def generate_peer_keypair(self) -> Tuple[str, str]:
        """
        Generate a WireGuard keypair for a MikroTik peer.

        Uses Python's cryptography library (pure Python, no system deps).
        Falls back to subprocess 'wg' if cryptography not available.

        Returns:
            Tuple of (private_key: str, public_key: str)
        """
        try:
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            import base64

            private_key = X25519PrivateKey.generate()
            private_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )

            private_b64 = base64.b64encode(private_bytes).decode('utf-8')
            public_b64 = base64.b64encode(public_bytes).decode('utf-8')

            return private_b64, public_b64

        except ImportError:
            try:
                result = subprocess.run(
                    ['wg', 'genkey'],
                    capture_output=True, text=True, timeout=5
                )
                private_key = result.stdout.strip()

                result = subprocess.run(
                    ['wg', 'pubkey'],
                    input=private_key, capture_output=True, text=True, timeout=5
                )
                public_key = result.stdout.strip()

                return private_key, public_key

            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.error(f"Failed to generate WireGuard keys: {e}")
                raise RuntimeError(
                    "Cannot generate WireGuard keys. "
                    "Install 'cryptography' package or 'wg' tool."
                )

    # =========================================================================
    # IP ALLOCATION (DATABASE-BACKED)
    # =========================================================================

    def _get_all_used_wireguard_ips(self) -> Dict[str, set]:
        """
        Query the database for all assigned WireGuard IPs across all orgs.

        Returns:
            Dict with 'all_ips' (set of all used IPs) and
            'org_indexes' (set of used org subnet indexes)
        """
        from app.models.router import Router

        all_ips = set()
        org_indexes = set()

        routers = Router.query.filter(
            Router.wireguard_ip != None
        ).with_entities(Router.wireguard_ip).all()

        for r in routers:
            if r.wireguard_ip:
                all_ips.add(r.wireguard_ip)
                try:
                    parts = r.wireguard_ip.split('.')
                    if len(parts) >= 3 and parts[0] == '10' and parts[1] == '0':
                        org_indexes.add(int(parts[2]))
                except (ValueError, IndexError):
                    pass

        return {'all_ips': all_ips, 'org_indexes': org_indexes}

    def _get_org_existing_ips(self, organization_id: str) -> List[str]:
        """Get all WireGuard IPs assigned to a specific organization."""
        from app.models.router import Router

        routers = Router.query.filter(
            Router.wireguard_ip != None,
            Router.organization_id == organization_id
        ).with_entities(Router.wireguard_ip).all()

        return [r.wireguard_ip for r in routers if r.wireguard_ip]

    def allocate_ip(
        self,
        organization_id: str,
        existing_ips: List[str] = None,
    ) -> Tuple[str, str, int]:
        """
        Allocate the next available WireGuard IP for a router.

        Queries the database to ensure no duplicate IPs across any org.
        Starts from .10 to leave room for gateways.

        Args:
            organization_id: Organization UUID
            existing_ips: Optional list of already-assigned IPs

        Returns:
            Tuple of (wireguard_ip: str, subnet: str, org_index: int)

        Raises:
            RuntimeError: If no IPs available
        """
        db_data = self._get_all_used_wireguard_ips()
        all_used_ips = db_data['all_ips']
        used_org_indexes = db_data['org_indexes']

        org_existing = self._get_org_existing_ips(organization_id)
        if not org_existing and existing_ips:
            org_existing = existing_ips
            for ip in existing_ips:
                all_used_ips.add(ip)

        org_index = self._resolve_org_index(
            organization_id, org_existing, used_org_indexes
        )

        subnet_prefix = f"{self.ORG_SUBNET_PREFIX}.{org_index}."
        highest = 9  # Start from .10

        for ip in org_existing:
            if ip and ip.startswith(subnet_prefix):
                try:
                    host = int(ip.split('.')[-1])
                    if host > highest:
                        highest = host
                except (ValueError, IndexError):
                    pass

        wireguard_ip = None
        for host in range(highest + 1, 255):
            candidate = f"{subnet_prefix}{host}"
            if candidate not in all_used_ips:
                wireguard_ip = candidate
                break

        if not wireguard_ip:
            raise RuntimeError(
                f"No available IPs in subnet {subnet_prefix}0/24 "
                f"for organization {organization_id}"
            )

        subnet = f"{subnet_prefix}0/{self.ORG_SUBNET_MASK}"
        logger.info(
            f"Allocated WireGuard IP {wireguard_ip} "
            f"in subnet {subnet} for org {organization_id}"
        )

        return wireguard_ip, subnet, org_index

    def _resolve_org_index(
        self,
        organization_id: str,
        org_existing_ips: List[str] = None,
        used_org_indexes: set = None,
    ) -> int:
        """
        Resolve organization subnet index.
        Priority: existing IPs → hash → next available.
        """
        if org_existing_ips:
            for ip in org_existing_ips:
                if ip and ip.startswith(self.ORG_SUBNET_PREFIX):
                    try:
                        parts = ip.split('.')
                        if len(parts) >= 3:
                            return int(parts[2])
                    except (ValueError, IndexError):
                        pass

        if used_org_indexes is None:
            db_data = self._get_all_used_wireguard_ips()
            used_org_indexes = db_data['org_indexes']

        clean_id = organization_id.replace('-', '')
        hash_val = int(clean_id[-4:], 16) if len(clean_id) >= 4 else 1
        org_index = (hash_val % 254) + 1

        if org_index in used_org_indexes:
            for offset in range(1, 255):
                candidate = ((org_index + offset) % 254) + 1
                if candidate not in used_org_indexes:
                    org_index = candidate
                    logger.info(
                        f"Org index collision — assigned {org_index} "
                        f"for org {organization_id}"
                    )
                    break

        return org_index

    # =========================================================================
    # VPS PEER MANAGEMENT
    # =========================================================================

    def add_peer(self, public_key: str, allowed_ip: str) -> bool:
        """
        Add a WireGuard peer to the VPS.

        Args:
            public_key: MikroTik's WireGuard public key
            allowed_ip: WireGuard IP assigned to this router (e.g., "10.0.1.10/32")

        Returns:
            True if added successfully
        """
        success, stdout, stderr = self._run_ssh(
            f'/usr/local/bin/wg-manage.sh add "{public_key}" "{allowed_ip}"'
        )

        if success:
            logger.info(
                f"WireGuard peer added: {allowed_ip} "
                f"(key: {public_key[:12]}...)"
            )
        else:
            logger.error(
                f"Failed to add WireGuard peer {allowed_ip}: {stderr}"
            )

        return success

    def remove_peer(self, public_key: str) -> bool:
        """
        Remove a WireGuard peer from the VPS.

        Args:
            public_key: MikroTik's WireGuard public key to remove

        Returns:
            True if removed successfully
        """
        success, stdout, stderr = self._run_ssh(
            f'/usr/local/bin/wg-manage.sh remove "{public_key}"'
        )

        if success:
            logger.info(f"WireGuard peer removed: {public_key[:12]}...")
        else:
            logger.error(f"Failed to remove WireGuard peer: {stderr}")

        return success

    def list_peers(self) -> Dict[str, Any]:
        """
        List all WireGuard peers on the VPS.

        Returns:
            Dict with 'peers' list and 'count'
        """
        success, stdout, stderr = self._run_ssh(
            '/usr/local/bin/wg-manage.sh list'
        )

        if not success:
            return {'error': stderr, 'peers': [], 'count': 0}

        peers = []
        for line in stdout.split('\n'):
            line = line.strip()
            if line.startswith('peer:'):
                peers.append({
                    'public_key': line.split(':', 1)[1].strip(),
                    'allowed_ips': '',
                    'last_handshake': '',
                })
            elif peers and 'allowed ips:' in line:
                peers[-1]['allowed_ips'] = line.split(':', 1)[1].strip()
            elif peers and 'latest handshake:' in line:
                peers[-1]['last_handshake'] = line.split(':', 1)[1].strip()

        return {
            'peers': peers,
            'count': len(peers),
        }

    # =========================================================================
    # MIKROTIK SETUP SCRIPT (VERIFIED COMMANDS)
    # =========================================================================

    def generate_mikrotik_setup_script(
        self,
        wireguard_ip: str,
        mikrotik_private_key: str,
        radius_secret: str,
        vps_endpoint: str = None,
        vps_public_key: str = None,
        include_radius: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate MikroTik setup script with CORRECTED networking.

        Key fixes:
            - allowed-address=10.0.0.1/32 (not /16) on peer
            - Explicit route for VPS via WireGuard
            - Firewall rule allowing WireGuard subnet input
            - API bound to WireGuard subnet
            - Verified RADIUS commands
        """
        endpoint = vps_endpoint or self.vps_endpoint
        pubkey = vps_public_key or self.vps_public_key

        if ':' in endpoint:
            ep_host, ep_port = endpoint.split(':', 1)
        else:
            ep_host, ep_port = endpoint, '51820'

        steps = []

        # STEP 1: WireGuard Interface
        steps.append({
            'step': 1,
            'title': 'Create WireGuard Interface',
            'description': 'Creates the WireGuard virtual interface.',
            'commands': [
                '/interface wireguard',
                'add listen-port=51820 name=wg-to-vps',
            ],
            'single_line': '/interface wireguard add listen-port=51820 name=wg-to-vps',
        })

        # STEP 2: WireGuard Peer (CORRECTED — /32 not /16)
        steps.append({
            'step': 2,
            'title': 'Connect to ISP Platform VPN',
            'description': (
                'Adds the VPS as a WireGuard peer. '
                'CRITICAL: allowed-address is /32 for stable return routing.'
            ),
            'commands': [
                '/interface wireguard peers',
                f'add allowed-address=10.0.0.1/32 '
                f'endpoint-address={ep_host} '
                f'endpoint-port={ep_port} '
                f'interface=wg-to-vps '
                f'persistent-keepalive=25 '
                f'public-key="{pubkey}"',
            ],
            'single_line': (
                f'/interface wireguard peers add '
                f'allowed-address=10.0.0.1/32 '
                f'endpoint-address={ep_host} endpoint-port={ep_port} '
                f'interface=wg-to-vps persistent-keepalive=25 '
                f'public-key="{pubkey}"'
            ),
        })

        # STEP 3: Assign WireGuard IP
        steps.append({
            'step': 3,
            'title': 'Assign VPN IP Address',
            'description': f'Assigns router VPN IP: {wireguard_ip}',
            'commands': [
                '/ip address',
                f'add address={wireguard_ip}/16 interface=wg-to-vps network=10.0.0.0',
            ],
            'single_line': (
                f'/ip address add address={wireguard_ip}/16 '
                f'interface=wg-to-vps network=10.0.0.0'
            ),
        })

        # STEP 4: Route for VPS (CRITICAL FIX)
        steps.append({
            'step': 4,
            'title': 'Add Route to VPS',
            'description': (
                'Explicit route ensures return packets reach the VPS correctly.'
            ),
            'commands': [
                '/ip route',
                'add dst-address=10.0.0.1/32 gateway=wg-to-vps',
            ],
            'single_line': '/ip route add dst-address=10.0.0.1/32 gateway=wg-to-vps',
        })

        # STEP 5: Firewall (CRITICAL — allow WireGuard subnet)
        steps.append({
            'step': 5,
            'title': 'Allow WireGuard Access',
            'description': (
                'Allows the ISP platform to manage this router. '
                'MUST be placed above any drop rules.'
            ),
            'commands': [
                '/ip firewall filter',
                'add chain=input src-address=10.0.0.0/16 '
                'action=accept comment="Allow WireGuard ISP Platform"',
            ],
            'single_line': (
                '/ip firewall filter add chain=input '
                'src-address=10.0.0.0/16 action=accept '
                'comment="Allow WireGuard ISP Platform"'
            ),
        })

        # STEP 6: Secure API Access
        steps.append({
            'step': 6,
            'title': 'Secure API Access',
            'description': 'Restricts API/Winbox/SSH to WireGuard subnet only.',
            'commands': [
                '/ip service set api address=10.0.0.0/16',
                '/ip service set winbox address=10.0.0.0/16',
                '/ip service set ssh address=10.0.0.0/16',
            ],
            'single_line': (
                '/ip service set api address=10.0.0.0/16; '
                '/ip service set winbox address=10.0.0.0/16; '
                '/ip service set ssh address=10.0.0.0/16'
            ),
        })

        # STEP 7: RADIUS Configuration
        if include_radius and radius_secret:
            steps.append({
                'step': 7,
                'title': 'Configure RADIUS Authentication',
                'description': 'Points router to platform RADIUS server.',
                'commands': [
                    '/radius',
                    f'add address=10.0.0.1 secret="{radius_secret}" '
                    f'service=hotspot,ppp '
                    f'authentication-port=1812 accounting-port=1813 timeout=3000',
                ],
                'single_line': (
                    f'/radius add address=10.0.0.1 secret="{radius_secret}" '
                    f'service=hotspot,ppp authentication-port=1812 '
                    f'accounting-port=1813 timeout=3000'
                ),
            })

            # STEP 8: Enable RADIUS (VERIFIED COMMANDS)
            steps.append({
                'step': 8,
                'title': 'Enable RADIUS on Services',
                'description': 'Enables RADIUS on hotspot and PPPoE.',
                'commands': [
                    '/ip hotspot profile set [find] use-radius=yes',
                    '/ppp aaa set use-radius=yes',
                    '/radius incoming set accept=yes',
                ],
                'single_line': (
                    '/ip hotspot profile set [find] use-radius=yes; '
                    '/ppp aaa set use-radius=yes; '
                    '/radius incoming set accept=yes'
                ),
            })

        # VERIFICATION
        steps.append({
            'step': 99,
            'title': 'Verify Connection',
            'description': 'Run these to confirm everything is working.',
            'commands': [
                '/ping 10.0.0.1 count=3',
                '/interface wireguard peers print',
                '/radius print',
            ],
            'single_line': (
                '/ping 10.0.0.1 count=3; '
                '/interface wireguard peers print'
            ),
        })

        return {
            'vps_endpoint': endpoint,
            'vps_public_key': pubkey,
            'wireguard_ip': wireguard_ip,
            'mikrotik_private_key': mikrotik_private_key,
            'steps': steps,
            'total_steps': len(steps),
        }