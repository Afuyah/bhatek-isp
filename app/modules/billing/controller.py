from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.billing.service import BillingService
from app.modules.billing.schemas import (
    PlanCreateSchema, PlanUpdateSchema, PurchasePlanSchema,
    VoucherCreateSchema, VoucherBatchCreateSchema, RedeemVoucherSchema,
    DiscountCouponCreateSchema, InvoiceFilterSchema
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger


class BillingController:
    """Billing controller for plans, subscriptions, vouchers, and invoices"""
    
    def __init__(self):
        self.service = BillingService()
    
    # Plan Endpoints     
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
        except Exception as e:
            logger.error(f"Create plan error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_plans(self):
        """Get all plans"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            skip = (page - 1) * per_page
            only_active = request.args.get('only_active', 'true').lower() == 'true'
            
            plans = self.service.get_plans(g.organization_id, skip, per_page, only_active)
            total = len(plans)
            
            return jsonify({
                'plans': [p.to_dict() for p in plans],
                'total': total,
                'page': page,
                'per_page': per_page
            }), 200
        except Exception as e:
            logger.error(f"Get plans error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def get_plan(self, plan_id):
        """Get plan by ID"""
        try:
            plan_uuid = UUID(plan_id)
            plan = self.service.get_plan(plan_uuid, g.organization_id)
            return jsonify(plan.to_dict()), 200
        except ValueError:
            return jsonify({'error': 'Invalid plan ID format'}), 400
        except Exception as e:
            logger.error(f"Get plan error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
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
        except Exception as e:
            logger.error(f"Update plan error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    @permission_required('plan_delete')
    def delete_plan(self, plan_id):
        """Delete a plan (soft delete)"""
        try:
            plan_uuid = UUID(plan_id)
            self.service.delete_plan(plan_uuid, g.organization_id)
            return jsonify({'success': True, 'message': 'Plan deleted successfully'}), 200
        except ValueError:
            return jsonify({'error': 'Invalid plan ID format'}), 400
        except Exception as e:
            logger.error(f"Delete plan error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    # Voucher Endpoints     
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
        except Exception as e:
            logger.error(f"Create voucher error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
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
        except Exception as e:
            logger.error(f"Create voucher batch error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def redeem_voucher(self):
        """Redeem a voucher"""
        try:
            data = RedeemVoucherSchema().load(request.json)
            subscriber_id = request.args.get('subscriber_id')
            if not subscriber_id:
                return jsonify({'error': 'subscriber_id required'}), 400
            
            result = self.service.redeem_voucher(
                organization_id=g.organization_id,
                voucher_code=data['voucher_code'],
                subscriber_id=UUID(subscriber_id),
                device_mac=data['device_mac']
            )
            return jsonify(result), 200
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.messages}), 400
        except Exception as e:
            logger.error(f"Redeem voucher error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    def check_voucher(self, voucher_code):
        """Check voucher validity without redeeming"""
        try:
            info = self.service.get_voucher_info(voucher_code, g.organization_id)
            return jsonify(info), 200
        except Exception as e:
            logger.error(f"Check voucher error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 400
    
    # Discount Coupon Endpoints     
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
            return jsonify({'error': str(e)}), 500
    
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
        except Exception as e:
            logger.error(f"Validate coupon error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 400
    
    # Subscription Endpoints     
    @token_required
    def get_subscription(self, subscription_id):
        """Get subscription by ID"""
        try:
            sub_uuid = UUID(subscription_id)
            subscription = self.service.get_subscription(sub_uuid, g.organization_id)
            return jsonify(subscription.to_dict(include_plan=True)), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        except Exception as e:
            logger.error(f"Get subscription error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
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
        except Exception as e:
            logger.error(f"Cancel subscription error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    @token_required
    @permission_required('subscription_renew')
    def renew_subscription(self, subscription_id):
        """Renew a subscription"""
        try:
            sub_uuid = UUID(subscription_id)
            subscription = self.service.renew_subscription(sub_uuid, g.organization_id)
            return jsonify({
                'success': True,
                'subscription': subscription.to_dict(),
                'message': 'Subscription renewed successfully'
            }), 200
        except ValueError:
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        except Exception as e:
            logger.error(f"Renew subscription error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500