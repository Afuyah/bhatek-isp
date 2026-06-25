"""
Router Service Module - WireGuard VPN + RADIUS Integration
==========================================================
All MikroTik API calls proxied through VPS via SSH.
Flask (Railway) cannot directly reach WireGuard IPs (10.0.0.0/16).
"""

from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime
import json as _json
import os as _os
import tempfile as _tempfile
import secrets
import re

from flask import current_app

from app.modules.router.repository import (
    RouterRepository, HotspotServerRepository, PPPoeServerRepository,
)
from app.models.router import Router, HotspotServer, PPPoeServer
from app.models.nas import NAS
from app.models.organization import Organization
from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError
from app.integrations.mikrotik.client import MikroTikClient
from app.integrations.wireguard.manager import WireGuardManager
from app.core.database.session import db


class RouterService:
    """Router management with WireGuard VPN and RADIUS integration."""

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
        return secrets.token_urlsafe(32)

    def _get_radius_server(self) -> str:
        return current_app.config.get('RADIUS_SERVER_IP', self.DEFAULT_RADIUS_SERVER)

    def _get_vps_public_key(self) -> str:
        return current_app.config.get('VPS_WIREGUARD_PUBLIC_KEY', '274kTJCdNISjJEBMLP9SuqaMyQ8GkDSqjXLttDgNsz4=')

    def _parse_uptime(self, uptime_str: str) -> int:
        if not uptime_str:
            return 0
        seconds = 0
        for pattern, mult in [('w', 604800), ('d', 86400), ('h', 3600), ('m', 60), ('s', 1)]:
            m = re.search(rf'(\d+){pattern}', uptime_str)
            if m:
                seconds += int(m.group(1)) * mult
        return seconds

    def _allocate_wireguard_ip(self, organization_id: UUID) -> tuple:
        import hashlib
        org_hash = hashlib.md5(str(organization_id).encode()).hexdigest()
        org_index = int(org_hash[:4], 16) % 200 + 1
        subnet = f"10.0.{org_index}.0/24"
        existing = self.repository.get_by_organization(organization_id, limit=10000)
        used = {r.wireguard_ip for r in existing if r.wireguard_ip and r.wireguard_ip.startswith(f"10.0.{org_index}.")}
        for host in range(10, 254):
            ip = f"10.0.{org_index}.{host}"
            if ip not in used:
                return ip, subnet, org_index
        for host in range(10, 254):
            for fb in range(201, 255):
                ip = f"10.0.{fb}.{host}"
                if ip not in used:
                    return ip, f"10.0.{fb}.0/24", fb
        raise BusinessError("No available WireGuard IPs")

    # =========================================================================
    # SSH HELPERS
    # =========================================================================

    def _get_ssh_credentials(self) -> tuple:
        """Get SSH credentials from os.environ first, then Flask config."""
        key = _os.environ.get('VPS_SSH_PRIVATE_KEY', '')
        host = _os.environ.get('VPS_HOST', '')
        user = _os.environ.get('VPS_SSH_USER', '')
        if not key:
            try:
                key = current_app.config.get('VPS_SSH_PRIVATE_KEY', '')
                host = current_app.config.get('VPS_HOST', '163.245.217.16')
                user = current_app.config.get('VPS_SSH_USER', 'root')
            except RuntimeError:
                pass
        if not host:
            host = '163.245.217.16'
        if not user:
            user = 'root'
        return key, host, user

    def _ssh_execute(self, script: str, timeout: int = 25) -> tuple:
        """Execute a Python script on the VPS via SSH. Returns (success, stdout, stderr)."""
        key, host, user = self._get_ssh_credentials()
        if not key:
            raise BusinessError("VPS SSH key not configured")
        if '\\n' in key:
            key = key.replace('\\n', '\n')

        key_path = None
        try:
            import paramiko
            with _tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(key)
                key_path = f.name
            _os.chmod(key_path, 0o600)

            pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, username=user, pkey=pkey, timeout=timeout)
            stdin, stdout, stderr = client.exec_command(script)
            out = stdout.read().decode('utf-8', errors='ignore').strip()
            err = stderr.read().decode('utf-8', errors='ignore').strip()
            exit_code = stdout.channel.recv_exit_status()
            client.close()
            return exit_code == 0, out, err
        except Exception as e:
            logger.error(f"SSH failed: {e}")
            return False, '', str(e)
        finally:
            if key_path and _os.path.exists(key_path):
                _os.unlink(key_path)

    # =========================================================================
    # VPS PROXY - All MikroTik API calls go through here
    # =========================================================================

    def _execute_via_vps(self, router: Router, command: str, **kwargs) -> List[Dict[str, Any]]:
        """Execute a MikroTik API command via VPS SSH tunnel."""
        password = self.encryption.decrypt(router.password_encrypted)
        host = str(router.ip_address)
        port = router.api_port or 8728
        username = router.username
        kwargs_json = _json.dumps(kwargs)

        script = '\n'.join([
            "import socket, struct, hashlib, binascii, json, sys",
            f"host = '{host}'",
            f"port = {port}",
            f"username = '{username}'",
            f"password = b'{password}'",
            f"command = '{command}'",
            f"kwargs = json.loads('{kwargs_json}')",
            "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
            "sock.settimeout(20)",
            "sock.connect((host, port))",
            "def sc(*w):",
            "    for x in w:",
            "        if not x: continue",
            "        b = x.encode(); sock.sendall(struct.pack('>I',len(b))+b)",
            "    sock.sendall(struct.pack('>I',0))",
            "def rr():",
            "    w=[]",
            "    while True:",
            "        lb=b''",
            "        while len(lb)<4:",
            "            c=sock.recv(4-len(lb))",
            "            if not c: return w",
            "            lb+=c",
            "        l=struct.unpack('>I',lb)[0]",
            "        if l==0: return w",
            "        wb=b''",
            "        while len(wb)<l:",
            "            c=sock.recv(l-len(wb))",
            "            if not c: return w",
            "            wb+=c",
            "        w.append(wb.decode('utf-8',errors='ignore'))",
            "sc('/login')",
            "for w in rr():",
            "    if '=ret=' in w: break",
            "    if '=challenge=' in w:",
            "        ch=w.split('=',2)[2]",
            "        cb=binascii.unhexlify(ch)",
            "        md=hashlib.md5(); md.update(b'\\x00'); md.update(password); md.update(cb)",
            "        sc('/login','=name='+username,'=response='+md.hexdigest().upper())",
            "        rr()",
            "words=[command]",
            "for k,v in kwargs.items():",
            "    if v is not None: words.append('='+k.replace('_','-')+'='+str(v))",
            "sc(*words)",
            "resp=rr()",
            "result=[]; cur={}",
            "for line in resp:",
            "    if line.startswith('!') and '=message=' in line: break",
            "    elif line.startswith('!'):",
            "        if cur and len(cur)>1: result.append(cur)",
            "        cur={'status':line[1:]}",
            "    elif '=' in line: k,v=line.split('=',1); cur[k]=v",
            "if cur and len(cur)>1: result.append(cur)",
            "print(json.dumps(result))",
            "sock.close()",
        ])

        success, stdout, stderr = self._ssh_execute(f"python3 << 'PYEOF'\n{script}\nPYEOF")
        if success and stdout:
            try:
                return _json.loads(stdout)
            except _json.JSONDecodeError:
                logger.error(f"VPS parse error: {stdout[:300]}")
                raise BusinessError("VPS returned invalid response")
        logger.error(f"VPS SSH failed (stderr: {stderr[:300]})")
        raise BusinessError(f"Failed via VPS: {stderr[:200]}")

    def _execute_client_method_via_vps(self, router: Router, method_name: str, **kwargs) -> Any:
        """Execute a MikroTikClient-equivalent method via VPS proxy."""
        if method_name == 'get_router_info':
            r = self._execute_via_vps(router, '/system/resource/print')
            i = self._execute_via_vps(router, '/system/identity/print')
            r0, i0 = r[0] if r else {}, i[0] if i else {}
            return {
                'hostname': i0.get('name'), 'version': r0.get('version'),
                'uptime': r0.get('uptime'), 'cpu_load': r0.get('cpu-load'),
                'free_memory': r0.get('free-memory'), 'total_memory': r0.get('total-memory'),
                'board_name': r0.get('board-name'), 'architecture_name': r0.get('architecture-name'),
                'platform': r0.get('platform'),
            }
        elif method_name == 'get_hotspot_servers':
            return self._execute_via_vps(router, '/ip/hotspot/print')
        elif method_name == 'get_pppoe_servers':
            return self._execute_via_vps(router, '/interface/pppoe-server/server/print')
        elif method_name == 'configure_radius':
            rs = kwargs.get('radius_server', self._get_radius_server())
            secret = kwargs.get('radius_secret', router.radius_secret)
            existing = self._execute_via_vps(router, '/radius/print')
            found = False
            for item in existing:
                if item.get('address') == rs:
                    self._execute_via_vps(router, '/radius/set', numbers=item.get('.id'),
                        secret=secret, service='hotspot,ppp', authentication_port='1812',
                        accounting_port='1813', timeout='3000', retries='3')
                    found = True
                    break
            if not found:
                self._execute_via_vps(router, '/radius/add', address=rs, secret=secret,
                    service='hotspot,ppp', authentication_port='1812',
                    accounting_port='1813', timeout='3000', retries='3')
            try:
                self._execute_via_vps(router, '/ip/hotspot/profile/set', numbers='[find]', **{'use-radius': 'yes'})
            except Exception:
                pass
            try:
                self._execute_via_vps(router, '/ppp/aaa/set', **{'use-radius': 'yes'})
            except Exception:
                pass
            try:
                self._execute_via_vps(router, '/radius/incoming/set', accept='yes')
            except Exception:
                pass
            return {'success': True, 'message': 'RADIUS configured', 'radius_server': rs}
        elif method_name == 'configure_walled_garden':
            domain = kwargs.get('platform_domain', current_app.config.get('PLATFORM_DOMAIN', 'isp.bhatek.space'))
            results = {'success': True, 'dns_added': False, 'domains_added': 0, 'errors': []}
            try:
                ex = self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/print')
                if not any(e.get('dst-port') == '53' and e.get('protocol') == 'udp' for e in ex):
                    self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/add', dst_port='53', protocol='udp', action='accept', comment='DNS')
                results['dns_added'] = True
            except Exception as e:
                results['errors'].append(str(e))
            domains = [
                {'host': domain, 'comment': 'ISP Portal'},
                {'host': '*.safaricom.co.ke', 'comment': 'M-Pesa'},
                {'host': '*.googleapis.com', 'comment': 'Fonts'},
                {'host': '*.gstatic.com', 'comment': 'CDN'},
            ]
            for d in domains:
                try:
                    ex = self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/print')
                    if not any(e.get('dst-host') == d['host'] for e in ex):
                        self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/add', **{'dst-host': d['host']}, action='accept', comment=d['comment'])
                        results['domains_added'] += 1
                except Exception as e:
                    results['errors'].append(str(e))
            return results
        raise ValueError(f"Unknown method: {method_name}")

    # =========================================================================
    # NAS ENTRY
    # =========================================================================

    def _create_nas_entry(self, router: Router, radius_secret: str) -> NAS:
        try:
            nas = NAS(organization_id=router.organization_id, nasname=str(router.ip_address),
                      shortname=router.name, type='mikrotik', secret=radius_secret,
                      description=f"Auto-created for {router.name}", router_id=router.id, is_active=True)
            db.session.add(nas)
            db.session.flush()
            router.nas_entry_id = nas.id
            db.session.commit()
            logger.info(f"NAS entry created: {router.name} (nasname={nas.nasname})")
            return nas
        except Exception as e:
            db.session.rollback()
            raise BusinessError(f"NAS entry failed: {e}")

    # =========================================================================
    # SETUP SCRIPT
    # =========================================================================

    def _generate_mikrotik_setup_script(self, wireguard_ip: str, mikrotik_private_key: str,
                                          radius_secret: str, router_name: str = "Router",
                                          organization_name: str = "ISP") -> str:
        pk = self._get_vps_public_key()
        return f"""# ISP Platform - MikroTik Setup
# Router: {router_name} | WireGuard IP: {wireguard_ip}
/interface wireguard add listen-port=51820 name=wg-to-vps
/interface wireguard peers add allowed-address=10.0.0.1/32 endpoint-address={self.VPS_ENDPOINT} endpoint-port=51820 interface=wg-to-vps persistent-keepalive=25 public-key="{pk}"
/ip address add address={wireguard_ip}/16 interface=wg-to-vps network=10.0.0.0
/ip route add dst-address=10.0.0.1/32 gateway=wg-to-vps
/ip firewall filter add chain=input src-address=10.0.0.0/16 action=accept comment="Allow ISP Platform"
/interface list member add interface=wg-to-vps list=LAN
/ip service set api address=10.0.0.0/16
/radius add address=10.0.0.1 secret="{radius_secret}" service=hotspot,ppp authentication-port=1812 accounting-port=1813
/ip hotspot profile set [find] use-radius=yes
/ppp aaa set use-radius=yes
/radius incoming set accept=yes
# Verify: /ping 10.0.0.1 count=3"""

    # =========================================================================
    # CREATE ROUTER
    # =========================================================================

    def create_router(self, organization_id: UUID, network_id: UUID, data: Dict[str, Any]) -> Dict[str, Any]:
        for f in ['name', 'username', 'password']:
            if not data.get(f):
                raise ValidationError(f"'{f}' is required")
        local_ip = data.get('ip_address') or data.get('local_ip')
        if not local_ip:
            raise ValidationError("ip_address or local_ip required")
        org = Organization.query.get(organization_id)
        if not org:
            raise ValidationError("Organization not found")

        wg_priv, wg_pub = self.wireguard.generate_peer_keypair()
        wg_ip, subnet, idx = self._allocate_wireguard_ip(organization_id)
        radius_secret = self._generate_radius_secret()
        enc_pw = self.encryption.encrypt(data['password'])
        enc_wg = self.encryption.encrypt(wg_priv)

        router = self.repository.create({
            'organization_id': organization_id, 'network_id': network_id,
            'name': data['name'], 'model': data.get('model'),
            'ip_address': wg_ip, 'local_ip': local_ip,
            'api_port': data.get('api_port', 8728), 'username': data['username'],
            'password_encrypted': enc_pw, 'location': data.get('location'),
            'description': data.get('description'), 'is_active': True,
            'status': 'pending_wireguard', 'radius_secret': radius_secret,
            'radius_config_status': 'pending', 'auto_config_attempts': 0,
            'wireguard_ip': wg_ip, 'wireguard_public_key': wg_pub,
            'wireguard_private_key_encrypted': enc_wg,
        })
        self._create_nas_entry(router, radius_secret)
        wg_ok = self.wireguard.add_peer(wg_pub, f"{wg_ip}/32")
        script = self._generate_mikrotik_setup_script(wg_ip, wg_priv, radius_secret, router.name, org.name)
        logger.info(f"Router created: {router.name} (WG: {wg_ip}, peer: {wg_ok})")
        return {
            'success': True, 'router': router,
            'wireguard': {'ip': wg_ip, 'public_key': wg_pub, 'private_key': wg_priv, 'peer_added_to_vps': wg_ok},
            'radius': {'secret': radius_secret, 'server': self._get_radius_server(), 'auth_port': 1812, 'acct_port': 1813},
            'setup_script': script,
            'next_step': 'Paste script into MikroTik terminal, then click Test Connection.',
        }

    # =========================================================================
    # TEST CONNECTION
    # =========================================================================

    def test_connection(self, router_id: UUID, organization_id: UUID, method: str = 'api') -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        if method != 'api':
            raise ValidationError(f"Unsupported: {method}")
        try:
            result = self._execute_via_vps(router, '/system/resource/print')
            if result and len(result) > 0:
                r = result[0]
                self.repository.update_status(router_id, organization_id, 'online')
                return {'success': True, 'connected': True, 'router_info': {
                    'version': r.get('version', '?'), 'board_name': r.get('board-name', '?'),
                    'cpu_load': r.get('cpu-load', '?'), 'uptime': r.get('uptime', '?'),
                    'free_memory': r.get('free-memory', '?'), 'total_memory': r.get('total-memory', '?'),
                    'architecture_name': r.get('architecture-name', '?'),
                }}
            self.repository.update_status(router_id, organization_id, 'offline')
            return {'success': False, 'connected': False, 'error': 'No response'}
        except Exception as e:
            self.repository.update_status(router_id, organization_id, 'error', error_message=str(e))
            raise BusinessError(f"Connection test failed: {e}")

    # =========================================================================
    # AUTO-CONFIGURE
    # =========================================================================

    def auto_configure_after_wireguard(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        result = {'success': True, 'radius_configured': False, 'discovered': False, 'steps': []}
        try:
            self._execute_client_method_via_vps(router, 'configure_radius', radius_secret=router.radius_secret)
            self.repository.update_radius_config_status(router_id, organization_id, 'configured')
            result['radius_configured'] = True
            result['steps'].append({'step': 'radius', 'status': 'success'})
        except Exception as e:
            result['steps'].append({'step': 'radius', 'status': 'error', 'error': str(e)})
        try:
            info = self._execute_client_method_via_vps(router, 'get_router_info')
            caps = ['api']
            try:
                if self._execute_client_method_via_vps(router, 'get_hotspot_servers'):
                    caps.append('hotspot')
            except Exception:
                pass
            try:
                if self._execute_client_method_via_vps(router, 'get_pppoe_servers'):
                    caps.append('pppoe')
            except Exception:
                pass
            self.repository.update_discovery(router_id, organization_id, model=info.get('board_name'),
                firmware_version=info.get('version'), capabilities=caps, discovery_method='api')
            result['discovered'] = True
            result['steps'].append({'step': 'discovery', 'status': 'success'})
        except Exception as e:
            result['steps'].append({'step': 'discovery', 'status': 'error', 'error': str(e)})
        st = 'online' if result['radius_configured'] else 'radius_pending'
        self.repository.update_status(router_id, organization_id, st)
        result['all_success'] = result['radius_configured'] and result['discovered']
        return result

    # =========================================================================
    # READ / UPDATE / DELETE
    # =========================================================================

    def get_router(self, router_id: UUID, organization_id: UUID) -> Router:
        r = self.repository.get_by_id(router_id, organization_id)
        if not r:
            raise NotFoundError("Router not found")
        return r

    def get_routers_by_organization(self, organization_id: UUID, skip=0, limit=100,
                                      status=None, network_id=None, radius_config_status=None) -> List[Router]:
        return self.repository.get_by_organization(organization_id, skip, limit, status, network_id, radius_config_status)

    def get_routers_by_network(self, network_id: UUID, organization_id: UUID) -> List[Router]:
        return self.repository.get_by_network(network_id, organization_id)

    def get_routers_pending_radius_config(self, organization_id: UUID) -> List[Router]:
        return self.repository.get_routers_pending_radius_config(organization_id)

    def get_router_by_ip(self, ip_address: str, organization_id: UUID) -> Optional[Router]:
        return self.repository.get_by_ip(ip_address, organization_id)

    def update_router(self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Router:
        if data.get("password"):
            data["password_encrypted"] = self.encryption.encrypt(data.pop("password"))
        elif "password" in data:
            data.pop("password")
        r = self.repository.update(router_id, organization_id, data)
        if not r:
            raise NotFoundError("Router not found")
        return r

    def delete_router(self, router_id: UUID, organization_id: UUID, soft_delete: bool = True) -> None:
        r = self.repository.get_by_id(router_id, organization_id, include_inactive=True)
        if not r:
            raise NotFoundError("Router not found")
        if r.wireguard_public_key:
            try:
                self.wireguard.remove_peer(r.wireguard_public_key)
            except Exception as e:
                logger.warning(f"WG peer remove failed: {e}")
        if not soft_delete:
            if len(self.hotspot_repo.get_by_router(router_id, organization_id)) > 0 or \
               len(self.pppoe_repo.get_by_router(router_id, organization_id)) > 0:
                raise BusinessError("Router has active services")
        self.repository.delete(router_id, organization_id, soft_delete)

    # =========================================================================
    # DISCOVERY / HEALTH / SYNC
    # =========================================================================

    def discover_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        try:
            info = self._execute_client_method_via_vps(r, 'get_router_info')
            caps = ['api']
            try:
                if self._execute_client_method_via_vps(r, 'get_hotspot_servers'):
                    caps.append('hotspot')
            except Exception:
                pass
            try:
                if self._execute_client_method_via_vps(r, 'get_pppoe_servers'):
                    caps.append('pppoe')
            except Exception:
                pass
            self.repository.update_discovery(router_id, organization_id, model=info.get('board_name'),
                firmware_version=info.get('version'), capabilities=caps, discovery_method='api')
            self.repository.update_status(router_id, organization_id, 'online')
            return {'success': True, 'method': 'api', 'info': info}
        except Exception as e:
            self.repository.update_status(router_id, organization_id, 'offline')
            return {'success': False, 'message': str(e)}

    def update_health(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        try:
            info = self._execute_client_method_via_vps(r, 'get_router_info')
            cpu = int(info['cpu_load']) if info.get('cpu_load') else None
            fm = int(info['free_memory']) if info.get('free_memory') else None
            tm = int(info['total_memory']) if info.get('total_memory') else None
            ut = info.get('uptime', '0s')
            us = self._parse_uptime(ut)
            self.repository.update_health(router_id, organization_id, cpu_load=cpu, free_memory=fm, total_memory=tm, uptime=ut)
            self.repository.update_status(router_id, organization_id, 'online')
            return {'cpu_load': cpu, 'free_memory': fm, 'total_memory': tm, 'uptime_seconds': us, 'uptime_display': ut,
                    'version': info.get('version'), 'board_name': info.get('board_name')}
        except Exception as e:
            self.repository.update_status(router_id, organization_id, 'error', error_message=str(e))
            raise BusinessError(f"Health check failed: {e}")

    def sync_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        results = {'success': True, 'hotspot_synced': 0, 'pppoe_synced': 0, 'errors': []}
        try:
            for hs in (self._execute_client_method_via_vps(r, 'get_hotspot_servers') or []):
                n = hs.get('name', '')
                if not n:
                    continue
                ex = self.hotspot_repo.get_by_router_and_hotspot_id(r.id, r.organization_id, n)
                d = {'organization_id': r.organization_id, 'router_id': r.id, 'name': n, 'hotspot_id': n,
                     'interface': hs.get('interface'), 'is_active': hs.get('disabled') != 'true'}
                if ex:
                    self.hotspot_repo.update(ex.id, r.organization_id, d)
                else:
                    self.hotspot_repo.create(d)
                results['hotspot_synced'] += 1
        except Exception as e:
            results['errors'].append(f"Hotspot: {e}")
        try:
            for ps in (self._execute_client_method_via_vps(r, 'get_pppoe_servers') or []):
                n = ps.get('name', '')
                if not n:
                    continue
                ex = self.pppoe_repo.get_by_router_and_name(r.id, r.organization_id, n)
                d = {'organization_id': r.organization_id, 'router_id': r.id, 'name': n,
                     'interface': ps.get('interface'), 'service_name': ps.get('service-name'),
                     'mtu': int(ps.get('mtu', 1492)), 'is_active': ps.get('disabled') != 'true'}
                if ex:
                    self.pppoe_repo.update(ex.id, r.organization_id, d)
                else:
                    self.pppoe_repo.create(d)
                results['pppoe_synced'] += 1
        except Exception as e:
            results['errors'].append(f"PPPoE: {e}")
        self.repository.update(router_id, organization_id, {'last_sync_at': datetime.utcnow(), 'status': 'online'})
        return results

    # =========================================================================
    # RADIUS CONFIG
    # =========================================================================

    def retry_radius_configuration(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        if not r.radius_secret:
            raise BusinessError("No RADIUS secret")
        try:
            self._execute_client_method_via_vps(r, 'configure_radius', radius_secret=r.radius_secret)
            self.repository.update_radius_config_status(r.id, organization_id, 'configured')
            return {'success': True, 'message': 'RADIUS configured'}
        except Exception as e:
            self.repository.update_radius_config_status(r.id, organization_id, 'failed', error=str(e))
            return {'success': False, 'message': str(e)}

    def configure_radius_manual(self, router_id: UUID, organization_id: UUID, radius_server: str, radius_secret: str) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        try:
            self._execute_client_method_via_vps(r, 'configure_radius', radius_server=radius_server, radius_secret=radius_secret)
            self.repository.update_radius_config_status(router_id, organization_id, 'configured')
            return {'success': True, 'message': 'RADIUS configured'}
        except Exception as e:
            raise BusinessError(str(e))

    # =========================================================================
    # WALLED GARDEN
    # =========================================================================

    def configure_walled_garden(self, router_id: UUID, organization_id: UUID, **kwargs) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        return self._execute_client_method_via_vps(r, 'configure_walled_garden', **kwargs)

    # =========================================================================
    # STATUS
    # =========================================================================

    def get_connection_status(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        s = r.settings or {}
        h = s.get('health', {})
        return {
            'router_id': str(r.id), 'name': r.name, 'ip_address': str(r.ip_address),
            'local_ip': r.local_ip, 'wireguard_ip': r.wireguard_ip, 'status': r.status,
            'radius_config_status': r.radius_config_status, 'auto_config_attempts': r.auto_config_attempts or 0,
            'last_seen_at': r.last_seen_at.isoformat() if r.last_seen_at else None,
            'last_sync_at': r.last_sync_at.isoformat() if r.last_sync_at else None,
            'is_active': r.is_active,
            'health': {'cpu_load': h.get('cpu_load'), 'free_memory': h.get('free_memory'),
                       'total_memory': h.get('total_memory'), 'uptime': h.get('uptime')},
            'has_error': bool(r.last_config_error), 'last_error': r.last_config_error,
        }