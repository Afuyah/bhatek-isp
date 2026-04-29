import re
from typing import Dict, Any
from app.core.exceptions import ValidationError

class AuthValidator:
  
    @staticmethod
    def validate_phone(phone: str) -> bool:
        """Validate Kenyan phone number"""
        pattern = r'^(254|0)[17]\d{8}$'
        if not re.match(pattern, phone):
            raise ValidationError('Invalid phone number format')
        return True
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, email):
            raise ValidationError('Invalid email format')
        return True
    
    @staticmethod
    def validate_password_strength(password: str) -> bool:
        """Validate password strength"""
        if len(password) < 6:
            raise ValidationError('Password must be at least 6 characters')
        if not any(c.isupper() for c in password):
            raise ValidationError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in password):
            raise ValidationError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in password):
            raise ValidationError('Password must contain at least one number')
        return True
