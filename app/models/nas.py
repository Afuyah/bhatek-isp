# app/models/nas.py
from sqlalchemy import Column, String, Integer, ForeignKey, Index, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin


class NAS(BaseModel, OrganizationMixin, TimestampMixin):
    """
    FreeRADIUS NAS (Network Access Server) table
    Stores RADIUS client configurations for MikroTik routers
    """
    __tablename__ = 'nas'
    
    # NAS Identifier
    nasname = Column(String(128), nullable=False, index=True)
    
    # Human-readable name
    shortname = Column(String(32), nullable=True)
    
    # NAS type
    type = Column(String(30), default='mikrotik')
    
    # Number of ports
    ports = Column(Integer, nullable=True)
    
    # RADIUS shared secret
    secret = Column(String(128), nullable=False)
    
    # Server identifier
    server = Column(String(64), nullable=True)
    
    # SNMP community string
    community = Column(String(50), nullable=True)
    
    # Description
    description = Column(Text, nullable=True)
    
    # Foreign key to Router table - NO relationship back to Router
    router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id', ondelete='SET NULL'), nullable=True, index=True)
    
    # Active status
    is_active = Column(Boolean, default=True, index=True)
    
    # Indexes
    __table_args__ = (
        Index('idx_nas_nasname', 'nasname'),
        Index('idx_nas_shortname', 'shortname'),
        Index('idx_nas_secret', 'secret'),
        Index('idx_nas_router', 'router_id'),
        Index('idx_nas_org', 'organization_id'),
        Index('idx_nas_active', 'is_active'),
        Index('idx_nas_type', 'type'),
    )
    
    # NO relationship back to Router - avoids circular reference
    
    def __repr__(self):
        return f'<NAS {self.shortname or self.nasname}>'
    
    def to_dict(self, include_secret=False):
        """Convert to dictionary for API responses"""
        data = {
            'id': str(self.id),
            'organization_id': str(self.organization_id),
            'nasname': self.nasname,
            'shortname': self.shortname,
            'type': self.type,
            'ports': self.ports,
            'server': self.server,
            'description': self.description,
            'router_id': str(self.router_id) if self.router_id else None,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        
        if include_secret:
            data['secret'] = self.secret
        
        return data