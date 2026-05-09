from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.access_point.service import AccessPointService
from app.modules.access_point.schemas import AccessPointCreateSchema, AccessPointUpdateSchema
from app.core.security.jwt import token_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, ValidationError as AppValidationError, BusinessError


class AccessPointController:
    """Access Point controller for API endpoints"""
    
    def __init__(self):
        self.service = AccessPointService()
        # CREATE    
    @token_required
    def create(self):
        """Create a new access point"""
        try:
            data = AccessPointCreateSchema().load(request.json)
            
            # Validate router_id is provided
            if not data.get('router_id'):
                return jsonify({'error': 'router_id is required'}), 400
            
            router_uuid = UUID(data['router_id'])
            
            ap = self.service.create_access_point(
                organization_id=g.organization_id,
                router_id=router_uuid,
                data=data
            )
            
            return jsonify({
                'success': True,
                'access_point': ap.to_dict(),
                'message': 'Access point created successfully'
            }), 201
            
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"Create access point error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
        # READ    
    @token_required
    def get(self, ap_id):
        """Get access point by ID"""
        try:
            ap_uuid = UUID(ap_id)
            ap = self.service.get_access_point(ap_uuid, g.organization_id)
            return jsonify(ap.to_dict()), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid access point ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get access point error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def list(self):
        """List access points for current organization with filters"""
        try:
            # Pagination
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            # Filters
            status = request.args.get('status')
            router_id = request.args.get('router_id')
            
            # Convert router_id to UUID if provided
            router_uuid = None
            if router_id:
                try:
                    router_uuid = UUID(router_id)
                except ValueError:
                    return jsonify({'error': 'Invalid router_id format'}), 400
            
            # Get access points via service
            aps = self.service.get_access_points_by_organization(
                organization_id=g.organization_id,
                skip=skip,
                limit=per_page,
                status=status,
                router_id=router_uuid
            )
            
            # Get total count
            total = self.service.repository.count_by_organization(
                g.organization_id, 
                status=status
            )
            
            return jsonify({
                'access_points': [ap.to_dict() for ap in aps],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
            
        except Exception as e:
            logger.error(f"List access points error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_by_router(self, router_id):
        """Get all access points for a specific router"""
        try:
            router_uuid = UUID(router_id)
            aps = self.service.get_access_points_by_router(router_uuid, g.organization_id)
            
            return jsonify({
                'access_points': [ap.to_dict() for ap in aps]
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except Exception as e:
            logger.error(f"Get access points by router error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_active(self):
        """Get active access points for dropdowns"""
        try:
            aps = self.service.get_active_access_points(g.organization_id)
            
            return jsonify({
                'access_points': [
                    {'id': str(ap.id), 'name': ap.name, 'location': ap.location, 'ssid': ap.ssid} 
                    for ap in aps
                ]
            }), 200
            
        except Exception as e:
            logger.error(f"Get active access points error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_stats(self, ap_id):
        """Get access point statistics"""
        try:
            ap_uuid = UUID(ap_id)
            stats = self.service.get_ap_stats(ap_uuid, g.organization_id)
            return jsonify(stats), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid access point ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get access point stats error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_organization_stats(self):
        """Get access point statistics for entire organization"""
        try:
            stats = self.service.get_organization_stats(g.organization_id)
            return jsonify(stats), 200
            
        except Exception as e:
            logger.error(f"Get organization AP stats error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_online(self):
        """Get all online access points for monitoring"""
        try:
            aps = self.service.get_online_access_points(g.organization_id)
            
            return jsonify({
                'access_points': [ap.to_dict() for ap in aps]
            }), 200
            
        except Exception as e:
            logger.error(f"Get online access points error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_offline(self):
        """Get all offline access points for alerts"""
        try:
            aps = self.service.get_offline_access_points(g.organization_id)
            
            return jsonify({
                'access_points': [ap.to_dict() for ap in aps],
                'count': len(aps)
            }), 200
            
        except Exception as e:
            logger.error(f"Get offline access points error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_issues(self):
        """Get access points with issues for dashboard alerts"""
        try:
            aps = self.service.repository.get_aps_with_issues(g.organization_id)
            
            return jsonify({
                'access_points': [ap.to_dict() for ap in aps],
                'count': len(aps)
            }), 200
            
        except Exception as e:
            logger.error(f"Get access points with issues error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
        # UPDATE    
    @token_required
    def update(self, ap_id):
        """Update access point"""
        try:
            ap_uuid = UUID(ap_id)
            data = AccessPointUpdateSchema().load(request.json)
            ap = self.service.update_access_point(ap_uuid, g.organization_id, data)
            
            return jsonify({
                'success': True,
                'access_point': ap.to_dict(),
                'message': 'Access point updated successfully'
            }), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid access point ID format'}), 400
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Update access point error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
        # DELETE    
    @token_required
    def delete(self, ap_id):
        """Delete or deactivate access point"""
        try:
            ap_uuid = UUID(ap_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            
            self.service.delete_access_point(ap_uuid, g.organization_id, soft_delete=soft)
            
            message = 'Access point deactivated successfully' if soft else 'Access point deleted permanently'
            return jsonify({'success': True, 'message': message}), 200
            
        except ValueError:
            return jsonify({'error': 'Invalid access point ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except Exception as e:
            logger.error(f"Delete access point error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
        # BULK OPERATIONS    
    @token_required
    def bulk_delete(self):
        """Bulk delete access points"""
        try:
            data = request.get_json()
            ap_ids = data.get('access_point_ids', [])
            soft = data.get('soft', True)
            
            if not ap_ids:
                return jsonify({'error': 'No access point IDs provided'}), 400
            
            deleted_count = 0
            errors = []
            
            for aid in ap_ids:
                try:
                    self.service.delete_access_point(UUID(aid), g.organization_id, soft_delete=soft)
                    deleted_count += 1
                except Exception as e:
                    errors.append({'id': aid, 'error': str(e)})
            
            return jsonify({
                'success': True,
                'deleted_count': deleted_count,
                'errors': errors
            }), 200
            
        except Exception as e:
            logger.error(f"Bulk delete error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500