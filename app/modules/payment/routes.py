from flask import Blueprint
from app.modules.payment.controller import PaymentController
from app.core.security.jwt import token_required

payment_bp = Blueprint('payment', __name__)
controller = PaymentController()

@payment_bp.route('/initiate', methods=['POST'])
@token_required
def initiate_payment():
    """Initiate a payment"""
    return controller.initiate_payment()

@payment_bp.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """M-Pesa payment callback"""
    return controller.mpesa_callback()

@payment_bp.route('/transactions/<uuid:transaction_id>', methods=['GET'])
@token_required
def get_transaction(transaction_id):
    """Get transaction by ID"""
    return controller.get_transaction(transaction_id)

@payment_bp.route('/transactions', methods=['GET'])
@token_required
def list_transactions():
    """List transactions"""
    return controller.list_transactions()