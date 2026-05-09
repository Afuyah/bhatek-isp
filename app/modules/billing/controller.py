from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID
from datetime import datetime

from app.modules.billing.service import BillingService
from app.modules.billing.schemas import (
    PlanCreateSchema, PlanUpdateSchema, PurchasePlanSchema,
    VoucherCreateSchema, VoucherBatchCreateSchema, RedeemVoucherSchema,
    DiscountCouponCreateSchema, InvoiceFilterSchema
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError, ValidationError as AppValidationError


class BillingController:
    """Billing controller"""
    
    def __init__(self):
        self.service = BillingService()
    
    # ==========================================================================
    # Plan Endpoints
    # ==========================================================================
    
    @token_required
    @permission_required('plan_create')
    def create_plan(self):
        """Create a new plan"""
        try:
            data = PlanCreateSchema().load(request.json)
            plan = self.service.create_plan(g.organization_id, data)
            return jsonify({
                'success': True,
                'plan': plan.to_dict(),
                'message': 'Plan created successfully'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except AppValidationError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"Create plan error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_plans(self):
        """Get all plans for organization"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            only_active = request.args.get('only_active', 'true').lower() == 'true'
            plan_type = request.args.get('plan_type')  # hotspot, pppoe, both
            
            plans = self.service.get_plans(g.organization_id, skip, per_page, only_active, plan_type)
            
            # Get total count
            total = self.service.plan_repo.count_by_organization(g.organization_id, only_active if only_active else None)
            
            return jsonify({
                'plans': [p.to_dict() for p in plans],
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': (total + per_page - 1) // per_page if total else 0
            }), 200
        except Exception as e:
            logger.error(f"Get plans error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_plan(self, plan_id):
        """Get plan by ID"""
        try:
            plan_uuid = UUID(plan_id)
            plan = self.service.get_plan(plan_uuid, g.organization_id)
            return jsonify(plan.to_dict()), 200
        except ValueError:
            return jsonify({'error': 'Invalid plan ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get plan error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    @permission_required('plan_update')
    def update_plan(self, plan_id):
        """Update a plan"""
        try:
            data = PlanUpdateSchema().load(request.json)
            plan_uuid = UUID(plan_id)
            plan = self.service.update_plan(plan_uuid, g.organization_id, data)
            return jsonify({
                'success': True,
                'plan': plan.to_dict(),
                'message': 'Plan updated successfully'
            }), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid plan ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Update plan error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    @permission_required('plan_delete')
    def delete_plan(self, plan_id):
        """Delete a plan (soft delete)"""
        try:
            plan_uuid = UUID(plan_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            self.service.delete_plan(plan_uuid, g.organization_id, soft)
            message = 'Plan deactivated successfully' if soft else 'Plan deleted permanently'
            return jsonify({'success': True, 'message': message}), 200
        except ValueError:
            return jsonify({'error': 'Invalid plan ID format'}), 400
        except BusinessError as e:
            return jsonify({'error': str(e)}), 409
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Delete plan error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_public_plans(self):
        """Get public plans for hotspot portal (no auth required for portal)"""
        try:
            plans = self.service.plan_repo.get_public_plans(g.organization_id)
            return jsonify({
                'plans': [p.to_dict() for p in plans]
            }), 200
        except Exception as e:
            logger.error(f"Get public plans error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # Voucher Endpoints
    # ==========================================================================
    
    @token_required
    @permission_required('voucher_create')
    def create_voucher(self):
        """Create a single voucher"""
        try:
            data = VoucherCreateSchema().load(request.json)
            voucher = self.service.create_voucher(
                organization_id=g.organization_id,
                plan_id=UUID(data['plan_id']),
                max_uses=data.get('max_uses', 1),
                expires_in_days=data['expires_in_days'],
                created_by=g.user_id
            )
            return jsonify({
                'success': True,
                'voucher': voucher.to_dict(),
                'message': 'Voucher created successfully'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid plan ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Create voucher error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    @permission_required('voucher_create')
    def create_voucher_batch(self):
        """Create a batch of vouchers"""
        try:
            data = VoucherBatchCreateSchema().load(request.json)
            batch = self.service.create_voucher_batch(
                organization_id=g.organization_id,
                plan_id=UUID(data['plan_id']),
                batch_name=data['batch_name'],
                quantity=data['quantity'],
                expires_in_days=data.get('expires_in_days', 30),
                created_by=g.user_id
            )
            return jsonify({
                'success': True,
                'batch': batch.to_dict(),
                'message': f'Batch of {data["quantity"]} vouchers created successfully'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid plan ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Create voucher batch error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def redeem_voucher(self):
        """Redeem a voucher for a subscriber"""
        try:
            data = RedeemVoucherSchema().load(request.json)
            
            result = self.service.redeem_voucher(
                organization_id=g.organization_id,
                voucher_code=data['voucher_code'],
                subscriber_id=UUID(data['subscriber_id']),
                router_id=UUID(data.get('router_id')) if data.get('router_id') else None
            )
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except ValueError:
            return jsonify({'error': 'Invalid UUID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except BusinessError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"Redeem voucher error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def check_voucher(self, voucher_code):
        """Check voucher validity without redeeming"""
        try:
            info = self.service.get_voucher_info(voucher_code, g.organization_id)
            return jsonify(info), 200
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Check voucher error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_voucher_batch(self, batch_id):
        """Get voucher batch by ID"""
        try:
            batch_uuid = UUID(batch_id)
            batch = self.service.get_voucher_batch(batch_uuid, g.organization_id)
            return jsonify(batch.to_dict()), 200
        except ValueError:
            return jsonify({'error': 'Invalid batch ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get voucher batch error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # Discount Coupon Endpoints
    # ==========================================================================
    
    @token_required
    @permission_required('coupon_create')
    def create_coupon(self):
        """Create a discount coupon"""
        try:
            data = DiscountCouponCreateSchema().load(request.json)
            data['valid_from'] = datetime.fromisoformat(data['valid_from'])
            data['valid_to'] = datetime.fromisoformat(data['valid_to'])
            coupon = self.service.create_coupon(g.organization_id, data)
            return jsonify({
                'success': True,
                'coupon': coupon.to_dict(),
                'message': 'Coupon created successfully'
            }), 201
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Create coupon error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def validate_coupon(self):
        """Validate a coupon code"""
        try:
            coupon_code = request.args.get('code')
            amount = request.args.get('amount', 0, type=float)
            
            if not coupon_code:
                return jsonify({'error': 'Coupon code required'}), 400
            
            result = self.service.validate_coupon(coupon_code, g.organization_id, amount)
            return jsonify(result), 200
        except BusinessError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"Validate coupon error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_coupons(self):
        """Get all discount coupons"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            coupons = self.service.get_coupons(g.organization_id, skip, per_page)
            total = len(coupons)
            
            return jsonify({
                'coupons': [c.to_dict() for c in coupons],
                'total': total,
                'page': page,
                'per_page': per_page
            }), 200
        except Exception as e:
            logger.error(f"Get coupons error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # Subscription Endpoints
    # ==========================================================================
    
    @token_required
    def get_subscription(self, subscription_id):
        """Get subscription by ID"""
        try:
            sub_uuid = UUID(subscription_id)
            subscription = self.service.get_subscription(sub_uuid, g.organization_id)
            return jsonify(subscription.to_dict(include_plan=True)), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Get subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_subscriber_subscriptions(self, subscriber_id):
        """Get all subscriptions for a subscriber"""
        try:
            sub_uuid = UUID(subscriber_id)
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            
            subscriptions = self.service.get_subscriber_subscriptions(
                sub_uuid, g.organization_id, skip, per_page
            )
            total = self.service.subscription_repo.count_by_subscriber(sub_uuid, g.organization_id)
            
            return jsonify({
                'subscriptions': [s.to_dict(include_plan=True) for s in subscriptions],
                'total': total,
                'page': page,
                'per_page': per_page
            }), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get subscriber subscriptions error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    def get_active_subscription(self, subscriber_id):
        """Get active subscription for a subscriber"""
        try:
            sub_uuid = UUID(subscriber_id)
            subscription = self.service.get_active_subscription(sub_uuid, g.organization_id)
            if not subscription:
                return jsonify({'active': False, 'message': 'No active subscription found'}), 200
            return jsonify({
                'active': True,
                'subscription': subscription.to_dict(include_plan=True)
            }), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscriber ID format'}), 400
        except Exception as e:
            logger.error(f"Get active subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    @permission_required('subscription_cancel')
    def cancel_subscription(self, subscription_id):
        """Cancel a subscription"""
        try:
            sub_uuid = UUID(subscription_id)
            reason = request.json.get('reason') if request.json else None
            self.service.cancel_subscription(sub_uuid, g.organization_id, reason)
            return jsonify({'success': True, 'message': 'Subscription cancelled successfully'}), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Cancel subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    @token_required
    @permission_required('subscription_renew')
    def renew_subscription(self, subscription_id):
        """Renew a subscription"""
        try:
            sub_uuid = UUID(subscription_id)
            subscription = self.service.renew_subscription(sub_uuid, g.organization_id)
            return jsonify({
                'success': True,
                'subscription': subscription.to_dict(include_plan=True),
                'message': 'Subscription renewed successfully'
            }), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Renew subscription error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500
    
    # ==========================================================================
    # Invoice Endpoints
    # ==========================================================================
    
    @token_required
    def generate_invoice(self, subscription_id):
        """Generate invoice for a subscription"""
        try:
            sub_uuid = UUID(subscription_id)
            invoice = self.service.generate_invoice_for_subscription(sub_uuid, g.organization_id)
            return jsonify({
                'success': True,
                'invoice': invoice.to_dict(),
                'message': 'Invoice generated successfully'
            }), 201
        except ValueError:
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        except NotFoundError as e:
            return jsonify({'error': str(e)}), 404
        except Exception as e:
            logger.error(f"Generate invoice error: {e}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500