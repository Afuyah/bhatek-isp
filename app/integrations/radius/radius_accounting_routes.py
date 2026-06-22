"""
RADIUS Accounting Routes
========================
Flask blueprint for RADIUS accounting endpoints.
Called by FreeRADIUS rlm_rest module for session tracking.

Endpoints:
    POST /api/radius/accounting        - Main accounting endpoint
    POST /api/radius/accounting/start  - Session start
    POST /api/radius/accounting/stop   - Session stop
    POST /api/radius/accounting/interim - Interim update

All endpoints always return HTTP 200 with {"result": "ok"}
to prevent FreeRADIUS from retrying and creating duplicates.
"""

from flask import Blueprint, request, jsonify

from app.core.logging.logger import logger
from app.integrations.radius.radius_accounting_handler import (
    RadiusAccountingHandler,
)

# Create blueprint
radius_accounting_bp = Blueprint(
    'radius_accounting',
    __name__,
    url_prefix='/api/radius',
)


@radius_accounting_bp.route('/accounting', methods=['POST'])
@radius_accounting_bp.route('/accounting/start', methods=['POST'])
@radius_accounting_bp.route('/accounting/stop', methods=['POST'])
@radius_accounting_bp.route('/accounting/interim', methods=['POST'])
def radius_accounting():
    """
    POST /api/radius/accounting

    Main RADIUS accounting endpoint called by FreeRADIUS rlm_rest.

    Dispatches based on 'acct_status_type' in the request body:
        1 = Start   → Create ActiveSession + RadiusAccounting record
        2 = Stop    → Close session, update data usage
        3 = Interim → Update byte counters and session time

    Request body (JSON, from FreeRADIUS rlm_rest):
        {
            "username": "AA:BB:CC:DD:EE:FF",
            "acct_status_type": "1",
            "acct_session_id": "8123456789",
            "acct_unique_id": "abc123def456",
            "nas_ip_address": "192.168.88.1",
            "framed_ip_address": "10.0.0.100",
            "calling_station_id": "AA:BB:CC:DD:EE:FF",
            "called_station_id": "CC:DD:EE:FF:AA:BB",
            "acct_input_octets": "0",
            "acct_output_octets": "0",
            "acct_session_time": "0",
            "acct_terminate_cause": ""
        }

    Response (always 200 OK):
        {"result": "ok"}

    Note: Always returns 200 to FreeRADIUS even on processing errors.
    This prevents FreeRADIUS from queuing retries for accounting packets.
    Errors are logged internally for monitoring and debugging.
    """
    try:
        # Parse request data
        data = request.get_json(silent=True) or request.form or {}

        if not data:
            logger.warning("RADIUS accounting: empty request body")
            return jsonify({'result': 'ok'}), 200

        # Process accounting
        handler = RadiusAccountingHandler()
        result = handler.process_accounting(data)

        # Log any issues but always return ok to FreeRADIUS
        if result.get('result') != 'ok':
            logger.warning(
                f"RADIUS accounting issue: {result.get('reason', 'unknown')}"
            )

        # Always return ok — FreeRADIUS should not retry accounting
        return jsonify({'result': 'ok'}), 200

    except Exception as e:
        logger.error(
            f"RADIUS accounting endpoint error: {e}", exc_info=True
        )
        # Always return ok to prevent FreeRADIUS retry storms
        return jsonify({'result': 'ok'}), 200