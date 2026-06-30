"""
Billing API Routes
==================
Blueprint for billing operations: plans, vouchers, subscriptions, coupons, invoices.

Route Structure:
    /api/v1/billing/
        ├── Plans
        │   ├── GET    /plans                          List plans
        │   ├── GET    /plans/public                   Public plans (captive portal)
        │   ├── POST   /plans                          Create plan
        │   ├── GET    /plans/<plan_id>                Get plan
        │   ├── PUT    /plans/<plan_id>                Update plan
        │   └── DELETE /plans/<plan_id>                Delete plan
        │
        ├── Vouchers
        │   ├── POST   /vouchers                       Create single voucher
        │   ├── POST   /vouchers/batch                 Create voucher batch
        │   ├── POST   /vouchers/redeem                Redeem voucher
        │   ├── GET    /vouchers/check/<code>          Check voucher validity
        │   ├── POST   /vouchers/<id>/void             Void voucher
        │   ├── GET    /vouchers/batches/<id>          Get batch
        │   └── GET    /vouchers/batches/<id>/vouchers List vouchers in batch
        │
        ├── Subscriptions
        │   ├── GET    /subscriptions/<id>             Get subscription
        │   ├── GET    /subscriptions/subscriber/<id>  Subscriber history
        │   ├── GET    /subscriptions/subscriber/<id>/active  Active subscription
        │   ├── POST   /subscriptions/<id>/cancel      Cancel subscription
        │   ├── POST   /subscriptions/<id>/renew       Renew subscription
        │   └── GET    /subscriptions/expiring-soon    Expiring subscriptions
        │
        ├── Coupons
        │   ├── GET    /coupons                        List coupons
        │   ├── POST   /coupons                        Create coupon
        │   └── GET    /coupons/validate               Validate coupon
        │
        ├── Invoices
        │   ├── POST   /invoices/generate              Generate invoice
        │   ├── GET    /invoices/daily                 Daily invoices  ?date=YYYY-MM-DD
        │   ├── GET    /invoices/weekly                Weekly invoices ?week=N&year=YYYY
        │   └── GET    /invoices/monthly               Monthly invoices ?month=N&year=YYYY
        │
        ├── Maintenance
        │   └── POST   /maintenance/expire             Run expiry checks
        │
        └── Stats
            ├── GET    /stats                          Dashboard statistics
            ├── GET    /stats/daily                    Daily stats   ?date=YYYY-MM-DD
            ├── GET    /stats/weekly                   Weekly stats  ?week=N&year=YYYY
            └── GET    /stats/monthly                  Monthly stats ?month=N&year=YYYY
"""

from flask import Blueprint, request, g, jsonify
from datetime import datetime

from app.modules.billing.controller import BillingController
from app.modules.billing.billing_service import BillingPeriodService
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger

billing_bp = Blueprint('billing', __name__, url_prefix='/api/v1/billing')
controller = BillingController()


# =========================================================================
# PLAN ROUTES
# =========================================================================

@billing_bp.route('/plans', methods=['GET'])
@token_required
def get_plans():
    """GET /api/v1/billing/plans — List plans with filters"""
    return controller.get_plans()


@billing_bp.route('/plans/public', methods=['GET'])
@token_required
def get_public_plans():
    """GET /api/v1/billing/plans/public — Public plans for captive portal"""
    return controller.get_public_plans()


@billing_bp.route('/plans', methods=['POST'])
@token_required
@permission_required('plan_create')
def create_plan():
    """POST /api/v1/billing/plans — Create a new plan"""
    return controller.create_plan()


@billing_bp.route('/plans/<plan_id>', methods=['GET'])
@token_required
def get_plan(plan_id):
    """GET /api/v1/billing/plans/<plan_id> — Get plan by ID"""
    return controller.get_plan(plan_id)


@billing_bp.route('/plans/<plan_id>', methods=['PUT'])
@token_required
@permission_required('plan_update')
def update_plan(plan_id):
    """PUT /api/v1/billing/plans/<plan_id> — Update a plan"""
    return controller.update_plan(plan_id)


