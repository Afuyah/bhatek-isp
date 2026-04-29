from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, DECIMAL, Index
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database.base import  BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin

class PaymentAccount(BaseModel, OrganizationMixin, TimestampMixin):
    """Payment gateway configuration per organization"""
    __tablename__ = 'payment_accounts'
    
    account_name = Column(String(255), nullable=False)
    account_type = Column(String(20), nullable=False)  # paybill, till_number
    shortcode = Column(String(10), nullable=False)
    consumer_key_encrypted = Column(String, nullable=False)
    consumer_secret_encrypted = Column(String, nullable=False)
    passkey_encrypted = Column(String, nullable=False)
    environment = Column(String(20), default='sandbox')
    callback_url = Column(String)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    last_test_at = Column(DateTime)
    test_status = Column(Boolean)
    
    # Relationships
    organization = relationship('Organization', back_populates='payment_accounts')
    transactions = relationship('Transaction', back_populates='payment_account', lazy='dynamic')
    
    __table_args__ = (
        Index('idx_payment_accounts_org', 'organization_id'),
        Index('idx_payment_accounts_shortcode', 'shortcode'),
        Index('idx_payment_accounts_default', 'organization_id', 'is_default'),
    )
    
    def to_dict(self, include_secrets=False):
        data = {
            'id': str(self.id),
            'account_name': self.account_name,
            'account_type': self.account_type,
            'shortcode': self.shortcode,
            'environment': self.environment,
            'callback_url': self.callback_url,
            'is_default': self.is_default,
            'is_active': self.is_active,
            'last_test_at': self.last_test_at.isoformat() if self.last_test_at else None,
            'test_status': self.test_status
        }
        if include_secrets:
            data['has_credentials'] = bool(self.consumer_key_encrypted)
        return data


class Transaction(BaseModel, OrganizationMixin):
    """Payment transactions - SINGLE SOURCE OF TRUTH"""
    __tablename__ = 'transactions'
    
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id'))
    subscription_id = Column(UUID(as_uuid=True), ForeignKey('subscriptions.id'))
    invoice_id = Column(UUID(as_uuid=True), ForeignKey('invoices.id'))
    payment_account_id = Column(UUID(as_uuid=True), ForeignKey('payment_accounts.id'))
    
    transaction_reference = Column(String(255), nullable=False, unique=True, index=True)
    mpesa_receipt = Column(String(50), index=True)
    checkout_request_id = Column(String(100), index=True)  # M-Pesa checkout request ID
    
    amount = Column(DECIMAL(10, 2), nullable=False)
    currency = Column(String(3), default='KES')
    
    status = Column(String(20), nullable=False, index=True)  # pending, success, failed, refunded, cancelled
    payment_method = Column(String(20), nullable=False)  # mpesa, cash, bank_transfer, card
    
    # Payment details
    payment_details = Column(JSON, default={})
    ip_address = Column(INET)
    user_agent = Column(String)
    callback_payload = Column(JSON)  # Raw webhook data
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime)
    
    # Custom data (renamed from 'metadata' to avoid SQLAlchemy conflict)
    custom_data = Column(JSON, default={})
    failure_reason = Column(String(255))
    
    # Relationships
    organization = relationship('Organization')
    subscriber = relationship('Subscriber', back_populates='transactions')
    subscription = relationship('Subscription', back_populates='transactions')
    invoice = relationship('Invoice', back_populates='transactions')
    payment_account = relationship('PaymentAccount', back_populates='transactions')
    
    __table_args__ = (
        Index('idx_transactions_subscriber', 'subscriber_id', 'status'),
        Index('idx_transactions_subscription', 'subscription_id'),
        Index('idx_transactions_invoice', 'invoice_id'),
        Index('idx_transactions_created', 'created_at'),
        Index('idx_transactions_mpesa_receipt', 'mpesa_receipt'),
        Index('idx_transactions_checkout', 'checkout_request_id'),
    )
    
    def is_success(self) -> bool:
        """Check if transaction was successful"""
        return self.status == 'success'
    
    def is_pending(self) -> bool:
        """Check if transaction is pending"""
        return self.status == 'pending'
    
    def mark_success(self, mpesa_receipt: str = None, completed_at: datetime = None):
        """Mark transaction as successful"""
        self.status = 'success'
        if mpesa_receipt:
            self.mpesa_receipt = mpesa_receipt
        self.completed_at = completed_at or datetime.utcnow()
    
    def mark_failed(self, reason: str = None):
        """Mark transaction as failed"""
        self.status = 'failed'
        self.failure_reason = reason
    
    def mark_refunded(self):
        """Mark transaction as refunded"""
        self.status = 'refunded'
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'transaction_reference': self.transaction_reference,
            'mpesa_receipt': self.mpesa_receipt,
            'checkout_request_id': self.checkout_request_id,
            'amount': float(self.amount),
            'currency': self.currency,
            'status': self.status,
            'payment_method': self.payment_method,
            'subscriber_id': str(self.subscriber_id) if self.subscriber_id else None,
            'subscription_id': str(self.subscription_id) if self.subscription_id else None,
            'invoice_id': str(self.invoice_id) if self.invoice_id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'failure_reason': self.failure_reason,
            'custom_data': self.custom_data
        }


class Refund(BaseModel, OrganizationMixin, TimestampMixin):
    """Refund records for transactions"""
    __tablename__ = 'refunds'
    
    transaction_id = Column(UUID(as_uuid=True), ForeignKey('transactions.id'), nullable=False)
    refund_reference = Column(String(100), nullable=False, unique=True, index=True)
    amount = Column(DECIMAL(10, 2), nullable=False)
    reason = Column(String(255))
    status = Column(String(20), default='pending')  # pending, completed, failed
    mpesa_refund_receipt = Column(String(50))
    processed_at = Column(DateTime)
    processed_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    
    # Relationships
    transaction = relationship('Transaction')
    
    __table_args__ = (
        Index('idx_refunds_transaction', 'transaction_id'),
        Index('idx_refunds_reference', 'refund_reference'),
    )
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'transaction_id': str(self.transaction_id),
            'refund_reference': self.refund_reference,
            'amount': float(self.amount),
            'reason': self.reason,
            'status': self.status,
            'mpesa_refund_receipt': self.mpesa_refund_receipt,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None
        }


class PaymentWebhookLog(BaseModel, OrganizationMixin):
    """Log of payment webhook/callback requests"""
    __tablename__ = 'payment_webhook_logs'
    
    webhook_type = Column(String(50), nullable=False)  # stk_callback, b2c_result, c2b_confirmation
    provider = Column(String(20), default='mpesa')
    request_id = Column(String(255), index=True)
    payload = Column(JSON)
    headers = Column(JSON)
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime)
    error_message = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    __table_args__ = (
        Index('idx_webhook_request_id', 'request_id'),
        Index('idx_webhook_processed', 'processed', 'created_at'),
    )
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'webhook_type': self.webhook_type,
            'provider': self.provider,
            'request_id': self.request_id,
            'processed': self.processed,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }