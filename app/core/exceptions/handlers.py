from flask import jsonify
from werkzeug.exceptions import HTTPException
from app.core.logging.logger import logger

class BusinessError(Exception):
    """Business logic error"""
    def __init__(self, message, code='BUSINESS_ERROR', status_code=400):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

class AuthenticationError(Exception):
    """Authentication error"""
    def __init__(self, message='Authentication required', code='AUTH_REQUIRED', status_code=401):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

class AuthorizationError(Exception):
    """Authorization error"""
    def __init__(self, message='Permission denied', code='PERMISSION_DENIED', status_code=403):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

class NotFoundError(Exception):
    """Resource not found error"""
    def __init__(self, message='Resource not found', code='NOT_FOUND', status_code=404):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

class ValidationError(Exception):
    """Validation error"""
    def __init__(self, message='Validation failed', code='VALIDATION_ERROR', status_code=400):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

def register_error_handlers(app):
    """Register error handlers"""
    
    @app.errorhandler(BusinessError)
    def handle_business_error(e):
        logger.warning(f"Business error: {e.message}")
        return jsonify({
            'error': e.code,
            'message': e.message
        }), e.status_code
    
    @app.errorhandler(AuthenticationError)
    def handle_auth_error(e):
        logger.warning(f"Authentication error: {e.message}")
        return jsonify({
            'error': e.code,
            'message': e.message
        }), e.status_code
    
    @app.errorhandler(AuthorizationError)
    def handle_authz_error(e):
        logger.warning(f"Authorization error: {e.message}")
        return jsonify({
            'error': e.code,
            'message': e.message
        }), e.status_code
    
    @app.errorhandler(NotFoundError)
    def handle_not_found(e):
        return jsonify({
            'error': e.code,
            'message': e.message
        }), e.status_code
    
    @app.errorhandler(ValidationError)
    def handle_validation_error(e):
        return jsonify({
            'error': e.code,
            'message': e.message
        }), e.status_code
    
    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        return jsonify({
            'error': e.name,
            'message': e.description
        }), e.code
    
    @app.errorhandler(Exception)
    def handle_generic_error(e):
        logger.error(f"Unhandled error: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'INTERNAL_ERROR',
            'message': 'An internal error occurred'
        }), 500
