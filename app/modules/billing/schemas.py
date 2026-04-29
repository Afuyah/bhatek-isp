from marshmallow import Schema, fields, validate, validates, ValidationError, validates_schema
from datetime import datetime
import re

class PlanCreateSchema(Schema):
    """Schema for creating a plan"""
    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    description = fields.String(allow_none=True)
    plan_type = fields.String(required=True, validate=validate.OneOf(['hotspot', 'pppoe', 'both']))
    billing_cycle = fields.String(validate=validate.OneOf(['daily', 'weekly', 'monthly', 'quarterly', 'yearly']), allow_none=True)
    
    validity_type = fields.String(required=True, validate=validate.OneOf(['time_based', 'data_based', 'unlimited']))
    validity_days = fields.Integer(allow_none=True)
    data_limit_mb = fields.Integer(allow_none=True)
    
    bandwidth_up_mbps = fields.Integer(allow_none=True)
    bandwidth_down_mbps = fields.Integer(allow_none=True)
    
    price = fields.Decimal(required=True, places=2)
    setup_fee = fields.Decimal(allow_none=True, places=2)
    discount_percentage = fields.Decimal(allow_none=True, places=2, validate=validate.Range(min=0, max=100))
    
    concurrent_logins = fields.Integer(allow_none=True)
    device_limit = fields.Integer(allow_none=True)
    session_timeout_seconds = fields.Integer(allow_none=True)
    idle_timeout_seconds = fields.Integer(allow_none=True)
    
    auto_renew = fields.Boolean(allow_none=True)
    is_unlimited = fields.Boolean(allow_none=True)
    is_active = fields.Boolean(allow_none=True)
    is_public = fields.Boolean(allow_none=True)
    
    features = fields.List(fields.String(), allow_none=True)
    sort_order = fields.Integer(allow_none=True)
    
    @validates('validity_days')
    def validate_validity_days(self, value):
        if value is not None and value <= 0:
            raise ValidationError('Validity days must be positive')
    
    @validates('data_limit_mb')
    def validate_data_limit(self, value):
        if value is not None and value <= 0:
            raise ValidationError('Data limit must be positive')
    
    @validates_schema
    def validate_validity(self, data, **kwargs):
        """Validate validity based on type"""
        validity_type = data.get('validity_type')
        if validity_type == 'time_based' and not data.get('validity_days'):
            raise ValidationError('Validity days required for time-based plans', field_name='validity_days')
        if validity_type == 'data_based' and not data.get('data_limit_mb'):
            raise ValidationError('Data limit required for data-based plans', field_name='data_limit_mb')


class PlanUpdateSchema(Schema):
    """Schema for updating a plan"""
    name = fields.String(validate=validate.Length(min=1, max=255), allow_none=True)
    description = fields.String(allow_none=True)
    plan_type = fields.String(validate=validate.OneOf(['hotspot', 'pppoe', 'both']), allow_none=True)
    billing_cycle = fields.String(validate=validate.OneOf(['daily', 'weekly', 'monthly', 'quarterly', 'yearly']), allow_none=True)
    
    validity_type = fields.String(validate=validate.OneOf(['time_based', 'data_based', 'unlimited']), allow_none=True)
    validity_days = fields.Integer(allow_none=True)
    data_limit_mb = fields.Integer(allow_none=True)
    
    bandwidth_up_mbps = fields.Integer(allow_none=True)
    bandwidth_down_mbps = fields.Integer(allow_none=True)
    
    price = fields.Decimal(places=2, allow_none=True)
    setup_fee = fields.Decimal(places=2, allow_none=True)
    discount_percentage = fields.Decimal(places=2, validate=validate.Range(min=0, max=100), allow_none=True)
    
    concurrent_logins = fields.Integer(validate=validate.Range(min=1), allow_none=True)
    device_limit = fields.Integer(validate=validate.Range(min=1), allow_none=True)
    session_timeout_seconds = fields.Integer(allow_none=True)
    idle_timeout_seconds = fields.Integer(allow_none=True)
    
    auto_renew = fields.Boolean(allow_none=True)
    is_unlimited = fields.Boolean(allow_none=True)
    is_active = fields.Boolean(allow_none=True)
    is_public = fields.Boolean(allow_none=True)
    
    features = fields.List(fields.String(), allow_none=True)
    sort_order = fields.Integer(allow_none=True)


