"""Public onboarding endpoint.

A freshly created router has no credentials yet, so it cannot authenticate.
The URL itself is the credential: a 256-bit token, valid for
``ONBOARDING_TTL_HOURS`` and redeemable once (with a short re-fetch grace).

The MikroTik fetches the script and imports it:

    /tool fetch url="https://.../onboard/<token>.rsc" dst-path=bhatek-onboard.rsc; \
        /import file-name=bhatek-onboard.rsc

Security notes:
  * the token is single-use: a second fetch after the grace window returns 410
  * the token expires, and regenerating invalidates the previous one
  * the response is never cached and never indexed
  * every redemption is logged with the client address
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


@onboarding_bp.route('/onboard/<token>.rsc', methods=['GET'])
def onboard_script(token):
    """Return the RouterOS script for a valid onboarding token."""
    router, reason = _router_service.redeem_onboarding_token(token)

    if reason == 'not_found':
        logger.warning("Onboarding link rejected (unknown token) from %s",
                       request.headers.get('X-Forwarded-For', request.remote_addr))
        abort(404)

    if reason in ('expired', 'used'):
        logger.warning("Onboarding link rejected (%s) for router %s from %s",
                       reason, router.id if router else '?',
                       request.headers.get('X-Forwarded-For', request.remote_addr))
        abort(410)

    script = _router_service.render_onboarding_script(router)
    logger.info("Onboarding script served for router %s (%s) to %s",
                router.id, router.name,
                request.headers.get('X-Forwarded-For', request.remote_addr))

    response = Response(script, mimetype='text/plain')
    response.headers.update(_NO_STORE)
    return response
