"""
MikroTik RouterOS API Client
============================
Production-ready client for multi-tenant ISP router management.

Protocol: Sentence-based variable-length word encoding over TCP (8728) / SSL (8729)
Auth: Modern direct login (v6.43+/v7.x checks !done) + legacy challenge fallback

Key Features:
    - Sentence-aware I/O (reads complete RouterOS sentences)
    - Correct =key=value parsing (=.id=*1 -> key='.id', value='*1')
    - Modern login: checks !done for success (not =ret=)
    - Legacy login: extracts =ret= as challenge (not =challenge=)
    - Errors only raised on !trap and !fatal (not on =message=)
    - Request tagging (.tag) for async operations
    - Thread-safe password caching
    - Connection pooling with SO_KEEPALIVE
    - API-safe: resolves .id from names (no CLI [find] in API)
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


# =============================================================================
# EXCEPTIONS
# =============================================================================

class MikroTikAPIError(Exception):
    """Base exception for MikroTik API errors."""
    pass


class MikroTikConnectionError(MikroTikAPIError):
    """Raised when connection to router fails."""
    pass


class MikroTikAuthError(MikroTikAPIError):
    """Raised when authentication to router fails."""
    pass


class MikroTikCommandError(MikroTikAPIError):
    """Raised when a command execution fails."""
    pass


# =============================================================================
# ROUTEROS VARIABLE-LENGTH WORD ENCODING
# =============================================================================

class RouterOSEncoder:
    """
    Variable-length word encoding for RouterOS API protocol.

    Length Encoding:
        < 0x80          → 1 byte  (0xxxxxxx)
        < 0x4000        → 2 bytes (10xxxxxx xxxxxxxx)
        < 0x200000      → 3 bytes (110xxxxx xxxxxxxx xxxxxxxx)
        < 0x10000000    → 4 bytes (1110xxxx + 3 bytes)
        >= 0x10000000   → 5 bytes (11110xxx + 4 bytes)
    """

    @staticmethod
    def encode_length(length: int) -> bytes:
        if length < 0x80:
            return bytes([length])
        elif length < 0x4000:
            length |= 0x8000
            return bytes([(length >> 8) & 0xFF, length & 0xFF])
        elif length < 0x200000:
            length |= 0xC00000
            return bytes([(length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
        elif length < 0x10000000:
            length |= 0xE0000000
            return bytes([(length >> 24) & 0xFF, (length >> 16) & 0xFF,
                          (length >> 8) & 0xFF, length & 0xFF])
        else:
            return bytes([0xF0, (length >> 24) & 0xFF, (length >> 16) & 0xFF,
                          (length >> 8) & 0xFF, length & 0xFF])

    @staticmethod
    def encode_word(word: str) -> bytes:
        wb = word.encode('utf-8')
        return RouterOSEncoder.encode_length(len(wb)) + wb

    @staticmethod
    def read_length(sock: socket.socket) -> int:
        """Read variable-length integer. Returns -1 on EOF."""
        first = sock.recv(1)
        if not first:
            return -1
        b = first[0]
        if b & 0x80 == 0x00:
            return b
        elif b & 0xC0 == 0x80:
            rest = sock.recv(1)
            if not rest: return -1
            return ((b & 0x3F) << 8) | rest[0]
        elif b & 0xE0 == 0xC0:
            rest = sock.recv(2)
            if len(rest) < 2: return -1
            return ((b & 0x1F) << 16) | (rest[0] << 8) | rest[1]
        elif b & 0xF0 == 0xE0:
            rest = sock.recv(3)
            if len(rest) < 3: return -1
            return ((b & 0x0F) << 24) | (rest[0] << 16) | (rest[1] << 8) | rest[2]
        elif b & 0xF8 == 0xF0:
            rest = sock.recv(4)
            if len(rest) < 4: return -1
            return ((b & 0x07) << 32) | (rest[0] << 24) | (rest[1] << 16) | (rest[2] << 8) | rest[3]
        return -1


# =============================================================================
# LOW-LEVEL CONNECTION
# =============================================================================

class MikroTikConnection:
    """
    Low-level MikroTik RouterOS API connection.

    Uses sentence-based I/O: reads a complete RouterOS sentence
    (all words between !re and !done/!trap/!fatal) before returning.

    Features:
        - Modern login (checks !done for success)
        - Legacy challenge-response fallback (extracts =ret= as challenge)
        - Sentence-aware I/O (not word-stream based)
        - SO_KEEPALIVE for dead connection detection
        - Request tagging (.tag) for async operations
        - Thread-safe via RLock
    """

    DEFAULT_TIMEOUT = 30
    CONNECT_TIMEOUT = 10

    def __init__(
        self, host: str, username: str, password: str,
        port: int = 8728, use_ssl: bool = False, timeout: int = DEFAULT_TIMEOUT
    ):
        if not host: raise ValueError("Host required")
        if not username: raise ValueError("Username required")

        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.timeout = timeout

        self.socket: Optional[socket.socket] = None
        self._connected: bool = False
        self._lock = threading.RLock()
        self._last_used: datetime = datetime.now()
        self._tag_counter: int = 0

    @property
    def is_connected(self) -> bool:
        if not self._connected or self.socket is None:
            return False
        try:
            self.socket.getpeername()
            return True
        except (socket.error, OSError):
            self._connected = False
            return False

    @property
    def last_used(self) -> datetime:
        return self._last_used

    @property
    def idle_seconds(self) -> float:
        return (datetime.now() - self._last_used).total_seconds()

    def _next_tag(self) -> str:
        self._tag_counter += 1
        return str(self._tag_counter)

    # -------------------------------------------------------------------------
    # CONNECTION LIFECYCLE
    # -------------------------------------------------------------------------

    def connect(self) -> None:
        with self._lock:
            if self.is_connected:
                return
            self._disconnect_socket()
            try:
                raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw.settimeout(self.CONNECT_TIMEOUT)
                raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if self.use_ssl:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    self.socket = ctx.wrap_socket(raw, server_hostname=self.host)
                else:
                    self.socket = raw
                self.socket.connect((self.host, self.port))
                self.socket.settimeout(self.timeout)
                self._login()
                self._connected = True
                self._last_used = datetime.now()
                logger.info(f"Connected to {self.host}:{self.port}")
            except (socket.timeout, TimeoutError) as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Timeout: {self.host}:{self.port}") from e
            except ConnectionRefusedError as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Refused: {self.host}:{self.port}") from e
            except (socket.error, OSError) as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Failed: {e}") from e
            except MikroTikAuthError:
                self._disconnect_socket()
                raise
            except Exception as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Error: {e}") from e

    def disconnect(self) -> None:
        with self._lock:
            self._disconnect_socket()
            self._connected = False

    def _disconnect_socket(self) -> None:
        if self.socket is not None:
            try: self.socket.close()
            except Exception: pass
            finally: self.socket = None

    # -------------------------------------------------------------------------
    # AUTHENTICATION
    # -------------------------------------------------------------------------

    def _login(self) -> None:
        """Try modern login first. On !done without =ret=, try challenge."""
        if self.socket is None:
            raise MikroTikAuthError("No socket")

        # Modern login (RouterOS v6.43+ / v7.x)
        self._send_sentence('/login', f'=name={self.username}', f'=password={self.password}')
        replies = self._read_sentence()

        # Modern login success = !done (with or without =ret=)
        # If we got replies without !trap, we're logged in
        has_trap = any(r.get('_trap') for r in replies)
        has_fatal = any(r.get('_fatal') for r in replies)

        if not has_trap and not has_fatal:
            logger.debug(f"Modern login OK: {self.host}")
            return

        # If we got a trap with message "invalid user name or password",
        # the router supports modern login but credentials are wrong
        for r in replies:
            msg = r.get('message', '')
            if 'invalid' in msg.lower() or 'cannot' in msg.lower():
                raise MikroTikAuthError(f"Login rejected: {msg}")

        # Otherwise, fall back to challenge-response for older routers
        logger.debug(f"Modern login got trap, trying challenge: {self.host}")
        self._login_challenge()

    def _login_challenge(self) -> None:
        """Legacy challenge-response for pre-6.43 routers."""
        self._send_sentence('/login')
        replies = self._read_sentence()

        # Extract challenge from =ret= (legacy routers return =ret= as challenge)
        challenge = None
        for r in replies:
            if r.get('ret') and len(r.get('ret', '')) == 32:
                # Looks like an MD5 challenge
                challenge = r.get('ret')
                break

        if not challenge:
            raise MikroTikAuthError(f"No challenge from {self.host}")

        # Compute response
        pw = self.password.encode('utf-8')
        cb = binascii.unhexlify(challenge)
        md5 = hashlib.md5()
        md5.update(b'\x00')
        md5.update(pw)
        md5.update(cb)
        rh = md5.hexdigest().upper()

        self._send_sentence('/login', f'=name={self.username}', f'=response={rh}')
        replies2 = self._read_sentence()

        has_trap = any(r.get('_trap') for r in replies2)
        if has_trap:
            msg = next((r.get('message', '') for r in replies2 if r.get('message')), 'Unknown')
            raise MikroTikAuthError(f"Challenge login rejected: {msg}")

        logger.debug(f"Challenge login OK: {self.host}")

    # -------------------------------------------------------------------------
    # COMMAND EXECUTION
    # -------------------------------------------------------------------------

    def execute(self, command: str, **kwargs) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.is_connected:
                self.connect()

            # Build words with optional tag
            words = [command]
            tag = self._next_tag()
            words.append(f'.tag={tag}')

            for key, value in kwargs.items():
                if value is not None:
                    words.append(f'={key.replace("_", "-")}={value}')

            try:
                self._send_sentence(*words)
                replies = self._read_sentence()
                self._last_used = datetime.now()

                # Check for errors
                for reply in replies:
                    if reply.get('_trap'):
                        raise MikroTikCommandError(
                            reply.get('message', 'Router error')
                        )
                    if reply.get('_fatal'):
                        raise MikroTikCommandError(
                            f"Fatal: {reply.get('message', 'Unknown')}"
                        )

                # Strip internal flags before returning
                clean = []
                for r in replies:
                    clean.append({k: v for k, v in r.items()
                                  if not k.startswith('_')})
                return clean

            except (socket.timeout, socket.error, ConnectionError) as e:
                self._connected = False
                raise MikroTikConnectionError(f"Lost connection: {e}") from e
            except MikroTikAPIError:
                raise
            except Exception as e:
                raise MikroTikCommandError(f"Failed: {e}") from e

    # -------------------------------------------------------------------------
    # SENTENCE-BASED I/O
    # -------------------------------------------------------------------------

    def _send_sentence(self, *words: str) -> None:
        """Send a complete sentence (command + arguments)."""
        if self.socket is None:
            raise MikroTikConnectionError("No socket")
        for word in words:
            if not word: continue
            self.socket.sendall(RouterOSEncoder.encode_word(word))
        self.socket.sendall(RouterOSEncoder.encode_length(0))

    def _read_sentence(self) -> List[Dict[str, Any]]:
        """
        Read one complete RouterOS sentence.

        RouterOS sends sentences as:
            !re
            =key=value
            (zero-length word)
            !re
            =key=value
            (zero-length word)
            !done

        Each zero-length word marks the end of a reply record.
        The sentence ends at !done, !trap, or !fatal.

        Returns:
            List of reply dictionaries for this sentence
        """
        if self.socket is None:
            raise MikroTikConnectionError("No socket")

        replies: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}
        done = False

        while not done:
            length = RouterOSEncoder.read_length(self.socket)
            if length < 0:
                raise MikroTikConnectionError("Connection closed")

            if length == 0:
                # Zero-length word = end of current reply record
                if current:
                    replies.append(current)
                    current = {}
                continue

            word = self._read_exact(length).decode('utf-8', errors='ignore')

            if word == '!done':
                done = True
            elif word == '!trap':
                current['_trap'] = True
            elif word == '!fatal':
                current['_fatal'] = True
                done = True
            elif word == '!re':
                if current:
                    replies.append(current)
                current = {}
            elif word.startswith('='):
                inner = word[1:]
                parts = inner.split('=', 1)
                if len(parts) == 2:
                    current[parts[0]] = parts[1]
                else:
                    current[parts[0]] = ''

        if current:
            replies.append(current)

        return replies

    def _read_exact(self, size: int) -> bytes:
        if self.socket is None:
            raise MikroTikConnectionError("No socket")
        data = b''
        while len(data) < size:
            try:
                chunk = self.socket.recv(size - len(data))
                if not chunk:
                    raise MikroTikConnectionError(f"Closed (got {len(data)} of {size})")
                data += chunk
            except socket.timeout:
                raise MikroTikConnectionError(f"Timeout from {self.host}")
            except socket.error as e:
                raise MikroTikConnectionError(f"Socket error: {e}")
        return data

    def ping(self) -> bool:
        try:
            self.execute('/system/resource/print')
            return True
        except MikroTikAPIError:
            return False


# =============================================================================
# HIGH-LEVEL CLIENT
# =============================================================================

class MikroTikClient:
    """
    High-level MikroTik API client for multi-tenant ISP management.

    All set/remove/disable operations resolve .id from names
    since the RouterOS API requires internal .id, not names.
    """

    DEFAULT_CONNECTION_TIMEOUT = 300
    MAX_TOTAL_CONNECTIONS = 100

    def __init__(self):
        self._connections: Dict[str, MikroTikConnection] = {}
        self._lock = threading.RLock()
        self.encryption = EncryptionService()
        self.connection_timeout = self.DEFAULT_CONNECTION_TIMEOUT
        self._password_cache: Dict[str, str] = {}

    def _get_connection_key(self, router_id, host, port):
        return f"{router_id or 'unknown'}:{host}:{port}"

    def _get_password(self, encrypted: str) -> str:
        """Thread-safe password decryption with caching."""
        with self._lock:
            if encrypted not in self._password_cache:
                self._password_cache[encrypted] = self.encryption.decrypt(encrypted)
            return self._password_cache[encrypted]

    def _resolve_id(self, router_data, path, name_attr, name_val):
        try:
            items = self.execute(router_data, f'{path}/print')
            for item in items:
                if item.get(name_attr) == name_val:
                    return item.get('.id')
        except Exception:
            pass
        return None

    def _resolve_all_ids(self, router_data, path):
        try:
            items = self.execute(router_data, f'{path}/print')
            return [i.get('.id') for i in items if i.get('.id')]
        except Exception:
            return []

    def get_connection(self, router_data: Dict[str, Any]) -> MikroTikConnection:
        router_id = router_data.get('id')
        host = router_data.get('ip_address')
        port = router_data.get('api_port', 8728)
        use_ssl = router_data.get('api_ssl', False)
        username = router_data.get('username')

        if not host: raise ValueError("ip_address required")
        if not username: raise ValueError("username required")

        encrypted = router_data.get('password_encrypted', '')
        if not encrypted: raise MikroTikAuthError(f"No password for {host}")

        key = self._get_connection_key(router_id, host, port)

        with self._lock:
            if key in self._connections:
                conn = self._connections[key]
                if conn.is_connected:
                    try:
                        conn.socket.getpeername()
                    except Exception:
                        conn.disconnect()
                        del self._connections[key]
                    else:
                        conn._last_used = datetime.now()
                        return conn
                else:
                    conn.disconnect()
                    del self._connections[key]

            if len(self._connections) >= self.MAX_TOTAL_CONNECTIONS:
                self._cleanup_oldest_connections()

            timeout = 30
            if has_app_context() and current_app:
                timeout = current_app.config.get('MIKROTIK_API_TIMEOUT', 30)

            password = self._get_password(encrypted)
            conn = MikroTikConnection(host=host, username=username, password=password,
                                       port=port, use_ssl=use_ssl, timeout=timeout)
            conn.connect()
            self._connections[key] = conn
            return conn

    def _cleanup_connections(self):
        with self._lock:
            stale = [k for k, c in self._connections.items()
                     if not c.is_connected or c.idle_seconds > self.connection_timeout]
            for k in stale:
                try: self._connections[k].disconnect()
                except Exception: pass
                del self._connections[k]

    def _cleanup_oldest_connections(self):
        with self._lock:
            sorted_conns = sorted(self._connections.items(), key=lambda x: x[1].last_used)
            for key, conn in sorted_conns[:10]:
                try: conn.disconnect()
                except Exception: pass
                del self._connections[key]

    def invalidate_connection(self, router_data):
        key = self._get_connection_key(router_data.get('id'), router_data.get('ip_address'),
                                        router_data.get('api_port', 8728))
        with self._lock:
            if key in self._connections:
                try: self._connections[key].disconnect()
                except Exception: pass
                del self._connections[key]

    def execute(self, router_data, command, retries=3, **kwargs):
        last_error = None
        backoff = 1
        for attempt in range(retries):
            try:
                conn = self.get_connection(router_data)
                return conn.execute(command, **kwargs)
            except (MikroTikConnectionError, socket.timeout, ConnectionError) as e:
                last_error = e
                self.invalidate_connection(router_data)
                if attempt < retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    raise MikroTikAPIError(f"Failed after {retries}: {last_error}") from last_error
            except MikroTikAPIError:
                raise
        raise MikroTikAPIError(f"Failed: {last_error}")

    # -------------------------------------------------------------------------
    # CONNECTION TEST
    # -------------------------------------------------------------------------

    def test_connection(self, host, username, password, port=8728, use_ssl=False):
        conn = None
        try:
            conn = MikroTikConnection(host=host, username=username, password=password,
                                       port=port, use_ssl=use_ssl, timeout=10)
            conn.connect()
            result = conn.execute('/system/resource/print')
            if result:
                r = result[0]
                return {'success': True, 'connected': True, 'router_info': {
                    'version': r.get('version', '?'), 'board_name': r.get('board-name', '?'),
                    'cpu_load': r.get('cpu-load', '?'), 'uptime': r.get('uptime', '?'),
                }}
            return {'success': False, 'connected': False, 'error': 'No response'}
        except MikroTikConnectionError as e:
            return {'success': False, 'connected': False, 'error': str(e)}
        except MikroTikAuthError:
            return {'success': False, 'connected': False, 'error': 'Auth failed'}
        except Exception as e:
            return {'success': False, 'connected': False, 'error': str(e)}
        finally:
            if conn:
                try: conn.disconnect()
                except Exception: pass

    # -------------------------------------------------------------------------
    # HEALTH
    # -------------------------------------------------------------------------

    def get_router_info(self, router_data):
        try:
            r = self.execute(router_data, '/system/resource/print')
            i = self.execute(router_data, '/system/identity/print')
            r0, i0 = r[0] if r else {}, i[0] if i else {}
            return {
                'hostname': i0.get('name'), 'version': r0.get('version'),
                'uptime': r0.get('uptime'), 'cpu_load': r0.get('cpu-load'),
                'free_memory': r0.get('free-memory'), 'total_memory': r0.get('total-memory'),
                'board_name': r0.get('board-name'), 'architecture_name': r0.get('architecture-name'),
            }
        except Exception:
            return {}

    def health_check(self, router_data):
        try:
            start = time.time()
            result = self.execute(router_data, '/system/resource/print', retries=2)
            rt = (time.time() - start) * 1000
            if result:
                r = result[0]
                return {'status': 'healthy', 'response_time_ms': round(rt, 2),
                        'cpu_load': r.get('cpu-load'), 'uptime': r.get('uptime'),
                        'free_memory': r.get('free-memory'), 'total_memory': r.get('total-memory')}
            return {'status': 'unhealthy', 'error': 'No response'}
        except Exception as e:
            return {'status': 'unhealthy', 'error': str(e)}

    # -------------------------------------------------------------------------
    # HOTSPOT
    # -------------------------------------------------------------------------

    def get_hotspot_servers(self, router_data):
        try: return self.execute(router_data, '/ip/hotspot/print')
        except Exception: return []

    def get_hotspot_users(self, router_data, server_id=None):
        params = {'server': server_id} if server_id else {}
        try: return self.execute(router_data, '/ip/hotspot/user/print', **params)
        except Exception: return []

    def get_active_sessions(self, router_data, server_id=None):
        params = {'server': server_id} if server_id else {}
        try: return self.execute(router_data, '/ip/hotspot/active/print', **params)
        except Exception: return []

    def disconnect_hotspot_user(self, router_data, username):
        sessions = self.get_active_sessions(router_data)
        for s in sessions:
            if s.get('user') == username:
                try:
                    self.execute(router_data, '/ip/hotspot/active/remove', numbers=s.get('.id'))
                    return {'success': True}
                except Exception as e:
                    return {'success': False, 'error': str(e)}
        return {'success': False, 'error': 'Not found'}

    def get_hotspot_profiles(self, router_data):
        try: return self.execute(router_data, '/ip/hotspot/user/profile/print')
        except Exception: return []

    # -------------------------------------------------------------------------
    # PPPoE
    # -------------------------------------------------------------------------

    def get_pppoe_servers(self, router_data):
        try: return self.execute(router_data, '/interface/pppoe-server/server/print')
        except Exception: return []

    def get_pppoe_secrets(self, router_data):
        try: return self.execute(router_data, '/ppp/secret/print')
        except Exception: return []

    def get_pppoe_active_sessions(self, router_data):
        try: return self.execute(router_data, '/ppp/active/print')
        except Exception: return []

    def disconnect_pppoe_user(self, router_data, username):
        sessions = self.get_pppoe_active_sessions(router_data)
        for s in sessions:
            if s.get('name') == username:
                try:
                    self.execute(router_data, '/ppp/active/remove', numbers=s.get('.id'))
                    return {'success': True}
                except Exception as e:
                    return {'success': False, 'error': str(e)}
        return {'success': False, 'error': 'Not found'}

    # -------------------------------------------------------------------------
    # RADIUS
    # -------------------------------------------------------------------------

    def configure_radius(self, router_data, radius_server, radius_secret):
        try:
            existing = self.execute(router_data, '/radius/print')
            found = False
            for item in existing:
                if item.get('address') == radius_server:
                    self.execute(router_data, '/radius/set', numbers=item.get('.id'),
                        secret=radius_secret, service='hotspot,ppp',
                        authentication_port='1812', accounting_port='1813')
                    found = True
                    break
            if not found:
                self.execute(router_data, '/radius/add', address=radius_server,
                    secret=radius_secret, service='hotspot,ppp',
                    authentication_port='1812', accounting_port='1813')

            for pid in self._resolve_all_ids(router_data, '/ip/hotspot/user/profile'):
                try:
                    self.execute(router_data, '/ip/hotspot/user/profile/set', numbers=pid, **{'use-radius': 'yes'})
                except Exception: pass

            try: self.execute(router_data, '/ppp/aaa/set', **{'use-radius': 'yes'})
            except Exception: pass
            try: self.execute(router_data, '/radius/incoming/set', accept='yes')
            except Exception: pass

            return {'success': True, 'message': 'RADIUS configured'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # WALLED GARDEN
    # -------------------------------------------------------------------------

    def configure_walled_garden(self, router_data, platform_domain=None, additional_domains=None):
        platform = platform_domain or 'isp.bhatek.space'
        results = {'success': True, 'dns_added': False, 'domains_added': 0, 'errors': []}
        try:
            ex = self.execute(router_data, '/ip/hotspot/walled-garden/ip/print')
            if not any(e.get('dst-port') == '53' and e.get('protocol') == 'udp' for e in ex):
                self.execute(router_data, '/ip/hotspot/walled-garden/ip/add',
                             dst_port='53', protocol='udp', action='accept', comment='DNS')
            results['dns_added'] = True
        except Exception as e:
            results['errors'].append(str(e))
        domains = [{'host': platform, 'comment': 'ISP Portal'},
                   {'host': '*.safaricom.co.ke', 'comment': 'M-Pesa'},
                   {'host': '*.googleapis.com', 'comment': 'Fonts'},
                   {'host': '*.gstatic.com', 'comment': 'CDN'}]
        if additional_domains:
            for d in additional_domains:
                domains.append({'host': d, 'comment': 'Custom'})
        for d in domains:
            try:
                ex = self.execute(router_data, '/ip/hotspot/walled-garden/ip/print')
                if not any(e.get('dst-host') == d['host'] for e in ex):
                    self.execute(router_data, '/ip/hotspot/walled-garden/ip/add',
                                 **{'dst-host': d['host']}, action='accept', comment=d['comment'])
                    results['domains_added'] += 1
            except Exception as e:
                results['errors'].append(str(e))
        return results

    # -------------------------------------------------------------------------
    # BANDWIDTH
    # -------------------------------------------------------------------------

    def set_bandwidth_limit(self, router_data, target, upload_mbps, download_mbps, queue_type='default'):
        try:
            rate = f"{upload_mbps}M/{download_mbps}M"
            name = f"limit_{target.replace('/', '_').replace(':', '_')}"
            self.execute(router_data, '/queue/simple/add', name=name, target=target,
                         max_limit=rate, queue=queue_type)
            return {'success': True, 'name': name, 'rate_limit': rate}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_simple_queues(self, router_data):
        try: return self.execute(router_data, '/queue/simple/print')
        except Exception: return []

    def remove_simple_queue(self, router_data, queue_id):
        try:
            self.execute(router_data, '/queue/simple/remove', numbers=queue_id)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # LIFECYCLE
    # -------------------------------------------------------------------------

    def close_all(self):
        with self._lock:
            for conn in list(self._connections.values()):
                try: conn.disconnect()
                except Exception: pass
            self._connections.clear()

    def __del__(self):
        try: self.close_all()
        except Exception: pass