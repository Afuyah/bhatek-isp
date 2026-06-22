"""
Subscriber Controller
=====================
REST API controller for subscriber management.

Handles hotspot (M-Pesa auto-created) and PPPoE (admin-created) users.
All endpoints require JWT authentication with organization context.
"""

from flask import request, g, jsonify
from marshmallow import ValidationError
from uuid import UUID

from app.modules.subscriber.service import SubscriberService
from app.modules.subscriber.schemas import (
    SubscriberCreateSchema,
    SubscriberUpdateSchema,
    PurchasePlanSchema,
    CheckAccessSchema,
    AddDeviceSchema,
    PPPoECreateSchema,
)
from app.core.security.jwt import token_required, permission_required
from app.core.logging.logger import logger
from app.core.exceptions.handlers import (
    NotFoundError,
    BusinessError,
    ValidationError as AppValidationError,
)


class SubscriberController:
    """Subscriber controller for hotspot and PPPoE users."""

    def __init__(self):
        self.service = SubscriberService()

    # =========================================================================
    # CREATE
    # =========================================================================

    @token_required
    def create_hotspot_subscriber(self):
        """
        POST /api/v1/subscribers/hotspot

        Create or get hotspot subscriber (auto-created via phone).
        Used during M-Pesa payment flow.
        """
        try:
            data = SubscriberCreateSchema().load(request.json)
            subscriber, created = self.service.get_or_create_hotspot_subscriber(
                organization_id=g.organization_id,
                phone=data['phone'],
                name=data.get('name'),
            )
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
                'created': created,
                'message': (
                    'Subscriber created successfully'
                    if created else 'Subscriber already exists'
                ),
            }), 201 if created else 200

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
            logger.error(
                f"Create hotspot subscriber error: {e}", exc_info=True
            )
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    @permission_required('pppoe_create')
    def create_pppoe_subscriber(self):
        """
        POST /api/v1/subscribers/pppoe

        Create PPPoE subscriber (admin only).
        """
        try:
            data = PPPoECreateSchema().load(request.json)
            subscriber = self.service.create_pppoe_subscriber(
                organization_id=g.organization_id,
                username=data['username'],
                password=data['password'],
                plan_id=UUID(data['plan_id']),
                phone=data.get('phone'),
                first_name=data.get('first_name'),
                last_name=data.get('last_name'),
            )
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
                'message': 'PPPoE subscriber created successfully',
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
            logger.error(
                f"Create PPPoE subscriber error: {e}", exc_info=True
            )
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # READ
    # =========================================================================

    @token_required
    def get_subscriber(self, subscriber_id):
        """
        GET /api/v1/subscribers/<subscriber_id>

        Get subscriber by ID.
        """
        try:
            subscriber_uuid = UUID(subscriber_id)
            subscriber = self.service.get_subscriber(
                subscriber_uuid, g.organization_id
            )
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get subscriber error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def list_subscribers(self):
        """
        GET /api/v1/subscribers

        List subscribers with pagination and filters.

        Query params: page, per_page, status, search,
                      has_active_subscription, subscriber_type
        """
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page

            # Build filters
            filters = {}
            if request.args.get('status'):
                filters['status'] = request.args.get('status')
            if request.args.get('search'):
                filters['search'] = request.args.get('search')
            if request.args.get('has_active_subscription') is not None:
                filters['has_active_subscription'] = (
                    request.args.get('has_active_subscription').lower() == 'true'
                )

            subscriber_type = request.args.get('subscriber_type')

            subscribers = self.service.get_organization_subscribers(
                g.organization_id, skip, per_page, filters, subscriber_type
            )
            total = self.service.repository.count_by_organization(
                g.organization_id, subscriber_type=subscriber_type
            )

            return jsonify({
                'success': True,
                'subscribers': [s.to_dict() for s in subscribers],
                'pagination': {
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'pages': (total + per_page - 1) // per_page if total else 0,
                    'has_next': (page * per_page) < total,
                    'has_prev': page > 1,
                },
            }), 200

        except Exception as e:
            logger.error(f"List subscribers error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def list_hotspot_users(self):
        """
        GET /api/v1/subscribers/hotspot

        List hotspot users only.
        """
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page

            subscribers = self.service.get_hotspot_users(
                g.organization_id, skip, per_page
            )
            total = self.service.repository.count_by_organization(
                g.organization_id, subscriber_type='hotspot'
            )

            return jsonify({
                'success': True,
                'subscribers': [s.to_dict() for s in subscribers],
                'pagination': {
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'pages': (total + per_page - 1) // per_page if total else 0,
                },
            }), 200

        except Exception as e:
            logger.error(f"List hotspot users error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def list_pppoe_users(self):
        """
        GET /api/v1/subscribers/pppoe

        List PPPoE users only.
        """
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 20, type=int), 100)
            skip = (page - 1) * per_page

            subscribers = self.service.get_pppoe_users(
                g.organization_id, skip, per_page
            )
            total = self.service.repository.count_by_organization(
                g.organization_id, subscriber_type='pppoe'
            )

            return jsonify({
                'success': True,
                'subscribers': [s.to_dict() for s in subscribers],
                'pagination': {
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'pages': (total + per_page - 1) // per_page if total else 0,
                },
            }), 200

        except Exception as e:
            logger.error(f"List PPPoE users error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # UPDATE
    # =========================================================================

    @token_required
    def update_subscriber(self, subscriber_id):
        """
        PUT /api/v1/subscribers/<subscriber_id>

        Update subscriber information.
        """
        try:
            data = SubscriberUpdateSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            subscriber = self.service.update_subscriber(
                subscriber_uuid, g.organization_id, data
            )
            return jsonify({
                'success': True,
                'subscriber': subscriber.to_dict(),
                'message': 'Subscriber updated successfully',
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
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except AppValidationError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'VALIDATION_ERROR',
            }), 400
        except Exception as e:
            logger.error(f"Update subscriber error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # DELETE
    # =========================================================================

    @token_required
    def delete_subscriber(self, subscriber_id):
        """
        DELETE /api/v1/subscribers/<subscriber_id>?soft=true

        Delete or deactivate subscriber.
        """
        try:
            subscriber_uuid = UUID(subscriber_id)
            soft = request.args.get('soft', 'true').lower() == 'true'
            self.service.delete_subscriber(
                subscriber_uuid, g.organization_id, soft_delete=soft
            )

            message = (
                'Subscriber deactivated successfully'
                if soft else 'Subscriber permanently deleted'
            )
            return jsonify({
                'success': True,
                'message': message,
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscriber ID format',
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
            logger.error(f"Delete subscriber error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # SUBSCRIPTION MANAGEMENT
    # =========================================================================

    @token_required
    def purchase_plan(self, subscriber_id):
        """
        POST /api/v1/subscribers/<subscriber_id>/purchase

        Purchase a plan for a subscriber (creates subscription + processes payment).
        """
        try:
            data = PurchasePlanSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            result = self.service.purchase_plan(
                organization_id=g.organization_id,
                subscriber_id=subscriber_uuid,
                plan_id=UUID(data['plan_id']),
                payment_method=data['payment_method'],
                payment_details=data.get('payment_details', {}),
            )

            status_code = 200 if result.get('success') else 400
            return jsonify(result), status_code

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
            }), 409
        except Exception as e:
            logger.error(f"Purchase plan error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def create_subscription(self, subscriber_id):
        """
        POST /api/v1/subscribers/<subscriber_id>/subscriptions

        Create subscription for a subscriber (admin action for PPPoE users).
        """
        try:
            data = request.get_json() or {}
            plan_id = UUID(data['plan_id'])
            auto_renew = data.get('auto_renew', False)

            subscriber_uuid = UUID(subscriber_id)
            subscription = self.service.create_subscription(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id,
                plan_id=plan_id,
                auto_renew=auto_renew,
            )

            return jsonify({
                'success': True,
                'subscription': subscription.to_dict(include_plan=True),
                'message': 'Subscription created successfully',
            }), 201

        except (ValueError, KeyError):
            return jsonify({
                'success': False,
                'error': 'Invalid UUID format or missing plan_id',
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
            logger.error(f"Create subscription error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_active_subscription(self, subscriber_id):
        """
        GET /api/v1/subscribers/<subscriber_id>/subscription/active

        Get active subscription for a subscriber.
        """
        try:
            subscriber_uuid = UUID(subscriber_id)
            subscription = self.service.get_active_subscription(
                subscriber_uuid, g.organization_id
            )

            if not subscription:
                return jsonify({
                    'success': True,
                    'active': False,
                    'message': 'No active subscription found',
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
            logger.error(
                f"Get active subscription error: {e}", exc_info=True
            )
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def renew_subscription(self, subscription_id):
        """
        POST /api/v1/subscribers/subscriptions/<subscription_id>/renew

        Renew an existing subscription.
        """
        try:
            sub_uuid = UUID(subscription_id)
            result = self.service.renew_subscription(
                sub_uuid, g.organization_id
            )
            return jsonify(result), 200

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

    @token_required
    def cancel_subscription(self, subscription_id):
        """
        POST /api/v1/subscribers/subscriptions/<subscription_id>/cancel

        Cancel a subscription.
        """
        try:
            sub_uuid = UUID(subscription_id)
            reason = (
                request.json.get('reason')
                if request.json else None
            )
            result = self.service.cancel_subscription(
                sub_uuid, g.organization_id, reason
            )

            return jsonify({
                'success': result,
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
        except Exception as e:
            logger.error(f"Cancel subscription error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # ACCESS CONTROL
    # =========================================================================

    @token_required
    def check_access(self, subscriber_id):
        """
        POST /api/v1/subscribers/<subscriber_id>/check-access

        Check if subscriber can access internet on a specific device.
        """
        try:
            data = CheckAccessSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            result = self.service.check_subscriber_access(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id,
                device_mac=data['device_mac'],
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
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Check access error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # DEVICE MANAGEMENT
    # =========================================================================

    @token_required
    def add_device(self, subscriber_id):
        """
        POST /api/v1/subscribers/<subscriber_id>/devices

        Add a device (MAC address) to a subscriber.
        """
        try:
            data = AddDeviceSchema().load(request.json)
            subscriber_uuid = UUID(subscriber_id)
            result = self.service.add_device(
                subscriber_id=subscriber_uuid,
                organization_id=g.organization_id,
                mac_address=data['mac_address'],
                device_name=data.get('device_name'),
                device_type=data.get('device_type'),
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
                'error': 'Invalid subscriber ID format',
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
            logger.error(f"Add device error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def remove_device(self, device_id):
        """
        DELETE /api/v1/subscribers/devices/<device_id>

        Remove a device from a subscriber.
        """
        try:
            device_uuid = UUID(device_id)
            result = self.service.remove_device(
                device_uuid, g.organization_id
            )
            return jsonify(result), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid device ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Remove device error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_devices(self, subscriber_id):
        """
        GET /api/v1/subscribers/<subscriber_id>/devices

        Get all devices for a subscriber.
        """
        try:
            subscriber_uuid = UUID(subscriber_id)
            devices = self.service.get_devices(
                subscriber_uuid, g.organization_id
            )
            return jsonify({
                'success': True,
                'devices': devices,
                'count': len(devices),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(f"Get devices error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # STATISTICS & REPORTING
    # =========================================================================

    @token_required
    def get_subscriber_stats(self, subscriber_id):
        """
        GET /api/v1/subscribers/<subscriber_id>/stats

        Get detailed subscriber statistics.
        """
        try:
            subscriber_uuid = UUID(subscriber_id)
            stats = self.service.get_subscriber_stats(
                subscriber_uuid, g.organization_id
            )
            return jsonify({'success': True, **stats}), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except NotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'error_code': 'NOT_FOUND',
            }), 404
        except Exception as e:
            logger.error(f"Get subscriber stats error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_dashboard_stats(self):
        """
        GET /api/v1/subscribers/stats/dashboard

        Get subscriber dashboard statistics for the organization.
        """
        try:
            stats = self.service.get_subscriber_dashboard_stats(
                g.organization_id
            )
            return jsonify({'success': True, **stats}), 200

        except Exception as e:
            logger.error(f"Get dashboard stats error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    @token_required
    def get_subscription_history(self, subscriber_id):
        """
        GET /api/v1/subscribers/<subscriber_id>/subscriptions/history

        Get subscription history for a subscriber.
        """
        try:
            subscriber_uuid = UUID(subscriber_id)
            history = self.service.repository.get_subscription_history(
                subscriber_uuid, g.organization_id, limit=50
            )
            return jsonify({
                'success': True,
                'subscriptions': [
                    {
                        'id': str(sub.id),
                        'plan_name': sub.plan.name if sub.plan else None,
                        'plan_id': str(sub.plan_id),
                        'start_time': (
                            sub.start_time.isoformat()
                            if sub.start_time else None
                        ),
                        'expiry_time': (
                            sub.expiry_time.isoformat()
                            if sub.expiry_time else None
                        ),
                        'status': sub.status,
                        'bandwidth_up': (
                            sub.bandwidth_up_mbps
                            or (sub.plan.bandwidth_up_mbps if sub.plan else 0)
                        ),
                        'bandwidth_down': (
                            sub.bandwidth_down_mbps
                            or (sub.plan.bandwidth_down_mbps if sub.plan else 0)
                        ),
                    }
                    for sub in history
                ],
                'count': len(history),
            }), 200

        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid subscriber ID format',
                'error_code': 'INVALID_UUID',
            }), 400
        except Exception as e:
            logger.error(
                f"Get subscription history error: {e}", exc_info=True
            )
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500

    # =========================================================================
    # AUTHENTICATION (used by captive portal — NOT the main RADIUS path)
    # =========================================================================

    @token_required
    def authenticate(self):
        """
        POST /api/v1/subscribers/authenticate

        Authenticate subscriber credentials.
        Used by captive portal for manual login (not the RADIUS path).

        The main RADIUS authentication path is:
        FreeRADIUS → /api/radius/authenticate → RadiusAuthHandler
        """
        try:
            data = request.get_json() or {}
            username = data.get('username')
            password = data.get('password')

            if not username or not password:
                return jsonify({
                    'success': False,
                    'error': 'Username and password required',
                    'error_code': 'MISSING_CREDENTIALS',
                }), 400

            # Authenticate
            subscriber = self.service.authenticate_subscriber(
                username, password, g.organization_id
            )

            if not subscriber:
                return jsonify({
                    'success': False,
                    'error': 'Authentication failed',
                    'error_code': 'AUTH_FAILED',
                }), 401

            subscription = self.service.get_active_subscription(
                subscriber.id, g.organization_id
            )

            return jsonify({
                'success': True,
                'subscriber_id': str(subscriber.id),
                'subscriber_type': subscriber.subscriber_type,
                'bandwidth_up': (
                    subscription.get_bandwidth_up() if subscription else 0
                ),
                'bandwidth_down': (
                    subscription.get_bandwidth_down() if subscription else 0
                ),
                'session_timeout': (
                    subscription.plan.session_timeout_seconds
                    if subscription else 86400
                ),
                'idle_timeout': (
                    subscription.plan.idle_timeout_seconds
                    if subscription else 300
                ),
            }), 200

        except Exception as e:
            logger.error(f"Authenticate error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Internal server error',
                'error_code': 'INTERNAL_ERROR',
            }), 500