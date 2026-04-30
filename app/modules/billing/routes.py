from flask import Blueprint, request, g, jsonify
from app.modules.billing.controller import BillingController
from app.core.security.jwt import token_required, permission_required

billing_bp = Blueprint('billing', __name__)
controller = BillingController()

#  Plan Routes 
@billing_bp.route('/plans', methods=['GET'])
@token_required
def get_plans():
    """Get all plans"""
    return controller.get_plans()

@billing_bp.route('/plans', methods=['POST'])
@token_required
@permission_required('plan_create')
def create_plan():
    """Create a new plan"""
    return controller.create_plan()

@billing_bp.route('/plans/<plan_id>', methods=['GET'])
@token_required
def get_plan(plan_id):
    """Get plan by ID"""
    return controller.get_plan(plan_id)

@billing_bp.route('/plans/<plan_id>', methods=['PUT'])
@token_required
@permission_required('plan_update')
def update_plan(plan_id):
    """Update a plan"""
    return controller.update_plan(plan_id)

@billing_bp.route('/plans/<plan_id>', methods=['DELETE'])
@token_required
@permission_required('plan_delete')
def delete_plan(plan_id):
    """Delete a plan"""
    return controller.delete_plan(plan_id)

#  Voucher Routes 
@billing_bp.route('/vouchers', methods=['POST'])
@token_required
@permission_required('voucher_create')
def create_voucher():
    """Create a single voucher"""
    return controller.create_voucher()

@billing_bp.route('/vouchers/batch', methods=['POST'])
@token_required
@permission_required('voucher_create')
def create_voucher_batch():
    """Create a batch of vouchers"""
    return controller.create_voucher_batch()

@billing_bp.route('/vouchers/redeem', methods=['POST'])
@token_required
def redeem_voucher():
    """Redeem a voucher"""
    return controller.redeem_voucher()

@billing_bp.route('/vouchers/check/<voucher_code>', methods=['GET'])
@token_required
def check_voucher(voucher_code):
    """Check voucher validity"""
    return controller.check_voucher(voucher_code)

#  Discount Coupon Routes 
@billing_bp.route('/coupons', methods=['POST'])
@token_required
@permission_required('coupon_create')
def create_coupon():
    """Create a discount coupon"""
    return controller.create_coupon()

@billing_bp.route('/coupons/validate', methods=['GET'])
@token_required
def validate_coupon():
    """Validate a coupon code"""
    return controller.validate_coupon()

#  Subscription Routes 
@billing_bp.route('/subscriptions/<subscription_id>', methods=['GET'])
@token_required
def get_subscription(subscription_id):
    """Get subscription by ID"""
    return controller.get_subscription(subscription_id)

@billing_bp.route('/subscriptions/<subscription_id>/cancel', methods=['POST'])
@token_required
@permission_required('subscription_cancel')
def cancel_subscription(subscription_id):
    """Cancel a subscription"""
    return controller.cancel_subscription(subscription_id)

@billing_bp.route('/subscriptions/<subscription_id>/renew', methods=['POST'])
@token_required
@permission_required('subscription_renew')
def renew_subscription(subscription_id):
    """Renew a subscription"""
    return controller.renew_subscription(subscription_id)