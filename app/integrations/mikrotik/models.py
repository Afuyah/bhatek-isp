from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field

@dataclass
class MikroTikRouter:
    """MikroTik router data model"""
    id: str
    name: str
    host: str
    username: str
    password: str
    port: int = 8728
    use_ssl: bool = False
    connection_pool_size: int = 5
    status: str = 'unknown'
    last_seen_at: Optional[datetime] = None
    settings: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'host': self.host,
            'username': self.username,
            'port': self.port,
            'use_ssl': self.use_ssl,
            'connection_pool_size': self.connection_pool_size,
            'status': self.status,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'settings': self.settings
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MikroTikRouter':
        return cls(
            id=data.get('id'),
            name=data.get('name'),
            host=data.get('host') or data.get('ip_address'),
            username=data.get('username'),
            password=data.get('password_encrypted', ''),
            port=data.get('port', data.get('api_port', 8728)),
            use_ssl=data.get('use_ssl', data.get('api_ssl', False)),
            connection_pool_size=data.get('connection_pool_size', 5),
            status=data.get('status', 'unknown'),
            last_seen_at=data.get('last_seen_at'),
            settings=data.get('settings', {})
        )

@dataclass
class HotspotUser:
    """Hotspot user data model"""
    username: str
    password: str
    profile: str
    server: str
    uptime: Optional[str] = None
    bytes_in: int = 0
    bytes_out: int = 0
    disabled: bool = False
    comment: Optional[str] = None
    limit_uptime: Optional[str] = None
    limit_bytes_in: Optional[int] = None
    limit_bytes_out: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'username': self.username,
            'password': self.password,
            'profile': self.profile,
            'server': self.server,
            'uptime': self.uptime,
            'bytes_in': self.bytes_in,
            'bytes_out': self.bytes_out,
            'disabled': self.disabled,
            'comment': self.comment,
            'limit_uptime': self.limit_uptime,
            'limit_bytes_in': self.limit_bytes_in,
            'limit_bytes_out': self.limit_bytes_out
        }
    
    @classmethod
    def from_mikrotik(cls, data: Dict[str, Any]) -> 'HotspotUser':
        return cls(
            username=data.get('name', data.get('user')),
            password=data.get('password', ''),
            profile=data.get('profile', 'default'),
            server=data.get('server', ''),
            uptime=data.get('uptime'),
            bytes_in=int(data.get('bytes-in', 0)),
            bytes_out=int(data.get('bytes-out', 0)),
            disabled=data.get('disabled') == 'true',
            comment=data.get('comment'),
            limit_uptime=data.get('limit-uptime'),
            limit_bytes_in=int(data.get('limit-bytes-in', 0)) if data.get('limit-bytes-in') else None,
            limit_bytes_out=int(data.get('limit-bytes-out', 0)) if data.get('limit-bytes-out') else None
        )

@dataclass
class PPPoESecret:
    """PPPoE secret data model"""
    username: str
    password: str
    profile: str
    service: Optional[str] = None
    remote_address: Optional[str] = None
    remote_ipv6_prefix: Optional[str] = None
    disabled: bool = False
    comment: Optional[str] = None
    routes: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'username': self.username,
            'password': self.password,
            'profile': self.profile,
            'service': self.service,
            'remote_address': self.remote_address,
            'remote_ipv6_prefix': self.remote_ipv6_prefix,
            'disabled': self.disabled,
            'comment': self.comment,
            'routes': self.routes
        }
    
    @classmethod
    def from_mikrotik(cls, data: Dict[str, Any]) -> 'PPPoESecret':
        return cls(
            username=data.get('name'),
            password=data.get('password', ''),
            profile=data.get('profile', 'default'),
            service=data.get('service'),
            remote_address=data.get('remote-address'),
            remote_ipv6_prefix=data.get('remote-ipv6-prefix'),
            disabled=data.get('disabled') == 'true',
            comment=data.get('comment'),
            routes=data.get('routes')
        )

