from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.organization.service import OrganizationService
from app.modules.organization.schemas import (
    OrganizationCreateSchema, OrganizationUpdateSchema,
    OrganizationUserAddSchema, OrganizationUserRoleUpdateSchema
)
from app.core.security.jwt import token_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError, ValidationError as AppValidationError


class OrganizationController:
    """Organization controller"""
    
    def __init__(self):
        self.service = OrganizationService()
    
    @token_required
    def create_organization(self):
        """Create a new organization"""
        try:
            data = OrganizationCreateSchema().load(request.json)
            organization = self.service.create_organization(data, g.user_id)
            
            return jsonify({
                'success': True,
                'message': 'Organization created successfully',
                'organization': organization.to_dict()
            }), 201
            
        except ValidationError as e:
            return jsonify({
                'error': 'Validation error',
                'details': e.messages
            }), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Create organization error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_organization(self, org_id):
        """Get organization by ID"""
        try:
            org_id_uuid = UUID(org_id)
            organization = self.service.get_organization(org_id_uuid)
            
            # Check if user belongs to this organization
            user_orgs = self.service.get_organizations_by_user(g.user_id)
            if organization.id not in [o.id for o in user_orgs]:
                return jsonify({'error': 'Access denied'}), 403
            
            return jsonify(organization.to_dict()), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid organization ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get organization error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_organization_by_slug(self, slug):
        """Get organization by slug"""
        try:
            organization = self.service.get_organization_by_slug(slug)
            
            # Check if user belongs to this organization
            user_orgs = self.service.get_organizations_by_user(g.user_id)
            if organization.id not in [o.id for o in user_orgs]:
                return jsonify({'error': 'Access denied'}), 403
            
            return jsonify(organization.to_dict()), 200
            
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get organization by slug error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def update_organization(self, org_id):
        """Update organization"""
        try:
            org_id_uuid = UUID(org_id)
            data = OrganizationUpdateSchema().load(request.json)
            
            # Check if user has admin permission
            user_orgs = self.service.get_organizations_by_user(g.user_id)
            if org_id_uuid not in [o.id for o in user_orgs]:
                return jsonify({'error': 'Access denied'}), 403
            
            # Check if user is admin of this organization
            org_user = self.service.org_user_repo.get_organization_user(org_id_uuid, g.user_id)
            if not org_user or org_user.role not in ['org_admin', 'super_admin']:
                return jsonify({'error': 'Admin permission required'}), 403
            
            organization = self.service.update_organization(org_id_uuid, data)
            
            return jsonify({
                'success': True,
                'message': 'Organization updated successfully',
                'organization': organization.to_dict()
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid organization ID format'}), 400
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Update organization error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def delete_organization(self, org_id):
        """Delete organization (soft delete)"""
        try:
            org_id_uuid = UUID(org_id)
            
            # Check if user has admin permission
            user_orgs = self.service.get_organizations_by_user(g.user_id)
            if org_id_uuid not in [o.id for o in user_orgs]:
                return jsonify({'error': 'Access denied'}), 403
            
            # Check if user is primary admin
            org_users = self.service.get_organization_users(org_id_uuid)
            user_org_role = next((u for u in org_users if u.user_id == g.user_id), None)
            
            if not user_org_role or not (user_org_role.role == 'org_admin' and user_org_role.is_primary):
                return jsonify({'error': 'Only primary admin can delete organization'}), 403
            
            self.service.delete_organization(org_id_uuid)
            
            return jsonify({
                'success': True,
                'message': 'Organization deleted successfully'
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid organization ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Delete organization error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def list_organizations(self):
        """List organizations for current user"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            organizations = self.service.get_organizations_by_user(g.user_id)
            
            # Apply pagination
            total = len(organizations)
            paginated_orgs = organizations[skip:skip + per_page]
            
            return jsonify({
                'organizations': [org.to_dict() for org in paginated_orgs],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
            
        except Exception as e:
            logger.error(f"List organizations error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_organization_users(self, org_id):
        """Get all users in organization"""
        try:
            org_id_uuid = UUID(org_id)
            
            # Check access
            user_orgs = self.service.get_organizations_by_user(g.user_id)
            if org_id_uuid not in [o.id for o in user_orgs]:
                return jsonify({'error': 'Access denied'}), 403
            
            org_users = self.service.get_organization_users(org_id_uuid)
            
            return jsonify({
                'users': [
                    {
                        'user_id': str(ou.user_id),
                        'email': ou.user.email if ou.user else None,
                        'first_name': ou.user.first_name if ou.user else None,
                        'last_name': ou.user.last_name if ou.user else None,
                        'role': ou.role,
                        'is_primary': ou.is_primary,
                        'joined_at': ou.joined_at.isoformat() if ou.joined_at else None
                    }
                    for ou in org_users
                ]
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid organization ID format'}), 400
        except Exception as e:
            logger.error(f"Get organization users error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def add_user_to_organization(self, org_id):
        """Add user to organization"""
        try:
            org_id_uuid = UUID(org_id)
            data = OrganizationUserAddSchema().load(request.json)
            
            # Check if current user has admin rights
            org_users = self.service.get_organization_users(org_id_uuid)
            current_user_role = next((u for u in org_users if u.user_id == g.user_id), None)
            
            if not current_user_role or current_user_role.role not in ['org_admin', 'super_admin']:
                return jsonify({'error': 'Admin permission required'}), 403
            
            org_user = self.service.add_user_to_organization(
                org_id=org_id_uuid,
                user_id=data['user_id'],
                role=data['role'],
                invited_by=g.user_id
            )
            
            return jsonify({
                'success': True,
                'message': 'User added to organization successfully',
                'organization_user': {
                    'user_id': str(org_user.user_id),
                    'role': org_user.role,
                    'is_primary': org_user.is_primary
                }
            }), 201
            
        except ValueError:
            return jsonify({'error': 'Invalid organization ID format'}), 400
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Add user to organization error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def remove_user_from_organization(self, org_id, user_id):
        """Remove user from organization"""
        try:
            org_id_uuid = UUID(org_id)
            user_id_uuid = UUID(user_id)
            
            # Check if current user has admin rights
            org_users = self.service.get_organization_users(org_id_uuid)
            current_user_role = next((u for u in org_users if u.user_id == g.user_id), None)
            
            if not current_user_role or current_user_role.role not in ['org_admin', 'super_admin']:
                return jsonify({'error': 'Admin permission required'}), 403
            
            # Cannot remove self if last admin
            if user_id_uuid == g.user_id:
                primary_admins = [u for u in org_users if u.role == 'org_admin' and u.is_primary]
                if len(primary_admins) == 1 and primary_admins[0].user_id == g.user_id:
                    return jsonify({'error': 'Cannot remove yourself as the last primary admin'}), 400
            
            result = self.service.remove_user_from_organization(org_id_uuid, user_id_uuid)
            
            if result:
                return jsonify({
                    'success': True,
                    'message': 'User removed from organization successfully'
                }), 200
            else:
                return jsonify({'error': 'User not found in organization'}), 404
            
        except ValueError:
            return jsonify({'error': 'Invalid ID format'}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Remove user from organization error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def update_user_role(self, org_id, user_id):
        """Update user's role in organization"""
        try:
            org_id_uuid = UUID(org_id)
            user_id_uuid = UUID(user_id)
            data = OrganizationUserRoleUpdateSchema().load(request.json)
            
            # Check if current user has admin rights
            org_users = self.service.get_organization_users(org_id_uuid)
            current_user_role = next((u for u in org_users if u.user_id == g.user_id), None)
            
            if not current_user_role or current_user_role.role not in ['org_admin', 'super_admin']:
                return jsonify({'error': 'Admin permission required'}), 403
            
            org_user = self.service.update_user_role(org_id_uuid, user_id_uuid, data['role'])
            
            return jsonify({
                'success': True,
                'message': 'User role updated successfully',
                'organization_user': {
                    'user_id': str(org_user.user_id),
                    'role': org_user.role,
                    'is_primary': org_user.is_primary
                }
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid ID format'}), 400
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Update user role error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_organization_stats(self, org_id):
        """Get organization statistics"""
        try:
            org_id_uuid = UUID(org_id)
            
            # Check access
            user_orgs = self.service.get_organizations_by_user(g.user_id)
            if org_id_uuid not in [o.id for o in user_orgs]:
                return jsonify({'error': 'Access denied'}), 403
            
            stats = self.service.get_organization_stats(org_id_uuid)
            
            return jsonify(stats), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid organization ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get organization stats error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def switch_organization(self, org_id):
        """Switch current working organization"""
        try:
            org_id_uuid = UUID(org_id)
            
            # Check if user belongs to this organization
            user_orgs = self.service.get_organizations_by_user(g.user_id)
            if org_id_uuid not in [o.id for o in user_orgs]:
                return jsonify({'error': 'Access denied'}), 403
            
            # Update session or JWT with current organization
            # This can be stored in Redis or JWT payload
            # For now, just return success
            return jsonify({
                'success': True,
                'message': 'Switched to organization',
                'organization_id': str(org_id_uuid)
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid organization ID format'}), 400
        except Exception as e:
            logger.error(f"Switch organization error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500