@billing_bp.route('/plans/<plan_id>', methods=['DELETE'])
@token_required
@permission_required('plan_delete')
def delete_plan(plan_id):
    """DELETE /api/v1/billing/plans/<plan_id> — Delete/deactivate a plan"""
    return controller.delete_plan(plan_id)


# =========================================================================
# VOUCHER ROUTES
# =========================================================================

@billing_bp.route('/vouchers', methods=['POST'])
@token_required
@permission_required('voucher_create')
def create_voucher():
    """POST /api/v1/billing/vouchers — Create a single voucher"""
    return controller.create_voucher()


@billing_bp.route('/vouchers/batch', methods=['POST'])
@token_required
@permission_required('voucher_create')
def create_voucher_batch():
    """POST /api/v1/billing/vouchers/batch — Create a batch of vouchers"""
    return controller.create_voucher_batch()


@billing_bp.route('/vouchers/redeem', methods=['POST'])
@token_required
def redeem_voucher():
    """POST /api/v1/billing/vouchers/redeem — Redeem a voucher"""
    return controller.redeem_voucher()


@billing_bp.route('/vouchers/check/<voucher_code>', methods=['GET'])
@token_required
def check_voucher(voucher_code):
    """GET /api/v1/billing/vouchers/check/<code> — Check voucher without redeeming"""
    return controller.check_voucher(voucher_code)


@billing_bp.route('/vouchers/<voucher_id>/void', methods=['POST'])
@token_required
@permission_required('voucher_manage')
def void_voucher(voucher_id):
    """POST /api/v1/billing/vouchers/<id>/void — Void a voucher"""
    return controller.void_voucher(voucher_id)


@billing_bp.route('/vouchers/batches/<batch_id>', methods=['GET'])
@token_required
def get_voucher_batch(batch_id):
    """GET /api/v1/billing/vouchers/batches/<id> — Get voucher batch"""
    return controller.get_voucher_batch(batch_id)


@billing_bp.route('/vouchers/batches/<batch_id>/vouchers', methods=['GET'])
@token_required
def get_batch_vouchers(batch_id):
    """GET /api/v1/billing/vouchers/batches/<id>/vouchers — List vouchers in batch"""
    return controller.get_batch_vouchers(batch_id)


# =========================================================================
# SUBSCRIPTION ROUTES
# =========================================================================

@billing_bp.route('/subscriptions/<subscription_id>', methods=['GET'])
@token_required
def get_subscription(subscription_id):
    """GET /api/v1/billing/subscriptions/<id> — Get subscription by ID"""
    return controller.get_subscription(subscription_id)


@billing_bp.route('/subscriptions/subscriber/<subscriber_id>', methods=['GET'])
@token_required
def get_subscriber_subscriptions(subscriber_id):
    """GET /api/v1/billing/subscriptions/subscriber/<id> — Subscription history"""
    return controller.get_subscriber_subscriptions(subscriber_id)


@billing_bp.route('/subscriptions/subscriber/<subscriber_id>/active', methods=['GET'])
@token_required
def get_active_subscription(subscriber_id):
    """GET /api/v1/billing/subscriptions/subscriber/<id>/active — Active subscription"""
    return controller.get_active_subscription(subscriber_id)


@billing_bp.route('/subscriptions/<subscription_id>/cancel', methods=['POST'])
@token_required
@permission_required('subscription_cancel')
def cancel_subscription(subscription_id):
    """POST /api/v1/billing/subscriptions/<id>/cancel — Cancel subscription"""
    return controller.cancel_subscription(subscription_id)


@billing_bp.route('/subscriptions/<subscription_id>/renew', methods=['POST'])
@token_required
@permission_required('subscription_renew')
def renew_subscription(subscription_id):
    """POST /api/v1/billing/subscriptions/<id>/renew — Renew subscription"""
    return controller.renew_subscription(subscription_id)


@billing_bp.route('/subscriptions/expiring-soon', methods=['GET'])
@token_required
def get_expiring_soon():
    """
    GET /api/v1/billing/subscriptions/expiring-soon?days=3&hours=24
    
    Get subscriptions expiring within N days or hours.
    """
    return controller.get_expiring_soon()


# =========================================================================
# DISCOUNT COUPON ROUTES
# =========================================================================

