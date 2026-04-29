from sqlalchemy import Column, String, Boolean, DateTime, Integer, JSON, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship
from datetime import datetime
import bcrypt

from app.core.database.base import BaseModel
from app.core.database.mixins import TimestampMixin

class User(BaseModel, TimestampMixin):
    __tablename__ = 'users'
    
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    
    first_name = Column(String(100))
    last_name = Column(String(100))
    
    # Add organization_id - this is needed for direct organization relationship
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'), nullable=True, index=True)
    
    role = Column(String(50), nullable=False, default='user')
    permissions = Column(JSON, default=lambda: [])
    
    is_active = Column(Boolean, default=True, index=True)
    is_super_admin = Column(Boolean, default=False)
    
    last_login_at = Column(DateTime)
    login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime)
    
    # Self-reference
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))

   
    creator = relationship('User',   remote_side='User.id',   foreign_keys=[created_by],  backref='created_users' )
    
    # Direct organization relationship (optional, for convenience)
    organization = relationship('Organization',  foreign_keys=[organization_id], back_populates='users' )

    organization_users = relationship('OrganizationUser', back_populates='user', foreign_keys='OrganizationUser.user_id', cascade='all, delete-orphan' )

    refresh_tokens = relationship('RefreshToken',  back_populates='user',  cascade='all, delete-orphan'   )

    audit_logs = relationship( 'AuditLog', back_populates='user'  )

    
    def set_password(self, password: str):
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(
            password.encode('utf-8'),
            self.password_hash.encode('utf-8')
        )

    def has_permission(self, permission: str) -> bool:
        return (
            permission in self.permissions or
            'admin' in self.permissions or
            self.is_super_admin
        )
    
    def get_organizations(self):
        """Get all organizations this user belongs to"""
        return [org_user.organization for org_user in self.organization_users]
    
    def get_current_organization(self):
        """Get the primary organization for this user"""
        primary_org_user = next(
            (ou for ou in self.organization_users if ou.is_primary), 
            None
        )
        return primary_org_user.organization if primary_org_user else None
    
    def to_dict(self, exclude=None, include_organizations=False):
        # Initialize exclude set
        if exclude is None:
            exclude = set()
        else:
            exclude = set(exclude)
        
        # Always exclude password_hash
        exclude.add('password_hash')
        
        # Build base data
        data = {
            'id': str(self.id),
            'email': self.email,
            'phone': self.phone,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'role': self.role,
            'is_active': self.is_active,
            'is_super_admin': self.is_super_admin,
            'organization_id': str(self.organization_id) if self.organization_id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
        }
        
        # Remove excluded fields
        for field in exclude:
            data.pop(field, None)
        
        # Add organizations if requested
        if include_organizations:
            data['organizations'] = [
                {
                    'id': str(ou.organization.id), 
                    'name': ou.organization.name, 
                    'role': ou.role,
                    'is_primary': ou.is_primary
                }
                for ou in self.organization_users
            ]
        
        return data

class RefreshToken(BaseModel):
    __tablename__ = 'refresh_tokens'
    
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token = Column(String(500), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False)
    revoked_at = Column(DateTime)
    user_agent = Column(Text)
    ip_address = Column(INET)
    
    user = relationship('User', back_populates='refresh_tokens')

class AuditLog(BaseModel):
    __tablename__ = 'audit_logs'
    
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    organization_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id'))
    subscriber_id = Column(UUID(as_uuid=True))
    
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50))
    resource_id = Column(String(100))
    details = Column(JSON)
    changes = Column(JSON)
    ip_address = Column(INET)
    user_agent = Column(Text)
    request_id = Column(UUID(as_uuid=True))
    status = Column(String(20))
    error_message = Column(Text)
    
    user = relationship('User', back_populates='audit_logs')
    
    __table_args__ = (
        Index('idx_audit_created', 'created_at'),
        Index('idx_audit_user_action', 'user_id', 'action'),
    )