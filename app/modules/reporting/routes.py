"""
Reporting API Routes
====================
Blueprint for all reporting and analytics endpoints.

Route Structure:
    /api/v1/reports/
        ├── GET  /dashboard                     Organization KPI overview
        ├── GET  /revenue                       Revenue report (?period=monthly&month=N&year=YYYY)
        ├── GET  /subscribers                   Subscriber statistics
        ├── GET  /routers                       Router health report
        ├── GET  /usage                         Data usage & session report
        ├── GET  /activity                      Recent activity feed
        ├── GET  /isp                           ISP-wide overview (super admin)
        │
        └── Charts
            ├── GET  /charts/revenue-trend      Daily revenue trend
            ├── GET  /charts/subscriber-growth  Subscriber growth trend
            ├── GET  /charts/plan-distribution  Plan subscriber distribution
            ├── GET  /charts/payment-methods    Payment method breakdown
            ├── GET  /charts/bandwidth          Bandwidth usage trend
            ├── GET  /charts/churn-rate         Monthly churn rate
            └── GET  /charts/router-status      Router status breakdown
"""

from flask import Blueprint
from app.modules.reporting.controller import ReportingController
from app.core.security.jwt import token_required

reports_bp = Blueprint('reports', __name__, url_prefix='/api/v1/reports')
controller = ReportingController()


# ─── Overview ────────────────────────────────────────────────────────────────

@reports_bp.route('/dashboard', methods=['GET'])
@token_required
def get_dashboard_overview():
    """GET /api/v1/reports/dashboard"""
    return controller.get_dashboard_overview()


# ─── Core Reports ─────────────────────────────────────────────────────────────

@reports_bp.route('/revenue', methods=['GET'])
@token_required
def get_revenue_report():
    """GET /api/v1/reports/revenue"""
    return controller.get_revenue_report()


@reports_bp.route('/subscribers', methods=['GET'])
@token_required
def get_subscriber_report():
    """GET /api/v1/reports/subscribers"""
    return controller.get_subscriber_report()


@reports_bp.route('/routers', methods=['GET'])
@token_required
def get_router_report():
    """GET /api/v1/reports/routers"""
    return controller.get_router_report()


@reports_bp.route('/usage', methods=['GET'])
@token_required
def get_usage_report():
    """GET /api/v1/reports/usage"""
    return controller.get_usage_report()


@reports_bp.route('/activity', methods=['GET'])
@token_required
def get_recent_activity():
    """GET /api/v1/reports/activity"""
    return controller.get_recent_activity()


@reports_bp.route('/isp', methods=['GET'])
@token_required
def get_isp_overview():
    """GET /api/v1/reports/isp — Super admin only"""
    return controller.get_isp_overview()


# ─── Chart Data ───────────────────────────────────────────────────────────────

@reports_bp.route('/charts/revenue-trend', methods=['GET'])
@token_required
def get_revenue_trend():
    """GET /api/v1/reports/charts/revenue-trend"""
    return controller.get_revenue_trend()


@reports_bp.route('/charts/subscriber-growth', methods=['GET'])
@token_required
def get_subscriber_growth():
    """GET /api/v1/reports/charts/subscriber-growth"""
    return controller.get_subscriber_growth()


@reports_bp.route('/charts/plan-distribution', methods=['GET'])
@token_required
def get_plan_distribution():
    """GET /api/v1/reports/charts/plan-distribution"""
    return controller.get_plan_distribution()


@reports_bp.route('/charts/payment-methods', methods=['GET'])
@token_required
def get_payment_method_distribution():
    """GET /api/v1/reports/charts/payment-methods"""
    return controller.get_payment_method_distribution()


@reports_bp.route('/charts/bandwidth', methods=['GET'])
@token_required
def get_bandwidth_usage():
    """GET /api/v1/reports/charts/bandwidth"""
    return controller.get_bandwidth_usage()


@reports_bp.route('/charts/churn-rate', methods=['GET'])
@token_required
def get_churn_rate():
    """GET /api/v1/reports/charts/churn-rate"""
    return controller.get_churn_rate()


@reports_bp.route('/charts/router-status', methods=['GET'])
@token_required
def get_router_status():
    """GET /api/v1/reports/charts/router-status"""
    return controller.get_router_status()