class PurchasePlanSchema(Schema):
    """Schema for purchasing a plan"""
    plan_id = fields.UUID(required=True)
    payment_method = fields.String(required=True, validate=validate.OneOf(['mpesa', 'cash', 'bank_transfer', 'card']))
    payment_details = fields.Dict(allow_none=True)
    coupon_code = fields.String(allow_none=True)
    
    @validates('payment_details')
    def validate_payment_details(self, value):
        if value and 'phone' not in value:
            raise ValidationError('Payment details must include phone number for M-Pesa')


class VoucherCreateSchema(Schema):
    """Schema for creating a voucher"""
    plan_id = fields.UUID(required=True)
    max_uses = fields.Integer(validate=validate.Range(min=1), allow_none=True)
    expires_in_days = fields.Integer(required=True, validate=validate.Range(min=1))
    notes = fields.String(allow_none=True)


class VoucherBatchCreateSchema(Schema):
    """Schema for creating a voucher batch"""
    plan_id = fields.UUID(required=True)
    batch_name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    quantity = fields.Integer(required=True, validate=validate.Range(min=1, max=10000))
    price_per_voucher = fields.Decimal(places=2, allow_none=True)
    expires_in_days = fields.Integer(validate=validate.Range(min=1), allow_none=True)
    notes = fields.String(allow_none=True)


class RedeemVoucherSchema(Schema):
    """Schema for redeeming a voucher"""
    voucher_code = fields.String(required=True)
    device_mac = fields.String(required=True)
    
    @validates('voucher_code')
    def validate_voucher_code(self, value):
        if not value or len(value) < 4:
            raise ValidationError('Invalid voucher code')
    
    @validates('device_mac')
    def validate_mac_address(self, value):
        # Basic MAC address validation
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid MAC address format')


class DiscountCouponCreateSchema(Schema):
    """Schema for creating a discount coupon"""
    code = fields.String(required=True, validate=validate.Length(min=3, max=50))
    description = fields.String(allow_none=True)
    discount_type = fields.String(required=True, validate=validate.OneOf(['percentage', 'fixed']))
    discount_value = fields.Decimal(required=True, places=2, validate=validate.Range(min=0.01))
    valid_from = fields.DateTime(required=True)
    valid_to = fields.DateTime(required=True)
    usage_limit = fields.Integer(validate=validate.Range(min=1), allow_none=True)
    minimum_purchase = fields.Decimal(places=2, validate=validate.Range(min=0), allow_none=True)
    applicable_plan_ids = fields.List(fields.UUID(), allow_none=True)
    
    @validates_schema
    def validate_dates(self, data, **kwargs):
        """Validate that valid_to is after valid_from"""
        valid_from = data.get('valid_from')
        valid_to = data.get('valid_to')
        if valid_from and valid_to and valid_to <= valid_from:
            raise ValidationError('valid_to must be after valid_from', field_name='valid_to')
    
    @validates('code')
    def validate_code(self, value):
        if not re.match(r'^[A-Z0-9]{3,20}$', value):
            raise ValidationError('Code must be uppercase alphanumeric, 3-20 characters')


class InvoiceFilterSchema(Schema):
    """Schema for filtering invoices"""
    status = fields.String(validate=validate.OneOf(['draft', 'sent', 'paid', 'overdue', 'cancelled', 'void']), allow_none=True)
    start_date = fields.DateTime(allow_none=True)
    end_date = fields.DateTime(allow_none=True)
    subscriber_id = fields.UUID(allow_none=True)
    
    @validates_schema
    def validate_dates(self, data, **kwargs):
        """Validate date range"""
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        if start_date and end_date and end_date < start_date:
            raise ValidationError('end_date must be after start_date', field_name='end_date')