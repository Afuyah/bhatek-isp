from flask import request, g, jsonify
from marshmallow import ValidationError

from app.modules.auth.service import AuthService
from app.modules.auth.schemas import (
    UserCreateSchema, LoginSchema, RefreshTokenSchema, ChangePasswordSchema,
    SendVerificationSchema, VerifyEmailSchema, RegisterOrganizationSchema
)
from app.core.exceptions.handlers import BusinessError
from app.core.logging.logger import logger

class AuthController:   
    def __init__(self):
        self.service = AuthService()
    
    def register(self):
        """Register new user"""
        try:
            data = UserCreateSchema().load(request.json)
            user = self.service.register(data)
            return jsonify({
                'success': True,
                'user': user.to_dict(),
                'message': 'Registration successful'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Registration error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    def login(self):
        """Login user"""
        try:
            data = LoginSchema().load(request.json)
            result = self.service.login(
                email=data['email'],
                password=data['password'],
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent', '')
            )
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Login error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 401
    
    def refresh(self):
        """Refresh access token"""
        try:
            data = RefreshTokenSchema().load(request.json)
            result = self.service.refresh_token(data['refresh_token'])
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Refresh error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 401
    
    def logout(self):
        """Logout user"""
        try:
            auth_header = request.headers.get('Authorization', '')
            refresh_token = request.json.get('refresh_token') if request.json else None
            
            self.service.logout(
                user_id=g.get('user_id'),
                refresh_token=refresh_token
            )
            return jsonify({'success': True, 'message': 'Logged out successfully'}), 200
        except Exception as e:
            logger.error(f"Logout error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    def change_password(self):
        """Change user password"""
        try:
            data = ChangePasswordSchema().load(request.json)
            self.service.change_password(
                user_id=g.get('user_id'),
                current_password=data['current_password'],
                new_password=data['new_password']
            )
            return jsonify({'success': True, 'message': 'Password changed successfully'}), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Change password error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 400
    
    
    def send_verification(self):
        """Send verification email to user"""
        try:
            data = SendVerificationSchema().load(request.json)
            result = self.service.send_verification_email(data['email'])
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except BusinessError as e:
            return jsonify({'error': e.message}), 400
        except Exception as e:
            logger.error(f"Send verification error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    def verify_email(self):
        """Verify email token"""
        try:
            data = VerifyEmailSchema().load(request.json)
            result = self.service.verify_email(data['token'])
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except BusinessError as e:
            return jsonify({'error': e.message}), 400
        except Exception as e:
            logger.error(f"Verify email error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    def register_organization(self):
        """Register new organization and admin user"""
        try:
            data = RegisterOrganizationSchema().load(request.json)
            result = self.service.register_organization(data)
            return jsonify(result), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except BusinessError as e:
            return jsonify({'error': e.message}), 400
        except Exception as e:
            logger.error(f"Register organization error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500