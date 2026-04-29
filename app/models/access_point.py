from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, MACADDR, INET
from sqlalchemy.orm import relationship
from app.core.database.base import  BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin

class AccessPoint(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'access_points'
    
    router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id'))
    hotspot_server_id = Column(UUID(as_uuid=True), ForeignKey('hotspot_servers.id'))
    name = Column(String(255), nullable=False)
    mac_address = Column(MACADDR, nullable=False, unique=True, index=True)
    ip_address = Column(INET)
    ssid = Column(String(32), nullable=False)
    ssid_visibility = Column(Boolean, default=True)
    encryption_type = Column(String(20), default='wpa2')
    encryption_key_encrypted = Column(String)
    channel = Column(Integer)
    frequency = Column(String(10), default='2.4ghz')
    location = Column(String(255))
    latitude = Column(String(10))
    longitude = Column(String(11))
    status = Column(String(20), default='unknown', index=True)
    last_seen_at = Column(DateTime)
    settings = Column(JSON, default={})
    is_active = Column(Boolean, default=True, index=True)
    
    # Relationships
    router = relationship('Router', back_populates='access_points')
    hotspot_server = relationship('HotspotServer', back_populates='access_points')
    active_sessions = relationship('ActiveSession', back_populates='access_point', lazy='dynamic')
    
    def __repr__(self):
        return f'<AccessPoint {self.name} ({self.ssid})>'