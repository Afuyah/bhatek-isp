from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from uuid import UUID
from datetime import datetime

from app.models.organization import Organization
from app.models.router import Router
from app.modules.billing.service import BillingService
from app.modules.subscriber.service import SubscriberService
from app.modules.payment.service import PaymentService
from app.core.logging.logger import logger

hotspot_bp = Blueprint('hotspot', __name__, url_prefix='/hotspot')

# HELPERS
def _get_org_by_slug(org_slug: str) -> Organization:
    """Resolve organization from URL slug."""
    org = Organization.query.filter_by(slug=org_slug, status='active').first()
    if not org:
        return None
    return org


def _get_portal_theme(org: Organization) -> dict:
    """Extract portal theme from organization settings with defaults."""
    settings = org.settings or {}
    theme = settings.get('portal_theme', {})
    return {
        'primary_color': theme.get('primary_color', '#4f46e5'),
        'welcome_title': theme.get('welcome_title', f'Welcome to {org.name}'),
        'welcome_subtitle': theme.get('welcome_subtitle', 'Fast, reliable internet'),
        'show_plans': theme.get('show_plans', True),
        'show_vouchers': theme.get('show_vouchers', True),
        'show_mpesa_code': theme.get('show_mpesa_code', True),
        'org_name': org.name,
        'org_slug': org.slug,
    }


def _get_router_from_request() -> Router:
    """Get router from NAS IP or MAC in request context."""
    nas_ip = request.args.get('nas') or request.remote_addr
    if nas_ip:
        router = Router.query.filter_by(ip_address=nas_ip, is_active=True).first()
        if router:
            return router
    return None

# MAIN PORTAL PAGE
@hotspot_bp.route('/<org_slug>')
def portal(org_slug):
    """
    Main captive portal page.

    Query params (from MikroTik redirect):
        mac: Client MAC address
        nas: NAS IP address
        ssid: Connected SSID
        target: Original URL user was trying to access
    """
    org = _get_org_by_slug(org_slug)
    if not org:
        return render_template('hotspot/error.html',
            message='WiFi service not found',
            org_name='Unknown'), 404

    theme = _get_portal_theme(org)
    client_mac = request.args.get('mac', '')
    nas_ip = request.args.get('nas', '')

    # Get public plans for this org
    billing = BillingService()
    plans = billing.get_public_plans(org.id)

    return render_template(
        'hotspot/portal.html',
        theme=theme,
        plans=plans,
        client_mac=client_mac,
        nas_ip=nas_ip,
        organization_id=str(org.id),
    )

# GET PLANS (AJAX)
@hotspot_bp.route('/<org_slug>/plans')
def get_plans(org_slug):
    """Get public plans as JSON for AJAX loading."""
    org = _get_org_by_slug(org_slug)
    if not org:
        return jsonify({'error': 'Organization not found'}), 404

    billing = BillingService()
    plans = billing.get_public_plans(org.id)

    return jsonify({
        'plans': [p.to_dict() for p in plans],
    })

# INITIATE M-PESA PAYMENT
@hotspot_bp.route('/<org_slug>/pay', methods=['POST'])
def initiate_payment(org_slug):
    
    org = _get_org_by_slug(org_slug)
    if not org:
        return jsonify({'success': False, 'error': 'Organization not found'}), 404

    data = request.get_json() or {}
    phone = data.get('phone', '').strip()
    plan_id = data.get('plan_id')
    device_mac = data.get('device_mac', request.args.get('mac', ''))

    if not phone:
        return jsonify({'success': False, 'error': 'Phone number is required'}), 400
    if not plan_id:
        return jsonify({'success': False, 'error': 'Please select a plan'}), 400

    try:
        plan_uuid = UUID(plan_id)
    except (ValueError, AttributeError):
        return jsonify({'success': False, 'error': 'Invalid plan'}), 400

    # Get or create subscriber by phone
    subscriber_service = SubscriberService()
    subscriber, created = subscriber_service.get_or_create_hotspot_subscriber(
        organization_id=org.id,
        phone=phone,
    )

    # Get plan to determine amount
    billing = BillingService()
    plan = billing.get_plan(plan_uuid, org.id)

    # Initiate payment
    payment_service = PaymentService()
    result = payment_service.process_payment(
        organization_id=org.id,
        amount=float(plan.price),
        payment_method='mpesa',
        payment_details={
            'phone': phone,
            'ip_address': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', ''),
        },
        subscriber_id=subscriber.id,
        plan_id=plan_uuid,
        device_mac=device_mac,
        extra_data={
            'plan_name': plan.name,
            'source': 'captive_portal',
        },
    )

    return jsonify(result)

