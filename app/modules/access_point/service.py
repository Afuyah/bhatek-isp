from typing import Dict, Any, Optional, List
from uuid import UUID

from app.modules.access_point.repository import AccessPointRepository
from app.models.access_point import AccessPoint
from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import ValidationError, NotFoundError

class AccessPointService:
    """Business logic for access point management"""
    
    def __init__(self):
        self.repository = AccessPointRepository()
        self.encryption = EncryptionService()
    
    def create_access_point(self, organization_id: UUID, data: Dict[str, Any]) -> AccessPoint:
        """Create new access point"""
        # Encrypt encryption key if provided
        if data.get('encryption_key'):
            data['encryption_key_encrypted'] = self.encryption.encrypt(data.pop('encryption_key'))
        
        ap_data = {
            'organization_id': organization_id,
            'router_id': data.get('router_id'),
            'hotspot_server_id': data.get('hotspot_server_id'),
            'name': data['name'],
            'mac_address': data['mac_address'],
            'ip_address': data.get('ip_address'),
            'ssid': data['ssid'],
            'ssid_visibility': data.get('ssid_visibility', True),
            'encryption_type': data.get('encryption_type', 'wpa2'),
            'encryption_key_encrypted': data.get('encryption_key_encrypted'),
            'channel': data.get('channel'),
            'frequency': data.get('frequency', '2.4ghz'),
            'location': data.get('location'),
            'settings': data.get('settings', {})
        }
        
        return self.repository.create(ap_data)
    
    def get_access_point(self, ap_id: UUID, organization_id: UUID) -> AccessPoint:
        """Get access point by ID"""
        ap = self.repository.get_by_id(ap_id, organization_id)
        if not ap:
            raise NotFoundError('Access point not found')
        return ap
    
    def update_access_point(self, ap_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> AccessPoint:
        """Update access point"""
        # Encrypt encryption key if provided
        if 'encryption_key' in data:
            data['encryption_key_encrypted'] = self.encryption.encrypt(data.pop('encryption_key'))
        
        ap = self.repository.update(ap_id, organization_id, data)
        if not ap:
            raise NotFoundError('Access point not found')
        return ap
    
    def delete_access_point(self, ap_id: UUID, organization_id: UUID):
        """Delete access point"""
        ap = self.repository.get_by_id(ap_id, organization_id)
        if not ap:
            raise NotFoundError('Access point not found')
        
        ap.is_active = False
        self.repository.update(ap_id, organization_id, {'is_active': False})
    
    def get_access_points_by_router(self, router_id: UUID, organization_id: UUID) -> List[AccessPoint]:
        """Get all access points for a router"""
        return self.repository.get_by_router(router_id, organization_id)
    
    def get_access_points_by_hotspot(self, hotspot_server_id: UUID, organization_id: UUID) -> List[AccessPoint]:
        """Get all access points for a hotspot server"""
        return self.repository.get_by_hotspot(hotspot_server_id, organization_id)
    
    def get_ap_stats(self, ap_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Get access point statistics"""
        ap = self.get_access_point(ap_id, organization_id)
        
        from app.modules.session.repository import SessionRepository
        session_repo = SessionRepository()
        
        active_sessions = session_repo.get_active_by_access_point(ap_id, organization_id)
        
        return {
            'access_point': ap.to_dict(),
            'active_sessions': len(active_sessions),
            'total_sessions_today': session_repo.count_by_access_point_today(ap_id, organization_id),
            'bandwidth_usage': session_repo.get_bandwidth_by_access_point(ap_id, organization_id)
        }