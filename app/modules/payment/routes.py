from flask import Blueprint
from app.modules.payment.controller import PaymentController
from app.core.security.jwt import token_required

payment_bp = Blueprint('payment', __name__, url_prefix='/api/v1/payments')
controller = PaymentController()

# INITIATE PAYMENT
@payment_bp.route('/initiate', methods=['POST'])
@token_required
def initiate_payment():
    """
    POST /api/v1/payments/initiate

    Initiate a payment. For M-Pesa, sends STK Push to customer's phone.
    Supports new users (subscriber_id is optional).
    """
    return controller.initiate_payment()

# M-PESA CALLBACK (PUBLIC)
@payment_bp.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """
    POST /api/v1/payments/mpesa/callback  (PUBLIC — no JWT)

    Receives payment result webhook from Safaricom.
    On success: activates subscription, syncs RADIUS, registers device.
    """
    return controller.mpesa_callback()

# VERIFY PAYMENT
@payment_bp.route('/verify/<transaction_id>', methods=['GET'])
@token_required
def verify_payment(transaction_id):
    """
    GET /api/v1/payments/verify/<transaction_id>

    Manually verify payment status by querying M-Pesa.
    Used when callback is delayed or missed.
    """
    return controller.verify_payment(transaction_id)

# TRANSACTIONS — SINGLE
@payment_bp.route('/transactions/<transaction_id>', methods=['GET'])
@token_required
def get_transaction(transaction_id):
    """
    GET /api/v1/payments/transactions/<transaction_id>

    Get transaction details by ID.
    """
    return controller.get_transaction(transaction_id)

# TRANSACTIONS — LIST
@payment_bp.route('/transactions', methods=['GET'])
@token_required
def list_transactions():
    """
    GET /api/v1/payments/transactions

    List transactions with pagination and filters.
    Query params: page, per_page, subscriber_id, status, payment_method
    """
    return controller.list_transactions()

# REFUND
@payment_bp.route('/transactions/<transaction_id>/refund', methods=['POST'])
@token_required
def refund_transaction(transaction_id):
    """
    POST /api/v1/payments/transactions/<transaction_id>/refund

    Refund a successful transaction.
    Cancels associated subscription and removes from RADIUS.
    """
    return controller.refund_transaction(transaction_id)

# DASHBOARD STATS
@payment_bp.route('/stats', methods=['GET'])
@token_required
def get_payment_stats():
    """
    GET /api/v1/payments/stats

    Get payment statistics for the organization dashboard.
    """
    return controller.get_payment_stats()