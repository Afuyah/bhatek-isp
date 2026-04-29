import logging

logger = logging.getLogger(__name__)

class TenantMiddleware:
    """Tenant isolation middleware for WSGI wrapping"""
    
    def __init__(self, app):
        """Initialize with WSGI app"""
        self.app = app
    
    def __call__(self, environ, start_response):
        """WSGI callable"""
        # Extract tenant from header (backward compatibility)
        tenant_id = environ.get('HTTP_X_TENANT_ID', '')
        
        if tenant_id:
            environ['TENANT_ID'] = tenant_id
            environ['ORGANIZATION_ID'] = tenant_id
        
        # Also check for organization_id in path
        path = environ.get('PATH_INFO', '')
        path_parts = path.split('/')
        
        # Look for /api/v1/organizations/{id} pattern
        for i, part in enumerate(path_parts):
            if part == 'organizations' and i + 1 < len(path_parts):
                org_id = path_parts[i + 1]
                if org_id and org_id != 'me' and org_id != 'v1':
                    environ['ORGANIZATION_ID'] = org_id
                    break
        
        return self.app(environ, start_response)