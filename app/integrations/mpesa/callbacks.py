from typing import Dict, Any
import json
from datetime import datetime

from app.core.logging.logger import logger
from app.core.database.redis_client import redis_client


class MpesaCallbackHandler:

    def __init__(self):
        self.callback_cache_ttl = 86400  # 24 hours

    # =========================================================================
    # STK PUSH CALLBACK
    # =========================================================================

    def process_stk_callback(
        self, callback_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        try:
            body = callback_data.get('Body', {})
            stk_callback = body.get('stkCallback', {})

            result_code = stk_callback.get('ResultCode')
            result_desc = stk_callback.get('ResultDesc', '')
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            merchant_request_id = stk_callback.get('MerchantRequestID')
            callback_metadata = stk_callback.get('CallbackMetadata', {})

            # Use checkout_request_id as the deduplication key
            
            callback_id = f"stk_{checkout_request_id}" if checkout_request_id else None

            if not callback_id:
                logger.warning("STK callback received without CheckoutRequestID")
                return {
                    'success': False,
                    'error': 'Missing CheckoutRequestID',
                }

            # Check for duplicate callback
            if self._is_duplicate_callback(callback_id):
                logger.info(
                    f"Duplicate STK callback ignored: {checkout_request_id}"
                )
                return {
                    'success': True,
                    'status': 'duplicate',
                    'message': 'Duplicate callback ignored',
                }

            # Store callback for idempotency
            self._store_callback(callback_id, callback_data)

            # Check result
            if result_code == 0 and callback_metadata:
                # Payment successful — extract metadata
                items = callback_metadata.get('Item', [])

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

                logger.info(
                    f"STK payment successful | "
                    f"receipt={mpesa_receipt} | "
                    f"amount={amount} | "
                    f"phone={phone} | "
                    f"checkout={checkout_request_id}"
                )

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
                    'result_description': result_desc,
                }

            else:
                # Payment failed or cancelled
                logger.warning(
                    f"STK payment failed | "
                    f"checkout={checkout_request_id} | "
                    f"code={result_code} | "
                    f"desc={result_desc}"
                )

                return {
                    'success': False,
                    'status': 'failed',
                    'checkout_request_id': checkout_request_id,
                    'merchant_request_id': merchant_request_id,
                    'result_code': result_code,
                    'result_description': result_desc,
                    'error': result_desc or 'Payment failed',
                }

        except Exception as e:
            logger.error(
                f"Error processing STK callback: {e}", exc_info=True
            )
            return {'success': False, 'error': str(e)}

    # =========================================================================
    # B2C CALLBACK
    # =========================================================================

    def process_b2c_callback(
        self, callback_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        try:
            result = callback_data.get('Result', {})
            result_code = result.get('ResultCode')
            result_desc = result.get('ResultDesc', '')
            conversation_id = result.get('ConversationID')
            transaction_id = result.get('TransactionID')

            callback_id = f"b2c_{conversation_id}" if conversation_id else None

            if callback_id and self._is_duplicate_callback(callback_id):
                logger.info(f"Duplicate B2C callback ignored: {conversation_id}")
                return {
                    'success': True,
                    'status': 'duplicate',
                    'message': 'Duplicate callback ignored',
                }

            if callback_id:
                self._store_callback(callback_id, callback_data)

            if result_code == 0:
                logger.info(
                    f"B2C payment successful | conversation={conversation_id}"
                )
                return {
                    'success': True,
                    'status': 'completed',
                    'conversation_id': conversation_id,
                    'transaction_id': transaction_id,
                    'result_code': result_code,
                    'result_description': result_desc,
                }
            else:
                logger.warning(
                    f"B2C payment failed | "
                    f"conversation={conversation_id} | "
                    f"desc={result_desc}"
                )
                return {
                    'success': False,
                    'status': 'failed',
                    'conversation_id': conversation_id,
                    'result_code': result_code,
                    'result_description': result_desc,
                    'error': result_desc,
                }

        except Exception as e:
            logger.error(
                f"Error processing B2C callback: {e}", exc_info=True
            )
            return {'success': False, 'error': str(e)}

    # =========================================================================
    # C2B VALIDATION (PAYBILL)
    # =========================================================================

    def process_c2b_validation(
        self, validation_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        try:
            transaction_type = validation_data.get('TransactionType')
            transaction_id = validation_data.get('TransID')
            transaction_amount = validation_data.get('TransAmount')
            business_shortcode = validation_data.get('BusinessShortCode')
            bill_ref_number = validation_data.get('BillRefNumber')
            phone_number = validation_data.get('MSISDN')

            logger.info(
                f"C2B validation | "
                f"trans_id={transaction_id} | "
                f"amount={transaction_amount} | "
                f"ref={bill_ref_number} | "
                f"phone={phone_number}"
            )

            # Accept all transactions by default
           
            return {
                'ResultCode': 0,
                'ResultDesc': 'Success',
            }

        except Exception as e:
            logger.error(
                f"Error processing C2B validation: {e}", exc_info=True
            )
            return {
                'ResultCode': 1,
                'ResultDesc': 'Validation failed',
            }

    # =========================================================================
    # C2B CONFIRMATION (PAYBILL)
    # =========================================================================

    def process_c2b_confirmation(
        self, confirmation_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        try:
            transaction_id = confirmation_data.get('TransID')
            transaction_amount = confirmation_data.get('TransAmount')
            transaction_time = confirmation_data.get('TransTime')
            phone_number = confirmation_data.get('MSISDN')
            bill_ref_number = confirmation_data.get('BillRefNumber')
            business_shortcode = confirmation_data.get('BusinessShortCode')
            first_name = confirmation_data.get('FirstName', '')

            callback_id = f"c2b_{transaction_id}" if transaction_id else None

            if callback_id and self._is_duplicate_callback(callback_id):
                logger.info(f"Duplicate C2B confirmation ignored: {transaction_id}")
                return {
                    'success': True,
                    'status': 'duplicate',
                    'message': 'Duplicate confirmation ignored',
                }

            if callback_id:
                self._store_callback(callback_id, confirmation_data)

            logger.info(
                f"C2B payment confirmed | "
                f"trans_id={transaction_id} | "
                f"amount={transaction_amount} | "
                f"phone={phone_number} | "
                f"ref={bill_ref_number}"
            )

            # Return structured data for PaymentService to process
            return {
                'success': True,
                'status': 'completed',
                'transaction_id': transaction_id,
                'amount': float(transaction_amount) if transaction_amount else None,
                'phone': phone_number,
                'reference': bill_ref_number,
                'transaction_time': transaction_time,
                'shortcode': business_shortcode,
                'first_name': first_name,
                'payment_method': 'c2b',
                'mpesa_receipt': transaction_id,  # C2B uses TransID as receipt
                'result_code': 0,
                'result_description': 'Success',
            }

        except Exception as e:
            logger.error(
                f"Error processing C2B confirmation: {e}", exc_info=True
            )
            return {
                'ResultCode': 1,
                'ResultDesc': 'Processing failed',
            }

    # =========================================================================
    # IDEMPOTENCY
    # =========================================================================

    def _is_duplicate_callback(self, callback_id: str) -> bool:
        """
        Check if callback has already been processed.

        Uses Redis to track processed callback IDs.
        Prevents double-processing if Safaricom retries the webhook.
        """
        try:
            redis_key = f"mpesa:callback:{callback_id}"
            return redis_client.exists(redis_key) > 0
        except Exception as e:
            logger.warning(f"Failed to check duplicate callback: {e}")
            return False  # Process it on error (better than losing data)

    def _store_callback(self, callback_id: str, callback_data: Dict[str, Any]):
        """
        Store callback ID in Redis for idempotency tracking.

        TTL of 24 hours ensures the key is eventually cleaned up.
        """
        try:
            redis_key = f"mpesa:callback:{callback_id}"
            redis_client.setex(
                redis_key,
                self.callback_cache_ttl,
                json.dumps({'processed_at': datetime.utcnow().isoformat()}),
            )
        except Exception as e:
            logger.warning(f"Failed to store callback for idempotency: {e}")