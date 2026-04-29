from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
import re

from app.modules.organization.repository import OrganizationRepository, OrganizationUserRepository
from app.models.organization import Organization, OrganizationUser
from app.modules.auth.repository import UserRepository
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError, ValidationError

class OrganizationService:
    """Business logic for organization management"""
    
    def __init__(self):
        self.org_repo = OrganizationRepository()
        self.org_user_repo = OrganizationUserRepository()
        self.user_repo = UserRepository()
    
    def create_organization(self, data: Dict[str, Any], owner_user_id: UUID) -> Organization:
        """Create a new organization with owner as primary admin"""
        
        # Generate slug from name if not provided
        if not data.get('slug'):
            data['slug'] = self._generate_slug(data['name'])
        else:
            # Validate slug format
            if not re.match(r'^[a-z0-9-]+$', data['slug']):
                raise ValidationError('Slug must contain only lowercase letters, numbers, and hyphens')
            
            # Check if slug is unique
            if self.org_repo.get_by_slug(data['slug']):
                raise ValidationError(f'Organization with slug "{data["slug"]}" already exists')
        
        # Create organization
        org_data = {
            'name': data['name'],
            'slug': data['slug'],
            'business_type': data.get('business_type', 'custom'),
            'email': data.get('email'),
            'phone': data.get('phone'),
            'address': data.get('address'),
            'city': data.get('city'),
            'country': data.get('country'),
            'timezone': data.get('timezone', 'Africa/Nairobi'),
            'currency': data.get('currency', 'KES'),
            'settings': data.get('settings', {}),
            'status': 'active'
        }
        
        organization = self.org_repo.create(org_data)
        
        # Add owner as primary organization user
        self.org_user_repo.add_user(
            organization_id=organization.id,
            user_id=owner_user_id,
            role='org_admin',
            is_primary=True,
            invited_by=owner_user_id
        )
        
        logger.info(f"Organization created: {organization.name} (ID: {organization.id}) by user {owner_user_id}")
        
        return organization
    
    def get_organization(self, org_id: UUID) -> Optional[Organization]:
        """Get organization by ID"""
        org = self.org_repo.get_by_id(org_id)
        if not org:
            raise NotFoundError(f'Organization not found: {org_id}')
        return org
    
    def get_organization_by_slug(self, slug: str) -> Optional[Organization]:
        """Get organization by slug"""
        org = self.org_repo.get_by_slug(slug)
        if not org:
            raise NotFoundError(f'Organization not found: {slug}')
        return org
    
    def update_organization(self, org_id: UUID, data: Dict[str, Any]) -> Organization:
        """Update organization"""
        
        # Check if slug is being updated and validate uniqueness
        if 'slug' in data:
            if not re.match(r'^[a-z0-9-]+$', data['slug']):
                raise ValidationError('Slug must contain only lowercase letters, numbers, and hyphens')
            
            existing = self.org_repo.get_by_slug(data['slug'])
            if existing and existing.id != org_id:
                raise ValidationError(f'Organization with slug "{data["slug"]}" already exists')
        
        organization = self.org_repo.update(org_id, data)
        if not organization:
            raise NotFoundError(f'Organization not found: {org_id}')
        
        logger.info(f"Organization updated: {organization.id}")
        return organization
    
    def delete_organization(self, org_id: UUID) -> bool:
        """Soft delete organization"""
        result = self.org_repo.delete(org_id)
        if result:
            logger.info(f"Organization deleted: {org_id}")
        return result
    
    def get_organizations_by_user(self, user_id: UUID) -> List[Organization]:
        """Get all organizations a user belongs to"""
        return self.org_repo.get_by_user_id(user_id)
    
    def add_user_to_organization(
        self, 
        org_id: UUID, 
        user_id: UUID, 
        role: str, 
        invited_by: UUID
    ) -> OrganizationUser:
        """Add user to organization"""
        
        # Check if user exists
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise NotFoundError(f'User not found: {user_id}')
        
        # Check if organization exists
        org = self.get_organization(org_id)
        if not org:
            raise NotFoundError(f'Organization not found: {org_id}')
        
        # Check if user already belongs to organization
        existing = self.org_user_repo.get_organization_user(org_id, user_id)
        if existing:
            raise BusinessError('User already belongs to this organization')
        
        # Add user
        org_user = self.org_user_repo.add_user(
            organization_id=org_id,
            user_id=user_id,
            role=role,
            is_primary=False,
            invited_by=invited_by
        )
        
        logger.info(f"User {user_id} added to organization {org_id} with role {role}")
        return org_user
    
    def remove_user_from_organization(self, org_id: UUID, user_id: UUID) -> bool:
        """Remove user from organization"""
        
        # Cannot remove the last primary admin
        org_users = self.org_user_repo.get_organization_users(org_id)
        primary_admins = [u for u in org_users if u.role == 'org_admin' and u.is_primary]
        
        if len(primary_admins) == 1 and primary_admins[0].user_id == user_id:
            raise BusinessError('Cannot remove the last primary admin of the organization')
        
        result = self.org_user_repo.remove_user(org_id, user_id)
        if result:
            logger.info(f"User {user_id} removed from organization {org_id}")
        
        return result
    
    def update_user_role(self, org_id: UUID, user_id: UUID, role: str) -> OrganizationUser:
        """Update user's role in organization"""
        
        org_user = self.org_user_repo.update_user_role(org_id, user_id, role)
        if not org_user:
            raise NotFoundError(f'User not found in organization')
        
        logger.info(f"User {user_id} role updated to {role} in organization {org_id}")
        return org_user
    
    def get_organization_users(self, org_id: UUID) -> List[OrganizationUser]:
        """Get all users in organization"""
        return self.org_user_repo.get_organization_users(org_id)
    
    def get_organization_stats(self, org_id: UUID) -> Dict[str, Any]:
        """Get organization statistics"""
        
        org = self.get_organization(org_id)
        
        # Get counts from related repositories
        # These will be implemented as other modules are built
        stats = {
            'organization_id': str(org.id),
            'name': org.name,
            'slug': org.slug,
            'status': org.status,
            'subscription_tier': org.subscription_tier,
            'subscription_status': org.subscription_status,
            'subscription_expires_at': org.subscription_expires_at.isoformat() if org.subscription_expires_at else None,
            'total_users': self.org_user_repo.count_users(org_id),
            'total_routers': 0,  # Will be filled by router module
            'total_subscribers': 0,  # Will be filled by subscriber module
            'total_revenue_monthly': 0,  # Will be filled by billing module
            'active_sessions': 0  # Will be filled by session module
        }
        
        return stats
    
    def _generate_slug(self, name: str) -> str:
        """Generate a URL-friendly slug from organization name"""
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        
        # Check if slug exists and add suffix if needed
        original_slug = slug
        counter = 1
        while self.org_repo.get_by_slug(slug):
            slug = f"{original_slug}-{counter}"
            counter += 1
        
        return slug