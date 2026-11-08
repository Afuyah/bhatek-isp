# bhatek-isp — synthesis: what it is, what breaks, what remains

Companion to `00-VERIFIED.md` (facts I reproduced by running checks) and the 36
slice reports in `G01.md`–`G36.md` (whole-file reads of all 254 text files).
Where a slice report and `00-VERIFIED.md` disagree, `00-VERIFIED.md` wins.

---

## 1. What the product is

A **multi-tenant ISP management platform for Kenyan wireless ISPs**, written as a
single Flask monolith. One deployment serves many ISP businesses
("organizations"); every other entity is scoped by `organization_id`.

It covers the full commercial and technical loop of a WISP:

- **Sell** — plans, hotspot vouchers, subscriptions, invoices, M-Pesa STK push.
- **Provision** — MikroTik routers reached over RouterOS API / SSH, WireGuard
  tunnels from a VPS to each router, hotspot and PPPoE servers, FreeRADIUS NAS rows.
- **Authorise** — FreeRADIUS calls back into the app for every Access-Request
  (MAC / phone / PPPoE) and gets reply attributes derived from plan + subscription.
- **Account** — FreeRADIUS accounting feeds live sessions and data usage back into
  subscriptions.
- **Operate** — admin console (Jinja) for subscribers, routers, networks, APs,
  billing, reports; background Celery jobs for expiry, renewal, health, cleanup.

### Four runtime surfaces

| Surface | URL space | Count | Purpose |
|---|---|---|---|
| JSON API | `/api/v1/*` | 11 blueprints | SPA/AJAX clients, captive portal |
| Admin web UI | `/organization/<org_id>/*`, `/dashboard` | 6 blueprints | server-rendered operator console |
| FreeRADIUS REST | `/api/radius/*` | 2 blueprints | `rlm_rest` auth + accounting |
| Celery | broker queue | 12 tasks / 11 beat entries | expiry, renewal, health, reports |

### Layering convention (used consistently across modules)

```
routes.py          Flask blueprint: auth decorator, request parsing, JSON envelope
  controller.py    thin HTTP→domain translation, validation errors → status codes
    service.py     business rules, transactions, integration calls
      repository.py SQLAlchemy queries, always ANDed with organization_id
        models/*.py SQLAlchemy models (+ to_dict serialisers)
  schemas.py       marshmallow request/response schemas
  web_routes.py    Jinja variant of routes.py (session auth, render_template)
```

Not every module follows it fully: `hotspot` and `web` have no service layer;
`reporting` has no real repository/schemas (both unused); `auth` and `payment`
have orphaned `validators.py`.

### Domain model (32 tables)

```
Organization ── OrganizationUser ── User ── RefreshToken / AuditLog / EmailVerification
   │
   ├── Network ── Router ── HotspotServer / PPPoeServer / AccessPoint ── NAS
   │                └── WireGuard keys (encrypted) + radius_secret (plaintext)
   │
   ├── Subscriber ── Device
   │        └── Subscription ── Plan
   │                 ├── Invoice ── InvoiceItem
   │                 └── ActiveSession ── RadiusAccounting
   │
   ├── VoucherBatch ── Voucher        DiscountCoupon
   └── PaymentAccount ── Transaction ── Refund ── PaymentWebhookLog

FreeRADIUS tables (shared, tenant-tagged): radcheck / radreply / radusergroup / radacct
```

### Integrations

| Integration | Mechanism | State |
|---|---|---|
| MikroTik RouterOS | raw TCP 8728 / TLS 8729 wire protocol (hand-rolled, 535 lines) | works for modern login; legacy login broken; TLS verification disabled |
| WireGuard | SSH to a VPS, `wg` commands, generates RouterOS setup scripts | works, no tenant check, no shell quoting |
| M-Pesa Daraja | STK push, query, C2B, B2C, reversal over `requests` | STK path works; security credential generation wrong for B2C/reversal |
| FreeRADIUS | REST (`rlm_rest`) in + SQL tables out | core of the product; endpoints unauthenticated |
| Email | Brevo API **and** SMTP, implemented twice | duplicated implementations |
| SMS | Africa's Talking + Twilio behind a provider ABC | adapters never imported by the dispatcher |

---

## 2. Maturity assessment — "how finished is this?"

The codebase is **broad and deep but demo-grade**: the domain thinking is real
(1,315-line RADIUS auth handler, 1,240-line billing service, 879-line task
module), while the connective tissue — boot, schema, scheduler, templates,
security — is unfinished. Roughly 55,900 lines of text, of which ~42 Python
files (25%) contain no executable statement and 27 templates are empty.

