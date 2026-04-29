from marshmallow import Schema, fields, validate, validates, ValidationError, validates_schema
import re

class UserCreateSchema(Schema):
    email = fields.Email(required=True)
    phone = fields.String(required=True, validate=validate.Length(min=10, max=15))
    password = fields.String(required=True, validate=validate.Length(min=8))
    first_name = fields.String(validate=validate.Length(max=100), allow_none=True)
    last_name = fields.String(validate=validate.Length(max=100), allow_none=True)

class UserUpdateSchema(Schema):
    email = fields.Email(allow_none=True)
    phone = fields.String(allow_none=True)
    first_name = fields.String(allow_none=True)
    last_name = fields.String(allow_none=True)
    is_active = fields.Boolean(allow_none=True)
    role = fields.String(allow_none=True)

class LoginSchema(Schema):
    email = fields.Email(required=True)
    password = fields.String(required=True)

class RefreshTokenSchema(Schema):
    refresh_token = fields.String(required=True)

class ChangePasswordSchema(Schema):
    current_password = fields.String(required=True)
    new_password = fields.String(required=True, validate=validate.Length(min=8))

class SendVerificationSchema(Schema):
    """Schema for sending verification email"""
    email = fields.Email(required=True)

class VerifyEmailSchema(Schema):
    """Schema for verifying email token"""
    token = fields.String(required=True, validate=validate.Length(min=10))

class RegisterOrganizationSchema(Schema):
    """Schema for organization registration"""
    email = fields.Email(required=True)
    password = fields.String(required=True, validate=validate.Length(min=8))
    confirm_password = fields.String(required=True)
    first_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    last_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    phone = fields.String(required=True)
    organization_name = fields.String(required=True, validate=validate.Length(min=2, max=255))
    organization_slug = fields.String(required=True, validate=validate.Length(min=2, max=100))
    
    @validates_schema
    def validate_all(self, data, **kwargs):
        """Validate all fields together to avoid data_key issues"""
        
        # Validate phone number
        phone = data.get('phone')
        if phone:
            pattern = r'^(254|0)[17]\d{8}$'
            if not re.match(pattern, phone):
                raise ValidationError(
                    'Invalid phone number format. Use 254XXXXXXXXX or 07XXXXXXXX',
                    field_name='phone'
                )
        
        # Validate organization slug
        org_slug = data.get('organization_slug')
        if org_slug:
            pattern = r'^[a-z0-9]+(?:-[a-z0-9]+)*$'
            if not re.match(pattern, org_slug):
                raise ValidationError(
                    'Slug must contain only lowercase letters, numbers, and hyphens',
                    field_name='organization_slug'
                )
        
        # Validate passwords match
        password = data.get('password')
        confirm_password = data.get('confirm_password')
        if password and confirm_password and password != confirm_password:
            raise ValidationError(
                'Passwords do not match',
                field_name='confirm_password'
            )