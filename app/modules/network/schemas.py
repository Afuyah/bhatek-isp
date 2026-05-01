from marshmallow import Schema, fields, validate, validates, ValidationError
import re


class NetworkCreateSchema(Schema):
    """Schema for creating a network"""

    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    slug = fields.String(validate=validate.Length(min=3, max=100), allow_none=True)

    type = fields.String(
        validate=validate.OneOf(['hotspot', 'pppoe', 'hybrid']),
        load_default='hybrid'  
    )

    description = fields.String(allow_none=True)

    settings = fields.Dict(load_default=dict )

    is_active = fields.Boolean( load_default=True)

    @validates('slug')
    def validate_slug(self, value, **kwargs):  
        if value:
            if not re.match(r'^[a-z0-9-]+$', value):
                raise ValidationError('Slug must contain only lowercase letters, numbers, and hyphens')
            if value.startswith('-') or value.endswith('-'):
                raise ValidationError('Slug cannot start or end with a hyphen')

class NetworkUpdateSchema(Schema):
    """Schema for updating a network"""
    name = fields.String(validate=validate.Length(min=1, max=255), allow_none=True)
    slug = fields.String(validate=validate.Length(min=3, max=100), allow_none=True)
    type = fields.String(validate=validate.OneOf(['hotspot', 'pppoe', 'hybrid']), allow_none=True)
    description = fields.String(allow_none=True)
    settings = fields.Dict(allow_none=True)
    is_active = fields.Boolean(allow_none=True)
    
    @validates('slug')
    def validate_slug(self, value):
        if value:
            if not re.match(r'^[a-z0-9-]+$', value):
                raise ValidationError('Slug must contain only lowercase letters, numbers, and hyphens')
            if value.startswith('-') or value.endswith('-'):
                raise ValidationError('Slug cannot start or end with a hyphen')


class NetworkResponseSchema(Schema):
    """Schema for network response"""
    id = fields.UUID()
    name = fields.String()
    slug = fields.String()
    type = fields.String()
    description = fields.String()
    settings = fields.Dict()
    is_active = fields.Boolean()
    created_at = fields.DateTime()
    updated_at = fields.DateTime()


class BulkNetworkStatusSchema(Schema):
    """Schema for bulk status update"""
    network_ids = fields.List(fields.UUID(), required=True)
    is_active = fields.Boolean(required=True)
    
    @validates('network_ids')
    def validate_network_ids(self, value):
        if not value:
            raise ValidationError('network_ids cannot be empty')
        if len(value) > 100:
            raise ValidationError('Cannot update more than 100 networks at once')