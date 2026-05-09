from marshmallow import Schema, fields, validate, validates, ValidationError, pre_load
import re


class AccessPointCreateSchema(Schema):
    """Schema for creating an access point """
    
    # Required fields
    router_id = fields.UUID(required=True)
    name = fields.String(required=True, validate=validate.Length(min=2, max=255))
    mac_address = fields.String(required=True)
    ssid = fields.String(required=True, validate=validate.Length(min=1, max=32))
    location = fields.String(required=True, validate=validate.Length(min=2, max=255))
    
    # Optional fields
    hotspot_server_id = fields.UUID(required=False)
    ip_address = fields.String(required=False)
    description = fields.String(required=False, validate=validate.Length(max=500))
    is_active = fields.Boolean(load_default=True)
    settings = fields.Dict(load_default=dict)
    
    @validates('mac_address')
    def validate_mac(self, value):
        """Validate MAC address format (XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX)"""
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        if not re.match(pattern, value):
            raise ValidationError('Invalid MAC address format. Use format: 00:11:22:33:44:55')
    
    @validates('ip_address')
    def validate_ip(self, value):
        """Validate IP address format if provided"""
        if value:
            import ipaddress
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValidationError('Invalid IP address format')
    
    @pre_load
    def clean_data(self, data, **kwargs):
        """Clean and prepare data before validation"""
        if 'mac_address' in data:
            # Normalize MAC to uppercase
            data['mac_address'] = data['mac_address'].upper()
        if 'name' in data:
            data['name'] = data['name'].strip()
        if 'ssid' in data:
            data['ssid'] = data['ssid'].strip()
        if 'location' in data:
            data['location'] = data['location'].strip()
        if 'description' in data and data['description']:
            data['description'] = data['description'].strip()
        return data


class AccessPointUpdateSchema(Schema):
    """Schema for updating an access point"""
    
    # Updatable fields
    router_id = fields.UUID(required=False)
    hotspot_server_id = fields.UUID(required=False)
    name = fields.String(required=False, validate=validate.Length(min=2, max=255))
    mac_address = fields.String(required=False)
    ip_address = fields.String(required=False)
    ssid = fields.String(required=False, validate=validate.Length(min=1, max=32))
    location = fields.String(required=False, validate=validate.Length(min=2, max=255))
    description = fields.String(required=False, validate=validate.Length(max=500))
    is_active = fields.Boolean(required=False)
    settings = fields.Dict(required=False)
    
    @validates('mac_address')
    def validate_mac(self, value):
        """Validate MAC address format if provided"""
        if value:
            pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
            if not re.match(pattern, value):
                raise ValidationError('Invalid MAC address format. Use format: 00:11:22:33:44:55')
    
    @validates('ip_address')
    def validate_ip(self, value):
        """Validate IP address format if provided"""
        if value:
            import ipaddress
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValidationError('Invalid IP address format')
    
    @pre_load
    def clean_data(self, data, **kwargs):
        """Clean and prepare data before validation"""
        if 'mac_address' in data and data['mac_address']:
            data['mac_address'] = data['mac_address'].upper()
        if 'name' in data and data['name']:
            data['name'] = data['name'].strip()
        if 'ssid' in data and data['ssid']:
            data['ssid'] = data['ssid'].strip()
        if 'location' in data and data['location']:
            data['location'] = data['location'].strip()
        if 'description' in data and data['description']:
            data['description'] = data['description'].strip()
        return data


class AccessPointFilterSchema(Schema):
    """Schema for filtering access points list"""
    
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    per_page = fields.Integer(load_default=20, validate=validate.Range(min=1, max=100))
    status = fields.String(validate=validate.OneOf(['online', 'offline', 'unknown', 'error']))
    router_id = fields.UUID()
    location = fields.String(validate=validate.Length(max=255))
    search = fields.String(validate=validate.Length(max=100))
    is_active = fields.Boolean()


class AccessPointBulkActionSchema(Schema):
    """Schema for bulk operations on access points"""
    
    access_point_ids = fields.List(fields.UUID(), required=True, validate=validate.Length(min=1))
    action = fields.String(required=True, validate=validate.OneOf(['activate', 'deactivate', 'delete']))
    soft_delete = fields.Boolean(load_default=True)
    
    @validates('access_point_ids')
    def validate_ids(self, value):
        """Validate at least one ID provided"""
        if not value:
            raise ValidationError('At least one access point ID is required')
        if len(value) > 100:
            raise ValidationError('Cannot process more than 100 access points at once')