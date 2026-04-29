from marshmallow import Schema, fields, validate

class NetworkCreateSchema(Schema):
    name = fields.String(required=True, validate=validate.Length(min=2, max=255))
    type = fields.String(required=True, validate=validate.OneOf(['hotspot', 'pppoe', 'both']))
    description = fields.String()
    settings = fields.Dict()

class NetworkUpdateSchema(Schema):
    name = fields.String(validate=validate.Length(min=2, max=255))
    type = fields.String(validate=validate.OneOf(['hotspot', 'pppoe', 'both']))
    description = fields.String()
    settings = fields.Dict()
    is_active = fields.Boolean()