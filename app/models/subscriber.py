from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, DECIMAL, Index
from sqlalchemy.dialects.postgresql import UUID, MACADDR
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database.base import  BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin

class Subscriber(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'subscribers'
    
    # ========================================================================
    # IDENTIFICATION
    # ========================================================================
    phone = Column(String(20), nullable=True)  # Now nullable for PPPoE users
    email = Column(String(255), nullable=True)
    
    # ========================================================================
    # SUBSCRIBER TYPE (CRITICAL FOR YOUR USE CASE)
    # ========================================================================
    subscriber_type = Column(String(20), nullable=False, default='hotspot', index=True)
    # Values: 'hotspot' - auto-created via M-Pesa, 'pppoe' - manually created by admin
    
    # For PPPoE users (admin creates)
    username = Column(String(100), nullable=True, unique=True, index=True)  # PPPoE login username
    password_encrypted = Column(String(255), nullable=True)  # Encrypted password for PPPoE
    
    # For all users
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    national_id = Column(String(20), nullable=True)
    address = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    
    # ========================================================================
    # STATUS & METRICS
    # ========================================================================
    status = Column(String(20), default='active', index=True)
    total_spent = Column(DECIMAL(10, 2), default=0)
    last_active_at = Column(DateTime, nullable=True)
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    organization = relationship('Organization', back_populates='subscribers')
    devices = relationship('Device', back_populates='subscriber', lazy='dynamic')
    subscriptions = relationship('Subscription', back_populates='subscriber', lazy='dynamic')
    active_sessions = relationship('ActiveSession', back_populates='subscriber', lazy='dynamic')
    vouchers = relationship('Voucher', back_populates='used_by_subscriber', lazy='dynamic')
    transactions = relationship('Transaction', back_populates='subscriber', lazy='dynamic')
    invoices = relationship('Invoice', back_populates='subscriber', lazy='dynamic')
    
    # ========================================================================
    # INDEXES
    # ========================================================================
    __table_args__ = (
        Index('idx_subscribers_org_phone', 'organization_id', 'phone', unique=True),
        Index('idx_subscribers_org_username', 'organization_id', 'username', unique=True),
        Index('idx_subscribers_type', 'subscriber_type'),
        Index('idx_subscribers_status', 'status'),
    )
    
    # ========================================================================
    # PROPERTIES
    # ========================================================================
    
    @property
    def is_hotspot_user(self) -> bool:
        return self.subscriber_type == 'hotspot'
    
    @property
    def is_pppoe_user(self) -> bool:
        return self.subscriber_type == 'pppoe'
    
    @property
    def display_name(self) -> str:
        """Return username or phone or email for display"""
        if self.username:
            return self.username
        if self.phone:
            return self.phone
        return self.email or str(self.id)
    
    @property
    def login_username(self) -> str:
        """Username for RADIUS authentication"""
        if self.subscriber_type == 'pppoe' and self.username:
            return self.username
        return self.phone or self.email or str(self.id)
    
    # ========================================================================
    # METHODS
    # ========================================================================
    
    def __repr__(self):
        return f'<Subscriber {self.display_name}>'
    
    def get_full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.display_name
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'organization_id': str(self.organization_id),
            'subscriber_type': self.subscriber_type,
            'phone': self.phone,
            'username': self.username,
            'email': self.email,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'national_id': self.national_id,
            'status': self.status,
            'total_spent': float(self.total_spent),
            'last_active_at': self.last_active_at.isoformat() if self.last_active_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Device(BaseModel, OrganizationMixin, TimestampMixin):
    __tablename__ = 'devices'
    
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id', ondelete='CASCADE'), nullable=False)
    mac_address = Column(MACADDR, nullable=False)
    device_name = Column(String(255), nullable=True)
    device_type = Column(String(50), nullable=True)  # phone, laptop, router, etc.
    is_primary = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    last_seen_at = Column(DateTime, nullable=True)
    
    # Relationships
    subscriber = relationship('Subscriber', back_populates='devices')
    
    __table_args__ = (
        Index('idx_devices_org_mac', 'organization_id', 'mac_address', unique=True),
        Index('idx_devices_subscriber', 'subscriber_id'),
    )
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'mac_address': self.mac_address,
            'device_name': self.device_name,
            'device_type': self.device_type,
            'is_primary': self.is_primary,
            'is_active': self.is_active,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None
        }