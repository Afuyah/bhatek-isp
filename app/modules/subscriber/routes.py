from flask import Blueprint, request, g, jsonify
from app.modules.subscriber.controller import SubscriberController
from app.core.security.jwt import token_required, permission_required

subscriber_bp = Blueprint('subscriber', __name__, url_prefix='/api/v1/subscribers')
controller = SubscriberController()

# SUBSCRIBER CRUD OPERATIONS
@subscriber_bp.route('/', methods=['POST'])
@token_required
@permission_required('subscriber_create')
def create_hotspot_subscriber():
    """Create a new hotspot subscriber (auto-created via phone)"""
    return controller.create_hotspot_subscriber()


@subscriber_bp.route('/pppoe', methods=['POST'])
@token_required
@permission_required('subscriber_create')
def create_pppoe_subscriber():
    """Create a new PPPoE subscriber (admin created)"""
    return controller.create_pppoe_subscriber()


@subscriber_bp.route('/', methods=['GET'])
@token_required
@permission_required('subscriber_read')
def list_subscribers():
    """List all subscribers with filters"""
    return controller.list_subscribers()


@subscriber_bp.route('/hotspot', methods=['GET'])
@token_required
@permission_required('subscriber_read')
def list_hotspot_users():
    """List hotspot users only"""
    return controller.list_hotspot_users()


@subscriber_bp.route('/pppoe', methods=['GET'])
@token_required
@permission_required('subscriber_read')
def list_pppoe_users():
    """List PPPoE users only"""
    return controller.list_pppoe_users()


@subscriber_bp.route('/<subscriber_id>', methods=['GET'])
@token_required
@permission_required('subscriber_read')
def get_subscriber(subscriber_id):
    """Get subscriber by ID"""
    return controller.get_subscriber(subscriber_id)


@subscriber_bp.route('/<subscriber_id>', methods=['PUT'])
@token_required
@permission_required('subscriber_update')
def update_subscriber(subscriber_id):
    """Update subscriber"""
    return controller.update_subscriber(subscriber_id)


@subscriber_bp.route('/<subscriber_id>', methods=['DELETE'])
@token_required
@permission_required('subscriber_delete')
def delete_subscriber(subscriber_id):
    """Delete or deactivate subscriber"""
    return controller.delete_subscriber(subscriber_id)

# SUBSCRIPTION MANAGEMENT
@subscriber_bp.route('/<subscriber_id>/purchase', methods=['POST'])
@token_required
@permission_required('payment_process')
def purchase_plan(subscriber_id):
    """Purchase a plan for hotspot subscriber (M-Pesa)"""
    return controller.purchase_plan(subscriber_id)


@subscriber_bp.route('/<subscriber_id>/subscriptions', methods=['POST'])
@token_required
@permission_required('subscription_create')
def create_subscription(subscriber_id):
    """Create a subscription for subscriber (admin for PPPoE)"""
    return controller.create_subscription(subscriber_id)


@subscriber_bp.route('/<subscriber_id>/subscriptions/active', methods=['GET'])
@token_required
def get_active_subscription(subscriber_id):
    """Get active subscription for subscriber"""
    return controller.get_active_subscription(subscriber_id)


@subscriber_bp.route('/<subscriber_id>/renew', methods=['POST'])
@token_required
@permission_required('payment_process')
def renew_subscription(subscriber_id):
    """Renew active subscription"""
    return controller.renew_subscription(subscriber_id)


@subscriber_bp.route('/subscriptions/<subscription_id>/cancel', methods=['POST'])
@token_required
@permission_required('subscription_cancel')
def cancel_subscription(subscription_id):
    """Cancel a subscription"""
    return controller.cancel_subscription(subscription_id)


@subscriber_bp.route('/<subscriber_id>/subscriptions/history', methods=['GET'])
@token_required
def get_subscription_history(subscriber_id):
    """Get subscription history"""
    return controller.get_subscription_history(subscriber_id)

# ACCESS CONTROL & AUTHENTICATION
@subscriber_bp.route('/<subscriber_id>/check-access', methods=['POST'])
@token_required
def check_subscriber_access(subscriber_id):
    """Check if subscriber can access internet (for hotspot portal)"""
    return controller.check_access(subscriber_id)


@subscriber_bp.route('/authenticate', methods=['POST'])
def authenticate():
    """
    Authenticate subscriber for RADIUS (public endpoint - no JWT required)
    This endpoint is called by FreeRADIUS during authentication
    """
    return controller.authenticate()

# DEVICE MANAGEMENT
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


@subscriber_bp.route('/devices/<device_id>', methods=['PUT'])
@token_required
def update_device(device_id):
    """Update device details"""
    return controller.update_device(device_id)


@subscriber_bp.route('/devices/<device_id>', methods=['DELETE'])
@token_required
def remove_device(device_id):
    """Remove device from subscriber"""
    return controller.remove_device(device_id)

# STATISTICS & REPORTING
@subscriber_bp.route('/<subscriber_id>/stats', methods=['GET'])
@token_required
def get_subscriber_stats(subscriber_id):
    """Get detailed subscriber statistics"""
    return controller.get_subscriber_stats(subscriber_id)


@subscriber_bp.route('/<subscriber_id>/usage', methods=['GET'])
@token_required
def get_subscriber_usage(subscriber_id):
    """Get subscriber usage statistics (data usage)"""
    return controller.get_usage(subscriber_id)


@subscriber_bp.route('/dashboard/stats', methods=['GET'])
@token_required
def get_dashboard_stats():
    """Get subscriber dashboard statistics for organization"""
    return controller.get_dashboard_stats()

# BULK OPERATIONS (Admin Only)
@subscriber_bp.route('/bulk/import', methods=['POST'])
@token_required
@permission_required('subscriber_create')
def bulk_import():
    """Bulk import subscribers from CSV/JSON"""
    return controller.bulk_import()


@subscriber_bp.route('/bulk/export', methods=['GET'])
@token_required
@permission_required('report_export')
def export_subscribers():
    """Export subscribers to CSV"""
    return controller.export_subscribers()


@subscriber_bp.route('/bulk/action', methods=['POST'])
@token_required
@permission_required('subscriber_update')
def bulk_action():
    """Bulk action on subscribers (activate, suspend, delete)"""
    return controller.bulk_action()