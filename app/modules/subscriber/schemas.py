from marshmallow import Schema, fields, validate, validates, ValidationError
import re

class SubscriberCreateSchema(Schema):
    """Schema for creating a subscriber"""
    phone = fields.String(required=True, validate=validate.Length(min=10, max=15))
    name = fields.String(validate=validate.Length(max=200))
    email = fields.Email()
    first_name = fields.String(validate=validate.Length(max=100))
    last_name = fields.String(validate=validate.Length(max=100))
    
    @validates('phone')
    def validate_phone(self, value):
        pattern = r'^(254|0)[17]\d{8}$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX')

class SubscriberUpdateSchema(Schema):
    """Schema for updating a subscriber"""
    first_name = fields.String(validate=validate.Length(max=100))
    last_name = fields.String(validate=validate.Length(max=100))
    email = fields.Email()
    status = fields.String(validate=validate.OneOf(['active', 'suspended', 'blocked']))

class PurchasePlanSchema(Schema):
    """Schema for purchasing a plan"""
    plan_id = fields.String(required=True)
    payment_method = fields.String(required=True, validate=validate.OneOf(['mpesa', 'cash', 'bank_transfer']))
    payment_details = fields.Dict()

class CheckAccessSchema(Schema):
    """Schema for checking subscriber access"""
    device_mac = fields.String(required=True, validate=validate.Length(equal=17))
    
    @validates('device_mac')
    def validate_mac(self, value):
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid MAC address format')

class AddDeviceSchema(Schema):
    """Schema for adding a device"""
    mac_address = fields.String(required=True, validate=validate.Length(equal=17))
    device_name = fields.String(validate=validate.Length(max=100))
    device_type = fields.String(validate=validate.OneOf(['phone', 'tablet', 'laptop', 'desktop', 'router', 'other']))
    
    @validates('mac_address')
    def validate_mac(self, value):
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid MAC address format')