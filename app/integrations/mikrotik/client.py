"""
MikroTik RouterOS API Client
============================
Production-ready client for multi-tenant ISP router management.

Protocol: Variable-length word encoding over TCP (8728) / SSL (8729)
Auth: Modern direct login (v6.43+/v7.x) + legacy challenge fallback
"""

import hashlib
import socket
import ssl
import struct
import binascii
import time
import threading
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

from flask import current_app, has_app_context

from app.core.logging.logger import logger
from app.core.security.encryption import EncryptionService


class MikroTikAPIError(Exception): pass
class MikroTikConnectionError(MikroTikAPIError): pass
class MikroTikAuthError(MikroTikAPIError): pass
class MikroTikCommandError(MikroTikAPIError): pass


class RouterOSEncoder:
    """Variable-length word encoding for RouterOS API protocol."""

    @staticmethod
    def encode_length(length: int) -> bytes:
        if length < 0x80: return bytes([length])
        elif length < 0x4000: length |= 0x8000; return bytes([(length>>8)&0xFF, length&0xFF])
        elif length < 0x200000: length |= 0xC00000; return bytes([(length>>16)&0xFF, (length>>8)&0xFF, length&0xFF])
        elif length < 0x10000000: length |= 0xE0000000; return bytes([(length>>24)&0xFF, (length>>16)&0xFF, (length>>8)&0xFF, length&0xFF])
        else: return bytes([0xF0, (length>>24)&0xFF, (length>>16)&0xFF, (length>>8)&0xFF, length&0xFF])

    @staticmethod
    def encode_word(word: str) -> bytes:
        wb = word.encode('utf-8')
        return RouterOSEncoder.encode_length(len(wb)) + wb

    @staticmethod
    def read_length(sock: socket.socket) -> int:
        first = sock.recv(1)
        if not first: return -1
        b = first[0]
        if b & 0x80 == 0: return b
        elif b & 0xC0 == 0x80: r = sock.recv(1); return ((b&0x3F)<<8)|r[0] if r else -1
        elif b & 0xE0 == 0xC0: r = sock.recv(2); return ((b&0x1F)<<16)|(r[0]<<8)|r[1] if len(r)==2 else -1
        elif b & 0xF0 == 0xE0: r = sock.recv(3); return ((b&0x0F)<<24)|(r[0]<<16)|(r[1]<<8)|r[2] if len(r)==3 else -1
        elif b & 0xF8 == 0xF0: r = sock.recv(4); return ((b&0x07)<<32)|(r[0]<<24)|(r[1]<<16)|(r[2]<<8)|r[3] if len(r)==4 else -1
        return -1


