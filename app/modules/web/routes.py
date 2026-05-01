# Add these imports at the top if not already present
from flask import render_template, request, redirect, url_for, session, jsonify, g, current_app
from datetime import datetime
import uuid
import re
from app.modules.web import web_bp
from app.modules.auth.service import AuthService
from app.modules.subscriber.service import SubscriberService
from app.modules.billing.service import BillingService
from app.modules.session.service import SessionService
from app.core.logging.logger import logger
from app.core.decorators.web_auth import web_login_required, web_super_admin_required, web_organization_member_required


# REGISTRATION API ENDPOINTS 
@web_bp.route('/api/check-email', methods=['POST'])
def check_email_availability():
    """API: Check if email is available for registration"""
    try:
        data = request.get_json()
        email = data.get('email')
        
        if not email:
            return jsonify({'available': False, 'error': 'Email is required'}), 400
        
        # Validate email format
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            return jsonify({'available': False, 'error': 'Invalid email format'}), 400
        
        auth_service = AuthService()
        result = auth_service.check_email_availability(email)
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Check email error: {e}", exc_info=True)
        return jsonify({'available': False, 'error': str(e)}), 500


@web_bp.route('/api/check-slug', methods=['POST'])
def check_slug_availability():
    """API: Check if organization slug is available"""
    try:
        data = request.get_json()
        slug = data.get('slug')
        
        if not slug:
            return jsonify({'available': False, 'error': 'Slug is required'}), 400
        
        # Validate slug format
        slug_pattern = r'^[a-z0-9-]+$'
        if not re.match(slug_pattern, slug):
            return jsonify({
                'available': False, 
                'error': 'Slug must contain only lowercase letters, numbers, and hyphens'
            }), 400
        
        if slug.startswith('-') or slug.endswith('-'):
            return jsonify({
                'available': False, 
                'error': 'Slug cannot start or end with a hyphen'
            }), 400
        
        auth_service = AuthService()
        result = auth_service.check_org_slug_availability(slug)
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Check slug error: {e}", exc_info=True)
        return jsonify({'available': False, 'error': str(e)}), 500


@web_bp.route('/api/send-verification', methods=['POST'])
def send_verification():
    """API: Send verification email to user"""
    try:
        data = request.get_json()
        email = data.get('email')
        
        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        auth_service = AuthService()
        result = auth_service.send_verification_email(email)
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Send verification error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@web_bp.route('/api/verify-email-token', methods=['POST'])
def verify_email_token():
    """API: Verify email token"""
    try:
        data = request.get_json()
        token = data.get('token')
        
        if not token:
            return jsonify({'success': False, 'error': 'Token is required'}), 400
        
        auth_service = AuthService()
        result = auth_service.verify_email(token)
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Verify email token error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@web_bp.route('/api/register-organization', methods=['POST'])
def register_organization():
    """API: Complete organization registration"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['email', 'password', 'first_name', 'last_name', 
                          'phone', 'organization_name', 'organization_slug']
        
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'{field} is required'}), 400
        
        # Validate password strength
        password = data.get('password')
        if len(password) < 8:
            return jsonify({'success': False, 'error': 'Password must be at least 8 characters'}), 400
        if not any(c.isupper() for c in password):
            return jsonify({'success': False, 'error': 'Password must contain at least one uppercase letter'}), 400
        if not any(c.isdigit() for c in password):
            return jsonify({'success': False, 'error': 'Password must contain at least one number'}), 400
        
        auth_service = AuthService()
        result = auth_service.register_organization(data)
        
        return jsonify(result), 201
        
    except Exception as e:
        logger.error(f"Register organization error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@web_bp.route('/api/resend-verification', methods=['POST'])
def resend_verification():
    """API: Resend verification email"""
    try:
        data = request.get_json()
        email = data.get('email')
        
        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        auth_service = AuthService()
        result = auth_service.resend_verification_email(email)
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Resend verification error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# WEB TEMPLATE ROUTES 
@web_bp.route('/')
def index():
    """Landing page"""
    # Check if user is already logged in via session
    if session.get('user_id'):
        user = session.get('user')
        if user and user.get('is_super_admin'):
            return redirect(url_for('web.super_admin_dashboard'))
        elif user and user.get('organization_id'):
            return redirect(url_for('web.organization_dashboard', org_id=user['organization_id']))
        else:
            return redirect(url_for('web.dashboard'))
    
    return render_template('web/index.html')


@web_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Admin/Staff login page"""
    # If already logged in, redirect to appropriate dashboard
    if session.get('user_id'):
        user = session.get('user')
        if user and user.get('is_super_admin'):
            return redirect(url_for('web.super_admin_dashboard'))
        elif user and user.get('organization_id'):
            return redirect(url_for('web.organization_dashboard', org_id=user['organization_id']))
        else:
            return redirect(url_for('web.dashboard'))
    
    if request.method == 'GET':
        # Check if there's a next URL
        next_url = request.args.get('next')
        if next_url:
            session['next_url'] = next_url
        return render_template('web/login.html')
    
    # POST - handle login
    try:
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            return render_template('web/login.html', error="Email and password are required")
        
        auth_service = AuthService()
        
        # Call the existing login method
        result = auth_service.login(
            email=email,
            password=password,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')
        )
        
        # Store in session
        session['access_token'] = result.get('access_token')
        session['refresh_token'] = result.get('refresh_token')
        session['user'] = result.get('user')
        session['user_id'] = result.get('user', {}).get('id')
        
        user_data = result.get('user', {})
        
        # Check for next URL
        next_url = session.pop('next_url', None)
        
        if user_data.get('is_super_admin'):
            return redirect(next_url or url_for('web.super_admin_dashboard'))
        elif user_data.get('organization_id'):
            return redirect(next_url or url_for('web.organization_dashboard', 
                                              org_id=user_data['organization_id']))
        else:
            return redirect(next_url or url_for('web.dashboard'))
        
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return render_template('web/login.html', error=str(e))


