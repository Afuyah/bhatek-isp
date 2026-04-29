from flask import Blueprint, request, g, jsonify
from app.modules.subscriber.controller import SubscriberController
from app.core.security.jwt import token_required, permission_required

subscriber_bp = Blueprint('subscriber', __name__)
controller = SubscriberController()

# CRUD operations
@subscriber_bp.route('/', methods=['POST'])
@token_required
@permission_required('subscriber_create')
def create_subscriber():
    """Create a new subscriber"""
    return controller.create()

@subscriber_bp.route('/', methods=['GET'])
@token_required
@permission_required('subscriber_read')
def list_subscribers():
    """List all subscribers"""
    return controller.list()

@subscriber_bp.route('/<subscriber_id>', methods=['GET'])
@token_required
@permission_required('subscriber_read')
def get_subscriber(subscriber_id):
    """Get subscriber by ID"""
    return controller.get(subscriber_id)

@subscriber_bp.route('/<subscriber_id>', methods=['PUT'])
@token_required
@permission_required('subscriber_update')
def update_subscriber(subscriber_id):
    """Update subscriber"""
    return controller.update(subscriber_id)

# Access and sessions
@subscriber_bp.route('/<subscriber_id>/check-access', methods=['POST'])
@token_required
def check_subscriber_access(subscriber_id):
    """Check if subscriber can access internet"""
    return controller.check_access(subscriber_id)

@subscriber_bp.route('/<subscriber_id>/stats', methods=['GET'])
@token_required
def get_subscriber_stats(subscriber_id):
    """Get subscriber statistics"""
    return controller.get_stats(subscriber_id)

@subscriber_bp.route('/<subscriber_id>/usage', methods=['GET'])
@token_required
def get_subscriber_usage(subscriber_id):
    """Get subscriber usage statistics"""
    return controller.get_usage(subscriber_id)

# Plans and subscriptions
@subscriber_bp.route('/<subscriber_id>/purchase', methods=['POST'])
@token_required
@permission_required('payment_process')
def purchase_plan(subscriber_id):
    """Purchase a plan for subscriber"""
    return controller.purchase_plan(subscriber_id)

@subscriber_bp.route('/<subscriber_id>/renew', methods=['POST'])
@token_required
@permission_required('payment_process')
def renew_subscription(subscriber_id):
    """Renew active subscription"""
    return controller.renew_subscription(subscriber_id)

@subscriber_bp.route('/<subscriber_id>/subscriptions', methods=['GET'])
@token_required
def get_subscription_history(subscriber_id):
    """Get subscription history"""
    return controller.get_subscription_history(subscriber_id)

# Device management
@subscriber_bp.route('/<subscriber_id>/devices', methods=['GET'])
@token_required
def get_subscriber_devices(subscriber_id):
    """Get all devices for subscriber"""
    return controller.get_devices(subscriber_id)

@subscriber_bp.route('/<subscriber_id>/devices', methods=['POST'])
@token_required
def add_device(subscriber_id):
    """Add device to subscriber"""
    return controller.add_device(subscriber_id)

@subscriber_bp.route('/devices/<device_id>', methods=['DELETE'])
@token_required
def remove_device(device_id):
    """Remove device from subscriber"""
    return controller.remove_device(device_id)

# Bulk operations (admin only)
@subscriber_bp.route('/bulk/import', methods=['POST'])
@token_required
@permission_required('subscriber_create')
def bulk_import():
    """Bulk import subscribers from CSV/JSON"""
    # This would be implemented in the service
    return jsonify({'message': 'Bulk import endpoint'}), 200

@subscriber_bp.route('/export', methods=['GET'])
@token_required
@permission_required('report_export')
def export_subscribers():
    """Export subscribers to CSV"""
    # This would be implemented in the service
    return jsonify({'message': 'Export endpoint'}), 200