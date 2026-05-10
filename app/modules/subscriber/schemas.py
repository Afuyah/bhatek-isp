from marshmallow import Schema, fields, validate, validates, ValidationError, validates_schema
import re
from uuid import UUID


class SubscriberCreateSchema(Schema):
    """Schema for creating a hotspot subscriber (auto-created via phone)"""
    phone = fields.String(required=True, validate=validate.Length(min=10, max=15))
    name = fields.String(validate=validate.Length(max=200))
    email = fields.Email(allow_none=True)
    first_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    last_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    
    @validates('phone')
    def validate_phone(self, value):
        pattern = r'^(254|0)[17]\d{8}$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX')


class PPPoECreateSchema(Schema):
    """Schema for creating a PPPoE subscriber (admin created)"""
    username = fields.String(required=True, validate=validate.Length(min=3, max=100))
    password = fields.String(required=True, validate=validate.Length(min=6, max=100))
    plan_id = fields.UUID(required=True)
    phone = fields.String(validate=validate.Length(min=10, max=15), allow_none=True)
    first_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    last_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    email = fields.Email(allow_none=True)
    
    @validates('username')
    def validate_username(self, value):
        # Only alphanumeric, underscore, dot
        if not re.match(r'^[a-zA-Z0-9_.]+$', value):
            raise ValidationError('Username can only contain letters, numbers, underscore, and dot')
    
    @validates('phone')
    def validate_phone(self, value):
        if value:
            pattern = r'^(254|0)[17]\d{8}$'
            if not re.match(pattern, value):
                raise ValidationError('Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX')
    
    @validates_schema
    def validate_plan_type(self, data, **kwargs):
        """Ensure plan is PPPoE compatible"""
        # This will be validated in service
        pass


class SubscriberUpdateSchema(Schema):
    """Schema for updating a subscriber"""
    first_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    last_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    email = fields.Email(allow_none=True)
    phone = fields.String(validate=validate.Length(min=10, max=15), allow_none=True)
    username = fields.String(validate=validate.Length(min=3, max=100), allow_none=True)
    password = fields.String(validate=validate.Length(min=6, max=100), allow_none=True)
    status = fields.String(validate=validate.OneOf(['active', 'suspended', 'blocked', 'deleted']), allow_none=True)
    notes = fields.String(validate=validate.Length(max=500), allow_none=True)
    
    @validates('phone')
    def validate_phone(self, value):
        if value:
            pattern = r'^(254|0)[17]\d{8}$'
            if not re.match(pattern, value):
                raise ValidationError('Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX')
    
    @validates('username')
    def validate_username(self, value):
        if value:
            if not re.match(r'^[a-zA-Z0-9_.]+$', value):
                raise ValidationError('Username can only contain letters, numbers, underscore, and dot')


class SubscriberFilterSchema(Schema):
    """Schema for filtering subscribers list"""
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    per_page = fields.Integer(load_default=20, validate=validate.Range(min=1, max=100))
    status = fields.String(validate=validate.OneOf(['active', 'suspended', 'blocked', 'deleted']))
    subscriber_type = fields.String(validate=validate.OneOf(['hotspot', 'pppoe']))
    search = fields.String(validate=validate.Length(max=100))
    has_active_subscription = fields.Boolean()
    sort_by = fields.String(validate=validate.OneOf(['created_at', 'last_active_at', 'total_spent']), load_default='created_at')
    sort_order = fields.String(validate=validate.OneOf(['asc', 'desc']), load_default='desc')


class PurchasePlanSchema(Schema):
    """Schema for purchasing a plan (hotspot user)"""
    plan_id = fields.UUID(required=True)
    payment_method = fields.String(required=True, validate=validate.OneOf(['mpesa', 'cash', 'bank_transfer', 'card']))
    payment_details = fields.Dict(allow_none=True)
    coupon_code = fields.String(allow_none=True)
    
    @validates('payment_details')
    def validate_payment_details(self, value):
        if value and 'phone' not in value:
            raise ValidationError('Payment details must include phone number for M-Pesa')


class CheckAccessSchema(Schema):
    """Schema for checking subscriber access"""
    device_mac = fields.String(required=True, validate=validate.Length(equal=17))
    
    @validates('device_mac')
    def validate_mac(self, value):
        # Accept both colon and hyphen separated formats
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid MAC address format. Use XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX')
    
    @validates_schema
    def validate_entire_schema(self, data, **kwargs):
        """Additional validation if needed"""
        pass


class AddDeviceSchema(Schema):
    """Schema for adding a device to subscriber"""
    mac_address = fields.String(required=True, validate=validate.Length(equal=17))
    device_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    device_type = fields.String(validate=validate.OneOf(['phone', 'tablet', 'laptop', 'desktop', 'Tv', 'router', 'other']), 
                                load_default='other')
    
    @validates('mac_address')
    def validate_mac(self, value):
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid MAC address format. Use XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX')
    
    @validates('device_name')
    def validate_device_name(self, value):
        if value and len(value.strip()) < 2:
            raise ValidationError('Device name must be at least 2 characters')


class UpdateDeviceSchema(Schema):
    """Schema for updating a device"""
    device_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    device_type = fields.String(validate=validate.OneOf(['phone', 'tablet', 'laptop', 'desktop', 'Tv', 'router', 'other']), 
                                allow_none=True)
    is_primary = fields.Boolean(allow_none=True)
    is_active = fields.Boolean(allow_none=True)


class CreateSubscriptionSchema(Schema):
    """Schema for creating a subscription (admin for PPPoE)"""
    plan_id = fields.UUID(required=True)
    auto_renew = fields.Boolean(load_default=False)
    start_date = fields.DateTime(allow_none=True)
    expiry_date = fields.DateTime(allow_none=True)
    
    @validates_schema
    def validate_dates(self, data, **kwargs):
        """Validate that expiry is after start"""
        start_date = data.get('start_date')
        expiry_date = data.get('expiry_date')
        if start_date and expiry_date and expiry_date <= start_date:
            raise ValidationError('Expiry date must be after start date', field_name='expiry_date')


class RenewSubscriptionSchema(Schema):
    """Schema for renewing a subscription"""
    duration_days = fields.Integer(validate=validate.Range(min=1, max=365), allow_none=True)
    payment_method = fields.String(validate=validate.OneOf(['mpesa', 'cash', 'bank_transfer', 'card']), 
                                   load_default='cash')
    payment_details = fields.Dict(allow_none=True)


class SubscriberStatsSchema(Schema):
    """Schema for subscriber stats response"""
    # This is for documentation/response only
    pass


class RadiusAuthSchema(Schema):
    """Schema for RADIUS authentication request"""
    username = fields.String(required=True)
    password = fields.String(required=True)
    organization_slug = fields.String(required=True)
    nas_ip_address = fields.String(allow_none=True)
    nas_identifier = fields.String(allow_none=True)
    called_station_id = fields.String(allow_none=True)
    calling_station_id = fields.String(allow_none=True)
    
    @validates('username')
    def validate_username(self, value):
        if not value or len(value) < 3:
            raise ValidationError('Invalid username format')


class BulkActionSchema(Schema):
    """Schema for bulk actions on subscribers"""
    subscriber_ids = fields.List(fields.UUID(), required=True, validate=validate.Length(min=1))
    action = fields.String(required=True, validate=validate.OneOf(['activate', 'suspend', 'delete', 'export']))
    
    @validates('subscriber_ids')
    def validate_ids(self, value):
        if not value:
            raise ValidationError('At least one subscriber ID is required')
        if len(value) > 100:
            raise ValidationError('Cannot process more than 100 subscribers at once')