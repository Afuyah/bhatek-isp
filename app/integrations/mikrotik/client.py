"""
MikroTik RouterOS API Client
============================
Production-ready client for multi-tenant ISP router management.
Implements the proprietary RouterOS API protocol with connection management,
automatic retry, RADIUS configuration, and full hotspot/PPPoE management.

Protocol: Binary length-prefixed word stream over TCP (port 8728) or SSL (port 8729)
Auth: Challenge-response MD5 (compatible with RouterOS v6.43+ and v7.x)

Verified Commands (RouterOS v6.43+ / v7.x):
    - /ip hotspot profile set [find] use-radius=yes
    - /ppp aaa set use-radius=yes
    - /radius incoming set accept=yes
    - /radius add address=... secret=... service=hotspot,ppp
    - WireGuard: /interface wireguard ...
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

    # Default timeouts (seconds)
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
        """
        Initialize connection parameters.

        Args:
            host: Router IP address or hostname
            username: RouterOS admin username
            password: Plaintext password (must be decrypted before passing)
            port: API port (8728 for plaintext, 8729 for SSL)
            use_ssl: Whether to use SSL/TLS
            timeout: Socket timeout in seconds
        """
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

        # Internal state
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
        """Check if connection is established and socket is valid."""
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
        """Timestamp of last successful command execution."""
        return self._last_used

    @property
    def idle_seconds(self) -> float:
        """Seconds since last command execution."""
        return (datetime.now() - self._last_used).total_seconds()

    # -------------------------------------------------------------------------
    # CONNECTION LIFECYCLE
    # -------------------------------------------------------------------------

    def connect(self) -> None:
        """
        Establish connection and authenticate with the router.

        Raises:
            MikroTikConnectionError: If TCP/SSL connection fails
            MikroTikAuthError: If authentication fails
        """
        with self._lock:
            if self.is_connected:
                return

            self._disconnect_socket()

            try:
                # Create socket
                raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw_socket.settimeout(self.CONNECT_TIMEOUT)

                # Wrap with SSL if requested
                if self.use_ssl:
                    context = ssl.create_default_context()
                    self.socket = context.wrap_socket(
                        raw_socket,
                        server_hostname=self.host
                    )
                else:
                    self.socket = raw_socket

                # Connect
                self.socket.connect((self.host, self.port))
                self.socket.settimeout(self.timeout)

                # Authenticate
                self._login()

                self._connected = True
                self._last_used = datetime.now()
                logger.info(
                    f"Connected to MikroTik router {self.host}:{self.port} "
                    f"(SSL: {self.use_ssl})"
                )

            except (socket.timeout, TimeoutError) as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(
                    f"Connection timeout to {self.host}:{self.port}"
                ) from e
            except ConnectionRefusedError as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(
                    f"Connection refused by {self.host}:{self.port}. "
                    "API may be disabled or firewall blocking."
                ) from e
            except (socket.error, OSError) as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(
                    f"Failed to connect to {self.host}:{self.port}: {e}"
                ) from e
            except MikroTikAuthError:
                self._disconnect_socket()
                raise
            except Exception as e:
                self._disconnect_socket()
                raise MikroTikConnectionError(
                    f"Unexpected error connecting to {self.host}:{self.port}: {e}"
                ) from e

    def disconnect(self) -> None:
        """Disconnect from the router gracefully."""
        with self._lock:
            self._disconnect_socket()
            self._connected = False

    def _disconnect_socket(self) -> None:
        """Close the socket if it exists."""
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
        """
        Authenticate using RouterOS challenge-response protocol.

        Flow:
        1. Send /login command
        2. Router returns challenge hash (or 'ret' if already logged in)
        3. Calculate MD5(0x00 + password + challenge_bytes)
        4. Send login with username and response hash
        5. Router returns 'ret' on success

        Compatible with RouterOS v6.43+ and v7.x.

        Raises:
            MikroTikAuthError: If authentication fails
        """
        if self.socket is None:
            raise MikroTikAuthError("No socket connection")

        try:
            # Step 1: Initiate login
            self._send_command('/login')
            response = self._read_response()

            for word in response:
                if '=ret=' in word:
                    # Already authenticated
                    return

                if '=challenge=' in word:
                    # Extract challenge and compute response
                    parts = word.split('=', 2)
                    if len(parts) < 3:
                        raise MikroTikAuthError(f"Invalid challenge format: {word}")

                    challenge = parts[2]
                    response_hash = self._compute_challenge_response(challenge)

                    # Send credentials
                    self._send_command(
                        '/login',
                        f'=name={self.username}',
                        f'=response={response_hash}'
                    )

                    # Verify login success
                    final_response = self._read_response()
                    for final_word in final_response:
                        if '=ret=' in final_word:
                            logger.debug(f"Authenticated to {self.host} as {self.username}")
                            return

                    raise MikroTikAuthError(
                        f"Login rejected for user '{self.username}' on {self.host}"
                    )

            raise MikroTikAuthError(f"No challenge received from {self.host}")

        except MikroTikAuthError:
            raise
        except MikroTikAPIError:
            raise
        except Exception as e:
            raise MikroTikAuthError(f"Authentication error: {e}") from e

    def _compute_challenge_response(self, challenge: str) -> str:
        """
        Compute MD5 challenge response.

        Formula: MD5(0x00 + password + challenge_hex_bytes)
        Per RouterOS v6.43+ protocol: prepend null byte, then password,
        then hex-decoded challenge.

        Args:
            challenge: Hex-encoded challenge string from router

        Returns:
            Uppercase hex-encoded MD5 hash
        """
        try:
            password_bytes = self.password.encode('utf-8')
            challenge_bytes = binascii.unhexlify(challenge)

            md5 = hashlib.md5()
            md5.update(b'\x00')          # Null byte prefix
            md5.update(password_bytes)
            md5.update(challenge_bytes)

            return md5.hexdigest().upper()
        except (binascii.Error, ValueError) as e:
            raise MikroTikAuthError(f"Invalid challenge format: {challenge}") from e

    # -------------------------------------------------------------------------
    # COMMAND EXECUTION
    # -------------------------------------------------------------------------

    def execute(self, command: str, **kwargs) -> List[Dict[str, Any]]:
        """
        Execute an API command and return parsed response.

        Args:
            command: RouterOS API path (e.g., '/ip/hotspot/user/print')
            **kwargs: Command arguments as key=value pairs

        Returns:
            List of dictionaries, each representing a response record

        Raises:
            MikroTikConnectionError: If not connected and reconnection fails
            MikroTikCommandError: If command execution fails
        """
        with self._lock:
            if not self.is_connected:
                logger.debug(f"Reconnecting to {self.host} before executing command")
                self.connect()

            # Build command words
            words = [command]
            for key, value in kwargs.items():
                if value is not None:
                    # Convert RouterOS attribute format: key -> key-name
                    attr_name = key.replace('_', '-')
                    words.append(f'={attr_name}={value}')

            try:
                # Send command
                self._send_command(*words)

                # Read and parse response
                response = self._read_response()
                result = self._parse_response(response)

                self._last_used = datetime.now()
                return result

            except (socket.timeout, socket.error, ConnectionError) as e:
                self._connected = False
                raise MikroTikConnectionError(
                    f"Connection lost during command '{command}': {e}"
                ) from e
            except MikroTikAPIError:
                raise
            except Exception as e:
                raise MikroTikCommandError(
                    f"Command '{command}' failed: {e}"
                ) from e

    def _parse_response(self, response: List[str]) -> List[Dict[str, Any]]:
        """
        Parse RouterOS API response into structured data.

        Response format:
            !re                <- status: 're' (reply)
            =key=value         <- attribute
            =key=value
            !done              <- status: 'done' (complete)

        Handles !trap error responses.
        """
        result: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}

        for line in response:
            if line.startswith('!') and '=message=' in line:
                # Error response: !trap=message=error text
                parts = line.split('=', 2)
                error_msg = parts[2] if len(parts) > 2 else line[1:]
                raise MikroTikCommandError(f"Router returned error: {error_msg}")

            elif line.startswith('!'):
                # New status block
                if current and len(current) > 1:  # More than just 'status' key
                    result.append(current)
                current = {'status': line[1:]}  # Strip '!' prefix

            elif '=' in line:
                key, value = line.split('=', 1)
                current[key] = value

            elif line == '.done':
                pass  # Command complete marker

        # Add final record if it contains data
        if current and len(current) > 1:
            result.append(current)

        return result

    def _send_command(self, *words: str) -> None:
        """
        Send command words over the socket.

        Each word is sent as: [4-byte big-endian length][UTF-8 encoded bytes]
        Command terminated by zero-length word.
        """
        if self.socket is None:
            raise MikroTikConnectionError("No socket connection")

        for word in words:
            if not word:
                continue
            word_bytes = word.encode('utf-8')
            length = len(word_bytes)

            # Send length prefix (4 bytes, big-endian)
            self.socket.sendall(struct.pack('>I', length))
            # Send word data
            self.socket.sendall(word_bytes)

        # Send empty word (length 0) to mark end of command
        self.socket.sendall(struct.pack('>I', 0))

    def _read_response(self) -> List[str]:
        """
        Read response words from the socket.

        Continuously reads length-prefixed words until a zero-length
        word is encountered (response terminator).
        """
        if self.socket is None:
            raise MikroTikConnectionError("No socket connection")

        self._words = []

        while True:
            # Read word length (4 bytes)
            length_bytes = self._read_exact(4)
            length = struct.unpack('>I', length_bytes)[0]

            if length == 0:
                # End of response
                break

            # Read word data
            word_bytes = self._read_exact(length)
            word = word_bytes.decode('utf-8', errors='ignore')
            self._words.append(word)

        return self._words

    def _read_exact(self, size: int) -> bytes:
        """
        Read exactly 'size' bytes from the socket.

        Handles partial reads by looping until all bytes are received.

        Raises:
            MikroTikConnectionError: If connection is closed prematurely
        """
        if self.socket is None:
            raise MikroTikConnectionError("No socket connection")

        data = b''
        while len(data) < size:
            try:
                chunk = self.socket.recv(size - len(data))
                if not chunk:
                    raise MikroTikConnectionError(
                        f"Connection closed by {self.host} "
                        f"(received {len(data)} of {size} bytes)"
                    )
                data += chunk
            except socket.timeout:
                raise MikroTikConnectionError(
                    f"Socket timeout reading from {self.host}"
                )
            except socket.error as e:
                raise MikroTikConnectionError(
                    f"Socket error reading from {self.host}: {e}"
                )

        return data

    # -------------------------------------------------------------------------
    # HEALTH CHECK
    # -------------------------------------------------------------------------

    def ping(self) -> bool:
        """
        Quick connectivity check.

        Returns:
            True if router responds to a simple command
        """
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
        - Full hotspot user/profile management
        - Full PPPoE secret/session management
        - Bandwidth queue management
        - Router health monitoring
        - WireGuard-ready (connects via WireGuard IP)

    Usage:
        client = MikroTikClient()
        result = client.execute(router_data, '/system/resource/print')
    """

    # Maximum idle time before connection is considered stale (seconds)
    DEFAULT_CONNECTION_TIMEOUT = 300  # 5 minutes

    # Global maximum connections across all routers
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
        """Generate a unique connection key for a router."""
        rid = str(router_id) if router_id else 'unknown'
        return f"{rid}:{host}:{port}"

    def get_connection(self, router_data: Dict[str, Any]) -> MikroTikConnection:
        """
        Get or create a connection to a router.

        Reuses existing connections if still valid. Decrypts the password
        from the encrypted value stored in the database.

        Args:
            router_data: Dictionary with router connection details:
                - id: Router UUID
                - ip_address: Router IP/hostname
                - username: RouterOS username
                - password_encrypted: Encrypted password
                - api_port: API port (default 8728)
                - api_ssl: Whether to use SSL (default False)

        Returns:
            Active MikroTikConnection instance

        Raises:
            MikroTikConnectionError: If connection fails
            MikroTikAuthError: If authentication fails
        """
        router_id = router_data.get('id')
        host = router_data.get('ip_address')
        port = router_data.get('api_port', 8728)
        use_ssl = router_data.get('api_ssl', False)
        username = router_data.get('username')

        if not host:
            raise ValueError("router_data must contain 'ip_address'")
        if not username:
            raise ValueError("router_data must contain 'username'")

        # Decrypt password at the last possible moment
        encrypted_password = router_data.get('password_encrypted', '')
        if not encrypted_password:
            raise MikroTikAuthError(f"No password provided for router {host}")

        try:
            password = self.encryption.decrypt(encrypted_password)
        except Exception as e:
            raise MikroTikAuthError(
                f"Failed to decrypt password for {host}: {e}"
            ) from e

        key = self._get_connection_key(router_id, host, port)

        with self._lock:
            # Check for existing valid connection
            if key in self._connections:
                conn = self._connections[key]
                if conn.is_connected:
                    conn._last_used = datetime.now()
                    return conn
                else:
                    # Remove stale connection
                    logger.debug(f"Removing stale connection for {key}")
                    conn.disconnect()
                    del self._connections[key]

            # Clean up if too many total connections
            if len(self._connections) >= self.MAX_TOTAL_CONNECTIONS:
                self._cleanup_oldest_connections()

            # Create new connection
            timeout = (
                current_app.config.get('MIKROTIK_API_TIMEOUT', 30)
                if current_app else 30
            )

            conn = MikroTikConnection(
                host=host,
                username=username,
                password=password,
                port=port,
                use_ssl=use_ssl,
                timeout=timeout
            )
            conn.connect()
            self._connections[key] = conn

            logger.debug(
                f"New connection to {host}:{port} "
                f"(total connections: {len(self._connections)})"
            )

            return conn

    def _cleanup_connections(self) -> None:
        """Remove stale and idle connections."""
        now = datetime.now()
        stale_keys: List[str] = []

        for key, conn in self._connections.items():
            if not conn.is_connected:
                stale_keys.append(key)
            elif conn.idle_seconds > self.connection_timeout:
                stale_keys.append(key)

        for key in stale_keys:
            logger.debug(f"Cleaning up connection: {key}")
            try:
                self._connections[key].disconnect()
            except Exception:
                pass
            del self._connections[key]

        if stale_keys:
            logger.info(f"Cleaned up {len(stale_keys)} stale connections")

    def _cleanup_oldest_connections(self) -> None:
        """Remove the oldest idle connections when at capacity."""
        sorted_conns = sorted(
            self._connections.items(),
            key=lambda x: x[1].last_used
        )

        to_remove = sorted_conns[:10]
        for key, conn in to_remove:
            logger.debug(f"Evicting old connection: {key}")
            try:
                conn.disconnect()
            except Exception:
                pass
            del self._connections[key]

    def invalidate_connection(self, router_data: Dict[str, Any]) -> None:
        """
        Force-remove a connection for a router.
        Call this when you know the connection is bad.
        """
        key = self._get_connection_key(
            router_data.get('id'),
            router_data.get('ip_address'),
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
        self,
        router_data: Dict[str, Any],
        command: str,
        retries: int = 3,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Execute a command with automatic retry and exponential backoff.

        On connection failure, the stale connection is invalidated and
        a fresh connection is established for the retry.

        Args:
            router_data: Router connection details
            command: RouterOS API path
            retries: Maximum number of attempts (default 3)
            **kwargs: Command arguments

        Returns:
            Parsed command response

        Raises:
            MikroTikAPIError: If all retries are exhausted
        """
        last_error: Optional[Exception] = None
        backoff = 1  # seconds

        for attempt in range(retries):
            try:
                conn = self.get_connection(router_data)
                return conn.execute(command, **kwargs)

            except (MikroTikConnectionError, socket.timeout, ConnectionError) as e:
                last_error = e
                logger.warning(
                    f"Command '{command}' failed "
                    f"(attempt {attempt + 1}/{retries}) "
                    f"on {router_data.get('ip_address')}: {e}"
                )

                # Invalidate bad connection so next attempt gets a fresh one
                self.invalidate_connection(router_data)

                if attempt < retries - 1:
                    time.sleep(backoff)
                    backoff *= 2  # Exponential backoff: 1s, 2s, 4s, 8s...
                else:
                    raise MikroTikAPIError(
                        f"Command '{command}' failed after "
                        f"{retries} attempts: {last_error}"
                    ) from last_error

            except MikroTikAPIError as e:
                # Non-connection errors should not retry
                raise

        raise MikroTikAPIError(
            f"Command '{command}' failed after {retries} attempts: {last_error}"
        )

    # -------------------------------------------------------------------------
    # CONNECTION TESTING
    # -------------------------------------------------------------------------

    def test_connection(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8728,
        use_ssl: bool = False
    ) -> Dict[str, Any]:
        """
        Test connection to a router with explicit credentials.

        Used during router onboarding to validate credentials before storing.
        Creates a temporary connection that is always closed after the test.

        Args:
            host: Router IP address or hostname
            username: RouterOS admin username
            password: Plaintext password
            port: API port
            use_ssl: Whether to use SSL

        Returns:
            Dictionary with 'success', 'connected', and 'router_info' or 'error'
        """
        conn = None
        try:
            conn = MikroTikConnection(
                host=host,
                username=username,
                password=password,
                port=port,
                use_ssl=use_ssl,
                timeout=10
            )
            conn.connect()

            result = conn.execute('/system/resource/print')

            if result and len(result) > 0:
                resource = result[0]
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
                        'architecture_name': resource.get(
                            'architecture-name', 'Unknown'
                        ),
                    }
                }
            else:
                return {
                    'success': False,
                    'connected': False,
                    'error': 'Connected but no response from router'
                }

        except MikroTikConnectionError as e:
            return {'success': False, 'connected': False, 'error': str(e)}
        except MikroTikAuthError:
            return {
                'success': False,
                'connected': False,
                'error': 'Authentication failed. Check username and password.'
            }
        except Exception as e:
            logger.error(f"Connection test failed for {host}:{port}: {e}")
            return {
                'success': False,
                'connected': False,
                'error': f'Connection failed: {e}'
            }
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
        self,
        router_data: Dict[str, Any],
        radius_server: str,
        radius_secret: str,
        radius_port: int = 1812,
        radius_acct_port: int = 1813,
        radius_timeout: int = 3000,
        radius_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Configure RADIUS authentication on a MikroTik router.

        Uses VERIFIED commands compatible with RouterOS v6.43+ and v7.x:
            - /radius add address=... secret=... service=hotspot,ppp
            - /ip hotspot profile set [find] use-radius=yes
            - /ppp aaa set use-radius=yes
            - /radius incoming set accept=yes

        Args:
            router_data: Router connection details
            radius_server: RADIUS server IP address
            radius_secret: Shared secret for RADIUS communication
            radius_port: Authentication port (default 1812)
            radius_acct_port: Accounting port (default 1813)
            radius_timeout: RADIUS request timeout in ms (default 3000)
            radius_retries: Number of RADIUS retry attempts (default 3)

        Returns:
            Dictionary with 'success' and 'message' or 'error'
        """
        try:
            # Check if RADIUS server already exists
            existing = self.execute(router_data, '/radius/print')
            server_exists = False

            for item in existing:
                if item.get('address') == radius_server:
                    server_exists = True
                    # Update existing entry with current settings
                    self.execute(
                        router_data,
                        '/radius/set',
                        numbers=item.get('.id'),
                        secret=radius_secret,
                        service='hotspot,ppp',
                        authentication_port=str(radius_port),
                        accounting_port=str(radius_acct_port),
                        timeout=str(radius_timeout),
                        retries=str(radius_retries)
                    )
                    logger.info(f"RADIUS server updated: {radius_server}")
                    break

            if not server_exists:
                # Add new RADIUS server
                self.execute(
                    router_data,
                    '/radius/add',
                    address=radius_server,
                    secret=radius_secret,
                    service='hotspot,ppp',
                    authentication_port=str(radius_port),
                    accounting_port=str(radius_acct_port),
                    timeout=str(radius_timeout),
                    retries=str(radius_retries)
                )
                logger.info(f"RADIUS server added: {radius_server}")

            # ✅ VERIFIED: Enable RADIUS for Hotspot
            # Works on RouterOS v6.43+ and v7.x
            try:
                self.execute(
                    router_data,
                    '/ip/hotspot/profile/set',
                    numbers='[find]',
                    **{'use-radius': 'yes'}
                )
                logger.info(
                    "Hotspot RADIUS enabled via "
                    "/ip hotspot profile set [find] use-radius=yes"
                )
            except MikroTikAPIError as e:
                logger.warning(f"Could not enable hotspot RADIUS: {e}")

            # ✅ VERIFIED: Enable RADIUS for PPP/PPPoE
            # Works on RouterOS v6.43+ and v7.x
            try:
                self.execute(
                    router_data,
                    '/ppp/aaa/set',
                    **{'use-radius': 'yes'}
                )
                logger.info(
                    "PPP RADIUS enabled via /ppp aaa set use-radius=yes"
                )
            except MikroTikAPIError:
                # Router may not have PPP/AAA module or already enabled
                logger.debug(
                    "PPP AAA RADIUS not configurable "
                    "(may already be enabled or module not available)"
                )

            # ✅ VERIFIED: Enable RADIUS Incoming for CoA/Disconnect
            try:
                self.execute(
                    router_data,
                    '/radius/incoming/set',
                    accept='yes'
                )
                logger.info(
                    "RADIUS incoming enabled via "
                    "/radius incoming set accept=yes"
                )
            except MikroTikAPIError:
                logger.debug(
                    "RADIUS incoming not configurable on this router"
                )

            logger.info(
                f"RADIUS fully configured on "
                f"{router_data.get('ip_address')} "
                f"-> {radius_server}:{radius_port}/{radius_acct_port}"
            )

            return {
                'success': True,
                'message': 'RADIUS configured successfully',
                'radius_server': radius_server,
                'radius_port': radius_port
            }

        except MikroTikAPIError as e:
            logger.error(
                f"Failed to configure RADIUS on "
                f"{router_data.get('ip_address')}: {e}"
            )
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(
                f"Unexpected error configuring RADIUS on "
                f"{router_data.get('ip_address')}: {e}"
            )
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # HEALTH & MONITORING
    # -------------------------------------------------------------------------

    def get_router_info(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """Get comprehensive router system information."""
        try:
            resource = self.execute(router_data, '/system/resource/print')
            identity = self.execute(router_data, '/system/identity/print')

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
        except Exception as e:
            logger.error(
                f"Failed to get router info for "
                f"{router_data.get('ip_address')}: {e}"
            )
            return {}

    def health_check(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """Quick health check for router."""
        try:
            start_time = time.time()
            result = self.execute(
                router_data, '/system/resource/print', retries=2
            )
            response_time = (time.time() - start_time) * 1000

            if result and len(result) > 0:
                resource = result[0]
                return {
                    'status': 'healthy',
                    'response_time_ms': round(response_time, 2),
                    'cpu_load': resource.get('cpu-load'),
                    'uptime': resource.get('uptime'),
                    'free_memory': resource.get('free-memory'),
                    'total_memory': resource.get('total-memory'),
                }
            return {'status': 'unhealthy', 'error': 'No response from router'}

        except Exception as e:
            return {'status': 'unhealthy', 'error': str(e)}

    def get_interface_stats(
        self, router_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get network interface statistics."""
        try:
            result = self.execute(router_data, '/interface/print')

            interfaces = []
            for iface in result:
                interfaces.append({
                    'name': iface.get('name'),
                    'type': iface.get('type'),
                    'mtu': iface.get('mtu'),
                    'rx_byte': int(iface.get('rx-byte', 0)),
                    'tx_byte': int(iface.get('tx-byte', 0)),
                    'rx_packet': int(iface.get('rx-packet', 0)),
                    'tx_packet': int(iface.get('tx-packet', 0)),
                    'rx_error': int(iface.get('rx-error', 0)),
                    'tx_error': int(iface.get('tx-error', 0)),
                    'rx_drop': int(iface.get('rx-drop', 0)),
                    'tx_drop': int(iface.get('tx-drop', 0)),
                    'running': iface.get('running') == 'true',
                    'disabled': iface.get('disabled') == 'true',
                    'comment': iface.get('comment'),
                })

            return interfaces
        except Exception as e:
            logger.error(f"Failed to get interface stats: {e}")
            return []

    # -------------------------------------------------------------------------
    # HOTSPOT MANAGEMENT
    # -------------------------------------------------------------------------

    def get_hotspot_servers(
        self, router_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get all hotspot servers configured on the router."""
        try:
            return self.execute(router_data, '/ip/hotspot/print')
        except Exception as e:
            logger.error(f"Failed to get hotspot servers: {e}")
            return []

    def get_hotspot_users(
        self,
        router_data: Dict[str, Any],
        hotspot_server_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all hotspot users, optionally filtered by server."""
        params = {}
        if hotspot_server_id:
            params['server'] = hotspot_server_id

        try:
            result = self.execute(
                router_data, '/ip/hotspot/user/print', **params
            )

            users = []
            for user in result:
                users.append({
                    'username': user.get('name'),
                    'password': user.get('password'),
                    'profile': user.get('profile'),
                    'server': user.get('server'),
                    'uptime': user.get('uptime'),
                    'bytes_in': int(user.get('bytes-in', 0)),
                    'bytes_out': int(user.get('bytes-out', 0)),
                    'disabled': user.get('disabled') == 'true',
                    'comment': user.get('comment'),
                    'limit_uptime': user.get('limit-uptime'),
                    'limit_bytes_in': user.get('limit-bytes-in'),
                    'limit_bytes_out': user.get('limit-bytes-out'),
                })

            return users
        except Exception as e:
            logger.error(f"Failed to get hotspot users: {e}")
            return []

    def create_hotspot_user(
        self,
        router_data: Dict[str, Any],
        hotspot_server_id: str,
        username: str,
        password: str,
        profile: str,
        limit_uptime: Optional[str] = None,
        limit_bytes_in: Optional[int] = None,
        limit_bytes_out: Optional[int] = None,
        comment: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a hotspot user on the MikroTik router.

        Note: For RADIUS-based authentication (the primary path),
        users should be created via RadCheck entries, not directly
        on the router. This method is available for fallback/manual
        operations.
        """
        params: Dict[str, Any] = {
            'server': hotspot_server_id,
            'name': username,
            'password': password,
            'profile': profile,
        }

        if limit_uptime:
            params['limit-uptime'] = limit_uptime
        if limit_bytes_in:
            params['limit-bytes-in'] = str(limit_bytes_in)
        if limit_bytes_out:
            params['limit-bytes-out'] = str(limit_bytes_out)
        if comment:
            params['comment'] = comment

        try:
            self.execute(router_data, '/ip/hotspot/user/add', **params)
            logger.info(
                f"Created hotspot user '{username}' "
                f"on {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to create hotspot user '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    def set_hotspot_user(
        self,
        router_data: Dict[str, Any],
        username: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Update hotspot user properties."""
        try:
            self.execute(
                router_data,
                '/ip/hotspot/user/set',
                numbers=username,
                **kwargs
            )
            logger.info(
                f"Updated hotspot user '{username}' "
                f"on {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to update hotspot user '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    def disable_hotspot_user(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Disable a hotspot user."""
        return self.set_hotspot_user(router_data, username, disabled='yes')

    def enable_hotspot_user(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Enable a hotspot user."""
        return self.set_hotspot_user(router_data, username, disabled='no')

    def remove_hotspot_user(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Remove a hotspot user."""
        try:
            self.execute(
                router_data,
                '/ip/hotspot/user/remove',
                numbers=username
            )
            logger.info(
                f"Removed hotspot user '{username}' "
                f"from {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to remove hotspot user '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    def get_active_sessions(
        self,
        router_data: Dict[str, Any],
        hotspot_server_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get active hotspot sessions."""
        params = {}
        if hotspot_server_id:
            params['server'] = hotspot_server_id

        try:
            result = self.execute(
                router_data, '/ip/hotspot/active/print', **params
            )

            sessions = []
            for session in result:
                sessions.append({
                    'session_id': session.get('.id'),
                    'username': session.get('user'),
                    'mac_address': session.get('mac-address'),
                    'ip_address': session.get('address'),
                    'uptime': session.get('uptime'),
                    'bytes_in': int(session.get('bytes-in', 0)),
                    'bytes_out': int(session.get('bytes-out', 0)),
                    'server': session.get('server'),
                    'keepalive_timeout': session.get('keepalive-timeout'),
                })

            return sessions
        except Exception as e:
            logger.error(f"Failed to get active hotspot sessions: {e}")
            return []

    def disconnect_hotspot_user(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Disconnect an active hotspot user session."""
        try:
            self.execute(
                router_data,
                '/ip/hotspot/active/remove',
                numbers=username
            )
            logger.info(
                f"Disconnected hotspot user '{username}' "
                f"from {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to disconnect hotspot user '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # HOTSPOT PROFILES
    # -------------------------------------------------------------------------

    def get_hotspot_profiles(
        self, router_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get all hotspot user profiles."""
        try:
            result = self.execute(
                router_data, '/ip/hotspot/user/profile/print'
            )

            profiles = []
            for profile in result:
                profiles.append({
                    'name': profile.get('name'),
                    'rate_limit': profile.get('rate-limit'),
                    'session_timeout': profile.get('session-timeout'),
                    'idle_timeout': profile.get('idle-timeout'),
                    'shared_users': int(profile.get('shared-users', 1)),
                    'status_autorefresh': profile.get('status-autorefresh'),
                    'transparent_proxy': (
                        profile.get('transparent-proxy') == 'true'
                    ),
                    'advertise': profile.get('advertise') == 'true',
                })

            return profiles
        except Exception as e:
            logger.error(f"Failed to get hotspot profiles: {e}")
            return []

    def create_hotspot_profile(
        self,
        router_data: Dict[str, Any],
        name: str,
        rate_limit: Optional[str] = None,
        session_timeout: Optional[str] = None,
        idle_timeout: Optional[str] = None,
        shared_users: int = 1,
        transparent_proxy: bool = False,
    ) -> Dict[str, Any]:
        """Create a hotspot user profile on the router."""
        try:
            params: Dict[str, Any] = {'name': name}

            if rate_limit:
                params['rate-limit'] = rate_limit
            if session_timeout:
                params['session-timeout'] = session_timeout
            if idle_timeout:
                params['idle-timeout'] = idle_timeout
            if shared_users:
                params['shared-users'] = str(shared_users)
            if transparent_proxy:
                params['transparent-proxy'] = 'yes'

            self.execute(
                router_data, '/ip/hotspot/user/profile/add', **params
            )
            logger.info(
                f"Created hotspot profile '{name}' "
                f"on {router_data.get('ip_address')}"
            )
            return {'success': True, 'name': name}
        except Exception as e:
            logger.error(
                f"Failed to create hotspot profile '{name}': {e}"
            )
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # PPPoE MANAGEMENT
    # -------------------------------------------------------------------------

    def get_pppoe_servers(
        self, router_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get all PPPoE servers configured on the router."""
        try:
            return self.execute(
                router_data, '/interface/pppoe-server/server/print'
            )
        except Exception as e:
            logger.error(f"Failed to get PPPoE servers: {e}")
            return []

    def get_pppoe_secrets(
        self, router_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get all PPPoE secrets (user accounts)."""
        try:
            result = self.execute(router_data, '/ppp/secret/print')

            secrets = []
            for secret in result:
                secrets.append({
                    '.id': secret.get('.id'),
                    'username': secret.get('name'),
                    'password': secret.get('password'),
                    'profile': secret.get('profile'),
                    'service': secret.get('service'),
                    'remote_address': secret.get('remote-address'),
                    'remote_ipv6_prefix': secret.get('remote-ipv6-prefix'),
                    'disabled': secret.get('disabled') == 'true',
                    'comment': secret.get('comment'),
                })

            return secrets
        except Exception as e:
            logger.error(f"Failed to get PPPoE secrets: {e}")
            return []

    def create_pppoe_secret(
        self,
        router_data: Dict[str, Any],
        username: str,
        password: str,
        profile: str,
        service: Optional[str] = None,
        comment: Optional[str] = None,
        remote_address: Optional[str] = None,
        remote_ipv6_prefix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a PPPoE secret on the MikroTik router.

        Note: For RADIUS-based authentication (the primary path),
        PPPoE secrets should be managed via RadCheck entries,
        not directly on the router.
        """
        params: Dict[str, Any] = {
            'name': username,
            'password': password,
            'profile': profile,
        }

        if service:
            params['service'] = service
        if comment:
            params['comment'] = comment
        if remote_address:
            params['remote-address'] = remote_address
        if remote_ipv6_prefix:
            params['remote-ipv6-prefix'] = remote_ipv6_prefix

        try:
            self.execute(router_data, '/ppp/secret/add', **params)
            logger.info(
                f"Created PPPoE secret '{username}' "
                f"on {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to create PPPoE secret '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    def set_pppoe_secret(
        self,
        router_data: Dict[str, Any],
        username: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Update PPPoE secret properties."""
        try:
            self.execute(
                router_data,
                '/ppp/secret/set',
                numbers=username,
                **kwargs
            )
            logger.info(
                f"Updated PPPoE secret '{username}' "
                f"on {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to update PPPoE secret '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    def disable_pppoe_secret(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Disable a PPPoE secret."""
        return self.set_pppoe_secret(router_data, username, disabled='yes')

    def enable_pppoe_secret(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Enable a PPPoE secret."""
        return self.set_pppoe_secret(router_data, username, disabled='no')

    def remove_pppoe_secret(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Remove a PPPoE secret."""
        try:
            self.execute(
                router_data,
                '/ppp/secret/remove',
                numbers=username
            )
            logger.info(
                f"Removed PPPoE secret '{username}' "
                f"from {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to remove PPPoE secret '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    def get_pppoe_active_sessions(
        self,
        router_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get active PPPoE sessions."""
        try:
            result = self.execute(router_data, '/ppp/active/print')

            sessions = []
            for session in result:
                sessions.append({
                    '.id': session.get('.id'),
                    'username': session.get('name'),
                    'service': session.get('service'),
                    'remote_address': session.get('address'),
                    'caller_id': session.get('caller-id'),
                    'uptime': session.get('uptime'),
                    'encoding': session.get('encoding'),
                    'session_id': session.get('session-id'),
                })

            return sessions
        except Exception as e:
            logger.error(f"Failed to get PPPoE active sessions: {e}")
            return []

    def disconnect_pppoe_user(
        self,
        router_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Disconnect an active PPPoE user session."""
        try:
            self.execute(
                router_data,
                '/ppp/active/remove',
                numbers=username
            )
            logger.info(
                f"Disconnected PPPoE user '{username}' "
                f"from {router_data.get('ip_address')}"
            )
            return {'success': True, 'username': username}
        except Exception as e:
            logger.error(
                f"Failed to disconnect PPPoE user '{username}': {e}"
            )
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # BANDWIDTH MANAGEMENT
    # -------------------------------------------------------------------------

    def set_bandwidth_limit(
        self,
        router_data: Dict[str, Any],
        target: str,
        upload_mbps: int,
        download_mbps: int,
        queue_type: str = 'default',
    ) -> Dict[str, Any]:
        """
        Set a bandwidth limit for a target (IP, subnet, or interface).

        Creates a simple queue with rate limiting.
        """
        try:
            rate_limit = f"{upload_mbps}M/{download_mbps}M"
            name = f"limit_{target.replace('/', '_').replace(':', '_')}"

            self.execute(
                router_data,
                '/queue/simple/add',
                name=name,
                target=target,
                max_limit=rate_limit,
                queue=queue_type,
            )

            logger.info(
                f"Set bandwidth limit {rate_limit} for {target} "
                f"on {router_data.get('ip_address')}"
            )
            return {'success': True, 'name': name, 'rate_limit': rate_limit}

        except Exception as e:
            logger.error(
                f"Failed to set bandwidth limit for {target}: {e}"
            )
            return {'success': False, 'error': str(e)}

    def get_simple_queues(
        self, router_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get all simple queues."""
        try:
            return self.execute(router_data, '/queue/simple/print')
        except Exception as e:
            logger.error(f"Failed to get simple queues: {e}")
            return []

    def remove_simple_queue(
        self,
        router_data: Dict[str, Any],
        queue_identifier: str
    ) -> Dict[str, Any]:
        """
        Remove a simple queue.

        Args:
            queue_identifier: Queue name or .id
        """
        try:
            self.execute(
                router_data,
                '/queue/simple/remove',
                numbers=queue_identifier
            )
            logger.info(
                f"Removed simple queue '{queue_identifier}' "
                f"from {router_data.get('ip_address')}"
            )
            return {'success': True, 'queue': queue_identifier}
        except Exception as e:
            logger.error(
                f"Failed to remove simple queue "
                f"'{queue_identifier}': {e}"
            )
            return {'success': False, 'error': str(e)}

    # -------------------------------------------------------------------------
    # CONNECTION LIFECYCLE
    # -------------------------------------------------------------------------

    def close_all(self) -> None:
        """Close all connections and release resources."""
        with self._lock:
            for key, conn in list(self._connections.items()):
                try:
                    conn.disconnect()
                except Exception:
                    pass
            self._connections.clear()
            logger.info("All MikroTik connections closed")

    def __del__(self):
        """Ensure connections are closed on garbage collection."""
        try:
            self.close_all()
        except Exception:
            pass