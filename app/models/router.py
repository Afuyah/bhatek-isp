from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship
from app.core.database.base import  BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin

class Router(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'routers'
    
    network_id = Column(UUID(as_uuid=True), ForeignKey('networks.id'))
    name = Column(String(255), nullable=False)
    model = Column(String(100))
    firmware_version = Column(String(50))
    ip_address = Column(INET, nullable=False)
    api_port = Column(Integer, default=8728)
    api_ssl_port = Column(Integer, default=8729)
    username = Column(String(100), nullable=False)
    password_encrypted = Column(String, nullable=False)
    ssh_port = Column(Integer, default=22)
    ssh_key_encrypted = Column(String)
    description = Column(Text, nullable=True)
    location = Column(String(255))
    latitude = Column(String(10))
    longitude = Column(String(11))
    status = Column(String(20), default='unknown', index=True)
    last_seen_at = Column(DateTime)
    last_sync_at = Column(DateTime)
    connection_pool_size = Column(Integer, default=5)
    settings = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True, index=True)
    
    # Relationships
    organization = relationship('Organization', back_populates='routers')
    network = relationship('Network', back_populates='routers')
    hotspot_servers = relationship('HotspotServer', back_populates='router', lazy='dynamic')
    pppoe_servers = relationship('PPPoeServer', back_populates='router', lazy='dynamic')
    access_points = relationship('AccessPoint', back_populates='router', lazy='dynamic')
    
    def __repr__(self):
        return f'<Router {self.name} ({self.ip_address})>'

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
    
    # Relationships
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
    
    # Relationships
    router = relationship('Router', back_populates='pppoe_servers')