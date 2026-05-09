from sqlalchemy import Column, String, Boolean, DateTime, JSON, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID, MACADDR, INET
from sqlalchemy.orm import relationship
from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin


class AccessPoint(BaseModel, OrganizationMixin, TimestampMixin):
    """
    Access Point model - simple, streamlined.
    """
    __tablename__ = 'access_points'
    
    # REQUIRED 
    router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    mac_address = Column(MACADDR, nullable=False, unique=True, index=True)
    ssid = Column(String(32), nullable=False)
    
    # LOCATION (User provides - critical for field techs)
    location = Column(String(255), nullable=False, index=True)
    
    # OPTIONAL (User can provide)
    ip_address = Column(INET, nullable=True)
    hotspot_server_id = Column(UUID(as_uuid=True), ForeignKey('hotspot_servers.id'), nullable=True)
    description = Column(String(500), nullable=True)  # Technician notes
    
    # AUTO-DETECTED / SYSTEM MANAGED
    channel = Column(Integer, nullable=True)
    frequency = Column(String(10), default='2.4GHz')
    encryption_type = Column(String(20), default='wpa2')
    status = Column(String(20), default='unknown', index=True)
    last_seen_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    
    # ADVANCED
    settings = Column(JSON, default=dict)
    
    # INDEXES
    __table_args__ = (
        Index('idx_ap_router_status', 'router_id', 'status'),
        Index('idx_ap_organization', 'organization_id'),
        Index('idx_ap_mac', 'mac_address'),
        Index('idx_ap_location', 'location'),
    )
    
    # RELATIONSHIPS
    router = relationship('Router', back_populates='access_points')
    hotspot_server = relationship('HotspotServer', back_populates='access_points')
    
    # METHODS
    
    def __repr__(self):
        return f'<AccessPoint {self.name}>'
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'organization_id': str(self.organization_id),
            'router_id': str(self.router_id),
            'hotspot_server_id': str(self.hotspot_server_id) if self.hotspot_server_id else None,
            'name': self.name,
            'mac_address': self.mac_address,
            'ip_address': str(self.ip_address) if self.ip_address else None,
            'ssid': self.ssid,
            'location': self.location,
            'description': self.description,
            'status': self.status,
            'is_active': self.is_active,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }