from flask import Blueprint, render_template, redirect, url_for, flash, session, request, abort
from functools import wraps
from uuid import UUID
from datetime import datetime

from app.core.logging.logger import logger
from app.modules.subscriber.service import SubscriberService
from app.modules.billing.service import BillingService
from app.modules.organization.service import OrganizationService
from app.modules.auth.repository import UserRepository
from app.modules.network.service import NetworkService
from app.core.exceptions.handlers import NotFoundError, BusinessError

# Create web blueprint with organization ID in URL pattern
subscriber_web_bp = Blueprint('subscriber_web', __name__, url_prefix='/organization/<org_id>/subscribers')

# Initialize services
subscriber_service = SubscriberService()
billing_service = BillingService()
organization_service = OrganizationService()
network_service = NetworkService()
user_repo = UserRepository()

# DECORATORS
def web_subscriber_access_required(f):
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
        
        # Get plans for dropdowns
        hotspot_plans = billing_service.get_plans(org_uuid, 0, 100, only_active=True, plan_type='hotspot')
        pppoe_plans = billing_service.get_plans(org_uuid, 0, 100, only_active=True, plan_type='pppoe')
        
        # Make available to templates
        kwargs['current_user'] = user
        kwargs['current_organization'] = organization
        kwargs['hotspot_plans'] = hotspot_plans
        kwargs['pppoe_plans'] = pppoe_plans
        
        return f(org_id, *args, **kwargs)
    return decorated_function

# SUBSCRIBER LISTING

