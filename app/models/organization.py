from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, DECIMAL, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database.base import BaseModel
from app.core.database.mixins import TimestampMixin

class Organization(BaseModel, TimestampMixin):
    __tablename__ = 'organizations'
    
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    business_type = Column(String(50), nullable=False)
    
    email = Column(String(255))
    phone = Column(String(20))
    address = Column(String)
    city = Column(String(100))
    country = Column(String(100))
    
    logo_url = Column(String)
    website = Column(String(255))
    
    timezone = Column(String(50), default='Africa/Nairobi')
    currency = Column(String(3), default='KES')
    
    subscription_tier = Column(String(50), default='basic')
    subscription_status = Column(String(50), default='active')
    subscription_expires_at = Column(DateTime)
    
    settings = Column(JSON, default=lambda: {})
    
    status = Column(String(20), default='active', index=True)

    # Relationships
    users = relationship(
        'User',
        foreign_keys='User.organization_id',
        back_populates='organization',
        lazy='dynamic'
    )
    
    organization_users = relationship(
        'OrganizationUser',
        back_populates='organization',
        lazy='dynamic',
        cascade='all, delete-orphan'
    )

    networks = relationship('Network', back_populates='organization', lazy='dynamic')
    routers = relationship('Router', back_populates='organization', lazy='dynamic')
    subscribers = relationship('Subscriber', back_populates='organization', lazy='dynamic')
    payment_accounts = relationship('PaymentAccount', back_populates='organization', lazy='dynamic')
    plans = relationship('Plan', back_populates='organization', lazy='dynamic')

    def __repr__(self):
        return f'<Organization {self.name}>'


class OrganizationUser(BaseModel):
    __tablename__ = 'organization_users'
    
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    
    role = Column(String(50), nullable=False)
    is_primary = Column(Boolean, default=False)
    joined_at = Column(DateTime, default=datetime.utcnow)
    
    invited_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))

    organization = relationship(
        'Organization',
        back_populates='organization_users'
    )

    user = relationship(
        'User',
        back_populates='organization_users',
        foreign_keys=[user_id]   
    )

    invited_by_user = relationship(
        'User',
        foreign_keys=[invited_by] 
    )

    __table_args__ = (
        Index('idx_org_user_unique', 'organization_id', 'user_id', unique=True),
    )


class OrganizationSetting(BaseModel):
    __tablename__ = 'organization_settings'
    
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    key = Column(String(100), nullable=False)
    value = Column(JSON)
    is_encrypted = Column(Boolean, default=False)
    
    __table_args__ = (
        Index('idx_org_setting_key', 'organization_id', 'key', unique=True),
    )



    