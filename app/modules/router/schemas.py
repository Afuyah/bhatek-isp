# app/modules/router/schemas.py
from marshmallow import Schema, fields, validate, validates, ValidationError, pre_load
import ipaddress
import re
from uuid import UUID


class RouterCreateSchema(Schema):
    """Schema for creating a new router with RADIUS auto-configuration"""
    
    # Required fields
    name = fields.String(required=True, validate=validate.Length(min=2, max=255))
    network_id = fields.UUID(required=True)
    ip_address = fields.String(required=True)
    username = fields.String(required=True, validate=validate.Length(min=1, max=100))
    password = fields.String(required=True, validate=validate.Length(min=1))
    
    # Optional fields
    model = fields.String(required=False, validate=validate.Length(max=100))
    api_port = fields.Integer(load_default=8728, validate=validate.Range(min=1, max=65535))
    location = fields.String(required=False, validate=validate.Length(max=255))
    description = fields.String(required=False, validate=validate.Length(max=500))
    is_active = fields.Boolean(load_default=True)
    
    # Advanced settings
    settings = fields.Dict(load_default=dict)
    
    @validates('ip_address')
    def validate_ip(self, value, **kwargs):
        """Validate IP address format"""
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValidationError('Invalid IP address format')
    
    @validates('name')
    def validate_name(self, value, **kwargs):
        """Validate router name"""
        if not value or not value.strip():
            raise ValidationError('Router name cannot be empty')
        if len(value) < 2:
            raise ValidationError('Router name must be at least 2 characters')
    
    @validates('api_port')
    def validate_api_port(self, value, **kwargs):
        """Validate API port"""
        if value < 1 or value > 65535:
            raise ValidationError('Port must be between 1 and 65535')
    
    @pre_load
    def clean_data(self, data, **kwargs):
        """Clean and prepare data before validation"""
        if 'name' in data:
            data['name'] = data['name'].strip()
        if 'username' in data:
            data['username'] = data['username'].strip()
        if 'description' in data and data['description']:
            data['description'] = data['description'].strip()
        return data


class RouterUpdateSchema(Schema):
    """Schema for updating an existing router"""
    
    # Optional fields (all can be updated)
    name = fields.String(required=False, validate=validate.Length(min=2, max=255))
    network_id = fields.UUID(required=False)
    model = fields.String(required=False, validate=validate.Length(max=100))
    ip_address = fields.String(required=False)
    api_port = fields.Integer(required=False, validate=validate.Range(min=1, max=65535))
    username = fields.String(required=False, validate=validate.Length(min=1, max=100))
    password = fields.String(required=False, validate=validate.Length(min=1))
    location = fields.String(required=False, validate=validate.Length(max=255))
    description = fields.String(required=False, validate=validate.Length(max=500))
    is_active = fields.Boolean(required=False)
    settings = fields.Dict(required=False)
    
    @validates('ip_address')
    def validate_ip(self, value, **kwargs):
        """Validate IP address format if provided"""
        if value:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValidationError('Invalid IP address format')
    
    @validates('name')
    def validate_name(self, value, **kwargs):
        """Validate router name if provided"""
        if value and len(value.strip()) < 2:
            raise ValidationError('Router name must be at least 2 characters')
    
    @pre_load
    def clean_data(self, data, **kwargs):
        """Clean and prepare data before validation"""
        if 'name' in data and data['name']:
            data['name'] = data['name'].strip()
        if 'username' in data and data['username']:
            data['username'] = data['username'].strip()
        if 'description' in data and data['description']:
            data['description'] = data['description'].strip()
        return data


class RouterTestSchema(Schema):
    """Schema for testing router connection before adding"""
    
    ip_address = fields.String(required=True)
    username = fields.String(required=True, validate=validate.Length(min=1))
    password = fields.String(required=True, validate=validate.Length(min=1))
    port = fields.Integer(load_default=8728, validate=validate.Range(min=1, max=65535))
    api_ssl = fields.Boolean(load_default=False)
    api_ssl_port = fields.Integer(load_default=8729, validate=validate.Range(min=1, max=65535))
    
    @validates('ip_address')
    def validate_ip(self, value, **kwargs):
        """Validate IP address format"""
        if not value:
            raise ValidationError('IP address is required')
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValidationError('Invalid IP address format')
    
    @validates('port')
    def validate_port(self, value, **kwargs):
        """Validate port range"""
        if value < 1 or value > 65535:
            raise ValidationError('Port must be between 1 and 65535')


