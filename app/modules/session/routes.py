from flask import Blueprint, request, g, jsonify
from app.modules.session.controller import SessionController
from app.core.security.jwt import token_required

session_bp = Blueprint('session', __name__)
controller = SessionController()

# Session CRUD endpoints
@session_bp.route('/<session_id>', methods=['GET'])
@token_required
def get_session(session_id):
    """Get session by ID"""
    return controller.get(session_id)

@session_bp.route('/<session_id>/terminate', methods=['POST'])
@token_required
def terminate_session(session_id):
    """Terminate a session"""
    return controller.terminate(session_id)

# List endpoints
@session_bp.route('/active', methods=['GET'])
@token_required
def list_active_sessions():
    """List active sessions"""
    return controller.list_active()

@session_bp.route('/stats', methods=['GET'])
@token_required
def get_session_stats():
    """Get session statistics"""
    return controller.get_stats()

@session_bp.route('/cleanup', methods=['POST'])
@token_required
def cleanup_expired():
    """Clean up expired sessions"""
    return controller.cleanup_expired()

# Filtered list endpoints
@session_bp.route('/user/<username>', methods=['GET'])
@token_required
def get_user_sessions(username):
    """Get sessions by username"""
    return controller.get_by_username(username)

@session_bp.route('/device/<device_mac>', methods=['GET'])
@token_required
def get_device_sessions(device_mac):
    """Get sessions by device MAC"""
    return controller.get_by_device(device_mac)

@session_bp.route('/router/<router_id>', methods=['GET'])
@token_required
def get_router_sessions(router_id):
    """Get sessions for a router"""
    return controller.get_router_stats(router_id)

# Router sync
@session_bp.route('/sync/<router_id>', methods=['POST'])
@token_required
def sync_router_sessions(router_id):
    """Sync router sessions"""
    return controller.sync_router(router_id)

# Usage and reporting
@session_bp.route('/usage/<username>', methods=['GET'])
@token_required
def get_user_usage(username):
    """Get usage statistics for a user"""
    return controller.get_user_usage(username)

@session_bp.route('/usage/organization', methods=['GET'])
@token_required
def get_organization_usage():
    """Get usage statistics for organization"""
    return controller.get_organization_usage()

# RADIUS accounting endpoint (no auth - called by FreeRADIUS)
@session_bp.route('/radius/accounting', methods=['POST'])
def radius_accounting():
    """Endpoint for RADIUS accounting packets"""
    return controller.radius_accounting()