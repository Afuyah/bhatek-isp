from sqlalchemy import Column, String, Boolean, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database.base import  BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin

class Network(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'networks'
    
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # hotspot, pppoe, both
    description = Column(String)
    settings = Column(JSON, default={})
    is_active = Column(Boolean, default=True, index=True)
    
    # Relationships
    organization = relationship('Organization', back_populates='networks')
    routers = relationship('Router', back_populates='network', lazy='dynamic')
    
    def __repr__(self):
        return f'<Network {self.name}>'