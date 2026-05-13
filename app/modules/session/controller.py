from flask import request, g, jsonify
from uuid import UUID
from datetime import datetime

from app.modules.session.service import SessionService
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger
from app.integrations.radius.radius_accounting_handler import RadiusAccountingHandler


class SessionController:
    def __init__(self):
        self.service = SessionService()
        self.radius_handler = RadiusAccountingHandler()
    
    @token_required
    def get(self, session_id):
        """Get session by ID"""
        try:
            session_id_uuid = UUID(session_id)
            session = self.service.get_session(session_id_uuid, g.organization_id)
            if not session:
                return jsonify({'error': 'Session not found'}), 404
            return jsonify(session.to_dict()), 200
        except ValueError:
            return jsonify({'error': 'Invalid session ID format'}), 400
        except Exception as e:
            logger.error(f"Error getting session: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    @permission_required('session_terminate')
    def terminate(self, session_id):
        """Terminate session"""
        try:
            session_id_uuid = UUID(session_id)
            cause = request.json.get('cause', 'manual_termination') if request.json else 'manual_termination'
            
            success = self.service.terminate_session(session_id_uuid, g.organization_id, cause)
            if not success:
                return jsonify({'error': 'Session not found or already terminated'}), 404
            
            return jsonify({
                'success': True, 
                'message': 'Session terminated successfully',
                'session_id': session_id,
                'cause': cause
            }), 200
        except ValueError:
            return jsonify({'error': 'Invalid session ID format'}), 400
        except Exception as e:
            logger.error(f"Error terminating session: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def list_active(self):
        """List active sessions with filtering and pagination"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            subscriber_id = request.args.get('subscriber_id')
            router_id = request.args.get('router_id')
            ap_id = request.args.get('access_point_id')
            username = request.args.get('username')
            device_mac = request.args.get('device_mac')
            
            sessions = []
            total = 0
            
            if subscriber_id:
                subscriber_id_uuid = UUID(subscriber_id)
                sessions = self.service.get_active_sessions_by_subscriber(subscriber_id_uuid, g.organization_id)
                total = len(sessions)
            elif router_id:
                router_id_uuid = UUID(router_id)
                sessions = self.service.repository.get_active_by_router(router_id_uuid, g.organization_id)
                total = len(sessions)
            elif ap_id:
                ap_id_uuid = UUID(ap_id)
                sessions = self.service.repository.get_active_by_access_point(ap_id_uuid, g.organization_id)
                total = len(sessions)
            elif username:
                sessions = self.service.get_active_sessions_by_username(username, g.organization_id)
                total = len(sessions)
            elif device_mac:
                sessions = self.service.get_active_sessions_by_device(device_mac, g.organization_id)
                total = len(sessions)
            else:
                sessions = self.service.repository.get_all_active(g.organization_id, skip, per_page)
                total = self.service.repository.count_active(g.organization_id)
            
            return jsonify({
                'sessions': [s.to_dict() for s in sessions],
                'total': total,
                'page': page,
                'per_page': per_page,
                'filters': {
                    'subscriber_id': subscriber_id,
                    'router_id': router_id,
                    'access_point_id': ap_id,
                    'username': username,
                    'device_mac': device_mac
                }
            }), 200
        except ValueError as e:
            return jsonify({'error': f'Invalid UUID format: {str(e)}'}), 400
        except Exception as e:
            logger.error(f"Error listing sessions: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    @permission_required('router_configure')
    def sync_router(self, router_id):
        """Sync router sessions"""
        try:
            router_id_uuid = UUID(router_id)
            result = self.service.sync_router_sessions(router_id_uuid, g.organization_id)
            return jsonify(result), 200
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except Exception as e:
            logger.error(f"Sync router sessions error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_stats(self):
        """Get session statistics"""
        try:
            stats = self.service.get_session_stats(g.organization_id)
            return jsonify(stats), 200
        except Exception as e:
            logger.error(f"Error getting session stats: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def cleanup_expired(self):
        """Clean up expired sessions"""
        try:
            count = self.service.cleanup_expired_sessions(g.organization_id)
            return jsonify({
                'success': True,
                'cleaned': count,
                'message': f'Cleaned up {count} expired sessions'
            }), 200
        except Exception as e:
            logger.error(f"Error cleaning up sessions: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_by_username(self, username):
        """Get sessions by username"""
        try:
            sessions = self.service.get_active_sessions_by_username(username, g.organization_id)
            return jsonify({
                'username': username,
                'sessions': [s.to_dict() for s in sessions],
                'count': len(sessions)
            }), 200
        except Exception as e:
            logger.error(f"Error getting sessions by username: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_by_device(self, device_mac):
        """Get sessions by device MAC"""
        try:
            sessions = self.service.get_active_sessions_by_device(device_mac, g.organization_id)
            return jsonify({
                'device_mac': device_mac,
                'sessions': [s.to_dict() for s in sessions],
                'count': len(sessions)
            }), 200
        except Exception as e:
            logger.error(f"Error getting sessions by device: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_router_stats(self, router_id):
        """Get session statistics for a specific router"""
        try:
            router_id_uuid = UUID(router_id)
            active_count = self.service.repository.count_active_by_router(router_id_uuid, g.organization_id)
            sessions = self.service.repository.get_active_by_router(router_id_uuid, g.organization_id)
            
            total_bytes_in = sum(s.bytes_in or 0 for s in sessions)
            total_bytes_out = sum(s.bytes_out or 0 for s in sessions)
            
            return jsonify({
                'router_id': router_id,
                'active_sessions': active_count,
                'total_bytes_in': total_bytes_in,
                'total_bytes_out': total_bytes_out,
                'total_bytes_gb': round((total_bytes_in + total_bytes_out) / (1024**3), 2),
                'sessions': [s.to_dict() for s in sessions[:20]]
            }), 200
        except ValueError:
            return jsonify({'error': 'Invalid router ID format'}), 400
        except Exception as e:
            logger.error(f"Error getting router stats: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    @permission_required('report_view')
    def get_user_usage(self, username):
        """Get usage statistics for a user"""
        try:
            days = request.args.get('days', 30, type=int)
            usage = self.service.get_user_usage(username, g.organization_id, days)
            return jsonify(usage), 200
        except Exception as e:
            logger.error(f"Error getting user usage: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    @permission_required('report_view')
    def get_organization_usage(self):
        """Get usage statistics for organization"""
        try:
            days = request.args.get('days', 30, type=int)
            usage = self.service.get_organization_usage(g.organization_id, days)
            return jsonify(usage), 200
        except Exception as e:
            logger.error(f"Error getting organization usage: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    # RADIUS ACCOUNTING ENDPOINT (PUBLIC - NO AUTH)
    
    def radius_accounting(self):
        """
        Endpoint for RADIUS accounting packets from FreeRADIUS.
        This method is called by the route (no authentication required).
        Delegates processing to RadiusAccountingHandler for consistency.
        """
        try:
            # Get data from request (supports both JSON and form-urlencoded)
            data = request.get_json() or request.form
            
            # Convert to dict if needed
            if hasattr(data, 'to_dict'):
                accounting_data = data.to_dict()
            elif isinstance(data, dict):
                accounting_data = data
            else:
                accounting_data = {}
            
            # Process using the RADIUS handler
            result = self.radius_handler.process_accounting(accounting_data)
            
            # Convert result to appropriate response
            if result.get('result') == 'ok':
                return jsonify({'result': 'ok'}), 200
            else:
                return jsonify({'result': 'fail', 'reason': result.get('reason', 'Unknown error')}), 200
                
        except Exception as e:
            logger.error(f"Error in RADIUS accounting: {e}", exc_info=True)
            return jsonify({'result': 'fail', 'reason': str(e)}), 500


            