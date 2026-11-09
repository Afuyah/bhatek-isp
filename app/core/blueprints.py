from app.modules.router.onboarding_routes import onboarding_bp
"""Blueprint registration.

Three groups, deliberately separated:

* **API** — ``/api/v1/*`` JSON surfaces.
* **Web** — server-rendered Jinja surfaces (``web`` carries no prefix because it
  owns ``/`` and the public pages; the rest are mounted per organization).
* **RADIUS** — endpoints FreeRADIUS calls via ``rlm_rest``. These are in the
  middleware exempt list; they are not user-facing.
"""


def register_api_blueprints(app):
    from app.modules.auth.routes import auth_bp
    from app.modules.organization.routes import org_bp
    from app.modules.router.routes import router_bp
    from app.modules.subscriber.routes import subscriber_bp
    from app.modules.billing.routes import billing_bp
    from app.modules.payment.routes import payment_bp
    from app.modules.session.routes import session_bp
    from app.modules.hotspot.routes import hotspot_bp
    from app.modules.reporting.routes import reports_bp

    app.register_blueprint(hotspot_bp)
    app.register_blueprint(auth_bp, url_prefix='/api/v1/auth')
    app.register_blueprint(org_bp, url_prefix='/api/v1/organizations')
    app.register_blueprint(router_bp, url_prefix='/api/v1/routers')
    # Public capability URL: /onboard/<token>.rsc (no auth, no prefix)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(subscriber_bp, url_prefix='/api/v1/subscribers')
    app.register_blueprint(billing_bp, url_prefix='/api/v1/billing')
    app.register_blueprint(payment_bp, url_prefix='/api/v1/payments')
    app.register_blueprint(session_bp, url_prefix='/api/v1/sessions')
    app.register_blueprint(reports_bp)


def register_web_blueprints(app):
    from app.modules.web import web_bp
    from app.modules.router.web_routes import router_web_bp
    from app.modules.billing.web_routes import billing_web_bp
    from app.modules.subscriber.web_routes import subscriber_web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(router_web_bp)
    app.register_blueprint(billing_web_bp)
    app.register_blueprint(subscriber_web_bp)


def register_radius_blueprints(app):
    from app.integrations.radius.radius_auth_handler import radius_auth_bp
    from app.integrations.radius.radius_accounting_routes import radius_accounting_bp

    app.register_blueprint(radius_auth_bp)
    app.register_blueprint(radius_accounting_bp)


def register_blueprints(app):
    """Register every blueprint on ``app``."""
    register_api_blueprints(app)
    register_web_blueprints(app)
    register_radius_blueprints(app)
    return app