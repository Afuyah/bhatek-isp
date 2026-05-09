from sqlalchemy import Column, String, Boolean, JSON, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin

class Network(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'networks'
    
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    type = Column(String(50), nullable=False)  # hotspot, pppoe, hybrid
    description = Column(String)
    settings = Column(JSON, default={})
    is_active = Column(Boolean, default=True, index=True)
    
    # Relationships
    organization = relationship('Organization', back_populates='networks')
    routers = relationship('Router', back_populates='network', lazy='dynamic')
    
    __table_args__ = (
        Index('idx_networks_org_slug', 'organization_id', 'slug', unique=True),
        Index('idx_networks_type', 'type'),
        Index('idx_networks_active', 'is_active'),
    )
    
    def __repr__(self):
        return f'<Network {self.name}>'
    
    def to_dict(self, include_counts: bool = True):
        """Convert network to dictionary with optional router/AP counts"""
        data = {
            'id': str(self.id),
            'name': self.name,
            'slug': self.slug,
            'type': self.type,
            'description': self.description,
            'settings': self.settings,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        
        # Add counts if requested
        if include_counts:
            # Count routers associated with this network
            data['router_count'] = self.routers.count() if hasattr(self, 'routers') else 0
            # Placeholders for future modules
            data['ap_count'] = 0  # Will be updated when AP module is ready
            data['active_sessions'] = 0 
        
        return data