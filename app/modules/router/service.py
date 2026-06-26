"""
Router Service Module
=====================
Business logic for router management with WireGuard VPN and RADIUS integration.
All MikroTik API calls proxied through VPS via SSH.

Flow:
    1. Admin pastes setup script → WireGuard + RADIUS + Walled Garden configured
    2. Test Connection → verifies API reachable
    3. Check RADIUS → verifies auth working (no reconfiguration)
    4. Retry RADIUS → only if check fails, re-runs config
"""

from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime
import json as _json
import os as _os
import tempfile as _tempfile
import base64 as _b64
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
        server = _os.environ.get('RADIUS_SERVER_IP', '')
        if not server:
            try: server = current_app.config.get('RADIUS_SERVER_IP', '')
            except RuntimeError: pass
        return server or self.DEFAULT_RADIUS_SERVER

    def _get_vps_public_key(self) -> str:
        key = _os.environ.get('VPS_WIREGUARD_PUBLIC_KEY', '')
        if not key:
            try: key = current_app.config.get('VPS_WIREGUARD_PUBLIC_KEY', '')
            except RuntimeError: pass
        return key or '274kTJCdNISjJEBMLP9SuqaMyQ8GkDSqjXLttDgNsz4='

    def _parse_uptime(self, uptime_str: str) -> int:
        if not uptime_str: return 0
        seconds = 0
        for pat, mul in [('w', 604800), ('d', 86400), ('h', 3600), ('m', 60), ('s', 1)]:
            m = re.search(rf'(\d+){pat}', uptime_str)
            if m: seconds += int(m.group(1)) * mul
        return seconds

    def _allocate_wireguard_ip(self, organization_id: UUID) -> tuple:
        import hashlib
        from app.models.router import Router
        all_routers = Router.query.with_entities(Router.wireguard_ip).filter(Router.wireguard_ip != None).all()
        all_used_ips = {r.wireguard_ip for r in all_routers if r.wireguard_ip}
        org_existing = self.repository.get_by_organization(organization_id, limit=10000)
        org_used = {r.wireguard_ip for r in org_existing if r.wireguard_ip}
        org_index = None
        for ip in org_used:
            parts = ip.split('.')
            if len(parts) == 4 and parts[0] == '10' and parts[1] == '0':
                org_index = int(parts[2]); break
        if org_index is None:
            org_hash = hashlib.md5(str(organization_id).encode()).hexdigest()
            org_index = int(org_hash[:4], 16) % 200 + 1
        for attempt in range(10):
            current_index = ((org_index - 1 + attempt) % 254) + 1
            for host in range(10, 254):
                candidate = f"10.0.{current_index}.{host}"
                if candidate not in all_used_ips:
                    return candidate, f"10.0.{current_index}.0/24", current_index
        raise BusinessError("No available WireGuard IPs")

    # =========================================================================
    # SSH HELPERS
    # =========================================================================

    def _get_ssh_credentials(self) -> tuple:
        key = _os.environ.get('VPS_SSH_PRIVATE_KEY', '')
        host = _os.environ.get('VPS_HOST', '')
        user = _os.environ.get('VPS_SSH_USER', '')
        if not key:
            try:
                key = current_app.config.get('VPS_SSH_PRIVATE_KEY', '')
                host = current_app.config.get('VPS_HOST', '163.245.217.16')
                user = current_app.config.get('VPS_SSH_USER', 'root')
            except RuntimeError: pass
        if not host: host = '163.245.217.16'
        if not user: user = 'root'
        return key, host, user

    def _ssh_execute(self, script: str, timeout: int = 25) -> tuple:
        key, host, user = self._get_ssh_credentials()
        if not key: raise BusinessError("VPS SSH key not configured")
        if '\\n' in key: key = key.replace('\\n', '\n')
        key_path = None
        try:
            import paramiko
            with _tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False) as f:
                f.write(key); key_path = f.name
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
            logger.error(f"SSH failed: {e}"); return False, '', str(e)
        finally:
            if key_path and _os.path.exists(key_path): _os.unlink(key_path)

    # =========================================================================
    # VPS PROXY
    # =========================================================================

    def _execute_via_vps(self, router: Router, command: str, **kwargs) -> List[Dict[str, Any]]:
        password = self.encryption.decrypt(router.password_encrypted)
        host = str(router.ip_address); port = router.api_port or 8728; username = router.username
        kwargs_b64 = _b64.b64encode(_json.dumps(kwargs).encode()).decode()
        password_b64 = _b64.b64encode(password.encode()).decode()
        username_b64 = _b64.b64encode(username.encode()).decode()
        command_b64 = _b64.b64encode(command.encode()).decode()
        host_b64 = _b64.b64encode(host.encode()).decode()

        script = '\n'.join([
            "import socket,struct,hashlib,binascii,json,base64,sys",
            f"host=base64.b64decode('{host_b64}').decode()",
            f"port={port}",
            f"username=base64.b64decode('{username_b64}').decode()",
            f"password=base64.b64decode('{password_b64}')",
            f"command=base64.b64decode('{command_b64}').decode()",
            f"kwargs=json.loads(base64.b64decode('{kwargs_b64}').decode())",
            "def el(l):",
            " if l<0x80:return bytes([l])",
            " elif l<0x4000:l|=0x8000;return bytes([(l>>8)&0xFF,l&0xFF])",
            " elif l<0x200000:l|=0xC00000;return bytes([(l>>16)&0xFF,(l>>8)&0xFF,l&0xFF])",
            " elif l<0x10000000:l|=0xE0000000;return bytes([(l>>24)&0xFF,(l>>16)&0xFF,(l>>8)&0xFF,l&0xFF])",
            " else:return bytes([0xF0,(l>>24)&0xFF,(l>>16)&0xFF,(l>>8)&0xFF,l&0xFF])",
            "def ew(w):wb=w.encode();return el(len(wb))+wb",
            "def rl(s):",
            " b=s.recv(1);if not b:return -1;b=b[0]",
            " if b&0x80==0:return b",
            " elif b&0xC0==0x80:r=s.recv(1);return((b&0x3F)<<8)|r[0]if r else -1",
            " elif b&0xE0==0xC0:r=s.recv(2);return((b&0x1F)<<16)|(r[0]<<8)|r[1]if len(r)==2 else -1",
            " elif b&0xF0==0xE0:r=s.recv(3);return((b&0x0F)<<24)|(r[0]<<16)|(r[1]<<8)|r[2]if len(r)==3 else -1",
            " elif b&0xF8==0xF0:r=s.recv(4);return((b&0x07)<<32)|(r[0]<<24)|(r[1]<<16)|(r[2]<<8)|r[3]if len(r)==4 else -1",
            " return -1",
            "def re(s,n):",
            " d=b'';while len(d)<n:",
            "  c=s.recv(n-len(d));if not c:return d;d+=c",
            " return d",
            "sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sock.settimeout(20);sock.connect((host,port))",
            "def ss(*w):",
            " for x in w:",
            "  if x:sock.sendall(ew(x))",
            " sock.sendall(el(0))",
            "def rs():",
            " replies=[];cur={};done=False",
            " while not done:",
            "  l=rl(sock);if l<0:break",
            "  if l==0:",
            "   if cur:replies.append(cur);cur={}",
            "   continue",
            "  word=re(sock,l).decode('utf-8',errors='ignore')",
            "  if word=='!done':done=True",
            "  elif word=='!trap':cur['_trap']=True",
            "  elif word=='!fatal':cur['_fatal']=True;done=True",
            "  elif word=='!re':",
            "   if cur:replies.append(cur);cur={}",
            "  elif word.startswith('='):",
            "   inner=word[1:];parts=inner.split('=',1)",
            "   if len(parts)==2:cur[parts[0]]=parts[1]",
            "   else:cur[parts[0]]=''",
            " if cur:replies.append(cur);return replies",
            "ss('/login','=name='+username,'=password='+password.decode())",
            "replies=rs()",
            "has_trap=any(r.get('_trap')for r in replies)",
            "if has_trap:",
            " ss('/login');replies=rs()",
            " challenge=None",
            " for r in replies:",
            "  ret=r.get('ret','')",
            "  if len(ret)==32:challenge=ret;break",
            " if challenge:",
            "  pw=password;cb=binascii.unhexlify(challenge)",
            "  md=hashlib.md5();md.update(b'\\x00');md.update(pw);md.update(cb)",
            "  rh=md.hexdigest().upper()",
            "  ss('/login','=name='+username,'=response='+rh);replies=rs()",
            "for r in replies:",
            " if r.get('_trap'):print(json.dumps([{'error':r.get('message','Login failed')}]));sock.close();sys.exit(0)",
            "words=[command]",
            "for k,v in kwargs.items():",
            " if v is not None:words.append('='+k.replace('_','-')+'='+str(v))",
            "ss(*words);replies=rs()",
            "for r in replies:",
            " if r.get('_trap'):print(json.dumps([{'error':r.get('message','Command failed')}]));sock.close();sys.exit(0)",
            "clean=[{k:v for k,v in r.items()if not k.startswith('_')}for r in replies]",
            "print(json.dumps(clean));sock.close()",
        ])

        success, stdout, stderr = self._ssh_execute(f"python3 << 'PYEOF'\n{script}\nPYEOF")
        if success and stdout:
            try:
                result = _json.loads(stdout)
                if result and len(result) == 1 and 'error' in result[0]: raise BusinessError(result[0]['error'])
                return result
            except _json.JSONDecodeError:
                logger.error(f"VPS parse error: {stdout[:300]}"); raise BusinessError("VPS returned invalid response")
        logger.error(f"VPS SSH failed (stderr: {stderr[:300]})")
        raise BusinessError(f"Failed via VPS: {stderr[:200]}")

    def _execute_client_method_via_vps(self, router: Router, method_name: str, **kwargs) -> Any:
        if method_name == 'get_router_info':
            r = self._execute_via_vps(router, '/system/resource/print')
            i = self._execute_via_vps(router, '/system/identity/print')
            r0, i0 = r[0] if r else {}, i[0] if i else {}
            return {'hostname': i0.get('name'), 'version': r0.get('version'), 'uptime': r0.get('uptime'),
                    'cpu_load': r0.get('cpu-load'), 'free_memory': r0.get('free-memory'),
                    'total_memory': r0.get('total-memory'), 'board_name': r0.get('board-name'),
                    'architecture_name': r0.get('architecture-name')}
        elif method_name == 'get_hotspot_servers': return self._execute_via_vps(router, '/ip/hotspot/print')
        elif method_name == 'get_pppoe_servers': return self._execute_via_vps(router, '/interface/pppoe-server/server/print')
        elif method_name == 'configure_radius':
            rs = kwargs.get('radius_server', self._get_radius_server())
            secret = kwargs.get('radius_secret', router.radius_secret)
            # First check if RADIUS is already correctly configured
            try:
                existing = self._execute_via_vps(router, '/radius/print')
                already_ok = any(
                    e.get('address') == rs and 'hotspot' in str(e.get('service',''))
                    for e in existing
                )
                if already_ok:
                    logger.info(f"RADIUS already configured on {router.name} — skipping")
                    self.repository.update_radius_config_status(router.id, router.organization_id, 'configured')
                    return {'success': True, 'message': 'RADIUS already configured', 'radius_server': rs, 'skipped': True}
            except Exception:
                pass  # Can't check, proceed with config
            
            # Ensure API accessible
            try: self._execute_via_vps(router, '/ip/service/enable', numbers='api')
            except: pass
            try: self._execute_via_vps(router, '/ip/service/set', numbers='api', address='0.0.0.0/0')
            except: pass
            
            # Configure RADIUS
            existing = self._execute_via_vps(router, '/radius/print'); found = False
            for item in existing:
                if item.get('address') == rs:
                    self._execute_via_vps(router, '/radius/set', numbers=item.get('.id'),
                        secret=secret, service='hotspot,ppp', authentication_port='1812',
                        accounting_port='1813', timeout='3s')
                    found = True; break
            if not found:
                self._execute_via_vps(router, '/radius/add', address=rs, secret=secret,
                    service='hotspot,ppp', authentication_port='1812',
                    accounting_port='1813', timeout='3s')
            # Enable on hotspot profiles
            for pid_data in (self._execute_via_vps(router, '/ip/hotspot/user/profile/print') or []):
                pid = pid_data.get('.id')
                if pid:
                    try: self._execute_via_vps(router, '/ip/hotspot/user/profile/set', numbers=pid, **{'use-radius': 'yes'})
                    except: pass
            try: self._execute_via_vps(router, '/ppp/aaa/set', **{'use-radius': 'yes'})
            except: pass
            try: self._execute_via_vps(router, '/radius/incoming/set', accept='yes')
            except: pass
            return {'success': True, 'message': 'RADIUS configured', 'radius_server': rs}
        elif method_name == 'configure_walled_garden':
            domain = kwargs.get('platform_domain', 'isp.bhatek.space')
            results = {'success': True, 'dns_added': False, 'domains_added': 0, 'errors': []}
            try:
                ex = self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/print')
                if not any(e.get('dst-port') == '53' and e.get('protocol') == 'udp' for e in ex):
                    self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/add',
                        dst_port='53', protocol='udp', action='accept', comment='DNS')
                results['dns_added'] = True
            except Exception as e: results['errors'].append(str(e))
            for d in [{'host': domain, 'comment': 'ISP Portal'}, {'host': '*.safaricom.co.ke', 'comment': 'M-Pesa'},
                       {'host': '*.googleapis.com', 'comment': 'Fonts'}, {'host': '*.gstatic.com', 'comment': 'CDN'}]:
                try:
                    ex = self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/print')
                    if not any(e.get('dst-host') == d['host'] for e in ex):
                        self._execute_via_vps(router, '/ip/hotspot/walled-garden/ip/add',
                            **{'dst-host': d['host']}, action='accept', comment=d['comment'])
                        results['domains_added'] += 1
                except Exception as e: results['errors'].append(str(e))
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
            db.session.add(nas); db.session.flush(); router.nas_entry_id = nas.id
            return nas
        except Exception as e: db.session.rollback(); raise BusinessError(f"NAS entry failed: {e}")

    # =========================================================================
    # SETUP SCRIPT
    # =========================================================================

    def _generate_mikrotik_setup_script(self, wireguard_ip: str, mikrotik_private_key: str,
                                          radius_secret: str, router_name: str = "Router",
                                          organization_name: str = "ISP") -> str:
        pk = self._get_vps_public_key()
        return f"""# ISP Platform - MikroTik Setup
# Router: {router_name} | WireGuard IP: {wireguard_ip}
/interface wireguard add listen-port=51820 private-key="{mikrotik_private_key}" name=wg-to-vps
/interface wireguard peers add allowed-address=10.0.0.1/32 endpoint-address={self.VPS_ENDPOINT} endpoint-port=51820 interface=wg-to-vps persistent-keepalive=25 public-key="{pk}"
/ip address add address={wireguard_ip}/16 interface=wg-to-vps network=10.0.0.0
/ip route add dst-address=10.0.0.1/32 gateway=wg-to-vps
/ip firewall filter add chain=input src-address=10.0.0.0/16 action=accept comment="Allow ISP Platform" place-before=0
/interface list member add interface=wg-to-vps list=LAN
/ip service enable api
/ip service set api address=0.0.0.0/0
/ip service enable winbox
/ip service set winbox address=0.0.0.0/0
/ip service enable ssh
/ip service set ssh address=0.0.0.0/0
/radius add address=10.0.0.1 secret="{radius_secret}" service=hotspot,ppp authentication-port=1812 accounting-port=1813 timeout=3s
/ip hotspot profile set [find] use-radius=yes
/ppp aaa set use-radius=yes
/radius incoming set accept=yes
/ip hotspot walled-garden ip add dst-port=53 protocol=udp action=accept comment="Allow DNS"
/ip hotspot walled-garden ip add dst-host=isp.bhatek.space action=accept comment="ISP Portal"
/ip hotspot walled-garden ip add dst-host=*.safaricom.co.ke action=accept comment="M-Pesa API"
/ip hotspot walled-garden ip add dst-host=*.googleapis.com action=accept comment="Google Fonts"
/ip hotspot walled-garden ip add dst-host=*.gstatic.com action=accept comment="Google CDN"
# Verify: /ping 10.0.0.1 count=3"""

    # =========================================================================
    # CREATE ROUTER
    # =========================================================================

    def create_router(self, organization_id: UUID, network_id: UUID, data: Dict[str, Any]) -> Dict[str, Any]:
        for f in ['name', 'username', 'password']:
            if not data.get(f): raise ValidationError(f"'{f}' is required")
        local_ip = data.get('ip_address') or data.get('local_ip')
        if not local_ip: raise ValidationError("ip_address or local_ip required")
        org = Organization.query.get(organization_id)
        if not org: raise ValidationError("Organization not found")
        wg_priv, wg_pub = self.wireguard.generate_peer_keypair()
        wg_ip, subnet, idx = self._allocate_wireguard_ip(organization_id)
        radius_secret = self._generate_radius_secret()
        enc_pw = self.encryption.encrypt(data['password']); enc_wg = self.encryption.encrypt(wg_priv)
        router = self.repository.create({
            'organization_id': organization_id, 'network_id': network_id, 'name': data['name'],
            'model': data.get('model'), 'ip_address': wg_ip, 'local_ip': local_ip,
            'api_port': data.get('api_port', 8728), 'username': data['username'],
            'password_encrypted': enc_pw, 'location': data.get('location'),
            'description': data.get('description'), 'is_active': True, 'status': 'pending_wireguard',
            'radius_secret': radius_secret, 'radius_config_status': 'pending', 'auto_config_attempts': 0,
            'wireguard_ip': wg_ip, 'wireguard_public_key': wg_pub, 'wireguard_private_key_encrypted': enc_wg,
        })
        self._create_nas_entry(router, radius_secret)
        db.session.commit()
        wg_ok = self.wireguard.add_peer(wg_pub, f"{wg_ip}/32")
        script = self._generate_mikrotik_setup_script(wg_ip, wg_priv, radius_secret, router.name, org.name)
        logger.info(f"Router created: {router.name} (WG: {wg_ip}, peer: {'OK' if wg_ok else 'FAILED'})")
        return {'success': True, 'router': router,
                'wireguard': {'ip': wg_ip, 'public_key': wg_pub, 'private_key': wg_priv, 'peer_added_to_vps': wg_ok},
                'radius': {'secret': radius_secret, 'server': self._get_radius_server(), 'auth_port': 1812, 'acct_port': 1813},
                'setup_script': script, 'next_step': 'Paste script into MikroTik terminal, then click Test Connection.'}

    # =========================================================================
    # TEST CONNECTION
    # =========================================================================

    def test_connection(self, router_id: UUID, organization_id: UUID, method: str = 'api') -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)
        if method != 'api': raise ValidationError(f"Unsupported: {method}")
        try:
            result = self._execute_via_vps(router, '/system/resource/print')
            if result and len(result) > 0:
                r = result[0]; self.repository.update_status(router_id, organization_id, 'online')
                return {'success': True, 'connected': True, 'router_info': {
                    'version': r.get('version','?'), 'board_name': r.get('board-name','?'),
                    'cpu_load': r.get('cpu-load','?'), 'uptime': r.get('uptime','?'),
                    'free_memory': r.get('free-memory','?'), 'total_memory': r.get('total-memory','?')}}
            self.repository.update_status(router_id, organization_id, 'offline')
            return {'success': False, 'connected': False, 'error': 'No response'}
        except Exception as e:
            self.repository.update_status(router_id, organization_id, 'error', error_message=str(e))
            raise BusinessError(f"Connection test failed: {e}")

    # =========================================================================
    # CHECK RADIUS — Verifies without reconfiguring
    # =========================================================================

    def check_radius_status(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Check if RADIUS is working without making any changes."""
        router = self.get_router(router_id, organization_id)
        result = {'success': True, 'nas_exists': False, 'api_reachable': False, 'radius_entries': 0}
        
        # Check NAS entry
        nas = NAS.query.filter_by(router_id=router_id, is_active=True).first()
        result['nas_exists'] = nas is not None
        
        # Check if we can reach the API
        try:
            radius_entries = self._execute_via_vps(router, '/radius/print')
            result['api_reachable'] = True
            result['radius_entries'] = len(radius_entries)
            # Check if correct RADIUS server is configured
            has_correct = any(
                e.get('address') == '10.0.0.1' and 'hotspot' in str(e.get('service',''))
                for e in radius_entries
            )
            result['radius_configured'] = has_correct
        except Exception:
            result['api_reachable'] = False
            result['radius_configured'] = False
        
        return result

    # =========================================================================
    # RETRY RADIUS — Only if check fails
    # =========================================================================

    def retry_radius_configuration(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        if not r.radius_secret: raise BusinessError("No RADIUS secret")
        
        # First check if already configured
        check = self.check_radius_status(router_id, organization_id)
        if check.get('radius_configured'):
            self.repository.update_radius_config_status(r.id, organization_id, 'configured')
            return {'success': True, 'message': 'RADIUS is already configured and working', 'skipped': True}
        
        # Not configured — run configuration
        try:
            self._execute_client_method_via_vps(r, 'configure_radius', radius_secret=r.radius_secret)
            self.repository.update_radius_config_status(r.id, organization_id, 'configured')
            return {'success': True, 'message': 'RADIUS configured successfully'}
        except Exception as e:
            self.repository.update_radius_config_status(r.id, organization_id, 'failed', error=str(e))
            return {'success': False, 'message': str(e)}

    def configure_radius_manual(self, router_id: UUID, organization_id: UUID, radius_server: str, radius_secret: str) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        try:
            self._execute_client_method_via_vps(r, 'configure_radius', radius_server=radius_server, radius_secret=radius_secret)
            self.repository.update_radius_config_status(router_id, organization_id, 'configured')
            return {'success': True, 'message': 'RADIUS configured'}
        except Exception as e: raise BusinessError(str(e))

    # =========================================================================
    # DISCOVERY
    # =========================================================================

    def discover_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id)
        try:
            info = self._execute_client_method_via_vps(r, 'get_router_info'); caps = ['api']
            try:
                if self._execute_client_method_via_vps(r, 'get_hotspot_servers'): caps.append('hotspot')
            except: pass
            try:
                if self._execute_client_method_via_vps(r, 'get_pppoe_servers'): caps.append('pppoe')
            except: pass
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
            ut = info.get('uptime', '0s'); us = self._parse_uptime(ut)
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
                if not n: continue
                ex = self.hotspot_repo.get_by_router_and_hotspot_id(r.id, r.organization_id, n)
                d = {'organization_id': r.organization_id, 'router_id': r.id, 'name': n, 'hotspot_id': n,
                     'interface': hs.get('interface'), 'is_active': hs.get('disabled') != 'true'}
                if ex: self.hotspot_repo.update(ex.id, r.organization_id, d)
                else: self.hotspot_repo.create(d)
                results['hotspot_synced'] += 1
        except Exception as e: results['errors'].append(f"Hotspot: {e}")
        try:
            for ps in (self._execute_client_method_via_vps(r, 'get_pppoe_servers') or []):
                n = ps.get('name', '')
                if not n: continue
                ex = self.pppoe_repo.get_by_router_and_name(r.id, r.organization_id, n)
                d = {'organization_id': r.organization_id, 'router_id': r.id, 'name': n,
                     'interface': ps.get('interface'), 'service_name': ps.get('service-name'),
                     'mtu': int(ps.get('mtu', 1492)), 'is_active': ps.get('disabled') != 'true'}
                if ex: self.pppoe_repo.update(ex.id, r.organization_id, d)
                else: self.pppoe_repo.create(d)
                results['pppoe_synced'] += 1
        except Exception as e: results['errors'].append(f"PPPoE: {e}")
        self.repository.update(router_id, organization_id, {'last_sync_at': datetime.utcnow(), 'status': 'online'})
        return results

    # =========================================================================
    # CRUD
    # =========================================================================

    def get_router(self, router_id: UUID, organization_id: UUID) -> Router:
        r = self.repository.get_by_id(router_id, organization_id)
        if not r: raise NotFoundError("Router not found")
        return r

    def get_routers_by_organization(self, organization_id: UUID, skip=0, limit=100, status=None, network_id=None, radius_config_status=None) -> List[Router]:
        return self.repository.get_by_organization(organization_id, skip, limit, status, network_id, radius_config_status)

    def get_routers_by_network(self, network_id: UUID, organization_id: UUID) -> List[Router]:
        return self.repository.get_by_network(network_id, organization_id)

    def get_routers_pending_radius_config(self, organization_id: UUID) -> List[Router]:
        return self.repository.get_routers_pending_radius_config(organization_id)

    def get_router_by_ip(self, ip_address: str, organization_id: UUID) -> Optional[Router]:
        return self.repository.get_by_ip(ip_address, organization_id)

    def update_router(self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Router:
        if data.get("password"): data["password_encrypted"] = self.encryption.encrypt(data.pop("password"))
        elif "password" in data: data.pop("password")
        r = self.repository.update(router_id, organization_id, data)
        if not r: raise NotFoundError("Router not found")
        return r

    def delete_router(self, router_id: UUID, organization_id: UUID, soft_delete: bool = True) -> None:
        r = self.repository.get_by_id(router_id, organization_id, include_inactive=True)
        if not r: raise NotFoundError("Router not found")
        if r.wireguard_public_key:
            try: self.wireguard.remove_peer(r.wireguard_public_key)
            except Exception as e: logger.warning(f"WG peer remove failed: {e}")
        if not soft_delete:
            if len(self.hotspot_repo.get_by_router(router_id, organization_id)) > 0 or len(self.pppoe_repo.get_by_router(router_id, organization_id)) > 0:
                raise BusinessError("Router has active services")
        self.repository.delete(router_id, organization_id, soft_delete)

    def configure_walled_garden(self, router_id: UUID, organization_id: UUID, **kwargs) -> Dict[str, Any]:
        return self._execute_client_method_via_vps(self.get_router(router_id, organization_id), 'configure_walled_garden', **kwargs)

    def get_connection_status(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        r = self.get_router(router_id, organization_id); s = r.settings or {}; h = s.get('health', {})
        return {'router_id': str(r.id), 'name': r.name, 'ip_address': str(r.ip_address),
                'local_ip': r.local_ip, 'wireguard_ip': r.wireguard_ip, 'status': r.status,
                'radius_config_status': r.radius_config_status, 'auto_config_attempts': r.auto_config_attempts or 0,
                'last_seen_at': r.last_seen_at.isoformat() if r.last_seen_at else None,
                'last_sync_at': r.last_sync_at.isoformat() if r.last_sync_at else None,
                'is_active': r.is_active, 'health': h, 'has_error': bool(r.last_config_error), 'last_error': r.last_config_error}