@billing_bp.route('/coupons', methods=['GET'])
@token_required
def get_coupons():
    """GET /api/v1/billing/coupons — List discount coupons"""
    return controller.get_coupons()


@billing_bp.route('/coupons', methods=['POST'])
@token_required
@permission_required('coupon_create')
def create_coupon():
    """POST /api/v1/billing/coupons — Create a discount coupon"""
    return controller.create_coupon()


@billing_bp.route('/coupons/validate', methods=['GET'])
@token_required
def validate_coupon():
    """GET /api/v1/billing/coupons/validate?code=XXX&amount=100 — Validate coupon"""
    return controller.validate_coupon()


# =========================================================================
# INVOICE ROUTES
# =========================================================================

@billing_bp.route('/invoices/generate', methods=['POST'])
@token_required
def generate_invoice():
    """POST /api/v1/billing/invoices/generate — Generate an invoice"""
    return controller.generate_invoice()


# =========================================================================
# MAINTENANCE ROUTES
# =========================================================================

@billing_bp.route('/maintenance/expire', methods=['POST'])
@token_required
@permission_required('billing_manage')
def run_expiry_checks():
    """POST /api/v1/billing/maintenance/expire — Run expiry checks (admin)"""
    return controller.run_expiry_checks()


# =========================================================================
# STATS ROUTES
# =========================================================================

@billing_bp.route('/stats', methods=['GET'])
@token_required
def get_billing_stats():
    """GET /api/v1/billing/stats — Dashboard statistics"""
    return controller.get_billing_stats()


# =========================================================================
# BILLING PERIOD — INVOICE ROUTES
# =========================================================================

_period_service = BillingPeriodService()


