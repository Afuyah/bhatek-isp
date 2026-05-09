from typing import Dict, Any, Optional, List
from uuid import UUID
from datetime import datetime

from app.modules.access_point.repository import AccessPointRepository
from app.models.access_point import AccessPoint
from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import ValidationError, NotFoundError, BusinessError


class AccessPointService:
    """Business logic for access point management"""
    
    def __init__(self):
        self.repository = AccessPointRepository()
        self.encryption = EncryptionService()
        # CREATE    
    def create_access_point(self, organization_id: UUID, router_id: UUID, data: Dict[str, Any]) -> AccessPoint:
        """Create a new access point under a router"""
        
        # Validate required fields
        required_fields = ['name', 'mac_address', 'ssid', 'location']
        for field in required_fields:
            if not data.get(field):
                raise ValidationError(f'{field.replace("_", " ").title()} is required')
        
        # Check if MAC address already exists in this organization
        existing = self.repository.get_by_mac(data['mac_address'], organization_id)
        if existing:
            raise ValidationError(f'Access point with MAC address {data["mac_address"]} already exists')
        
        # Prepare data for repository
        ap_data = {
            'organization_id': organization_id,
            'router_id': router_id,
            'name': data['name'],
            'mac_address': data['mac_address'].upper(),  # Normalize MAC to uppercase
            'ssid': data['ssid'],
            'location': data['location'],
            'ip_address': data.get('ip_address'),
            'hotspot_server_id': data.get('hotspot_server_id'),
            'description': data.get('description'),
            'is_active': data.get('is_active', True),
            'settings': data.get('settings', {})
        }
        
        # Auto-detect fields can be set later by sync process
        # channel, frequency, encryption_type, status will be updated via sync
        
        ap = self.repository.create(ap_data)
        
        logger.info(f"Access point created: {ap.name} (MAC: {ap.mac_address}) for organization {organization_id}")
        return ap
        # READ    
    def get_access_point(self, ap_id: UUID, organization_id: UUID) -> AccessPoint:
        """Get access point by ID with tenant isolation"""
        ap = self.repository.get_by_id(ap_id, organization_id)
        if not ap:
            raise NotFoundError('Access point not found')
        return ap
    
    def get_access_points_by_router(self, router_id: UUID, organization_id: UUID, 
                                     skip: int = 0, limit: int = 100) -> List[AccessPoint]:
        """Get all access points for a router"""
        return self.repository.get_by_router(router_id, organization_id, skip, limit)
    
    def get_access_points_by_organization(self, organization_id: UUID, skip: int = 0, 
                                           limit: int = 100, status: str = None,
                                           router_id: UUID = None) -> List[AccessPoint]:
        """Get all access points for an organization with filters"""
        return self.repository.get_by_organization(organization_id, skip, limit, status, router_id)
    
    def get_active_access_points(self, organization_id: UUID) -> List[AccessPoint]:
        """Get all active access points for dropdowns"""
        return self.repository.get_all_active(organization_id)
    
    def get_online_access_points(self, organization_id: UUID) -> List[AccessPoint]:
        """Get all online access points for monitoring"""
        return self.repository.get_online_aps(organization_id)
    
    def get_offline_access_points(self, organization_id: UUID) -> List[AccessPoint]:
        """Get all offline access points for alerts"""
        return self.repository.get_offline_aps(organization_id)
        # UPDATE    
    def update_access_point(self, ap_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> AccessPoint:
        """Update an access point"""
        
        ap = self.repository.get_by_id(ap_id, organization_id, include_inactive=True)
        if not ap:
            raise NotFoundError('Access point not found')
        
        # Normalize MAC address if provided
        if 'mac_address' in data and data['mac_address']:
            data['mac_address'] = data['mac_address'].upper()
            
            # Check if new MAC already exists (excluding current AP)
            existing = self.repository.get_by_mac(data['mac_address'], organization_id)
            if existing and existing.id != ap_id:
                raise ValidationError(f'Access point with MAC address {data["mac_address"]} already exists')
        
        # Clean up empty values
        data = {k: v for k, v in data.items() if v is not None}
        
        updated_ap = self.repository.update(ap_id, organization_id, data)
        if not updated_ap:
            raise NotFoundError('Access point not found')
        
        logger.info(f"Access point updated: {ap_id}")
        return updated_ap
    
    def update_ap_status(self, ap_id: UUID, organization_id: UUID, status: str, 
                         error_message: str = None) -> bool:
        """Update access point status (called by health check)"""
        return self.repository.update_status(ap_id, organization_id, status, error_message)
        # DELETE    
    def delete_access_point(self, ap_id: UUID, organization_id: UUID, soft_delete: bool = True) -> bool:
        """Delete or deactivate an access point"""
        
        ap = self.repository.get_by_id(ap_id, organization_id, include_inactive=True)
        if not ap:
            raise NotFoundError('Access point not found')
        
        # Check if AP has active sessions
        from app.modules.session.repository import SessionRepository
        session_repo = SessionRepository()
        active_sessions = session_repo.get_active_by_access_point(ap_id, organization_id)
        
        if active_sessions and not soft_delete:
            raise BusinessError('Cannot delete access point with active sessions. Deactivate it instead.')
        
        result = self.repository.delete(ap_id, organization_id, soft_delete)
        
        action = 'deactivated' if soft_delete else 'deleted'
        logger.info(f"Access point {ap_id} {action}")
        return result
        # STATISTICS    
    def get_ap_stats(self, ap_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        """Get access point statistics (active sessions, usage)"""
        
        ap = self.get_access_point(ap_id, organization_id)
        
        # Placeholder for session module statistics
        # Will be implemented when session module is ready
        stats = {
            'access_point': ap.to_dict(),
            'active_sessions': 0,
            'total_sessions_today': 0,
            'bandwidth_usage_mb': 0,
            'uptime_percentage': 0
        }
        
        # TODO: Implement when session module is ready
        # from app.modules.session.repository import SessionRepository
        # session_repo = SessionRepository()
        # stats['active_sessions'] = session_repo.get_active_by_access_point(ap_id, organization_id).count()
        # stats['total_sessions_today'] = session_repo.count_by_access_point_today(ap_id, organization_id)
        # stats['bandwidth_usage_mb'] = session_repo.get_bandwidth_by_access_point(ap_id, organization_id)
        
        return stats
    
    def get_organization_stats(self, organization_id: UUID) -> Dict[str, Any]:
        """Get access point statistics for entire organization"""
        
        total = self.repository.count_by_organization(organization_id)
        online = self.repository.count_by_organization(organization_id, status='online')
        offline = self.repository.count_by_organization(organization_id, status='offline')
        
        return {
            'total': total,
            'online': online,
            'offline': offline,
            'unknown': total - online - offline
        }
        # BULK OPERATIONS    
    def bulk_update_status(self, organization_id: UUID, ap_ids: List[UUID], is_active: bool) -> int:
        """Bulk update access point active status"""
        count = 0
        for ap_id in ap_ids:
            try:
                self.update_access_point(ap_id, organization_id, {'is_active': is_active})
                count += 1
            except Exception as e:
                logger.warning(f"Failed to update AP {ap_id}: {e}")
        return count
        # UTILITY    
    def validate_mac_address(self, mac: str) -> bool:
        """Validate MAC address format"""
        import re
        # Accept formats: 00:11:22:33:44:55, 00-11-22-33-44-55, 001122334455
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$|^[0-9A-Fa-f]{12}$'
        return bool(re.match(pattern, mac))