import asyncio
import hashlib
import socket
import ssl
import struct
import binascii
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import threading

from flask import current_app

from app.core.logging.logger import logger
from app.core.security.encryption import EncryptionService

class MikroTikAPIError(Exception):
    """MikroTik API exception"""
    pass

class MikroTikConnection:
    """MikroTik API connection with async support and connection pooling"""
    
    def __init__(self, host: str, username: str, password: str, 
                 port: int = 8728, use_ssl: bool = False, timeout: int = 30):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.timeout = timeout
        self.socket = None
        self._connected = False
        self._in_buffer = b''
        self._words = []
        self._lock = threading.Lock()
        self._last_used = datetime.now()
    
    @property
    def is_connected(self) -> bool:
        return self._connected and self.socket is not None
    
    @property
    def last_used(self) -> datetime:
        return self._last_used
    
    def connect(self):
        """Connect to MikroTik router (synchronous)"""
        with self._lock:
            if self.is_connected:
                return
            
            try:
                if self.use_ssl:
                    context = ssl.create_default_context()
                    self.socket = context.wrap_socket(
                        socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                        server_hostname=self.host
                    )
                else:
                    self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                
                self.socket.settimeout(self.timeout)
                self.socket.connect((self.host, self.port))
                self._connected = True
                
                # Authenticate
                self._login()
                logger.info(f"Connected to MikroTik router {self.host}")
                
            except Exception as e:
                self._connected = False
                logger.error(f"Failed to connect to MikroTik {self.host}: {e}")
                raise MikroTikAPIError(f"Connection failed: {e}")
    
    def disconnect(self):
        """Disconnect from router"""
        with self._lock:
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
            self._connected = False
    
    def _login(self):
        """Authenticate with router"""
        # Send login command
        self._send_command('/login')
        
        # Read response
        response = self._read_response()
        
        # Check response
        for word in response:
            if '=ret=' in word:
                # Already logged in
                return
            elif '=challenge=' in word:
                challenge = word.split('=')[1]
                # Calculate response
                response_hash = self._calculate_response(challenge)
                # Send login with response
                self._send_command('/login', f'=name={self.username}', f'=response={response_hash}')
                
                # Read final response
                final_response = self._read_response()
                for final_word in final_response:
                    if '=ret=' in final_word:
                        # Login successful
                        return
                
                raise MikroTikAPIError("Login failed")
        
        raise MikroTikAPIError("Login failed: No challenge received")
    
    def _calculate_response(self, challenge: str) -> str:
        """Calculate MD5 response for challenge"""
        # Challenge format: "challenge" + password
        password_bytes = self.password.encode('utf-8')
        challenge_bytes = binascii.unhexlify(challenge)
        
        # Calculate MD5
        md5 = hashlib.md5()
        md5.update(challenge_bytes)
        md5.update(password_bytes)
        md5.update(challenge_bytes)
        
        return md5.hexdigest().upper()
    
    def _send_command(self, *words):
        """Send command to router"""
        for word in words:
            if not word:
                continue
            word_bytes = word.encode('utf-8')
            length = len(word_bytes)
            
            # Send length prefix (4 bytes, big-endian)
            self.socket.sendall(struct.pack('>I', length))
            # Send word
            self.socket.sendall(word_bytes)
        
        # Send empty word to indicate end of command
        self.socket.sendall(struct.pack('>I', 0))
    
    def _read_response(self) -> List[str]:
        """Read response from router"""
        self._words = []
        self._in_buffer = b''
        
        while True:
            # Read length (4 bytes)
            length_bytes = self._read_exact(4)
            length = struct.unpack('>I', length_bytes)[0]
            
            if length == 0:
                # End of response
                break
            
            # Read word
            word_bytes = self._read_exact(length)
            word = word_bytes.decode('utf-8', errors='ignore')
            self._words.append(word)
        
        return self._words
    
    def _read_exact(self, size: int) -> bytes:
        """Read exact number of bytes"""
        data = b''
        while len(data) < size:
            chunk = self.socket.recv(size - len(data))
            if not chunk:
                raise MikroTikAPIError("Connection closed")
            data += chunk
        return data
    
    def execute(self, command: str, **kwargs) -> List[Dict[str, Any]]:
        """Execute API command"""
        with self._lock:
            if not self.is_connected:
                self.connect()
            
            # Build command
            words = [command]
            for key, value in kwargs.items():
                if value is not None:
                    words.append(f"={key}={value}")
            
            # Send command
            self._send_command(*words)
            
            # Read response
            response = self._read_response()
            
            # Parse response
            result = []
            current = {}
            
            for line in response:
                if line.startswith('!'):
                    if current:
                        result.append(current)
                    current = {'status': line[1:]}
                elif '=' in line:
                    key, value = line.split('=', 1)
                    current[key] = value
                elif line == '.done':
                    pass
            
            if current:
                result.append(current)
            
            self._last_used = datetime.now()
            return result
    
    def execute_batch(self, commands: List[Tuple[str, Dict]]) -> List[List[Dict[str, Any]]]:
        """Execute multiple commands in batch"""
        results = []
        for command, kwargs in commands:
            results.append(self.execute(command, **kwargs))
        return results
    
    def ping(self) -> bool:
        """Check if router is responsive"""
        try:
            self.execute('/system/resource/print', timeout=5)
            return True
        except:
            return False

