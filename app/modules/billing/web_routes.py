from flask import Blueprint, render_template, redirect, url_for, flash, session, request, abort
from functools import wraps
from uuid import UUID
from datetime import datetime

from app.core.logging.logger import logger
from app.modules.billing.service import BillingService
from app.modules.organization.service import OrganizationService
from app.modules.auth.repository import UserRepository

# Create web blueprint with organization ID in URL pattern
billing_web_bp = Blueprint('billing_web', __name__, url_prefix='/organization/<org_id>/billing')

# Initialize services
billing_service = BillingService()
organization_service = OrganizationService()
user_repo = UserRepository()

# DECORATORS
def web_billing_access_required(f):
    """Decorator to validate organization access and load context"""
    @wraps(f)
    def decorated_function(org_id, *args, **kwargs):
        # Check if user is logged in
        if not session.get('user_id'):
            flash('Please login to continue', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('web.login'))
        
        # Get user from database
        user = user_repo.get_by_id(session['user_id'])
        if not user or not user.is_active:
            session.clear()
            flash('User account not found or inactive', 'danger')
            return redirect(url_for('web.login'))
        
        # Validate organization ID format
        try:
            org_uuid = UUID(org_id)
        except ValueError:
            logger.warning(f"Invalid org_id format: {org_id}")
            abort(404)
        
        # Check if user belongs to this organization
        user_orgs = organization_service.get_organizations_by_user(user.id)
        if org_uuid not in [org.id for org in user_orgs]:
            logger.warning(f"User {user.id} attempted to access organization {org_id} without permission")
            flash('You do not have access to this organization', 'danger')
            return redirect(url_for('web.dashboard'))
        
        # Get organization details
        organization = organization_service.get_organization(org_uuid)
        
        # Make user and org available to template
        kwargs['current_user'] = user
        kwargs['current_organization'] = organization
        
        return f(org_id, *args, **kwargs)
    return decorated_function

# PLAN ROUTES
@billing_web_bp.route('/')
@web_billing_access_required
def index(org_id, current_user=None, current_organization=None):
    """Billing home page - redirect to dashboard"""
    return redirect(url_for('billing_web.dashboard', org_id=org_id))


@billing_web_bp.route('/plans')
@web_billing_access_required
def plans(org_id, current_user=None, current_organization=None):
    """List all plans"""
    plans = billing_service.get_plans(current_organization.id, 0, 100, only_active=False)
    
    # Separate by type for display
    hotspot_plans = [p for p in plans if p.plan_type in ['hotspot', 'both']]
    pppoe_plans = [p for p in plans if p.plan_type in ['pppoe', 'both']]
    
    return render_template(
        'web/billing/plans/index.html',
        organization=current_organization,
        user=current_user,
        hotspot_plans=hotspot_plans,
        pppoe_plans=pppoe_plans,
        all_plans=plans
    )


