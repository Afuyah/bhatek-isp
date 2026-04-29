from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timedelta
import secrets

from app.core.database.base import BaseModel

class EmailVerification(BaseModel):
    __tablename__ = 'email_verifications'
    
    email = Column(String(255), nullable=False, index=True)
    token = Column(String(100), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False)
    
    def __init__(self, email, **kwargs):
        super().__init__(**kwargs)
        self.email = email
        self.token = secrets.token_urlsafe(32)
        self.expires_at = datetime.utcnow() + timedelta(hours=24)
    
    def is_valid(self):
        return not self.is_used and self.expires_at > datetime.utcnow()
    
    def mark_used(self):
        self.is_used = True