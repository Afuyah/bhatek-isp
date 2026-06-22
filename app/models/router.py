# app/models/router.py
from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship
from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin


class Router(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'routers'
    
    # Basic Information
    network_id = Column(UUID(as_uuid=True), ForeignKey('networks.id'))
    name = Column(String(255), nullable=False)
    model = Column(String(100))
    firmware_version = Column(String(50))
    
    # Connection Settings
    ip_address = Column(INET, nullable=False)          
    local_ip = Column(String(45), nullable=True)       
    api_port = Column(Integer, default=8728)
    api_ssl_port = Column(Integer, default=8729)
    username = Column(String(100), nullable=False)
    password_encrypted = Column(String, nullable=False)
    
    # SSH Settings
    ssh_port = Column(Integer, default=22)
    ssh_key_encrypted = Column(String)
    
    # Location & Description
    description = Column(Text, nullable=True)
    location = Column(String(255))
    latitude = Column(String(10))
    longitude = Column(String(11))
    
    # Status & Monitoring
    status = Column(String(20), default='unknown', index=True)
    last_seen_at = Column(DateTime)
    last_sync_at = Column(DateTime)
    connection_pool_size = Column(Integer, default=5)
    settings = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True, index=True)
    
    # WireGuard Integration Fields
    wireguard_ip = Column(String(45), nullable=True, unique=True, index=True)
    wireguard_public_key = Column(String(255), nullable=True)
    wireguard_private_key_encrypted = Column(String(500), nullable=True)
    
    # RADIUS Integration Fields
    radius_secret = Column(String(255), nullable=True, unique=True, index=True)
    radius_configured_at = Column(DateTime, nullable=True)
    radius_config_status = Column(String(20), default='pending', index=True)
    auto_config_attempts = Column(Integer, default=0)
    last_config_error = Column(Text, nullable=True)
    
    # Foreign key to NAS table
    nas_entry_id = Column(UUID(as_uuid=True), ForeignKey('nas.id', ondelete='SET NULL'), nullable=True, index=True)
    
    # Relationships
    organization = relationship('Organization', back_populates='routers')
    network = relationship('Network', back_populates='routers')
    hotspot_servers = relationship('HotspotServer', back_populates='router', lazy='dynamic')
    pppoe_servers = relationship('PPPoeServer', back_populates='router', lazy='dynamic')
    access_points = relationship('AccessPoint', back_populates='router', lazy='dynamic')
    nas_entry = relationship('NAS', foreign_keys=[nas_entry_id], uselist=False)
    
    def __repr__(self):
        return f'<Router {self.name} ({self.ip_address})>'
    
    def to_dict(self, include_sensitive: bool = False):
        """Convert router to dictionary."""
        data = {
            'id': str(self.id),
            'organization_id': str(self.organization_id),
            'network_id': str(self.network_id) if self.network_id else None,
            'name': self.name,
            'model': self.model,
            'firmware_version': self.firmware_version,
            'ip_address': str(self.ip_address) if self.ip_address else None,
            'local_ip': self.local_ip,
            'api_port': self.api_port,
            'username': self.username,
            'ssh_port': self.ssh_port,
            'description': self.description,
            'location': self.location,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'status': self.status,
            'is_active': self.is_active,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'last_sync_at': self.last_sync_at.isoformat() if self.last_sync_at else None,
            'radius_config_status': self.radius_config_status,
            'radius_configured_at': self.radius_configured_at.isoformat() if self.radius_configured_at else None,
            'auto_config_attempts': self.auto_config_attempts or 0,
            'last_config_error': self.last_config_error,
            'nas_entry_id': str(self.nas_entry_id) if self.nas_entry_id else None,
            'wireguard_ip': self.wireguard_ip,
            'wireguard_public_key': self.wireguard_public_key,
            'settings': self.settings or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        
        if include_sensitive:
            data['radius_secret'] = self.radius_secret
        
        return data


class HotspotServer(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'hotspot_servers'
    
    router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(255), nullable=False)
    hotspot_id = Column(String(50), nullable=False)
    interface = Column(String(50))
    address_pool = Column(String(50))
    dns_name = Column(String(255))
    ssl_certificate = Column(String)
    ssl_key_encrypted = Column(String)
    login_page_theme = Column(JSON, default=dict)
    authentication_methods = Column(JSON, default=lambda: ['voucher', 'phone'])
    idle_timeout = Column(Integer, default=300)
    session_timeout = Column(Integer, default=86400)
    keepalive_timeout = Column(Integer, default=120)
    is_active = Column(Boolean, default=True, index=True)
    
    router = relationship('Router', back_populates='hotspot_servers')
    access_points = relationship('AccessPoint', back_populates='hotspot_server', lazy='dynamic')


class PPPoeServer(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'pppoe_servers'
    
    router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(255), nullable=False)
    interface = Column(String(50))
    service_name = Column(String(100))
    mtu = Column(Integer, default=1492)
    max_sessions = Column(Integer, default=100)
    authentication_protocols = Column(JSON, default=['chap', 'mschapv2'])
    is_active = Column(Boolean, default=True, index=True)
    
    router = relationship('Router', back_populates='pppoe_servers')