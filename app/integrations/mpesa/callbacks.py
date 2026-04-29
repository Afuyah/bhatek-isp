from typing import Dict, Any
import json
from datetime import datetime

from app.core.logging.logger import logger
from app.core.database.redis_client import redis_client

class MpesaCallbackHandler:
    
    def __init__(self):
        self.callback_cache_ttl = 86400  # 24 hours
    
    def process_stk_callback(self, callback_data: Dict[str, Any]) -> Dict[str, Any]:
        
        try:
            body = callback_data.get('Body', {})
            stk_callback = body.get('stkCallback', {})
            
            result_code = stk_callback.get('ResultCode')
            result_desc = stk_callback.get('ResultDesc', '')
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            merchant_request_id = stk_callback.get('MerchantRequestID')
            callback_metadata = stk_callback.get('CallbackMetadata', {})
            
            # Generate unique callback ID for idempotency
            callback_id = f"{checkout_request_id}_{datetime.now().timestamp()}"
            
            # Check for duplicate callback
            if self._is_duplicate_callback(callback_id):
                logger.warning(f"Duplicate callback received for {checkout_request_id}")
                return {'success': True, 'message': 'Duplicate callback ignored'}
            
            # Store callback for idempotency
            self._store_callback(callback_id, callback_data)
            
            if result_code == 0 and callback_metadata:
                # Payment successful
                items = callback_metadata.get('Item', [])
                
                # Extract metadata
                amount = None
                mpesa_receipt = None
                transaction_date = None
                phone = None
                
                for item in items:
                    name = item.get('Name')
                    value = item.get('Value')
                    if name == 'Amount':
                        amount = value
                    elif name == 'MpesaReceiptNumber':
                        mpesa_receipt = value
                    elif name == 'TransactionDate':
                        transaction_date = value
                    elif name == 'PhoneNumber':
                        phone = value
                
                logger.info(f"Successful payment: {mpesa_receipt} for {amount} from {phone}")
                
                return {
                    'success': True,
                    'status': 'completed',
                    'checkout_request_id': checkout_request_id,
                    'merchant_request_id': merchant_request_id,
                    'mpesa_receipt': mpesa_receipt,
                    'amount': amount,
                    'phone': phone,
                    'transaction_date': transaction_date,
                    'result_code': result_code,
                    'result_description': result_desc
                }
            else:
                # Payment failed
                logger.warning(f"Payment failed: {checkout_request_id} - {result_desc}")
                
                return {
                    'success': False,
                    'status': 'failed',
                    'checkout_request_id': checkout_request_id,
                    'merchant_request_id': merchant_request_id,
                    'result_code': result_code,
                    'result_description': result_desc,
                    'error': result_desc
                }
                
        except Exception as e:
            logger.error(f"Error processing STK callback: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def process_b2c_callback(self, callback_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process B2C payment callback"""
        try:
            result = callback_data.get('Result', {})
            result_code = result.get('ResultCode')
            result_desc = result.get('ResultDesc')
            conversation_id = result.get('ConversationID')
            
            if result_code == 0:
                logger.info(f"B2C payment successful: {conversation_id}")
                return {
                    'success': True,
                    'status': 'completed',
                    'conversation_id': conversation_id,
                    'result_code': result_code,
                    'result_description': result_desc
                }
            else:
                logger.warning(f"B2C payment failed: {conversation_id} - {result_desc}")
                return {
                    'success': False,
                    'status': 'failed',
                    'conversation_id': conversation_id,
                    'result_code': result_code,
                    'result_description': result_desc
                }
                
        except Exception as e:
            logger.error(f"Error processing B2C callback: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    def process_c2b_validation(self, validation_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process C2B validation request
        Return {'ResultCode': 0, 'ResultDesc': 'Success'} to accept
        Return {'ResultCode': 1, 'ResultDesc': 'Rejected'} to reject
        """
        try:
            transaction_type = validation_data.get('TransactionType')
            transaction_id = validation_data.get('TransID')
            transaction_amount = validation_data.get('TransAmount')
            business_shortcode = validation_data.get('BusinessShortCode')
            bill_ref_number = validation_data.get('BillRefNumber')
            phone_number = validation_data.get('MSISDN')
            
            logger.info(f"C2B validation request: {transaction_id} for {transaction_amount}")
            
            # Validate transaction
            # Check if business exists, if amount is valid, etc.
            
            # For now, accept all transactions
            return {
                'ResultCode': 0,
                'ResultDesc': 'Success'
            }
            
        except Exception as e:
            logger.error(f"Error processing C2B validation: {e}", exc_info=True)
            return {
                'ResultCode': 1,
                'ResultDesc': 'Validation failed'
            }
    
    def process_c2b_confirmation(self, confirmation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process C2B confirmation after successful validation"""
        try:
            transaction_id = confirmation_data.get('TransID')
            transaction_amount = confirmation_data.get('TransAmount')
            transaction_time = confirmation_data.get('TransTime')
            phone_number = confirmation_data.get('MSISDN')
            bill_ref_number = confirmation_data.get('BillRefNumber')
            
            logger.info(f"C2B confirmation: {transaction_id} - {transaction_amount} from {phone_number}")
            
            # Process the confirmed payment
            # Update database, create subscription, etc.
            
            return {
                'ResultCode': 0,
                'ResultDesc': 'Success'
            }
            
        except Exception as e:
            logger.error(f"Error processing C2B confirmation: {e}", exc_info=True)
            return {
                'ResultCode': 1,
                'ResultDesc': 'Processing failed'
            }
    
    def _is_duplicate_callback(self, callback_id: str) -> bool:
        """Check if callback has already been processed"""
        redis_key = f"mpesa:callback:{callback_id}"
        return redis_client.exists(redis_key)
    
    def _store_callback(self, callback_id: str, callback_data: Dict[str, Any]):
        """Store callback for idempotency"""
        redis_key = f"mpesa:callback:{callback_id}"
        redis_client.setex(redis_key, self.callback_cache_ttl, json.dumps(callback_data))