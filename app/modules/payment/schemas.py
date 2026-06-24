class PaymentInitiateSchema(Schema):
    
    amount = fields.Float(
        required=True,
        validate=validate.Range(min=0.01),
    )
    payment_method = fields.String(
        required=True,
        validate=validate.OneOf(['mpesa', 'cash', 'bank_transfer', 'voucher']),
    )
    phone = fields.String(
        required=False,
        validate=validate.Regexp(
            r'^(?:\+?254|0)?[17]\d{8}$',
            error='Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX',
        ),
    )
    subscriber_id = fields.UUID(
        required=False,
        allow_none=True,
    )
    plan_id = fields.UUID(
        required=False,
        allow_none=True,
    )
    device_mac = fields.String(
        required=False,
        allow_none=True,
    )
    metadata = fields.Dict(
        required=False,
    )


class PaymentCallbackSchema(Schema):
    """
    Schema for M-Pesa callback/webhook data.

    The Body.stkCallback structure is what Safaricom sends.
    We only validate the top-level structure — detailed parsing
    is done by MpesaCallbackHandler.
    """
    Body = fields.Dict(required=True)


class RefundSchema(Schema):
    """
    Schema for refunding a transaction.
    """
    reason = fields.String(
        required=True,
        validate=validate.Length(min=3, max=500),
    )
    amount = fields.Float(
        required=False,
        validate=validate.Range(min=0.01),
    )


class PaymentVerifySchema(Schema):
    """
    Schema for verifying payment status.
    """
    checkout_request_id = fields.String(
        required=False,
    )


class PaymentListSchema(Schema):
    """
    Schema for filtering transaction lists.
    """
    page = fields.Integer(
        required=False,
        validate=validate.Range(min=1),
        missing=1,
    )
    per_page = fields.Integer(
        required=False,
        validate=validate.Range(min=1, max=100),
        missing=20,
    )
    subscriber_id = fields.UUID(
        required=False,
    )
    status = fields.String(
        required=False,
        validate=validate.OneOf([
            'pending', 'success', 'failed', 'refunded', 'cancelled',
        ]),
    )
    payment_method = fields.String(
        required=False,
        validate=validate.OneOf([
            'mpesa', 'cash', 'bank_transfer', 'voucher',
        ]),
    )