@billing_web_bp.route('/plans/create', methods=['GET', 'POST'])
@web_billing_access_required
def plan_create(org_id, current_user=None, current_organization=None):
    """Create a new plan with dynamic validity"""
    
    if request.method == 'GET':
        return render_template(
            'web/billing/plans/create.html',
            organization=current_organization,
            user=current_user,
            form_data=None
        )
    
    # POST - Create plan with dynamic validity
    try:
        data = {
            'name': request.form.get('name'),
            'description': request.form.get('description'),
            'plan_type': request.form.get('plan_type'),
            'billing_cycle': request.form.get('billing_cycle', 'one_time'),
            'validity_type': request.form.get('validity_type'),
            # Dynamic validity fields (replaces validity_days)
            'validity_value': request.form.get('validity_value', type=int),
            'validity_unit': request.form.get('validity_unit'),
            'data_limit_mb': request.form.get('data_limit_mb', type=int),
            'bandwidth_up_mbps': request.form.get('bandwidth_up_mbps', type=int, default=0),
            'bandwidth_down_mbps': request.form.get('bandwidth_down_mbps', type=int, default=0),
            'price': request.form.get('price', type=float),
            'setup_fee': request.form.get('setup_fee', type=float, default=0),
            'device_limit': request.form.get('device_limit', type=int, default=1),
            'is_active': request.form.get('is_active') == 'true',
            'is_public': request.form.get('is_public') == 'true'
        }
        
        plan = billing_service.create_plan(current_organization.id, data)
        flash(f'Plan "{plan.name}" created successfully! (Validity: {plan.validity_display})', 'success')
        return redirect(url_for('billing_web.plans', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating plan: {e}", exc_info=True)
        flash(f'Error creating plan: {str(e)}', 'danger')
        return render_template(
            'web/billing/plans/create.html',
            organization=current_organization,
            user=current_user,
            form_data=request.form
        )


@billing_web_bp.route('/plans/<plan_id>/edit', methods=['GET', 'POST'])
@web_billing_access_required
def plan_edit(org_id, plan_id, current_user=None, current_organization=None):
    """Edit a plan with dynamic validity"""
    
    try:
        plan_uuid = UUID(plan_id)
        # Get plan including inactive ones
        plan = billing_service.plan_repo.get_by_id(plan_uuid, current_organization.id, include_inactive=True)
        if not plan:
            flash('Plan not found', 'danger')
            return redirect(url_for('billing_web.plans', org_id=org_id))
        
        if request.method == 'GET':
            return render_template(
                'web/billing/plans/edit.html',
                organization=current_organization,
                user=current_user,
                plan=plan
            )
        
        # POST - Update plan with dynamic validity
        data = {}
        
        name = request.form.get('name')
        if name:
            data['name'] = name
        
        description = request.form.get('description')
        if description:
            data['description'] = description
        
        plan_type = request.form.get('plan_type')
        if plan_type:
            data['plan_type'] = plan_type
        
        billing_cycle = request.form.get('billing_cycle')
        if billing_cycle:
            data['billing_cycle'] = billing_cycle
        
        validity_type = request.form.get('validity_type')
        if validity_type:
            data['validity_type'] = validity_type
        
        # Dynamic validity fields
        validity_value = request.form.get('validity_value', type=int)
        if validity_value:
            data['validity_value'] = validity_value
        
        validity_unit = request.form.get('validity_unit')
        if validity_unit:
            data['validity_unit'] = validity_unit
        
        data_limit_mb = request.form.get('data_limit_mb', type=int)
        if data_limit_mb:
            data['data_limit_mb'] = data_limit_mb
        
        bandwidth_up = request.form.get('bandwidth_up_mbps', type=int)
        if bandwidth_up:
            data['bandwidth_up_mbps'] = bandwidth_up
        
        bandwidth_down = request.form.get('bandwidth_down_mbps', type=int)
        if bandwidth_down:
            data['bandwidth_down_mbps'] = bandwidth_down
        
        price = request.form.get('price', type=float)
        if price:
            data['price'] = price
        
        data['is_active'] = request.form.get('is_active') == 'true'
        data['is_public'] = request.form.get('is_public') == 'true'
        
        updated_plan = billing_service.update_plan(plan_uuid, current_organization.id, data)
        flash(f'Plan "{updated_plan.name}" updated successfully!', 'success')
        return redirect(url_for('billing_web.plans', org_id=org_id))
        
    except ValueError:
        flash('Invalid plan ID format', 'danger')
        return redirect(url_for('billing_web.plans', org_id=org_id))
    except Exception as e:
        logger.error(f"Error updating plan: {e}", exc_info=True)
        flash(f'Error updating plan: {str(e)}', 'danger')
        return redirect(url_for('billing_web.plans', org_id=org_id))

@billing_web_bp.route('/plans/<plan_id>/delete', methods=['POST'])
@web_billing_access_required
def plan_delete(org_id, plan_id, current_user=None, current_organization=None):
    """Delete a plan"""
    
    try:
        plan_uuid = UUID(plan_id)
        billing_service.delete_plan(plan_uuid, current_organization.id, soft_delete=True)
        flash('Plan deactivated successfully!', 'success')
    except ValueError:
        flash('Invalid plan ID format', 'danger')
    except Exception as e:
        logger.error(f"Error deleting plan: {e}", exc_info=True)
        flash(f'Error deleting plan: {str(e)}', 'danger')
    
    return redirect(url_for('billing_web.plans', org_id=org_id))

# VOUCHER ROUTES
@billing_web_bp.route('/vouchers')
@web_billing_access_required
def vouchers(org_id, current_user=None, current_organization=None):
    """List all vouchers (batches and single vouchers)"""
    
    # Get voucher batches
    batches = billing_service.voucher_batch_repo.get_by_organization(current_organization.id, 0, 50)
    
    # Get single vouchers (not part of any batch)
    # You'll need to add this method to your VoucherRepository
    single_vouchers = billing_service.voucher_repo.get_single_vouchers(current_organization.id)
    
    # Get statistics
    stats = {
        'total_batches': len(batches),
        'total_vouchers': sum(b.quantity for b in batches) + len(single_vouchers),
        'active_vouchers': billing_service.voucher_repo.count_by_status(current_organization.id, 'active'),
        'used_vouchers': billing_service.voucher_repo.count_by_status(current_organization.id, 'used'),
        'expired_vouchers': billing_service.voucher_repo.count_by_status(current_organization.id, 'expired'),
    }
    
    return render_template(
        'web/billing/vouchers/index.html',
        organization=current_organization,
        user=current_user,
        batches=batches,
        single_vouchers=single_vouchers,
        stats=stats
    )


@billing_web_bp.route('/vouchers/create', methods=['GET', 'POST'])
@web_billing_access_required
def voucher_create(org_id, current_user=None, current_organization=None):
    """Create a single voucher with dynamic validity"""
    plans = billing_service.get_plans(current_organization.id, 0, 100, only_active=True)
    
    if request.method == 'GET':
        return render_template(
            'web/billing/vouchers/create.html',
            organization=current_organization,
            user=current_user,
            plans=plans,
            form_data=None
        )
    
    # POST - Create voucher
    try:
        plan_id = UUID(request.form.get('plan_id'))
        
        # ✅ Convert empty strings to None for enum fields
        validity_value = request.form.get('validity_value', type=int)
        validity_unit = request.form.get('validity_unit')
        if validity_unit == '':
            validity_unit = None
        
        activation_type = request.form.get('activation_type', 'immediate')
        max_uses = request.form.get('max_uses', type=int, default=1)
        
        voucher = billing_service.create_voucher(
            organization_id=current_organization.id,
            plan_id=plan_id,
            max_uses=max_uses,
            validity_value=validity_value,
            validity_unit=validity_unit,
            activation_type=activation_type,
            created_by=current_user.id
        )
        
        flash(f'Voucher "{voucher.code}" created successfully!', 'success')
        return redirect(url_for('billing_web.vouchers', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating voucher: {e}", exc_info=True)
        flash(f'Error creating voucher: {str(e)}', 'danger')
        return render_template(
            'web/billing/vouchers/create.html',
            organization=current_organization,
            user=current_user,
            plans=plans,
            form_data=request.form
        )

@billing_web_bp.route('/vouchers/batch/create', methods=['GET', 'POST'])
@web_billing_access_required
def voucher_batch_create(org_id, current_user=None, current_organization=None):
    """Create a batch of vouchers with dynamic validity"""
    plans = billing_service.get_plans(current_organization.id, 0, 100, only_active=True)
    
    if request.method == 'GET':
        return render_template(
            'web/billing/vouchers/batch_create.html',
            organization=current_organization,
            user=current_user,
            plans=plans,
            form_data=None
        )
    
    # POST - Create voucher batch
    try:
        plan_id = UUID(request.form.get('plan_id'))
        batch_name = request.form.get('batch_name')
        quantity = request.form.get('quantity', type=int)
        
        # ✅ Convert empty strings to None for enum fields
        validity_value = request.form.get('validity_value', type=int)
        validity_unit = request.form.get('validity_unit')
        if validity_unit == '' or validity_unit is None:
            validity_unit = None
        
        batch = billing_service.create_voucher_batch(
            organization_id=current_organization.id,
            plan_id=plan_id,
            batch_name=batch_name,
            quantity=quantity,
            validity_value=validity_value,
            validity_unit=validity_unit,
            created_by=current_user.id
        )
        
        flash(f'Batch of {quantity} vouchers created successfully!', 'success')
        return redirect(url_for('billing_web.vouchers', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating voucher batch: {e}", exc_info=True)
        flash(f'Error creating voucher batch: {str(e)}', 'danger')
        return render_template(
            'web/billing/vouchers/batch_create.html',
            organization=current_organization,
            user=current_user,
            plans=plans,
            form_data=request.form
        )


@billing_web_bp.route('/vouchers/batch/<batch_id>')
@web_billing_access_required
def voucher_batch_detail(org_id, batch_id, current_user=None, current_organization=None):
    """View voucher batch details"""
    
    try:
        batch_uuid = UUID(batch_id)
        batch = billing_service.get_voucher_batch(batch_uuid, current_organization.id)
        vouchers = list(batch.vouchers) if batch.vouchers else []
        
        return render_template(
            'web/billing/vouchers/batch_detail.html',
            organization=current_organization,
            user=current_user,
            batch=batch,
            vouchers=vouchers
        )
        
    except ValueError:
        flash('Invalid batch ID format', 'danger')
        return redirect(url_for('billing_web.vouchers', org_id=org_id))
    except Exception as e:
        logger.error(f"Error loading voucher batch: {e}", exc_info=True)
        flash('Batch not found', 'danger')
        return redirect(url_for('billing_web.vouchers', org_id=org_id))

# SUBSCRIPTION ROUTES
@billing_web_bp.route('/subscriptions')
@web_billing_access_required
def subscriptions(org_id, current_user=None, current_organization=None):
    """List all active subscriptions"""
    
    expiring_soon = billing_service.subscription_repo.get_expiring_soon(current_organization.id, 7)
    
    return render_template(
        'web/billing/subscriptions/index.html',
        organization=current_organization,
        user=current_user,
        expiring_soon=expiring_soon
    )

# DASHBOARD
@billing_web_bp.route('/dashboard')
@web_billing_access_required
def dashboard(org_id, current_user=None, current_organization=None):
    """Billing dashboard with overview"""
    
    # Get statistics
    plans = billing_service.get_plans(current_organization.id, 0, 100, only_active=True)
    active_plans = len(plans)
    
    # Get expiring subscriptions
    expiring = billing_service.subscription_repo.get_expiring_soon(current_organization.id, 7)
    
    # Get voucher batches
    batches = billing_service.voucher_batch_repo.get_by_organization(current_organization.id, 0, 10)
    
    return render_template(
        'web/billing/dashboard.html',
        organization=current_organization,
        user=current_user,
        active_plans=active_plans,
        expiring_subscriptions=expiring,
        voucher_batches=batches
    )