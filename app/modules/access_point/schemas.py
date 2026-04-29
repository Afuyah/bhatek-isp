from marshmallow import Schema, fields, validate, validates, ValidationError
import re

class AccessPointCreateSchema(Schema):
    router_id = fields.UUID()
    hotspot_server_id = fields.UUID()
    name = fields.String(required=True, validate=validate.Length(min=2, max=255))
    mac_address = fields.String(required=True)
    ip_address = fields.String()
    ssid = fields.String(required=True, validate=validate.Length(min=1, max=32))
    ssid_visibility = fields.Boolean(load_default=True)
    encryption_type = fields.String(load_default='wpa2',  validate=validate.OneOf(['open', 'wpa2', 'wpa3']))
    encryption_key = fields.String()
    channel = fields.Integer(validate=validate.Range(min=1, max=165))
    frequency = fields.String(load_default='2.4ghz', validate=validate.OneOf(['2.4ghz', '5ghz', 'both']))

    location = fields.String()
    settings = fields.Dict(load_default=dict)

    @validates('mac_address')
    def validate_mac(self, value):
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid MAC address format')


class AccessPointUpdateSchema(Schema):
    router_id = fields.UUID()
    hotspot_server_id = fields.UUID()
    name = fields.String(validate=validate.Length(min=2, max=255))
    ip_address = fields.String()
    ssid = fields.String(validate=validate.Length(min=1, max=32))
    ssid_visibility = fields.Boolean()
    encryption_type = fields.String(validate=validate.OneOf(['open', 'wpa2', 'wpa3']))
    encryption_key = fields.String()
    channel = fields.Integer(validate=validate.Range(min=1, max=165))
    frequency = fields.String(validate=validate.OneOf(['2.4ghz', '5ghz', 'both']))
    location = fields.String()
    settings = fields.Dict()
    is_active = fields.Boolean()