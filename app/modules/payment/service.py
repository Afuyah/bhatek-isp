from typing import Dict, Any, Optional
from uuid import UUID
from datetime import datetime
import uuid

from app.modules.payment.repository import TransactionRepository, PaymentAccountRepository
from app.models.payment import Transaction
from app.integrations.mpesa.client import MpesaClient
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError

class PaymentService:
    """Business logic for payment processing"""
    
    def __init__(self):
        self.transaction_repo = TransactionRepository()
        self.payment_account_repo = PaymentAccountRepository()
    
    def process_payment(
        self, 
        organization_id: UUID, 
        subscriber_id: UUID, 
        amount: float,
        payment_method: str, 
        payment_details: Dict[str, Any], 
        extra_data: Dict[str, Any] = None  # Changed from 'metadata' to 'extra_data'
    ) -> Dict[str, Any]:
        """Process a payment"""
        
        # Generate transaction reference
        transaction_ref = f"TXN-{uuid.uuid4().hex[:12].upper()}"
        
        # Create transaction record
        transaction_data = {
            'organization_id': organization_id,
            'subscriber_id': subscriber_id,
            'transaction_reference': transaction_ref,
            'amount': amount,
            'status': 'pending',
            'payment_method': payment_method,
            'payment_details': payment_details,
            'ip_address': payment_details.get('ip_address'),
            'user_agent': payment_details.get('user_agent'),
            'custom_data': extra_data or {},  # Use custom_data instead of metadata
            'created_at': datetime.utcnow()
        }
        
        transaction = self.transaction_repo.create(transaction_data)
        
        if payment_method == 'mpesa':
            return self._process_mpesa_payment(organization_id, transaction, payment_details, extra_data)
        elif payment_method == 'cash':
            return self._process_cash_payment(transaction, payment_details)
        elif payment_method == 'bank_transfer':
            return self._process_bank_payment(transaction, payment_details)
        elif payment_method == 'voucher':
            return self._process_voucher_payment(transaction, payment_details)
        else:
            raise BusinessError(f"Unsupported payment method: {payment_method}")
    
    def _process_mpesa_payment(
        self, 
        organization_id: UUID, 
        transaction: Transaction,
        payment_details: Dict[str, Any], 
        extra_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Process M-Pesa STK Push payment"""
        
        # Get payment account
        payment_account = self.payment_account_repo.get_default(organization_id)
        if not payment_account:
            raise BusinessError("No payment account configured for this organization")
        
        # Create M-Pesa client
        mpesa = MpesaClient(str(organization_id), payment_account.to_dict())
        
        # Initiate STK Push
        phone = payment_details.get('phone')
        if not phone:
            raise BusinessError("Phone number required for M-Pesa payment")
        
        reference = transaction.transaction_reference[:12]  # Max 12 chars
        description = extra_data.get('plan_name', 'Internet Payment')[:13] if extra_data else "Internet Payment"
        
        result = mpesa.stk_push(
            phone_number=phone,
            amount=float(transaction.amount),
            reference=reference,
            description=description
        )
        
        if result.get('success'):
            # Update transaction with checkout request ID
            updated_payment_details = dict(payment_details)
            updated_payment_details['checkout_request_id'] = result['checkout_request_id']
            
            self.transaction_repo.update(
                transaction.id,
                {
                    'payment_details': updated_payment_details,
                    'custom_data': extra_data or {}
                }
            )
            
            return {
                'status': 'pending',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'checkout_request_id': result['checkout_request_id'],
                'response_code': result.get('response_code'),
                'customer_message': result.get('customer_message', 'Please check your phone to complete payment'),
                'message': 'STK Push sent. Please check your phone to complete payment.'
            }
        else:
            # Mark transaction as failed
            self.transaction_repo.update(
                transaction.id, 
                {
                    'status': 'failed', 
                    'failure_reason': result.get('error', 'Payment initiation failed'),
                    'custom_data': {'error_details': result}
                }
            )
            return {
                'status': 'failed',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'error': result.get('error', 'Payment initiation failed'),
                'response_description': result.get('response_description'),
                'message': 'Failed to initiate payment. Please try again.'
            }
    

    
    def _process_bank_payment(self, transaction: Transaction, payment_details: Dict[str, Any]) -> Dict[str, Any]:
        """Process bank transfer payment (manual verification)"""
        
        # Bank transfers require manual verification
        self.transaction_repo.update(
            transaction.id,
            {
                'status': 'pending',
                'payment_details': {
                    **transaction.payment_details,
                    'bank_name': payment_details.get('bank_name'),
                    'account_name': payment_details.get('account_name'),
                    'reference_number': payment_details.get('reference_number'),
                    'verification_status': 'pending'
                }
            }
        )
        
        return {
            'status': 'pending',
            'transaction_id': str(transaction.id),
            'transaction_reference': transaction.transaction_reference,
            'message': 'Bank transfer recorded. Payment will be verified manually.'
        }
    
    def _process_voucher_payment(self, transaction: Transaction, payment_details: Dict[str, Any]) -> Dict[str, Any]:
        """Process voucher payment"""
        
        voucher_code = payment_details.get('voucher_code')
        if not voucher_code:
            raise BusinessError("Voucher code required")
        
        # Validate voucher (would call voucher service)
        # This is a placeholder - actual implementation would validate the voucher
        
        self.transaction_repo.update(
            transaction.id,
            {
                'status': 'success',
                'completed_at': datetime.utcnow(),
                'payment_details': {
                    **transaction.payment_details,
                    'voucher_code': voucher_code,
                    'voucher_validated': True
                }
            }
        )
        
        return {
            'status': 'success',
            'transaction_id': str(transaction.id),
            'transaction_reference': transaction.transaction_reference,
            'message': 'Voucher payment successful'
        }
    
    def process_callback(self, transaction_id: UUID, callback_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process payment callback (from M-Pesa)"""
        
        transaction = self.transaction_repo.get_by_id(transaction_id)
        if not transaction:
            logger.warning(f"Transaction not found for callback: {transaction_id}")
            return {'success': False, 'error': 'Transaction not found'}
        
        # Extract callback result
        body = callback_data.get('Body', {})
        stk_callback = body.get('stkCallback', {})
        result_code = stk_callback.get('ResultCode')
        result_desc = stk_callback.get('ResultDesc')
        
        if result_code == '0':
            # Payment successful
            mpesa_receipt = None
            amount = None
            transaction_date = None
            
            callback_metadata = stk_callback.get('CallbackMetadata', {})
            items = callback_metadata.get('Item', [])
            
            for item in items:
                name = item.get('Name')
                value = item.get('Value')
                if name == 'MpesaReceiptNumber':
                    mpesa_receipt = value
                elif name == 'Amount':
                    amount = value
                elif name == 'TransactionDate':
                    transaction_date = value
            
            # Update transaction
            self.transaction_repo.update(
                transaction_id,
                {
                    'status': 'success',
                    'mpesa_receipt': mpesa_receipt,
                    'completed_at': datetime.utcnow(),
                    'callback_payload': callback_data,
                    'payment_details': {
                        **(transaction.payment_details or {}),
                        'mpesa_result_code': result_code,
                        'mpesa_result_desc': result_desc,
                        'mpesa_amount': amount,
                        'mpesa_transaction_date': transaction_date
                    }
                }
            )
            
            logger.info(f"Payment successful: {transaction_id} - Receipt: {mpesa_receipt}")
            
            return {
                'success': True,
                'status': 'success',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'mpesa_receipt': mpesa_receipt,
                'amount': amount,
                'message': 'Payment completed successfully'
            }
        else:
            # Payment failed
            self.transaction_repo.update(
                transaction_id,
                {
                    'status': 'failed',
                    'failure_reason': result_desc,
                    'callback_payload': callback_data,
                    'payment_details': {
                        **(transaction.payment_details or {}),
                        'mpesa_result_code': result_code,
                        'mpesa_result_desc': result_desc
                    }
                }
            )
            
            logger.warning(f"Payment failed: {transaction_id} - {result_desc}")
            
            return {
                'success': False,
                'status': 'failed',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'error': result_desc,
                'message': 'Payment failed. Please try again.'
            }
    
    def verify_payment_status(self, transaction_id: UUID, checkout_request_id: str = None) -> Dict[str, Any]:
        """Verify payment status by querying M-Pesa"""
        
        transaction = self.transaction_repo.get_by_id(transaction_id)
        if not transaction:
            raise NotFoundError("Transaction not found")
        
        if transaction.status != 'pending':
            return {
                'status': transaction.status,
                'transaction_id': str(transaction.id),
                'message': f'Payment already {transaction.status}'
            }
        
        # Get payment account
        payment_account = self.payment_account_repo.get_default(transaction.organization_id)
        if not payment_account:
            raise BusinessError("No payment account configured")
        
        # Create M-Pesa client and query status
        mpesa = MpesaClient(str(transaction.organization_id), payment_account.to_dict())
        
        checkout_id = checkout_request_id or transaction.payment_details.get('checkout_request_id')
        if not checkout_id:
            raise BusinessError("No checkout request ID found")
        
        result = mpesa.query_status(checkout_id)
        
        if result.get('success') and result.get('status') == 'completed':
            # Update transaction as successful
            self.transaction_repo.update(
                transaction_id,
                {
                    'status': 'success',
                    'mpesa_receipt': result.get('mpesa_receipt'),
                    'completed_at': datetime.utcnow()
                }
            )
            return {
                'status': 'success',
                'transaction_id': str(transaction.id),
                'mpesa_receipt': result.get('mpesa_receipt'),
                'message': 'Payment completed'
            }
        elif result.get('status') == 'failed':
            self.transaction_repo.update(
                transaction_id,
                {
                    'status': 'failed',
                    'failure_reason': result.get('result_description')
                }
            )
            return {
                'status': 'failed',
                'transaction_id': str(transaction.id),
                'error': result.get('result_description'),
                'message': 'Payment failed'
            }
        else:
            return {
                'status': 'pending',
                'transaction_id': str(transaction.id),
                'message': 'Payment still pending. Please complete the transaction on your phone.'
            }
    
    def get_transaction(self, transaction_id: UUID) -> Optional[Transaction]:
        """Get transaction by ID"""
        return self.transaction_repo.get_by_id(transaction_id)
    
    def get_transactions_by_subscriber(self, subscriber_id: UUID, organization_id: UUID, limit: int = 50) -> list:
        """Get all transactions for a subscriber"""
        return self.transaction_repo.get_by_subscriber(subscriber_id, organization_id, limit)
    
    def refund_transaction(self, transaction_id: UUID, reason: str, amount: float = None) -> Dict[str, Any]:
        """Refund a transaction"""
        
        transaction = self.transaction_repo.get_by_id(transaction_id)
        if not transaction:
            raise NotFoundError("Transaction not found")
        
        if transaction.status != 'success':
            raise BusinessError("Only successful transactions can be refunded")
        
        refund_amount = amount or float(transaction.amount)
        
        # Create refund record
        from app.modules.payment.models import Refund
        from app.core.database.session import db
        
        refund = Refund(
            organization_id=transaction.organization_id,
            transaction_id=transaction.id,
            refund_reference=f"REF-{uuid.uuid4().hex[:12].upper()}",
            amount=refund_amount,
            reason=reason,
            status='pending'
        )
        db.session.add(refund)
        
        # Update transaction status
        transaction.status = 'refunded'
        db.session.commit()
        
        logger.info(f"Refund initiated for transaction {transaction_id}: {refund_amount}")
        
        return {
            'success': True,
            'refund_id': str(refund.id),
            'refund_reference': refund.refund_reference,
            'amount': refund_amount,
            'status': 'pending',
            'message': 'Refund initiated successfully'
        }