"""
Subscriber API Routes
=====================
Blueprint for subscriber management endpoints.

Route Structure:
    /api/v1/subscribers/
        ├── POST   /                              Create hotspot subscriber
        ├── GET    /                              List subscribers (paginated)
        │
        ├── POST   /pppoe                         Create PPPoE subscriber
        ├── GET    /hotspot                       List hotspot users
        ├── GET    /pppoe                         List PPPoE users
        │
        ├── GET    /<subscriber_id>               Get subscriber detail
        ├── PUT    /<subscriber_id>               Update subscriber
        ├── DELETE /<subscriber_id>               Delete/deactivate
        │
        ├── POST   /<subscriber_id>/purchase      Purchase plan (M-Pesa)
        ├── POST   /<subscriber_id>/subscriptions Create subscription (admin)
        ├── GET    /<subscriber_id>/subscriptions/active  Get active subscription
        ├── GET    /<subscriber_id>/subscriptions/history  Get subscription history
        │
        ├── POST   /subscriptions/<id>/renew      Renew subscription
        ├── POST   /subscriptions/<id>/cancel     Cancel subscription
        │
        ├── POST   /<subscriber_id>/check-access  Check device access
        │
        ├── GET    /<subscriber_id>/devices       List devices
        ├── POST   /<subscriber_id>/devices       Add device
        ├── DELETE /devices/<device_id>           Remove device
        │
        ├── GET    /<subscriber_id>/stats         Subscriber statistics
        ├── GET    /dashboard/stats               Dashboard statistics
        │
        ├── POST   /bulk/import                   Bulk import (admin)
        ├── GET    /bulk/export                   Bulk export (admin)
        └── POST   /bulk/action                   Bulk action (admin)

Note: RADIUS authentication is handled by the separate RADIUS blueprint
      at /api/radius/authenticate (RadiusAuthHandler).
      The /authenticate route below is for captive portal manual login.
"""

from flask import Blueprint

from app.modules.subscriber.controller import SubscriberController
from app.core.security.jwt import token_required, permission_required

subscriber_bp = Blueprint(
    'subscriber', __name__, url_prefix='/api/v1/subscribers'
)
controller = SubscriberController()
# CREATE
@subscriber_bp.route('', methods=['POST'])
@token_required
def create_hotspot_subscriber():
    """
    POST /api/v1/subscribers

    Create a hotspot subscriber (auto-created via phone during M-Pesa flow).
    """
    return controller.create_hotspot_subscriber()


@subscriber_bp.route('/pppoe', methods=['POST'])
@token_required
@permission_required('pppoe_create')
def create_pppoe_subscriber():
    """
    POST /api/v1/subscribers/pppoe

    Create a PPPoE subscriber (admin only).
    """
    return controller.create_pppoe_subscriber()

# LIST
@subscriber_bp.route('', methods=['GET'])
@token_required
def list_subscribers():
    """
    GET /api/v1/subscribers

    List subscribers with pagination and filters.
    Query params: page, per_page, status, search,
                  has_active_subscription, subscriber_type
    """
    return controller.list_subscribers()


@subscriber_bp.route('/hotspot', methods=['GET'])
@token_required
def list_hotspot_users():
    """
    GET /api/v1/subscribers/hotspot

    List hotspot users only.
    """
    return controller.list_hotspot_users()


@subscriber_bp.route('/pppoe', methods=['GET'])
@token_required
def list_pppoe_users():
    """
    GET /api/v1/subscribers/pppoe

    List PPPoE users only.

    Note: This route and the POST /pppoe route share the same path
    but different HTTP methods, so they don't conflict.
    """
    return controller.list_pppoe_users()

# SINGLE RESOURCE
@subscriber_bp.route('/<subscriber_id>', methods=['GET'])
@token_required
def get_subscriber(subscriber_id):
    """
    GET /api/v1/subscribers/<subscriber_id>

    Get subscriber by ID.
    """
    return controller.get_subscriber(subscriber_id)


@subscriber_bp.route('/<subscriber_id>', methods=['PUT'])
@token_required
def update_subscriber(subscriber_id):
    """
    PUT /api/v1/subscribers/<subscriber_id>

    Update subscriber information.
    """
    return controller.update_subscriber(subscriber_id)


@subscriber_bp.route('/<subscriber_id>', methods=['DELETE'])
@token_required
def delete_subscriber(subscriber_id):
    """
    DELETE /api/v1/subscribers/<subscriber_id>?soft=true

    Delete or deactivate subscriber.
    """
    return controller.delete_subscriber(subscriber_id)