@dataclass
class HotspotActiveSession:
    """Active hotspot session data model"""
    username: str
    mac_address: str
    ip_address: str
    server: str
    uptime: str
    bytes_in: int = 0
    bytes_out: int = 0
    session_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'username': self.username,
            'mac_address': self.mac_address,
            'ip_address': self.ip_address,
            'server': self.server,
            'uptime': self.uptime,
            'bytes_in': self.bytes_in,
            'bytes_out': self.bytes_out,
            'session_id': self.session_id
        }
    
    @classmethod
    def from_mikrotik(cls, data: Dict[str, Any]) -> 'HotspotActiveSession':
        return cls(
            username=data.get('user'),
            mac_address=data.get('mac-address'),
            ip_address=data.get('address'),
            server=data.get('server'),
            uptime=data.get('uptime', '0s'),
            bytes_in=int(data.get('bytes-in', 0)),
            bytes_out=int(data.get('bytes-out', 0)),
            session_id=data.get('.id')
        )

@dataclass
class PPPoEActiveSession:
    """Active PPPoE session data model"""
    username: str
    service: str
    remote_address: str
    caller_id: str
    uptime: str
    encoding: Optional[str] = None
    session_id: Optional[str] = None
    radius_session_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'username': self.username,
            'service': self.service,
            'remote_address': self.remote_address,
            'caller_id': self.caller_id,
            'uptime': self.uptime,
            'encoding': self.encoding,
            'session_id': self.session_id,
            'radius_session_id': self.radius_session_id
        }
    
    @classmethod
    def from_mikrotik(cls, data: Dict[str, Any]) -> 'PPPoEActiveSession':
        return cls(
            username=data.get('name'),
            service=data.get('service', ''),
            remote_address=data.get('address', ''),
            caller_id=data.get('caller-id', ''),
            uptime=data.get('uptime', '0s'),
            encoding=data.get('encoding'),
            session_id=data.get('session-id'),
            radius_session_id=data.get('radius-session-id')
        )

@dataclass
class HotspotProfile:
    """Hotspot user profile data model"""
    name: str
    rate_limit: Optional[str] = None
    session_timeout: Optional[str] = None
    idle_timeout: Optional[str] = None
    shared_users: int = 1
    status_autorefresh: Optional[str] = None
    transparent_proxy: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'rate_limit': self.rate_limit,
            'session_timeout': self.session_timeout,
            'idle_timeout': self.idle_timeout,
            'shared_users': self.shared_users,
            'status_autorefresh': self.status_autorefresh,
            'transparent_proxy': self.transparent_proxy
        }
    
    @classmethod
    def from_mikrotik(cls, data: Dict[str, Any]) -> 'HotspotProfile':
        return cls(
            name=data.get('name'),
            rate_limit=data.get('rate-limit'),
            session_timeout=data.get('session-timeout'),
            idle_timeout=data.get('idle-timeout'),
            shared_users=int(data.get('shared-users', 1)),
            status_autorefresh=data.get('status-autorefresh'),
            transparent_proxy=data.get('transparent-proxy') == 'true'
        )

@dataclass
class InterfaceStats:
    """Network interface statistics"""
    name: str
    type: str
    mtu: int
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    rx_drops: int = 0
    tx_drops: int = 0
    running: bool = False
    enabled: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'type': self.type,
            'mtu': self.mtu,
            'rx_bytes': self.rx_bytes,
            'tx_bytes': self.tx_bytes,
            'rx_packets': self.rx_packets,
            'tx_packets': self.tx_packets,
            'rx_errors': self.rx_errors,
            'tx_errors': self.tx_errors,
            'rx_drops': self.rx_drops,
            'tx_drops': self.tx_drops,
            'running': self.running,
            'enabled': self.enabled
        }
    
    @classmethod
    def from_mikrotik(cls, data: Dict[str, Any]) -> 'InterfaceStats':
        return cls(
            name=data.get('name'),
            type=data.get('type', 'unknown'),
            mtu=int(data.get('mtu', 1500)),
            rx_bytes=int(data.get('rx-byte', 0)),
            tx_bytes=int(data.get('tx-byte', 0)),
            rx_packets=int(data.get('rx-packet', 0)),
            tx_packets=int(data.get('tx-packet', 0)),
            rx_errors=int(data.get('rx-error', 0)),
            tx_errors=int(data.get('tx-error', 0)),
            rx_drops=int(data.get('rx-drop', 0)),
            tx_drops=int(data.get('tx-drop', 0)),
            running=data.get('running') == 'true',
            enabled=data.get('disabled') != 'true'
        )