from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, DECIMAL, Text, Index, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
import enum

from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin


class ValidityUnit(str, enum.Enum):
    """Validity time units"""
    MINUTES = 'minutes'
    HOURS = 'hours'
    DAYS = 'days'
    MONTHS = 'months'
    YEARS = 'years'


class Plan(BaseModel, OrganizationMixin, TimestampMixin):
    """Internet service plans/packages with dynamic validity periods"""
    __tablename__ = 'plans'
    
    # ========================================================================
    # BASIC INFORMATION
    # ========================================================================
    name = Column(String(255), nullable=False)
    description = Column(Text)
    plan_type = Column(String(20), nullable=False)  # hotspot, pppoe, both
    billing_cycle = Column(String(20), default='one_time')  # one_time, daily, weekly, monthly, quarterly, yearly
    
    # ========================================================================
    # DYNAMIC VALIDITY CONFIGURATION
    # ========================================================================
    validity_type = Column(String(20), nullable=False)  # time_based, data_based, unlimited
    
    # Time-based validity (supports minutes, hours, days, months, years)
    validity_value = Column(Integer, nullable=True)  # e.g., 30, 2, 1
    validity_unit = Column(Enum(ValidityUnit), nullable=True)  # minutes, hours, days, months, years
    
    # Data-based validity
    data_limit_mb = Column(Integer, nullable=True)  # For data-based plans
    
    # Helper property to get validity as timedelta (for time-based plans)
    @property
    def validity_timedelta(self) -> timedelta:
        if self.validity_type != 'time_based' or not self.validity_value or not self.validity_unit:
            return timedelta(days=30)  # Default 30 days
        
        unit = self.validity_unit
        value = self.validity_value
        
        if unit == ValidityUnit.MINUTES:
            return timedelta(minutes=value)
        elif unit == ValidityUnit.HOURS:
            return timedelta(hours=value)
        elif unit == ValidityUnit.DAYS:
            return timedelta(days=value)
        elif unit == ValidityUnit.MONTHS:
            return timedelta(days=value * 30)  # Approximate
        elif unit == ValidityUnit.YEARS:
            return timedelta(days=value * 365)  # Approximate
        
        return timedelta(days=30)
    
    @property
    def validity_display(self) -> str:
        """Human-readable validity display"""
        if self.validity_type == 'unlimited':
            return 'Unlimited'
        elif self.validity_type == 'data_based':
            if self.data_limit_mb >= 1024:
                return f'{self.data_limit_mb / 1024:.0f} GB'
            return f'{self.data_limit_mb} MB'
        else:  # time_based
            if not self.validity_value or not self.validity_unit:
                return 'N/A'
            unit_display = {
                ValidityUnit.MINUTES: 'minute(s)',
                ValidityUnit.HOURS: 'hour(s)',
                ValidityUnit.DAYS: 'day(s)',
                ValidityUnit.MONTHS: 'month(s)',
                ValidityUnit.YEARS: 'year(s)'
            }
            return f'{self.validity_value} {unit_display.get(self.validity_unit, "days")}'
    
    # ========================================================================
    # BANDWIDTH LIMITS (Mbps)
    # ========================================================================
    bandwidth_up_mbps = Column(Integer, default=0)  # 0 = unlimited
    bandwidth_down_mbps = Column(Integer, default=0)  # 0 = unlimited
    
    # ========================================================================
    # PRICING
    # ========================================================================
    price = Column(DECIMAL(10, 2), nullable=False)
    setup_fee = Column(DECIMAL(10, 2), default=0)
    discount_percentage = Column(DECIMAL(5, 2), default=0)
    
    # ========================================================================
    # LIMITS
    # ========================================================================
    concurrent_logins = Column(Integer, default=1)
    device_limit = Column(Integer, default=1)
    session_timeout_seconds = Column(Integer, nullable=True)
    idle_timeout_seconds = Column(Integer, nullable=True)
    
    # ========================================================================
    # FEATURES
    # ========================================================================
    auto_renew = Column(Boolean, default=False)
    is_unlimited = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True, index=True)
    is_public = Column(Boolean, default=True)
    
    # ========================================================================
    # ADDITIONAL
    # ========================================================================
    features = Column(JSON, default=list)
    terms_and_conditions = Column(Text)
    sort_order = Column(Integer, default=0)
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    organization = relationship('Organization', back_populates='plans')
    subscriptions = relationship('Subscription', back_populates='plan', lazy='dynamic')
    vouchers = relationship('Voucher', back_populates='plan', lazy='dynamic')
    invoices = relationship('Invoice', back_populates='plan', lazy='dynamic')
    
    # ========================================================================
    # INDEXES
    # ========================================================================
    __table_args__ = (
        Index('idx_plans_org_active', 'organization_id', 'is_active'),
        Index('idx_plans_type', 'plan_type'),
    )
    
    # ========================================================================
    # METHODS
    # ========================================================================
    
    def __repr__(self):
        return f'<Plan {self.name} - {self.price}>'
    
    def get_discounted_price(self) -> float:
        """Calculate discounted price"""
        if self.discount_percentage > 0:
            return float(self.price) * (1 - self.discount_percentage / 100)
        return float(self.price)
    
    def calculate_expiry(self, start_time: datetime = None) -> datetime:
        """Calculate expiry time based on plan validity"""
        if start_time is None:
            start_time = datetime.utcnow()
        
        if self.validity_type == 'unlimited':
            return start_time + timedelta(days=365 * 10)  # 10 years effectively unlimited
        
        if self.validity_type == 'time_based':
            return start_time + self.validity_timedelta
        
        # For data-based, return start time + 1 year
        return start_time + timedelta(days=365)
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'name': self.name,
            'description': self.description,
            'plan_type': self.plan_type,
            'billing_cycle': self.billing_cycle,
            'validity_type': self.validity_type,
            'validity_value': self.validity_value,
            'validity_unit': self.validity_unit.value if self.validity_unit else None,
            'validity_display': self.validity_display,
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
    """User subscriptions to plans"""
    __tablename__ = 'subscriptions'
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id', ondelete='CASCADE'), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'), nullable=False)
    
    # ========================================================================
    # STATUS & TIME
    # ========================================================================
    status = Column(String(20), default='active', index=True)  # active, expired, cancelled, suspended, pending
    start_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    expiry_time = Column(DateTime, nullable=False, index=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancellation_reason = Column(String(255), nullable=True)
    
    # ========================================================================
    # OVERRIDE PLAN SETTINGS
    # ========================================================================
    device_limit = Column(Integer, nullable=True)
    bandwidth_up_mbps = Column(Integer, nullable=True)
    bandwidth_down_mbps = Column(Integer, nullable=True)
    
    # ========================================================================
    # BILLING
    # ========================================================================
    auto_renew = Column(Boolean, default=False)
    billing_cycle = Column(String(20), nullable=True)
    
    # ========================================================================
    # USAGE TRACKING
    # ========================================================================
    total_data_used_mb = Column(DECIMAL(10, 2), default=0)
    last_reset_at = Column(DateTime, nullable=True)
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    subscriber = relationship('Subscriber', back_populates='subscriptions')
    plan = relationship('Plan', back_populates='subscriptions')
    active_sessions = relationship('ActiveSession', back_populates='subscription', lazy='dynamic')
    transactions = relationship('Transaction', back_populates='subscription', lazy='dynamic')
    invoices = relationship('Invoice', back_populates='subscription', lazy='dynamic')
    
    # ========================================================================
    # INDEXES
    # ========================================================================
    __table_args__ = (
        Index('idx_subscriptions_subscriber', 'subscriber_id', 'status'),
        Index('idx_subscriptions_expiry', 'expiry_time'),
        Index('idx_subscriptions_plan', 'plan_id'),
        Index('idx_subscriptions_org_status', 'organization_id', 'status'),
    )
    
    # ========================================================================
    # METHODS
    # ========================================================================
    
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
    
    # ========================================================================
    # BASIC INFORMATION
    # ========================================================================
    invoice_number = Column(String(50), nullable=False, unique=True, index=True)
    invoice_type = Column(String(20), nullable=False)  # subscription, voucher_batch, setup_fee, renewal
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id'), nullable=True)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey('subscriptions.id'), nullable=True)
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'), nullable=True)
    
    # ========================================================================
    # FINANCIALS
    # ========================================================================
    subtotal = Column(DECIMAL(10, 2), nullable=False)
    tax_amount = Column(DECIMAL(10, 2), default=0)
    tax_rate = Column(DECIMAL(5, 2), default=0)
    discount_amount = Column(DECIMAL(10, 2), default=0)
    total = Column(DECIMAL(10, 2), nullable=False)
    currency = Column(String(3), default='KES')
    
    # ========================================================================
    # DATES
    # ========================================================================
    issue_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    due_date = Column(DateTime, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    
    # ========================================================================
    # STATUS
    # ========================================================================
    status = Column(String(20), default='draft', index=True)  # draft, sent, paid, overdue, cancelled, void
    
    # ========================================================================
    # ADDITIONAL
    # ========================================================================
    notes = Column(Text, nullable=True)
    terms = Column(Text, nullable=True)
    billing_period_start = Column(DateTime, nullable=True)
    billing_period_end = Column(DateTime, nullable=True)
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    organization = relationship('Organization')
    subscriber = relationship('Subscriber', back_populates='invoices')
    subscription = relationship('Subscription', back_populates='invoices')
    plan = relationship('Plan', back_populates='invoices')
    transactions = relationship('Transaction', back_populates='invoice', lazy='dynamic')
    invoice_items = relationship('InvoiceItem', back_populates='invoice', lazy='dynamic', cascade='all, delete-orphan')
    
    # ========================================================================
    # INDEXES
    # ========================================================================
    __table_args__ = (
        Index('idx_invoices_subscriber', 'subscriber_id', 'status'),
        Index('idx_invoices_due_date', 'due_date'),
        Index('idx_invoices_issue_date', 'issue_date'),
        Index('idx_invoices_number', 'invoice_number'),
    )
    
    # ========================================================================
    # METHODS
    # ========================================================================
    
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
    invoice_metadata = Column(JSON, default={})
    
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
    """Prepaid vouchers with dynamic validity"""
    __tablename__ = 'vouchers'
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'), nullable=False)
    batch_id = Column(UUID(as_uuid=True), ForeignKey('voucher_batches.id'), nullable=True)
    
    # ========================================================================
    # VOUCHER IDENTIFIERS
    # ========================================================================
    code = Column(String(50), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=True)
    price_paid = Column(DECIMAL(10, 2), nullable=True)
    
    # ========================================================================
    # DYNAMIC VALIDITY (can override plan validity)
    # ========================================================================
    validity_value = Column(Integer, nullable=True)  # Override plan validity
    validity_unit = Column(Enum(ValidityUnit), nullable=True)  # minutes, hours, days, months, years
    validity_type = Column(String(20), default='time_based')  # time_based, data_based
    
    # If data-based voucher
    data_limit_mb = Column(Integer, nullable=True)
    
    # ========================================================================
    # ACTIVATION SETTINGS
    # ========================================================================
    activation_type = Column(String(20), default='immediate')  # immediate, first_use, scheduled
    scheduled_activation_at = Column(DateTime, nullable=True)
    activated_at = Column(DateTime, nullable=True)
    
    # ========================================================================
    # STATUS & USAGE
    # ========================================================================
    status = Column(String(20), default='active', index=True)  # active, used, expired, void
    usage_count = Column(Integer, default=0)
    max_uses = Column(Integer, default=1)
    
    # ========================================================================
    # CREATION & EXPIRY
    # ========================================================================
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    
    # ========================================================================
    # USAGE INFO
    # ========================================================================
    used_by_subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id'), nullable=True)
    used_at = Column(DateTime, nullable=True)
    used_on_router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id'), nullable=True)
    
    # ========================================================================
    # ADDITIONAL
    # ========================================================================
    notes = Column(Text, nullable=True)
    
    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================
    plan = relationship('Plan', back_populates='vouchers')
    batch = relationship('VoucherBatch', back_populates='vouchers')
    used_by_subscriber = relationship('Subscriber', back_populates='vouchers')
    created_by_user = relationship('User', foreign_keys=[created_by])
    
    # ========================================================================
    # INDEXES
    # ========================================================================
    __table_args__ = (
        Index('idx_vouchers_code', 'code'),
        Index('idx_vouchers_status_expiry', 'status', 'expires_at'),
        Index('idx_vouchers_batch', 'batch_id'),
    )
    
    # ========================================================================
    # PROPERTIES
    # ========================================================================
    
    @property
    def validity_display(self) -> str:
        """Human-readable validity display"""
        if self.validity_value and self.validity_unit:
            unit_display = {
                ValidityUnit.MINUTES: 'minute(s)',
                ValidityUnit.HOURS: 'hour(s)',
                ValidityUnit.DAYS: 'day(s)',
                ValidityUnit.MONTHS: 'month(s)',
                ValidityUnit.YEARS: 'year(s)'
            }
            return f'{self.validity_value} {unit_display.get(self.validity_unit, "days")}'
        return self.plan.validity_display if self.plan else 'N/A'
    
    # ========================================================================
    # METHODS
    # ========================================================================
    
    def calculate_expiry_from_activation(self, activation_time: datetime = None) -> datetime:
        """Calculate expiry time based on activation"""
        if activation_time is None:
            activation_time = datetime.utcnow()
        
        if self.validity_value and self.validity_unit:
            unit = self.validity_unit
            value = self.validity_value
            
            if unit == ValidityUnit.MINUTES:
                return activation_time + timedelta(minutes=value)
            elif unit == ValidityUnit.HOURS:
                return activation_time + timedelta(hours=value)
            elif unit == ValidityUnit.DAYS:
                return activation_time + timedelta(days=value)
            elif unit == ValidityUnit.MONTHS:
                return activation_time + timedelta(days=value * 30)
            elif unit == ValidityUnit.YEARS:
                return activation_time + timedelta(days=value * 365)
        
        # Fallback to plan validity
        return self.plan.calculate_expiry(activation_time) if self.plan else activation_time + timedelta(days=30)
    
    def is_valid(self) -> bool:
        """Check if voucher is valid for use"""
        if self.status != 'active':
            return False
        if self.expires_at and self.expires_at <= datetime.utcnow():
            return False
        if self.usage_count >= self.max_uses:
            return False
        return True
    
    def can_activate(self) -> bool:
        """Check if voucher can be activated"""
        if not self.is_valid():
            return False
        if self.activated_at and self.used_at:
            return False
        return True
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'code': self.code,
            'plan_id': str(self.plan_id),
            'plan_name': self.plan.name if self.plan else None,
            'price_paid': float(self.price_paid) if self.price_paid else None,
            'validity_value': self.validity_value,
            'validity_unit': self.validity_unit.value if self.validity_unit else None,
            'validity_display': self.validity_display,
            'activation_type': self.activation_type,
            'status': self.status,
            'usage_count': self.usage_count,
            'max_uses': self.max_uses,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'is_valid': self.is_valid()
        }


class VoucherBatch(BaseModel, OrganizationMixin, TimestampMixin):
    """Bulk voucher generation batches"""
    __tablename__ = 'voucher_batches'
    
    plan_id = Column(UUID(as_uuid=True), ForeignKey('plans.id'), nullable=False)
    
    batch_name = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False)
    price_per_voucher = Column(DECIMAL(10, 2), nullable=True)
    total_amount = Column(DECIMAL(10, 2), nullable=True)
    
    status = Column(String(20), default='pending')  # pending, generated, partially_used, exhausted
    
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    generated_at = Column(DateTime, nullable=True)
    
    # Batch validity settings (applied to all vouchers in batch)
    validity_value = Column(Integer, nullable=True)
    validity_unit = Column(Enum(ValidityUnit), nullable=True)
    expires_in_days = Column(Integer, default=30)  # Legacy - kept for backward compatibility
    
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
            'validity_value': self.validity_value,
            'validity_unit': self.validity_unit.value if self.validity_unit else None,
            'expires_in_days': self.expires_in_days,
            'generated_at': self.generated_at.isoformat() if self.generated_at else None
        }


class DiscountCoupon(BaseModel, OrganizationMixin, TimestampMixin):
    """Discount coupons for promotions"""
    __tablename__ = 'discount_coupons'
    
    code = Column(String(50), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    
    discount_type = Column(String(20), nullable=False)  # percentage, fixed
    discount_value = Column(DECIMAL(10, 2), nullable=False)
    
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)
    
    usage_limit = Column(Integer, nullable=True)
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