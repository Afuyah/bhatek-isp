from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, DECIMAL, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database.base import  BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin

class Plan(BaseModel, OrganizationMixin, TimestampMixin):
    """Internet service plans/packages"""
    __tablename__ = 'plans'
    
    name = Column(String(255), nullable=False)
    description = Column(Text)
    plan_type = Column(String(20), nullable=False)  # hotspot, pppoe, both
    billing_cycle = Column(String(20), default='monthly')  # daily, weekly, monthly, quarterly, yearly
    
    # Validity configuration
    validity_type = Column(String(20), nullable=False)  # time_based, data_based, unlimited
    validity_days = Column(Integer)  # for time-based plans
    data_limit_mb = Column(Integer)  # for data-based plans
    
    # Bandwidth limits (Mbps)
    bandwidth_up_mbps = Column(Integer, default=0)  # 0 = unlimited
    bandwidth_down_mbps = Column(Integer, default=0)  # 0 = unlimited
    
    # Pricing
    price = Column(DECIMAL(10, 2), nullable=False)
    setup_fee = Column(DECIMAL(10, 2), default=0)
    discount_percentage = Column(DECIMAL(5, 2), default=0)
    
    # Limits
    concurrent_logins = Column(Integer, default=1)
    device_limit = Column(Integer, default=1)
    session_timeout_seconds = Column(Integer)
    idle_timeout_seconds = Column(Integer)
    
    # Features
    auto_renew = Column(Boolean, default=False)
    is_unlimited = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True, index=True)
    is_public = Column(Boolean, default=True)
    
    # Additional
    features = Column(JSON, default=list)
    terms_and_conditions = Column(Text)
    sort_order = Column(Integer, default=0)
    
    # Relationships
    organization = relationship('Organization', back_populates='plans')
    subscriptions = relationship('Subscription', back_populates='plan', lazy='dynamic')
    vouchers = relationship('Voucher', back_populates='plan', lazy='dynamic')
    invoices = relationship('Invoice', back_populates='plan', lazy='dynamic')
    
    __table_args__ = (
        Index('idx_plans_org_active', 'organization_id', 'is_active'),
        Index('idx_plans_type', 'plan_type'),
    )
    
    def __repr__(self):
        return f'<Plan {self.name} - {self.price}>'
    
    def get_discounted_price(self) -> float:
        """Calculate discounted price"""
        if self.discount_percentage > 0:
            return float(self.price) * (1 - self.discount_percentage / 100)
        return float(self.price)
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'name': self.name,
            'description': self.description,
            'plan_type': self.plan_type,
            'billing_cycle': self.billing_cycle,
            'validity_type': self.validity_type,
            'validity_days': self.validity_days,
            'data_limit_mb': self.data_limit_mb,
            'bandwidth_up_mbps': self.bandwidth_up_mbps,
            'bandwidth_down_mbps': self.bandwidth_down_mbps,
            'price': float(self.price),
            'setup_fee': float(self.setup_fee),
            'discount_percentage': float(self.discount_percentage),
            'discounted_price': self.get_discounted_price(),
            'concurrent_logins': self.concurrent_logins,
            'device_limit': self.device_limit,
            'session_timeout_seconds': self.session_timeout_seconds,
            'idle_timeout_seconds': self.idle_timeout_seconds,
            'auto_renew': self.auto_renew,
            'is_unlimited': self.is_unlimited,
            'is_active': self.is_active,
            'is_public': self.is_public,
            'features': self.features,
            'sort_order': self.sort_order
        }


