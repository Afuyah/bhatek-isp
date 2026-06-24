from typing import Dict, Any, Optional
from datetime import datetime
import json
import uuid

from app.core.logging.logger import logger
from app.core.database.redis_client import redis_client
from app.integrations.mpesa.client import MpesaClient


class STKPushService:
    """
    Service for managing STK Push payment transactions.

    Tracks transaction state in Redis with a 1-hour TTL.
    Uses Celery for background status polling.
    """

    def __init__(self):
        self.transaction_cache_ttl = 3600  # 1 hour

    # =========================================================================
    # INITIATE PAYMENT
    # =========================================================================

    def initiate_payment(
        self,
        organization_id: str,
        payment_account: Dict[str, Any],
        phone_number: str,
        amount: float,
        reference: str,
        description: str,
        plan_id: str = None,
        device_mac: str = None,
        subscriber_id: str = None,
        callback_url: str = None,
    ) -> Dict[str, Any]:
        """
        Initiate STK Push payment with Redis state tracking.

        Args:
            organization_id: Organization UUID
            payment_account: Decrypted payment account dict
            phone_number: Customer phone (254XXXXXXXXX)
            amount: Payment amount
            reference: Payment reference (max 12 chars)
            description: Payment description (max 13 chars)
            plan_id: Optional plan UUID being purchased
            device_mac: Optional device MAC to register on success
            subscriber_id: Optional existing subscriber UUID
            callback_url: Optional custom callback URL

        Returns:
            Dict with transaction_id, checkout_request_id, customer_message
        """
        # Generate unique tracking ID
        transaction_id = str(uuid.uuid4())

        # Store transaction state in Redis
        transaction_state = {
            'transaction_id': transaction_id,
            'organization_id': organization_id,
            'phone_number': phone_number,
            'amount': amount,
            'reference': reference,
            'description': description,
            'plan_id': plan_id,
            'device_mac': device_mac,
            'subscriber_id': subscriber_id,
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat(),
            'attempts': 0,
            'checkout_request_id': None,
            'mpesa_receipt': None,
        }

        redis_key = f"stk_push:{transaction_id}"
        redis_client.setex(
            redis_key,
            self.transaction_cache_ttl,
            json.dumps(transaction_state),
        )

        # Initialize M-Pesa client with org credentials
        mpesa = MpesaClient(organization_id, payment_account)

        # Send STK Push to customer's phone
        result = mpesa.stk_push(
            phone_number=phone_number,
            amount=amount,
            reference=reference[:12],
            description=description[:13],
            callback_url=callback_url,
        )

        if result.get('success'):
            # Update state to processing
            transaction_state['status'] = 'processing'
            transaction_state['checkout_request_id'] = result.get(
                'checkout_request_id'
            )
            redis_client.setex(
                redis_key,
                self.transaction_cache_ttl,
                json.dumps(transaction_state),
            )

            # Schedule background status query via Celery
            self._schedule_status_query(
                transaction_id=transaction_id,
                checkout_request_id=result['checkout_request_id'],
                organization_id=organization_id,
                payment_account=payment_account,
            )

            logger.info(
                f"STK Push initiated | "
                f"txn={transaction_id} | "
                f"checkout={result['checkout_request_id']} | "
                f"phone={phone_number} | "
                f"amount={amount}"
            )

            return {
                'success': True,
                'transaction_id': transaction_id,
                'checkout_request_id': result['checkout_request_id'],
                'customer_message': result.get(
                    'customer_message',
                    'Please enter your PIN to complete payment',
                ),
            }
        else:
            # Mark as failed
            transaction_state['status'] = 'failed'
            transaction_state['error'] = result.get(
                'error', 'STK Push failed'
            )
            redis_client.setex(
                redis_key,
                self.transaction_cache_ttl,
                json.dumps(transaction_state),
            )

            logger.error(
                f"STK Push failed | "
                f"txn={transaction_id} | "
                f"error={result.get('error')}"
            )

            return {
                'success': False,
                'transaction_id': transaction_id,
                'error': result.get('error', 'STK Push failed'),
                'response_description': result.get('response_description'),
            }

    # =========================================================================
    # BACKGROUND STATUS QUERY
    # =========================================================================

    def _schedule_status_query(
        self,
        transaction_id: str,
        checkout_request_id: str,
        organization_id: str,
        payment_account: Dict[str, Any],
    ):
        """
        Schedule a Celery task to query payment status.

        The task will poll Safaricom after a delay to check if the
        payment was completed (in case the callback is missed).
        """
        try:
            from app.tasks.payment import query_payment_status
            query_payment_status.delay(
                transaction_id,
                checkout_request_id,
                organization_id,
            )
            logger.debug(
                f"Scheduled status query for transaction {transaction_id}"
            )
        except ImportError:
            logger.warning(
                f"Celery task not available — status query not scheduled "
                f"for transaction {transaction_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to schedule status query: {e}"
            )

    # =========================================================================
    # UPDATE TRANSACTION STATUS
    # =========================================================================

    def update_transaction_status(
        self,
        transaction_id: str,
        status: str,
        mpesa_receipt: str = None,
        result_code: str = None,
        result_description: str = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update transaction state in Redis after callback or status query.

        Called when:
            - Safaricom sends a successful callback
            - Background Celery task confirms payment
            - Payment is confirmed failed

        Args:
            transaction_id: The internal transaction UUID
            status: 'success', 'failed', 'cancelled'
            mpesa_receipt: M-Pesa receipt number (on success)
            result_code: Raw M-Pesa result code
            result_description: Human-readable result description

        Returns:
            Updated transaction state dict, or None if not found in Redis
        """
        redis_key = f"stk_push:{transaction_id}"
        transaction_data = redis_client.get(redis_key)

        if not transaction_data:
            logger.warning(
                f"Transaction {transaction_id} not found in Redis "
                f"(may have expired)"
            )
            return None

        # Parse existing state
        try:
            transaction_state = json.loads(
                transaction_data.decode()
                if isinstance(transaction_data, bytes)
                else transaction_data
            )
        except json.JSONDecodeError:
            logger.error(
                f"Failed to parse transaction state for {transaction_id}"
            )
            return None

        # Update state
        transaction_state['status'] = status
        transaction_state['updated_at'] = datetime.utcnow().isoformat()

        if mpesa_receipt:
            transaction_state['mpesa_receipt'] = mpesa_receipt
        if result_code is not None:
            transaction_state['result_code'] = result_code
        if result_description:
            transaction_state['result_description'] = result_description

        if status == 'success':
            transaction_state['completed_at'] = datetime.utcnow().isoformat()

        # Persist updated state
        redis_client.setex(
            redis_key,
            self.transaction_cache_ttl,
            json.dumps(transaction_state),
        )

        logger.info(
            f"Transaction {transaction_id} status updated to '{status}'"
        )

        return transaction_state

    # =========================================================================
    # GET TRANSACTION STATUS
    # =========================================================================

    def get_transaction_status(
        self, transaction_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get current transaction state from Redis.

        Args:
            transaction_id: The internal transaction UUID

        Returns:
            Transaction state dict, or None if not found/expired
        """
        redis_key = f"stk_push:{transaction_id}"
        transaction_data = redis_client.get(redis_key)

        if not transaction_data:
            return None

        try:
            return json.loads(
                transaction_data.decode()
                if isinstance(transaction_data, bytes)
                else transaction_data
            )
        except json.JSONDecodeError:
            logger.error(
                f"Failed to parse transaction state for {transaction_id}"
            )
            return None

    # =========================================================================
    # DELETE TRANSACTION STATE
    # =========================================================================

    def clear_transaction_state(self, transaction_id: str) -> bool:
        """
        Remove transaction state from Redis.

        Called after the transaction has been fully processed and
        persisted to the database.
        """
        try:
            redis_key = f"stk_push:{transaction_id}"
            redis_client.delete(redis_key)
            logger.debug(f"Cleared transaction state for {transaction_id}")
            return True
        except Exception as e:
            logger.warning(
                f"Failed to clear transaction state for "
                f"{transaction_id}: {e}"
            )
            return False