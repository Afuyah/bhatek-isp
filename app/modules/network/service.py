from typing import Dict, Any, Optional, List
from uuid import UUID

from app.modules.network.repository import NetworkRepository
from app.models.network import Network
from app.core.logging.logger import logger
from app.core.exceptions.handlers import ValidationError, NotFoundError

class NetworkService:
    """Business logic for network management"""
    
    def __init__(self):
        self.repository = NetworkRepository()
    
    def create_network(self, organization_id: UUID, data: Dict[str, Any]) -> Network:
        """Create new network"""
        network_data = {
            'organization_id': organization_id,
            'name': data['name'],
            'type': data['type'],
            'description': data.get('description'),
            'settings': data.get('settings', {})
        }
        
        return self.repository.create(network_data)
    
    def get_network(self, network_id: UUID, organization_id: UUID) -> Network:
        """Get network by ID"""
        network = self.repository.get_by_id(network_id, organization_id)
        if not network:
            raise NotFoundError('Network not found')
        return network
    
    def update_network(self, network_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Network:
        """Update network"""
        network = self.repository.update(network_id, organization_id, data)
        if not network:
            raise NotFoundError('Network not found')
        return network
    
    def delete_network(self, network_id: UUID, organization_id: UUID):
        """Delete network"""
        if not self.repository.delete(network_id, organization_id):
            raise NotFoundError('Network not found')
    
    def list_networks(self, organization_id: UUID, skip: int = 0, limit: int = 100) -> List[Network]:
        """List networks for organization"""
        return self.repository.get_by_organization(organization_id, skip, limit)