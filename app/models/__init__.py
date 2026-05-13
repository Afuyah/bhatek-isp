# Import base classes and mixins from core.database
from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin, TimestampMixin, SoftDeleteMixin, StatusMixin

# Import all business models
from app.models.auth import User, RefreshToken, AuditLog
from app.models.verification import EmailVerification
from app.models.organization import Organization, OrganizationUser
from app.models.network import Network
from app.models.router import Router, HotspotServer, PPPoeServer
from app.models.access_point import AccessPoint
from app.models.subscriber import Subscriber, Device
from app.models.billing import Plan, Subscription, Invoice, InvoiceItem, Voucher, VoucherBatch, DiscountCoupon
from app.models.payment import PaymentAccount, Transaction, Refund, PaymentWebhookLog
from app.models.session import ActiveSession

# RADIUS integration models (for FreeRADIUS)
from app.models.radius import RadCheck, RadReply, RadUserGroup, RadAcct

# NEW: NAS model for FreeRADIUS client configuration
from app.models.nas import NAS


# Export all models
__all__ = [
    # Base classes
    'BaseModel', 'OrganizationMixin', 'TimestampMixin', 'SoftDeleteMixin', 'StatusMixin', 
    
    # Auth models
    'User', 'RefreshToken', 'AuditLog', 'EmailVerification',
    
    # Organization models
    'Organization', 'OrganizationUser',
    
    # Network models
    'Network',
    
    # Router models
    'Router', 'HotspotServer', 'PPPoeServer',
    
    # Access Point models
    'AccessPoint',
    
    # Subscriber models
    'Subscriber', 'Device',
    
    # Billing models
    'Plan', 'Subscription', 'Invoice', 'InvoiceItem', 'Voucher', 'VoucherBatch', 'DiscountCoupon',
    
    # Payment models
    'PaymentAccount', 'Transaction', 'Refund', 'PaymentWebhookLog',
    
    # Session models
    'ActiveSession',
    
    # RADIUS models (FreeRADIUS integration)
    'RadCheck', 'RadReply', 'RadUserGroup', 'RadAcct',
    
    # NEW: NAS model
    'NAS',
]