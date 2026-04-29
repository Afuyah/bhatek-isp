from marshmallow import Schema, fields, validate

class PaymentInitiateSchema(Schema):
    subscriber_id = fields.UUID(required=True)
    amount = fields.Float(required=True, validate=validate.Range(min=0.01))
    payment_method = fields.String(required=True, validate=validate.OneOf(['mpesa', 'cash']))
    phone = fields.String()
    metadata = fields.Dict()

class PaymentCallbackSchema(Schema):
    Body = fields.Dict(required=True)