class MikroTikConnection:
    """Sentence-aware RouterOS API connection with modern + legacy auth."""

    DEFAULT_TIMEOUT = 30
    CONNECT_TIMEOUT = 10

    def __init__(self, host: str, username: str, password: str,
                 port: int = 8728, use_ssl: bool = False, timeout: int = DEFAULT_TIMEOUT):
        if not host: raise ValueError("Host required")
        if not username: raise ValueError("Username required")
        self.host = host; self.username = username; self.password = password
        self.port = port; self.use_ssl = use_ssl; self.timeout = timeout
        self.socket: Optional[socket.socket] = None
        self._connected: bool = False
        self._lock = threading.RLock()
        self._last_used = datetime.now()
        self._tag_counter = 0

    @property
    def is_connected(self) -> bool:
        if not self._connected or self.socket is None: return False
        try: self.socket.getpeername(); return True
        except: self._connected = False; return False

    @property
    def last_used(self) -> datetime: return self._last_used

    @property
    def idle_seconds(self) -> float: return (datetime.now() - self._last_used).total_seconds()

    def _next_tag(self) -> str: self._tag_counter += 1; return str(self._tag_counter)

    def connect(self) -> None:
        with self._lock:
            if self.is_connected: return
            self._disconnect_socket()
            try:
                raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw.settimeout(self.CONNECT_TIMEOUT)
                raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if self.use_ssl:
                    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
                    self.socket = ctx.wrap_socket(raw, server_hostname=self.host)
                else: self.socket = raw
                self.socket.connect((self.host, self.port)); self.socket.settimeout(self.timeout)
                self._login(); self._connected = True; self._last_used = datetime.now()
                logger.info(f"Connected to {self.host}:{self.port}")
            except (socket.timeout, TimeoutError) as e: self._disconnect_socket(); raise MikroTikConnectionError(f"Timeout: {self.host}") from e
            except ConnectionRefusedError as e: self._disconnect_socket(); raise MikroTikConnectionError(f"Refused: {self.host}") from e
            except MikroTikAuthError: self._disconnect_socket(); raise
            except Exception as e: self._disconnect_socket(); raise MikroTikConnectionError(str(e)) from e

    def disconnect(self) -> None:
        with self._lock: self._disconnect_socket(); self._connected = False

    def _disconnect_socket(self) -> None:
        if self.socket:
            try: self.socket.close()
            except: pass
            finally: self.socket = None

    def _login(self) -> None:
        if not self.socket: raise MikroTikAuthError("No socket")
        self._send_sentence('/login', f'=name={self.username}', f'=password={self.password}')
        replies = self._read_sentence()
        if not any(r.get('_trap') for r in replies) and not any(r.get('_fatal') for r in replies):
            logger.debug(f"Modern login OK: {self.host}"); return
        for r in replies:
            msg = r.get('message','')
            if 'invalid' in msg.lower() or 'cannot' in msg.lower(): raise MikroTikAuthError(f"Login rejected: {msg}")
        self._login_challenge()

    def _login_challenge(self) -> None:
        self._send_sentence('/login'); replies = self._read_sentence()
        challenge = next((r.get('ret') for r in replies if r.get('ret') and len(r.get('ret',''))==32), None)
        if not challenge: raise MikroTikAuthError("No challenge")
        pw = self.password.encode('utf-8'); cb = binascii.unhexlify(challenge)
        md5 = hashlib.md5(); md5.update(b'\x00'); md5.update(pw); md5.update(cb); rh = md5.hexdigest().upper()
        self._send_sentence('/login', f'=name={self.username}', f'=response={rh}')
        replies2 = self._read_sentence()
        if any(r.get('_trap') for r in replies2):
            msg = next((r.get('message','') for r in replies2 if r.get('message')), 'Unknown')
            raise MikroTikAuthError(f"Challenge rejected: {msg}")
        logger.debug(f"Challenge login OK: {self.host}")

    def execute(self, command: str, **kwargs) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.is_connected: self.connect()
            words = [command, f'.tag={self._next_tag()}']
            for k, v in kwargs.items():
                if v is not None: words.append(f'={k.replace("_","-")}={v}')
            try:
                self._send_sentence(*words); replies = self._read_sentence(); self._last_used = datetime.now()
                for r in replies:
                    if r.get('_trap'): raise MikroTikCommandError(r.get('message','Router error'))
                    if r.get('_fatal'): raise MikroTikCommandError(f"Fatal: {r.get('message','')}")
                return [{k:v for k,v in r.items() if not k.startswith('_')} for r in replies]
            except (socket.timeout, socket.error, ConnectionError) as e:
                self._connected = False; raise MikroTikConnectionError(str(e)) from e
            except MikroTikAPIError: raise
            except Exception as e: raise MikroTikCommandError(str(e)) from e

    def _send_sentence(self, *words: str) -> None:
        if not self.socket: raise MikroTikConnectionError("No socket")
        for w in words:
            if w: self.socket.sendall(RouterOSEncoder.encode_word(w))
        self.socket.sendall(RouterOSEncoder.encode_length(0))

    def _read_sentence(self) -> List[Dict[str, Any]]:
        if not self.socket: raise MikroTikConnectionError("No socket")
        replies, current, done = [], {}, False
        while not done:
            length = RouterOSEncoder.read_length(self.socket)
            if length < 0: raise MikroTikConnectionError("Closed")
            if length == 0:
                if current: replies.append(current); current = {}
                continue
            word = self._read_exact(length).decode('utf-8', errors='ignore')
            if word == '!done': done = True
            elif word == '!trap': current['_trap'] = True
            elif word == '!fatal': current['_fatal'] = True; done = True
            elif word == '!re':
                if current: replies.append(current)
                current = {}
            elif word.startswith('='):
                inner = word[1:]; parts = inner.split('=', 1)
                current[parts[0]] = parts[1] if len(parts)==2 else ''
        if current: replies.append(current)
        return replies

    def _read_exact(self, size: int) -> bytes:
        if not self.socket: raise MikroTikConnectionError("No socket")
        data = b''
        while len(data) < size:
            try:
                chunk = self.socket.recv(size - len(data))
                if not chunk: raise MikroTikConnectionError("Closed")
                data += chunk
            except socket.timeout: raise MikroTikConnectionError("Timeout")
            except socket.error as e: raise MikroTikConnectionError(str(e))
        return data

    def ping(self) -> bool:
        try: self.execute('/system/resource/print'); return True
        except: return False


