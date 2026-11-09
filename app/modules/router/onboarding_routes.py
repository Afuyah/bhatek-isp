"""Public provisioning endpoint.

A router that has just been added has no credentials yet, so the URL itself is
the credential. The payload is signed (and time-limited) and carries both the
router id and its revocable token:

    /tool fetch mode=https url="https://<org>.<domain>/provision/<payload>" \
        dst-path=bhatek.rsc;:delay 2s;/import bhatek.rsc

Security properties:
  * signed with SECRET_KEY — the payload cannot be forged or edited
  * time-limited (``PROVISION_LINK_TTL_SECONDS``, 24h by default)
  * bound to ``routers.onboarding_token``, so "New link" revokes the old one
  * single-use, with a short grace window so a failed import can be retried
  * never cached, never indexed, and every redemption is logged
"""

from flask import Blueprint, Response, abort, request

from app.core.logging.logger import logger
from app.modules.router.service import RouterService

onboarding_bp = Blueprint('onboarding', __name__)

_router_service = RouterService()

_NO_STORE = {
    'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
    'Pragma': 'no-cache',
    'X-Robots-Tag': 'noindex, nofollow',
}


def _serve(router, reason):
    if reason == 'not_found':
        logger.warning("Provision request rejected (unknown payload) from %s",
                       request.headers.get('X-Forwarded-For', request.remote_addr))
        abort(404)
    if reason in ('expired', 'used'):
        logger.warning("Provision request rejected (%s) for router %s from %s",
                       reason, router.id if router else '?',
                       request.headers.get('X-Forwarded-For', request.remote_addr))
        abort(410)

    script = _router_service.render_onboarding_script(router)
    logger.info("Provisioning script served for router %s (%s) to %s",
                router.id, router.name,
                request.headers.get('X-Forwarded-For', request.remote_addr))

    response = Response(script, mimetype='text/plain')
    response.headers.update(_NO_STORE)
    return response


@onboarding_bp.route('/provision/<payload>', methods=['GET'])
def provision(payload):
    """Serve the RouterOS script for a signed provisioning payload."""
    router, reason = _router_service.redeem_provision_payload(payload)
    return _serve(router, reason)


@onboarding_bp.route('/onboard/<token>.rsc', methods=['GET'])
def onboard_script(token):
    """Legacy capability URL, kept so links issued before the switch still work."""
    router, reason = _router_service.redeem_onboarding_token(token)
    return _serve(router, reason)
