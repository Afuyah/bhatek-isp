from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
import re

from app.modules.network.repository import NetworkRepository
from app.models.network import Network
from app.modules.organization.service import OrganizationService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError, ValidationError


class NetworkService:
 
    def __init__(self):
        self.network_repo = NetworkRepository()
        self.org_service = OrganizationService()
    
    def _generate_slug(self, name: str, organization_id: UUID) -> str:
        """Generate a unique slug for network"""
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        
        # Check uniqueness within organization
        original_slug = slug
        counter = 1
        while self.network_repo.get_by_slug(slug, organization_id):
            slug = f"{original_slug}-{counter}"
            counter += 1
        
        return slug
    
    def create_network(self, organization_id: UUID, data: Dict[str, Any]) -> Network:
        """Create a new network within an organization"""
        
        # Verify organization exists
        org = self.org_service.get_organization(organization_id)
        if not org:
            raise NotFoundError(f'Organization not found: {organization_id}')
        
        # Generate slug if not provided
        if not data.get('slug'):
            data['slug'] = self._generate_slug(data['name'], organization_id)
        else:
            # Validate slug format
            if not re.match(r'^[a-z0-9-]+$', data['slug']):
                raise ValidationError('Slug must contain only lowercase letters, numbers, and hyphens')
            
            # Check uniqueness within organization
            existing = self.network_repo.get_by_slug(data['slug'], organization_id)
            if existing:
                raise ValidationError(f'Network with slug "{data["slug"]}" already exists in this organization')
        
        # Create network
        network_data = {
            'organization_id': organization_id,
            'name': data['name'],
            'slug': data['slug'],
            'type': data.get('type', 'hybrid'),
            'description': data.get('description'),
            'settings': data.get('settings', {}),
            'is_active': data.get('is_active', True)
        }
        
        network = self.network_repo.create(network_data)
        
        logger.info(f"Network created: {network.name} in organization {organization_id}")
        return network
    
    def get_network(self, network_id: UUID, organization_id: UUID) -> Network:
        """Get network by ID"""
        network = self.network_repo.get_by_id(network_id, organization_id)
        if not network:
            raise NotFoundError(f'Network not found: {network_id}')
        return network
    
    def get_network_by_slug(self, slug: str, organization_id: UUID) -> Network:
        """Get network by slug"""
        network = self.network_repo.get_by_slug(slug, organization_id)
        if not network:
            raise NotFoundError(f'Network not found: {slug}')
        return network
    
    def update_network(self, network_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Network:
        """Update network"""
        
        # Check if network exists
        network = self.get_network(network_id, organization_id)
        
        # Check slug uniqueness if being updated
        if 'slug' in data:
            if not re.match(r'^[a-z0-9-]+$', data['slug']):
                raise ValidationError('Slug must contain only lowercase letters, numbers, and hyphens')
            
            existing = self.network_repo.get_by_slug(data['slug'], organization_id)
            if existing and existing.id != network_id:
                raise ValidationError(f'Network with slug "{data["slug"]}" already exists')
        
        # Update network
        updated_network = self.network_repo.update(network_id, organization_id, data)
        if not updated_network:
            raise NotFoundError(f'Network not found: {network_id}')
        
        logger.info(f"Network updated: {network_id}")
        return updated_network
    
    def delete_network(self, network_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete network"""
        result = self.network_repo.delete(network_id, organization_id, soft_delete)
        if not result:
            raise BusinessError('Cannot delete network with associated routers. Remove routers first.')
        
        logger.info(f"Network deleted: {network_id}")
        return result
    
    def get_organization_networks(self, organization_id: UUID, skip: int = 0, 
                                  limit: int = 100, filters: Dict = None) -> List[Network]:
        """Get all networks for an organization"""
        return self.network_repo.get_by_organization(organization_id, skip, limit, filters)
    
    def get_active_networks(self, organization_id: UUID) -> List[Network]:
        """Get all active networks for an organization"""
        return self.network_repo.get_all_active(organization_id)
    
    def get_network_stats(self, organization_id: UUID) -> Dict[str, Any]:
        """Get network statistics"""
        return self.network_repo.get_network_stats(organization_id)
    
    def bulk_update_status(self, organization_id: UUID, network_ids: List[UUID], is_active: bool) -> int:
        """Bulk update network status"""
        return self.network_repo.bulk_update_status(organization_id, network_ids, is_active)