class Subscription(BaseModel, OrganizationMixin, TimestampMixin):
    """User subscriptions to plans """
    __tablename__ = 'subscriptions'
    
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id', ondelete='CASCADE'), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'), nullable=False)
    
    status = Column(String(20), default='active', index=True)  # active, expired, cancelled, suspended, pending
    start_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    expiry_time = Column(DateTime, nullable=False, index=True)
    cancelled_at = Column(DateTime)
    cancellation_reason = Column(String(255))
    
    # Override plan settings
    device_limit = Column(Integer)
    bandwidth_up_mbps = Column(Integer)
    bandwidth_down_mbps = Column(Integer)
    
    # Billing
    auto_renew = Column(Boolean, default=False)
    billing_cycle = Column(String(20))
    
    # Usage tracking
    total_data_used_mb = Column(DECIMAL(10, 2), default=0)
    last_reset_at = Column(DateTime)
    
    # Relationships
    subscriber = relationship('Subscriber', back_populates='subscriptions')
    plan = relationship('Plan', back_populates='subscriptions')
    active_sessions = relationship('ActiveSession', back_populates='subscription', lazy='dynamic')
    transactions = relationship('Transaction', back_populates='subscription', lazy='dynamic')
    invoices = relationship('Invoice', back_populates='subscription', lazy='dynamic')
    
    __table_args__ = (
        Index('idx_subscriptions_subscriber', 'subscriber_id', 'status'),
        Index('idx_subscriptions_expiry', 'expiry_time'),
        Index('idx_subscriptions_plan', 'plan_id'),
        Index('idx_subscriptions_org_status', 'organization_id', 'status'),
    )
    
    def is_active(self) -> bool:
        """Check if subscription is currently active"""
        return self.status == 'active' and self.expiry_time > datetime.utcnow()
    
    def days_remaining(self) -> int:
        """Get days remaining until expiry"""
        if self.expiry_time > datetime.utcnow():
            return (self.expiry_time - datetime.utcnow()).days
        return 0
    
    def get_bandwidth_up(self) -> int:
        """Get upload bandwidth limit"""
        return self.bandwidth_up_mbps or self.plan.bandwidth_up_mbps or 0
    
    def get_bandwidth_down(self) -> int:
        """Get download bandwidth limit"""
        return self.bandwidth_down_mbps or self.plan.bandwidth_down_mbps or 0
    
    def get_device_limit(self) -> int:
        """Get device limit"""
        return self.device_limit or self.plan.device_limit or 1
    
    def to_dict(self, include_plan=False):
        data = {
            'id': str(self.id),
            'subscriber_id': str(self.subscriber_id),
            'plan_id': str(self.plan_id),
            'status': self.status,
            'start_time': self.start_time.isoformat(),
            'expiry_time': self.expiry_time.isoformat(),
            'is_active': self.is_active(),
            'days_remaining': self.days_remaining(),
            'device_limit': self.get_device_limit(),
            'bandwidth_up_mbps': self.get_bandwidth_up(),
            'bandwidth_down_mbps': self.get_bandwidth_down(),
            'auto_renew': self.auto_renew,
            'total_data_used_mb': float(self.total_data_used_mb) if self.total_data_used_mb else 0
        }
        if include_plan and self.plan:
            data['plan'] = self.plan.to_dict()
        return data


class Invoice(BaseModel, OrganizationMixin, TimestampMixin):
    """Invoices for billing"""
    __tablename__ = 'invoices'
    
    invoice_number = Column(String(50), nullable=False, unique=True, index=True)
    invoice_type = Column(String(20), nullable=False)  # subscription, voucher_batch, setup_fee, renewal
    
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id'))
    subscription_id = Column(UUID(as_uuid=True), ForeignKey('subscriptions.id'))
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'))
    
    subtotal = Column(DECIMAL(10, 2), nullable=False)
    tax_amount = Column(DECIMAL(10, 2), default=0)
    tax_rate = Column(DECIMAL(5, 2), default=0)
    discount_amount = Column(DECIMAL(10, 2), default=0)
    total = Column(DECIMAL(10, 2), nullable=False)
    currency = Column(String(3), default='KES')
    
    issue_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    due_date = Column(DateTime, nullable=False)
    paid_at = Column(DateTime)
    
    status = Column(String(20), default='draft', index=True)  # draft, sent, paid, overdue, cancelled, void
    
    notes = Column(Text)
    terms = Column(Text)
    billing_period_start = Column(DateTime)
    billing_period_end = Column(DateTime)
    
    # Relationships
    organization = relationship('Organization')
    subscriber = relationship('Subscriber', back_populates='invoices')
    subscription = relationship('Subscription', back_populates='invoices')
    plan = relationship('Plan', back_populates='invoices')
    transactions = relationship('Transaction', back_populates='invoice', lazy='dynamic')
    invoice_items = relationship('InvoiceItem', back_populates='invoice', lazy='dynamic', cascade='all, delete-orphan')
    
    __table_args__ = (
        Index('idx_invoices_subscriber', 'subscriber_id', 'status'),
        Index('idx_invoices_due_date', 'due_date'),
        Index('idx_invoices_issue_date', 'issue_date'),
        Index('idx_invoices_number', 'invoice_number'),
    )
    
    def is_overdue(self) -> bool:
        """Check if invoice is overdue"""
        return self.status == 'sent' and self.due_date < datetime.utcnow()
    
    def to_dict(self, include_items=False):
        data = {
            'id': str(self.id),
            'invoice_number': self.invoice_number,
            'invoice_type': self.invoice_type,
            'subscriber_id': str(self.subscriber_id) if self.subscriber_id else None,
            'subtotal': float(self.subtotal),
            'tax_amount': float(self.tax_amount),
            'discount_amount': float(self.discount_amount),
            'total': float(self.total),
            'currency': self.currency,
            'issue_date': self.issue_date.isoformat(),
            'due_date': self.due_date.isoformat(),
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'status': self.status,
            'is_overdue': self.is_overdue(),
            'notes': self.notes
        }
        if include_items:
            data['items'] = [item.to_dict() for item in self.invoice_items]
        return data


class InvoiceItem(BaseModel):
    """Line items for invoices"""
    __tablename__ = 'invoice_items'
    
    invoice_id = Column(UUID(as_uuid=True), ForeignKey('invoices.id', ondelete='CASCADE'), nullable=False)
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(DECIMAL(10, 2), nullable=False)
    total = Column(DECIMAL(10, 2), nullable=False)
    Invoice_metadata = Column(JSON, default={})
    
    invoice = relationship('Invoice', back_populates='invoice_items')
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'description': self.description,
            'quantity': self.quantity,
            'unit_price': float(self.unit_price),
            'total': float(self.total)
        }


