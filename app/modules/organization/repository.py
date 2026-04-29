from typing import Optional, List, Dict, Any
from uuid import UUID
from sqlalchemy import and_, desc, func
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

from app.models.organization import Organization, OrganizationUser, OrganizationSetting
from app.core.database.session import db
from app.core.logging.logger import logger

class OrganizationRepository:
    """Data access layer for Organization operations"""
    
    def __init__(self):
        self.model = Organization
    
    def get_by_id(self, org_id: UUID) -> Optional[Organization]:
        """Get organization by ID"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == org_id,
                    self.model.status != 'deleted'
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_slug(self, slug: str) -> Optional[Organization]:
        """Get organization by slug"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.slug == slug,
                    self.model.status != 'deleted'
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_slug: {e}", exc_info=True)
            raise
    
    def get_by_user_id(self, user_id: UUID) -> List[Organization]:
        """Get all organizations for a user"""
        try:
            return self.model.query.join(
                OrganizationUser,
                OrganizationUser.organization_id == self.model.id
            ).filter(
                and_(
                    OrganizationUser.user_id == user_id,
                    self.model.status == 'active'
                )
            ).order_by(self.model.created_at.desc()).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_user_id: {e}", exc_info=True)
            raise
    
    def get_all(self, skip: int = 0, limit: int = 100, filters: Dict = None) -> List[Organization]:
        """Get all organizations with pagination"""
        try:
            query = self.model.query.filter(self.model.status != 'deleted')
            
            if filters:
                if filters.get('status'):
                    query = query.filter(self.model.status == filters['status'])
                if filters.get('business_type'):
                    query = query.filter(self.model.business_type == filters['business_type'])
                if filters.get('search'):
                    search = f"%{filters['search']}%"
                    query = query.filter(
                        self.model.name.ilike(search) | self.model.slug.ilike(search)
                    )
            
            return query.order_by(self.model.created_at.desc()).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_all: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Organization:
        """Create new organization"""
        try:
            organization = self.model(**data)
            db.session.add(organization)
            db.session.commit()
            return organization
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, org_id: UUID, data: Dict[str, Any]) -> Optional[Organization]:
        """Update organization"""
        try:
            organization = self.get_by_id(org_id)
            if not organization:
                return None
            
            for key, value in data.items():
                if hasattr(organization, key) and value is not None:
                    setattr(organization, key, value)
            
            db.session.commit()
            return organization
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise
    
    def delete(self, org_id: UUID, soft_delete: bool = True) -> bool:
        """Delete organization (soft delete by default)"""
        try:
            organization = self.get_by_id(org_id)
            if not organization:
                return False
            
            if soft_delete:
                organization.status = 'deleted'
            else:
                db.session.delete(organization)
            
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise
    
    def count(self, filters: Dict = None) -> int:
        """Count organizations with filters"""
        try:
            query = self.model.query.filter(self.model.status != 'deleted')
            if filters and filters.get('status'):
                query = query.filter(self.model.status == filters['status'])
            return query.count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count: {e}", exc_info=True)
            raise


