from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.payment.service import PaymentService
from app.modules.payment.schemas import (
    PaymentInitiateSchema,
    PaymentCallbackSchema,
    RefundSchema,
)
from app.core.security.jwt import token_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import (
    NotFoundError,
    BusinessError,
    ValidationError as AppValidationError,
)


class PaymentController:

    def __init__(self):
        self.service = PaymentService()

    # =========================================================================
    # INITIATE PAYMENT
    # =========================================================================

    @token_required
    def initiate_payment(self):
        """
        POST /api/v1/payments/initiate

        Initiate a payment. For M-Pesa, sends STK Push to customer's phone.

        Request body:
            {
                "amount": 100.00,
                "payment_method": "mpesa",
                "phone": "254712345678",
                "plan_id": "uuid-of-plan",
                "device_mac": "AA:BB:CC:DD:EE:FF",
                "subscriber_id": null
            }

        subscriber_id is optional — new users will be auto-created on payment success.
        """
        try:
            data = PaymentInitiateSchema().load(request.json)

            result = self.service.process_payment(
                organization_id=g.organization_id,
                amount=data['amount'],
                payment_method=data['payment_method'],
                payment_details={
                    'phone': data.get('phone'),
                    'ip_address': request.remote_addr,
                    'user_agent': request.headers.get('User-Agent', ''),
                },
                subscriber_id=data.get('subscriber_id'),
                plan_id=data.get('plan_id'),
                device_mac=data.get('device_mac'),
                extra_data=data.get('metadata'),
            )

            return jsonify(result), 200

        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except AppValidationError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'VALIDATION_ERROR',
            }), 400
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'BUSINESS_ERROR',
            }), 409
        except Exception as e:
            logger.error(f"Initiate payment error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # M-PESA CALLBACK (PUBLIC — NO AUTH)
    # =========================================================================

    def mpesa_callback(self):
        """
        POST /api/v1/payments/mpesa/callback  (PUBLIC)

        Receives payment result webhook from Safaricom.
        On success: activates subscription, syncs RADIUS, registers device.

        No JWT required — Safaricom calls this directly.
        """
        try:
            callback_data = request.get_json()

            if not callback_data:
                logger.warning("M-Pesa callback received with empty body")
                return jsonify({'success': True}), 200

            # Extract checkout request ID from callback
            stk_callback = (
                callback_data.get('Body', {}).get('stkCallback', {})
            )
            checkout_request_id = stk_callback.get('CheckoutRequestID')

            if not checkout_request_id:
                logger.warning(
                    "M-Pesa callback received without CheckoutRequestID"
                )
                return jsonify({'success': True}), 200

            # Find transaction by checkout request ID
            transaction = self.service.transaction_repo.get_by_checkout_id(
                checkout_request_id
            )

            if transaction:
                result = self.service.process_callback(
                    transaction.id, callback_data
                )

                logger.info(
                    f"M-Pesa callback processed: "
                    f"transaction={transaction.id}, "
                    f"status={result.get('status')}, "
                    f"subscription_activated={result.get('subscription_activated')}"
                )

                return jsonify(result), 200
            else:
                # Transaction not found — log and acknowledge
                logger.warning(
                    f"No transaction found for checkout_request_id: "
                    f"{checkout_request_id}. Callback data stored for audit."
                )
                return jsonify({
                    'success': True,
                    'message': 'Callback received but no matching transaction found',
                }), 200

        except Exception as e:
            logger.error(f"M-Pesa callback error: {e}", exc_info=True)
            # Always return 200 to Safaricom to prevent retries
            return jsonify({
                'success': False,
                'error': 'Internal error processing callback',
            }), 200

    # =========================================================================
    # VERIFY PAYMENT
    # =========================================================================

    @token_required
    def verify_payment(self, transaction_id):
        """
        GET /api/v1/payments/verify/<transaction_id>

        Verify payment status by querying M-Pesa.
        Used when callback is delayed or missed.
        """
        try:
            transaction_uuid = UUID(transaction_id)
            result = self.service.verify_payment_status(transaction_uuid)
            return jsonify(result), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid transaction ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'BUSINESS_ERROR',
            }), 409
        except Exception as e:
            logger.error(f"Verify payment error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # GET TRANSACTION
    # =========================================================================

    @token_required
    def get_transaction(self, transaction_id):
        """
        GET /api/v1/payments/transactions/<transaction_id>

        Get transaction details by ID.
        """
        try:
            transaction_uuid = UUID(transaction_id)
            transaction = self.service.get_transaction(transaction_uuid)

            if not transaction:
                return jsonify({
                    'success': False,
                    'error': 'Transaction not found',
                    'error_code': 'NOT_FOUND',
                }), 404

            # Ensure tenant isolation
            if transaction.organization_id != g.organization_id:
                return jsonify({
                    'success': False,
                    'error': 'Transaction not found',
                    'error_code': 'NOT_FOUND',
                }), 404

            return jsonify({
                'success': True,
                'transaction': transaction.to_dict(),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid transaction ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(f"Get transaction error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # LIST TRANSACTIONS
    # =========================================================================

    @token_required
    def list_transactions(self):
        """
        GET /api/v1/payments/transactions

        List transactions with pagination and filters.

        Query params:
            page, per_page, subscriber_id, status, payment_method
        """
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page

            subscriber_id = request.args.get('subscriber_id')
            status = request.args.get('status')
            payment_method = request.args.get('payment_method')

            # Convert subscriber_id to UUID if provided
            subscriber_uuid = None
            if subscriber_id:
                try:
                    subscriber_uuid = UUID(subscriber_id)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'error': 'Invalid subscriber_id format',
                        'error_code': 'INVALID_UUID',
                    }), 400

            transactions = self.service.transaction_repo.get_by_organization(
                organization_id=g.organization_id,
                skip=skip,
                limit=per_page,
                subscriber_id=subscriber_uuid,
                status=status,
                payment_method=payment_method,
            )

            total = self.service.transaction_repo.count_by_organization(
                organization_id=g.organization_id,
                subscriber_id=subscriber_uuid,
                status=status,
                payment_method=payment_method,
            )

            # Get stats for summary
            stats = self.service.transaction_repo.get_payment_stats(
                g.organization_id
            )

            return jsonify({
                'success': True,
                'transactions': [t.to_dict() for t in transactions],
                'summary': stats,
                'pagination': {
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'pages': (
                        (total + per_page - 1) // per_page
                        if total > 0 else 0
                    ),
                    'has_next': (page * per_page) < total,
                    'has_prev': page > 1,
                },
            }), 200

        except Exception as e:
            logger.error(f"List transactions error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # REFUND
    # =========================================================================

    @token_required
    def refund_transaction(self, transaction_id):
        """
        POST /api/v1/payments/transactions/<transaction_id>/refund

        Refund a successful transaction.
        Cancels associated subscription and removes from RADIUS.
        """
        try:
            transaction_uuid = UUID(transaction_id)
            data = request.get_json() or {}
            reason = data.get('reason', 'Manual refund')
            amount = data.get('amount')

            result = self.service.refund_transaction(
                transaction_id=transaction_uuid,
                reason=reason,
                amount=amount,
            )

            return jsonify(result), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid transaction ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'BUSINESS_ERROR',
            }), 409
        except Exception as e:
            logger.error(f"Refund transaction error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # DASHBOARD STATS
    # =========================================================================

    @token_required
    def get_payment_stats(self):
        """
        GET /api/v1/payments/stats

        Get payment statistics for the organization dashboard.
        """
        try:
            stats = self.service.transaction_repo.get_payment_stats(
                g.organization_id
            )
            return jsonify({
                'success': True,
                'stats': stats,
            }), 200

        except Exception as e:
            logger.error(f"Get payment stats error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500