@subscriber_web_bp.route('/')
@web_subscriber_access_required
def index(org_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """List all subscribers"""
    
    # Get filters from query params
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    skip = (page - 1) * per_page
    
    subscriber_type = request.args.get('type')
    status = request.args.get('status')
    search = request.args.get('search')
    
    # Build filters
    filters = {}
    if status:
        filters['status'] = status
    if search:
        filters['search'] = search
    
    # Fetch subscribers
    subscribers = subscriber_service.get_organization_subscribers(
        organization_id=current_organization.id,
        skip=skip,
        limit=per_page,
        filters=filters,
        subscriber_type=subscriber_type
    )
    
    # Pre-load active subscription for each subscriber
    for subscriber in subscribers:
        subscriber.active_sub = subscriber_service.get_active_subscription(
            subscriber.id, current_organization.id
        )
    
    # Get total count
    total = subscriber_service.repository.count_by_organization(
        current_organization.id, 
        subscriber_type=subscriber_type
    )
    
    pagination = {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total else 0
    }
    
    # Get statistics
    stats = subscriber_service.get_subscriber_dashboard_stats(current_organization.id)
    
    return render_template(
        'web/subscriber/index.html',
        organization=current_organization,
        user=current_user,
        subscribers=subscribers,
        pagination=pagination,
        stats=stats,
        filters={'type': subscriber_type, 'status': status, 'search': search}
    )


@subscriber_web_bp.route('/hotspot')
@web_subscriber_access_required
def hotspot_users(org_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """List hotspot users only"""
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    skip = (page - 1) * per_page
    
    subscribers = subscriber_service.get_hotspot_users(current_organization.id, skip, per_page)
    total = subscriber_service.repository.count_by_organization(current_organization.id, subscriber_type='hotspot')
    
    pagination = {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total else 0
    }
    
    return render_template(
        'web/subscriber/hotspot.html',
        organization=current_organization,
        user=current_user,
        subscribers=subscribers,
        pagination=pagination
    )


@subscriber_web_bp.route('/pppoe')
@web_subscriber_access_required
def pppoe_users(org_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """List PPPoE users only"""
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    skip = (page - 1) * per_page
    
    subscribers = subscriber_service.get_pppoe_users(current_organization.id, skip, per_page)
    total = subscriber_service.repository.count_by_organization(current_organization.id, subscriber_type='pppoe')
    
    pagination = {
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total else 0
    }
    
    return render_template(
        'web/subscriber/pppoe.html',
        organization=current_organization,
        user=current_user,
        subscribers=subscribers,
        pagination=pagination
    )

# SUBSCRIBER CREATE (Hotspot & PPPoE)

@subscriber_web_bp.route('/create/hotspot', methods=['GET', 'POST'])
@web_subscriber_access_required
def create_hotspot(org_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Create a hotspot subscriber (will be created via phone)"""
    
    if request.method == 'GET':
        return render_template(
            'web/subscriber/create_hotspot.html',
            organization=current_organization,
            user=current_user,
            form_data=None
        )
    
    # POST - Create hotspot subscriber
    try:
        phone = request.form.get('phone', '').strip()
        name = request.form.get('name', '').strip()
        
        if not phone:
            flash('Phone number is required', 'danger')
            return render_template('web/subscriber/create_hotspot.html',
                                 organization=current_organization, user=current_user,
                                 form_data=request.form)
        
        subscriber, created = subscriber_service.get_or_create_hotspot_subscriber(
            organization_id=current_organization.id,
            phone=phone,
            name=name
        )
        
        if created:
            flash(f'Hotspot subscriber "{subscriber.phone}" created successfully!', 'success')
        else:
            flash(f'Hotspot subscriber "{subscriber.phone}" already exists', 'info')
        
        return redirect(url_for('subscriber_web.index', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating hotspot subscriber: {e}", exc_info=True)
        flash(f'Error creating subscriber: {str(e)}', 'danger')
        return render_template('web/subscriber/create_hotspot.html',
                             organization=current_organization, user=current_user,
                             form_data=request.form)


@subscriber_web_bp.route('/create/pppoe', methods=['GET', 'POST'])
@web_subscriber_access_required
def create_pppoe(org_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Create a PPPoE subscriber (admin creates manually)"""
    
    if request.method == 'GET':
        return render_template(
            'web/subscriber/create_pppoe.html',
            organization=current_organization,
            user=current_user,
            plans=pppoe_plans,
            form_data=None
        )
    
    # POST - Create PPPoE subscriber
    try:
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        plan_id = request.form.get('plan_id')
        phone = request.form.get('phone', '').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        
        if not username or not password or not plan_id:
            flash('Username, password, and plan are required', 'danger')
            return render_template('web/subscriber/create_pppoe.html',
                                 organization=current_organization, user=current_user,
                                 plans=pppoe_plans, form_data=request.form)
        
        subscriber = subscriber_service.create_pppoe_subscriber(
            organization_id=current_organization.id,
            username=username,
            password=password,
            plan_id=UUID(plan_id),
            phone=phone if phone else None,
            first_name=first_name if first_name else None,
            last_name=last_name if last_name else None
        )
        
        flash(f'PPPoE subscriber "{subscriber.username}" created successfully!', 'success')
        return redirect(url_for('subscriber_web.pppoe_users', org_id=org_id))
        
    except Exception as e:
        logger.error(f"Error creating PPPoE subscriber: {e}", exc_info=True)
        flash(f'Error creating subscriber: {str(e)}', 'danger')
        return render_template('web/subscriber/create_pppoe.html',
                             organization=current_organization, user=current_user,
                             plans=pppoe_plans, form_data=request.form)

# SUBSCRIBER DETAILS

@subscriber_web_bp.route('/<subscriber_id>')
@web_subscriber_access_required
def show(org_id, subscriber_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Subscriber details page"""
    
    try:
        subscriber_uuid = UUID(subscriber_id)
        subscriber = subscriber_service.get_subscriber(subscriber_uuid, current_organization.id)
        
        # Get active subscription
        active_subscription = subscriber_service.get_active_subscription(subscriber_uuid, current_organization.id)
        
        # Get subscription history
        subscription_history = subscriber_service.repository.get_subscription_history(subscriber_uuid, current_organization.id, limit=10)
        
        # Get devices
        devices = subscriber_service.get_devices(subscriber_uuid, current_organization.id)
        
        # Get statistics
        stats = subscriber_service.get_subscriber_stats(subscriber_uuid, current_organization.id)
        
        # Add current time for expiry comparison
        now = datetime.utcnow()
        
        return render_template(
            'web/subscriber/show.html',
            organization=current_organization,
            user=current_user,
            subscriber=subscriber,
            active_subscription=active_subscription,
            subscription_history=subscription_history,
            devices=devices,
            stats=stats,
            now=now
        )
        
    except ValueError:
        flash('Invalid subscriber ID format', 'danger')
        return redirect(url_for('subscriber_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error fetching subscriber: {e}", exc_info=True)
        flash('Subscriber not found', 'danger')
        return redirect(url_for('subscriber_web.index', org_id=org_id))


@subscriber_web_bp.route('/<subscriber_id>/edit', methods=['GET', 'POST'])
@web_subscriber_access_required
def edit(org_id, subscriber_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Edit subscriber"""
    
    try:
        subscriber_uuid = UUID(subscriber_id)
        subscriber = subscriber_service.get_subscriber(subscriber_uuid, current_organization.id)
        
        if request.method == 'GET':
            return render_template(
                'web/subscriber/edit.html',
                organization=current_organization,
                user=current_user,
                subscriber=subscriber
            )
        
        # POST - Update subscriber
        data = {}
        
        first_name = request.form.get('first_name', '').strip()
        if first_name:
            data['first_name'] = first_name
        
        last_name = request.form.get('last_name', '').strip()
        if last_name:
            data['last_name'] = last_name
        
        email = request.form.get('email', '').strip()
        if email:
            data['email'] = email
        
        phone = request.form.get('phone', '').strip()
        if phone and subscriber.subscriber_type == 'hotspot':
            data['phone'] = phone
        
        # For PPPoE users, allow username/password update
        if subscriber.subscriber_type == 'pppoe':
            username = request.form.get('username', '').strip()
            if username:
                data['username'] = username
            
            password = request.form.get('password', '')
            if password:
                data['password'] = password
        
        status = request.form.get('status')
        if status:
            data['status'] = status
        
        if data:
            updated_subscriber = subscriber_service.update_subscriber(subscriber_uuid, current_organization.id, data)
            flash(f'Subscriber updated successfully!', 'success')
        
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
        
    except ValueError:
        flash('Invalid subscriber ID format', 'danger')
        return redirect(url_for('subscriber_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error updating subscriber: {e}", exc_info=True)
        flash(f'Error updating subscriber: {str(e)}', 'danger')
        return redirect(url_for('subscriber_web.index', org_id=org_id))


@subscriber_web_bp.route('/<subscriber_id>/delete', methods=['POST'])
@web_subscriber_access_required
def delete(org_id, subscriber_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Delete subscriber"""
    
    try:
        subscriber_uuid = UUID(subscriber_id)
        soft = request.form.get('soft', 'true').lower() == 'true'
        
        subscriber_service.delete_subscriber(subscriber_uuid, current_organization.id, soft_delete=soft)
        
        message = 'Subscriber deactivated successfully' if soft else 'Subscriber deleted permanently'
        flash(message, 'success')
        
    except ValueError:
        flash('Invalid subscriber ID format', 'danger')
    except Exception as e:
        logger.error(f"Error deleting subscriber: {e}", exc_info=True)
        flash(f'Error deleting subscriber: {str(e)}', 'danger')
    
    return redirect(url_for('subscriber_web.index', org_id=org_id))

# SUBSCRIPTION MANAGEMENT

@subscriber_web_bp.route('/<subscriber_id>/subscriptions/create', methods=['GET', 'POST'])
@web_subscriber_access_required
def create_subscription(org_id, subscriber_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Create a subscription for a subscriber (assign plan)"""
    
    try:
        subscriber_uuid = UUID(subscriber_id)
        subscriber = subscriber_service.get_subscriber(subscriber_uuid, current_organization.id)
        
        # Determine which plans to show based on subscriber type
        if subscriber.subscriber_type == 'hotspot':
            plans = hotspot_plans
        else:
            plans = pppoe_plans
        
        if request.method == 'GET':
            return render_template(
                'web/subscriber/create_subscription.html',
                organization=current_organization,
                user=current_user,
                subscriber=subscriber,
                plans=plans
            )
        
        # POST - Create subscription
        plan_id = UUID(request.form.get('plan_id'))
        auto_renew = request.form.get('auto_renew') == 'true'
        
        subscription = subscriber_service.create_subscription(
            subscriber_id=subscriber_uuid,
            organization_id=current_organization.id,
            plan_id=plan_id,
            auto_renew=auto_renew
        )
        
        flash(f'Plan assigned successfully! Subscription expires: {subscription.expiry_time.strftime("%Y-%m-%d")}', 'success')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
        
    except ValueError:
        flash('Invalid ID format', 'danger')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
    except Exception as e:
        logger.error(f"Error creating subscription: {e}", exc_info=True)
        flash(f'Error creating subscription: {str(e)}', 'danger')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))


@subscriber_web_bp.route('/<subscriber_id>/subscriptions/renew', methods=['GET'])
@web_subscriber_access_required
def renew_subscription_form(org_id, subscriber_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Show form to renew subscription"""
    try:
        subscriber_uuid = UUID(subscriber_id)
        subscriber = subscriber_service.get_subscriber(subscriber_uuid, current_organization.id)
        
        # Get active subscription
        active_subscription = subscriber_service.get_active_subscription(subscriber_uuid, current_organization.id)
        
        if not active_subscription:
            flash('No active subscription found to renew', 'warning')
            return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
        
        # Get all active plans for potential upgrade
        all_plans = billing_service.get_plans(current_organization.id, only_active=True)
        
        # Current time for expiry comparison
        now = datetime.utcnow()
        
        return render_template(
            'web/subscriber/renew_subscription.html',
            organization=current_organization,
            user=current_user,
            subscriber=subscriber,
            active_subscription=active_subscription,
            plans=all_plans,
            now=now
        )
        
    except ValueError:
        flash('Invalid subscriber ID format', 'danger')
        return redirect(url_for('subscriber_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error loading renew form: {e}", exc_info=True)
        flash(f'Error loading renewal form: {str(e)}', 'danger')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))


@subscriber_web_bp.route('/<subscriber_id>/subscriptions/renew', methods=['POST'])
@web_subscriber_access_required
def renew_subscription_post(org_id, subscriber_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Process subscription renewal"""
    try:
        subscriber_uuid = UUID(subscriber_id)
        subscription_id = request.form.get('subscription_id')
        plan_id = request.form.get('plan_id')
        auto_renew = request.form.get('auto_renew') == 'true'
        
        if not subscription_id:
            flash('Invalid subscription', 'danger')
            return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
        
        subscription_uuid = UUID(subscription_id)
        
        # If a new plan is selected (not 'same'), upgrade to different plan
        if plan_id and plan_id != 'same':
            # Cancel old subscription
            try:
                billing_service.cancel_subscription(subscription_uuid, current_organization.id, 'replaced_by_new_plan')
            except Exception as e:
                logger.warning(f"Could not cancel old subscription: {e}")
            
            # Create new subscription with new plan
            new_subscription = billing_service.create_subscription(
                organization_id=current_organization.id,
                subscriber_id=subscriber_uuid,
                plan_id=UUID(plan_id),
                auto_renew=auto_renew
            )
            
            flash(f'Plan upgraded to {new_subscription.plan.name} successfully! Expires: {new_subscription.expiry_time.strftime("%Y-%m-%d")}', 'success')
        else:
            # Renew with same plan
            result = billing_service.renew_subscription(subscription_uuid, current_organization.id)
            
            # Get updated subscription to show new expiry
            updated_sub = billing_service.get_subscription(subscription_uuid, current_organization.id)
            flash(f'Subscription renewed successfully! New expiry: {updated_sub.expiry_time.strftime("%Y-%m-%d")}', 'success')
        
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
        
    except ValueError as e:
        flash(f'Invalid ID format: {str(e)}', 'danger')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
    except NotFoundError as e:
        flash(str(e), 'danger')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
    except BusinessError as e:
        flash(str(e), 'warning')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
    except Exception as e:
        logger.error(f"Error processing renewal: {e}", exc_info=True)
        flash(f'Error renewing subscription: {str(e)}', 'danger')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))


@subscriber_web_bp.route('/subscriptions/<subscription_id>/cancel', methods=['POST'])
@web_subscriber_access_required
def cancel_subscription(org_id, subscription_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Cancel a subscription"""
    
    try:
        sub_uuid = UUID(subscription_id)
        reason = request.form.get('reason', 'user_requested')
        billing_service.cancel_subscription(sub_uuid, current_organization.id, reason)
        flash('Subscription cancelled successfully', 'success')
        
    except ValueError:
        flash('Invalid subscription ID format', 'danger')
    except Exception as e:
        logger.error(f"Error cancelling subscription: {e}", exc_info=True)
        flash(f'Error cancelling subscription: {str(e)}', 'danger')
    
    return redirect(request.referrer or url_for('subscriber_web.index', org_id=org_id))

# DEVICE MANAGEMENT

@subscriber_web_bp.route('/<subscriber_id>/devices/create', methods=['GET', 'POST'])
@web_subscriber_access_required
def add_device(org_id, subscriber_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Add device to subscriber"""
    
    try:
        subscriber_uuid = UUID(subscriber_id)
        
        if request.method == 'GET':
            return render_template(
                'web/subscriber/add_device.html',
                organization=current_organization,
                user=current_user,
                subscriber_id=subscriber_id
            )
        
        # POST - Add device
        mac_address = request.form.get('mac_address', '').strip().upper()
        device_name = request.form.get('device_name', '').strip()
        device_type = request.form.get('device_type', 'other')
        
        if not mac_address:
            flash('MAC address is required', 'danger')
            return render_template('web/subscriber/add_device.html',
                                 organization=current_organization, user=current_user,
                                 subscriber_id=subscriber_id, form_data=request.form)
        
        result = subscriber_service.add_device(
            subscriber_id=subscriber_uuid,
            organization_id=current_organization.id,
            mac_address=mac_address,
            device_name=device_name if device_name else None,
            device_type=device_type
        )
        
        if result.get('success'):
            flash('Device added successfully', 'success')
        else:
            flash(result.get('message', 'Error adding device'), 'danger')
        
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))
        
    except ValueError:
        flash('Invalid subscriber ID format', 'danger')
        return redirect(url_for('subscriber_web.index', org_id=org_id))
    except Exception as e:
        logger.error(f"Error adding device: {e}", exc_info=True)
        flash(f'Error adding device: {str(e)}', 'danger')
        return redirect(url_for('subscriber_web.show', org_id=org_id, subscriber_id=subscriber_id))


@subscriber_web_bp.route('/devices/<device_id>/delete', methods=['POST'])
@web_subscriber_access_required
def remove_device(org_id, device_id, current_user=None, current_organization=None, hotspot_plans=None, pppoe_plans=None):
    """Remove device from subscriber"""
    
    try:
        device_uuid = UUID(device_id)
        subscriber_service.remove_device(device_uuid, current_organization.id)
        flash('Device removed successfully', 'success')
        
    except ValueError:
        flash('Invalid device ID format', 'danger')
    except Exception as e:
        logger.error(f"Error removing device: {e}", exc_info=True)
        flash(f'Error removing device: {str(e)}', 'danger')
    
    return redirect(request.referrer or url_for('subscriber_web.index', org_id=org_id))