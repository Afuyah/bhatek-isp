from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field
from uuid import UUID


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
    
    # Additional fields for compatibility with your Router model
    organization_id: Optional[str] = None
    network_id: Optional[str] = None
    model: Optional[str] = None
    routeros_version: Optional[str] = None
    serial_number: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'name': self.name,
            'host': self.host,
            'ip_address': self.host,  # Alias for compatibility
            'username': self.username,
            'port': self.port,
            'api_port': self.port,  # Alias for compatibility
            'use_ssl': self.use_ssl,
            'api_ssl': self.use_ssl,  # Alias for compatibility
            'connection_pool_size': self.connection_pool_size,
            'status': self.status,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'settings': self.settings,
            'organization_id': self.organization_id,
            'network_id': self.network_id,
            'model': self.model,
            'routeros_version': self.routeros_version,
            'serial_number': self.serial_number,
            'location': self.location,
            'description': self.description,
            'is_active': self.is_active
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MikroTikRouter':
        """Create from dictionary (from Router model)"""
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
            settings=data.get('settings', {}),
            organization_id=str(data.get('organization_id')) if data.get('organization_id') else None,
            network_id=str(data.get('network_id')) if data.get('network_id') else None,
            model=data.get('model'),
            routeros_version=data.get('routeros_version'),
            serial_number=data.get('serial_number'),
            location=data.get('location'),
            description=data.get('description'),
            is_active=data.get('is_active', True)
        )
    
    @classmethod
    def from_router_model(cls, router_model) -> 'MikroTikRouter':
        """Create from SQLAlchemy Router model"""
        return cls(
            id=str(router_model.id),
            name=router_model.name,
            host=str(router_model.ip_address),
            username=router_model.username,
            password=router_model.password_encrypted,  # Still encrypted, decrypt when used
            port=router_model.api_port or 8728,
            use_ssl=False,
            status=router_model.status or 'unknown',
            last_seen_at=router_model.last_seen_at,
            settings=router_model.settings or {},
            organization_id=str(router_model.organization_id) if router_model.organization_id else None,
            network_id=str(router_model.network_id) if router_model.network_id else None,
            model=router_model.model,
            routeros_version=router_model.routeros_version,
            serial_number=router_model.serial_number,
            location=router_model.location,
            description=router_model.description,
            is_active=router_model.is_active
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


@dataclass
class RouterHealth:
    """Router health metrics"""
    cpu_load: int = 0
    memory_used: int = 0
    memory_total: int = 0
    uptime_seconds: int = 0
    temperature: Optional[float] = None
    board_temp: Optional[float] = None
    voltage: Optional[float] = None
    
    @property
    def memory_usage_percent(self) -> float:
        if self.memory_total > 0:
            return (self.memory_used / self.memory_total) * 100
        return 0
    
    @property
    def uptime_hours(self) -> float:
        return self.uptime_seconds / 3600
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'cpu_load': self.cpu_load,
            'memory_used': self.memory_used,
            'memory_total': self.memory_total,
            'memory_usage_percent': self.memory_usage_percent,
            'uptime_seconds': self.uptime_seconds,
            'uptime_hours': self.uptime_hours,
            'temperature': self.temperature,
            'board_temp': self.board_temp,
            'voltage': self.voltage
        }
    
    @classmethod
    def from_resource(cls, resource_data: Dict[str, Any]) -> 'RouterHealth':
        uptime_str = resource_data.get('uptime', '0s')
        uptime_seconds = cls._parse_uptime(uptime_str)
        
        return cls(
            cpu_load=int(resource_data.get('cpu-load', 0)),
            memory_used=int(resource_data.get('free-memory', 0)),
            memory_total=int(resource_data.get('total-memory', 0)),
            uptime_seconds=uptime_seconds,
            temperature=float(resource_data.get('temperature')) if resource_data.get('temperature') else None,
            board_temp=float(resource_data.get('board-temperature')) if resource_data.get('board-temperature') else None,
            voltage=float(resource_data.get('voltage')) if resource_data.get('voltage') else None
        )
    
    @staticmethod
    def _parse_uptime(uptime_str: str) -> int:
        """Parse MikroTik uptime string to seconds"""
        seconds = 0
        parts = uptime_str.split('w') if 'w' in uptime_str else [uptime_str]
        if len(parts) > 1:
            seconds += int(parts[0]) * 7 * 24 * 3600
            uptime_str = parts[1]
        
        parts = uptime_str.split('d') if 'd' in uptime_str else [uptime_str]
        if len(parts) > 1:
            seconds += int(parts[0]) * 24 * 3600
            uptime_str = parts[1]
        
        parts = uptime_str.split(':')
        if len(parts) == 3:
            seconds += int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            seconds += int(parts[0]) * 60 + int(parts[1])
        
        return seconds