@billing_bp.route('/invoices/daily', methods=['GET'])
@token_required
def get_daily_invoices():
    """
    GET /api/v1/billing/invoices/daily?date=2026-06-30

    Return invoices issued on the given date (defaults to today).
    """
    try:
        date_str = request.args.get('date')
        date = datetime.strptime(date_str, '%Y-%m-%d') if date_str else None

        invoices = _period_service.get_invoices_by_period(
            period='daily',
            date=date,
            organization_id=g.organization_id,
        )
        revenue = _period_service.get_revenue_by_period(
            period='daily',
            date=date,
            organization_id=g.organization_id,
        )
        return jsonify({
            'success': True,
            'period': 'daily',
            'date': date_str or datetime.utcnow().strftime('%Y-%m-%d'),
            'invoices': [inv.to_dict() for inv in invoices],
            'count': len(invoices),
            'revenue': revenue,
        }), 200
    except ValueError as exc:
        return jsonify({
            'success': False,
            'error': f'Invalid date format: {exc}. Use YYYY-MM-DD.',
            'error_code': 'INVALID_DATE',
        }), 400
    except Exception as exc:
        logger.error(f"get_daily_invoices error: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500


@billing_bp.route('/invoices/weekly', methods=['GET'])
@token_required
def get_weekly_invoices():
    """
    GET /api/v1/billing/invoices/weekly?week=26&year=2026

    Return invoices issued during the given ISO week (defaults to current week).
    """
    try:
        week = request.args.get('week', type=int)
        year = request.args.get('year', type=int)

        invoices = _period_service.get_invoices_by_period(
            period='weekly',
            week=week,
            year=year,
            organization_id=g.organization_id,
        )
        revenue = _period_service.get_revenue_by_period(
            period='weekly',
            week=week,
            year=year,
            organization_id=g.organization_id,
        )
        return jsonify({
            'success': True,
            'period': 'weekly',
            'week': week,
            'year': year,
            'invoices': [inv.to_dict() for inv in invoices],
            'count': len(invoices),
            'revenue': revenue,
        }), 200
    except Exception as exc:
        logger.error(f"get_weekly_invoices error: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500


@billing_bp.route('/invoices/monthly', methods=['GET'])
@token_required
def get_monthly_invoices():
    """
    GET /api/v1/billing/invoices/monthly?month=6&year=2026

    Return invoices issued during the given calendar month (defaults to current month).
    """
    try:
        month = request.args.get('month', type=int)
        year = request.args.get('year', type=int)

        invoices = _period_service.get_invoices_by_period(
            period='monthly',
            month=month,
            year=year,
            organization_id=g.organization_id,
        )
        revenue = _period_service.get_revenue_by_period(
            period='monthly',
            month=month,
            year=year,
            organization_id=g.organization_id,
        )
        return jsonify({
            'success': True,
            'period': 'monthly',
            'month': month,
            'year': year,
            'invoices': [inv.to_dict() for inv in invoices],
            'count': len(invoices),
            'revenue': revenue,
        }), 200
    except Exception as exc:
        logger.error(f"get_monthly_invoices error: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500


# =========================================================================
# BILLING PERIOD — STATS ROUTES
# =========================================================================

@billing_bp.route('/stats/daily', methods=['GET'])
@token_required
def get_daily_stats():
    """
    GET /api/v1/billing/stats/daily?date=2026-06-30

    Return aggregated billing stats for a single day.
    """
    try:
        date_str = request.args.get('date')
        date = datetime.strptime(date_str, '%Y-%m-%d') if date_str else None

        revenue = _period_service.get_revenue_by_period(
            period='daily', date=date, organization_id=g.organization_id
        )
        subscriptions = _period_service.get_subscription_stats_by_period(
            period='daily', date=date, organization_id=g.organization_id
        )
        vouchers = _period_service.get_voucher_stats_by_period(
            period='daily', date=date, organization_id=g.organization_id
        )
        return jsonify({
            'success': True,
            'period': 'daily',
            'date': date_str or datetime.utcnow().strftime('%Y-%m-%d'),
            'revenue': revenue,
            'subscriptions': subscriptions,
            'vouchers': vouchers,
        }), 200
    except ValueError as exc:
        return jsonify({
            'success': False,
            'error': f'Invalid date format: {exc}. Use YYYY-MM-DD.',
            'error_code': 'INVALID_DATE',
        }), 400
    except Exception as exc:
        logger.error(f"get_daily_stats error: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500


@billing_bp.route('/stats/weekly', methods=['GET'])
@token_required
def get_weekly_stats():
    """
    GET /api/v1/billing/stats/weekly?week=26&year=2026

    Return aggregated billing stats for an ISO week.
    """
    try:
        week = request.args.get('week', type=int)
        year = request.args.get('year', type=int)

        revenue = _period_service.get_revenue_by_period(
            period='weekly', week=week, year=year, organization_id=g.organization_id
        )
        subscriptions = _period_service.get_subscription_stats_by_period(
            period='weekly', week=week, year=year, organization_id=g.organization_id
        )
        vouchers = _period_service.get_voucher_stats_by_period(
            period='weekly', week=week, year=year, organization_id=g.organization_id
        )
        return jsonify({
            'success': True,
            'period': 'weekly',
            'week': week,
            'year': year,
            'revenue': revenue,
            'subscriptions': subscriptions,
            'vouchers': vouchers,
        }), 200
    except Exception as exc:
        logger.error(f"get_weekly_stats error: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500


@billing_bp.route('/stats/monthly', methods=['GET'])
@token_required
def get_monthly_stats():
    """
    GET /api/v1/billing/stats/monthly?month=6&year=2026

    Return aggregated billing stats for a calendar month.
    """
    try:
        month = request.args.get('month', type=int)
        year = request.args.get('year', type=int)

        revenue = _period_service.get_revenue_by_period(
            period='monthly', month=month, year=year, organization_id=g.organization_id
        )
        subscriptions = _period_service.get_subscription_stats_by_period(
            period='monthly', month=month, year=year, organization_id=g.organization_id
        )
        vouchers = _period_service.get_voucher_stats_by_period(
            period='monthly', month=month, year=year, organization_id=g.organization_id
        )
        return jsonify({
            'success': True,
            'period': 'monthly',
            'month': month,
            'year': year,
            'revenue': revenue,
            'subscriptions': subscriptions,
            'vouchers': vouchers,
        }), 200
    except Exception as exc:
        logger.error(f"get_monthly_stats error: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'error_code': 'INTERNAL_ERROR',
        }), 500