"""
Reporting Controller
====================
REST API controller for all reporting and analytics endpoints.
Delegates to ReportingService for data aggregation.
"""

from flask import request, g, jsonify
from uuid import UUID
from datetime import datetime

from app.modules.reporting.service import ReportingService
from app.core.security.jwt import token_required
from app.core.logging.logger import logger


class ReportingController:
    """Controller for reporting and analytics endpoints."""

    def __init__(self):
        self.service = ReportingService()

    # =========================================================================
    # DASHBOARD OVERVIEW
    # =========================================================================

    @token_required
    def get_dashboard_overview(self):
        """GET /api/v1/reports/dashboard — Organization KPI overview."""
        try:
            data = self.service.get_dashboard_overview(g.organization_id)
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Dashboard overview error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # REVENUE REPORTS
    # =========================================================================

    @token_required
    def get_revenue_report(self):
        """GET /api/v1/reports/revenue — Revenue report for a period."""
        try:
            period = request.args.get('period', 'monthly')
            month = request.args.get('month', type=int)
            year = request.args.get('year', type=int)
            start_str = request.args.get('start_date')
            end_str = request.args.get('end_date')

            start_date = datetime.fromisoformat(start_str) if start_str else None
            end_date = datetime.fromisoformat(end_str) if end_str else None

            data = self.service.get_revenue_report(
                organization_id=g.organization_id,
                period=period,
                start_date=start_date,
                end_date=end_date,
                month=month,
                year=year,
            )
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Revenue report error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # SUBSCRIBER REPORTS
    # =========================================================================

    @token_required
    def get_subscriber_report(self):
        """GET /api/v1/reports/subscribers — Subscriber statistics."""
        try:
            period = request.args.get('period', 'monthly')
            start_str = request.args.get('start_date')
            end_str = request.args.get('end_date')

            start_date = datetime.fromisoformat(start_str) if start_str else None
            end_date = datetime.fromisoformat(end_str) if end_str else None

            data = self.service.get_subscriber_report(
                organization_id=g.organization_id,
                period=period,
                start_date=start_date,
                end_date=end_date,
            )
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Subscriber report error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # ROUTER REPORTS
    # =========================================================================

    @token_required
    def get_router_report(self):
        """GET /api/v1/reports/routers — Router health and performance."""
        try:
            data = self.service.get_router_report(g.organization_id)
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Router report error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # USAGE REPORTS
    # =========================================================================

    @token_required
    def get_usage_report(self):
        """GET /api/v1/reports/usage — Data usage and session statistics."""
        try:
            period = request.args.get('period', 'monthly')
            start_str = request.args.get('start_date')
            end_str = request.args.get('end_date')

            start_date = datetime.fromisoformat(start_str) if start_str else None
            end_date = datetime.fromisoformat(end_str) if end_str else None

            data = self.service.get_usage_report(
                organization_id=g.organization_id,
                period=period,
                start_date=start_date,
                end_date=end_date,
            )
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Usage report error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # CHART DATA ENDPOINTS
    # =========================================================================

    @token_required
    def get_revenue_trend(self):
        """GET /api/v1/reports/charts/revenue-trend — Daily revenue trend."""
        try:
            period = request.args.get('period', 'monthly')
            month = request.args.get('month', type=int)
            year = request.args.get('year', type=int)
            data = self.service.get_revenue_report(
                organization_id=g.organization_id,
                period=period,
                month=month,
                year=year,
            )
            return jsonify({'success': True, 'data': data.get('daily_trend', [])}), 200
        except Exception as e:
            logger.error(f"Revenue trend error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @token_required
    def get_subscriber_growth(self):
        """GET /api/v1/reports/charts/subscriber-growth — Subscriber growth trend."""
        try:
            period = request.args.get('period', 'monthly')
            data = self.service.get_subscriber_report(
                organization_id=g.organization_id,
                period=period,
            )
            return jsonify({'success': True, 'data': data.get('growth_trend', [])}), 200
        except Exception as e:
            logger.error(f"Subscriber growth error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @token_required
    def get_plan_distribution(self):
        """GET /api/v1/reports/charts/plan-distribution — Plan subscriber distribution."""
        try:
            data = self.service.get_plan_distribution(g.organization_id)
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Plan distribution error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @token_required
    def get_payment_method_distribution(self):
        """GET /api/v1/reports/charts/payment-methods — Payment method breakdown."""
        try:
            period = request.args.get('period', 'monthly')
            data = self.service.get_payment_method_distribution(
                organization_id=g.organization_id,
                period=period,
            )
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Payment method distribution error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @token_required
    def get_bandwidth_usage(self):
        """GET /api/v1/reports/charts/bandwidth — Bandwidth usage trend."""
        try:
            days = request.args.get('days', 30, type=int)
            data = self.service.get_bandwidth_trend(
                organization_id=g.organization_id,
                days=days,
            )
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Bandwidth usage error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @token_required
    def get_churn_rate(self):
        """GET /api/v1/reports/charts/churn-rate — Monthly churn rate."""
        try:
            months = request.args.get('months', 6, type=int)
            data = self.service.get_churn_rate(
                organization_id=g.organization_id,
                months=months,
            )
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Churn rate error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @token_required
    def get_router_status(self):
        """GET /api/v1/reports/charts/router-status — Router status breakdown."""
        try:
            data = self.service.get_router_report(g.organization_id)
            return jsonify({
                'success': True,
                'data': data.get('status_breakdown', {}),
                'total': data.get('total_routers', 0),
            }), 200
        except Exception as e:
            logger.error(f"Router status error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # RECENT ACTIVITY
    # =========================================================================

    @token_required
    def get_recent_activity(self):
        """GET /api/v1/reports/activity — Recent platform activity."""
        try:
            limit = request.args.get('limit', 20, type=int)
            data = self.service.get_recent_activity(
                organization_id=g.organization_id,
                limit=limit,
            )
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"Recent activity error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    # =========================================================================
    # ISP-WIDE (super admin)
    # =========================================================================

    @token_required
    def get_isp_overview(self):
        """GET /api/v1/reports/isp — Platform-wide overview (super admin)."""
        try:
            user = getattr(g, 'current_user', None)
            if user and not getattr(user, 'is_super_admin', False):
                return jsonify({'success': False, 'error': 'Super admin access required'}), 403
            data = self.service.get_isp_wide_overview()
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            logger.error(f"ISP overview error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500