class MikroTikClient:
    """High-level MikroTik API client for multi-tenant ISP management."""

    DEFAULT_CONNECTION_TIMEOUT = 300
    MAX_TOTAL_CONNECTIONS = 100

    def __init__(self):
        self._connections: Dict[str, MikroTikConnection] = {}
        self._lock = threading.RLock()
        self.encryption = EncryptionService()
        self.connection_timeout = self.DEFAULT_CONNECTION_TIMEOUT
        self._password_cache: Dict[str, str] = {}

    def _get_key(self, rid, host, port): return f"{rid or '?'}:{host}:{port}"

    def _get_password(self, encrypted: str) -> str:
        with self._lock:
            if encrypted not in self._password_cache:
                self._password_cache[encrypted] = self.encryption.decrypt(encrypted)
            return self._password_cache[encrypted]

    def _resolve_id(self, rd, path, attr, val):
        try:
            for i in self.execute(rd, f'{path}/print'):
                if i.get(attr) == val: return i.get('.id')
        except: pass
        return None

    def _resolve_all_ids(self, rd, path):
        try: return [i.get('.id') for i in self.execute(rd, f'{path}/print') if i.get('.id')]
        except: return []

    def get_connection(self, router_data: Dict[str, Any]) -> MikroTikConnection:
        rid = router_data.get('id'); host = router_data.get('ip_address')
        port = router_data.get('api_port', 8728); ssl_flag = router_data.get('api_ssl', False)
        username = router_data.get('username')
        if not host: raise ValueError("ip_address required")
        if not username: raise ValueError("username required")
        encrypted = router_data.get('password_encrypted', '')
        if not encrypted: raise MikroTikAuthError(f"No password for {host}")
        key = self._get_key(rid, host, port)
        with self._lock:
            if key in self._connections:
                conn = self._connections[key]
                if conn.is_connected:
                    try: conn.socket.getpeername(); conn._last_used = datetime.now(); return conn
                    except: conn.disconnect(); del self._connections[key]
                else: conn.disconnect(); del self._connections[key]
            if len(self._connections) >= self.MAX_TOTAL_CONNECTIONS: self._cleanup_oldest()
            timeout = current_app.config.get('MIKROTIK_API_TIMEOUT', 30) if has_app_context() and current_app else 30
            conn = MikroTikConnection(host=host, username=username, password=self._get_password(encrypted),
                                       port=port, use_ssl=ssl_flag, timeout=timeout)
            conn.connect(); self._connections[key] = conn; return conn

    def _cleanup_oldest(self):
        with self._lock:
            for key, conn in sorted(self._connections.items(), key=lambda x: x[1].last_used)[:10]:
                try: conn.disconnect()
                except: pass
                del self._connections[key]

    def invalidate_connection(self, rd):
        key = self._get_key(rd.get('id'), rd.get('ip_address'), rd.get('api_port', 8728))
        with self._lock:
            if key in self._connections:
                try: self._connections[key].disconnect()
                except: pass
                del self._connections[key]

    def execute(self, rd, command, retries=3, **kwargs):
        last_err = None; backoff = 1
        for attempt in range(retries):
            try: return self.get_connection(rd).execute(command, **kwargs)
            except (MikroTikConnectionError, socket.timeout, ConnectionError) as e:
                last_err = e; self.invalidate_connection(rd)
                if attempt < retries-1: time.sleep(backoff); backoff *= 2
                else: raise MikroTikAPIError(f"Failed after {retries}: {last_err}") from last_err
            except MikroTikAPIError: raise
        raise MikroTikAPIError(f"Failed: {last_err}")

    # -------------------------------------------------------------------------
    # CONNECTION TEST
    # -------------------------------------------------------------------------

    def test_connection(self, host, username, password, port=8728, use_ssl=False):
        conn = None
        try:
            conn = MikroTikConnection(host=host, username=username, password=password, port=port, use_ssl=use_ssl, timeout=10)
            conn.connect()
            result = conn.execute('/system/resource/print')
            if result:
                r = result[0]
                return {'success': True, 'connected': True, 'router_info': {
                    'version': r.get('version','?'), 'board_name': r.get('board-name','?'),
                    'cpu_load': r.get('cpu-load','?'), 'uptime': r.get('uptime','?'),
                    'free_memory': r.get('free-memory','?'), 'total_memory': r.get('total-memory','?'),
                    'architecture_name': r.get('architecture-name','?'),
                }}
            return {'success': False, 'connected': False, 'error': 'No response'}
        except MikroTikConnectionError as e: return {'success': False, 'connected': False, 'error': str(e)}
        except MikroTikAuthError: return {'success': False, 'connected': False, 'error': 'Auth failed'}
        except Exception as e: return {'success': False, 'connected': False, 'error': str(e)}
        finally:
            if conn:
                try: conn.disconnect()
                except: pass

    # -------------------------------------------------------------------------
    # HEALTH
    # -------------------------------------------------------------------------

    def get_router_info(self, rd):
        try:
            r = self.execute(rd, '/system/resource/print'); i = self.execute(rd, '/system/identity/print')
            r0, i0 = r[0] if r else {}, i[0] if i else {}
            return {
                'hostname': i0.get('name'), 'version': r0.get('version'),
                'uptime': r0.get('uptime'), 'cpu_load': r0.get('cpu-load'),
                'free_memory': r0.get('free-memory'), 'total_memory': r0.get('total-memory'),
                'board_name': r0.get('board-name'), 'architecture_name': r0.get('architecture-name'),
            }
        except: return {}

    def health_check(self, rd):
        try:
            start = time.time(); result = self.execute(rd, '/system/resource/print', retries=2)
            rt = (time.time() - start) * 1000
            if result:
                r = result[0]
                return {'status': 'healthy', 'response_time_ms': round(rt,2),
                        'cpu_load': r.get('cpu-load'), 'uptime': r.get('uptime'),
                        'free_memory': r.get('free-memory'), 'total_memory': r.get('total-memory')}
            return {'status': 'unhealthy', 'error': 'No response'}
        except Exception as e: return {'status': 'unhealthy', 'error': str(e)}

    def get_interface_stats(self, rd):
        try: return self.execute(rd, '/interface/print')
        except: return []

    # -------------------------------------------------------------------------
    # HOTSPOT
    # -------------------------------------------------------------------------

    def get_hotspot_servers(self, rd):
        try: return self.execute(rd, '/ip/hotspot/print')
        except: return []

    def get_hotspot_users(self, rd, server_id=None):
        p = {'server': server_id} if server_id else {}
        try: return self.execute(rd, '/ip/hotspot/user/print', **p)
        except: return []

    def create_hotspot_user(self, rd, server, username, password, profile, **extra):
        params = {'server': server, 'name': username, 'password': password, 'profile': profile}
        for k, v in extra.items():
            if v is not None: params[k.replace('_','-')] = str(v) if not isinstance(v, str) else v
        try: self.execute(rd, '/ip/hotspot/user/add', **params); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    def set_hotspot_user(self, rd, username, **kwargs):
        uid = self._resolve_id(rd, '/ip/hotspot/user', 'name', username)
        if not uid: return {'success': False, 'error': f'User {username} not found'}
        try: self.execute(rd, '/ip/hotspot/user/set', numbers=uid, **kwargs); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    def disable_hotspot_user(self, rd, username): return self.set_hotspot_user(rd, username, disabled='yes')
    def enable_hotspot_user(self, rd, username): return self.set_hotspot_user(rd, username, disabled='no')

    def remove_hotspot_user(self, rd, username):
        uid = self._resolve_id(rd, '/ip/hotspot/user', 'name', username)
        if not uid: return {'success': False, 'error': f'User {username} not found'}
        try: self.execute(rd, '/ip/hotspot/user/remove', numbers=uid); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    def get_active_sessions(self, rd, server_id=None):
        p = {'server': server_id} if server_id else {}
        try: return self.execute(rd, '/ip/hotspot/active/print', **p)
        except: return []

    def disconnect_hotspot_user(self, rd, username):
        for s in self.get_active_sessions(rd):
            if s.get('user') == username:
                try: self.execute(rd, '/ip/hotspot/active/remove', numbers=s.get('.id')); return {'success': True}
                except Exception as e: return {'success': False, 'error': str(e)}
        return {'success': False, 'error': 'Not found'}

    def get_hotspot_profiles(self, rd):
        try: return self.execute(rd, '/ip/hotspot/user/profile/print')
        except: return []

    def create_hotspot_profile(self, rd, name, **kwargs):
        params = {'name': name}
        for k, v in kwargs.items():
            if v is not None: params[k.replace('_','-')] = str(v) if not isinstance(v, str) else v
        try: self.execute(rd, '/ip/hotspot/user/profile/add', **params); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # PPPoE
    # -------------------------------------------------------------------------

    def get_pppoe_servers(self, rd):
        try: return self.execute(rd, '/interface/pppoe-server/server/print')
        except: return []

    def get_pppoe_secrets(self, rd):
        try: return self.execute(rd, '/ppp/secret/print')
        except: return []

    def create_pppoe_secret(self, rd, username, password, profile, **extra):
        params = {'name': username, 'password': password, 'profile': profile}
        for k, v in extra.items():
            if v is not None: params[k.replace('_','-')] = v
        try: self.execute(rd, '/ppp/secret/add', **params); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    def set_pppoe_secret(self, rd, username, **kwargs):
        uid = self._resolve_id(rd, '/ppp/secret', 'name', username)
        if not uid: return {'success': False, 'error': f'Secret {username} not found'}
        try: self.execute(rd, '/ppp/secret/set', numbers=uid, **kwargs); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    def disable_pppoe_secret(self, rd, username): return self.set_pppoe_secret(rd, username, disabled='yes')
    def enable_pppoe_secret(self, rd, username): return self.set_pppoe_secret(rd, username, disabled='no')

    def remove_pppoe_secret(self, rd, username):
        uid = self._resolve_id(rd, '/ppp/secret', 'name', username)
        if not uid: return {'success': False, 'error': f'Secret {username} not found'}
        try: self.execute(rd, '/ppp/secret/remove', numbers=uid); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    def get_pppoe_active_sessions(self, rd):
        try: return self.execute(rd, '/ppp/active/print')
        except: return []

    def disconnect_pppoe_user(self, rd, username):
        for s in self.get_pppoe_active_sessions(rd):
            if s.get('name') == username:
                try: self.execute(rd, '/ppp/active/remove', numbers=s.get('.id')); return {'success': True}
                except Exception as e: return {'success': False, 'error': str(e)}
        return {'success': False, 'error': 'Not found'}

    # -------------------------------------------------------------------------
    # RADIUS
    # -------------------------------------------------------------------------

    def configure_radius(self, rd, radius_server, radius_secret):
        try:
            existing = self.execute(rd, '/radius/print'); found = False
            for item in existing:
                if item.get('address') == radius_server:
                    self.execute(rd, '/radius/set', numbers=item.get('.id'),
                        secret=radius_secret, service='hotspot,ppp',
                        authentication_port='1812', accounting_port='1813'); found = True; break
            if not found:
                self.execute(rd, '/radius/add', address=radius_server, secret=radius_secret,
                    service='hotspot,ppp', authentication_port='1812', accounting_port='1813')
            for pid in self._resolve_all_ids(rd, '/ip/hotspot/user/profile'):
                try: self.execute(rd, '/ip/hotspot/user/profile/set', numbers=pid, **{'use-radius': 'yes'})
                except: pass
            try: self.execute(rd, '/ppp/aaa/set', **{'use-radius': 'yes'})
            except: pass
            try: self.execute(rd, '/radius/incoming/set', accept='yes')
            except: pass
            return {'success': True, 'message': 'RADIUS configured'}
        except Exception as e: return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # WALLED GARDEN
    # -------------------------------------------------------------------------

    def configure_walled_garden(self, rd, platform_domain=None, additional_domains=None):
        platform = platform_domain or 'isp.bhatek.space'
        results = {'success': True, 'dns_added': False, 'domains_added': 0, 'errors': []}
        try:
            ex = self.execute(rd, '/ip/hotspot/walled-garden/ip/print')
            if not any(e.get('dst-port')=='53' and e.get('protocol')=='udp' for e in ex):
                self.execute(rd, '/ip/hotspot/walled-garden/ip/add', dst_port='53', protocol='udp', action='accept', comment='DNS')
            results['dns_added'] = True
        except Exception as e: results['errors'].append(str(e))
        domains = [{'host': platform, 'comment': 'ISP Portal'},
                   {'host': '*.safaricom.co.ke', 'comment': 'M-Pesa'},
                   {'host': '*.googleapis.com', 'comment': 'Fonts'},
                   {'host': '*.gstatic.com', 'comment': 'CDN'}]
        if additional_domains:
            for d in additional_domains: domains.append({'host': d, 'comment': 'Custom'})
        for d in domains:
            try:
                ex = self.execute(rd, '/ip/hotspot/walled-garden/ip/print')
                if not any(e.get('dst-host')==d['host'] for e in ex):
                    self.execute(rd, '/ip/hotspot/walled-garden/ip/add', **{'dst-host': d['host']}, action='accept', comment=d['comment'])
                    results['domains_added'] += 1
            except Exception as e: results['errors'].append(str(e))
        return results

    # -------------------------------------------------------------------------
    # BANDWIDTH
    # -------------------------------------------------------------------------

    def set_bandwidth_limit(self, rd, target, upload_mbps, download_mbps, queue_type='default'):
        try:
            rate = f"{upload_mbps}M/{download_mbps}M"; name = f"limit_{target.replace('/','_').replace(':','_')}"
            self.execute(rd, '/queue/simple/add', name=name, target=target, max_limit=rate, queue=queue_type)
            return {'success': True, 'name': name, 'rate_limit': rate}
        except Exception as e: return {'success': False, 'error': str(e)}

    def get_simple_queues(self, rd):
        try: return self.execute(rd, '/queue/simple/print')
        except: return []

    def remove_simple_queue(self, rd, qid):
        try: self.execute(rd, '/queue/simple/remove', numbers=qid); return {'success': True}
        except Exception as e: return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # LIFECYCLE
    # -------------------------------------------------------------------------

    def close_all(self):
        with self._lock:
            for c in list(self._connections.values()):
                try: c.disconnect()
                except: pass
            self._connections.clear()

    def __del__(self):
        try: self.close_all()
        except: pass