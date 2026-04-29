from sqlalchemy import Column, UUID, ForeignKey, Boolean, DateTime, func , String
from sqlalchemy.orm import declared_attr
import uuid

class OrganizationMixin:
    """Mixin to add organization isolation"""
    
    @declared_attr
    def organization_id(cls):
        return Column(
            UUID(as_uuid=True),
            ForeignKey('organizations.id', ondelete='CASCADE'),
            nullable=False,
            index=True
        )

class TenantMixin:
    """Alias for OrganizationMixin"""
    pass

class TimestampMixin:
    """Mixin for timestamp fields"""
    
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

class SoftDeleteMixin:
    """Mixin for soft delete functionality"""
    
    is_deleted = Column(Boolean, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), nullable=True)

class StatusMixin:
    """Mixin for status tracking"""
    
    status = Column(
        String(50),
        default='active',
        index=True
    )
    is_active = Column(Boolean, default=True, index=True)