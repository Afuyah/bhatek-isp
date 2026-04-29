PERMISSIONS = {
    # Organization permissions
    'org_create': 'Can create organizations',
    'org_read': 'Can read organizations',
    'org_update': 'Can update organizations',
    'org_delete': 'Can delete organizations',
    
    # Router permissions
    'router_create': 'Can create routers',
    'router_read': 'Can read routers',
    'router_update': 'Can update routers',
    'router_delete': 'Can delete routers',
    'router_configure': 'Can configure routers',
    
    # Subscriber permissions
    'subscriber_create': 'Can create subscribers',
    'subscriber_read': 'Can read subscribers',
    'subscriber_update': 'Can update subscribers',
    'subscriber_delete': 'Can delete subscribers',
    
    # Payment permissions
    'payment_process': 'Can process payments',
    'payment_read': 'Can read payments',
    'payment_refund': 'Can refund payments',
    
    # Report permissions
    'report_view': 'Can view reports',
    'report_export': 'Can export reports',
    
    # Admin permissions
    'admin_access': 'Full admin access',
}

ROLE_PERMISSIONS = {
    'super_admin': ['*'],
    'org_admin': [
        'org_read', 'org_update',
        'router_create', 'router_read', 'router_update', 'router_configure',
        'subscriber_create', 'subscriber_read', 'subscriber_update',
        'payment_process', 'payment_read',
        'report_view', 'report_export'
    ],
    'staff': [
        'subscriber_read', 'subscriber_create',
        'payment_process',
        'report_view'
    ],
    'viewer': [
        'subscriber_read',
        'report_view'
    ]
}

def has_permission(user_permissions: list, required_permission: str) -> bool:
    """Check if user has required permission"""
    if '*' in user_permissions:
        return True
    return required_permission in user_permissions
