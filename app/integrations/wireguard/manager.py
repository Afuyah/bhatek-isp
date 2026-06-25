"""
WireGuard Manager
=================
Manages WireGuard peers on the VPS via SSH.
Handles IP allocation, peer management, and MikroTik script generation.

Multi-Tenant: IPs allocated per organization subnet.
Verified Commands: Uses RouterOS v6.43+ / v7.x compatible commands.
"""

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
    SSH config is read from os.environ FIRST, then Flask config as fallback.
    This ensures it works regardless of Flask application context.

    IP Allocation:
        - Each organization gets a /24 subnet within 10.0.0.0/16
        - Organization index derived from org UUID hash
        - IPs start from .10 (reserve .1 for gateway)
        - Allocated IPs tracked in database to prevent duplicates
    """

    VPS_IP = "10.0.0.1"
    VPS_SUBNET = "10.0.0.0/16"
    ORG_SUBNET_PREFIX = "10.0"
    ORG_SUBNET_MASK = 24

    def __init__(self):
        self._host = None
        self._user = None
        self._private_key = None
        self._vps_public_key = None
        self._vps_endpoint = None

    @property
    def host(self) -> str:
        if self._host is None:
            self._host = os.environ.get('VPS_HOST', current_app.config.get('VPS_HOST', '163.245.217.16') if current_app else '163.245.217.16')
        return self._host

    @property
    def user(self) -> str:
        if self._user is None:
            self._user = os.environ.get('VPS_SSH_USER', current_app.config.get('VPS_SSH_USER', 'root') if current_app else 'root')
        return self._user

    @property
    def private_key(self) -> str:
        if self._private_key is None:
            self._private_key = os.environ.get('VPS_SSH_PRIVATE_KEY', current_app.config.get('VPS_SSH_PRIVATE_KEY', '') if current_app else '')
        return self._private_key

    @property
    def vps_public_key(self) -> str:
        if self._vps_public_key is None:
            self._vps_public_key = os.environ.get('VPS_WIREGUARD_PUBLIC_KEY', current_app.config.get('VPS_WIREGUARD_PUBLIC_KEY', '274kTJCdNISjJEBMLP9SuqaMyQ8GkDSqjXLttDgNsz4=') if current_app else '274kTJCdNISjJEBMLP9SuqaMyQ8GkDSqjXLttDgNsz4=')
        return self._vps_public_key

    @property
    def vps_endpoint(self) -> str:
        if self._vps_endpoint is None:
            self._vps_endpoint = os.environ.get('VPS_WIREGUARD_ENDPOINT', current_app.config.get('VPS_WIREGUARD_ENDPOINT', '163.245.217.16:51820') if current_app else '163.245.217.16:51820')
        return self._vps_endpoint

    def _get_ssh_config(self) -> Tuple[str, str, str]:
        """
        Get SSH config from os.environ FIRST (works everywhere),
        then fall back to Flask app context.
        """
        # PRIMARY: Read from os.environ (always available)
        key = os.environ.get('VPS_SSH_PRIVATE_KEY', '')
        host = os.environ.get('VPS_HOST', '')
        user = os.environ.get('VPS_SSH_USER', '')

        # FALLBACK: Try Flask config if env vars are empty
        if not key or not host or not user:
            try:
                if not key:
                    key = current_app.config.get('VPS_SSH_PRIVATE_KEY', '')
                if not host:
                    host = current_app.config.get('VPS_HOST', '163.245.217.16')
                if not user:
                    user = current_app.config.get('VPS_SSH_USER', 'root')
            except RuntimeError:
                pass

        # FINAL DEFAULTS
        if not host:
            host = '163.245.217.16'
        if not user:
            user = 'root'

        return key, host, user

    def _run_ssh(self, command: str, timeout: int = 15) -> Tuple[bool, str, str]:
        """
        Execute a command on the VPS via SSH using paramiko.
        Reads SSH config from os.environ first, then Flask config.
        """
        vps_private_key, vps_host, vps_user = self._get_ssh_config()

        if not vps_private_key:
            logger.error("VPS_SSH_PRIVATE_KEY not configured in environment or config")
            return False, '', 'SSH private key not configured'

        # Fix newline encoding from environment variable
        if '\\n' in vps_private_key:
            vps_private_key = vps_private_key.replace('\\n', '\n')

        key_path = None
        try:
            import paramiko

            with tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(vps_private_key)
                key_path = f.name

            os.chmod(key_path, 0o600)

            pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=vps_host, username=vps_user, pkey=pkey, timeout=timeout)

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

        except ImportError:
            logger.error("paramiko not installed")
            return False, '', 'paramiko not installed'
        except Exception as e:
            logger.error(f"SSH connection failed to {vps_host}: {e}")
            return False, '', str(e)
        finally:
            if key_path and os.path.exists(key_path):
                os.unlink(key_path)

    # =========================================================================
    # KEY GENERATION
    # =========================================================================

    def generate_peer_keypair(self) -> Tuple[str, str]:
        try:
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            import base64
            private_key = X25519PrivateKey.generate()
            private_bytes = private_key.private_bytes(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw, encryption_algorithm=serialization.NoEncryption())
            public_bytes = private_key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
            return base64.b64encode(private_bytes).decode('utf-8'), base64.b64encode(public_bytes).decode('utf-8')
        except ImportError:
            try:
                result = subprocess.run(['wg', 'genkey'], capture_output=True, text=True, timeout=5)
                private_key = result.stdout.strip()
                result = subprocess.run(['wg', 'pubkey'], input=private_key, capture_output=True, text=True, timeout=5)
                return private_key, result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.error(f"Failed to generate WireGuard keys: {e}")
                raise RuntimeError("Cannot generate WireGuard keys.")

    # =========================================================================
    # IP ALLOCATION
    # =========================================================================

    def _get_all_used_wireguard_ips(self) -> Dict[str, set]:
        from app.models.router import Router
        all_ips, org_indexes = set(), set()
        routers = Router.query.filter(Router.wireguard_ip != None).with_entities(Router.wireguard_ip).all()
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
        from app.models.router import Router
        routers = Router.query.filter(Router.wireguard_ip != None, Router.organization_id == organization_id).with_entities(Router.wireguard_ip).all()
        return [r.wireguard_ip for r in routers if r.wireguard_ip]

    def allocate_ip(self, organization_id: str, existing_ips: List[str] = None) -> Tuple[str, str, int]:
        db_data = self._get_all_used_wireguard_ips()
        all_used_ips, used_org_indexes = db_data['all_ips'], db_data['org_indexes']
        org_existing = self._get_org_existing_ips(organization_id)
        if not org_existing and existing_ips:
            org_existing = existing_ips
            for ip in existing_ips:
                all_used_ips.add(ip)
        org_index = self._resolve_org_index(organization_id, org_existing, used_org_indexes)
        subnet_prefix = f"{self.ORG_SUBNET_PREFIX}.{org_index}."
        highest = 9
        for ip in org_existing:
            if ip and ip.startswith(subnet_prefix):
                try:
                    host = int(ip.split('.')[-1])
                    if host > highest:
                        highest = host
                except (ValueError, IndexError):
                    pass
        for host in range(highest + 1, 255):
            candidate = f"{subnet_prefix}{host}"
            if candidate not in all_used_ips:
                return candidate, f"{subnet_prefix}0/{self.ORG_SUBNET_MASK}", org_index
        raise RuntimeError(f"No available IPs in subnet {subnet_prefix}0/24")

    def _resolve_org_index(self, organization_id: str, org_existing_ips=None, used_org_indexes=None) -> int:
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
            used_org_indexes = self._get_all_used_wireguard_ips()['org_indexes']
        clean_id = organization_id.replace('-', '')
        hash_val = int(clean_id[-4:], 16) if len(clean_id) >= 4 else 1
        org_index = (hash_val % 254) + 1
        if org_index in used_org_indexes:
            for offset in range(1, 255):
                candidate = ((org_index + offset) % 254) + 1
                if candidate not in used_org_indexes:
                    return candidate
        return org_index

    # =========================================================================
    # PEER MANAGEMENT
    # =========================================================================

    def add_peer(self, public_key: str, allowed_ip: str) -> bool:
        success, stdout, stderr = self._run_ssh(f'/usr/local/bin/wg-manage.sh add "{public_key}" "{allowed_ip}"')
        if success:
            logger.info(f"WireGuard peer added: {allowed_ip}")
        else:
            logger.error(f"Failed to add WireGuard peer {allowed_ip}: {stderr}")
        return success

    def remove_peer(self, public_key: str) -> bool:
        success, stdout, stderr = self._run_ssh(f'/usr/local/bin/wg-manage.sh remove "{public_key}"')
        if success:
            logger.info("WireGuard peer removed")
        else:
            logger.error(f"Failed to remove WireGuard peer: {stderr}")
        return success

    def list_peers(self) -> Dict[str, Any]:
        success, stdout, stderr = self._run_ssh('/usr/local/bin/wg-manage.sh list')
        if not success:
            return {'error': stderr, 'peers': [], 'count': 0}
        peers = []
        for line in stdout.split('\n'):
            line = line.strip()
            if line.startswith('peer:'):
                peers.append({'public_key': line.split(':', 1)[1].strip(), 'allowed_ips': '', 'last_handshake': ''})
            elif peers and 'allowed ips:' in line:
                peers[-1]['allowed_ips'] = line.split(':', 1)[1].strip()
            elif peers and 'latest handshake:' in line:
                peers[-1]['last_handshake'] = line.split(':', 1)[1].strip()
        return {'peers': peers, 'count': len(peers)}

    # =========================================================================
    # SETUP SCRIPT
    # =========================================================================

    def generate_mikrotik_setup_script(self, wireguard_ip: str, mikrotik_private_key: str, radius_secret: str, vps_endpoint: str = None, vps_public_key: str = None, include_radius: bool = True) -> Dict[str, Any]:
        endpoint = vps_endpoint or self.vps_endpoint
        pubkey = vps_public_key or self.vps_public_key
        ep_host, ep_port = (endpoint.split(':', 1) + ['51820'])[:2] if ':' in endpoint else (endpoint, '51820')
        steps = [
            {'step': 1, 'title': 'Create WireGuard Interface', 'commands': ['/interface wireguard add listen-port=51820 name=wg-to-vps'], 'single_line': '/interface wireguard add listen-port=51820 name=wg-to-vps'},
            {'step': 2, 'title': 'Connect to ISP Platform', 'commands': [f'/interface wireguard peers add allowed-address=10.0.0.1/32 endpoint-address={ep_host} endpoint-port={ep_port} interface=wg-to-vps persistent-keepalive=25 public-key="{pubkey}"'], 'single_line': f'/interface wireguard peers add allowed-address=10.0.0.1/32 endpoint-address={ep_host} endpoint-port={ep_port} interface=wg-to-vps persistent-keepalive=25 public-key="{pubkey}"'},
            {'step': 3, 'title': 'Assign VPN IP', 'commands': [f'/ip address add address={wireguard_ip}/16 interface=wg-to-vps network=10.0.0.0'], 'single_line': f'/ip address add address={wireguard_ip}/16 interface=wg-to-vps network=10.0.0.0'},
            {'step': 4, 'title': 'Add Route to VPS', 'commands': ['/ip route add dst-address=10.0.0.1/32 gateway=wg-to-vps'], 'single_line': '/ip route add dst-address=10.0.0.1/32 gateway=wg-to-vps'},
            {'step': 5, 'title': 'Firewall & API Access', 'commands': ['/ip firewall filter add chain=input src-address=10.0.0.0/16 action=accept comment="Allow ISP Platform"', '/interface list member add interface=wg-to-vps list=LAN', '/ip service set api address=10.0.0.0/16'], 'single_line': '/ip firewall filter add chain=input src-address=10.0.0.0/16 action=accept comment="Allow ISP Platform"; /interface list member add interface=wg-to-vps list=LAN; /ip service set api address=10.0.0.0/16'},
        ]
        if include_radius and radius_secret:
            steps.append({'step': 6, 'title': 'Configure RADIUS', 'commands': [f'/radius add address=10.0.0.1 secret="{radius_secret}" service=hotspot,ppp authentication-port=1812 accounting-port=1813 timeout=3000'], 'single_line': f'/radius add address=10.0.0.1 secret="{radius_secret}" service=hotspot,ppp authentication-port=1812 accounting-port=1813 timeout=3000'})
            steps.append({'step': 7, 'title': 'Enable RADIUS', 'commands': ['/ip hotspot profile set [find] use-radius=yes', '/ppp aaa set use-radius=yes', '/radius incoming set accept=yes'], 'single_line': '/ip hotspot profile set [find] use-radius=yes; /ppp aaa set use-radius=yes; /radius incoming set accept=yes'})
        steps.append({'step': 99, 'title': 'Verify', 'commands': ['/ping 10.0.0.1 count=3'], 'single_line': '/ping 10.0.0.1 count=3'})
        return {'vps_endpoint': endpoint, 'vps_public_key': pubkey, 'wireguard_ip': wireguard_ip, 'steps': steps, 'total_steps': len(steps)}