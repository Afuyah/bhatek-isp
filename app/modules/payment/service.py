from typing import Dict, Any, Optional
from uuid import UUID
from datetime import datetime
import uuid

from app.modules.payment.repository import TransactionRepository, PaymentAccountRepository
from app.models.payment import Transaction, PaymentWebhookLog
from app.integrations.mpesa.client import MpesaClient
from app.integrations.mpesa.callback_handler import MpesaCallbackHandler
from app.core.logging.logger import logger
from app.core.exceptions.handlers import BusinessError, NotFoundError, ValidationError
from app.core.database.session import db


class PaymentService:
    """
    Business logic for payment processing and subscription activation.

    Handles the complete payment lifecycle:
        1. Initiate payment (STK Push, bank transfer, voucher)
        2. Process callback from payment provider
        3. Activate subscription on successful payment
        4. Sync subscriber to RADIUS
        5. Register device MAC for auto-connect
    """

    def __init__(self):
        self.transaction_repo = TransactionRepository()
        self.payment_account_repo = PaymentAccountRepository()
        self.callback_handler = MpesaCallbackHandler()

        # Lazy-loaded dependencies
        self._subscriber_service = None
        self._billing_service = None
        self._radius_sync_service = None

    @property
    def subscriber_service(self):
        if self._subscriber_service is None:
            from app.modules.subscriber.service import SubscriberService
            self._subscriber_service = SubscriberService()
        return self._subscriber_service

    @property
    def billing_service(self):
        if self._billing_service is None:
            from app.modules.billing.service import BillingService
            self._billing_service = BillingService()
        return self._billing_service

    @property
    def radius_sync_service(self):
        if self._radius_sync_service is None:
            from app.integrations.radius.radius_sync_service import RadiusSyncService
            self._radius_sync_service = RadiusSyncService()
        return self._radius_sync_service

    # =========================================================================
    # INITIATE PAYMENT
    # =========================================================================

    def process_payment(
        self,
        organization_id: UUID,
        amount: float,
        payment_method: str,
        payment_details: Dict[str, Any],
        subscriber_id: UUID = None,
        plan_id: UUID = None,
        device_mac: str = None,
        extra_data: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Process a payment — creates Transaction and initiates payment.

        Args:
            organization_id: Tenant organization UUID
            amount: Payment amount
            payment_method: 'mpesa', 'cash', 'bank_transfer', 'voucher'
            payment_details: Dict with phone, ip_address, user_agent
            subscriber_id: Optional existing subscriber UUID
            plan_id: UUID of the plan being purchased
            device_mac: MAC address to register on success
            extra_data: Additional metadata

        Returns:
            Dict with transaction status and details
        """
        # Generate unique transaction reference
        transaction_ref = f"TXN-{uuid.uuid4().hex[:12].upper()}"

        # Validate M-Pesa requirements
        if payment_method == 'mpesa':
            phone = payment_details.get('phone')
            if not phone:
                raise ValidationError("Phone number is required for M-Pesa payments")

        # Build transaction data
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
            'custom_data': {
                **(extra_data or {}),
                'plan_id': str(plan_id) if plan_id else None,
                'device_mac': device_mac,
            },
            'created_at': datetime.utcnow(),
        }

        transaction = self.transaction_repo.create(transaction_data)

        # Route to payment method handler
        if payment_method == 'mpesa':
            return self._process_mpesa_payment(
                organization_id, transaction, payment_details, extra_data
            )
        elif payment_method == 'cash':
            return self._process_cash_payment(transaction, payment_details)
        elif payment_method == 'bank_transfer':
            return self._process_bank_payment(transaction, payment_details)
        elif payment_method == 'voucher':
            return self._process_voucher_payment(transaction, payment_details)
        else:
            raise BusinessError(f"Unsupported payment method: {payment_method}")

    # =========================================================================
    # M-PESA PAYMENT
    # =========================================================================

    def _process_mpesa_payment(
        self,
        organization_id: UUID,
        transaction: Transaction,
        payment_details: Dict[str, Any],
        extra_data: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Process M-Pesa STK Push payment.

        Gets the org's default payment account, creates an MpesaClient,
        and sends an STK Push to the customer's phone.
        """
        # Get organization's default payment account
        payment_account = self.payment_account_repo.get_default(organization_id)
        if not payment_account:
            raise BusinessError(
                "No payment account configured for this organization"
            )

        # Create M-Pesa client with org credentials
        mpesa = MpesaClient(str(organization_id), payment_account.to_dict())

        phone = payment_details.get('phone')
        reference = transaction.transaction_reference[:12]
        description = (
            extra_data.get('plan_name', 'Internet Payment')[:13]
            if extra_data else "Internet Payment"
        )

        # Send STK Push
        result = mpesa.stk_push(
            phone_number=phone,
            amount=float(transaction.amount),
            reference=reference,
            description=description,
        )

        if result.get('success'):
            # Update transaction with checkout request ID
            updated_details = dict(payment_details)
            updated_details['checkout_request_id'] = result['checkout_request_id']

            self.transaction_repo.update(transaction.id, {
                'payment_details': updated_details,
                'custom_data': {
                    **(transaction.custom_data or {}),
                    **(extra_data or {}),
                },
            })

            return {
                'success': True,
                'status': 'pending',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'checkout_request_id': result['checkout_request_id'],
                'customer_message': result.get(
                    'customer_message',
                    'Please check your phone to complete payment',
                ),
                'message': 'STK Push sent. Enter PIN to complete payment.',
            }
        else:
            # Mark transaction as failed
            self.transaction_repo.update(transaction.id, {
                'status': 'failed',
                'failure_reason': result.get('error', 'Payment initiation failed'),
            })
            return {
                'success': False,
                'status': 'failed',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'error': result.get('error', 'Payment initiation failed'),
                'message': 'Failed to initiate payment. Please try again.',
            }

    # =========================================================================
    # CASH PAYMENT
    # =========================================================================

    def _process_cash_payment(
        self,
        transaction: Transaction,
        payment_details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process cash payment — marks as pending manual verification."""
        self.transaction_repo.update(transaction.id, {
            'status': 'pending',
            'payment_details': {
                **transaction.payment_details,
                'collected_by': payment_details.get('collected_by'),
                'receipt_number': payment_details.get('receipt_number'),
            },
        })
        return {
            'success': True,
            'status': 'pending',
            'transaction_id': str(transaction.id),
            'transaction_reference': transaction.transaction_reference,
            'message': 'Cash payment recorded. Awaiting verification.',
        }

    # =========================================================================
    # BANK TRANSFER
    # =========================================================================

    def _process_bank_payment(
        self,
        transaction: Transaction,
        payment_details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process bank transfer — requires manual verification."""
        self.transaction_repo.update(transaction.id, {
            'status': 'pending',
            'payment_details': {
                **transaction.payment_details,
                'bank_name': payment_details.get('bank_name'),
                'account_name': payment_details.get('account_name'),
                'reference_number': payment_details.get('reference_number'),
                'verification_status': 'pending',
            },
        })
        return {
            'success': True,
            'status': 'pending',
            'transaction_id': str(transaction.id),
            'transaction_reference': transaction.transaction_reference,
            'message': 'Bank transfer recorded. Awaiting verification.',
        }

    # =========================================================================
    # VOUCHER PAYMENT
    # =========================================================================

    def _process_voucher_payment(
        self,
        transaction: Transaction,
        payment_details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process voucher payment — validates and activates immediately."""
        voucher_code = payment_details.get('voucher_code')
        if not voucher_code:
            raise BusinessError("Voucher code is required")

        # Validate voucher via billing service
        from app.modules.billing.service import BillingService
        billing = BillingService()
        voucher = billing.validate_voucher(
            voucher_code, transaction.organization_id
        )
        if not voucher:
            raise BusinessError("Invalid or expired voucher code")

        # Mark transaction as successful immediately
        self.transaction_repo.update(transaction.id, {
            'status': 'success',
            'completed_at': datetime.utcnow(),
            'payment_details': {
                **transaction.payment_details,
                'voucher_code': voucher_code,
                'voucher_validated': True,
            },
        })

        # Activate subscription
        self._activate_subscription(transaction)

        return {
            'success': True,
            'status': 'success',
            'transaction_id': str(transaction.id),
            'transaction_reference': transaction.transaction_reference,
            'message': 'Voucher payment successful. Subscription activated.',
        }

    # =========================================================================
    # PAYMENT CALLBACK (WEBHOOK)
    # =========================================================================

    def process_callback(
        self,
        transaction_id: UUID,
        callback_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process payment callback from M-Pesa.

        On successful payment:
            1. Updates Transaction status
            2. Logs webhook for audit
            3. Finds or creates Subscriber by phone
            4. Creates Subscription linked to the purchased plan
            5. Syncs subscriber to RADIUS
            6. Registers device MAC for auto-connect
        """
        # Log webhook for audit
        self._log_webhook(callback_data)

        transaction = self.transaction_repo.get_by_id(transaction_id)
        if not transaction:
            logger.warning(f"Transaction not found for callback: {transaction_id}")
            return {'success': False, 'error': 'Transaction not found'}

        # Process callback via handler
        callback_result = self.callback_handler.process_stk_callback(
            callback_data
        )

        if not callback_result.get('success'):
            # Payment failed
            result_desc = callback_result.get('result_description', 'Payment failed')
            self.transaction_repo.update(transaction_id, {
                'status': 'failed',
                'failure_reason': result_desc,
                'callback_payload': callback_data,
            })
            logger.warning(f"Payment failed: {transaction_id} - {result_desc}")
            return {
                'success': False,
                'status': 'failed',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'error': result_desc,
                'message': 'Payment failed. Please try again.',
            }

        # Payment successful — extract details
        mpesa_receipt = callback_result.get('mpesa_receipt')
        amount = callback_result.get('amount')
        phone = callback_result.get('phone')

        # Check for duplicate receipt
        if mpesa_receipt:
            existing = self.transaction_repo.get_by_mpesa_receipt(
                mpesa_receipt, transaction.organization_id
            )
            if existing and existing.id != transaction_id:
                logger.warning(
                    f"Duplicate M-Pesa receipt: {mpesa_receipt}. "
                    f"Existing transaction: {existing.id}"
                )
                return {
                    'success': False,
                    'status': 'duplicate',
                    'transaction_id': str(transaction.id),
                    'message': 'This M-Pesa receipt has already been processed.',
                }

        # Update transaction as successful
        self.transaction_repo.update(transaction_id, {
            'status': 'success',
            'mpesa_receipt': mpesa_receipt,
            'completed_at': datetime.utcnow(),
            'callback_payload': callback_data,
            'payment_details': {
                **(transaction.payment_details or {}),
                'mpesa_result_code': callback_result.get('result_code'),
                'mpesa_result_desc': callback_result.get('result_description'),
                'mpesa_amount': amount,
                'mpesa_transaction_date': callback_result.get('transaction_date'),
            },
        })

        # Reload transaction to get updated state
        transaction = self.transaction_repo.get_by_id(transaction_id)

        # Activate subscription for the subscriber
        try:
            activation_result = self._activate_subscription(transaction, phone)
            logger.info(
                f"Payment successful and subscription activated: "
                f"{transaction_id} - Receipt: {mpesa_receipt} - "
                f"Subscriber: {activation_result.get('subscriber_id')}"
            )
            return {
                'success': True,
                'status': 'success',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'mpesa_receipt': mpesa_receipt,
                'amount': amount,
                'subscription_activated': activation_result.get('success', False),
                'subscriber_id': activation_result.get('subscriber_id'),
                'subscription_id': activation_result.get('subscription_id'),
                'message': 'Payment completed and subscription activated.',
            }
        except Exception as e:
            logger.error(
                f"Payment succeeded but subscription activation failed: "
                f"{transaction_id} - {e}",
                exc_info=True,
            )
            return {
                'success': True,
                'status': 'success',
                'transaction_id': str(transaction.id),
                'transaction_reference': transaction.transaction_reference,
                'mpesa_receipt': mpesa_receipt,
                'amount': amount,
                'subscription_activated': False,
                'activation_error': str(e),
                'message': (
                    'Payment received but subscription activation failed. '
                    'Support will resolve this shortly.'
                ),
            }

    # =========================================================================
    # SUBSCRIPTION ACTIVATION
    # =========================================================================

    def _activate_subscription(
        self,
        transaction: Transaction,
        phone: str = None,
    ) -> Dict[str, Any]:
        """
        Activate a subscription after successful payment.

        Steps:
            1. Find or create subscriber by phone
            2. Get plan_id from transaction.custom_data
            3. Create Subscription
            4. Link subscription to transaction
            5. Sync subscriber to RADIUS
            6. Register device MAC
        """
        org_id = transaction.organization_id

        # Step 1: Find or create subscriber
        subscriber = None
        if transaction.subscriber_id:
            # Existing subscriber linked to transaction
            subscriber = self.subscriber_service.get_subscriber(
                transaction.subscriber_id, org_id
            )
        elif phone:
            # Find by phone or create new
            subscriber, created = self.subscriber_service.get_or_create_hotspot_subscriber(
                organization_id=org_id,
                phone=phone,
            )
            # Link subscriber to transaction
            self.transaction_repo.update(transaction.id, {
                'subscriber_id': subscriber.id,
            })

        if not subscriber:
            logger.error(
                f"Cannot activate subscription: no subscriber for "
                f"transaction {transaction.id}"
            )
            return {'success': False, 'error': 'No subscriber found'}

        # Step 2: Get plan_id from transaction custom_data
        plan_id = None
        if transaction.custom_data:
            plan_id_str = transaction.custom_data.get('plan_id')
            if plan_id_str:
                try:
                    plan_id = UUID(plan_id_str)
                except (ValueError, AttributeError):
                    logger.error(
                        f"Invalid plan_id in transaction {transaction.id}: "
                        f"{plan_id_str}"
                    )

        if not plan_id:
            # Try to resolve plan by amount
            plan_id = self._resolve_plan_by_amount(
                float(transaction.amount), org_id
            )

        if not plan_id:
            logger.error(
                f"No plan_id found for transaction {transaction.id}"
            )
            return {'success': False, 'error': 'No plan associated with this payment'}

        # Step 3: Create subscription
        try:
            subscription = self.subscriber_service.create_subscription(
                subscriber_id=subscriber.id,
                organization_id=org_id,
                plan_id=plan_id,
                auto_renew=False,
            )

            # Step 4: Link subscription to transaction
            self.transaction_repo.update(transaction.id, {
                'subscription_id': subscription.id,
            })

            logger.info(
                f"Subscription {subscription.id} created for "
                f"subscriber {subscriber.id} via transaction {transaction.id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to create subscription for transaction "
                f"{transaction.id}: {e}"
            )
            return {'success': False, 'error': str(e)}

        # Step 5: Sync subscriber to RADIUS
        try:
            self.radius_sync_service.sync_hotspot_user_to_radius(
                subscriber, subscription
            )
        except Exception as e:
            logger.warning(
                f"RADIUS sync failed for subscriber {subscriber.id}: {e}"
            )

        # Step 6: Register device MAC if provided
        device_mac = None
        if transaction.custom_data:
            device_mac = transaction.custom_data.get('device_mac')

        if device_mac:
            try:
                self.subscriber_service.add_device(
                    subscriber_id=subscriber.id,
                    organization_id=org_id,
                    mac_address=device_mac,
                )
                logger.info(
                    f"Device {device_mac} registered for subscriber "
                    f"{subscriber.id}"
                )
            except Exception as e:
                logger.warning(
                    f"Device registration failed for {device_mac}: {e}"
                )

        return {
            'success': True,
            'subscriber_id': str(subscriber.id),
            'subscription_id': str(subscription.id),
            'plan_name': subscription.plan.name if subscription.plan else None,
            'expiry_time': subscription.expiry_time.isoformat(),
        }

    def _resolve_plan_by_amount(
        self,
        amount: float,
        organization_id: UUID,
    ) -> Optional[UUID]:
        """
        Resolve a plan by the payment amount.

        Used as fallback when plan_id is not in transaction custom_data.
        Matches the plan price to the amount paid.
        """
        from app.modules.subscriber.repository import PlanRepository
        plan_repo = PlanRepository()
        plans = plan_repo.get_by_organization(organization_id, is_active=True)

        for plan in plans:
            if float(plan.price) == amount:
                return plan.id

        logger.warning(
            f"No plan found for amount {amount} in org {organization_id}"
        )
        return None

    # =========================================================================
    # WEBHOOK LOGGING
    # =========================================================================

    def _log_webhook(self, callback_data: Dict[str, Any]) -> None:
        """
        Log incoming payment webhook for audit trail.

        Extracts organization context from the callback data
        and stores the raw payload.
        """
        try:
            stk_callback = callback_data.get('Body', {}).get('stkCallback', {})
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            merchant_request_id = stk_callback.get('MerchantRequestID')

            # Try to find the transaction to get org context
            org_id = None
            if checkout_request_id:
                transaction = self.transaction_repo.get_by_checkout_id(
                    checkout_request_id
                )
                if transaction:
                    org_id = transaction.organization_id

            webhook_log = PaymentWebhookLog(
                organization_id=org_id,
                webhook_type='stk_callback',
                provider='mpesa',
                request_id=checkout_request_id or merchant_request_id or str(uuid.uuid4()),
                payload=callback_data,
                headers={},  # Would capture from request in production
                processed=True,
                processed_at=datetime.utcnow(),
            )
            db.session.add(webhook_log)
            db.session.commit()

        except Exception as e:
            logger.warning(f"Failed to log webhook: {e}")

    # =========================================================================
    # PAYMENT VERIFICATION
    # =========================================================================

    def verify_payment_status(
        self,
        transaction_id: UUID,
        checkout_request_id: str = None,
    ) -> Dict[str, Any]:
        """
        Verify payment status by querying M-Pesa directly.

        Used when callback is delayed or missed.
        """
        transaction = self.transaction_repo.get_by_id(transaction_id)
        if not transaction:
            raise NotFoundError("Transaction not found")

        if transaction.status != 'pending':
            return {
                'success': True,
                'status': transaction.status,
                'transaction_id': str(transaction.id),
                'message': f'Payment already {transaction.status}',
            }

        # Get payment account
        payment_account = self.payment_account_repo.get_default(
            transaction.organization_id
        )
        if not payment_account:
            raise BusinessError("No payment account configured")

        # Query M-Pesa
        mpesa = MpesaClient(
            str(transaction.organization_id),
            payment_account.to_dict(),
        )

        checkout_id = (
            checkout_request_id
            or transaction.payment_details.get('checkout_request_id')
        )
        if not checkout_id:
            raise BusinessError("No checkout request ID found")

        result = mpesa.query_status(checkout_id)

        if result.get('success') and result.get('status') == 'completed':
            self.transaction_repo.update(transaction_id, {
                'status': 'success',
                'mpesa_receipt': result.get('mpesa_receipt'),
                'completed_at': datetime.utcnow(),
            })
            # Activate subscription
            self._activate_subscription(transaction)
            return {
                'success': True,
                'status': 'success',
                'transaction_id': str(transaction.id),
                'mpesa_receipt': result.get('mpesa_receipt'),
                'message': 'Payment completed and subscription activated',
            }
        elif result.get('status') == 'failed':
            self.transaction_repo.update(transaction_id, {
                'status': 'failed',
                'failure_reason': result.get('result_description'),
            })
            return {
                'success': False,
                'status': 'failed',
                'transaction_id': str(transaction.id),
                'error': result.get('result_description'),
                'message': 'Payment failed',
            }
        else:
            return {
                'success': True,
                'status': 'pending',
                'transaction_id': str(transaction.id),
                'message': 'Payment still pending. Complete transaction on phone.',
            }

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    def get_transaction(self, transaction_id: UUID) -> Optional[Transaction]:
        """Get transaction by ID."""
        return self.transaction_repo.get_by_id(transaction_id)

    def get_transaction_by_mpesa_receipt(
        self,
        mpesa_receipt: str,
        organization_id: UUID,
    ) -> Optional[Transaction]:
        """
        Get transaction by M-Pesa receipt number.

        Used by self-remediation flow when user enters M-Pesa code
        in the captive portal.
        """
        return self.transaction_repo.get_by_mpesa_receipt(
            mpesa_receipt, organization_id
        )

    def get_transactions_by_subscriber(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        limit: int = 50,
    ) -> list:
        """Get all transactions for a subscriber."""
        return self.transaction_repo.get_by_subscriber(
            subscriber_id, organization_id, limit
        )

    # =========================================================================
    # REFUND
    # =========================================================================

    def refund_transaction(
        self,
        transaction_id: UUID,
        reason: str,
        amount: float = None,
    ) -> Dict[str, Any]:
        """
        Refund a successful transaction.

        Creates a Refund record and marks the transaction as refunded.
        Also cancels the associated subscription.
        """
        transaction = self.transaction_repo.get_by_id(transaction_id)
        if not transaction:
            raise NotFoundError("Transaction not found")

        if transaction.status != 'success':
            raise BusinessError("Only successful transactions can be refunded")

        refund_amount = amount or float(transaction.amount)

        # Create refund record
        from app.models.payment import Refund

        refund = Refund(
            organization_id=transaction.organization_id,
            transaction_id=transaction.id,
            refund_reference=f"REF-{uuid.uuid4().hex[:12].upper()}",
            amount=refund_amount,
            reason=reason,
            status='pending',
        )
        db.session.add(refund)

        # Mark transaction as refunded
        transaction.status = 'refunded'

        # Cancel associated subscription if exists
        if transaction.subscription_id:
            try:
                self.subscriber_service.cancel_subscription(
                    transaction.subscription_id,
                    transaction.organization_id,
                    reason=f"Payment refunded: {reason}",
                )

                # Remove from RADIUS
                subscriber = self.subscriber_service.get_subscriber(
                    transaction.subscriber_id,
                    transaction.organization_id,
                )
                self.radius_sync_service.remove_subscriber_from_radius(
                    subscriber
                )

                logger.info(
                    f"Subscription {transaction.subscription_id} cancelled "
                    f"due to refund of transaction {transaction_id}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to cancel subscription during refund: {e}"
                )

        db.session.commit()

        logger.info(
            f"Refund initiated for transaction {transaction_id}: "
            f"{refund_amount}"
        )

        return {
            'success': True,
            'refund_id': str(refund.id),
            'refund_reference': refund.refund_reference,
            'amount': refund_amount,
            'status': 'pending',
            'message': 'Refund initiated and subscription cancelled',
        }