@web_bp.route('/dashboard')
@web_login_required
def dashboard():
    """User dashboard"""
    user = g.current_user
    
    if user.is_super_admin:
        return redirect(url_for('web.super_admin_dashboard'))
    elif user.organization_id:
        return redirect(url_for('web.organization_dashboard', org_id=user.organization_id))
    
    return render_template('web/dashboard.html', user=user.to_dict() if hasattr(user, 'to_dict') else {'email': user.email})


@web_bp.route('/organization/<org_id>/dashboard')
@web_login_required
@web_organization_member_required
def organization_dashboard(org_id):
    """Organization-specific dashboard"""
    user = g.current_user
    organization = g.current_organization if hasattr(g, 'current_organization') else None
    
    if not organization:
        from app.models.organization import Organization
        organization = Organization.query.get(org_id)
    
    return render_template(
        'web/organization/dashboard.html',
        user=user,
        organization=organization
    )


@web_bp.route('/super-admin')
@web_super_admin_required
def super_admin_dashboard():
    """Super admin dashboard"""
    user = g.current_user
    return render_template('web/super_admin/dashboard.html', user=user)


@web_bp.route('/logout')
def logout():
    """Logout"""
    # Optionally revoke token
    if session.get('refresh_token'):
        try:
            auth_service = AuthService()
            auth_service.logout(
                user_id=session.get('user_id'),
                refresh_token=session.get('refresh_token')
            )
        except Exception as e:
            logger.error(f"Error during logout: {e}")
    
    session.clear()
    return redirect(url_for('web.index'))


@web_bp.route('/register')
def register_page():
    """Registration page"""
    email = request.args.get('email')
    # If email is provided, pre-fill it in the registration form
    return render_template('web/register.html', email=email)


@web_bp.route('/register-success')
def register_success():
    """Registration success page with organization URL"""
    org_slug = request.args.get('org_slug')
    org_name = request.args.get('org_name')
    
    return render_template('web/register_success.html', 
                          org_name=org_name, 
                          org_slug=org_slug)


@web_bp.route('/verify-email')
def verify_email_page():
    """Email verification page"""
    token = request.args.get('token')
    
    if not token:
        return redirect(url_for('web.index'))
    
    return render_template('web/verify_email.html', token=token)


# HOTSPOT ROUTES (Public ) 
@web_bp.route('/hotspot/<org_slug>')
def hotspot_portal(org_slug):
    """Captive portal landing page (public)"""
    from app.models import Organization
    organization = Organization.query.filter_by(slug=org_slug).first()
    
    if not organization:
        return render_template('error.html', message="Organization not found"), 404
    
    # Get available plans
    billing_service = BillingService()
    plans = billing_service.get_public_plans(organization.id)
    
    # Get hotspot info from query params
    hotspot_id = request.args.get('hotspot')
    ap_mac = request.args.get('ap')
    router_ip = request.args.get('router')
    
    return render_template(
        'web/hotspot/index.html',
        organization=organization,
        plans=plans,
        hotspot_id=hotspot_id,
        ap_mac=ap_mac,
        router_ip=router_ip
    )


