from flask import request, g, jsonify
from marshmallow import ValidationError

from app.modules.payment.service import PaymentService
from app.modules.payment.schemas import PaymentInitiateSchema, PaymentCallbackSchema
from app.core.security.jwt import token_required
from app.core.logging.logger import logger

class PaymentController:
    """Payment controller"""
    
    def __init__(self):
        self.service = PaymentService()
    
    @token_required
    def initiate_payment(self):
        """Initiate a payment"""
        try:
            data = PaymentInitiateSchema().load(request.json)
            result = self.service.process_payment(
                organization_id=g.organization_id,
                subscriber_id=data['subscriber_id'],
                amount=data['amount'],
                payment_method=data['payment_method'],
                payment_details={
                    'phone': data.get('phone'),
                    'ip_address': request.remote_addr,
                    'user_agent': request.headers.get('User-Agent', '')
                },
                metadata=data.get('metadata')
            )
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Initiate payment error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    def mpesa_callback(self):
        """M-Pesa payment callback"""
        try:
            callback_data = request.get_json()
            # Extract transaction reference from callback
            stk_callback = callback_data.get('Body', {}).get('stkCallback', {})
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            
            # Find transaction by checkout_request_id
            transaction = self.service.transaction_repo.get_by_checkout_id(checkout_request_id)
            if transaction:
                result = self.service.process_callback(transaction.id, callback_data)
                return jsonify(result), 200
            
            return jsonify({'success': True}), 200
        except Exception as e:
            logger.error(f"M-Pesa callback error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @token_required
    def get_transaction(self, transaction_id):
        """Get transaction by ID"""
        try:
            transaction = self.service.get_transaction(transaction_id)
            if not transaction:
                return jsonify({'error': 'Transaction not found'}), 404
            return jsonify(transaction.to_dict()), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def list_transactions(self):
        """List transactions"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            subscriber_id = request.args.get('subscriber_id')
            status = request.args.get('status')
            
            transactions = self.service.transaction_repo.get_by_organization(
                g.organization_id, skip, per_page, subscriber_id, status
            )
            total = self.service.transaction_repo.count_by_organization(g.organization_id, subscriber_id, status)
            
            return jsonify({
                'transactions': [t.to_dict() for t in transactions],
                'total': total,
                'page': page,
                'per_page': per_page
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500