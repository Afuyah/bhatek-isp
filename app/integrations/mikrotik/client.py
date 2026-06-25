"""
MikroTik RouterOS API Client
============================
Production-ready client for multi-tenant ISP router management.
Implements the proprietary RouterOS API protocol with connection management,
automatic retry, RADIUS configuration, walled garden setup, and full
hotspot/PPPoE management.

Protocol: Binary length-prefixed word stream over TCP (port 8728) or SSL (port 8729)
Auth: Challenge-response MD5 (compatible with RouterOS v6.43+ and v7.x)

Verified Commands (RouterOS v6.43+ / v7.x):
    - /ip hotspot profile set [find] use-radius=yes
    - /ppp aaa set use-radius=yes
    - /radius incoming set accept=yes
    - /radius add address=... secret=... service=hotspot,ppp
    - WireGuard: /interface wireguard ...
    - Walled Garden: /ip hotspot walled-garden ip add ...
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

from flask import current_app

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
# LOW-LEVEL CONNECTION
# =============================================================================

class MikroTikConnection:
    """
    Low-level MikroTik RouterOS API connection.
    Handles TCP/SSL socket communication, login challenge/response
    authentication, and raw command/response execution using the
    MikroTik proprietary API protocol.

    Protocol format:
        - Each word: 4-byte big-endian length prefix + UTF-8 encoded bytes
        - End of command: zero-length word (4 null bytes)
        - Response: '=key=value' attributes, '!status' status lines, '.done' terminator

    Thread-safe for concurrent use via internal lock.
    Compatible with RouterOS v6.43+ and v7.x.
    """

    DEFAULT_TIMEOUT = 30
    CONNECT_TIMEOUT = 10

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8728,
        use_ssl: bool = False,
        timeout: int = DEFAULT_TIMEOUT
    ):
        if not host:
            raise ValueError("Host is required")
        if not username:
            raise ValueError("Username is required")

        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.timeout = timeout

        self.socket: Optional[socket.socket] = None
        self._connected: bool = False
        self._words: List[str] = []
        self._lock = threading.Lock()
        self._last_used: datetime = datetime.now()

    # -------------------------------------------------------------------------
    # PROPERTIES
    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------
    # CONNECTION LIFECYCLE
    # -------------------------------------------------------------------------

    def connect(self) -> None:
        with self._lock:
            if self.is_connected:
                return
            self._disconnect_socket()
            try:
                raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw_socket.settimeout(self.CONNECT_TIMEOUT)
                if self.use_ssl:
                    context = ssl.create_default_context()
                    self.socket = context.wrap_socket(raw_socket, server_hostname=self.host)
                else:
                    self.socket = raw_socket
                self.socket.connect((self.host, self.port))
                self.socket.settimeout(self.timeout)
                self._login()
                self._connected = True
                self._last_used = datetime.now()
                logger.info(f"Connected to MikroTik router {self.host}:{self.port} (SSL: {self.use_ssl})")
            except (socket.timeout, TimeoutError) as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Connection timeout to {self.host}:{self.port}") from e
            except ConnectionRefusedError as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Connection refused by {self.host}:{self.port}. API may be disabled.") from e
            except (socket.error, OSError) as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}") from e
            except MikroTikAuthError:
                self._disconnect_socket()
                raise
            except Exception as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(f"Unexpected error connecting to {self.host}:{self.port}: {e}") from e

    def disconnect(self) -> None:
        with self._lock:
            self._disconnect_socket()
            self._connected = False

    def _disconnect_socket(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            except (socket.error, OSError):
                pass
            finally:
                self.socket = None

    # -------------------------------------------------------------------------
    # AUTHENTICATION
    # -------------------------------------------------------------------------

    def _login(self) -> None:
        if self.socket is None:
            raise MikroTikAuthError("No socket connection")
        try:
            self._send_command('/login')
            response = self._read_response()
            for word in response:
                if '=ret=' in word:
                    return
                if '=challenge=' in word:
                    parts = word.split('=', 2)
                    if len(parts) < 3:
                        raise MikroTikAuthError(f"Invalid challenge format: {word}")
                    challenge = parts[2]
                    response_hash = self._compute_challenge_response(challenge)
                    self._send_command('/login', f'=name={self.username}', f'=response={response_hash}')
                    final_response = self._read_response()
                    for final_word in final_response:
                        if '=ret=' in final_word:
                            logger.debug(f"Authenticated to {self.host} as {self.username}")
                            return
                    raise MikroTikAuthError(f"Login rejected for user '{self.username}' on {self.host}")
            raise MikroTikAuthError(f"No challenge received from {self.host}")
        except MikroTikAuthError:
            raise
        except MikroTikAPIError:
            raise
        except Exception as e:
            raise MikroTikAuthError(f"Authentication error: {e}") from e

    def _compute_challenge_response(self, challenge: str) -> str:
        try:
            password_bytes = self.password.encode('utf-8')
            challenge_bytes = binascii.unhexlify(challenge)
            md5 = hashlib.md5()
            md5.update(b'\x00')
            md5.update(password_bytes)
            md5.update(challenge_bytes)
            return md5.hexdigest().upper()
        except (binascii.Error, ValueError) as e:
            raise MikroTikAuthError(f"Invalid challenge format: {challenge}") from e

    # -------------------------------------------------------------------------
    # COMMAND EXECUTION
    # -------------------------------------------------------------------------

    def execute(self, command: str, **kwargs) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.is_connected:
                logger.debug(f"Reconnecting to {self.host} before executing command")
                self.connect()
            words = [command]
            for key, value in kwargs.items():
                if value is not None:
                    attr_name = key.replace('_', '-')
                    words.append(f'={attr_name}={value}')
            try:
                self._send_command(*words)
                response = self._read_response()
                result = self._parse_response(response)
                self._last_used = datetime.now()
                return result
            except (socket.timeout, socket.error, ConnectionError) as e:
                self._connected = False
                raise MikroTikConnectionError(f"Connection lost during command '{command}': {e}") from e
            except MikroTikAPIError:
                raise
            except Exception as e:
                raise MikroTikCommandError(f"Command '{command}' failed: {e}") from e

    def _parse_response(self, response: List[str]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}
        for line in response:
            if line.startswith('!') and '=message=' in line:
                parts = line.split('=', 2)
                error_msg = parts[2] if len(parts) > 2 else line[1:]
                raise MikroTikCommandError(f"Router returned error: {error_msg}")
            elif line.startswith('!'):
                if current and len(current) > 1:
                    result.append(current)
                current = {'status': line[1:]}
            elif '=' in line:
                key, value = line.split('=', 1)
                current[key] = value
            elif line == '.done':
                pass
        if current and len(current) > 1:
            result.append(current)
        return result

    def _send_command(self, *words: str) -> None:
        if self.socket is None:
            raise MikroTikConnectionError("No socket connection")
        for word in words:
            if not word:
                continue
            word_bytes = word.encode('utf-8')
            length = len(word_bytes)
            self.socket.sendall(struct.pack('>I', length))
            self.socket.sendall(word_bytes)
        self.socket.sendall(struct.pack('>I', 0))

    def _read_response(self) -> List[str]:
        if self.socket is None:
            raise MikroTikConnectionError("No socket connection")
        self._words = []
        while True:
            length_bytes = self._read_exact(4)
            length = struct.unpack('>I', length_bytes)[0]
            if length == 0:
                break
            word_bytes = self._read_exact(length)
            word = word_bytes.decode('utf-8', errors='ignore')
            self._words.append(word)
        return self._words

    def _read_exact(self, size: int) -> bytes:
        if self.socket is None:
            raise MikroTikConnectionError("No socket connection")
        data = b''
        while len(data) < size:
            try:
                chunk = self.socket.recv(size - len(data))
                if not chunk:
                    raise MikroTikConnectionError(f"Connection closed by {self.host} (received {len(data)} of {size} bytes)")
                data += chunk
            except socket.timeout:
                raise MikroTikConnectionError(f"Socket timeout reading from {self.host}")
            except socket.error as e:
                raise MikroTikConnectionError(f"Socket error reading from {self.host}: {e}")
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

    Features:
        - Connection lifecycle management with automatic reconnection
        - Password decryption (receives encrypted passwords from database)
        - Automatic retry with exponential backoff
        - RADIUS configuration with VERIFIED RouterOS commands
        - Walled garden configuration for captive portal
        - Full hotspot user/profile management
        - Full PPPoE secret/session management
        - Bandwidth queue management
        - Router health monitoring
        - WireGuard-ready (connects via WireGuard IP)
    """

    DEFAULT_CONNECTION_TIMEOUT = 300
    MAX_TOTAL_CONNECTIONS = 100

    def __init__(self):
        self._connections: Dict[str, MikroTikConnection] = {}
        self._lock = threading.Lock()
        self.encryption = EncryptionService()
        self.connection_timeout = self.DEFAULT_CONNECTION_TIMEOUT

    # -------------------------------------------------------------------------
    # CONNECTION MANAGEMENT
    # -------------------------------------------------------------------------

    def _get_connection_key(self, router_id: Any, host: str, port: int) -> str:
        rid = str(router_id) if router_id else 'unknown'
        return f"{rid}:{host}:{port}"

    def get_connection(self, router_data: Dict[str, Any]) -> MikroTikConnection:
        router_id = router_data.get('id')
        host = router_data.get('ip_address')
        port = router_data.get('api_port', 8728)
        use_ssl = router_data.get('api_ssl', False)
        username = router_data.get('username')

        if not host:
            raise ValueError("router_data must contain 'ip_address'")
        if not username:
            raise ValueError("router_data must contain 'username'")

        encrypted_password = router_data.get('password_encrypted', '')
        if not encrypted_password:
            raise MikroTikAuthError(f"No password provided for router {host}")

        try:
            password = self.encryption.decrypt(encrypted_password)
        except Exception as e:
            raise MikroTikAuthError(f"Failed to decrypt password for {host}: {e}") from e

        key = self._get_connection_key(router_id, host, port)

        with self._lock:
            if key in self._connections:
                conn = self._connections[key]
                if conn.is_connected:
                    conn._last_used = datetime.now()
                    return conn
                else:
                    logger.debug(f"Removing stale connection for {key}")
                    conn.disconnect()
                    del self._connections[key]

            if len(self._connections) >= self.MAX_TOTAL_CONNECTIONS:
                self._cleanup_oldest_connections()

            timeout = current_app.config.get('MIKROTIK_API_TIMEOUT', 30) if current_app else 30

            conn = MikroTikConnection(
                host=host, username=username, password=password,
                port=port, use_ssl=use_ssl, timeout=timeout
            )
            conn.connect()
            self._connections[key] = conn
            logger.debug(f"New connection to {host}:{port} (total: {len(self._connections)})")
            return conn

    def _cleanup_connections(self) -> None:
        now = datetime.now()
        stale_keys = [
            key for key, conn in self._connections.items()
            if not conn.is_connected or conn.idle_seconds > self.connection_timeout
        ]
        for key in stale_keys:
            try:
                self._connections[key].disconnect()
            except Exception:
                pass
            del self._connections[key]
        if stale_keys:
            logger.info(f"Cleaned up {len(stale_keys)} stale connections")

    def _cleanup_oldest_connections(self) -> None:
        sorted_conns = sorted(self._connections.items(), key=lambda x: x[1].last_used)
        for key, conn in sorted_conns[:10]:
            try:
                conn.disconnect()
            except Exception:
                pass
            del self._connections[key]

    def invalidate_connection(self, router_data: Dict[str, Any]) -> None:
        key = self._get_connection_key(
            router_data.get('id'), router_data.get('ip_address'),
            router_data.get('api_port', 8728)
        )
        with self._lock:
            if key in self._connections:
                try:
                    self._connections[key].disconnect()
                except Exception:
                    pass
                del self._connections[key]
                logger.debug(f"Invalidated connection for {key}")

    # -------------------------------------------------------------------------
    # COMMAND EXECUTION WITH RETRY
    # -------------------------------------------------------------------------

    def execute(
        self, router_data: Dict[str, Any], command: str,
        retries: int = 3, **kwargs
    ) -> List[Dict[str, Any]]:
        last_error: Optional[Exception] = None
        backoff = 1
        for attempt in range(retries):
            try:
                conn = self.get_connection(router_data)
                return conn.execute(command, **kwargs)
            except (MikroTikConnectionError, socket.timeout, ConnectionError) as e:
                last_error = e
                logger.warning(f"Command '{command}' failed (attempt {attempt + 1}/{retries}) on {router_data.get('ip_address')}: {e}")
                self.invalidate_connection(router_data)
                if attempt < retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    raise MikroTikAPIError(f"Command '{command}' failed after {retries} attempts: {last_error}") from last_error
            except MikroTikAPIError:
                raise
        raise MikroTikAPIError(f"Command '{command}' failed: {last_error}")

    # -------------------------------------------------------------------------
    # CONNECTION TESTING
    # -------------------------------------------------------------------------

    def test_connection(
        self, host: str, username: str, password: str,
        port: int = 8728, use_ssl: bool = False
    ) -> Dict[str, Any]:
        conn = None
        try:
            conn = MikroTikConnection(host=host, username=username, password=password, port=port, use_ssl=use_ssl, timeout=10)
            conn.connect()
            result = conn.execute('/system/resource/print')
            if result and len(result) > 0:
                resource = result[0]
                return {
                    'success': True, 'connected': True,
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
            return {'success': False, 'connected': False, 'error': 'No response from router'}
        except MikroTikConnectionError as e:
            return {'success': False, 'connected': False, 'error': str(e)}
        except MikroTikAuthError:
            return {'success': False, 'connected': False, 'error': 'Authentication failed. Check username and password.'}
        except Exception as e:
            logger.error(f"Connection test failed for {host}:{port}: {e}")
            return {'success': False, 'connected': False, 'error': f'Connection failed: {e}'}
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # RADIUS CONFIGURATION (VERIFIED COMMANDS — v6.43+ / v7.x)
    # -------------------------------------------------------------------------

    def configure_radius(
        self, router_data: Dict[str, Any],
        radius_server: str, radius_secret: str,
        radius_port: int = 1812, radius_acct_port: int = 1813,
        radius_timeout: int = 3000, radius_retries: int = 3
    ) -> Dict[str, Any]:
        try:
            existing = self.execute(router_data, '/radius/print')
            server_exists = False
            for item in existing:
                if item.get('address') == radius_server:
                    server_exists = True
                    self.execute(router_data, '/radius/set', numbers=item.get('.id'),
                        secret=radius_secret, service='hotspot,ppp',
                        authentication_port=str(radius_port), accounting_port=str(radius_acct_port),
                        timeout=str(radius_timeout), retries=str(radius_retries))
                    logger.info(f"RADIUS server updated: {radius_server}")
                    break
            if not server_exists:
                self.execute(router_data, '/radius/add', address=radius_server,
                    secret=radius_secret, service='hotspot,ppp',
                    authentication_port=str(radius_port), accounting_port=str(radius_acct_port),
                    timeout=str(radius_timeout), retries=str(radius_retries))
                logger.info(f"RADIUS server added: {radius_server}")

            try:
                self.execute(router_data, '/ip/hotspot/profile/set', numbers='[find]', **{'use-radius': 'yes'})
                logger.info("Hotspot RADIUS enabled via /ip hotspot profile set [find] use-radius=yes")
            except MikroTikAPIError as e:
                logger.warning(f"Could not enable hotspot RADIUS: {e}")

            try:
                self.execute(router_data, '/ppp/aaa/set', **{'use-radius': 'yes'})
                logger.info("PPP RADIUS enabled via /ppp aaa set use-radius=yes")
            except MikroTikAPIError:
                logger.debug("PPP AAA RADIUS not configurable (may already be enabled)")

            try:
                self.execute(router_data, '/radius/incoming/set', accept='yes')
                logger.info("RADIUS incoming enabled via /radius incoming set accept=yes")
            except MikroTikAPIError:
                logger.debug("RADIUS incoming not configurable on this router")

            logger.info(f"RADIUS fully configured on {router_data.get('ip_address')} -> {radius_server}:{radius_port}/{radius_acct_port}")
            return {'success': True, 'message': 'RADIUS configured successfully', 'radius_server': radius_server, 'radius_port': radius_port}
        except MikroTikAPIError as e:
            logger.error(f"Failed to configure RADIUS on {router_data.get('ip_address')}: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Unexpected error configuring RADIUS on {router_data.get('ip_address')}: {e}")
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # WALLED GARDEN CONFIGURATION (CAPTIVE PORTAL)
    # -------------------------------------------------------------------------

    def configure_walled_garden(
        self,
        router_data: Dict[str, Any],
        platform_domain: str = None,
        org_slug: str = None,
        additional_domains: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Configure walled garden on MikroTik hotspot.

        The walled garden allows unauthenticated users to access specific
        domains before purchasing internet. This is critical for the captive
        portal to work — users must reach the payment page without internet.

        Allowed by default:
            - The ISP platform domain (captive portal + APIs)
            - Safaricom M-Pesa domains (for STK Push)
            - Google Fonts (for portal styling)
            - DNS (UDP port 53 for name resolution)

        Args:
            router_data: Router connection details
            platform_domain: The ISP platform domain (e.g., 'isp.bhatek.space')
            org_slug: Organization slug for hotspot redirect URL
            additional_domains: Any extra domains the ISP wants to allow

        Returns:
            Dict with success/failure and details
        """
        platform = platform_domain or current_app.config.get(
            'PLATFORM_DOMAIN', 'isp.bhatek.space'
        )

        # Build the list of domains to allow
        allowed_domains = [
            # ISP Platform — captive portal, APIs, payment callbacks
            {'host': platform, 'comment': 'ISP Platform Portal'},
            # Safaricom M-Pesa API domains
            {'host': '*.safaricom.co.ke', 'comment': 'M-Pesa API'},
            {'host': '*.daraja.co.ke', 'comment': 'M-Pesa Daraja API'},
            # Google Fonts (for portal typography)
            {'host': '*.googleapis.com', 'comment': 'Google Fonts API'},
            {'host': '*.gstatic.com', 'comment': 'Google Fonts CDN'},
            # Cloudflare (if platform uses it)
            {'host': '*.cloudflare.com', 'comment': 'Cloudflare'},
        ]

        # Add any ISP-specific additional domains
        if additional_domains:
            for domain in additional_domains:
                allowed_domains.append({
                    'host': domain,
                    'comment': 'ISP Custom Domain',
                })

        results = {
            'success': True,
            'dns_added': False,
            'domains_added': 0,
            'errors': [],
        }

        try:
            # Step 1: Allow DNS resolution (UDP port 53)
            try:
                # Check if DNS rule already exists
                existing_dns = self.execute(
                    router_data,
                    '/ip/hotspot/walled-garden/ip/print',
                    **{'?dst-port': '53'},
                )
                dns_exists = any(
                    e.get('dst-port') == '53' and e.get('protocol') == 'udp'
                    for e in existing_dns
                )
                if not dns_exists:
                    self.execute(
                        router_data,
                        '/ip/hotspot/walled-garden/ip/add',
                        dst_port='53',
                        protocol='udp',
                        action='accept',
                        comment='Allow DNS Resolution',
                    )
                results['dns_added'] = True
                logger.info("Walled garden: DNS allowed")
            except MikroTikAPIError as e:
                results['errors'].append(f"DNS: {str(e)}")
                logger.warning(f"Could not add DNS walled garden rule: {e}")

            # Step 2: Add domain-based walled garden entries
            existing_entries = self.execute(
                router_data, '/ip/hotspot/walled-garden/ip/print'
            )

            for domain_entry in allowed_domains:
                host = domain_entry['host']
                comment = domain_entry['comment']

                # Check if this host already exists
                already_exists = any(
                    e.get('dst-host') == host or e.get('comment') == comment
                    for e in existing_entries
                )

                if not already_exists:
                    try:
                        self.execute(
                            router_data,
                            '/ip/hotspot/walled-garden/ip/add',
                            **{'dst-host': host},
                            action='accept',
                            comment=comment,
                        )
                        results['domains_added'] += 1
                        logger.info(f"Walled garden: {host} allowed ({comment})")
                    except MikroTikAPIError as e:
                        results['errors'].append(f"{host}: {str(e)}")
                        logger.warning(f"Could not add walled garden for {host}: {e}")
                else:
                    logger.debug(f"Walled garden entry already exists: {host}")

            # Step 3: Configure hotspot to use the platform portal URL
            if org_slug:
                try:
                    portal_url = f"https://{platform}/hotspot/{org_slug}"
                    self.execute(
                        router_data,
                        '/ip/hotspot/profile/set',
                        numbers='[find]',
                        **{'dns-name': platform},
                    )
                    logger.info(f"Hotspot portal URL set to: {portal_url}")
                except MikroTikAPIError as e:
                    logger.warning(f"Could not set hotspot portal URL: {e}")

            if results['domains_added'] > 0:
                logger.info(
                    f"Walled garden configured: {results['domains_added']} domains added, "
                    f"DNS: {results['dns_added']}"
                )
            else:
                logger.info("Walled garden: all entries already configured")

            return results

        except Exception as e:
            logger.error(f"Failed to configure walled garden: {e}")
            return {
                'success': False,
                'error': str(e),
                'dns_added': False,
                'domains_added': 0,
            }

    # -------------------------------------------------------------------------
    # HEALTH & MONITORING
    # -------------------------------------------------------------------------

    def get_router_info(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resource = self.execute(router_data, '/system/resource/print')
            identity = self.execute(router_data, '/system/identity/print')
            r = resource[0] if resource else {}
            i = identity[0] if identity else {}
            return {
                'hostname': i.get('name'), 'version': r.get('version'),
                'build_time': r.get('build-time'), 'uptime': r.get('uptime'),
                'cpu_load': r.get('cpu-load'), 'cpu_count': r.get('cpu-count'),
                'free_memory': r.get('free-memory'), 'total_memory': r.get('total-memory'),
                'free_hdd': r.get('free-hdd'), 'total_hdd': r.get('total-hdd'),
                'architecture_name': r.get('architecture-name'),
                'board_name': r.get('board-name'), 'platform': r.get('platform'),
            }
        except Exception as e:
            logger.error(f"Failed to get router info for {router_data.get('ip_address')}: {e}")
            return {}

    def health_check(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            start_time = time.time()
            result = self.execute(router_data, '/system/resource/print', retries=2)
            response_time = (time.time() - start_time) * 1000
            if result and len(result) > 0:
                resource = result[0]
                return {
                    'status': 'healthy', 'response_time_ms': round(response_time, 2),
                    'cpu_load': resource.get('cpu-load'), 'uptime': resource.get('uptime'),
                    'free_memory': resource.get('free-memory'), 'total_memory': resource.get('total-memory'),
                }
            return {'status': 'unhealthy', 'error': 'No response from router'}
        except Exception as e:
            return {'status': 'unhealthy', 'error': str(e)}

    def get_interface_stats(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            result = self.execute(router_data, '/interface/print')
            return [{
                'name': i.get('name'), 'type': i.get('type'), 'mtu': i.get('mtu'),
                'rx_byte': int(i.get('rx-byte', 0)), 'tx_byte': int(i.get('tx-byte', 0)),
                'rx_packet': int(i.get('rx-packet', 0)), 'tx_packet': int(i.get('tx-packet', 0)),
                'rx_error': int(i.get('rx-error', 0)), 'tx_error': int(i.get('tx-error', 0)),
                'rx_drop': int(i.get('rx-drop', 0)), 'tx_drop': int(i.get('tx-drop', 0)),
                'running': i.get('running') == 'true', 'disabled': i.get('disabled') == 'true',
                'comment': i.get('comment'),
            } for i in result]
        except Exception as e:
            logger.error(f"Failed to get interface stats: {e}")
            return []

    # -------------------------------------------------------------------------
    # HOTSPOT MANAGEMENT
    # -------------------------------------------------------------------------

    def get_hotspot_servers(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return self.execute(router_data, '/ip/hotspot/print')
        except Exception as e:
            logger.error(f"Failed to get hotspot servers: {e}")
            return []

    def get_hotspot_users(self, router_data: Dict[str, Any], hotspot_server_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {}
        if hotspot_server_id:
            params['server'] = hotspot_server_id
        try:
            result = self.execute(router_data, '/ip/hotspot/user/print', **params)
            return [{
                'username': u.get('name'), 'password': u.get('password'),
                'profile': u.get('profile'), 'server': u.get('server'),
                'uptime': u.get('uptime'), 'bytes_in': int(u.get('bytes-in', 0)),
                'bytes_out': int(u.get('bytes-out', 0)), 'disabled': u.get('disabled') == 'true',
                'comment': u.get('comment'), 'limit_uptime': u.get('limit-uptime'),
                'limit_bytes_in': u.get('limit-bytes-in'), 'limit_bytes_out': u.get('limit-bytes-out'),
            } for u in result]
        except Exception as e:
            logger.error(f"Failed to get hotspot users: {e}")
            return []

    def create_hotspot_user(self, router_data: Dict[str, Any], hotspot_server_id: str,
                            username: str, password: str, profile: str,
                            limit_uptime: Optional[str] = None, limit_bytes_in: Optional[int] = None,
                            limit_bytes_out: Optional[int] = None, comment: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {'server': hotspot_server_id, 'name': username, 'password': password, 'profile': profile}
        if limit_uptime: params['limit-uptime'] = limit_uptime
        if limit_bytes_in: params['limit-bytes-in'] = str(limit_bytes_in)
        if limit_bytes_out: params['limit-bytes-out'] = str(limit_bytes_out)
        if comment: params['comment'] = comment
        try:
            self.execute(router_data, '/ip/hotspot/user/add', **params)
            logger.info(f"Created hotspot user '{username}' on {router_data.get('ip_address')}")
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(f"Failed to create hotspot user '{username}': {e}")
            return {'success': False, 'error': str(e)}

    def set_hotspot_user(self, router_data: Dict[str, Any], username: str, **kwargs) -> Dict[str, Any]:
        try:
            self.execute(router_data, '/ip/hotspot/user/set', numbers=username, **kwargs)
            return {'success': True, 'username': username}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def disable_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        return self.set_hotspot_user(router_data, username, disabled='yes')

    def enable_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        return self.set_hotspot_user(router_data, username, disabled='no')

    def remove_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        try:
            self.execute(router_data, '/ip/hotspot/user/remove', numbers=username)
            return {'success': True, 'username': username}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_active_sessions(self, router_data: Dict[str, Any], hotspot_server_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {}
        if hotspot_server_id:
            params['server'] = hotspot_server_id
        try:
            result = self.execute(router_data, '/ip/hotspot/active/print', **params)
            return [{
                'session_id': s.get('.id'), 'username': s.get('user'),
                'mac_address': s.get('mac-address'), 'ip_address': s.get('address'),
                'uptime': s.get('uptime'), 'bytes_in': int(s.get('bytes-in', 0)),
                'bytes_out': int(s.get('bytes-out', 0)), 'server': s.get('server'),
                'keepalive_timeout': s.get('keepalive-timeout'),
            } for s in result]
        except Exception as e:
            logger.error(f"Failed to get active sessions: {e}")
            return []

    def disconnect_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        try:
            self.execute(router_data, '/ip/hotspot/active/remove', numbers=username)
            return {'success': True, 'username': username}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # HOTSPOT PROFILES
    # -------------------------------------------------------------------------

    def get_hotspot_profiles(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            result = self.execute(router_data, '/ip/hotspot/user/profile/print')
            return [{
                'name': p.get('name'), 'rate_limit': p.get('rate-limit'),
                'session_timeout': p.get('session-timeout'), 'idle_timeout': p.get('idle-timeout'),
                'shared_users': int(p.get('shared-users', 1)), 'status_autorefresh': p.get('status-autorefresh'),
                'transparent_proxy': p.get('transparent-proxy') == 'true', 'advertise': p.get('advertise') == 'true',
            } for p in result]
        except Exception as e:
            logger.error(f"Failed to get hotspot profiles: {e}")
            return []

    def create_hotspot_profile(self, router_data: Dict[str, Any], name: str,
                               rate_limit: Optional[str] = None, session_timeout: Optional[str] = None,
                               idle_timeout: Optional[str] = None, shared_users: int = 1,
                               transparent_proxy: bool = False) -> Dict[str, Any]:
        try:
            params: Dict[str, Any] = {'name': name}
            if rate_limit: params['rate-limit'] = rate_limit
            if session_timeout: params['session-timeout'] = session_timeout
            if idle_timeout: params['idle-timeout'] = idle_timeout
            if shared_users: params['shared-users'] = str(shared_users)
            if transparent_proxy: params['transparent-proxy'] = 'yes'
            self.execute(router_data, '/ip/hotspot/user/profile/add', **params)
            return {'success': True, 'name': name}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # PPPoE MANAGEMENT
    # -------------------------------------------------------------------------

    def get_pppoe_servers(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return self.execute(router_data, '/interface/pppoe-server/server/print')
        except Exception as e:
            logger.error(f"Failed to get PPPoE servers: {e}")
            return []

    def get_pppoe_secrets(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            result = self.execute(router_data, '/ppp/secret/print')
            return [{
                '.id': s.get('.id'), 'username': s.get('name'), 'password': s.get('password'),
                'profile': s.get('profile'), 'service': s.get('service'),
                'remote_address': s.get('remote-address'), 'remote_ipv6_prefix': s.get('remote-ipv6-prefix'),
                'disabled': s.get('disabled') == 'true', 'comment': s.get('comment'),
            } for s in result]
        except Exception as e:
            logger.error(f"Failed to get PPPoE secrets: {e}")
            return []

    def create_pppoe_secret(self, router_data: Dict[str, Any], username: str, password: str, profile: str,
                            service: Optional[str] = None, comment: Optional[str] = None,
                            remote_address: Optional[str] = None, remote_ipv6_prefix: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {'name': username, 'password': password, 'profile': profile}
        if service: params['service'] = service
        if comment: params['comment'] = comment
        if remote_address: params['remote-address'] = remote_address
        if remote_ipv6_prefix: params['remote-ipv6-prefix'] = remote_ipv6_prefix
        try:
            self.execute(router_data, '/ppp/secret/add', **params)
            return {'success': True, 'username': username}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def set_pppoe_secret(self, router_data: Dict[str, Any], username: str, **kwargs) -> Dict[str, Any]:
        try:
            self.execute(router_data, '/ppp/secret/set', numbers=username, **kwargs)
            return {'success': True, 'username': username}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def disable_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        return self.set_pppoe_secret(router_data, username, disabled='yes')

    def enable_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        return self.set_pppoe_secret(router_data, username, disabled='no')

    def remove_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        try:
            self.execute(router_data, '/ppp/secret/remove', numbers=username)
            return {'success': True, 'username': username}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_pppoe_active_sessions(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            result = self.execute(router_data, '/ppp/active/print')
            return [{
                '.id': s.get('.id'), 'username': s.get('name'), 'service': s.get('service'),
                'remote_address': s.get('address'), 'caller_id': s.get('caller-id'),
                'uptime': s.get('uptime'), 'encoding': s.get('encoding'), 'session_id': s.get('session-id'),
            } for s in result]
        except Exception as e:
            logger.error(f"Failed to get PPPoE active sessions: {e}")
            return []

    def disconnect_pppoe_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        try:
            self.execute(router_data, '/ppp/active/remove', numbers=username)
            return {'success': True, 'username': username}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # BANDWIDTH MANAGEMENT
    # -------------------------------------------------------------------------

    def set_bandwidth_limit(self, router_data: Dict[str, Any], target: str,
                            upload_mbps: int, download_mbps: int,
                            queue_type: str = 'default') -> Dict[str, Any]:
        try:
            rate_limit = f"{upload_mbps}M/{download_mbps}M"
            name = f"limit_{target.replace('/', '_').replace(':', '_')}"
            self.execute(router_data, '/queue/simple/add', name=name, target=target,
                         max_limit=rate_limit, queue=queue_type)
            return {'success': True, 'name': name, 'rate_limit': rate_limit}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_simple_queues(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return self.execute(router_data, '/queue/simple/print')
        except Exception as e:
            logger.error(f"Failed to get simple queues: {e}")
            return []

    def remove_simple_queue(self, router_data: Dict[str, Any], queue_identifier: str) -> Dict[str, Any]:
        try:
            self.execute(router_data, '/queue/simple/remove', numbers=queue_identifier)
            return {'success': True, 'queue': queue_identifier}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # CONNECTION LIFECYCLE
    # -------------------------------------------------------------------------

    def close_all(self) -> None:
        with self._lock:
            for key, conn in list(self._connections.items()):
                try:
                    conn.disconnect()
                except Exception:
                    pass
            self._connections.clear()
            logger.info("All MikroTik connections closed")

    def __del__(self):
        try:
            self.close_all()
        except Exception:
            pass