@web_bp.route('/hotspot/<org_slug>/connect', methods=['POST'])
def hotspot_connect(org_slug):
    """Handle hotspot connection request (public API)"""
    try:
        data = request.get_json() or request.form
        phone = data.get('phone')
        voucher_code = data.get('voucher_code')
        device_mac = data.get('device_mac')
        ap_mac = data.get('ap_mac')
        router_ip = data.get('router_ip')
        
        from app.models import Organization
        organization = Organization.query.filter_by(slug=org_slug).first()
        
        if not organization:
            return jsonify({'error': 'Organization not found'}), 404
        
        subscriber_service = SubscriberService()
        
        if voucher_code:
            # Redeem voucher
            result = subscriber_service.redeem_voucher(
                organization_id=organization.id,
                voucher_code=voucher_code,
                device_mac=device_mac,
                router_ip=router_ip,
                ap_mac=ap_mac
            )
        elif phone:
            # Check existing subscription
            subscriber, is_new = subscriber_service.get_or_create_subscriber(
                organization_id=organization.id,
                phone=phone
            )
            
            # Check if subscriber has active subscription
            subscription = subscriber_service.get_active_subscription(subscriber.id)
            
            if subscription:
                # Create session
                session_service = SessionService()
                session_result = session_service.create_hotspot_session(
                    subscriber_id=subscriber.id,
                    organization_id=organization.id,
                    device_mac=device_mac,
                    router_ip=router_ip,
                    ap_mac=ap_mac
                )
                result = {
                    'success': True,
                    'message': 'Connected successfully',
                    'session': session_result
                }
            else:
                # No active subscription - redirect to payment
                result = {
                    'success': False,
                    'requires_payment': True,
                    'subscriber_id': str(subscriber.id),
                    'message': 'No active subscription. Please purchase a plan.'
                }
        else:
            return jsonify({'error': 'Phone number or voucher code required'}), 400
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Hotspot connect error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@web_bp.route('/hotspot/<org_slug>/plans')
def hotspot_plans(org_slug):
    """Get available plans for hotspot (public API)"""
    from app.models import Organization
    from app.modules.billing.service import BillingService
    
    organization = Organization.query.filter_by(slug=org_slug).first()
    
    if not organization:
        return jsonify({'error': 'Organization not found'}), 404
    
    billing_service = BillingService()
    plans = billing_service.get_public_plans(organization.id)
    
    return jsonify({
        'plans': [plan.to_dict() for plan in plans],
        'currency': organization.currency
    })


@web_bp.route('/hotspot/<org_slug>/purchase', methods=['POST'])
def hotspot_purchase(org_slug):
    """Purchase plan from hotspot portal (public API)"""
    try:
        data = request.get_json()
        plan_id = data.get('plan_id')
        phone = data.get('phone')
        payment_method = data.get('payment_method', 'mpesa')
        device_mac = data.get('device_mac')
        
        from app.models import Organization
        from app.modules.subscriber.service import SubscriberService
        from app.modules.billing.service import BillingService
        
        organization = Organization.query.filter_by(slug=org_slug).first()
        
        if not organization:
            return jsonify({'error': 'Organization not found'}), 404
        
        # Get or create subscriber
        subscriber_service = SubscriberService()
        subscriber, is_new = subscriber_service.get_or_create_subscriber(
            organization_id=organization.id,
            phone=phone
        )
        
        # Purchase plan
        billing_service = BillingService()
        result = billing_service.purchase_plan(
            subscriber_id=subscriber.id,
            plan_id=plan_id,
            payment_method=payment_method,
            payment_details={
                'phone': phone,
                'ip_address': request.remote_addr,
                'user_agent': request.headers.get('User-Agent', '')
            }
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Purchase error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@web_bp.route('/hotspot/<org_slug>/status/<session_id>')
def hotspot_status(org_slug, session_id):
    """Check hotspot session status (public API)"""
    from app.modules.session.service import SessionService
    
    session_service = SessionService()
    session_data = session_service.get_session(session_id)
    
    if not session_data:
        return jsonify({'active': False, 'message': 'Session not found'})
    
    # Verify session belongs to the organization
    from app.models import Organization
    organization = Organization.query.filter_by(slug=org_slug).first()
    if not organization or str(session_data.organization_id) != str(organization.id):
        return jsonify({'active': False, 'message': 'Access denied'}), 403
    
    return jsonify({
        'active': session_data.status == 'active',
        'expires_at': session_data.expiry_time.isoformat() if session_data.expiry_time else None,
        'data_used_mb': (session_data.bytes_in + session_data.bytes_out) / (1024 * 1024) if session_data else 0
    })


# ERROR HANDLERS 
@web_bp.errorhandler(404)
def not_found_error(error):
    """404 error handler"""
    return render_template('web/404.html'), 404


@web_bp.errorhandler(500)
def internal_error(error):
    """500 error handler"""
    logger.error(f"Internal server error: {error}", exc_info=True)
    return render_template('web/500.html'), 500




    