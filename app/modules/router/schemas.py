from marshmallow import Schema, fields, validate, validates, ValidationError
import ipaddress

class RouterCreateSchema(Schema):
    name = fields.String(required=True, validate=validate.Length(min=2, max=255))
    network_id = fields.UUID()
    model = fields.String()
    ip_address = fields.String(required=True)

    api_port = fields.Integer(load_default=8728, validate=validate.Range(min=1, max=65535))

    api_ssl_port = fields.Integer(load_default=8729, validate=validate.Range(min=1, max=65535))
    username = fields.String(required=True)
    password = fields.String(required=True)
    location = fields.String()
    settings = fields.Dict(load_default=dict)

    @validates('ip_address')
    def validate_ip(self, value):
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValidationError('Invalid IP address')


class RouterUpdateSchema(Schema):
    name = fields.String(validate=validate.Length(min=2, max=255))
    network_id = fields.UUID()
    model = fields.String()
    ip_address = fields.String()
    api_port = fields.Integer(validate=validate.Range(min=1, max=65535))
    username = fields.String()
    password = fields.String()
    location = fields.String()
    settings = fields.Dict(load_default=dict)
    is_active = fields.Boolean()


class RouterTestSchema(Schema):
    ip_address = fields.String(required=True)
    username = fields.String(required=True)
    password = fields.String(required=True)

    port = fields.Integer(load_default=8728)