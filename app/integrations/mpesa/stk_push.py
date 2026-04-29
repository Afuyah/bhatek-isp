from typing import Dict, Any, Optional
from datetime import datetime
import uuid

from app.core.logging.logger import logger
from app.core.database.redis_client import redis_client
from app.integrations.mpesa.client import MpesaClient

class STKPushService:
    """Service for managing STK Push transactions"""
    
    def __init__(self):
        self.transaction_cache_ttl = 3600  # 1 hour
    
    def initiate_payment(
        self,
        organization_id: str,
        payment_account: Dict[str, Any],
        phone_number: str,
        amount: float,
        reference: str,
        description: str,
        callback_url: str = None
    ) -> Dict[str, Any]:
        """Initiate STK Push payment with tracking"""
        
        # Generate unique transaction ID
        transaction_id = str(uuid.uuid4())
        
        # Store transaction state in Redis
        transaction_state = {
            'transaction_id': transaction_id,
            'organization_id': organization_id,
            'phone_number': phone_number,
            'amount': amount,
            'reference': reference,
            'description': description,
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
            'attempts': 0,
            'checkout_request_id': None
        }
        
        redis_key = f"stk_push:{transaction_id}"
        redis_client.setex(redis_key, self.transaction_cache_ttl, str(transaction_state))
        
        # Initialize M-Pesa client
        mpesa = MpesaClient(organization_id, payment_account)
        
        # Make STK Push request
        result = mpesa.stk_push(
            phone_number=phone_number,
            amount=amount,
            reference=reference,
            description=description,
            callback_url=callback_url
        )
        
        if result.get('success'):
            # Update transaction state
            transaction_state['status'] = 'processing'
            transaction_state['checkout_request_id'] = result.get('checkout_request_id')
            redis_client.setex(redis_key, self.transaction_cache_ttl, str(transaction_state))
            
            # Schedule status query
            self._schedule_status_query(transaction_id, result.get('checkout_request_id'), organization_id, payment_account)
            
            return {
                'success': True,
                'transaction_id': transaction_id,
                'checkout_request_id': result.get('checkout_request_id'),
                'customer_message': result.get('customer_message', 'Please enter your PIN to complete payment')
            }
        else:
            # Update transaction state as failed
            transaction_state['status'] = 'failed'
            transaction_state['error'] = result.get('error')
            redis_client.setex(redis_key, self.transaction_cache_ttl, str(transaction_state))
            
            return {
                'success': False,
                'transaction_id': transaction_id,
                'error': result.get('error'),
                'response_description': result.get('response_description')
            }
    
    def _schedule_status_query(
        self,
        transaction_id: str,
        checkout_request_id: str,
        organization_id: str,
        payment_account: Dict[str, Any]
    ):
        """Schedule background task to query payment status"""
        # This would be implemented with Celery
        from app.tasks.payment import query_payment_status
        query_payment_status.delay(transaction_id, checkout_request_id, organization_id)
    
    def update_transaction_status(
        self,
        transaction_id: str,
        status: str,
        mpesa_receipt: str = None,
        result_code: str = None,
        result_description: str = None
    ) -> Dict[str, Any]:
        """Update transaction status after callback"""
        redis_key = f"stk_push:{transaction_id}"
        transaction_data = redis_client.get(redis_key)
        
        if transaction_data:
            import ast
            transaction_state = ast.literal_eval(transaction_data.decode() if isinstance(transaction_data, bytes) else transaction_data)
            
            transaction_state['status'] = status
            transaction_state['completed_at'] = datetime.now().isoformat()
            
            if mpesa_receipt:
                transaction_state['mpesa_receipt'] = mpesa_receipt
            if result_code:
                transaction_state['result_code'] = result_code
            if result_description:
                transaction_state['result_description'] = result_description
            
            redis_client.setex(redis_key, self.transaction_cache_ttl, str(transaction_state))
            
            logger.info(f"Updated transaction {transaction_id} status to {status}")
            
            return transaction_state
        
        return None
    
    def get_transaction_status(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        """Get current transaction status"""
        redis_key = f"stk_push:{transaction_id}"
        transaction_data = redis_client.get(redis_key)
        
        if transaction_data:
            import ast
            return ast.literal_eval(transaction_data.decode() if isinstance(transaction_data, bytes) else transaction_data)
        
        return None