import uuid
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class RequestIDMiddleware:
    """Request ID middleware for WSGI wrapping"""
    
    def __init__(self, app):
        """Initialize with WSGI app"""
        self.app = app
        self.header_name = 'X-Request-ID'
    
    def __call__(self, environ, start_response):
        """WSGI callable"""
        # Generate or propagate request ID
        request_id = self._get_incoming_request_id(environ)
        
        if not request_id:
            request_id = self._generate_request_id()
        
        # Store in environment
        environ['REQUEST_ID'] = request_id
        
        # Add to response headers
        def custom_start_response(status, headers, exc_info=None):
            # Add request ID header if not already present
            if not any(h[0].lower() == self.header_name.lower() for h in headers):
                headers.append((self.header_name, request_id))
            return start_response(status, headers, exc_info)
        
        return self.app(environ, custom_start_response)
    
    def _get_incoming_request_id(self, environ) -> Optional[str]:
        """Get request ID from incoming headers"""
        header_names = [
            'HTTP_X_REQUEST_ID',
            'HTTP_X_CORRELATION_ID',
            'HTTP_X_TRACE_ID',
            'HTTP_REQUEST_ID'
        ]
        
        for header in header_names:
            request_id = environ.get(header, '')
            if request_id:
                if self._is_valid_request_id(request_id):
                    return request_id
        
        return None
    
    def _generate_request_id(self) -> str:
        """Generate unique request ID"""
        return str(uuid.uuid4())
    
    def _is_valid_request_id(self, request_id: str) -> bool:
        """Validate request ID format"""
        import re
        if re.match(r'^[a-zA-Z0-9\-_]+$', request_id):
            return True
        logger.warning(f"Invalid request ID format: {request_id}")
        return False