class OrganizationUserRepository:
    """Data access layer for OrganizationUser operations"""
    
    def __init__(self):
        self.model = OrganizationUser
    
    def get_organization_user(self, org_id: UUID, user_id: UUID) -> Optional[OrganizationUser]:
        """Get organization-user relationship"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == org_id,
                    self.model.user_id == user_id
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_organization_user: {e}", exc_info=True)
            raise
    
    def get_organization_users(self, org_id: UUID) -> List[OrganizationUser]:
        """Get all users in organization"""
        try:
            return self.model.query.filter(
                self.model.organization_id == org_id
            ).order_by(self.model.joined_at).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_organization_users: {e}", exc_info=True)
            raise
    
    def get_user_organizations(self, user_id: UUID) -> List[OrganizationUser]:
        """Get all organizations for a user"""
        try:
            return self.model.query.filter(
                self.model.user_id == user_id
            ).order_by(self.model.joined_at.desc()).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_user_organizations: {e}", exc_info=True)
            raise
    
    def add_user(self, organization_id: UUID, user_id: UUID, role: str, 
                 is_primary: bool = False, invited_by: UUID = None) -> OrganizationUser:
        """Add user to organization"""
        try:
            org_user = self.model(
                organization_id=organization_id,
                user_id=user_id,
                role=role,
                is_primary=is_primary,
                invited_by=invited_by
            )
            db.session.add(org_user)
            db.session.commit()
            return org_user
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in add_user: {e}", exc_info=True)
            raise
    
    def update_user_role(self, org_id: UUID, user_id: UUID, role: str) -> Optional[OrganizationUser]:
        """Update user's role in organization"""
        try:
            org_user = self.get_organization_user(org_id, user_id)
            if not org_user:
                return None
            
            org_user.role = role
            db.session.commit()
            return org_user
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_user_role: {e}", exc_info=True)
            raise
    
    def remove_user(self, org_id: UUID, user_id: UUID) -> bool:
        """Remove user from organization"""
        try:
            org_user = self.get_organization_user(org_id, user_id)
            if not org_user:
                return False
            
            db.session.delete(org_user)
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in remove_user: {e}", exc_info=True)
            raise
    
    def count_users(self, org_id: UUID) -> int:
        """Count users in organization"""
        try:
            return self.model.query.filter(
                self.model.organization_id == org_id
            ).count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_users: {e}", exc_info=True)
            raise
    
    def get_admins(self, org_id: UUID) -> List[OrganizationUser]:
        """Get all admin users in organization"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == org_id,
                    self.model.role == 'org_admin'
                )
            ).order_by(self.model.joined_at).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_admins: {e}", exc_info=True)
            raise
    
    def get_primary_admin(self, org_id: UUID) -> Optional[OrganizationUser]:
        """Get primary admin of organization"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == org_id,
                    self.model.is_primary == True,
                    self.model.role == 'org_admin'
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_primary_admin: {e}", exc_info=True)
            raise
    
    def transfer_primary_admin(self, org_id: UUID, from_user_id: UUID, to_user_id: UUID) -> bool:
        """Transfer primary admin role to another user"""
        try:
            # Remove primary from current
            current_primary = self.get_primary_admin(org_id)
            if current_primary:
                current_primary.is_primary = False
            
            # Set new primary
            new_primary = self.get_organization_user(org_id, to_user_id)
            if not new_primary:
                return False
            
            new_primary.is_primary = True
            new_primary.role = 'org_admin'
            
            db.session.commit()
            logger.info(f"Primary admin transferred from {from_user_id} to {to_user_id} in org {org_id}")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in transfer_primary_admin: {e}", exc_info=True)
            raise
    
    def get_users_by_role(self, org_id: UUID, role: str) -> List[OrganizationUser]:
        """Get users by role in organization"""
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == org_id,
                    self.model.role == role
                )
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_users_by_role: {e}", exc_info=True)
            raise
    
    def user_has_role(self, org_id: UUID, user_id: UUID, role: str) -> bool:
        """Check if user has specific role in organization"""
        try:
            org_user = self.get_organization_user(org_id, user_id)
            return org_user is not None and org_user.role == role
        except SQLAlchemyError as e:
            logger.error(f"Database error in user_has_role: {e}", exc_info=True)
            raise
    
    def bulk_add_users(self, org_id: UUID, users_data: List[Dict[str, Any]], invited_by: UUID) -> List[OrganizationUser]:
        """Bulk add users to organization"""
        try:
            org_users = []
            for user_data in users_data:
                org_user = self.model(
                    organization_id=org_id,
                    user_id=user_data['user_id'],
                    role=user_data.get('role', 'staff'),
                    is_primary=False,
                    invited_by=invited_by
                )
                org_users.append(org_user)
            
            db.session.add_all(org_users)
            db.session.commit()
            
            logger.info(f"Bulk added {len(org_users)} users to organization {org_id}")
            return org_users
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in bulk_add_users: {e}", exc_info=True)
            raise
    
    def get_user_count_by_role(self, org_id: UUID) -> Dict[str, int]:
        """Get user count grouped by role"""
        try:
            results = db.session.query(
                self.model.role,
                func.count(self.model.user_id).label('count')
            ).filter(
                self.model.organization_id == org_id
            ).group_by(self.model.role).all()
            
            return {result.role: result.count for result in results}
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_user_count_by_role: {e}", exc_info=True)
            raise


class OrganizationSettingRepository:
    """Data access layer for OrganizationSetting operations"""
    
    def __init__(self):
        self.model = OrganizationSetting
    
    def get_setting(self, org_id: UUID, key: str) -> Optional[Any]:
        """Get a specific setting value"""
        try:
            setting = self.model.query.filter(
                and_(
                    self.model.organization_id == org_id,
                    self.model.key == key
                )
            ).first()
            return setting.value if setting else None
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_setting: {e}", exc_info=True)
            raise
    
    def get_all_settings(self, org_id: UUID) -> Dict[str, Any]:
        """Get all settings for an organization"""
        try:
            settings = self.model.query.filter(
                self.model.organization_id == org_id
            ).all()
            return {setting.key: setting.value for setting in settings}
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_all_settings: {e}", exc_info=True)
            raise
    
    def set_setting(self, org_id: UUID, key: str, value: Any, is_encrypted: bool = False) -> OrganizationSetting:
        """Set a setting value (create or update)"""
        try:
            setting = self.model.query.filter(
                and_(
                    self.model.organization_id == org_id,
                    self.model.key == key
                )
            ).first()
            
            if setting:
                setting.value = value
                setting.is_encrypted = is_encrypted
            else:
                setting = self.model(
                    organization_id=org_id,
                    key=key,
                    value=value,
                    is_encrypted=is_encrypted
                )
                db.session.add(setting)
            
            db.session.commit()
            logger.info(f"Setting {key} updated for organization {org_id}")
            return setting
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in set_setting: {e}", exc_info=True)
            raise
    
    def delete_setting(self, org_id: UUID, key: str) -> bool:
        """Delete a setting"""
        try:
            setting = self.model.query.filter(
                and_(
                    self.model.organization_id == org_id,
                    self.model.key == key
                )
            ).first()
            
            if setting:
                db.session.delete(setting)
                db.session.commit()
                logger.info(f"Setting {key} deleted for organization {org_id}")
                return True
            
            return False
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete_setting: {e}", exc_info=True)
            raise
    
    def bulk_set_settings(self, org_id: UUID, settings: Dict[str, Any]) -> List[OrganizationSetting]:
        """Bulk set multiple settings"""
        try:
            created_settings = []
            for key, value in settings.items():
                setting = self.set_setting(org_id, key, value)
                created_settings.append(setting)
            
            db.session.commit()
            logger.info(f"Bulk set {len(created_settings)} settings for organization {org_id}")
            return created_settings
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in bulk_set_settings: {e}", exc_info=True)
            raise