class RouterRadiusSchema(Schema):
    """Schema for manually configuring RADIUS on a router"""
    
    radius_server = fields.String(required=True)
    radius_secret = fields.String(required=True, validate=validate.Length(min=1))
    radius_port = fields.Integer(load_default=1812, validate=validate.Range(min=1, max=65535))
    radius_acct_port = fields.Integer(load_default=1813, validate=validate.Range(min=1, max=65535))
    
    @validates('radius_server')
    def validate_radius_server(self, value, **kwargs):
        """Validate RADIUS server IP or hostname"""
        if not value:
            raise ValidationError('RADIUS server is required')
        try:
            ipaddress.ip_address(value)
        except ValueError:
            # Allow hostnames as well
            if not re.match(r'^[a-zA-Z0-9.-]+$', value):
                raise ValidationError('Invalid RADIUS server address')


class RouterSyncSchema(Schema):
    """Schema for sync options"""
    
    sync_hotspot = fields.Boolean(load_default=True)
    sync_pppoe = fields.Boolean(load_default=True)
    sync_dhcp = fields.Boolean(load_default=False)
    sync_firewall = fields.Boolean(load_default=False)


class RouterFilterSchema(Schema):
    """Schema for filtering routers list"""
    
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    per_page = fields.Integer(load_default=20, validate=validate.Range(min=1, max=100))
    status = fields.String(validate=validate.OneOf(['online', 'offline', 'error', 'unknown']))
    network_id = fields.UUID(required=False)
    search = fields.String(validate=validate.Length(max=100))
    is_active = fields.Boolean(required=False)
    radius_config_status = fields.String(validate=validate.OneOf(['pending', 'configured', 'failed', 'manual']))


class RouterBulkActionSchema(Schema):
    """Schema for bulk operations"""
    
    router_ids = fields.List(fields.UUID(), required=True, validate=validate.Length(min=1))
    soft = fields.Boolean(load_default=True)
    
    @validates('router_ids')
    def validate_ids(self, value, **kwargs):
        """Validate at least one ID provided"""
        if not value:
            raise ValidationError('At least one router ID is required')
        if len(value) > 100:
            raise ValidationError('Cannot process more than 100 routers at once')


class RouterRadiusRetrySchema(Schema):
    """Schema for retrying RADIUS configuration on a router"""
    
    # No required fields - uses stored configuration
    # 'force' is optional with metadata instead of description parameter
    force = fields.Boolean(load_default=False, metadata={"description": "Force reconfiguration even if already configured"})


class RouterResponseSchema(Schema):
    """Schema for router API responses"""
    
    id = fields.UUID()
    organization_id = fields.UUID()
    network_id = fields.UUID()
    name = fields.String()
    model = fields.String(allow_none=True)
    ip_address = fields.String()
    api_port = fields.Integer()
    username = fields.String()
    location = fields.String(allow_none=True)
    description = fields.String(allow_none=True)
    status = fields.String()
    radius_config_status = fields.String()
    radius_configured_at = fields.DateTime(allow_none=True)
    is_active = fields.Boolean()
    created_at = fields.DateTime()
    updated_at = fields.DateTime()
    last_seen_at = fields.DateTime(allow_none=True)
    last_sync_at = fields.DateTime(allow_none=True)
    
    # Health metrics
    cpu_usage = fields.Integer(allow_none=True)
    memory_usage = fields.Integer(allow_none=True)
    uptime_seconds = fields.Integer(allow_none=True)
    
    class Meta:
        ordered = True


class RouterCreateResponseSchema(Schema):
    """Schema for router creation response (includes RADIUS config)"""
    
    success = fields.Boolean()
    router = fields.Nested(RouterResponseSchema)
    auto_configured = fields.Boolean()
    radius_secret = fields.String(allow_none=True)
    radius_server_ip = fields.String(allow_none=True)
    radius_ports = fields.Dict(keys=fields.String(), values=fields.Integer(), allow_none=True)
    manual_config_instructions = fields.Dict(allow_none=True)
    warning = fields.String(allow_none=True)
    message = fields.String()