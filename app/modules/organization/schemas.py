from marshmallow import (
    Schema, fields, validate, validates, ValidationError, pre_load
)
import re


# Base Schema (Shared Logic)
class BaseOrganizationSchema(Schema):
    """Base schema with shared validation + normalization"""

    @pre_load
    def normalize_input(self, data, **kwargs):
        # Normalize slug
        slug = data.get('slug')
        if slug:
            slug = slug.strip().lower().replace(' ', '-')
            data['slug'] = slug

        # Normalize phone
        phone = data.get('phone')
        if phone:
            phone = re.sub(r'\s+', '', phone)

            if phone.startswith('0'):
                phone = '254' + phone[1:]
            elif phone.startswith('+254'):
                phone = phone[1:]

            data['phone'] = phone

        return data

    @validates('slug')
    def validate_slug(self, value):
        if value:
            if not re.match(r'^[a-z0-9-]+$', value):
                raise ValidationError(
                    'Slug must contain only lowercase letters, numbers, and hyphens'
                )
            if value.startswith('-') or value.endswith('-'):
                raise ValidationError(
                    'Slug cannot start or end with a hyphen'
                )

    @validates('phone')
    def validate_phone(self, value):
        if value:
            pattern = r'^(254)[17]\d{8}$'
            if not re.match(pattern, value):
                raise ValidationError(
                    'Invalid phone format. Use 2547XXXXXXXX or 2541XXXXXXXX'
                )


# Create Schema
class OrganizationCreateSchema(BaseOrganizationSchema):
    """Schema for creating an organization"""

    name = fields.String(required=True, validate=validate.Length(min=1, max=255))

    slug = fields.String(
        validate=validate.Length(min=3, max=100),
        allow_none=True
    )

    business_type = fields.String(
        validate=validate.OneOf([
            'hospital', 'university', 'mall', 'residential',
            'office', 'hotel', 'street_wifi', 'custom'
        ]),
        load_default='custom'
    )

    email = fields.Email(allow_none=True)
    phone = fields.String(allow_none=True)

    address = fields.String(allow_none=True)
    city = fields.String(allow_none=True)
    country = fields.String(allow_none=True)

    timezone = fields.String(load_default='Africa/Nairobi')

    currency = fields.String(
        validate=validate.OneOf(['KES', 'USD', 'EUR']),
        load_default='KES'
    )

    settings = fields.Dict(load_default=dict)


# Update Schema
class OrganizationUpdateSchema(BaseOrganizationSchema):
    """Schema for updating an organization"""

    name = fields.String(validate=validate.Length(min=1, max=255), allow_none=True)

    slug = fields.String(
        validate=validate.Length(min=3, max=100),
        allow_none=True
    )

    business_type = fields.String(
        validate=validate.OneOf([
            'hospital', 'university', 'mall', 'residential',
            'office', 'hotel', 'street_wifi', 'custom'
        ]),
        allow_none=True
    )

    email = fields.Email(allow_none=True)
    phone = fields.String(allow_none=True)

    address = fields.String(allow_none=True)
    city = fields.String(allow_none=True)
    country = fields.String(allow_none=True)

    logo_url = fields.String(allow_none=True)
    website = fields.String(allow_none=True)

    timezone = fields.String(allow_none=True)

    currency = fields.String(
        validate=validate.OneOf(['KES', 'USD', 'EUR']),
        allow_none=True
    )

    settings = fields.Dict(allow_none=True)

    status = fields.String(
        validate=validate.OneOf(['active', 'suspended']),
        allow_none=True
    )


# Organization User Schemas
class OrganizationUserAddSchema(Schema):
    """Schema for adding a user to organization"""

    user_id = fields.UUID(required=True)

    role = fields.String(
        required=True,
        validate=validate.OneOf(['org_admin', 'staff', 'viewer'])
    )


class OrganizationUserRoleUpdateSchema(Schema):
    """Schema for updating user role"""

    role = fields.String(
        required=True,
        validate=validate.OneOf(['org_admin', 'staff', 'viewer'])
    )


# Response Schema
class OrganizationResponseSchema(Schema):
    """Schema for organization response"""

    id = fields.UUID(dump_only=True)
    name = fields.String()
    slug = fields.String()

    business_type = fields.String()
    email = fields.String()
    phone = fields.String()

    address = fields.String()
    city = fields.String()
    country = fields.String()

    logo_url = fields.String()
    website = fields.String()

    timezone = fields.String()
    currency = fields.String()

    subscription_tier = fields.String()
    subscription_status = fields.String()
    subscription_expires_at = fields.DateTime()

    settings = fields.Dict()
    status = fields.String()

    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)