| Area | State | Evidence |
|---|---|---|
| Domain model | **strong** | 32 tables, sensible tenancy, FreeRADIUS-compatible |
| Business logic | **substantial** | billing/subscriber/router/radius services are real implementations |
| JSON API | **broad** | 250 endpoints, all `url_for` targets resolve |
| Admin UI | **partial** | 69 non-empty templates; 27 empty; 10 render targets missing |
| Schema/migrations | **absent** | 1 revision, creates 1 unrelated table, claims to be base |
| Background jobs | **dead** | `app/tasks.py` shadowed by `app/tasks/` package → nothing registers |
| Booting | **unproven** | missing deps in this env; `ProductionConfig.validate()` can never pass |
| Security | **weak** | see P0 list: unauthenticated RADIUS, IDOR, CSRF absent, secrets in git |
| Tests | **none** | 4 placeholder files, no assertions, `conftest.py` is a comment |
| Ops | **skeletal** | Docker/compose/Fly present but inconsistent; no README, no CI |

---

## 3. Defect register

### P0 — must fix before the system can run or be exposed

| # | Defect | Where | Consequence |
|---|---|---|---|
| 1 | `app/tasks.py` is shadowed by the `app/tasks/` package | `app/tasks/`, `app/tasks.py` | no Celery task registers; `celery -A app.tasks.celery_app:celery` crashes; beat publishes unregistered tasks |
| 2 | Base Alembic revision creates nothing | `migrations/versions/f248ffbfc9be_.py` | `flask db upgrade` fails on empty DB; 31/32 tables unmigrated |
| 3 | Production config can never validate | `config/settings.py:ProductionConfig.validate` | reads `DATABASE_URL` attribute that does not exist → always raises |
| 4 | Ten `render_template()` targets missing; 8 distinct templates | hotspot/network/subscriber/web routes | `TemplateNotFound` 500s, including the 404/500 handlers themselves |
| 5 | RADIUS accounting endpoint is unauthenticated | `radius_accounting_routes.py` | anyone who can reach the URL can forge start/stop/interim for any username |
| 6 | RADIUS auth accepts client-supplied `organization_slug`, no shared secret | `radius_auth_handler.radius_authenticate` | unauthenticated auth oracle across tenants |
| 7 | Cross-tenant IDOR on payments | `payment/service.py:verify_payment_status`/`refund_transaction` | any tenant can activate or refund another org's transaction by UUID |
| 8 | Cross-tenant IDOR on hotspot payment polling | `hotspot/routes.py:check_payment_status` | leaks another tenant's receipt/status |
| 9 | Password hash leaks in API responses | `auth/routes.py:get_me`, `auth/controller.py:register` | `user.to_dict()` without `exclude={'password_hash'}` |
| 10 | Live credentials committed to a public repo | `alembic.ini:5` (Railway Postgres), `tests/org.py:20` (user login) | rotate both; they are in GitHub history |
| 11 | SSRF / credential probe on an unauthenticated endpoint | `router/routes.py:test_connection` (in `exempt_paths`) | arbitrary host/port/user/pass probe from the server |
| 12 | `org_admin` gets `permissions=['*']` | `auth/service.py:register_organization` | permission checks short-circuit for every org admin |

### P1 — feature paths that are broken or wrong

| # | Defect | Where |
|---|---|---|
| 13 | Voucher/M-Pesa code reusable: receipt matched by org only, never marked consumed | `radius_auth_handler._verify_mpesa_code` |
| 14 | Accounting stop/start queries omit `organization_id` | `radius_accounting_handler` |
| 15 | Data usage not idempotent; double-bills on retransmitted Stop | `_update_subscription_data_usage` |
| 16 | Raw-SQL RADIUS sync passes plain strings (removed in SQLAlchemy 2.0); failures swallowed, callers still get `True` | `radius_sync_service._sync_*_raw` |
| 17 | Refresh-token rotation is read-then-write with no lock → replayable | `auth/service.py:refresh_token` |
| 18 | Logout's session-scoped branch is unreachable (controller never forwards `session_id`) | `auth/service.py`, `auth/controller.py` |
| 19 | Login does not require a verified email | `auth/service.py:login` |
| 20 | `Plan.get_discounted_price` mixes `float` and `Decimal` → TypeError on any discounted plan | `models/billing.py` |
| 21 | `ActiveSession` relationships lack `back_populates` declared on the other side | `models/session.py` vs `subscriber.py` |
| 22 | `Transaction.mark_success` not idempotent; invoice/subscriber never settled | `models/payment.py` |
| 23 | Public plans endpoint requires a JWT → captive portal cannot list plans | `billing/routes.py:get_public_plans` |
| 24 | Session revocation writes keys nothing reads; blacklist fails open without Redis | `core/security/jwt.py` |
| 25 | `register_middlewares()` wraps the app not `app.wsgi_app` → RecursionError (currently dead code — do not wire it) | `core/middleware/__init__.py` |
| 26 | Rate-limit rules target `/api/...` but routes serve `/api/v1/...` | `core/middleware/rate_limit.py` |
| 27 | Celery beat entries use dicts, not `crontab` objects | `config/settings.py:CELERY_BEAT_SCHEDULE` |
| 28 | `BASE_URL`/SMTP config defined twice; second definition wins | `config/settings.py` |
| 29 | `redis_client.exists/ping` monkey-patch recurses into itself | `core/database/redis_client.py` |
| 30 | MikroTik health swallows exceptions → offline is never recorded | `integrations/mikrotik/client.py`, `router_health.py` |
| 31 | MikroTik/AP/network/subscriber web forms have no CSRF token | 6 template slices |
| 32 | Open redirects via `next_url` / `request.referrer` | `web/routes.py:login`, `subscriber/web_routes.py` |
| 33 | `WireGuardManager` runs SSH without tenant check or `shlex.quote` | `integrations/wireguard/manager.py` |
| 34 | Billing dashboard revenue/subscriber numbers are hardcoded fakes (`total_subs += 5`) | `billing/web_routes.py:563-573` |