# REDEEM VOUCHER
@hotspot_bp.route('/<org_slug>/redeem', methods=['POST'])
def redeem_voucher(org_slug):
    
    org = _get_org_by_slug(org_slug)
    if not org:
        return jsonify({'success': False, 'error': 'Organization not found'}), 404

    data = request.get_json() or {}
    voucher_code = data.get('voucher_code', '').strip()
    phone = data.get('phone', '').strip()
    device_mac = data.get('device_mac', request.args.get('mac', ''))

    if not voucher_code:
        return jsonify({'success': False, 'error': 'Voucher code is required'}), 400
    if not phone:
        return jsonify({'success': False, 'error': 'Phone number is required'}), 400

    # Get or create subscriber
    subscriber_service = SubscriberService()
    subscriber, created = subscriber_service.get_or_create_hotspot_subscriber(
        organization_id=org.id,
        phone=phone,
    )

    # Redeem voucher
    billing = BillingService()
    try:
        result = billing.redeem_voucher(
            organization_id=org.id,
            voucher_code=voucher_code,
            subscriber_id=subscriber.id,
            device_mac=device_mac,
        )
        return jsonify({
            'success': True,
            'message': 'Voucher redeemed successfully!',
            'plan_name': result.get('plan_name'),
            'expiry_time': result.get('expiry_time'),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# VERIFY M-PESA CODE (SELF-REMEDIATION)
@hotspot_bp.route('/<org_slug>/verify-mpesa', methods=['POST'])
def verify_mpesa_code(org_slug):
    org = _get_org_by_slug(org_slug)
    if not org:
        return jsonify({'success': False, 'error': 'Organization not found'}), 404

    data = request.get_json() or {}
    mpesa_code = data.get('mpesa_code', '').strip().upper()
    device_mac = data.get('device_mac', request.args.get('mac', ''))

    if not mpesa_code:
        return jsonify({'success': False, 'error': 'M-Pesa code is required'}), 400

    # Find transaction by M-Pesa receipt
    payment_service = PaymentService()
    transaction = payment_service.get_transaction_by_mpesa_receipt(
        mpesa_code, org.id
    )

    if not transaction:
        return jsonify({
            'success': False,
            'error': 'M-Pesa code not found. Please check and try again.',
        }), 400

    if transaction.status != 'success':
        return jsonify({
            'success': False,
            'error': 'Payment was not successful.',
        }), 400

    # Get subscriber
    if not transaction.subscriber_id:
        return jsonify({
            'success': False,
            'error': 'No account linked to this payment.',
        }), 400

    subscriber_service = SubscriberService()
    subscriber = subscriber_service.get_subscriber(
        transaction.subscriber_id, org.id
    )

    # Check active subscription
    active_sub = subscriber_service.get_active_subscription(
        subscriber.id, org.id
    )

    if not active_sub:
        return jsonify({
            'success': False,
            'error': 'No active subscription. Your plan may have expired.',
        }), 400

    # Register device MAC for auto-connect
    if device_mac:
        try:
            subscriber_service.add_device(
                subscriber_id=subscriber.id,
                organization_id=org.id,
                mac_address=device_mac,
            )
        except Exception as e:
            logger.warning(f"Device registration failed: {e}")

    return jsonify({
        'success': True,
        'message': 'Payment verified! You are now connected.',
        'plan_name': active_sub.plan.name if active_sub.plan else 'Active Plan',
        'expiry_time': active_sub.expiry_time.isoformat(),
    })

# POLL PAYMENT STATUS
@hotspot_bp.route('/<org_slug>/status/<transaction_id>')
def check_payment_status(org_slug, transaction_id):
    """Poll payment status for real-time UI updates."""
    org = _get_org_by_slug(org_slug)
    if not org:
        return jsonify({'success': False, 'error': 'Organization not found'}), 404

    try:
        txn_uuid = UUID(transaction_id)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid transaction'}), 400

    payment_service = PaymentService()
    transaction = payment_service.get_transaction(txn_uuid)

    if not transaction:
        return jsonify({'status': 'unknown'})

    return jsonify({
        'status': transaction.status,
        'mpesa_receipt': transaction.mpesa_receipt,
    })

# VOUCHER INFO (PRE-REDEMPTION CHECK)
@hotspot_bp.route('/<org_slug>/voucher-info/<voucher_code>')
def voucher_info(org_slug, voucher_code):
    """Get voucher details before redemption."""
    org = _get_org_by_slug(org_slug)
    if not org:
        return jsonify({'success': False, 'error': 'Organization not found'}), 404

    billing = BillingService()
    try:
        info = billing.get_voucher_info(voucher_code, org.id)
        return jsonify({'success': True, 'voucher': info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 404