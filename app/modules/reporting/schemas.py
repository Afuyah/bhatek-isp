"""Reporting schemas — marshmallow validation for report query parameters."""

from marshmallow import Schema, fields, validate


class ReportQuerySchema(Schema):
    """Common query parameters for report endpoints."""
    period = fields.Str(
        load_default='monthly',
        validate=validate.OneOf(['daily', 'weekly', 'monthly', 'yearly', 'custom']),
    )
    start_date = fields.DateTime(load_default=None, allow_none=True)
    end_date = fields.DateTime(load_default=None, allow_none=True)
    month = fields.Int(load_default=None, allow_none=True, validate=validate.Range(1, 12))
    year = fields.Int(load_default=None, allow_none=True)


class RevenueReportSchema(ReportQuerySchema):
    """Revenue report query parameters."""
    pass


class SubscriberReportSchema(ReportQuerySchema):
    """Subscriber report query parameters."""
    pass


class UsageReportSchema(ReportQuerySchema):
    """Usage report query parameters."""
    pass


class ChurnRateSchema(Schema):
    """Churn rate query parameters."""
    months = fields.Int(load_default=6, validate=validate.Range(1, 24))


class BandwidthTrendSchema(Schema):
    """Bandwidth trend query parameters."""
    days = fields.Int(load_default=30, validate=validate.Range(1, 365))