### P2 — structural debt

- 46 zero-byte files + 23 comment-only Python files; 52 modules never imported
  (`app/utils/*`, all `validators.py`, both `permissions.py`, `stkp_push.py`,
  `sms/{twilio,africa_talking}.py`, the entire `app/tasks*` tree).
- Duplicated implementations: email (2×), admin bootstrap (3×), RADIUS reply
  builder (2×), Redis client probe (2×), middleware registration (2×),
  three parallel Jinja shells.
- `requirements.txt` unpinned and lists `cryptography` twice; `setup.py` cannot
  parse it; `make lint` needs tools that are not installed.
- No README, no CI, no test suite, no `conftest.py` fixtures.
- Datetimes use naive `utcnow()` throughout a product whose config declares
  `Africa/Nairobi`.

---

## 4. Completion roadmap

Order matters: nothing else can be validated until 1–3 are done.

**Phase 0 — make it installable**
1. Create a venv, `pip install -r requirements.txt` (add pins; drop the duplicate `cryptography`).
2. Fix `setup.py` to filter comments/blanks.
3. Add a README with the run commands, env var list, and FreeRADIUS client config.

**Phase 1 — make it boot and hold a schema**
4. Fix `ProductionConfig.validate()` to read `SQLALCHEMY_DATABASE_URI`.
5. Collapse `app/tasks/` into `app/tasks.py` (or move the tasks into the package)
   and give `app/tasks/celery_app.py` a real `celery` object.
6. Generate one real initial migration for all 32 tables; delete the fake base
   revision; verify `upgrade head` on an empty Postgres.
7. Unify the three config locations (`settings.py`, `config/*.py`, `config/__init__.py`).

**Phase 2 — close the functional gaps**
8. Create the 8 missing templates; rewrite the 3 hotspot calls to `web/hotspot/...`;
   fill the 27 empty templates or delete the routes that render them.
9. Fix `app/templates/web/hotspot/index.html` extends path.
10. Repair the Celery beat schedule entries and confirm one task runs end-to-end.
11. Wire the RADIUS sync raw-SQL fallback to `text()` and propagate failures.
12. Fix the `back_populates`/`float×Decimal` model bugs before any real billing.

**Phase 3 — security hardening** (do before exposing to the internet)
13. Authenticate `/api/radius/*` (shared secret/HMAC + NAS IP allowlist).
14. Tenant-scope every payment/accounting/hotspot lookup by `organization_id`.
15. Stop returning `password_hash`; add CSRFProtect to all web POSTs.
16. Fix the M-Pesa code-consumption logic (single-use, bound to phone).
17. Remove `permissions=['*']` for org admins; implement `ROLE_PERMISSIONS`.
18. Rotate and purge both leaked credentials; scrub git history.
19. Fix the open redirects and the unauthenticated router test endpoint.

**Phase 4 — prove it**
20. Real test suite: auth, billing, RADIUS accept/reject, accounting idempotency,
    tenant isolation (a cross-tenant test per module).
21. CI: lint + tests + `flask db upgrade` against a throwaway Postgres.
22. Ops: align compose port with gunicorn, add a beat service, add healthchecks,
    run migrations on release.

---

## 5. How this report was produced

- Source copied byte-for-byte to `C:\Users\Bhatek Space\Documents\deepseek-harness\bhatek-isp`
  (299 files, 12.22 MB, verified by name+size).
- Partitioned all 254 text files into 36 disjoint slices; one subagent per slice
  read every file in full (no skimming, no filename inference; 0-byte files
  recorded as stubs) and reported per-file role, flows, dependencies, defects, stubs.
- Coverage: 36/36 slices, 254/254 files reported.
- In parallel, 13 cross-cutting checks were executed against the tree: AST parse
  of all 169 Python files, import-graph resolution, route/endpoint inventory,
  `url_for` cross-check, `render_template` target check, template `extends`
  resolution, model-vs-migration table diff, unused-module detection,
  zero-byte/statement-free classification, dependency availability, secret scan,
  package-vs-module shadowing test, and one boot attempt.
