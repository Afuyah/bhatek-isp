ORGANIZATION_PERMISSIONS = {
    'org_view': 'Can view organization details',
    'org_edit': 'Can edit organization details',
    'org_delete': 'Can delete organization',
    'org_manage_users': 'Can manage organization users',
    'org_manage_billing': 'Can manage organization billing',
    'org_view_reports': 'Can view organization reports'
}

def can_manage_organization(user_role: str, is_owner: bool = False) -> bool:
    """Check if user can manage organization"""
    return user_role in ['super_admin', 'org_admin'] or is_owner
