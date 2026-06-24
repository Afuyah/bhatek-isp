from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID
from datetime import datetime

from app.modules.billing.service import BillingService
from app.modules.billing.schemas import (
    PlanCreateSchema, PlanUpdateSchema,
    VoucherCreateSchema, VoucherBatchCreateSchema, RedeemVoucherSchema,
    DiscountCouponCreateSchema,
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import (
    NotFoundError, BusinessError,
    ValidationError as AppValidationError,
)


class BillingController:
    """Billing controller for plans, subscriptions, vouchers, and invoices."""

    def __init__(self):
        self.service = BillingService()

    # PLAN ENDPOINTS

    @token_required
    @permission_required('plan_create')
    def create_plan(self):
        """
        POST /api/v1/billing/plans

        Create a new plan with dynamic validity.
        """
        try:
            data = PlanCreateSchema().load(request.json)
            plan = self.service.create_plan(g.organization_id, data)
            return jsonify({
                'success': True,
                'plan': plan.to_dict(),
                'message': 'Plan created successfully',
            }), 201
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
            logger.error(f"Create plan error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_plans(self):
        """
        GET /api/v1/billing/plans

        List plans with filters and pagination.
        Query params: page, per_page, only_active, plan_type
        """
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page
            only_active = request.args.get('only_active', 'true').lower() == 'true'
            plan_type = request.args.get('plan_type')

            plans = self.service.get_plans(
                g.organization_id, skip, per_page, only_active, plan_type
            )
            total = self.service.plan_repo.count_by_organization(
                g.organization_id, is_active=only_active if only_active else None
            )

            return jsonify({
                'success': True,
                'plans': [p.to_dict() for p in plans],
                'pagination': {
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'pages': (total + per_page - 1) // per_page if total > 0 else 0,
                    'has_next': (page * per_page) < total,
                    'has_prev': page > 1,
                },
            }), 200
        except Exception as e:
            logger.error(f"Get plans error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_plan(self, plan_id):
        """GET /api/v1/billing/plans/<plan_id>"""
        try:
            plan_uuid = UUID(plan_id)
            plan = self.service.get_plan(plan_uuid, g.organization_id)
            return jsonify({
                'success': True,
                'plan': plan.to_dict(),
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid plan ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get plan error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('plan_update')
    def update_plan(self, plan_id):
        """PUT /api/v1/billing/plans/<plan_id>"""
        try:
            data = PlanUpdateSchema().load(request.json)
            plan_uuid = UUID(plan_id)
            plan = self.service.update_plan(plan_uuid, g.organization_id, data)
            return jsonify({
                'success': True,
                'plan': plan.to_dict(),
                'message': 'Plan updated successfully',
            }), 200
        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid plan ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Update plan error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('plan_delete')
    def delete_plan(self, plan_id):
        """DELETE /api/v1/billing/plans/<plan_id>?soft=true"""
        try:
            plan_uuid = UUID(plan_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            self.service.delete_plan(plan_uuid, g.organization_id, soft)
            message = (
                'Plan deactivated successfully'
                if soft else 'Plan deleted permanently'
            )
            return jsonify({
                'success': True,
                'message': message,
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid plan ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'BUSINESS_ERROR',
            }), 409
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Delete plan error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_public_plans(self):
        """
        GET /api/v1/billing/plans/public

        Get public plans for the captive portal.
        No special permissions required — these are public-facing.
        """
        try:
            plans = self.service.get_public_plans(g.organization_id)
            return jsonify({
                'success': True,
                'plans': [p.to_dict() for p in plans],
            }), 200
        except Exception as e:
            logger.error(f"Get public plans error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # VOUCHER ENDPOINTS

    @token_required
    @permission_required('voucher_create')
    def create_voucher(self):
        """
        POST /api/v1/billing/vouchers

        Create a single voucher with optional validity override.
        """
        try:
            data = VoucherCreateSchema().load(request.json)
            voucher = self.service.create_voucher(
                organization_id=g.organization_id,
                plan_id=UUID(data['plan_id']),
                max_uses=data.get('max_uses', 1),
                validity_value=data.get('validity_value'),
                validity_unit=data.get('validity_unit'),
                activation_type=data.get('activation_type', 'immediate'),
                created_by=g.user_id,
            )
            return jsonify({
                'success': True,
                'voucher': voucher.to_dict(),
                'message': 'Voucher created successfully',
            }), 201
        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid plan ID format',
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
            logger.error(f"Create voucher error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('voucher_create')
    def create_voucher_batch(self):
        """
        POST /api/v1/billing/vouchers/batch

        Create a batch of vouchers (max 1000).
        """
        try:
            data = VoucherBatchCreateSchema().load(request.json)
            batch = self.service.create_voucher_batch(
                organization_id=g.organization_id,
                plan_id=UUID(data['plan_id']),
                batch_name=data['batch_name'],
                quantity=data['quantity'],
                validity_value=data.get('validity_value'),
                validity_unit=data.get('validity_unit'),
                expires_in_days=data.get('expires_in_days'),
                activation_type=data.get('activation_type', 'immediate'),
                created_by=g.user_id,
            )
            return jsonify({
                'success': True,
                'batch': batch.to_dict(),
                'message': f'Batch of {data["quantity"]} vouchers created',
            }), 201
        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid plan ID format',
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
            logger.error(f"Create voucher batch error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def redeem_voucher(self):
        """
        POST /api/v1/billing/vouchers/redeem

        Redeem a voucher for a subscriber.
        Creates subscription, syncs RADIUS, registers device.
        """
        try:
            data = RedeemVoucherSchema().load(request.json)
            result = self.service.redeem_voucher(
                organization_id=g.organization_id,
                voucher_code=data['voucher_code'],
                subscriber_id=UUID(data['subscriber_id']),
                router_id=UUID(data['router_id']) if data.get('router_id') else None,
                device_mac=data.get('device_mac'),
            )
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({
                'success': False,
                'error': 'Validation error',
                'error_code': 'VALIDATION_ERROR',
                'details': e.messages,
            }), 400
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid UUID format',
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
            }), 400
        except Exception as e:
            logger.error(f"Redeem voucher error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def check_voucher(self, voucher_code):
        """
        GET /api/v1/billing/vouchers/check/<code>

        Check voucher validity WITHOUT redeeming.
        Shows what the voucher provides before committing.
        """
        try:
            info = self.service.get_voucher_info(
                voucher_code, g.organization_id
            )
            return jsonify({
                'success': True,
                'voucher': info,
            }), 200
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Check voucher error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('voucher_manage')
    def void_voucher(self, voucher_id):
        """
        POST /api/v1/billing/vouchers/<voucher_id>/void

        Manually void a voucher so it cannot be redeemed.
        """
        try:
            voucher_uuid = UUID(voucher_id)
            self.service.void_voucher(voucher_uuid, g.organization_id)
            return jsonify({
                'success': True,
                'message': 'Voucher voided successfully',
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid voucher ID format',
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
            logger.error(f"Void voucher error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_voucher_batch(self, batch_id):
        """GET /api/v1/billing/vouchers/batches/<batch_id>"""
        try:
            batch_uuid = UUID(batch_id)
            batch = self.service.get_voucher_batch(batch_uuid, g.organization_id)
            return jsonify({
                'success': True,
                'batch': batch.to_dict(),
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid batch ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get voucher batch error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_batch_vouchers(self, batch_id):
        """GET /api/v1/billing/vouchers/batches/<batch_id>/vouchers"""
        try:
            batch_uuid = UUID(batch_id)
            vouchers = self.service.get_batch_vouchers(
                batch_uuid, g.organization_id
            )
            return jsonify({
                'success': True,
                'vouchers': [v.to_dict() for v in vouchers],
                'count': len(vouchers),
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid batch ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(f"Get batch vouchers error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # SUBSCRIPTION ENDPOINTS

    @token_required
    def get_subscription(self, subscription_id):
        """GET /api/v1/billing/subscriptions/<subscription_id>"""
        try:
            sub_uuid = UUID(subscription_id)
            subscription = self.service.get_subscription(
                sub_uuid, g.organization_id
            )
            return jsonify({
                'success': True,
                'subscription': subscription.to_dict(include_plan=True),
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscription ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get subscription error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_subscriber_subscriptions(self, subscriber_id):
        """GET /api/v1/billing/subscribers/<subscriber_id>/subscriptions"""
        try:
            sub_uuid = UUID(subscriber_id)
            subscriptions = self.service.get_subscriber_subscriptions(
                sub_uuid, g.organization_id
            )
            return jsonify({
                'success': True,
                'subscriptions': [
                    s.to_dict(include_plan=True) for s in subscriptions
                ],
                'count': len(subscriptions),
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(f"Get subscriber subscriptions error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_active_subscription(self, subscriber_id):
        """GET /api/v1/billing/subscribers/<subscriber_id>/subscription/active"""
        try:
            sub_uuid = UUID(subscriber_id)
            subscription = self.service.get_active_subscription(
                sub_uuid, g.organization_id
            )
            if not subscription:
                return jsonify({
                    'success': True,
                    'active': False,
                    'message': 'No active subscription',
                }), 200
            return jsonify({
                'success': True,
                'active': True,
                'subscription': subscription.to_dict(include_plan=True),
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(f"Get active subscription error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('subscription_cancel')
    def cancel_subscription(self, subscription_id):
        """POST /api/v1/billing/subscriptions/<subscription_id>/cancel"""
        try:
            sub_uuid = UUID(subscription_id)
            reason = request.json.get('reason') if request.json else None
            self.service.cancel_subscription(sub_uuid, g.organization_id, reason)
            return jsonify({
                'success': True,
                'message': 'Subscription cancelled successfully',
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscription ID format',
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
            logger.error(f"Cancel subscription error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('subscription_renew')
    def renew_subscription(self, subscription_id):
        """POST /api/v1/billing/subscriptions/<subscription_id>/renew"""
        try:
            sub_uuid = UUID(subscription_id)
            subscription = self.service.renew_subscription(
                sub_uuid, g.organization_id
            )
            return jsonify({
                'success': True,
                'subscription': subscription.to_dict(include_plan=True),
                'message': 'Subscription renewed successfully',
            }), 200
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscription ID format',
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
            logger.error(f"Renew subscription error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # DISCOUNT COUPON ENDPOINTS

    @token_required
    @permission_required('coupon_create')
    def create_coupon(self):
        """POST /api/v1/billing/coupons"""
        try:
            data = DiscountCouponCreateSchema().load(request.json)
            data['valid_from'] = datetime.fromisoformat(data['valid_from'])
            data['valid_to'] = datetime.fromisoformat(data['valid_to'])
            coupon = self.service.create_coupon(g.organization_id, data)
            return jsonify({
                'success': True,
                'coupon': coupon.to_dict(),
                'message': 'Coupon created successfully',
            }), 201
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
        except Exception as e:
            logger.error(f"Create coupon error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def validate_coupon(self):
        """GET /api/v1/billing/coupons/validate?code=XXX&amount=100"""
        try:
            coupon_code = request.args.get('code')
            amount = request.args.get('amount', 0, type=float)

            if not coupon_code:
                return jsonify({
                    'success': False,
                    'error': 'Coupon code required',
                    'error_code': 'MISSING_CODE',
                }), 400

            result = self.service.validate_coupon(
                coupon_code, g.organization_id, amount
            )
            return jsonify({
                'success': True,
                'coupon': result,
            }), 200
        except BusinessError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'INVALID_COUPON',
            }), 400
        except Exception as e:
            logger.error(f"Validate coupon error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_coupons(self):
        """GET /api/v1/billing/coupons"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page

            coupons = self.service.get_coupons(
                g.organization_id, skip, per_page
            )
            return jsonify({
                'success': True,
                'coupons': [c.to_dict() for c in coupons],
            }), 200
        except Exception as e:
            logger.error(f"Get coupons error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # INVOICE ENDPOINTS

    @token_required
    def generate_invoice(self):
        """POST /api/v1/billing/invoices/generate"""
        try:
            data = request.get_json() or {}
            subscriber_id = UUID(data['subscriber_id'])
            subscription_id = UUID(data['subscription_id']) if data.get('subscription_id') else None
            plan_id = UUID(data['plan_id']) if data.get('plan_id') else None

            invoice = self.service.generate_invoice(
                organization_id=g.organization_id,
                subscriber_id=subscriber_id,
                subscription_id=subscription_id,
                plan_id=plan_id,
                invoice_type=data.get('invoice_type', 'subscription'),
                notes=data.get('notes'),
            )
            return jsonify({
                'success': True,
                'invoice': invoice.to_dict(include_items=True),
                'message': 'Invoice generated successfully',
            }), 201
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid UUID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Generate invoice error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # MAINTENANCE

    @token_required
    @permission_required('billing_manage')
    def run_expiry_checks(self):
        """
        POST /api/v1/billing/maintenance/expire

        Run expiry checks for subscriptions and vouchers.
        Admin-only — typically called by scheduled Celery task.
        """
        try:
            result = self.service.run_expiry_checks(g.organization_id)
            return jsonify({
                'success': True,
                'result': result,
                'message': (
                    f"Expired {result['subscriptions_expired']} subscriptions "
                    f"and {result['vouchers_expired']} vouchers"
                ),
            }), 200
        except Exception as e:
            logger.error(f"Run expiry checks error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_expiring_soon(self):
        """
        GET /api/v1/billing/subscriptions/expiring-soon?days=3

        Get subscriptions expiring within N days (for renewal reminders).
        """
        try:
            days = request.args.get('days', 3, type=int)
            hours = request.args.get('hours', type=int)

            if hours:
                subscriptions = self.service.get_expiring_in_hours(
                    g.organization_id, hours
                )
            else:
                subscriptions = self.service.get_expiring_soon(
                    g.organization_id, days
                )

            return jsonify({
                'success': True,
                'subscriptions': [
                    s.to_dict(include_plan=True) for s in subscriptions
                ],
                'count': len(subscriptions),
            }), 200
        except Exception as e:
            logger.error(f"Get expiring soon error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # DASHBOARD STATS

    @token_required
    def get_billing_stats(self):
        """GET /api/v1/billing/stats"""
        try:
            stats = self.service.get_billing_stats(g.organization_id)
            return jsonify({
                'success': True,
                'stats': stats,
            }), 200
        except Exception as e:
            logger.error(f"Get billing stats error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500