# SUBSCRIPTION MANAGEMENT
@subscriber_bp.route('/<subscriber_id>/purchase', methods=['POST'])
@token_required
def purchase_plan(subscriber_id):
    """
    POST /api/v1/subscribers/<subscriber_id>/purchase

    Purchase a plan for a hotspot subscriber (M-Pesa payment flow).
    """
    return controller.purchase_plan(subscriber_id)


@subscriber_bp.route('/<subscriber_id>/subscriptions', methods=['POST'])
@token_required
def create_subscription(subscriber_id):
    """
    POST /api/v1/subscribers/<subscriber_id>/subscriptions

    Create a subscription for a subscriber (admin action, typically PPPoE).
    """
    return controller.create_subscription(subscriber_id)


@subscriber_bp.route(
    '/<subscriber_id>/subscriptions/active', methods=['GET']
)
@token_required
def get_active_subscription(subscriber_id):
    """
    GET /api/v1/subscribers/<subscriber_id>/subscriptions/active

    Get the active subscription for a subscriber.
    """
    return controller.get_active_subscription(subscriber_id)


@subscriber_bp.route(
    '/<subscriber_id>/subscriptions/history', methods=['GET']
)
@token_required
def get_subscription_history(subscriber_id):
    """
    GET /api/v1/subscribers/<subscriber_id>/subscriptions/history

    Get subscription history for a subscriber.
    """
    return controller.get_subscription_history(subscriber_id)


@subscriber_bp.route(
    '/subscriptions/<subscription_id>/renew', methods=['POST']
)
@token_required
def renew_subscription(subscription_id):
    """
    POST /api/v1/subscribers/subscriptions/<subscription_id>/renew

    Renew an existing subscription.
    """
    return controller.renew_subscription(subscription_id)


@subscriber_bp.route(
    '/subscriptions/<subscription_id>/cancel', methods=['POST']
)
@token_required
def cancel_subscription(subscription_id):
    """
    POST /api/v1/subscribers/subscriptions/<subscription_id>/cancel

    Cancel a subscription.
    """
    return controller.cancel_subscription(subscription_id)

# ACCESS CONTROL
@subscriber_bp.route('/<subscriber_id>/check-access', methods=['POST'])
@token_required
def check_subscriber_access(subscriber_id):
    """
    POST /api/v1/subscribers/<subscriber_id>/check-access

    Check if subscriber can access internet on a specific device.
    Enforces device limits and subscription validity.
    """
    return controller.check_access(subscriber_id)

# DEVICE MANAGEMENT
@subscriber_bp.route('/<subscriber_id>/devices', methods=['GET'])
@token_required
def get_subscriber_devices(subscriber_id):
    """
    GET /api/v1/subscribers/<subscriber_id>/devices

    Get all devices for a subscriber.
    """
    return controller.get_devices(subscriber_id)


@subscriber_bp.route('/<subscriber_id>/devices', methods=['POST'])
@token_required
def add_device(subscriber_id):
    """
    POST /api/v1/subscribers/<subscriber_id>/devices

    Add a device (MAC address) to a subscriber.
    """
    return controller.add_device(subscriber_id)


@subscriber_bp.route('/devices/<device_id>', methods=['DELETE'])
@token_required
def remove_device(device_id):
    """
    DELETE /api/v1/subscribers/devices/<device_id>

    Remove a device from a subscriber.
    """
    return controller.remove_device(device_id)

# STATISTICS & REPORTING
@subscriber_bp.route('/<subscriber_id>/stats', methods=['GET'])
@token_required
def get_subscriber_stats(subscriber_id):
    """
    GET /api/v1/subscribers/<subscriber_id>/stats

    Get detailed statistics for a subscriber.
    """
    return controller.get_subscriber_stats(subscriber_id)


@subscriber_bp.route('/dashboard/stats', methods=['GET'])
@token_required
def get_dashboard_stats():
    """
    GET /api/v1/subscribers/dashboard/stats

    Get subscriber dashboard statistics for the organization.
    """
    return controller.get_dashboard_stats()

# BULK OPERATIONS (ADMIN ONLY)
@subscriber_bp.route('/bulk/import', methods=['POST'])
@token_required
@permission_required('subscriber_create')
def bulk_import():
    """
    POST /api/v1/subscribers/bulk/import

    Bulk import subscribers from CSV/JSON.
    """
    return controller.bulk_import()


@subscriber_bp.route('/bulk/export', methods=['GET'])
@token_required
def export_subscribers():
    """
    GET /api/v1/subscribers/bulk/export

    Export subscribers to CSV.
    """
    return controller.export_subscribers()


@subscriber_bp.route('/bulk/action', methods=['POST'])
@token_required
@permission_required('subscriber_update')
def bulk_action():
    """
    POST /api/v1/subscribers/bulk/action

    Bulk action on subscribers (activate, suspend, delete).
    """
    return controller.bulk_action()