class MikroTikClient:
    """MikroTik API client with connection pooling and automatic retries"""
    
    def __init__(self):
        self._connections = {}
        self._lock = threading.Lock()
        self.encryption = EncryptionService()
        self.max_connections_per_router = 10
        self.connection_timeout = 300  # 5 minutes idle timeout
    
    def _get_connection_key(self, router_id: str, host: str, port: int) -> str:
        """Generate connection key"""
        return f"{router_id}:{host}:{port}"
    
    def get_connection(self, router_data: Dict[str, Any]) -> MikroTikConnection:
        """Get or create connection to router"""
        router_id = router_data.get('id')
        host = router_data.get('ip_address')
        port = router_data.get('api_port', 8728)
        use_ssl = router_data.get('api_ssl', False)
        
        # Decrypt password
        password = self.encryption.decrypt(router_data.get('password_encrypted', ''))
        
        key = self._get_connection_key(router_id, host, port)
        
        with self._lock:
            if key in self._connections:
                conn = self._connections[key]
                # Check if connection is still valid
                if conn.is_connected:
                    conn._last_used = datetime.now()
                    return conn
                else:
                    # Remove stale connection
                    del self._connections[key]
            
            # Create new connection
            conn = MikroTikConnection(
                host=host,
                username=router_data.get('username'),
                password=password,
                port=port,
                use_ssl=use_ssl,
                timeout=current_app.config.get('MIKROTIK_API_TIMEOUT', 30)
            )
            conn.connect()
            self._connections[key] = conn
            
            # Clean up old connections if too many
            if len(self._connections) > self.max_connections_per_router * 10:
                self._cleanup_connections()
            
            return conn
    
    def _cleanup_connections(self):
        """Remove stale connections"""
        now = datetime.now()
        stale_keys = []
        
        for key, conn in self._connections.items():
            if not conn.is_connected or (now - conn.last_used).seconds > self.connection_timeout:
                stale_keys.append(key)
                conn.disconnect()
        
        for key in stale_keys:
            del self._connections[key]
        
        if stale_keys:
            logger.info(f"Cleaned up {len(stale_keys)} stale connections")
    
    def execute(self, router_data: Dict[str, Any], command: str, 
                retries: int = 3, **kwargs) -> List[Dict[str, Any]]:
        """Execute command with automatic retry"""
        last_error = None
        
        for attempt in range(retries):
            try:
                conn = self.get_connection(router_data)
                return conn.execute(command, **kwargs)
            except (socket.timeout, ConnectionError, MikroTikAPIError) as e:
                last_error = e
                logger.warning(f"Command failed (attempt {attempt + 1}/{retries}): {e}")
                
                # Disconnect and retry
                key = self._get_connection_key(
                    router_data.get('id'),
                    router_data.get('ip_address'),
                    router_data.get('api_port', 8728)
                )
                with self._lock:
                    if key in self._connections:
                        self._connections[key].disconnect()
                        del self._connections[key]
                
                if attempt < retries - 1:
                    import time
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise MikroTikAPIError(f"Command failed after {retries} attempts: {last_error}")
        
        raise MikroTikAPIError(f"Command failed: {last_error}")
    
    def create_hotspot_user(self, router_data: Dict[str, Any], hotspot_server_id: str,
                            username: str, password: str, profile: str,
                            limit_uptime: str = None, limit_bytes_in: int = None,
                            limit_bytes_out: int = None, comment: str = None) -> Dict[str, Any]:
        """Create hotspot user on MikroTik"""
        params = {
            'server': hotspot_server_id,
            'name': username,
            'password': password,
            'profile': profile
        }
        
        if limit_uptime:
            params['limit-uptime'] = limit_uptime
        if limit_bytes_in:
            params['limit-bytes-in'] = str(limit_bytes_in)
        if limit_bytes_out:
            params['limit-bytes-out'] = str(limit_bytes_out)
        if comment:
            params['comment'] = comment
        
        result = self.execute(router_data, '/ip/hotspot/user/add', **params)
        
        logger.info(f"Created hotspot user {username} on router {router_data.get('id')}")
        return {'success': True, 'result': result}
    
    def disable_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Disable hotspot user"""
        result = self.execute(router_data, '/ip/hotspot/user/set',
                              numbers=username, disabled='yes')
        logger.info(f"Disabled hotspot user {username}")
        return {'success': True}
    
    def enable_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Enable hotspot user"""
        result = self.execute(router_data, '/ip/hotspot/user/set',
                              numbers=username, disabled='no')
        logger.info(f"Enabled hotspot user {username}")
        return {'success': True}
    
    def remove_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Remove hotspot user"""
        result = self.execute(router_data, '/ip/hotspot/user/remove', numbers=username)
        logger.info(f"Removed hotspot user {username}")
        return {'success': True}
    
    def get_hotspot_users(self, router_data: Dict[str, Any], 
                          hotspot_server_id: str = None) -> List[Dict[str, Any]]:
        """Get all hotspot users"""
        params = {}
        if hotspot_server_id:
            params['server'] = hotspot_server_id
        
        result = self.execute(router_data, '/ip/hotspot/user/print', **params)
        
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
                'comment': user.get('comment')
            })
        
        return users
    
    def create_pppoe_secret(self, router_data: Dict[str, Any], username: str,
                             password: str, profile: str, service: str = None,
                             comment: str = None, remote_address: str = None,
                             remote_ipv6_prefix: str = None) -> Dict[str, Any]:
        """Create PPPoE secret on MikroTik"""
        params = {
            'name': username,
            'password': password,
            'profile': profile
        }
        
        if service:
            params['service'] = service
        if comment:
            params['comment'] = comment
        if remote_address:
            params['remote-address'] = remote_address
        if remote_ipv6_prefix:
            params['remote-ipv6-prefix'] = remote_ipv6_prefix
        
        result = self.execute(router_data, '/ppp/secret/add', **params)
        
        logger.info(f"Created PPPoE secret {username} on router {router_data.get('id')}")
        return {'success': True, 'result': result}
    
    def disable_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Disable PPPoE secret"""
        result = self.execute(router_data, '/ppp/secret/set',
                              numbers=username, disabled='yes')
        logger.info(f"Disabled PPPoE secret {username}")
        return {'success': True}
    
    def enable_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Enable PPPoE secret"""
        result = self.execute(router_data, '/ppp/secret/set',
                              numbers=username, disabled='no')
        logger.info(f"Enabled PPPoE secret {username}")
        return {'success': True}
    
    def remove_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Remove PPPoE secret"""
        result = self.execute(router_data, '/ppp/secret/remove', numbers=username)
        logger.info(f"Removed PPPoE secret {username}")
        return {'success': True}
    
    def get_pppoe_secrets(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get all PPPoE secrets"""
        result = self.execute(router_data, '/ppp/secret/print')
        
        secrets = []
        for secret in result:
            secrets.append({
                'username': secret.get('name'),
                'password': secret.get('password'),
                'profile': secret.get('profile'),
                'service': secret.get('service'),
                'remote_address': secret.get('remote-address'),
                'disabled': secret.get('disabled') == 'true',
                'comment': secret.get('comment')
            })
        
        return secrets
    
    def get_active_sessions(self, router_data: Dict[str, Any], 
                            hotspot_server_id: str = None) -> List[Dict[str, Any]]:
        """Get active hotspot sessions"""
        params = {}
        if hotspot_server_id:
            params['server'] = hotspot_server_id
        
        try:
            result = self.execute(router_data, '/ip/hotspot/active/print', **params)
            
            sessions = []
            for session in result:
                sessions.append({
                    'username': session.get('user'),
                    'mac_address': session.get('mac-address'),
                    'ip_address': session.get('address'),
                    'uptime': session.get('uptime'),
                    'bytes_in': int(session.get('bytes-in', 0)),
                    'bytes_out': int(session.get('bytes-out', 0)),
                    'server': session.get('server')
                })
            
            return sessions
        except MikroTikAPIError as e:
            logger.error(f"Failed to get active sessions: {e}")
            return []
    
    def get_pppoe_active_sessions(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get active PPPoE sessions"""
        try:
            result = self.execute(router_data, '/ppp/active/print')
            
            sessions = []
            for session in result:
                sessions.append({
                    'username': session.get('name'),
                    'service': session.get('service'),
                    'remote_address': session.get('address'),
                    'caller_id': session.get('caller-id'),
                    'uptime': session.get('uptime'),
                    'encoding': session.get('encoding'),
                    'session_id': session.get('session-id')
                })
            
            return sessions
        except MikroTikAPIError as e:
            logger.error(f"Failed to get PPPoE active sessions: {e}")
            return []
    
    def disconnect_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Disconnect hotspot user"""
        try:
            # Find active session
            sessions = self.get_active_sessions(router_data)
            for session in sessions:
                if session['username'] == username:
                    # Remove session
                    self.execute(router_data, '/ip/hotspot/active/remove',
                                 numbers=username)
                    logger.info(f"Disconnected hotspot user {username}")
                    break
            
            return {'success': True}
        except MikroTikAPIError as e:
            logger.error(f"Failed to disconnect user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def disconnect_pppoe_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """Disconnect PPPoE user"""
        try:
            self.execute(router_data, '/ppp/active/remove', numbers=username)
            logger.info(f"Disconnected PPPoE user {username}")
            return {'success': True}
        except MikroTikAPIError as e:
            logger.error(f"Failed to disconnect PPPoE user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_router_info(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """Get router system information"""
        try:
            resource = self.execute(router_data, '/system/resource/print')
            identity = self.execute(router_data, '/system/identity/print')
            
            if resource:
                resource = resource[0]
            if identity:
                identity = identity[0]
            
            return {
                'hostname': identity.get('name') if identity else None,
                'version': resource.get('version') if resource else None,
                'build_time': resource.get('build-time') if resource else None,
                'uptime': resource.get('uptime') if resource else None,
                'cpu_load': resource.get('cpu-load') if resource else None,
                'free_memory': resource.get('free-memory') if resource else None,
                'total_memory': resource.get('total-memory') if resource else None,
                'free_hdd': resource.get('free-hdd') if resource else None,
                'total_hdd': resource.get('total-hdd') if resource else None,
                'architecture_name': resource.get('architecture-name') if resource else None,
                'board_name': resource.get('board-name') if resource else None,
                'platform': resource.get('platform') if resource else None
            }
        except Exception as e:
            logger.error(f"Failed to get router info: {e}")
            return {}
    
    def get_interface_stats(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get interface statistics"""
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
                    'running': iface.get('running') == 'true'
                })
            
            return interfaces
        except Exception as e:
            logger.error(f"Failed to get interface stats: {e}")
            return []
    
    def set_bandwidth_limit(self, router_data: Dict[str, Any], 
                           target: str, upload: int, download: int) -> Dict[str, Any]:
        """Set bandwidth limit for a user or IP"""
        try:
            # Simple queue for simple bandwidth limiting
            rate_limit = f"{upload}M/{download}M"
            result = self.execute(router_data, '/queue/simple/add',
                                  name=f"limit_{target}",
                                  target=target,
                                  max_limit=rate_limit)
            logger.info(f"Set bandwidth limit {rate_limit} for {target}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to set bandwidth limit: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_hotspot_profiles(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get hotspot user profiles"""
        try:
            result = self.execute(router_data, '/ip/hotspot/user/profile/print')
            
            profiles = []
            for profile in result:
                profiles.append({
                    'name': profile.get('name'),
                    'rate_limit': profile.get('rate-limit'),
                    'session_timeout': profile.get('session-timeout'),
                    'idle_timeout': profile.get('idle-timeout'),
                    'shared_users': profile.get('shared-users'),
                    'status_autorefresh': profile.get('status-autorefresh')
                })
            
            return profiles
        except Exception as e:
            logger.error(f"Failed to get hotspot profiles: {e}")
            return []
    
    def create_hotspot_profile(self, router_data: Dict[str, Any], name: str,
                               rate_limit: str = None, session_timeout: str = None,
                               idle_timeout: str = None, shared_users: int = 1) -> Dict[str, Any]:
        """Create hotspot user profile"""
        try:
            params = {'name': name}
            if rate_limit:
                params['rate-limit'] = rate_limit
            if session_timeout:
                params['session-timeout'] = session_timeout
            if idle_timeout:
                params['idle-timeout'] = idle_timeout
            if shared_users:
                params['shared-users'] = str(shared_users)
            
            result = self.execute(router_data, '/ip/hotspot/user/profile/add', **params)
            logger.info(f"Created hotspot profile {name}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to create hotspot profile: {e}")
            return {'success': False, 'error': str(e)}
    
    def close_all(self):
        """Close all connections"""
        with self._lock:
            for key, conn in self._connections.items():
                try:
                    conn.disconnect()
                except:
                    pass
            self._connections.clear()