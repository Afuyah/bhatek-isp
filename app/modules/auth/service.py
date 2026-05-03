from typing import Dict, Any, Optional, Tuple, List
from uuid import UUID, uuid4
from datetime import datetime, timedelta
from flask import current_app, request
import secrets
import threading

from app.modules.auth.repository import UserRepository, RefreshTokenRepository
from app.models.auth import User
from app.core.security.jwt import JWTService
from app.core.database.session import db
from app.core.logging.logger import logger
from app.core.exceptions.handlers import AuthenticationError, ValidationError, BusinessError, NotFoundError
from flask import copy_current_request_context


class AuthService:
    
    def __init__(self):
        self.user_repo = UserRepository()
        self.token_repo = RefreshTokenRepository()
        self._jwt_service = None
    
    @property
    def jwt_service(self):
        """Get JWT service instance from Flask extensions"""
        if self._jwt_service is None:
            if hasattr(current_app, 'extensions') and 'jwt_service' in current_app.extensions:
                self._jwt_service = current_app.extensions['jwt_service']
            else:
                # Fallback: create new instance
                self._jwt_service = JWTService(current_app)
        return self._jwt_service
    
    def register(self, data: Dict[str, Any]) -> User:
        """Register a new user"""
        # Check existing
        if self.user_repo.get_by_email(data['email']):
            raise ValidationError('Email already registered')
        
        if self.user_repo.get_by_phone(data['phone']):
            raise ValidationError('Phone number already registered')
        
        # Create user
        user_data = {
            'email': data['email'],
            'phone': data['phone'],
            'first_name': data.get('first_name'),
            'last_name': data.get('last_name'),
            'role': data.get('role', 'user'),
            'is_active': True
        }
        
        user = self.user_repo.create(user_data)
        user.set_password(data['password'])
        self.user_repo.update(user.id, {'password_hash': user.password_hash})
        
        # Send welcome email asynchronously
        self._send_welcome_email_async(user.email, user.first_name, user.last_name)
        
        logger.info(f"User registered: {user.email}")
        
        return user
    
    def _send_welcome_email_async(self, email: str, first_name: str = None, last_name: str = None):
        """Send welcome email asynchronously using Flask's context copy"""
        from app.integrations.email.service import EmailService
        
        name = f"{first_name} {last_name}".strip() if first_name or last_name else "User"
        
        @copy_current_request_context
        def send():
            try:
                email_service = EmailService()
                success = email_service.send_welcome_email(
                    to_email=email,
                    first_name=name,
                    organization_name="Bhatek ISP"
                )
                
                if success:
                    logger.info(f"Welcome email sent to {email}")
                else:
                    logger.error(f"Failed to send welcome email to {email}")
                    
            except Exception as e:
                logger.error(f"Failed to send welcome email to {email}: {e}", exc_info=True)
        
        # Start thread with copied context
        thread = threading.Thread(target=send, daemon=True)
        thread.start()
    
    def login(self, email: str, password: str, ip_address: str, user_agent: str) -> Dict[str, Any]:
        """Authenticate user and generate tokens with session tracking"""
        user = self.user_repo.get_by_email(email)
        if not user:
            raise AuthenticationError('Invalid credentials')
        
        # Check lockout
        if user.locked_until and user.locked_until > datetime.utcnow():
            raise AuthenticationError(f'Account locked until {user.locked_until}')
        
        # Verify password
        if not user.check_password(password):
            self.user_repo.update_login_attempts(user.id, False)
            raise AuthenticationError('Invalid credentials')
        
        if not user.is_active:
            raise AuthenticationError('Account is disabled')
        
        # Reset login attempts
        self.user_repo.update_login_attempts(user.id, True)
        
        # Generate unique session ID for this login session
        session_id = str(uuid4())
        
        # Get device fingerprint for additional security (optional)
        device_fingerprint = self._get_device_fingerprint()
        
        # Generate access token with session_id
        access_token = self.jwt_service.generate_access_token(
            user_id=str(user.id),
            email=user.email,
            organization_id=str(user.organization_id) if user.organization_id else None,
            role=user.role,
            permissions=user.permissions,
            session_id=session_id  # ✅ CRITICAL: Enables single-session revocation
        )
        
        # Generate refresh token with same session_id
        refresh_token = self.jwt_service.generate_refresh_token(
            user_id=str(user.id),
            session_id=session_id  # ✅ CRITICAL: Links refresh token to session
        )
        
        # Store refresh token with session_id
        self.token_repo.create({
            'user_id': user.id,
            'token': refresh_token,
            'session_id': session_id,  # ✅ ADD THIS to RefreshToken model
            'expires_at': datetime.utcnow() + current_app.config['JWT_REFRESH_TOKEN_EXPIRES'],
            'user_agent': user_agent,
            'ip_address': ip_address,
            'device_fingerprint': device_fingerprint
        })
        
        logger.info(f"User logged in: {user.email}, session_id: {session_id}")
        
        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'token_type': 'Bearer',
            'expires_in': current_app.config['JWT_ACCESS_TOKEN_EXPIRES'].total_seconds(),
            'session_id': session_id,  # ✅ Return to client for optional tracking
            'user': user.to_dict(exclude={'password_hash'})
        }
    
    def refresh_token(self, refresh_token: str) -> Dict[str, str]:
        """Generate new access token with refresh token rotation"""
        # Get valid refresh token from database
        token_record = self.token_repo.get_valid_token(refresh_token)
        if not token_record:
            raise AuthenticationError('Invalid or expired refresh token')
        
        # Get user
        user = self.user_repo.get_by_id(token_record.user_id)
        if not user or not user.is_active:
            raise AuthenticationError('User not found or inactive')
        
        # Extract session_id from the refresh token record
        session_id = token_record.session_id
        
        # Generate NEW refresh token (rotation) - blacklist old one happens in JWTService
        new_refresh_token = self.jwt_service.generate_refresh_token(
            user_id=str(user.id),
            session_id=session_id
        )
        
        # Generate new access token with same session_id
        access_token = self.jwt_service.generate_access_token(
            user_id=str(user.id),
            email=user.email,
            organization_id=str(user.organization_id) if user.organization_id else None,
            role=user.role,
            permissions=user.permissions,
            session_id=session_id
        )
        
        # Blacklist the old refresh token in database
        token_record.revoked = True
        token_record.revoked_at = datetime.utcnow()
        db.session.commit()
        
        # Create new refresh token record
        self.token_repo.create({
            'user_id': user.id,
            'token': new_refresh_token,
            'session_id': session_id,
            'expires_at': datetime.utcnow() + current_app.config['JWT_REFRESH_TOKEN_EXPIRES'],
            'user_agent': token_record.user_agent,
            'ip_address': token_record.ip_address,
            'device_fingerprint': token_record.device_fingerprint
        })
        
        logger.info(f"Token refreshed for user: {user.email}, session_id: {session_id}")
        
        return {
            'access_token': access_token,
            'refresh_token': new_refresh_token,
            'token_type': 'Bearer',
            'expires_in': current_app.config['JWT_ACCESS_TOKEN_EXPIRES'].total_seconds()
        }
    
    def logout(self, user_id: UUID, refresh_token: str = None, session_id: str = None):
        """
        Logout user - supports three scenarios:
        1. No params: Logout from ALL devices
        2. refresh_token only: Logout specific device using refresh token
        3. session_id only: Logout specific device using session ID (from access token)
        """
        
        # Scenario 1: Logout from ALL devices
        if not refresh_token and not session_id:
            # Revoke all refresh tokens in database
            self.token_repo.revoke_user_tokens(user_id)
            
            # Revoke all JWT tokens by incrementing token version
            try:
                self.jwt_service.revoke_user_tokens(str(user_id))
            except Exception as e:
                logger.error(f"Error revoking JWT tokens: {e}")
            
            logger.info(f"User logged out from ALL devices: {user_id}")
            return
        
        # Scenario 2 & 3: Logout specific device
        target_session_id = None
        
        # Get session_id from refresh token if provided
        if refresh_token:
            token_record = self.token_repo.get_valid_token(refresh_token)
            if token_record and token_record.user_id == user_id:
                target_session_id = token_record.session_id
                # Revoke this specific refresh token
                token_record.revoked = True
                token_record.revoked_at = datetime.utcnow()
                db.session.commit()
        elif session_id:
            target_session_id = session_id
            # Revoke all refresh tokens with this session_id
            self.token_repo.revoke_session_tokens(user_id, target_session_id)
        
        # Revoke JWT tokens for this specific session only
        if target_session_id:
            try:
                # Call JWTService to revoke only this session's tokens
                self.jwt_service.revoke_user_tokens(str(user_id), session_id=target_session_id)
            except Exception as e:
                logger.error(f"Error revoking JWT tokens for session {target_session_id}: {e}")
            
            logger.info(f"User logged out from device (session {target_session_id}): {user_id}")
        else:
            # Fallback to full revocation if session_id not found
            logger.warning(f"Session not found for logout, revoking all tokens for user: {user_id}")
            self.token_repo.revoke_user_tokens(user_id)
            try:
                self.jwt_service.revoke_user_tokens(str(user_id))
            except Exception as e:
                logger.error(f"Error revoking JWT tokens: {e}")
    
    def change_password(self, user_id: UUID, current_password: str, new_password: str):
        """Change user password - revokes ALL sessions"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise ValidationError('User not found')
        
        if not user.check_password(current_password):
            raise AuthenticationError('Current password is incorrect')
        
        user.set_password(new_password)
        self.user_repo.update(user_id, {'password_hash': user.password_hash})
        
        # Revoke ALL tokens (security measure after password change)
        self.token_repo.revoke_user_tokens(user_id)
        
        # Revoke ALL JWT tokens by incrementing global token version
        try:
            self.jwt_service.revoke_user_tokens(str(user_id))
        except Exception as e:
            logger.error(f"Error revoking JWT tokens after password change: {e}")
        
        logger.info(f"Password changed for user: {user_id} - all sessions revoked")
    
    def _get_device_fingerprint(self) -> Optional[str]:
        """Generate device fingerprint from request for additional security"""
        if not request:
            return None
        
        fingerprint_data = [
            request.user_agent.string if request.user_agent else '',
            request.headers.get('Accept-Language', ''),
            request.headers.get('Sec-CH-UA', ''),
            request.remote_addr or ''
        ]
        
        fingerprint = '|'.join(fingerprint_data)
        if fingerprint and fingerprint.strip():
            import hashlib
            return hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
        
        return None
    
    def create_super_admin(self, email: str, password: str, phone: str) -> User:
        """Create super admin user"""
        user_data = {
            'email': email,
            'phone': phone,
            'role': 'super_admin',
            'is_super_admin': True,
            'is_active': True,
            'permissions': ['*']
        }
        
        user = self.user_repo.create(user_data)
        user.set_password(password)
        self.user_repo.update(user.id, {'password_hash': user.password_hash})
        
        # Send welcome email to super admin
        self._send_welcome_email_async(email, "Super", "Admin")
        
        logger.info(f"Super admin created: {email}")
        
        return user

    def send_verification_email(self, email: str) -> Dict[str, Any]:
        """Send verification email to user for organization registration"""
        from app.models.verification import EmailVerification
        from app.integrations.email.service import EmailService
        
        # Check if user already exists
        existing_user = self.user_repo.get_by_email(email)
        if existing_user:
            raise BusinessError('Email already registered. Please login instead.')
        
        # Check for existing unused verification token
        existing_token = EmailVerification.query.filter_by(
            email=email, 
            is_used=False
        ).first()
        
        # If token exists and is still valid, reuse it
        if existing_token and existing_token.expires_at > datetime.utcnow():
            token = existing_token.token
            logger.info(f"Reusing existing verification token for {email}")
        else:
            # Create new verification token
            verification = EmailVerification(email=email)
            db.session.add(verification)
            db.session.commit()
            token = verification.token
            logger.info(f"Created new verification token for {email}")
        
        # Send verification email
        email_service = EmailService()
        verification_url = f"{current_app.config['BASE_URL']}/verify-email?token={token}"
        
        success = email_service.send_verification_email(
            to_email=email,
            verification_url=verification_url
        )
        
        if not success:
            raise BusinessError('Failed to send verification email. Please try again.')
        
        logger.info(f"Verification email sent to {email}")
        
        return {
            'success': True, 
            'message': 'Verification email sent successfully',
            'email': email
        }
    
    def verify_email(self, token: str) -> Dict[str, Any]:
        """Verify email token and prepare for registration"""
        from app.models.verification import EmailVerification
        
        # Find verification record
        verification = EmailVerification.query.filter_by(token=token).first()
        
        if not verification:
            raise BusinessError('Invalid verification token')
        
        if not verification.is_valid():
            raise BusinessError('Verification token has expired. Please request a new one.')
        
        # Mark token as used
        verification.mark_used()
        db.session.commit()
        
        logger.info(f"Email verified for: {verification.email}")
        
        return {
            'success': True, 
            'email': verification.email,
            'message': 'Email verified successfully. Please complete your registration.'
        }
    
    def register_organization(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new organization and create admin user"""
        from app.models import Organization, OrganizationUser
        from app.models.verification import EmailVerification
        from app.integrations.email.service import EmailService
        
        email = data.get('email')
        password = data.get('password')
        first_name = data.get('first_name')
        last_name = data.get('last_name')
        phone = data.get('phone')
        org_name = data.get('organization_name')
        org_slug = data.get('organization_slug')
        
        # Validate email was verified
        verification = EmailVerification.query.filter_by(
            email=email, 
            is_used=True
        ).first()
        
        if not verification:
            raise BusinessError('Email not verified. Please verify your email first.')
        
        # Check if user already exists
        existing_user = self.user_repo.get_by_email(email)
        if existing_user:
            raise BusinessError('Email already registered. Please login.')
        
        # Check if organization slug is unique
        from app.models.organization import Organization as OrgModel
        existing_org = OrgModel.query.filter_by(slug=org_slug).first()
        if existing_org:
            raise BusinessError('Organization slug already taken. Please choose another.')
        
        try:
            # Create organization
            organization = OrgModel(
                name=org_name,
                slug=org_slug,
                business_type='isp',
                email=email,
                phone=phone,
                status='active',
                subscription_tier='professional',
                subscription_status='active',
                currency='KES',
                timezone='Africa/Nairobi'
            )
            db.session.add(organization)
            db.session.flush()  # Get organization ID
            
            # Create admin user
            user = User(
                email=email,
                phone=phone,
                first_name=first_name,
                last_name=last_name,
                organization_id=organization.id,
                role='org_admin',
                is_active=True,
                is_super_admin=False,
                permissions=['*']
            )
            
            # Set password
            user.set_password(password)
            
            # Add to session
            db.session.add(user)
            db.session.flush()
            
            # Create organization-user relationship
            org_user = OrganizationUser(
                organization_id=organization.id,
                user_id=user.id,
                role='org_admin',
                is_primary=True,
                invited_by=user.id
            )
            db.session.add(org_user)
            
            db.session.commit()
            
            # Send welcome email
            self._send_organization_welcome_email_async(
                email=email,
                first_name=first_name,
                last_name=last_name,
                organization_name=org_name,
                organization_slug=org_slug
            )
            
            logger.info(f"New organization registered: {org_name} (slug: {org_slug}) by {email}")
            
            return {
                'success': True,
                'user_id': str(user.id),
                'organization_id': str(organization.id),
                'organization_name': organization.name,
                'organization_slug': organization.slug,
                'email': user.email,
                'message': 'Organization registered successfully! Please login to continue.'
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Organization registration failed for {email}: {str(e)}", exc_info=True)
            raise BusinessError(f'Registration failed: {str(e)}')
    
    def _send_organization_welcome_email_async(self, email: str, first_name: str, 
                                                last_name: str, organization_name: str,
                                                organization_slug: str):
        """Send welcome email for organization registration"""
        from app.integrations.email.service import EmailService
        
        email_service = EmailService()
        full_name = f"{first_name} {last_name}".strip() if first_name or last_name else "User"
        
        def send():
            try:
                # Send welcome email
                email_service.send_welcome_email(
                    to_email=email,
                    first_name=full_name,
                    organization_name=organization_name
                )
                logger.info(f"Welcome email sent to {email} for organization {organization_name}")
            except Exception as e:
                logger.error(f"Failed to send welcome email to {email}: {e}", exc_info=True)
        
        # Send asynchronously to avoid blocking
        thread = threading.Thread(target=send, daemon=True)
        thread.start()
    
    def resend_verification_email(self, email: str) -> Dict[str, Any]:
        """Resend verification email"""
        from app.models.verification import EmailVerification
        from app.integrations.email.service import EmailService
        
        # Check if user already exists
        existing_user = self.user_repo.get_by_email(email)
        if existing_user:
            raise BusinessError('Email already registered. Please login instead.')
        
        # Check for existing verification token
        verification = EmailVerification.query.filter_by(
            email=email, 
            is_used=False
        ).first()
        
        if verification and verification.expires_at > datetime.utcnow():
            token = verification.token
        else:
            # Create new verification token
            if verification:
                verification.is_used = True  # Mark old as used
            new_verification = EmailVerification(email=email)
            db.session.add(new_verification)
            db.session.commit()
            token = new_verification.token
        
        # Send verification email
        email_service = EmailService()
        verification_url = f"{current_app.config['BASE_URL']}/verify-email?token={token}"
        
        success = email_service.send_verification_email(
            to_email=email,
            verification_url=verification_url
        )
        
        if not success:
            raise BusinessError('Failed to send verification email. Please try again.')
        
        logger.info(f"Resent verification email to {email}")
        
        return {
            'success': True,
            'message': 'Verification email resent successfully',
            'email': email
        }
    
    def check_email_availability(self, email: str) -> Dict[str, Any]:
        """Check if email is available for registration"""
        existing_user = self.user_repo.get_by_email(email)
        
        return {
            'available': existing_user is None,
            'message': 'Email is available' if not existing_user else 'Email is already registered'
        }
    
    def check_org_slug_availability(self, slug: str) -> Dict[str, Any]:
        """Check if organization slug is available"""
        from app.models.organization import Organization
        
        existing_org = Organization.query.filter_by(slug=slug).first()
        
        return {
            'available': existing_org is None,
            'message': 'Slug is available' if not existing_org else 'Slug is already taken'
        }