class Voucher(BaseModel, OrganizationMixin, TimestampMixin):
    """Prepaid vouchers for hotspot access"""
    __tablename__ = 'vouchers'
    
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'), nullable=False)
    batch_id = Column(UUID(as_uuid=True), ForeignKey('voucher_batches.id'))
    
    code = Column(String(50), nullable=False, unique=True, index=True)
    password_hash = Column(String(255))
    price_paid = Column(DECIMAL(10, 2))
    
    status = Column(String(20), default='active', index=True)  # active, used, expired, void
    usage_count = Column(Integer, default=0)
    max_uses = Column(Integer, default=1)
    
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    
    used_by_subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id'))
    used_at = Column(DateTime)
    used_on_router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id'))
    
    notes = Column(Text)
    
    # Relationships
    plan = relationship('Plan', back_populates='vouchers')
    batch = relationship('VoucherBatch', back_populates='vouchers')
    used_by_subscriber = relationship('Subscriber', back_populates='vouchers')
    
    __table_args__ = (
        Index('idx_vouchers_code', 'code'),
        Index('idx_vouchers_status_expiry', 'status', 'expires_at'),
        Index('idx_vouchers_batch', 'batch_id'),
    )
    
    def is_valid(self) -> bool:
        """Check if voucher is valid for use"""
        return self.status == 'active' and self.expires_at > datetime.utcnow() and self.usage_count < self.max_uses
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'code': self.code,
            'plan_id': str(self.plan_id),
            'plan_name': self.plan.name if self.plan else None,
            'price_paid': float(self.price_paid) if self.price_paid else None,
            'status': self.status,
            'usage_count': self.usage_count,
            'max_uses': self.max_uses,
            'expires_at': self.expires_at.isoformat(),
            'is_valid': self.is_valid()
        }


class VoucherBatch(BaseModel, OrganizationMixin, TimestampMixin):
    """Bulk voucher generation batches"""
    __tablename__ = 'voucher_batches'
    
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'), nullable=False)
    
    batch_name = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False)
    price_per_voucher = Column(DECIMAL(10, 2))
    total_amount = Column(DECIMAL(10, 2))
    
    status = Column(String(20), default='pending')  # pending, generated, partially_used, exhausted
    
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    created_at = Column(DateTime, default=datetime.utcnow)
    generated_at = Column(DateTime)
    
    expires_in_days = Column(Integer, default=30)
    
    # Relationships
    plan = relationship('Plan')
    vouchers = relationship('Voucher', back_populates='batch', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'batch_name': self.batch_name,
            'plan_id': str(self.plan_id),
            'plan_name': self.plan.name if self.plan else None,
            'quantity': self.quantity,
            'price_per_voucher': float(self.price_per_voucher) if self.price_per_voucher else None,
            'total_amount': float(self.total_amount) if self.total_amount else None,
            'status': self.status,
            'expires_in_days': self.expires_in_days,
            'generated_at': self.generated_at.isoformat() if self.generated_at else None
        }


class DiscountCoupon(BaseModel, OrganizationMixin, TimestampMixin):
    """Discount coupons for promotions"""
    __tablename__ = 'discount_coupons'
    
    code = Column(String(50), nullable=False, unique=True, index=True)
    description = Column(Text)
    
    discount_type = Column(String(20), nullable=False)  # percentage, fixed
    discount_value = Column(DECIMAL(10, 2), nullable=False)
    
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)
    
    usage_limit = Column(Integer)
    used_count = Column(Integer, default=0)
    minimum_purchase = Column(DECIMAL(10, 2), default=0)
    
    applicable_plan_ids = Column(JSON, default=list)  # Empty list means all plans
    is_active = Column(Boolean, default=True)
    
    __table_args__ = (
        Index('idx_coupons_code', 'code'),
        Index('idx_coupons_valid', 'valid_from', 'valid_to'),
    )
    
    def is_valid(self) -> bool:
        """Check if coupon is valid"""
        now = datetime.utcnow()
        return (self.is_active and 
                self.valid_from <= now <= self.valid_to and
                (self.usage_limit is None or self.used_count < self.usage_limit))
    
    def calculate_discount(self, amount: float) -> float:
        """Calculate discount amount"""
        if self.discount_type == 'percentage':
            return amount * (self.discount_value / 100)
        else:
            return min(float(self.discount_value), amount)
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'code': self.code,
            'description': self.description,
            'discount_type': self.discount_type,
            'discount_value': float(self.discount_value),
            'valid_from': self.valid_from.isoformat(),
            'valid_to': self.valid_to.isoformat(),
            'usage_limit': self.usage_limit,
            'used_count': self.used_count,
            'minimum_purchase': float(self.minimum_purchase),
            'is_valid': self.is_valid()
        }