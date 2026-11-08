# bhatek-isp â€” complete codebase audit

**Subject:** `C:\Users\Bhatek Space\Documents\deepseek-harness\bhatek-isp`
(copied byte-for-byte from `C:\Users\Bhatek Space\Desktop\newseting\bhatek-isp`)
**Date:** 2026-11-06
**Scale:** 299 files / 12.22 MB / 271 tracked / 169 Python files / ~55,900 lines of text
**Coverage:** 254/254 text files read in full by 36 reader agents + 13 executable cross-cutting checks

Read in this order:

1. **Verified findings** â€” reproduced by command; treat as ground truth.
2. **Synthesis** â€” what the product is, maturity, ranked defect register, roadmap.
3. **Slice reports** â€” per-file detail for every file, grouped by module.

---

# Part 1 â€” Verified structural findings

## bhatek-isp â€” verified structural findings

These facts were produced by direct inspection/execution against
`C:\Users\Bhatek Space\Documents\deepseek-harness\bhatek-isp` on 2026-11-06,
independently of the per-slice reader agents. Anything the slice reports add
on top of this; where a slice contradicts this file, this file wins.

### 1. Copy integrity

| Check | Result |
|---|---|
| Source | `C:\Users\Bhatek Space\Desktop\newseting\bhatek-isp` |
| Target | `C:\Users\Bhatek Space\Documents\deepseek-harness\bhatek-isp` |
| Files copied | 299 (including `.git`) |
| Directories | 81 |
| Size | 12.22 MB (source == target) |
| Name+size comparison | identical for all 299 files |
| Target before copy | existed, empty |

### 2. Repository

- Branch `master`, 101 commits, 2026-04-29 â†’ 2026-08-02.
- `origin` = https://github.com/Afuyah/bhatek-isp (public).
- 271 tracked files. No README anywhere. No CI config, no pre-commit, no linter config.
- Authors: `afuyah` (71), `unknown` (16), `Afuyah` (7), `railway-app[bot]` (5), `Fly.io` (1).
- Recent history is dominated by "fix: ... invalid url_for() calls" commits â€” evidence the web layer has been patched reactively.

### 3. Shape of the codebase

| Metric | Value |
|---|---|
| Python files | 169 (incl. migrations/tests/scripts) |
| Syntax errors (`ast.parse`) | **0** |
| Text files read by the 36 readers | 254 |
| Lines of text (excl. binaries) | ~55,900 |
| Flask blueprints | 19 |
| Route decorators | 254 (250 distinct endpoints) |
| `url_for(...)` calls in templates | 190 |
| Jinja templates | 96 files, of which **27 are 0 bytes** |
| SQLAlchemy tables declared | **32** |
| Alembic revisions | **1** (creates `radpostauth` only) |
| Celery tasks defined in `app/tasks.py` | 12 |
| Celery beat entries in settings | 11 |
| Tests | 4 placeholder files, 0 assertions, `conftest.py` is a one-line comment |

### 4. Verified defects (reproduced by command, not inferred)

#### 4.1 `app/tasks.py` is dead code â€” shadowed by an empty package
Both `app/tasks.py` (879 lines, all 12 real Celery tasks) and the package
directory `app/tasks/` exist. Python resolves the **package** first, so
`import app.tasks` loads `app/tasks/__init__.py` (0 bytes).

Consequences:
- `create_app()` sets `celery.conf.imports = ('app.tasks',)` â†’ imports an empty package â†’ **no task is ever registered**.
- All 11 `CELERY_BEAT_SCHEDULE` entries in `settings.py` target `app.tasks.<task>` â†’ beat fires, workers answer `Received unregistered task`.
- `Makefile` and `docker-compose.yml` run `celery -A app.tasks.celery_app:celery`, but `app/tasks/celery_app.py` contains only the comment `# Celery app` â†’ **worker crashes on start**.
- `app/integrations/mpesa/stk_push.py:177` does `from app.tasks.payment import query_payment_status` â†’ `ModuleNotFoundError` (no `app/tasks/payment.py`).

#### 4.2 Ten `render_template()` paths point at files that do not exist
Rendering any of these raises `TemplateNotFound` (HTTP 500):

| Template requested | Caller |
|---|---|
| `hotspot/portal.html` | `app/modules/hotspot/routes.py:91` |
| `hotspot/error.html` | `app/modules/hotspot/routes.py:56,60,79` |
| `web/network/stats.html` | `app/modules/network/web_routes.py:357` |
| `web/subscriber/hotspot.html` | `app/modules/subscriber/web_routes.py:156` |
| `web/register_success.html` | `app/modules/web/routes.py:402` |
| `web/404.html` | `app/modules/web/routes.py:612` |
| `web/500.html` | `app/modules/web/routes.py:619` |
| `error.html` | `app/modules/web/routes.py:426` |

The hotspot templates exist but live under `web/hotspot/`; the calls omit the
`web/` prefix. The 404/500 handlers themselves point at missing templates, so
error handling raises a second error.

#### 4.3 Three routes render templates that are 0 bytes
`web/billing/invoices/show.html`, `web/billing/subscriptions/show.html`,
`web/network/edit.html` â€” the page renders blank (layout-less empty response).

#### 4.4 One broken template inheritance
`app/templates/web/hotspot/index.html` starts with `{% extends "base.html" %}`,
but the file is `web/base.html`; template lookup is rooted at `templates/`, so
`base.html` does not resolve â†’ `TemplateNotFound` for the hotspot landing page.

#### 4.5 Three broken internal imports
| Location | Import | Why it fails |
|---|---|---|
| `app/integrations/mpesa/stk_push.py:177` | `from app.tasks.payment import query_payment_status` | module does not exist |
| `app/modules/auth/validators.py:3` | `from app.core.exceptions import ValidationError` | `core/exceptions/__init__.py` is empty; class lives in `.handlers` |
| `manage.py:28` | `from scripts.seed_data import seed_all` | `scripts/seed_data.py` is a one-line comment |

All three are in modules that nothing imports, so they are latent, not live.

#### 4.6 Template `url_for()` targets â€” clean
All 190 `url_for('endpoint')` calls in templates resolve to a registered
endpoint (verified against the 250 collected endpoints). The url_for breakage
that recent commits chased is currently fixed.

#### 4.7 The schema cannot be rebuilt from migrations
32 tables are declared across `app/models/*`; the single revision
`f248ffbfc9be_` has `down_revision = None` (i.e. it claims to be the base) yet
only does `op.drop_table('radpostauth')` plus a `batch_alter_table('routers')`.
`flask db upgrade` on an empty database fails at the first statement, and 31 of
32 app tables have no migration at all. Schema creation in practice depends on
`db.create_all()`-style bootstrapping that does not exist in the repo either
(`scripts/init_db.sql` is a 22-byte comment).

#### 4.8 Live credential committed to a public repo
`alembic.ini:5`:
`sqlalchemy.url = postgresql://postgres:<32-char password>@interchange.proxy.rlwy.net:39029/railway`

- Present in the working tree **and** in git history (`git log -S` finds it introduced in `95c3ef4 first commit`).
- Pushed to `github.com/Afuyah/bhatek-isp`.
- This is a Railway-hosted Postgres. **Rotate it.** Removing it from HEAD does not remove it from history.

#### 4.9 Placeholder files that look real
- **46 zero-byte files**: 19 `.py` (`__init__.py` for most packages, plus `app/modules/session/schemas.py`) and **27 templates** (all of `web/auth/*`, all of `web/dashboard/*` component/layout partials, `web/billing/invoices/show.html`, `web/billing/subscriptions/show.html`, `web/network/edit.html`, `web/organization/{create,edit,show}.html`, `web/subscriber/create.html` + both components, `web/vouchers/*`, `web/router/components/connection_status.html`).
- **23 more `.py` files contain only a comment**, no code: `app/tasks/{__init__,celery_app,cleanup,expiry,notifications,sync}.py`, `scripts/{backup,seed_data}.py`, `tests/conftest.py`, `app/utils/*`, `app/core/logging/formatters.py`, `app/core/config/{development,production,testing}.py`.
- Net: **42 of 169 Python files (25%) contain no executable statement.**
- `app/templates/web/vouchers/struc` is a leftover mojibake directory-tree planning note.

#### 4.10 Dead modules (no importer anywhere)
52 app modules are never imported. The meaningful ones:
`app/utils/{helpers,decorators,validators}.py`, all four `validators.py`
(`auth`, `subscriber`, `router`, `payment`), `auth/permissions.py`,
`organization/permissions.py`, `core/extensions.py`,
`core/logging/formatters.py`, `reporting/{repository,schemas}.py`,
`session/schemas.py`, `integrations/mpesa/stk_push.py`,
`integrations/sms/{africa_talking,twilio}.py`, and the whole `app/tasks*` tree.

#### 4.11 `register_middlewares()` is dead code that would break the app
`app/core/middleware/__init__.py` defines `register_middlewares(app)` which
wraps the **Flask app object** rather than `app.wsgi_app`, producing
`RecursionError` on every request. Nothing calls it â€” the real stack is built
inline in `app/__init__.py:264-272` (RequestID â†’ Tenant â†’ Auth over
`app.wsgi_app`), which is correct. Treat the middleware package's
`register_middlewares`/`get_middleware_stack` as poison: do not wire it up.

#### 4.12 The app cannot boot in this environment (dependency gap, not a code bug)
`python -c "from app import create_app; create_app()"` fails immediately:

```
app/core/logging/logger.py:5: ModuleNotFoundError: No module named 'pythonjsonlogger'
```

Seven third-party imports are not installed here â€” `bcrypt`, `cryptography`,
`flask_redis`, `paramiko`, `pythonjsonlogger`, `requests`, `twilio`. All seven
*are* declared in `requirements.txt`, so this is a missing-venv problem, not a
repo defect. `pip install -r requirements.txt` was blocked by this session's
sandbox; it must be done in a real venv before any runtime testing.

#### 4.13 Dependency manifest is not installable as written
- `requirements.txt` is entirely unpinned (no `==` anywhere) and lists
  `cryptography` twice (lines 30 and 53).
- `setup.py` does `install_requires=[line.strip() for line in open("requirements.txt")]`
  â€” that keeps `# Core`, `# Database` and blank lines, so `pip install .`
  raises `InvalidRequirement`.
- `make lint` calls `flake8`/`black`/`isort`, none of which are in requirements.

### 5. What the product is

A multi-tenant ISP management platform for Kenyan wireless ISPs, built as a
Flask monolith with four surfaces:

1. **JSON API** (`/api/v1/*`) â€” 11 blueprints: auth, organizations, networks,
   routers, access-points, subscribers, billing, payments, sessions, hotspot,
   reports.
2. **Server-rendered web UI** (`/â€¦`) â€” 6 web blueprints serving Jinja pages for
   the same entities, organiser as an admin console.
3. **FreeRADIUS REST integration** (`/api/radius/*`) â€” `rlm_rest` calls back
   into the app for `authenticate`/`disconnect` and accounting; this is how
   MikroTik hotspots and PPPoE sessions actually get authorised.
4. **Celery workers** â€” plan expiry, auto-renewal, router health, RADIUS cache
   cleanup, session cleanup, billing reports.

Domain model: `Organization` â†’ `Network` â†’ `Router` (MikroTik) â†’
`HotspotServer`/`PPPoeServer`/`AccessPoint`; `Subscriber` â†’ `Device`,
`Subscription` â†’ `Plan`/`Invoice`/`Voucher`; `Transaction`/`Refund` for M-Pesa;
`ActiveSession` + FreeRADIUS `radcheck/radreply/radusergroup/radacct` for
live auth and accounting.

Billing is M-Pesa-first (STK push + callback), with vouchers as the prepaid
path and Brevo/SMTP email + Africa's Talking/Twilio SMS as notification
channels.

---

# Part 2 â€” Synthesis

## bhatek-isp â€” synthesis: what it is, what breaks, what remains

Companion to `00-VERIFIED.md` (facts I reproduced by running checks) and the 36
slice reports in `G01.md`â€“`G36.md` (whole-file reads of all 254 text files).
Where a slice report and `00-VERIFIED.md` disagree, `00-VERIFIED.md` wins.

---

### 1. What the product is

A **multi-tenant ISP management platform for Kenyan wireless ISPs**, written as a
single Flask monolith. One deployment serves many ISP businesses
("organizations"); every other entity is scoped by `organization_id`.

It covers the full commercial and technical loop of a WISP:

- **Sell** â€” plans, hotspot vouchers, subscriptions, invoices, M-Pesa STK push.
- **Provision** â€” MikroTik routers reached over RouterOS API / SSH, WireGuard
  tunnels from a VPS to each router, hotspot and PPPoE servers, FreeRADIUS NAS rows.
- **Authorise** â€” FreeRADIUS calls back into the app for every Access-Request
  (MAC / phone / PPPoE) and gets reply attributes derived from plan + subscription.
- **Account** â€” FreeRADIUS accounting feeds live sessions and data usage back into
  subscriptions.
- **Operate** â€” admin console (Jinja) for subscribers, routers, networks, APs,
  billing, reports; background Celery jobs for expiry, renewal, health, cleanup.

#### Four runtime surfaces

| Surface | URL space | Count | Purpose |
|---|---|---|---|
| JSON API | `/api/v1/*` | 11 blueprints | SPA/AJAX clients, captive portal |
| Admin web UI | `/organization/<org_id>/*`, `/dashboard` | 6 blueprints | server-rendered operator console |
| FreeRADIUS REST | `/api/radius/*` | 2 blueprints | `rlm_rest` auth + accounting |
| Celery | broker queue | 12 tasks / 11 beat entries | expiry, renewal, health, reports |

#### Layering convention (used consistently across modules)

```
routes.py          Flask blueprint: auth decorator, request parsing, JSON envelope
  controller.py    thin HTTPâ†’domain translation, validation errors â†’ status codes
    service.py     business rules, transactions, integration calls
      repository.py SQLAlchemy queries, always ANDed with organization_id
        models/*.py SQLAlchemy models (+ to_dict serialisers)
  schemas.py       marshmallow request/response schemas
  web_routes.py    Jinja variant of routes.py (session auth, render_template)
```

Not every module follows it fully: `hotspot` and `web` have no service layer;
`reporting` has no real repository/schemas (both unused); `auth` and `payment`
have orphaned `validators.py`.

#### Domain model (32 tables)

```
Organization â”€â”€ OrganizationUser â”€â”€ User â”€â”€ RefreshToken / AuditLog / EmailVerification
   â”‚
   â”œâ”€â”€ Network â”€â”€ Router â”€â”€ HotspotServer / PPPoeServer / AccessPoint â”€â”€ NAS
   â”‚                â””â”€â”€ WireGuard keys (encrypted) + radius_secret (plaintext)
   â”‚
   â”œâ”€â”€ Subscriber â”€â”€ Device
   â”‚        â””â”€â”€ Subscription â”€â”€ Plan
   â”‚                 â”œâ”€â”€ Invoice â”€â”€ InvoiceItem
   â”‚                 â””â”€â”€ ActiveSession â”€â”€ RadiusAccounting
   â”‚
   â”œâ”€â”€ VoucherBatch â”€â”€ Voucher        DiscountCoupon
   â””â”€â”€ PaymentAccount â”€â”€ Transaction â”€â”€ Refund â”€â”€ PaymentWebhookLog

FreeRADIUS tables (shared, tenant-tagged): radcheck / radreply / radusergroup / radacct
```

#### Integrations

| Integration | Mechanism | State |
|---|---|---|
| MikroTik RouterOS | raw TCP 8728 / TLS 8729 wire protocol (hand-rolled, 535 lines) | works for modern login; legacy login broken; TLS verification disabled |
| WireGuard | SSH to a VPS, `wg` commands, generates RouterOS setup scripts | works, no tenant check, no shell quoting |
| M-Pesa Daraja | STK push, query, C2B, B2C, reversal over `requests` | STK path works; security credential generation wrong for B2C/reversal |
| FreeRADIUS | REST (`rlm_rest`) in + SQL tables out | core of the product; endpoints unauthenticated |
| Email | Brevo API **and** SMTP, implemented twice | duplicated implementations |
| SMS | Africa's Talking + Twilio behind a provider ABC | adapters never imported by the dispatcher |

---

### 2. Maturity assessment â€” "how finished is this?"

The codebase is **broad and deep but demo-grade**: the domain thinking is real
(1,315-line RADIUS auth handler, 1,240-line billing service, 879-line task
module), while the connective tissue â€” boot, schema, scheduler, templates,
security â€” is unfinished. Roughly 55,900 lines of text, of which ~42 Python
files (25%) contain no executable statement and 27 templates are empty.

| Area | State | Evidence |
|---|---|---|
| Domain model | **strong** | 32 tables, sensible tenancy, FreeRADIUS-compatible |
| Business logic | **substantial** | billing/subscriber/router/radius services are real implementations |
| JSON API | **broad** | 250 endpoints, all `url_for` targets resolve |
| Admin UI | **partial** | 69 non-empty templates; 27 empty; 10 render targets missing |
| Schema/migrations | **absent** | 1 revision, creates 1 unrelated table, claims to be base |
| Background jobs | **dead** | `app/tasks.py` shadowed by `app/tasks/` package â†’ nothing registers |
| Booting | **unproven** | missing deps in this env; `ProductionConfig.validate()` can never pass |
| Security | **weak** | see P0 list: unauthenticated RADIUS, IDOR, CSRF absent, secrets in git |
| Tests | **none** | 4 placeholder files, no assertions, `conftest.py` is a comment |
| Ops | **skeletal** | Docker/compose/Fly present but inconsistent; no README, no CI |

---

### 3. Defect register

#### P0 â€” must fix before the system can run or be exposed

| # | Defect | Where | Consequence |
|---|---|---|---|
| 1 | `app/tasks.py` is shadowed by the `app/tasks/` package | `app/tasks/`, `app/tasks.py` | no Celery task registers; `celery -A app.tasks.celery_app:celery` crashes; beat publishes unregistered tasks |
| 2 | Base Alembic revision creates nothing | `migrations/versions/f248ffbfc9be_.py` | `flask db upgrade` fails on empty DB; 31/32 tables unmigrated |
| 3 | Production config can never validate | `config/settings.py:ProductionConfig.validate` | reads `DATABASE_URL` attribute that does not exist â†’ always raises |
| 4 | Ten `render_template()` targets missing; 8 distinct templates | hotspot/network/subscriber/web routes | `TemplateNotFound` 500s, including the 404/500 handlers themselves |
| 5 | RADIUS accounting endpoint is unauthenticated | `radius_accounting_routes.py` | anyone who can reach the URL can forge start/stop/interim for any username |
| 6 | RADIUS auth accepts client-supplied `organization_slug`, no shared secret | `radius_auth_handler.radius_authenticate` | unauthenticated auth oracle across tenants |
| 7 | Cross-tenant IDOR on payments | `payment/service.py:verify_payment_status`/`refund_transaction` | any tenant can activate or refund another org's transaction by UUID |
| 8 | Cross-tenant IDOR on hotspot payment polling | `hotspot/routes.py:check_payment_status` | leaks another tenant's receipt/status |
| 9 | Password hash leaks in API responses | `auth/routes.py:get_me`, `auth/controller.py:register` | `user.to_dict()` without `exclude={'password_hash'}` |
| 10 | Live credentials committed to a public repo | `alembic.ini:5` (Railway Postgres), `tests/org.py:20` (user login) | rotate both; they are in GitHub history |
| 11 | SSRF / credential probe on an unauthenticated endpoint | `router/routes.py:test_connection` (in `exempt_paths`) | arbitrary host/port/user/pass probe from the server |
| 12 | `org_admin` gets `permissions=['*']` | `auth/service.py:register_organization` | permission checks short-circuit for every org admin |

#### P1 â€” feature paths that are broken or wrong

| # | Defect | Where |
|---|---|---|
| 13 | Voucher/M-Pesa code reusable: receipt matched by org only, never marked consumed | `radius_auth_handler._verify_mpesa_code` |
| 14 | Accounting stop/start queries omit `organization_id` | `radius_accounting_handler` |
| 15 | Data usage not idempotent; double-bills on retransmitted Stop | `_update_subscription_data_usage` |
| 16 | Raw-SQL RADIUS sync passes plain strings (removed in SQLAlchemy 2.0); failures swallowed, callers still get `True` | `radius_sync_service._sync_*_raw` |
| 17 | Refresh-token rotation is read-then-write with no lock â†’ replayable | `auth/service.py:refresh_token` |
| 18 | Logout's session-scoped branch is unreachable (controller never forwards `session_id`) | `auth/service.py`, `auth/controller.py` |
| 19 | Login does not require a verified email | `auth/service.py:login` |
| 20 | `Plan.get_discounted_price` mixes `float` and `Decimal` â†’ TypeError on any discounted plan | `models/billing.py` |
| 21 | `ActiveSession` relationships lack `back_populates` declared on the other side | `models/session.py` vs `subscriber.py` |
| 22 | `Transaction.mark_success` not idempotent; invoice/subscriber never settled | `models/payment.py` |
| 23 | Public plans endpoint requires a JWT â†’ captive portal cannot list plans | `billing/routes.py:get_public_plans` |
| 24 | Session revocation writes keys nothing reads; blacklist fails open without Redis | `core/security/jwt.py` |
| 25 | `register_middlewares()` wraps the app not `app.wsgi_app` â†’ RecursionError (currently dead code â€” do not wire it) | `core/middleware/__init__.py` |
| 26 | Rate-limit rules target `/api/...` but routes serve `/api/v1/...` | `core/middleware/rate_limit.py` |
| 27 | Celery beat entries use dicts, not `crontab` objects | `config/settings.py:CELERY_BEAT_SCHEDULE` |
| 28 | `BASE_URL`/SMTP config defined twice; second definition wins | `config/settings.py` |
| 29 | `redis_client.exists/ping` monkey-patch recurses into itself | `core/database/redis_client.py` |
| 30 | MikroTik health swallows exceptions â†’ offline is never recorded | `integrations/mikrotik/client.py`, `router_health.py` |
| 31 | MikroTik/AP/network/subscriber web forms have no CSRF token | 6 template slices |
| 32 | Open redirects via `next_url` / `request.referrer` | `web/routes.py:login`, `subscriber/web_routes.py` |
| 33 | `WireGuardManager` runs SSH without tenant check or `shlex.quote` | `integrations/wireguard/manager.py` |
| 34 | Billing dashboard revenue/subscriber numbers are hardcoded fakes (`total_subs += 5`) | `billing/web_routes.py:563-573` |

#### P2 â€” structural debt

- 46 zero-byte files + 23 comment-only Python files; 52 modules never imported
  (`app/utils/*`, all `validators.py`, both `permissions.py`, `stkp_push.py`,
  `sms/{twilio,africa_talking}.py`, the entire `app/tasks*` tree).
- Duplicated implementations: email (2Ã—), admin bootstrap (3Ã—), RADIUS reply
  builder (2Ã—), Redis client probe (2Ã—), middleware registration (2Ã—),
  three parallel Jinja shells.
- `requirements.txt` unpinned and lists `cryptography` twice; `setup.py` cannot
  parse it; `make lint` needs tools that are not installed.
- No README, no CI, no test suite, no `conftest.py` fixtures.
- Datetimes use naive `utcnow()` throughout a product whose config declares
  `Africa/Nairobi`.

---

### 4. Completion roadmap

Order matters: nothing else can be validated until 1â€“3 are done.

**Phase 0 â€” make it installable**
1. Create a venv, `pip install -r requirements.txt` (add pins; drop the duplicate `cryptography`).
2. Fix `setup.py` to filter comments/blanks.
3. Add a README with the run commands, env var list, and FreeRADIUS client config.

**Phase 1 â€” make it boot and hold a schema**
4. Fix `ProductionConfig.validate()` to read `SQLALCHEMY_DATABASE_URI`.
5. Collapse `app/tasks/` into `app/tasks.py` (or move the tasks into the package)
   and give `app/tasks/celery_app.py` a real `celery` object.
6. Generate one real initial migration for all 32 tables; delete the fake base
   revision; verify `upgrade head` on an empty Postgres.
7. Unify the three config locations (`settings.py`, `config/*.py`, `config/__init__.py`).

**Phase 2 â€” close the functional gaps**
8. Create the 8 missing templates; rewrite the 3 hotspot calls to `web/hotspot/...`;
   fill the 27 empty templates or delete the routes that render them.
9. Fix `app/templates/web/hotspot/index.html` extends path.
10. Repair the Celery beat schedule entries and confirm one task runs end-to-end.
11. Wire the RADIUS sync raw-SQL fallback to `text()` and propagate failures.
12. Fix the `back_populates`/`floatÃ—Decimal` model bugs before any real billing.

**Phase 3 â€” security hardening** (do before exposing to the internet)
13. Authenticate `/api/radius/*` (shared secret/HMAC + NAS IP allowlist).
14. Tenant-scope every payment/accounting/hotspot lookup by `organization_id`.
15. Stop returning `password_hash`; add CSRFProtect to all web POSTs.
16. Fix the M-Pesa code-consumption logic (single-use, bound to phone).
17. Remove `permissions=['*']` for org admins; implement `ROLE_PERMISSIONS`.
18. Rotate and purge both leaked credentials; scrub git history.
19. Fix the open redirects and the unauthenticated router test endpoint.

**Phase 4 â€” prove it**
20. Real test suite: auth, billing, RADIUS accept/reject, accounting idempotency,
    tenant isolation (a cross-tenant test per module).
21. CI: lint + tests + `flask db upgrade` against a throwaway Postgres.
22. Ops: align compose port with gunicorn, add a beat service, add healthchecks,
    run migrations on release.

---

### 5. How this report was produced

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

---

# Part 3 â€” Per-slice reports (all 254 files)

| # | Slice |
|---|---|
| 01 | G01 â€” build/config/migrations root |
| 02 | G02 â€” core config + database layer |
| 03 | G03 â€” core security / decorators / exceptions / logging |
| 04 | G04 â€” core middleware stack |
| 05 | G05 â€” Identity, organization, router, network & RADIUS models |
| 06 | G06 â€” models: billing/payment/session/subscriber |
| 07 | G07 â€” integration: mikrotik + router health |
| 08 | G08 â€” integration: radius auth handler + dictionary |
| 09 | G09 â€” RADIUS accounting integration |
| 10 | G10 â€” integration: radius cache + sync service |
| 11 | G11 â€” integration: mpesa |
| 12 | G12 â€” Integration: SMS / Email / WireGuard |
| 13 | G13 â€” auth module |
| 14 | G14 â€” Organization (tenant) module |
| 15 | G15 â€” router module (controller / schemas / validators) |
| 16 | G16 â€” Router Repository & Service |
| 17 | G17 â€” Router blueprints |
| 18 | G18 â€” subscriber (controller/schemas/validators) |
| 19 | G19 â€” subscriber (repository/service) |
| 20 | G20 â€” Subscriber module blueprints |
| 21 | G21 â€” Billing (services/schemas) |
| 22 | G22 â€” billing (controller/repository) |
| 23 | G23 â€” Billing Blueprints |
| 24 | G24 â€” payment module |
| 25 | G25 â€” Session & RADIUS Accounting |
| 26 | G26 â€” Reporting and analytics module |
| 27 | G27 â€” network module |
| 28 | G28 â€” access_point module |
| 29 | G29 â€” hotspot + web shell |
| 30 | G30 â€” tasks + utils |
| 31 | G31 â€” templates: shell/auth/org/dashboard |
| 32 | G32 â€” templates: subscriber |
| 33 | G33 â€” templates: billing |
| 34 | G34 â€” templates: router |
| 35 | G35 â€” templates: hotspot / access_point / network |
| 36 | G36 â€” static css + tests + scripts |

---

## G01 â€” build/config/migrations root

### Summary
Outer shell of the ISP platform: app entrypoints (`wsgi.py`, `manage.py`), an admin-bootstrap script (`tm.py`), packaging and deploy files (`setup.py`, `Dockerfile`, `docker-compose.yml`, `fly.toml`, `Makefile`), and Alembic schema versioning. It decides how Flask is served, how the first super admin is created, and how Postgres is migrated. Schema tracking is effectively absent â€” the base revision contains no CREATEs â€” and the deploy targets disagree on ports.

### Files
- `wsgi.py` â€” 6 â€” WSGI entrypoint â€” `app = create_app()` for gunicorn `wsgi:app`; `__main__` calls bare `app.run()`.
- `manage.py` â€” 33 â€” Flask CLI â€” `create_admin`/`seed_data`; super-admin password read via echoed `input()`.
- `tm.py` â€” 243 â€” admin bootstrap script â€” writes `User`/`Organization`/`OrganizationUser` directly, bypassing AuthService.
- `setup.py` â€” 13 â€” packaging â€” `install_requires` from raw stripped `requirements.txt` lines.
- `Makefile` â€” 47 â€” dev task runner â€” dev/db/lint/docker/celery targets; never exports FLASK_APP.
- `Dockerfile` â€” 15 â€” image build â€” gunicorn on 0.0.0.0:8080, root user, no healthcheck.
- `docker-compose.yml` â€” 49 â€” local orchestration â€” web/db/redis/celery; publishes 5000:5000.
- `fly.toml` â€” 23 â€” Fly.io config â€” `[[vm]]` declares both `memory='1gb'` and `memory_mb=256`.
- `alembic.ini` â€” 45 â€” Alembic config â€” `script_location=migrations` plus live Railway password.
- `requirements.txt` â€” 72 â€” dependency manifest â€” fully unpinned; `cryptography` listed twice.
- `migrations/env.py` â€” 113 â€” Alembic env â€” Flask-Migrate template; needs app context, overwrites `sqlalchemy.url`.
- `migrations/versions/f248ffbfc9be_.py` â€” 49 â€” base revision â€” drops `radpostauth`, alters `routers`; no CREATEs.
- `migrations/script.py.mako` â€” 24 â€” revision template â€” standard `pass` bodies.
- Trivial: `.gitignore` (62), `migrations/alembic.ini` (50, logging only, no `script_location`), `migrations/README` (1).

### Defects (ranked, max 6)
1. `migrations/versions/f248ffbfc9be_.py:upgrade` â€” base revision (`down_revision=None`) only drops `radpostauth` and `batch_alter_table('routers')`; on an empty DB `flask db upgrade` fails with UndefinedTable â€” no schema is reproducible.
2. `alembic.ini:sqlalchemy.url` â€” live Railway Postgres credentials with plaintext password committed to VCS.
3. `Dockerfile:CMD` vs `docker-compose.yml:web.ports` â€” gunicorn binds 8080 but compose publishes 5000:5000, so the web container is unreachable from the host.
4. `tm.py:create_super_admin` / `create_super_admin_with_organization` â€” duplicate check `filter_by(email=email)` runs before the email prompt, so interactive runs match `email IS NULL` and can create duplicate admins.
5. `setup.py:install_requires` â€” blank and `#`-comment lines from requirements.txt become invalid requirement strings; `pip install .` raises InvalidRequirement.
6. `Makefile:db-init`/`db-migrate`/`dev` â€” no FLASK_APP export, so `flask db`/`flask run` abort; `db-init` also re-runs `flask db init` over the existing `migrations/` tree.

### Stubs
- `migrations/versions/f248ffbfc9be_.py` â€” message "empty message", unadjusted autogenerate.
- `migrations/README` â€” one boilerplate line.
- `migrations/alembic.ini` â€” `[alembic]` section entirely commented out.
- `fly.toml` â€” empty `[build]`, no `[env]`, worker, or `release_command`.
- No 0-byte files in this slice.

---

## G02 â€” core config + database layer

### Summary
G02 is the bootstrap substrate: extension wiring, environment config, and the SQLAlchemy/Redis plumbing other slices import. `settings.py` holds all real config (JWT, DB pool, five Redis DBs, CORS/CSP, SMTP/Brevo, Celery beat, M-Pesa, SMS, MikroTik); `extensions.py` registers only Redis and JWTService; `base.py`/`mixins.py` define the shared model contract. The empty `__init__.py` files and one-line per-environment modules leave production boot, tenant isolation and Celery beat broken.

### Files
`app/core/__init__.py` â€” 0 â€” empty stub â€” no package re-exports (`extensions`, `get_config`, `db` unreachable from `app.core`).
`app/core/extensions.py` â€” 56 â€” bootstrap/extension registry â€” singleton `ExtensionManager` registers `app.extensions['redis']` and `['jwt_service']`; Celery/cache/ratelimiter only comments.
`app/core/config/__init__.py` â€” 0 â€” empty stub â€” nothing re-exports `Config`/`config`/`get_config`.
`app/core/config/{development,production,testing}.py` â€” 1 each â€” empty stubs â€” single comment lines; real classes are in `settings.py`.
`app/core/config/settings.py` â€” 542 â€” configuration module â€” `Config` + Development/Production/Testing/Staging, 11-entry `CELERY_BEAT_SCHEDULE`, `REDIS_DBS`, `get_redis_url_for_db`, `get_config`; duplicate `BASE_URL`/SMTP blocks.
`app/core/database/__init__.py` â€” 0 â€” empty stub â€” `db`/`BaseModel`/mixins never surfaced.
`app/core/database/base.py` â€” 54 â€” declarative base â€” abstract `BaseModel`: postgresql `UUID` pk default `uuid.uuid4`, timezone `created_at`/`updated_at`, `to_dict(exclude, include_relationships)`.
`app/core/database/mixins.py` â€” 51 â€” mixins â€” `OrganizationMixin.organization_id` FK `organizations.id` CASCADE; `TenantMixin` is bare `pass`; timestamp/soft-delete/status mixins.
`app/core/database/redis_client.py` â€” 168 â€” Redis wrapper â€” static `RedisClient` helpers swallowing exceptions via `print()`; `get_raw_client()`; self-recursive `exists`/`ping` monkey-patch.
`app/core/database/session.py` â€” 18 â€” DB session factory â€” `db = SQLAlchemy()`; `session_scope()` commits/rollbacks then closes.

### Defects (ranked)
1. `settings.py:ProductionConfig.validate` â€” required key `DATABASE_URL` is looked up as a class attribute that does not exist (only `SQLALCHEMY_DATABASE_URI` does), so production boot always raises `ValueError`, regardless of env.
2. `settings.py:Config.get_redis_url_for_db` â€” `'/db' in base_url` never matches `redis://host:6379/0`, so it appends a second index (`redis://localhost:6379/0/3`) â†’ every per-concern Redis connection is malformed.
3. `settings.py:Config.CELERY_BEAT_SCHEDULE` â€” six entries use plain dicts `{'type':'crontab'}` instead of `crontab()` objects; beat cannot build `ScheduleEntry`, so plan expiry, router health, RADIUS cleanup and billing reports never run.
4. `extensions.py:ExtensionManager.init_app` â€” probes `redis_client.client`, which `FlaskRedis` lacks (`_client`/`connection`, and `RedisClient.get_raw_client()` already does this correctly), so `app.extensions['redis']` is always `None` â†’ JWT revocation degrades to per-worker in-memory blacklist.
5. `mixins.py:TenantMixin` â€” body is `pass` despite the docstring calling it an alias of `OrganizationMixin`; models declared with it get no `organization_id`, silently disabling tenant isolation.
6. `settings.py:Config` â€” `BASE_URL` is reassigned at line 190 to `os.environ.get('BASE_URL')`, shadowing the line-17 default, so `MPESA_CALLBACK_BASE_URL` resolves to `None/api/mpesa/callback` when unset; SMTP/FROM_* are likewise declared twice and the Brevo relay default is overwritten by `smtp.gmail.com`.

### Stubs
- Three empty `__init__.py` files (core, config, database) â€” no re-exports.
- `development.py`, `production.py`, `testing.py` â€” one comment line each; duplicated by `settings.py` classes.
- `mixins.py:TenantMixin` â€” `pass`.
- `extensions.py` â€” "Future extensions" comment; Celery/cache/rate limiter never initialised.
- `redis_client.py:166-168` â€” no-op `setex` `pass` branch.

---

## G03 â€” core security / decorators / exceptions / logging

### Summary
Cross-cutting trust layer: JWTService issues/validates HS256 access and refresh tokens and backs the API decorators (token_required, optional_token, permission_required, role_required, organization_required) that populate flask.g. EncryptionService wraps Fernet with version-prefixed ciphertext for secrets at rest, web_auth supplies session decorators for Jinja pages, and exceptions/logging define JSON error handlers and a JSON log formatter. Auth routes, API views, Celery tasks and the app factory all depend on it, but revocation is Redis-dependent and fails open, and four package inits are empty.

### Files
`app/core/security/__init__.py` â€” 0 lines â€” empty stub â€” no re-exports; only submodule imports work.
`app/core/security/encryption.py` â€” 213 lines â€” crypto service â€” Fernet `v1:...` tokens from ENCRYPTION_KEY, ENCRYPTION_PREVIOUS_KEYS rotation map, dict field helpers, module singleton.
`app/core/security/jwt.py` â€” 453 lines â€” JWT service + decorators â€” access/refresh encode, jti blacklist, token_version revocation, device fingerprint; five auth decorators.
`app/core/decorators/__init__.py` â€” 0 lines â€” empty stub â€” web_auth not re-exported.
`app/core/decorators/web_auth.py` â€” 72 lines â€” session web auth â€” User.query.get into g.current_user, super-admin/org checks, 302 to web.login/web.dashboard.
`app/core/exceptions/__init__.py` â€” 0 lines â€” empty stub â€” error classes not re-exported.
`app/core/exceptions/handlers.py` â€” 99 lines â€” error taxonomy + handlers â€” five Exception subclasses with code/status_code, JSON handlers for them, HTTPException and generic Exception.
`app/core/logging/__init__.py` â€” 0 lines â€” empty stub â€” `from app.core.logging import logger` yields the submodule.
`app/core/logging/formatters.py` â€” 1 line â€” empty stub â€” only `# Formatters`; real formatter is in logger.py.
`app/core/logging/logger.py` â€” 63 lines â€” JSON logging â€” CustomJsonFormatter stamps timestamp plus g.request_id/user_id/organization_id/ip; setup_logging clears root handlers, stdout only.

### Defects (ranked, max 6)
`jwt.py:JWTService._increment_user_token_version` â€” Redis INCR on a missing key returns 1, equal to the fallback in `_get_user_token_version`, so first revoke-all leaves every token valid.
`jwt.py:JWTService._blacklist_token / _is_token_blacklisted` â€” no Redis means silent no-op and False (also fail-open on exceptions); revoke_token and refresh rotation give no replay protection.
`jwt.py:JWTService.init_app` â€” never sets `app.extensions['jwt_service']`, yet token_required reads it â†’ RuntimeError/500 on every bearer route unless registered elsewhere.
`jwt.py:organization_required` â€” compares token-derived `g.organization_id` to itself instead of a requested org, so no tenant isolation; `_revoke_session` writes `user_session:*` that nothing reads.
`jwt.py:JWTService._get_device_fingerprint` â€” `if not request` raises off-request, so token generation fails in Celery/CLI/tests; refresh secret defaults to the access secret.
`logging/logger.py:setup_logging` â€” ends with `app.config['LOG_LEVEL']` after a `.get`, KeyError at startup when unset; wipes all root handlers, and `g.request_id` is never set so correlation is empty.

### Stubs
security/__init__.py, decorators/__init__.py, exceptions/__init__.py, logging/__init__.py (0 bytes each); logging/formatters.py (comment only).

---

## G04 â€” core middleware stack

### Summary
G04 owns the per-request pipeline: request-ID correlation, security headers, CORS, JWT auth, Redis rate limiting, and an unwired tenant resolver, assembled by `register_middlewares()`. It is meant to guard every Flask route, but the stack is non-functional: layers wrap the Flask app object instead of `app.wsgi_app`, so earlier layers are discarded and `AuthMiddleware` re-enters `Flask.__call__` until RecursionError. Because `JWT_SECRET_KEY` is never loaded and rate-limit rules use `/api/...` while routes serve `/api/v1/...`, the platform gets neither auth nor meaningful throttling.

### Files
- `app/core/middleware/__init__.py` â€” 40 lines â€” registration/assembly â€” builds the broken chain; `get_middleware_stack()` debug helper loops on an always-true condition.
- `app/core/middleware/auth.py` â€” 386 lines â€” WSGI JWT gate â€” hardcoded HS256/audience/issuer, no config secret; injects environ identity keys.
- `app/core/middleware/rate_limit.py` â€” 181 lines â€” before_request limiter â€” Redis Lua INCR/EXPIRE, default 100/h, configured rules never match.
- `app/core/middleware/request_id.py` â€” 62 lines â€” correlation IDs â€” adopts client headers, stores `environ['REQUEST_ID']`, appends `X-Request-ID`.
- `app/core/middleware/security.py` â€” 186 lines â€” headers + CORS â€” HSTS/CSP set; every OPTIONS gets wildcard 204; both classes self-install in `__init__`.
- `app/core/middleware/tenant.py` â€” 33 lines â€” tenant resolver (unwired) â€” trusts client `X-Tenant-ID`; never registered.

### Defects (ranked, max 6)
1. `__init__.py:register_middlewares` â€” wraps the Flask app, not `app.wsgi_app`; final `AuthMiddleware` calls `Flask.__call__`, which calls itself â†’ RecursionError 500 on every request; RequestID/SecurityHeaders/CORS never execute.
2. `auth.py:_validate_token` â€” `secret_key` stays None (no `app.config['JWT_SECRET_KEY']` fallback), so all protected routes return 401; algorithm/audience/issuer hardcoded.
3. `auth.py:AuthMiddleware.__call__` â€” Bearer-only gate: browser session pages like `/organization/<id>/routers` get JSON 401 instead of a `/login` redirect.
4. `security.py:CORSMiddleware._handle_preflight` â€” 204 for every OPTIONS with `Allow-Origin: *` + credentials true (spec-invalid) and no allow-list; `_is_origin_allowed` wildcard reduces `https://*.example.com` to prefix `https://` â†’ any origin.
5. `rate_limit.py:RateLimiter.rate_limits` â€” keys `/api/auth/login` etc. never match `/api/v1/...`; default 100/h applies to everything, fail-open on Redis errors; `g.user_id` never set, so all keys are IP-based.
6. `tenant.py:TenantMiddleware` â€” unregistered dead code; if wired, an unauthenticated `X-Tenant-ID` header overwrites `ORGANIZATION_ID`, enabling cross-tenant access.

### Stubs
- `rate_limit.py:rate_limit` decorator â€” writes `g.custom_rate_limit`, never read; no-op.
- `_get_rate_limit_config` "no limit" branch unreachable.
- `security.py:_is_sensitive_path` â€” `/api/auth` paths don't exist; no-store never applied.
- `request_id.py` â€” no logging filter consumes `REQUEST_ID`; no length cap.
- `tenant.py` â€” entire isolation middleware unwired.

---

## G05 â€” Identity, organization, router, network & RADIUS models

### Summary
G05 is the persistence core: the `app/models` barrel plus SQLAlchemy models for tenancy (Organization, OrganizationUser), identity (User, RefreshToken, AuditLog, EmailVerification), the physical hierarchy (Network -> Router -> HotspotServer/PPPoeServer -> AccessPoint, plus NAS) and the FreeRADIUS tables (radcheck, radreply, radusergroup, radacct). All models are PostgreSQL-specific (`UUID`, `INET`, `MACADDR`), imperative `Column(...)` style with `to_dict` serializers consumed by the Flask JSON APIs. Auth, router provisioning and RADIUS accounting read/write these tables directly; no raw SQL appears in the slice.

### Files
- `app/models/__init__.py` â€” 61 â€” package barrel â€” imports every model module and exports via `__all__`, but omits OrganizationSetting.
- `app/models/auth.py` â€” 187 â€” identity models â€” User with bcrypt set_password/check_password, RefreshToken.is_valid, AuditLog with FK-less subscriber_id.
- `app/models/organization.py` â€” 108 â€” tenancy root â€” Organization with dynamic users/routers/plans relationships; OrganizationUser unique (org, user); OrganizationSetting unexported.
- `app/models/router.py` â€” 144 â€” MikroTik devices â€” Router stores API/SSH creds, WireGuard keys, plaintext radius_secret, nas_entry_id; HotspotServer and PPPoeServer children.
- `app/models/nas.py` â€” 82 â€” FreeRADIUS NAS client â€” nas table (nasname, secret, type, router_id) with seven indexes; deliberately no Router relationship.
- `app/models/network.py` â€” 53 â€” network grouping â€” groups routers per org by type; to_dict returns router_count plus hardcoded ap_count/active_sessions.
- `app/models/access_point.py` â€” 74 â€” wireless AP â€” unique MACADDR, required router_id/ssid/location; to_dict omits channel, frequency, encryption_type, settings.
- `app/models/verification.py` â€” 26 â€” email token â€” token_urlsafe(32) with 24h expiry; no user FK, purpose or tenant scope.
- `app/models/radius.py` â€” 88 â€” FreeRADIUS tables â€” RadCheck/RadReply/RadUserGroup/RadAcct with organization_id from mixin; RadAcct uses Integer radacctid PK.

### Defects (ranked, max 6)
1. `app/models/network.py:Network.slug` â€” `unique=True` on the column plus `unique(organization_id, slug)`; global uniqueness forbids two tenants sharing "main". Same global-unique flaw on `auth.py:User.email`.
2. `app/models/auth.py:User` â€” membership stored twice (direct organization_id FK and OrganizationUser join); `get_current_organization()` ignores organization_id and returns None when no is_primary row.
3. `app/models/radius.py:RadAcct` â€” Integer `radacctid` primary key alongside BaseModel's UUID id; 32-bit octet counters overflow past 2 GiB; `nas_ip_address` String(15) blocks IPv6.
4. `app/models/radius.py:RadCheck` â€” organization_id is written but FreeRADIUS selects by username alone, so tenants collide; no `unique(username, attribute)` on radcheck/radreply.
5. `app/models/router.py:Router` â€” plaintext, unique, indexed `radius_secret` exposed by `to_dict(include_sensitive=True)`; `routers.nas_entry_id` <-> `nas.router_id` is a circular FK.
6. `app/models/network.py:Network.to_dict` / `access_point.py:AccessPoint.to_dict` â€” ap_count/active_sessions hardcoded 0; AP serializer drops persisted columns, and `str(organization_id)` emits 'None' unguarded (also NAS.to_dict, Router.to_dict).

### Stubs
- `Network.to_dict`: ap_count and active_sessions hardcoded 0 ("will be updated when AP module is ready").
- `OrganizationSetting`: defined but never imported or exported.
- `NAS`: no Router relationship by design (bare FK only).
- `verification.py`: only EmailVerification; no SMS/reset token models.
- No 0-byte files in this slice â€” all nine paths hold full implementations.

---

## G06 â€” models: billing/payment/session/subscriber

### Summary
G06 is the commercial/identity core: four SQLAlchemy modules declaring fifteen tables across billing, payments, live sessions/accounting and subscriber identity. `billing.py` holds plans, subscriptions, invoices, vouchers and coupons; `payment.py` the M-Pesa transaction/refund/webhook records; `session.py` ActiveSession plus FreeRADIUS accounting; `subscriber.py` hotspot/PPPoE identity and the device registry. It supplies schema, validity/pricing helpers and `to_dict` serialisers only â€” paymentâ†’invoiceâ†’subscriptionâ†’sessionâ†’usage orchestration sits in services outside this slice.

### Files
- `app/models/billing.py` â€” 676 lines â€” billing/quota models â€” Plan/Subscription/Invoice/InvoiceItem/Voucher/VoucherBatch/DiscountCoupon with validity math; `get_discounted_price` is floatÃ—Decimal and raises.
- `app/models/payment.py` â€” 211 lines â€” payment models â€” PaymentAccount/Transaction/Refund/PaymentWebhookLog; `mark_success` is not idempotent and never settles Invoice/Subscriber.
- `app/models/session.py` â€” 120 lines â€” session/accounting models â€” ActiveSession + RadiusAccounting; ActiveSession relationships omit `back_populates` that Subscriber/Subscription declare.
- `app/models/subscriber.py` â€” 148 lines â€” subscriber identity models â€” Subscriber + Device; `username` is globally unique despite the per-org index.

### Defects (ranked)
1. `billing.py:Plan.get_discounted_price` â€” `float(price) * Decimal(discount/100)` raises TypeError for any discount > 0 loaded from DB; `Plan.to_dict` calls it unconditionally â†’ 500 on discounted plans.
2. `session.py:ActiveSession.subscriber/subscription` â€” omit `back_populates` that `Subscriber.active_sessions`/`Subscription.active_sessions` declare â†’ configure_mappers failure or unsynchronised collections.
3. `payment.py:Transaction.mark_success` â€” not idempotent and `mpesa_receipt`/`checkout_request_id` are not unique â†’ duplicate callbacks double-credit; Invoice and Subscriber never settled.
4. `billing.py:Invoice/Voucher/Plan` â€” `invoice_number`, `code`, `transaction_reference` globally unique rather than per `organization_id` â†’ cross-tenant collisions; `or` chains ignore explicit 0 overrides.
5. `billing.py:Voucher.created_at` â€” re-declares `created_at` alongside TimestampMixin; Transaction/PaymentWebhookLog hand-roll timestamps â†’ inconsistent audit columns.
6. `payment.py:PaymentWebhookLog` â€” raw M-Pesa payload/headers stored unencrypted and unbounded, no signature column, `organization_id` required from a context callbacks lack.

Also noted: `InvoiceItem` lacks a tenant column; `Invoice.is_overdue` ignores 'overdue' status; `RadiusAccounting.organization_id` has no FK; Subscription has no `data_limit` snapshot; naive `datetime.utcnow()` vs Africa/Nairobi; duplicate indexes; unused imports (session.py `Boolean`/`JSON`, subscriber.py `JSON`/`Integer`).

### Stubs
- No literal TODO/pass markers; all gaps are missing behaviour.
- Voucher/VoucherBatch: no generate/redeem/`usage_count` increment.
- Invoice: no `mark_paid`/recalculate; `paid_at`, `tax_rate`, billing periods untouched.
- DiscountCoupon `used_count` never incremented; `minimum_purchase`/`applicable_plan_ids` unenforced.
- PaymentAccount `last_test_at`/`test_status` never written; PaymentWebhookLog `processed` has no helper.
- ActiveSession: no terminate/usage update; RadiusAccounting never rolls up into `Subscription.total_data_used_mb`; Subscriber `total_spent`/`last_active_at` never maintained.

---

## G07 â€” integration: mikrotik + router health

### Summary
This slice is the MikroTik integration layer: a RouterOS wire-protocol client (TCP 8728/TLS 8729, variable-length words, modern + legacy login), a high-level `MikroTikClient` for hotspot/PPPoE/RADIUS/walled-garden/queue operations, dataclass DTOs, and `router_health` helpers that classify connectivity and persist router status. It sits between external Celery provisioning/health tasks and live routers, and is the only path by which subscriber create/suspend and router health reach RouterOS. No routes, ORM models, or Celery tasks are defined here, and two core paths (offline detection, legacy login) are broken.

### Files
`app/integrations/__init__.py` â€” 0 lines â€” empty package root â€” no exports or shared client factory.
`app/integrations/router_health.py` â€” 234 lines â€” health-check service â€” `check_router_connection` maps `health_check` to online/offline/error and persists via `db.session.commit`.
`app/integrations/mikrotik/__init__.py` â€” 53 lines â€” export barrel â€” re-exports 6 client symbols + 8 dataclasses (eager Flask/EncryptionService import).
`app/integrations/mikrotik/client.py` â€” 535 lines â€” RouterOS API client â€” connection cache/retry/decrypt plus full hotspot/PPPoE/RADIUS/queue surface.
`app/integrations/mikrotik/models.py` â€” 432 lines â€” DTO dataclasses â€” RouterOS key mapping via `from_mikrotik`; inverted memory metric.

### Defects (ranked, max 6)
`router_health.py:check_router_connection` â€” `MikroTikClient.health_check` swallows every exception into `{'status':'unhealthy'}`, so the except branch that returns `status='offline'` for timeout/refused is unreachable; all outages are recorded as `'error'`.
`mikrotik/client.py:MikroTikConnection._read_sentence` â€” stops reading on `!done`, dropping the trailing `=ret=<challenge>` of legacy `/login`; `_login` then reports success without authenticating and the socket desynchronises (`_login_challenge` effectively dead).
`mikrotik/client.py:MikroTikConnection.connect` â€” TLS uses `verify_mode=CERT_NONE`/`check_hostname=False`; `router_health._router_to_dict` hardcodes `api_ssl: False`, so credentials go in cleartext over 8728.
`router_health.py:_get_admin_email` â€” global `ADMIN_EMAIL` env var overrides all tenants; fallback query `filter_by(organization_id, is_active=True).first()` has no admin/role filter and no ordering, so any active user can receive router alerts.
`mikrotik/client.py:MikroTikClient.get_connection/_cleanup_oldest` â€” cache keyed only on `rid:host:port`, 100-connection cap and oldest-10 eviction are global, so one tenant can evict another tenant's live sessions.
`mikrotik/models.py:RouterHealth.from_resource` â€” `memory_used` is populated from `free-memory`, so `memory_usage_percent` reports free/total instead of utilisation.

### Stubs
- `app/integrations/__init__.py` empty (0 bytes).
- `_login_challenge` unreachable; legacy auth path unusable.
- No Celery task/beat registration despite the "called from Celery tasks" docstring.
- `MikroTikRouter`, `InterfaceStats`, `HotspotProfile`, `RouterHealth` exported but unused by the client (dicts used instead).
- No threshold alerting, dedup, or consecutive-failure tracking â€” only a status string.

---

## G08 â€” integration: radius auth handler + dictionary

### Summary
The RADIUS integration package: a package facade, a MikroTik attribute codec, and the FreeRADIUS REST auth endpoint. `RadiusAuthHandler` resolves the tenant from the NAS IP, dispatches MAC / phone (M-Pesa) / PPPoE authentication, and returns flat rlm_rest reply attributes from plan + subscription data. It is the access-control gate for every hotspot and PPPoE session on the platform, backed by Redis caching and the radcheck/radreply SQL fallback.

### Files
`app/integrations/radius/__init__.py` â€” 56 lines â€” package facade â€” eagerly re-exports dictionary, cache, sync, auth and accounting symbols for the app factory; any submodule importing names back from the package risks a circular import.
`app/integrations/radius/dictionary.py` â€” 327 lines â€” RADIUS attribute codec â€” `MikroTikDictionary` (vendor 14988) formats/parses rate-limit strings and builds reply attributes from plan dicts or Subscription ORM objects; burst constants 41â€“43 are dead.
`app/integrations/radius/radius_auth_handler.py` â€” 1315 lines â€” REST auth service + blueprint â€” `radius_auth_bp` at `/api/radius` with `/authenticate` and `/disconnect`; three auth modes, org-scoped Redis keys, `_build_accept`/`_reject`; endpoints trust caller-supplied identity and tenant.

### Defects (ranked, max 6)
`radius_auth_handler._verify_mpesa_code` â€” the receipt is matched only by `mpesa_receipt` + `organization_id`; its phone/subscriber is never compared to the request phone and never marked consumed, so any valid code in the tenant authenticates any phone, repeatedly.
`radius_auth_handler.radius_authenticate` â€” accepts a client-supplied `organization_slug`, bypassing the documented NASâ†’Routerâ†’Organization boundary, and has no shared-secret/HMAC check; the endpoint becomes an unauthenticated auth oracle with no explicit Auth-Type Accept/Reject signal (both outcomes are HTTP 200).
`radius_auth_handler._find_recent_payment` â€” matches `payment_details.contains(phone)` (substring on JSON) and then creates a subscription for the request phone, so another customer's payment can grant access; it reads `payment_details` while creation reads `custom_data`.
`radius_auth_handler.authenticate` â€” accepted results are cached under the raw `username`, but `_auth_mac` reads `effective_mac.upper()`, so the accept cache is write-only for MAC auth; reject keys are unnormalized, so hammering protection is bypassed by reformatting the identity.
`radius_auth_handler._build_accept` â€” duplicates `MikroTikDictionary.get_attributes_from_subscription` divergently: no `Framed-Pool`/`Mikrotik-Group` (PPPoE gets no pool), no burst, hardcoded `paid-users`, always emits `Simultaneous-Use`; device limits are counted two incompatible ways (`>` on sessions vs `>=` on devices).
`dictionary.format_rate_limit` â€” burst threshold/time are appended without the burst pair, so `10M/5M 15M/15M` and `10M/5M 30s/30s` occupy burst slots and produce structurally invalid rate-limit strings.

### Stubs
- No zero-byte files; no TODO/FIXME; only `pass` is the swallow at `dictionary.py:157`.
- `radius_disconnect` clears cache only â€” no CoA/Disconnect-Request to the NAS despite "force disconnect" docstring.
- Documented but absent: cross-subscriber MAC reassignment; unknown-MAC auto-registration in `_auth_mac`.

---

## G09 â€” RADIUS accounting integration

### Summary
This slice ingests FreeRADIUS accounting packets: a Flask blueprint answers `rlm_rest` POSTs on `/api/radius/accounting` and delegates to `RadiusAccountingHandler`, which resolves the tenant from the NAS IP and records session start, interim counters and stop. It links each session to a `Subscriber`/`Subscription` and accumulates bytes into `Subscription.total_data_used_mb`, so it is the feed between FreeRADIUS/MikroTik session reality and billing/data-cap state. It sits between the FreeRADIUS integration and the subscriber, billing and session models, and its correctness determines whether live usage and quota consumption are trustworthy.

### Files
`C:/Users/Bhatek Space/Documents/deepseek-harness/bhatek-isp/app/integrations/radius/radius_accounting_handler.py` â€” 693 lines â€” service layer â€” `RadiusAccountingHandler.process_accounting` dispatches status 1/2/3 to `_process_start`/`_process_stop`/`_process_interim`, resolving org via `Router.ip_address` and deduping through `RadiusCache`.
`C:/Users/Bhatek Space/Documents/deepseek-harness/bhatek-isp/app/integrations/radius/radius_accounting_routes.py` â€” 96 lines â€” Flask blueprint â€” `radius_accounting_bp` stacks `/accounting`, `/accounting/start`, `/accounting/stop`, `/accounting/interim` on one unauthenticated view that always returns HTTP 200 `{"result":"ok"}`.

### Defects (ranked, max 6)
1. `radius_accounting_routes.py:radius_accounting` â€” no authentication, shared secret or IP allowlist; any client reaching the URL can forge Start/Stop/Interim for arbitrary usernames, and combined with defect 2 can terminate another tenant's session.
2. `radius_accounting_handler.py:_process_stop` (also `_process_start`, `_process_interim`) â€” the `ActiveSession`/`RadiusAccounting` queries `filter_by(session_id, username, ...)` omit `organization_id`, contradicting the documented tenant isolation; a cross-tenant packet overwrites or closes foreign session rows.
3. `radius_accounting_handler.py:_update_subscription_data_usage` â€” cumulative octets are added to `total_data_used_mb` with no idempotency guard (dedup is skipped when `acct_unique_id` is empty) and a non-atomic read-modify-write, so retransmitted Stops double-bill and concurrent stops lose updates.
4. `radius_accounting_handler.py:_process_stop` â€” the helper commits mid-flow (line ~631) and `_process_stop` commits again; partial usage persists when a later step fails, and the helper's bare `except` only logs, silently dropping usage.
5. `radius_accounting_routes.py:radius_accounting` â€” every failure, empty body and handler `{'result':'fail'}` collapses to HTTP 200 ok, so unresolvable-NAS packets vanish with no retry, dead-letter or metric.
6. `radius_accounting_handler.py:_process_start`/`_process_interim` â€” session cache uses the bare `session_id` key despite the "cache keys include organization_id" claim, and unchecked `int()` on `acct_status_type`, octets and terminate cause aborts the whole packet on non-numeric input.

### Stubs
- Accounting-On/Off (4/5) are acknowledge-only: logged, no `Router` state update, no stale-session sweep.
- `_create_accounting_record` writes subscriber-less start rows that no Stop can ever close.
- No data-cap or expiry enforcement, and interim updates never propagate to `Subscription`.
- No tests, migration or FreeRADIUS client config proving the endpoint contract.

---

## G10 â€” integration: radius cache + sync service

### Summary
RadiusCache wraps redis_client with org-scoped auth/reject/session/NAS/subscriber keys, accounting dedup, a fixed-window limiter, and SCAN-based org purge, serving the FreeRADIUS hot path. RadiusSyncService projects subscribers, subscriptions and plans into radcheck, radreply and radusergroup, with hotspot/PPPoE dispatch, bulk resync, removal and verification. Together they are the caching and persistence halves of ISP authentication, but they never reference each other, so DB writes leave cached auth decisions stale.

### Files
`app/integrations/radius/radius_cache.py` â€” 893 lines â€” Redis cache service â€” classmethod-only RadiusCache over redis_client; tenant scoping is opt-in per call (alias RedisCache).
`app/integrations/radius/radius_sync_service.py` â€” 659 lines â€” service layer â€” RadiusSyncService writes FreeRADIUS tables via ORM plus a raw-SQL fallback that is incompatible with SQLAlchemy 2.0.

### Defects (ranked, max 6)
- `radius_sync_service.py:_sync_radcheck_raw` (and `_sync_radreply_raw`, `_sync_radusergroup_raw`, all `_remove_*`) â€” `db.session.execute("DELETE ...", {...})` passes a plain string without `text()`, removed in SQLAlchemy 2.0; every raw fallback and raw removal fails at runtime.
- `radius_sync_service.py:_sync_radcheck_raw` â€” catches Exception, rolls back and returns None while callers still return True, so subscriber sync reports success having written zero rows.
- `radius_cache.py:update_rate_limit` â€” claims CoA but only rewrites cached session dicts; no radreply update and no CoA/Disconnect packet, so plan changes never reach live sessions.
- `radius_cache.py:_generate_key` with `cache_session`/`update_rate_limit` â€” falsy identifiers are dropped, so a missing organization_id yields the shared unscoped key family and missing `session_id` collapses writes onto `radius:session`, breaking tenant isolation and invalidation.
- `radius_sync_service.py:_get_radius_attributes` â€” `plan_obj = plan or subscription.plan` can be None (AttributeError), and `subscription.bandwidth_up_mbps or plan.bandwidth_up_mbps` discards explicit zero overrides.
- `radius_sync_service.py:_sync_radcheck`/`_sync_radreply`/`_sync_radusergroup` â€” each table commits independently with no enclosing transaction and only ImportError is caught, so partial writes persist without rollback.

### Stubs
- No Celery task or scheduler hook invoking sync; call sites are outside this slice.
- `count_active_device_sessions` returns 0 on cache miss, awaiting a compute-and-set caller that does not exist.
- `BillingService` constructed in `RadiusSyncService.__init__` but never used.
- No CoA/Disconnect sender anywhere.

---

## G11 â€” integration: mpesa

### Summary
Safaricom Daraja adapter: an encrypted-credential HTTP client (STK push, STK query, B2C, reversal, C2B register/simulate), a stateless webhook normaliser for STK/C2B/B2C payloads with Redis dedupe, and a Redis-backed STK-push state machine. It holds no routes, models or DB writes; a payments route and PaymentService outside this slice are expected to call it to credit subscribers and activate plans. Everything is per-organisation, keyed on a decrypted `payment_account` dict.

### Files
`app/integrations/mpesa/__init__.py` â€” 0 lines â€” EMPTY_STUB â€” no re-exports, so every consumer must import deep module paths.
`app/integrations/mpesa/callbacks.py` â€” 322 lines â€” webhook callback handler â€” `MpesaCallbackHandler` normalises four payload shapes and dedupes on `mpesa:callback:{id}` (86400s TTL); `process_c2b_validation` accepts everything.
`app/integrations/mpesa/client.py` â€” 473 lines â€” HTTP API client â€” `MpesaClient` decrypts keys via `EncryptionService`, caches tokens at `mpesa:token:{org}`, has no retries and a placeholder `_generate_security_credential`.
`app/integrations/mpesa/stk_push.py` â€” 331 lines â€” STK push service â€” `STKPushService` tracks `stk_push:{uuid}` (3600s TTL) and dispatches `query_payment_status.delay`, but never stores a checkout-idâ†’uuid index.

### Defects (ranked, max 6)
1. `client.py:_generate_security_credential` â€” returns base64(consumer_secret) with unused cryptography imports; every B2C and reversal call is rejected in production.
2. `stk_push.py:initiate_payment` â€” the callback carries only `CheckoutRequestID`, but no reverse mapping to `transaction_id` is written, so callbacks can never locate the Redis state.
3. `callbacks.py:_store_callback` â€” dedupe key written *before* the caller applies payment; a downstream crash permanently suppresses Safaricom's retry and the money is lost.
4. `callbacks.py:process_stk_callback` â€” `result_code == 0 and callback_metadata` sends successful-but-metadata-less callbacks to the "failed" branch; the int-vs-string `'0'` comparison also misreads string codes.
5. `callbacks.py:process_c2b_validation` â€” unconditional `ResultCode 0`, no `BillRefNumber` lookup; unknown paybill references are accepted by Safaricom and silently dropped.
6. `client.py:__init__` â€” `token_cache_key` omits shortcode/environment, and no check binds `payment_account` to `organization_id`; sibling accounts share bearer tokens.

### Stubs
- `__init__.py`: empty, no exports.
- `_generate_security_credential`: base64 placeholder awaiting Safaricom certificate.
- `b2b_url`: defined, no B2B method.
- `process_c2b_validation`: "Accept all transactions by default".
- `transaction_state['attempts']`: never incremented.
- No DB model or row for M-Pesa transactions anywhere in the slice.

---

## G12 â€” Integration: SMS / Email / WireGuard

### Summary
G12 is the outbound-notification and VPN-provisioning layer; it owns no routes, models or persistence. SMS (Africa's Talking form-POST, Twilio SDK) sits behind the `SMSProvider` ABC and `SMSService` with Redis rate limits, retries and tracking, while Brevo email exists twice â€” a Flask-context-bound `BrevoEmailService` facade and a standalone Celery copy in `email_service.py`. `WireGuardManager` SSHes to the VPS to allocate per-organization `/24` addresses, manage peers and emit RouterOS setup scripts that join MikroTik routers to RADIUS; callers are the M-Pesa, voucher, expiry, billing and router-onboarding slices.

### Files
`app/integrations/sms/__init__.py` â€” 0 lines â€” empty stub â€” no package exports; should re-export provider/service like `wireguard/__init__.py`.
`app/integrations/sms/africa_talking.py` â€” 133 lines â€” SMS provider adapter â€” `AfricaTalkingProvider.send` posts to `/version1/messaging`; `_format_phone` hardcodes Kenya `254`.
`app/integrations/sms/provider.py` â€” 205 lines â€” SMS abstraction + service â€” `SMSService` registry is never populated, so the first send raises `ValueError` after Redis counters increment.
`app/integrations/sms/twilio.py` â€” 91 lines â€” SMS provider adapter â€” lazy `twilio.rest.Client`; no E.164 normalization; bulk uses 10 threads bypassing rate limits.
`app/integrations/email/brevo_service.py` â€” 276 lines â€” email service â€” reads `current_app.config` in `__init__`; REST or SMTP relay, mock returns `True`; `BASE_URL` KeyError.
`app/integrations/email/service.py` â€” 58 lines â€” email facade â€” delegates to Brevo, builds payment-confirmation HTML locally; unusable outside app context.
`app/integrations/email_service.py` â€” 432 lines â€” Celery email helper â€” duplicate Brevo API/SMTP sender with env fallback; expiry, router-alert and billing templates.
`app/integrations/wireguard/__init__.py` â€” 3 lines â€” package export â€” `__all__ = ['WireGuardManager']`.
`app/integrations/wireguard/manager.py` â€” 411 lines â€” infrastructure manager â€” paramiko SSH to `wg-manage.sh`, X25519 keygen, per-org `/24` allocation, RouterOS script generator.

### Defects (ranked, max 6)
1. `provider.py:SMSService.get_provider` â€” `_providers` is never registered anywhere in the slice; first `send_sms` raises `ValueError('No SMS provider registered')` after already consuming Redis quota.
2. `wireguard/manager.py:host/user/private_key/vps_public_key/vps_endpoint` â€” the `os.environ.get` default eagerly evaluates `current_app if current_app else ...`, so the properties raise `RuntimeError` outside app context, exactly where the docstring promises they work.
3. `wireguard/manager.py:generate_mikrotik_setup_script` â€” assigns `{wireguard_ip}/16` and firewall-accepts `10.0.0.0/16`, contradicting the allocator's per-org `/24`; every tenant can reach every other tenant's router. Also sets API/Winbox to `0.0.0.0/0`.
4. `brevo_service.py:send_email` / `email_service.py:_send_email` â€” mock branch returns `True` with no credentials, so verification and reset emails report success while nothing is delivered.
5. `wireguard/manager.py:allocate_ip` â€” unguarded read-then-write lets concurrent onboarding allocate duplicate IPs; `int(clean_id[-4:], 16)` raises `ValueError` for non-UUID org ids.
6. `wireguard/manager.py:add_peer/_run_ssh` â€” no tenant ownership check or `shlex.quote`; Ed25519-only key loading; `timeout` covers connect but not `exec_command`.

### Stubs
- `sms/__init__.py` is 0 bytes â€” exports missing, deep imports required.
- No Celery task definitions despite the `email_service.py` docstring; reminder/alert/report senders have no callers.
- Mock email branches stand in for delivery and are reachable in production misconfiguration.
- `/usr/local/bin/wg-manage.sh` on the VPS is assumed, not present or verified here.
- Hardcoded placeholders: `163.245.217.16`, VPS public key, `isp.bhatek.space`, `support@bhatek.space`.

---

## G13 â€” auth module

### Summary
The slice is the platform's authentication entry point: a Flask blueprint (`auth_bp`, nine routes) over `AuthController` â†’ `AuthService` â†’ `UserRepository`/`RefreshTokenRepository`/`AuditLogRepository`, implementing registration, JWT login with lockout and per-login `session_id`, refresh rotation, logout, password change, `GET /me`, email verification, and self-service organization onboarding. It issues the `user_id`, `organization_id`, `role` and `permissions` claims every other module's authorization depends on, and it assumes `app.core.security.jwt`, `app.core.database.session`, `app.models.*` and `app.integrations.email.service`. `auth/__init__.py` is 0 bytes, so blueprint registration must happen in an external composition root.

### Files
`app/modules/auth/__init__.py` â€” 0 lines â€” empty stub â€” no initializer or `auth_bp` export; package wiring lives elsewhere.
`app/modules/auth/controller.py` â€” 133 lines â€” HTTP controller layer â€” eight handlers mapping `ValidationError`â†’400 but every other exceptionâ†’401/500 with `str(e)`.
`app/modules/auth/permissions.py` â€” 58 lines â€” permission constants â€” 21 permission keys and four role lists, but `ROLE_PERMISSIONS` has no resolver and `has_permission` only tests membership or `'*'`.
`app/modules/auth/repository.py` â€” 420 lines â€” data-access layer â€” SQLAlchemy 2.0 `select`/`update` on `db.session`, refresh tokens stored as raw JWTs, no tenant predicate anywhere.
`app/modules/auth/routes.py` â€” 61 lines â€” Flask blueprint routes â€” `controller = AuthController()` built at import; `/me` queries `UserRepository` inline, bypassing the service.
`app/modules/auth/schemas.py` â€” 80 lines â€” marshmallow schemas â€” `RegisterOrganizationSchema.@validates_schema` carries all phone/slug/password-match rules; `UserUpdateSchema` unused.
`app/modules/auth/service.py` â€” 607 lines â€” service layer â€” login/refresh/logout/session logic, plus `resend_verification_email`, availability checks and `create_super_admin`, none reachable over HTTP.
`app/modules/auth/validators.py` â€” 34 lines â€” orphaned validators â€” `AuthValidator` never imported; requires password length 6 where schemas require 8.

### Defects (ranked, max 6)
`service.py:register_organization` â€” sets `permissions=['*']` for `org_admin`, contradicting `ROLE_PERMISSIONS['org_admin']`; `has_permission` then short-circuits on `'*'`, so one org's admin passes every check platform-wide.
`routes.py:get_me` / `controller.py:register` â€” return `user.to_dict()` without `exclude={'password_hash'}` (login uses the exclusion), leaking hashes in HTTP 200/201 bodies.
`controller.py:register` â€” catches marshmallow `ValidationError` only; `AuthService.register` raises `app.core.exceptions.handlers.ValidationError` for duplicate email/phone, which escapes to `except Exception` as a 500.
`service.py:refresh_token` â€” read-then-mutate revocation with no conditional UPDATE or lock, so two concurrent replays of one refresh token each yield a valid new pair; no reuse detection.
`service.py:logout` â€” controller never forwards `session_id`, making the session-scoped branch unreachable; a refresh token owned by another user falls to the else-branch and revokes all of the caller's tokens.
`service.py:login` â€” never checks an email-verified flag, so unverified accounts authenticate; verification only gates organization registration.

### Stubs
- `auth/__init__.py`: 0 bytes.
- No routes for `resend_verification_email`, `check_email_availability`, `check_org_slug_availability`, `create_super_admin`, `get_active_sessions`, `revoke_session_tokens`.
- `AuditLogRepository`/`AuditLog` defined, never written to.
- `validators.AuthValidator` and `schemas.UserUpdateSchema` orphaned.
- No device/session management UI endpoints despite `session_id`/`device_fingerprint` columns.

---

## G14 â€” Organization (tenant) module

### Summary
G14 is the tenant-management slice: a Flask blueprint exposing 12 organization and membership endpoints, backed by a controller â†’ service â†’ repository stack over Organization, OrganizationUser and OrganizationSetting, with marshmallow schemas. It creates tenants (creator becomes primary org_admin), gates every org-scoped read and write on active-org membership, manages memberships/roles with a last-primary-admin guard, and soft-deletes by writing status='deleted'. It anchors multi-tenancy for the platform, but router, subscriber, billing and session metrics are zero placeholders awaiting those modules, and the settings/permissions plumbing is present yet unwired.

### Files
- `app/modules/__init__.py` â€” 0 lines â€” empty stub â€” no module registry, package index or blueprint aggregation.
- `app/modules/organization/__init__.py` â€” 32 lines â€” package facade â€” re-exports models, three repositories, service, controller, org_bp; importing it instantiates OrganizationController and its repositories.
- `app/modules/organization/controller.py` â€” 383 lines â€” HTTP controller layer â€” 12 @token_required handlers with duplicated inline role checks; switch_organization is a no-op; pagination unguarded.
- `app/modules/organization/permissions.py` â€” 12 lines â€” unused permission constants â€” ORGANIZATION_PERMISSIONS and can_manage_organization never imported anywhere.
- `app/modules/organization/repository.py` â€” 449 lines â€” SQLAlchemy data access â€” three repos, every write commits internally; OrganizationSettingRepository has no callers.
- `app/modules/organization/routes.py` â€” 90 lines â€” Flask blueprint â€” 12 routes, no url_prefix, controller built at import, @token_required duplicated over the controller's own.
- `app/modules/organization/schemas.py` â€” 186 lines â€” marshmallow schemas â€” pre_load normalises slug and 254-prefix phone; explicit nulls pass update validation.
- `app/modules/organization/service.py` â€” 213 lines â€” business logic layer â€” non-atomic org+owner creation, slug generation, stats with hardcoded zeros.

### Defects (ranked, max 6)
- `controller.py:get_organization` (and 7 sibling handlers) â€” access is checked via `get_organizations_by_user`, which filters `status=='active'`; suspending an org (permitted by the update schema) 403s every member, so the status can never be restored through the API.
- `service.py:create_organization` â€” organization and owner `add_user` commits are separate; an add_user failure leaves an admin-less, unreachable tenant. Slug check-then-insert races into IntegrityError â†’ 500.
- `controller.py:list_organizations` â€” `per_page` is unvalidated: `per_page=0` raises ZeroDivisionError in the pages calculation (â†’ 500), and a non-positive `page` slices backwards.
- `controller.py:update_user_role` â€” no sole-primary-admin guard: demoting the only org_admin leaves zero admins and no API recovery; `transfer_primary_admin` exists but is never called.
- `controller.py:delete_organization` â€” requires `org_admin` + `is_primary` (super_admin denied, inconsistent with update/add) and ignores the service's False return, answering 200 for a nonexistent org.
- `service.py:update_organization` â€” `'slug' in data` then `re.match(..., None)` on an explicit null â†’ TypeError â†’ 500; `repository.update` also skips None values, so clearing fields silently no-ops while returning 200.

### Stubs
- `app/modules/__init__.py` is empty (0 bytes).
- `switch_organization` returns success without persisting any current-org state.
- Organization stats hardcode routers, subscribers, revenue and sessions to 0.
- OrganizationSettingRepository, permissions.py and OrganizationResponseSchema are never wired.
- No invitation flow: `invited_by` is stored, but membership is immediate.

---

## G15 â€” router module (controller / schemas / validators)

### Summary
`RouterController` is the Flask-JSON edge over `RouterService`: router CRUD, WireGuard onboarding, walled-garden/hotspot/PPPoE provisioning, RADIUS configure/retry/secret/test, connect/discover/health/sync, bulk delete-sync-retry, PPPoE removal and full-sync, all scoped by `g.organization_id` from `@token_required`. `schemas.py` supplies the marshmallow DTOs with IP/port/bulk validators, while `validators.py` is an empty comment stub. The slice fronts MikroTik RouterOS, FreeRADIUS NAS entries and WireGuard VPS peers, and is mounted by the router routes/blueprint outside this slice.

### Files
`app/modules/router/controller.py` â€” 1398 lines â€” REST controller â€” ~30 handlers, but `create` rejects every valid `network_id` and `delete_pppoe_server` is not tenant-scoped.
`app/modules/router/schemas.py` â€” 239 lines â€” marshmallow DTOs â€” most classes (filter, bulk, test, sync, response) are never loaded by the controller.
`app/modules/router/validators.py` â€” 1 line â€” empty stub â€” only `# Validators for router`; no imports or functions.

### Defects (ranked, max 6)
`controller.py:RouterController.create` â€” `RouterCreateSchema` yields a `uuid.UUID`, then `UUID(network_id)` re-wraps it; `AttributeError` is caught with `ValueError` â†’ every create returns 400 `INVALID_NETWORK_ID`.
`controller.py:RouterController.delete_pppoe_server` â€” `pppoe_repo.get_by_id(pppoe_id)` is unscoped and never checked against `router_uuid` â†’ cross-tenant PPPoE deletion on a foreign router.
`controller.py:RouterController.list` â€” `search` filters in Python after pagination and `total` ignores `network_id`, so pages/has_next lie; `per_page=0` divides by zero â†’ 500.
`controller.py:RouterController.test_radius` â€” forces `result['success'] = True`, reporting failed RADIUS verification as success.
`controller.py:RouterController.bulk_delete` / `bulk_sync` / `bulk_retry_radius` â€” skip `RouterBulkActionSchema`, so the 100-ID cap, UUID checks and failure status are bypassed; unbounded synchronous loop, always HTTP 200.
`controller.py:RouterController.full_sync` â€” legacy `NAS.query` inline import, commit only on the create branch (lost `_sync_nas_ip` changes), and `all_ok` misreads steps lacking `success`.

### Stubs
- `validators.py` â€” comment-only; field validation lives in `schemas.py`.
- `schemas.RouterRadiusRetrySchema` â€” `force` flag never read.
- `schemas.RouterTestSchema`, `RouterSyncSchema` â€” DTOs for handlers that load no body.

---

## G16 â€” Router Repository & Service

### Summary
This slice is the router domain's persistence layer and its provisioning engine: three tenant-scoped repositories over `Router`, `NAS`, `HotspotServer` and `PPPoeServer`, plus a service that allocates WireGuard addresses, generates the MikroTik setup script, proxies every RouterOS call through a VPS SSH heredoc, and creates the FreeRADIUS NAS row. It fits the platform as the boundary between admin router onboarding and FreeRADIUS/MikroTik runtime: the NAS `nasname`/`secret` it writes is exactly what FreeRADIUS matches on every Access-Request. Consequently its tenant scoping and status bookkeeping propagate straight into subscriber authentication.

### Files
- `app/modules/router/repository.py` â€” 1052 lines â€” repository layer â€” three repositories with tenant-filtered reads, CRUD commit/rollback, RADIUS status and `auto_config_attempts` counters, settings JSON for health/discovery, soft/hard delete cascading to NAS.
- `app/modules/router/service.py` â€” 861 lines â€” service layer â€” WireGuard keypair/peer, setup-script generation, hand-rolled RouterOS API over VPS SSH, RADIUS configure/check/retry, discovery, health, hotspot/PPPoE sync.

### Defects (ranked, max 6)
1. `service.py:delete_router` â€” removes the WireGuard peer **before** the "Router has active services" guard, so a refused hard delete still cuts the live router off the VPS.
2. `service.py:check_radius_status` â€” hardcodes `10.0.0.1` instead of `_get_radius_server()`, so `RADIUS_SERVER_IP` is ignored and status can be marked `configured` against the wrong server; the setup script hardcodes it too.
3. `repository.py:RouterRepository.update_status` â€” increments `auto_config_attempts` on any `error_message`, mixing link/health failures with RADIUS attempts; `get_routers_with_issues` (`> 3`) then flags healthy routers.
4. `service.py:sync_router` â€” always returns `success=True` and forces `status='online'` despite appended errors; a soft-deleted hotspot is found by `get_by_router_and_hotspot_id` (no `is_active` filter), `update()` resolves via `is_active==True` and no-ops, yet `hotspot_synced` still increments.
5. `service.py:delete_pppoe_server` / `create_pppoe_server` â€” row loaded without checking `pppoe.router_id == router_id` (wrong MikroTik edited); `int(max_sessions)` sits outside the `try`, raising bare `ValueError`.
6. `repository.py:update_health` / `update_discovery` â€” mutate `router.settings` in place and reassign the same dict, so SQLAlchemy may miss the change and never persist health/discovery JSON.

### Stubs
- No TODO/FIXME/pass-body placeholders; only `except: pass` handlers.
- `RouterService.__init__`: `self.mikrotik_client` never invoked â€” dead direct-API path.
- `VPS_ENDPOINT` constant and `organization_name` script parameter unused.
- `RouterRepository.nas_model` unused; `serial_number` never supplied to `update_discovery`.

---

## G17 â€” Router blueprints

### Summary
This slice is the HTTP edge of the router module: a JSON API blueprint, a tenant-scoped server-rendered UI blueprint, and an empty package initializer. It is how operators onboard, inspect, and repair MikroTik routers (WireGuard tunnel, RADIUS/NAS wiring, hotspot and PPPoE servers) inside an organization. Almost all business logic is delegated to RouterController/RouterService, so these files are routing, auth gating, form parsing, and flash-message glue.

### Files
`app/modules/router/__init__.py` â€” 0 lines â€” empty stub â€” no blueprint export or registration hook exists.
`app/modules/router/routes.py` â€” 301 lines â€” Flask API blueprint â€” `router_bp` at `/api/v1/routers`, ~27 `token_required` endpoints delegating to `RouterController`; only the public `/test` has local logic (`RouterTestSchema` + `MikroTikClient.test_connection`).
`app/modules/router/web_routes.py` â€” 649 lines â€” Flask web blueprint (Jinja pages) â€” `router_web_bp` at `/organization/<org_id>/routers`, guarded by `web_router_access_required` (session user + `OrganizationService` membership); drives `RouterService`, `RouterRepository`/`HotspotServerRepository`/`PPPoeServerRepository`, plus a direct `NAS` ORM update.

### Defects (ranked, max 6)
1. `routes.py:test_connection` â€” unauthenticated `POST /api/v1/routers/test` accepts arbitrary host/username/password/port, so it is an SSRF and credential-probe surface; failures still return HTTP 200.
2. `web_routes.py:edit_hotspot` / `edit_pppoe` â€” the generic `except Exception` path redirects with `hotspot.router_id`/`pppoe.router_id`; a repository failure before assignment raises `NameError` â†’ 500.
3. `web_routes.py:regenerate_radius_secret` â€” plaintext RADIUS secret stored in the Flask session cookie, NAS updated via raw `NAS.query` + `db.session.commit()` outside repositories, no audit record beyond `logger.warning`.
4. `web_routes.py:show` â€” calls private `router_service._generate_mikrotik_setup_script` with plaintext `router.radius_secret`; blanket `except Exception` converts DB/template errors into a misleading "Router not found".
5. `web_routes.py:index` â€” pagination `total` comes from `count_by_organization` without the `network_id` filter, so totals/pages disagree with the filtered list.
6. `web_routes.py:edit` â€” "No changes submitted" branch is unreachable (`data` always populated); `create_hotspot`/`create_pppoe` ignore the service `result` and flash success unconditionally.

### Stubs
- `__init__.py` â€” zero-byte initializer; blueprint registration must live elsewhere.
- `web_routes.py:edit` â€” dead `else: flash('No changes submitted')`.
- `web_routes.py:create_hotspot` / `create_pppoe` â€” assigned-but-unused `result`.
- `web_routes.py:health` â€” renders a router list only; performs no health probing.

---

## G18 â€” subscriber (controller/schemas/validators)

### Summary

SubscriberController exposes 21 JWT-guarded views that parse marshmallow bodies and delegate to SubscriberService, covering hotspot users auto-created by phone during the M-Pesa payment flow, admin-provisioned PPPoE users, listing/pagination, update, delete, plan purchase, subscription lifecycle, device MAC allow-listing, access checks and organisation stats. It is the HTTP boundary of the subscriber module, feeding the captive portal and admin console while the real RADIUS path is documented as living elsewhere (FreeRADIUS â†’ /api/radius/authenticate â†’ RadiusAuthHandler). Tenant scoping is by g.organization_id passed into every service call; schemas.py supplies 12 schemas of which only 6 are imported, and validators.py is empty.

### Files

`app/modules/subscriber/controller.py` â€” 1022 lines â€” REST API controller â€” 21 @token_required views (pppoe_create permission only on PPPoE create); no Blueprint or route registration, so every documented path exists only in docstrings.
`app/modules/subscriber/schemas.py` â€” 202 lines â€” marshmallow schemas â€” 12 schemas, only 6 imported; RadiusAuth/BulkAction/Filter/UpdateDevice/Renew/CreateSubscription/Stats are dead code; payment_details always demands 'phone'.
`app/modules/subscriber/validators.py` â€” 1 line â€” empty stub â€” contains only '# Validators for subscriber'; its evident phone/MAC/username normalisers live inline in schemas.py.

### Defects (ranked, max 6)

1. `controller.py:create_pppoe_subscriber` / `purchase_plan` â€” `UUID(data['plan_id'])` re-wraps a value that `fields.UUID` already deserialised to a uuid.UUID; `uuid.UUID(UUID_instance)` raises AttributeError, which the ValueError handlers do not catch, so both endpoints return 500 INTERNAL_ERROR on every valid request.
2. `controller.py:list_subscribers` â€” status/search/has_active_subscription filter the page rows, but total comes from `repository.count_by_organization(org, subscriber_type=...)` only, so pagination total/pages/has_next contradict the returned rows.
3. `controller.py:create_hotspot_subscriber` â€” schema-validated email, first_name and last_name are never forwarded; the service receives only phone and name, silently losing data for M-Pesa-created subscribers.
4. `controller.py:create_subscription` â€” reads raw `request.get_json()`, bypassing CreateSubscriptionSchema entirely, so the expiry-after-start validation never runs.
5. `controller.py:cancel_subscription` â€” always returns HTTP 200 with 'Subscription cancelled successfully' even when the service result is False, and lacks the BusinessError handler its sibling views have (business failures become 500).
6. `schemas.py:PurchasePlanSchema.validate_payment_details` â€” requires 'phone' whenever payment_details is present, even for cash, bank_transfer and card, rejecting valid non-M-Pesa purchases.

### Stubs

- `validators.py` â€” comment only, no code.
- `SubscriberStatsSchema` â€” `pass` body.
- `PPPoECreateSchema.validate_plan_type` â€” `pass` ("validated in service").
- `CheckAccessSchema.validate_entire_schema` â€” `pass`.
- `purchase_plan` ignores the validated coupon_code; `delete_subscriber` hard-deletes on `?soft=false` guarded only by @token_required.

---

## G19 â€” subscriber (repository/service)

### Summary
The slice is the tenant-scoped data and business layer for subscribers: SubscriberRepository and PlanRepository wrap SQLAlchemy for Subscriber, Device, Subscription and Plan, while SubscriberService adds phone normalisation, hotspot/PPPoE provisioning, subscription create/renew/cancel, RADIUS password and MAC authentication, device limits and auth-cache invalidation. It is the seam between M-Pesa purchase events, the FreeRADIUS auth hot path and MikroTik session enforcement. Every query is organization_id-scoped and ORM-parameterised (no SQL injection), but commits inside repository methods make multi-entity flows non-atomic and the RADIUS-facing contracts are internally inconsistent.

### Files
- `app/modules/subscriber/repository.py` â€” 999 lines â€” repository layer (SQLAlchemy data access) â€” SubscriberRepository + PlanRepository, org-scoped CRUD/counts, commits in every method, mass-assignment in `update`, `get_subscriber_stats` mixes subscriber and subscription counts.
- `app/modules/subscriber/service.py` â€” 1097 lines â€” service layer (business logic + RADIUS auth) â€” lazy-wired provisioning/subscription/auth/device logic; `sync_pppoe_user_to_radius` called with three different arities and `radius_cache` returns a class, not an instance.

### Defects (ranked, max 6)
1. `service.py:create_subscription` â€” commit at line 436 precedes RADIUS sync; a sync failure raises `BusinessError` after rows are persisted, so a retry inserts a second active Subscription and re-adds `plan.price` to `total_spent` (duplicate billing). Same pattern in `create_pppoe_subscriber`, `update_subscriber`, `renew_subscription`, `cancel_subscription`.
2. `service.py:sync_pppoe_user_to_radius` call sites (258, 315, 453) â€” invoked with 2, 3 and 4 arguments; at most one matches the real signature, so PPPoE provisioning, password change or subscription create raises TypeError at the RADIUS write.
3. `repository.py:update / update_device / PlanRepository.update` â€” `setattr` for any `hasattr` key allows overwriting `organization_id` (tenant escape), `status`, `total_spent`, `password_encrypted`; RADIUS/DB write can move a subscriber into another org.
4. `service.py:authenticate_subscriber` â€” credential passed to `get_by_login_credential` unnormalised and matched as `phone OR username` with `.first()`, so `07â€¦` logins fail against stored `2547â€¦` and a username colliding with another subscriber's phone authenticates the wrong account.
5. `repository.py:get_by_id` â€” defaults `include_inactive=False`, so `service.get_subscriber` cannot load suspended/deleted subscribers; update, delete and access checks raise `NotFoundError` and status cannot be restored to active.
6. `service.py:delete_subscriber` â€” soft-deletes the Subscriber but never expires its Subscription rows, so `get_active_subscribers_count` and dashboard stats keep counting deleted subscribers; `repository.py:get_recent_subscribers`/`get_subscribers_expiring_soon` also omit status filters.

### Stubs
None. Both files are fully implemented with no TODO/pass/NotImplemented placeholders; dead code exists (`payment_service`, `sms_service`, `current_app`, `update_total_spent`, most PlanRepository CRUD) but nothing is stubbed.

---

## G20 â€” Subscriber module blueprints

### Summary
This slice is the whole HTTP surface of the subscriber module: a REST blueprint exposing ~25 `/api/v1/subscribers` endpoints that forward straight to a module-level `SubscriberController`, and a Jinja blueprint under `/organization/<org_id>/subscribers` rendering pages for hotspot/PPPoE subscribers, their devices and subscriptions. Both are thin routers â€” session/org authorisation and delegation to `SubscriberService`, `BillingService` and `OrganizationService` happen here, while RADIUS, MikroTik and M-Pesa work stays in the controller/service slices. It is the operator- and captive-portal-facing entry point for subscriber lifecycle management across tenants.

### Files
`app/modules/subscriber/__init__.py` â€” 0 lines â€” empty stub â€” exports neither blueprint, so `from app.modules.subscriber import subscriber_bp` raises ImportError.
`app/modules/subscriber/routes.py` â€” 322 lines â€” REST blueprint â€” `subscriber_bp` at `/api/v1/subscribers`, one `SubscriberController()` built at import, only three routes permission-gated.
`app/modules/subscriber/web_routes.py` â€” 657 lines â€” Jinja web blueprint â€” `subscriber_web_bp`, `web_subscriber_access_required` login+org-membership gate, 15 views, `NetworkService` imported but unused.

### Defects (ranked, max 6)
1. `web_routes.py:web_subscriber_access_required` â€” two `get_plans(...,0,100,...)` queries per request, `index` adds N+1 `get_active_subscription`, and `get_organization` result is never None-checked, so a stale org UUID 500s on `.id`.
2. `web_routes.py:index` â€” `count_by_organization` ignores the status/search filters applied to the page query, so the pagination total never matches filtered rows and trailing pages are empty.
3. `web_routes.py:renew_subscription_form` â€” `get_plans(org_id, only_active=True)` contradicts the five-argument call in the decorator; if skip/limit are required, the renewal page raises TypeError while listing works.
4. `web_routes.py:cancel_subscription` / `remove_device` â€” redirect to `request.referrer` (open redirect), never verify the subscription/device belongs to the `<subscriber_id>` in the URL, and no POST anywhere carries a CSRF check.
5. `routes.py` â€” `update_subscriber`, `delete_subscriber`, `purchase_plan`, `renew`, `cancel`, `add_device` and `export_subscribers` carry only `@token_required`, contradicting the "BULK OPERATIONS (ADMIN ONLY)" header; `pppoe_create` also breaks the `subscriber_*` permission namespace.
6. `web_routes.py:renew_subscription_post` â€” unused `result`, unguarded `updated_sub.expiry_time.strftime(...)` â†’ AttributeError surfaced as a bogus renew error; upgrade path calls `BillingService.create_subscription` while `create_subscription` view calls `SubscriberService.create_subscription` (duplicate implementations).

### Stubs
- `__init__.py` (0 bytes): evidently meant to re-export `subscriber_bp` / `subscriber_web_bp` for the app factory.
- `routes.py` docstring: advertises a `POST /authenticate` captive-portal route that does not exist.

---

## G21 â€” Billing (services/schemas)

### Summary
This slice implements the commercial core: `BillingService` manages plans, subscriptions, vouchers, batches, coupons, invoices and the expiry job, while `BillingPeriodService` aggregates revenue, churn and redemption per daily/weekly/monthly window. It sits between the billing HTTP/Celery entry points and the billing repositories/models, and drives FreeRADIUS sync plus auth-cache invalidation, so every access grant or revocation flows through it. `schemas.py` supplies the marshmallow contracts that gate those service calls.

### Files
- `app/modules/billing/service.py` â€” 1416 lines â€” service layer â€” owns plan/subscription/voucher/coupon/invoice lifecycle with RADIUS side effects; voucher codes are stored hyphenated but looked up hyphen-stripped.
- `app/modules/billing/billing_service.py` â€” 386 lines â€” analytics service â€” `BillingPeriodService` resolves period bounds and aggregates invoices, subscriptions and vouchers; the org filter is optional on every query, so omission reads across tenants.
- `app/modules/billing/schemas.py` â€” 263 lines â€” marshmallow schemas â€” nine schemas validate plan, purchase, voucher, batch, redemption, coupon and invoice-filter payloads; the activation-type whitelist rejects the service-supported 'scheduled' value.

### Defects (ranked, max 6)
1. `service.py:redeem_voucher` â€” strips hyphens before `get_valid_by_code`, while `_generate_voucher_code` stores `XXXX-XXXX-XXXX`; redemptions fail unless the repository re-inserts separators.
2. `billing_service.py:get_revenue_by_period` (plus three siblings) â€” `if organization_id:` makes tenant scoping optional; omitting it returns every tenant's revenue.
3. `schemas.py:VoucherBatchCreateSchema` â€” quantity allows 10000 while `BillingService.MAX_BATCH_SIZE` is 1000; payloads accepted by validation die at runtime.
4. `service.py:cancel_subscription` â€” the `ActiveSession` filter omits `organization_id`, so session termination can cross tenant boundaries.
5. `billing_service.py:get_subscription_stats_by_period` â€” 'expired' requires `cancelled_at`, which the expiry job never sets; the metric is always 0.
6. `service.py:create_subscription` â€” `device_limit or plan.device_limit` discards an explicit 0; explicit zero bandwidths silently become plan defaults.

### Stubs
- `schemas.py:RedeemVoucherSchema.validate_voucher_code` ends in a bare `pass`.
- `service.py:create_voucher` accepts 'scheduled' activation but implements no scheduling.
- `Voucher.password_hash` is generated but never verified.
- `run_expiry_checks` omits the documented voucher-batch status refresh.

---

## G22 â€” billing (controller/repository)

### Summary
This slice is the billing module's HTTP and persistence boundary: BillingController exposes 25 JSON handlers for plans, vouchers, subscriptions, discount coupons, invoices, expiry maintenance and dashboard stats, while five repositories mediate all Plan/Subscription/Voucher/VoucherBatch/DiscountCoupon SQL with organization_id tenancy. It sits between the JWT/permission layer and BillingService, and is the surface that captive-portal sales, voucher redemption, renewals and Celery expiry sweeps ultimately drive. Tenant scoping is broadly correct, but voucher activation, invoice payload validation, coupon usage scoping and per-repository commits undermine the flows it fronts.

### Files
`app/modules/billing/controller.py` â€” 938 lines â€” Flask controller class â€” 25 endpoints in docstring-only paths, `g.organization_id` tenancy, identical try/except envelopes, `generate_invoice` with no schema, several reads lacking `permission_required`.
`app/modules/billing/repository.py` â€” 860 lines â€” SQLAlchemy repository layer â€” Plan/Subscription/Voucher/VoucherBatch/DiscountCoupon repositories, eager `db.session.commit()` per method, `use_voucher`/`activate_voucher` pass `organization_id=None`, no InvoiceRepository despite importing Invoice/InvoiceItem.

### Defects (ranked, max 6)
- `repository.py:VoucherRepository.use_voucher/activate_voucher` â€” both call `get_by_id(voucher_id, None, ...)` so the filter becomes `organization_id IS NULL`; no voucher matches, redemption and first-use activation always return False (and tenant isolation is dropped).
- `repository.py:VoucherRepository.get_valid_by_code` â€” requires `expires_at > utcnow()`, but `first_use` vouchers only receive `expires_at` inside `activate_voucher`, so those vouchers can never be redeemed.
- `controller.py:generate_invoice` â€” manual `request.get_json()`, uncaught KeyError/ValueError on `subscriber_id`, and no InvoiceGenerateSchema, so malformed bodies produce 500 instead of 400.
- `controller.py:get_plans` â€” total comes from `service.plan_repo.count_by_organization` without the `plan_type` filter, so total/pages disagree with returned rows; `per_page`/`page` are unfloored (per_page=0 divides by zero â†’ 500) and `is_active=only_active if only_active else None` makes only_active=false mean "no filter".
- `repository.py:DiscountCouponRepository.increment_usage` â€” no organization_id and legacy `query.get()`, incrementing across tenants and risking AttributeError on SQLAlchemy 2.0.
- `controller.py:delete_plan` â€” ignores the boolean from `BillingService.delete_plan`/`PlanRepository.delete`, always reporting success even when a hard delete of a plan with active subscriptions was refused.

### Stubs
- Invoice persistence is absent from this slice: no `InvoiceRepository`; `Invoice`/`InvoiceItem` imported and unused while `generate_invoice` delegates writes outside the slice.
- Leftover patch marker `# âœ… NEW METHOD - Add this` at `SubscriptionRepository.get_by_organization` (repository.py:244). No `pass`/TODO bodies elsewhere.

---

## G23 â€” Billing Blueprints

### Summary
G23 is only the routing layer of the billing module: a 542-line JSON API blueprint at `/api/v1/billing` (31 endpoints for plans, vouchers, subscriptions, coupons, invoices, expiry maintenance and period stats) and a 630-line server-rendered blueprint at `/organization/<org_id>/billing` (14 routes for the admin UI). The API delegates to `BillingController` and `BillingPeriodService`, adding query parsing, `g.organization_id` scoping and JSON envelopes; the web layer authenticates by session user plus organization membership, then calls `BillingService` and its repositories for plan CRUD, voucher/batch creation, detail pages and an inline dashboard. It sits above the controller/service/repository layers and the `web/billing/**` Jinja templates, and is the registration point the app factory needs for both the customer-facing API and the operator console.

### Files
`app/modules/billing/__init__.py` â€” 0 lines â€” empty stub â€” no blueprint exports or registration hook; everything must be imported explicitly.
`app/modules/billing/routes.py` â€” 542 lines â€” JSON API blueprint â€” 31 routes; `BillingController()` and `BillingPeriodService()` are built at import time.
`app/modules/billing/web_routes.py` â€” 630 lines â€” server-rendered web blueprint â€” 14 routes; `web_billing_access_required` checks login and org membership but no role.

### Defects (ranked, max 6)
`routes.py:get_public_plans` â€” `GET /plans/public` is documented for the captive portal but carries `@token_required`, so unauthenticated portal clients get 401 and can never list plans.
`web_routes.py:web_billing_access_required` â€” no permission/role gate on any web route, so any org member (even read-only) can POST plan create/delete and voucher batch create; the API at least enforces `permission_required` strings.
`web_routes.py:plan_edit` â€” falsy guards (`if price:`, `if bandwidth_up:`, `if data_limit_mb:`) make zero values unsettable, while `is_active`/`is_public` are always overwritten from the form, so partial posts silently deactivate plans.
`web_routes.py:dashboard` â€” `total_revenue` sums all `status == 'active'` subscriptions regardless of `expiry_time`, contradicting `active_subscriptions`/MRR which require `expiry_time > now`; the page also renders fabricated zeros (`total_subs += 5`, `revenue_data.append(0)`, hardcoded `price`/`plan_type`).
`web_routes.py:invoices` / `invoice_show` â€” invoice list passes `invoices=[]` and zeroed stats, and detail never loads a record (`invoice=None`, any id accepted, no 404 or org scoping), leaving tenancy to client-side JS.
`routes.py:generate_invoice` / `redeem_voucher` â€” only `@token_required`, so any authenticated user can mint invoices or burn voucher codes; period handlers also read `g.organization_id` unguarded and convert every failure into a generic 500.

### Stubs
- `__init__.py` empty (0 bytes); no package-level blueprint wiring.
- Dashboard revenue trend, growth curve, top-plan price/type/validity, recent transactions, pending payments, overdue invoices â€” placeholders awaiting payment module.
- Invoice list/detail pages â€” shells driven by client-side API calls.
- `VoucherRepository.get_single_vouchers` â€” commented as not yet implemented.

---

## G24 â€” payment module

### Summary
The payment module owns money-in: a `/api/v1/payments` blueprint that initiates M-Pesa STK Push, cash, bank-transfer and voucher payments, receives the public Safaricom callback, verifies, lists, refunds and reports transactions. `PaymentService` writes a pending `Transaction`, then on callback success deduplicates the M-Pesa receipt, marks it success and calls `_activate_subscription` to create the `Subscription`, sync RADIUS and register the device MAC. It sits between the captive-portal/API front end and the mpesa, subscriber, billing and radius integrations, and is the provisioning trigger for every paid subscription.

### Files
- `app/modules/payment/__init__.py` â€” 0 lines â€” package init (empty stub) â€” exports nothing; `payment_bp` is never re-exported for registration.
- `app/modules/payment/controller.py` â€” 415 lines â€” HTTP controller â€” seven JSON handlers; `PaymentCallbackSchema`/`RefundSchema` imported unused; `token_required` applied twice.
- `app/modules/payment/repository.py` â€” 714 lines â€” data access layer â€” four repositories; webhook/refund repos unused; `get_by_checkout_id` relies on JSONB `.astext`.
- `app/modules/payment/routes.py` â€” 87 lines â€” blueprint â€” seven routes under `/api/v1/payments`; `PaymentController()` built at import time.
- `app/modules/payment/schemas.py` â€” 95 lines â€” marshmallow schemas â€” only `PaymentInitiateSchema` is ever loaded; four schemas dead.
- `app/modules/payment/service.py` â€” 847 lines â€” business layer â€” initiation routing, callback processing, activation, verification, refund.
- `app/modules/payment/validators.py` â€” 1 line â€” empty stub â€” comment only, no functions.

### Defects (ranked, max 6)
1. `service.py:verify_payment_status` / `refund_transaction` â€” neither accepts nor checks `organization_id`; controller passes only the UUID, so any tenant can activate or refund another org's transaction (cross-tenant IDOR).
2. `service.py:process_callback` / `controller.py:mpesa_callback` â€” public webhook has no signature/IP check, and the payload `amount`/`CheckoutRequestID` are never compared to the stored transaction, so forged or underpaying callbacks are marked success.
3. `controller.py:initiate_payment` + `schemas.py:PaymentInitiateSchema` â€” schema has no `voucher_code`/`collected_by`/`bank_name` fields and controller forwards only phone/ip/user-agent, so the cash, bank and voucher branches are unreachable; voucher always raises "Voucher code is required".
4. `service.py:process_payment` / `_process_voucher_payment` â€” transaction is committed `pending` before account/voucher validation (orphan rows on error), and the voucher is never marked redeemed, so one code activates unlimited subscriptions.
5. `repository.py:TransactionRepository.get_pending_transactions` â€” uses `datetime.timedelta` while only the `datetime` class is imported, raising `AttributeError`; the stuck-payment sweep can never run.
6. `service.py:refund_transaction` â€” no M-Pesa reversal call, unredeemed `Refund` stays `pending`, and cancellation failures are swallowed while the response claims "subscription cancelled".

### Stubs
- `__init__.py` empty; `validators.py` comment only.
- `PaymentCallbackSchema`, `PaymentVerifySchema`, `PaymentListSchema`, `RefundSchema` never loaded.
- `PaymentWebhookLogRepository`, `RefundRepository`, `PaymentAccountRepository` CRUD never called (models built inline).
- `_log_webhook` `headers={}` placeholder; logs `processed=True` on arrival, killing the retry query.
- `refund_transaction` is a DB-only record with no provider settlement step.

---

## G25 â€” Session & RADIUS Accounting

### Summary
The session slice exposes live-connection data: listing active sessions (by subscriber, router, access point, username, MAC), manual termination with a MikroTik hotspot kick, expiry cleanup, router-to-DB sync, 24h stats, and usage reports aggregated from FreeRADIUS accounting rows. It is a Flask blueprint (`session`, 12 routes, no url_prefix) over SessionController â†’ SessionService â†’ SessionRepository / RadiusAccountingRepository, backed by `ActiveSession` and `RadiusAccounting`. It sits between the router/RADIUS integration layer (RouterRepository, MikroTikClient, RadiusCache, EncryptionService) and the reporting/dashboard layer, so its correctness drives both live ops and billing-adjacent usage numbers.

### Files
- `app/modules/session/__init__.py` â€” 25 lines â€” package exports â€” `__all__` names `RadiusAccounting`, which is never imported.
- `app/modules/session/controller.py` â€” 262 lines â€” HTTP controller â€” ten token-decorated JSON handlers plus public `radius_accounting` delegating to `RadiusAccountingHandler`.
- `app/modules/session/repository.py` â€” 391 lines â€” SQLAlchemy data access â€” org-scoped session queries, bulk expire update, aggregate accounting usage, unscoped purge.
- `app/modules/session/routes.py` â€” 83 lines â€” Flask blueprint â€” 12 routes, view-level `token_required` only, module-level controller instance.
- `app/modules/session/schemas.py` â€” 0 lines â€” EMPTY_STUB â€” intended Marshmallow request/response schemas; absent, so `to_dict()` is the only serializer.
- `app/modules/session/service.py` â€” 310 lines â€” business logic â€” create/terminate/sync/cleanup/stats/usage wiring MikroTik, RadiusCache, EncryptionService.

### Defects (ranked, max 6)
1. `routes.py:terminate_session` + `controller.py:terminate` â€” `token_required` applied on both route and controller method, double token decoding; `permission_required` exists only inside the controller, so route wiring can silently drop authorization.
2. `service.py:sync_router_sessions` â€” an empty `get_active_sessions` result (router down) terminates every local session with cause `router_sync`; no guard on failed/empty fetch.
3. `repository.py:cleanup_old_records` â€” bulk deletes `RadiusAccounting` by `acct_start_time` with no `organization_id` filter, destroying other tenants' accounting history.
4. `controller.py:terminate` â€” `request.json` read twice raises on non-JSON bodies â†’ 500 not 400; `if not success: 404` is unreachable because `terminate_session` raises `NotFoundError`, so unknown IDs return 500 with internal text.
5. `service.py:get_user_usage` â€” sums octets across Start/Interim/Stop rows, inflating per-user usage, while org usage filters `acct_status_type=='Stop'`; the two reports disagree and active sessions count zero bytes.
6. `controller.py:list_active` â€” filtered branches ignore `skip`/`per_page` while only the unfiltered branch paginates; `per_page` is uncapped, allowing oversized payloads.

Also: `__init__.py` exports undefined `RadiusAccounting`; `/radius/accounting` is unauthenticated with no shared secret and returns failures as HTTP 200.

### Stubs
- `schemas.py` â€” 0 bytes, no validation/serialization contract.
- `service.py:update_session_stats`, `terminate_user_sessions` â€” no route or caller.
- `repository.py:get_by_session_id`, `get_recent_by_subscriber`, `get_sessions_by_date_range`, `cleanup_old_records` â€” unused.
- No session-create route; `create_session` reachable only from outside the slice.

---

## G26 â€” Reporting and analytics module

### Summary
G26 is the read-only analytics slice: `reports_bp` (`/api/v1/reports`) exposes 13 GET endpoints that `ReportingController` maps onto `ReportingService`, which aggregates payment, billing, subscriber, router and session tables into KPI dicts and chart series, all filtered by `g.organization_id` except the super-admin `/isp` overview. It owns no models, tasks or templates, so it sits as a pure consumer of the ORM and JWT layers defined elsewhere, feeding dashboard and chart widgets. Tenant isolation is consistent, but input validation and bounds are absent, making every route an unvalidated window onto tenant tables.

### Files
`app/modules/reporting/__init__.py` â€” 1 line â€” package docstring â€” nothing but a docstring; `reports_bp` lives in routes.py, so registration is external.
`app/modules/reporting/controller.py` â€” 268 lines â€” REST controller â€” 13 handlers parse raw `request.args`, never touch schemas, and answer HTTP 500 with `str(e)`.
`app/modules/reporting/repository.py` â€” 75 lines â€” unused query helpers â€” `ReportingRepository` is never imported; its two aggregations duplicate service code.
`app/modules/reporting/routes.py` â€” 135 lines â€” Flask blueprint â€” `url_prefix='/api/v1/reports'`; every view re-applies the `@token_required` its controller method already carries.
`app/modules/reporting/schemas.py` â€” 41 lines â€” marshmallow schemas â€” `ReportQuerySchema`, `ChurnRateSchema`, `BandwidthTrendSchema` exist but nothing imports them.
`app/modules/reporting/service.py` â€” 779 lines â€” aggregation service â€” `ReportingService._date_range` plus dashboard, revenue, subscriber, router, usage, churn, ISP-wide and activity methods; logs then re-raises.

### Defects (ranked)
1. `controller.py:ReportingController.get_isp_overview` â€” `getattr(g, 'current_user', None)` fails open: if `token_required` populates `g.user` instead, any tenant user receives platform-wide revenue and all-org subscriber counts.
2. `routes.py` views + `controller.py` methods â€” `@token_required` applied twice, so authentication and `g` population run twice per request.
3. `controller.py:get_churn_rate` / `service.py:get_churn_rate` â€” `months` is unbounded; `for i in range(months-1,-1,-1)` runs two count queries per iteration, so `?months=1000000` is a DB/CPU denial of service.
4. `schemas.py` â€” dead module: period OneOf, month/month(s)/days ranges never enforced; `controller.py:get_recent_activity` turns `limit=-1` into SQLAlchemy no-LIMIT and dumps whole tables.
5. `service.py:_date_range` â€” no `custom` branch and a lone `start_date`/`end_date` is ignored, so `period=custom` silently returns 30-day data labelled custom.
6. `service.py:get_usage_report`, `get_bandwidth_trend` â€” aggregate `ActiveSession` only while `RadiusAccounting` is imported and unused, so closed sessions vanish from usage; naive `utcnow()` boundaries sit three hours off Africa/Nairobi month/day edges.

### Stubs
- `schemas.py` `pass` subclasses, unwired to any request path.
- `service.py:get_router_report` â€” `cpu_load` and `uptime` hardcoded `None`.
- `service.py:get_usage_report` â€” `RadiusAccounting` imported, never queried.
- `service.py:_date_range` â€” unused `week` parameter.
- `repository.py:ReportingRepository` â€” complete class, zero call sites.

---

## G27 â€” network module

### Summary
CRUD slice for the `Network` entity (hotspot/pppoe/hybrid segments): `NetworkRepository` (every query ANDed with `organization_id`) â†’ `NetworkService` (slug generation/uniqueness, org check, router-guarded delete) â†’ `NetworkController`/`network_bp` JSON API, plus an overlapping session-authenticated Jinja UI (`network_web_bp`, `/organization/<org_id>/networks`). It supplies the network context the rest of the platform hangs off: routers (`Network.routers`, `RouterService.get_routers_by_network`), and the parent object for subscriber plans, NAS/FreeRADIUS groups and MikroTik provisioning. Read paths are tenant-safe and consistent; the write paths and the web layer carry several real bugs.

### Files
- `app/modules/network/__init__.py` â€” 30 lines â€” export barrel â€” eagerly imports `routes.py`, so importing the package constructs `NetworkController()`; `network_web_bp` is not exported.
- `app/modules/network/controller.py` â€” 197 lines â€” JSON controller â€” 9 `@token_required` methods; `bulk_update_status` re-wraps UUIDs and always 500s.
- `app/modules/network/repository.py` â€” 202 lines â€” data access â€” all lookups scoped by `organization_id`; `update` silently drops `None`.
- `app/modules/network/routes.py` â€” 69 lines â€” API blueprint â€” 9 rules on `network_bp`, each re-applying `@token_required`.
- `app/modules/network/schemas.py` â€” 71 lines â€” marshmallow schemas â€” create/update/bulk-status; slug regex duplicated; `NetworkResponseSchema` unused.
- `app/modules/network/service.py` â€” 132 lines â€” service layer â€” `_generate_slug` with `-N` retry, per-org slug uniqueness, delete maps `False` to `BusinessError`.
- `app/modules/network/web_routes.py` â€” 446 lines â€” Jinja web blueprint â€” session/org-membership decorator plus 7 views; AP/session/bandwidth values are placeholders.

### Defects (ranked)
1. `controller.py:bulk_update_status` â€” `UUID(nid)` applied to values `fields.UUID()` already deserialised â†’ `AttributeError` â†’ blanket handler â†’ 500; `PUT /api/v1/networks/bulk-status` can never succeed.
2. `controller.py:list_networks` / `web_routes.py:index` â€” `request.args.get('is_active', type=bool)` makes `bool('false') == True`, and `total` comes from `count_by_organization` ignoring `type`/`search`, so `pages` lies whenever those filters are set.
3. `repository.py:update` â€” `value is not None` guard means `description`/`settings` can never be cleared, contradicting `NetworkUpdateSchema(allow_none=True)` on every field.
4. `service.py:update_network` â€” `re.match(..., data['slug'])` with an explicit `null` slug raises `TypeError` â†’ 500; `controller.update_network` catches only marshmallow `ValidationError`, not the app error the service raises.
5. `repository.py:delete` + `service.py:delete_network` â€” `False` conflates not-found with router conflict, so a missing ID reports "Cannot delete network with associated routers" (409); `network.routers.count()` assumes a dynamic relationship.
6. `web_routes.py:edit`/`create`/`bulk_action` â€” `is_active` is forced from checkbox presence (unchecked always deactivates on edit, always activates on create), descriptions cannot be cleared, bulk delete commits per item (non-atomic), and there is no CSRF or role check on any mutating POST.

### Stubs
- `web_routes.py:show` â€” `access_points = []`, `active_sessions = 0`, `bandwidth_usage = 0` hardcoded.
- `web_routes.py` â€” `get_current_user`, `get_current_organization_from_session` defined, never called.
- `schemas.py:NetworkResponseSchema` â€” exported, never used; responses come from `to_dict()`.
- No service method for paginated totals; controller and web index call `network_repo` directly.
- No AP/session/bandwidth model backing the network detail page.

---

## G28 â€” access_point module

### Summary
A four-layer CRUD slice for Wi-Fi access points attached to a router inside an organization: `AccessPointRepository` (tenant-filtered SQLAlchemy queries), `AccessPointService` (MAC uniqueness, soft delete guarded by active sessions), an `AccessPointController` JSON API, and a session-authenticated Jinja web UI. It supplies list/filter/pagination, dropdowns, monitoring views (online/offline/issues), organization stats and bulk delete for the platform dashboard. No MikroTik/FreeRADIUS integration exists here â€” channel/frequency/encryption/status are only described as "updated via sync", and per-AP stats are hardcoded zeros.

### Files
`app/modules/access_point/__init__.py` â€” 0 lines â€” EMPTY_STUB â€” no blueprint export or registration hook; package defines nothing.
`app/modules/access_point/controller.py` â€” 294 lines â€” JSON API controller â€” 13 methods, each re-wraps already-parsed UUIDs and re-applies `@token_required`.
`app/modules/access_point/repository.py` â€” 256 lines â€” SQLAlchemy repository â€” org-scoped, parameterised queries over `AccessPoint`; soft delete sets `status='deactivated'`.
`app/modules/access_point/routes.py` â€” 103 lines â€” API blueprint â€” `ap_bp` at `/api/v1/access-points`, 12 rules, module-level controller instance.
`app/modules/access_point/schemas.py` â€” 130 lines â€” Marshmallow schemas â€” create/update validators plus two schemas that are never imported.
`app/modules/access_point/service.py` â€” 192 lines â€” service layer â€” business rules; `get_ap_stats` returns hardcoded zeros with a TODO.
`app/modules/access_point/web_routes.py` â€” 357 lines â€” Jinja web routes â€” `ap_web_bp` at `/organization/<org_id>/access-points`, membership-gated, renders `web/access_point/*.html`.

### Defects (ranked)
`controller.py:create/get/update/delete/get_stats/get_by_router` â€” `UUID(x)` re-applied to `uuid.UUID` from Flask's `<uuid:...>` converter and `fields.UUID`; TypeError escapes the `except ValueError` and becomes 500, so every single-AP API endpoint is dead.
`controller.py:list` + `repository.py:count_by_organization` â€” count takes only `status`, so router-filtered pages report unfiltered totals.
`service.py:create_access_point` (+controller create, web create/edit) â€” `router_id` accepted from client without verifying it belongs to the caller's organization: cross-tenant router reference.
`repository.py:update` + `service.py:update_access_point` â€” both drop `None` values, so ip_address/description/hotspot_server_id can never be cleared.
`repository.py:update_status` â€” writes the error text into `description`, destroying operator data.
`web_routes.py:index` â€” `search` is read and passed to the template but never to the query: search box is a no-op.

### Stubs
`__init__.py` empty; `get_ap_stats` zero metrics + TODO; `AccessPointFilterSchema` and `AccessPointBulkActionSchema` unused; `bulk_update_status` and `validate_mac_address` never called; "sync process" that would set channel/frequency/encryption/status has no producer.

---

## G29 â€” hotspot + web shell

### Summary
`hotspot_bp` (`/hotspot`) serves the public MikroTik captive portal: tenant lookup by NAS IP, themed `hotspot/portal.html`, public plans, M-Pesa STK purchase, voucher redeem/pre-check, receipt self-verification and payment polling via BillingService, SubscriberService, PaymentService. `web_bp` (no prefix) is the browser shell: signup/verification JSON APIs, session login, organization and super-admin dashboards fed by ReportingService plus billing/router/AP repositories. It is the tenant-facing front door of the platform, but its second public `/hotspot/<slug>` implementation collides with the real one and payment polling is unscoped.

### Files
`app/modules/hotspot/__init__.py` â€” 0 lines â€” EMPTY_STUB â€” package exports no `hotspot_bp`; blueprint exists only in `routes.py`, so `from app.modules.hotspot import hotspot_bp` fails.
`app/modules/hotspot/routes.py` â€” 324 lines â€” public captive-portal blueprint â€” defines `portal_default`, `portal`, `get_plans`, `initiate_payment`, `redeem_voucher`, `verify_mpesa_code`, `check_payment_status`, `voucher_info`; no CSRF or rate limiting.
`app/modules/web/__init__.py` â€” 5 lines â€” blueprint package init â€” `web_bp` with relative template/static folders, no url_prefix, bottom-imports routes.
`app/modules/web/routes.py` â€” 624 lines â€” web shell routes â€” signup APIs under `/api/*`, `/login`, dashboards, `/logout`, plus duplicate `/hotspot/<slug>` portal/connect/plans/purchase/status.

### Defects (ranked, max 6)
`app/modules/hotspot/__init__.py` (empty) vs `routes.py:hotspot_bp` â€” no package-level blueprint export; app-factory import raises ImportError, so the captive portal is unreachable unless `routes` is imported directly.
`app/modules/web/routes.py:hotspot_portal/hotspot_plans` â€” identical URL rules to `hotspot/routes.py:portal/get_plans` since `web_bp` has no prefix; only the first-registered blueprint matches, the other is dead, and templates differ (`web/hotspot/index.html` vs `hotspot/portal.html`), while its `redeem_voucher`/`get_active_subscription` calls disagree with hotspot's signatures.
`app/modules/hotspot/routes.py:check_payment_status` â€” `payment_service.get_transaction(txn_uuid)` is not tenant-scoped; any org slug plus a transaction UUID returns status and `mpesa_receipt`, leaking other tenants' payments.
`app/modules/hotspot/routes.py:initiate_payment` â€” `get_plan(plan_uuid, org.id)` is unchecked before `float(plan.price)` (AttributeError â†’ 500 on unknown plan), and no public/active check lets any org plan UUID be bought.
`app/modules/web/routes.py:login` â€” unvalidated `next` becomes `session['next_url']` then `redirect(next_url or ...)`, an open redirect; tokens are also written into the client-readable session cookie with no rotation, and `error=str(e)` leaks internals.
`app/modules/web/routes.py:organization_dashboard` â€” `UUID(org_id)` unguarded (500 on bad path), no explicit `user.organization_id == org_id` check, repo access via `subscription_repo`/`voucher_batch_repo`, and unguarded calls to expiring-voucher/router/AP lookups.

### Stubs
- `hotspot/__init__.py` â€” 0 bytes; should define `hotspot_bp` and import routes.
- `hotspot/routes.py:_get_router_from_request` â€” dead helper, never called (duplicate of inline lookup).
- `web/routes.py:organization_dashboard` â€” `subscriber_growth: 0`, `session_growth: 0` placeholders.
- `web/routes.py` line 1 scaffolding comment; unused `uuid`/`current_app` imports; trailing blank lines 621â€“624.

---

## G30 â€” tasks + utils

### Summary
A single 1054-line Celery task module plus ten empty/comment-only stubs. It owns the platform's background lifecycle work: marking `Subscription` rows expired/disconnected, removing FreeRADIUS entries via `RadiusSyncService`, terminating `ActiveSession` rows, purging `RadiusCache`, disconnecting hotspot/PPPoE users via `MikroTikClient`, and emailing subscribers/admins. It is the async half of billing and access control â€” but it is entirely shadowed by its own package, so none of it registers or runs.

### Files
- `app/tasks.py` â€” 1054 lines â€” Celery task module (shadowed) â€” 13 `@shared_task`s (expiry/renew/cascade, session+radius cleanup, router health, 3 billing reports); each calls `create_app()`; ORM-only, no raw SQL; global, unscoped queries.
- `app/tasks/__init__.py` â€” 0 lines â€” EMPTY_STUB â€” no re-exports; wins import resolution over `tasks.py`, so every task name is unimportable.
- `app/tasks/celery_app.py` â€” 1 line â€” stub â€” `# Celery app` only; no `Celery()` instance, no `beat_schedule`.
- `app/tasks/cleanup.py` â€” 1 line â€” stub â€” `# Cleanup tasks`; intended home for `cleanup_expired_sessions`/`cleanup_radius_cache`.
- `app/tasks/expiry.py` â€” 1 line â€” stub â€” `# Expiry tasks`; no expiry implementation.
- `app/tasks/notifications.py` â€” 1 line â€” stub â€” `# Notification tasks`; nothing implemented.
- `app/tasks/sync.py` â€” 1 line â€” stub â€” `# Sync tasks`; no router-health/RADIUS sync.
- `app/utils/__init__.py` â€” 0 lines â€” EMPTY_STUB â€” exports nothing.
- `app/utils/decorators.py` â€” 1 line â€” stub â€” no auth/tenant/role guards.
- `app/utils/helpers.py` â€” 1 line â€” stub â€” no shared helpers.
- `app/utils/validators.py` â€” 1 line â€” stub â€” no phone/email/IP validators.

### Defects (ranked, max 6)
1. `app/tasks.py` (whole module) â€” package `app/tasks/` with an empty `__init__.py` shadows it; `import app.tasks` resolves to the package, so all 13 tasks are never registered and `from app.tasks import check_plan_expiration` raises ImportError.
2. `app/tasks/celery_app.py` â€” no Celery app or `beat_schedule` exists anywhere in the slice, so every documented cadence (02:00, 03:00, 04:00, 30 min, hourly, 15 min) never fires; `celery -A app.celery_app` per the module docstring has nothing to load.
3. `disconnect_expired_plans` vs `expire_subscriptions_cascade` â€” duplicated cascades with contradictory statuses (`'disconnected'` vs `'expired'`) over the same `status=='active'` rows; the 15-minute job consumes them, so the daily job is a no-op.
4. Missing tenant scoping â€” `Router`, `Subscription`, `ActiveSession`, `Invoice`, `Voucher` queries are platform-wide with no `organization_id` filter, yet `_build_billing_report` emails one global `ADMIN_EMAIL`; cross-tenant revenue leak.
5. `auto_renew_plans` â€” extends `expiry_time` and commits with no invoice, debit or payment check; RADIUS sync failure only logs, leaving DB and FreeRADIUS divergent.
6. `_disconnect_from_mikrotik` â€” uses `subscriber.login_username` instead of the `phone if hotspot else username` rule; the AttributeError is swallowed by `except Exception: pass`, so MikroTik disconnects silently no-op.

Also: `check_plan_expiration`'s 2-hour reminder window on a daily run misses ~22/24 of expiries; `send_expiration_reminder` is dead code; weekly/monthly report windows contradict their docstrings; naive `utcnow()` throughout.

### Stubs
`app/tasks/__init__.py`, `app/tasks/celery_app.py`, `cleanup.py`, `expiry.py`, `notifications.py`, `sync.py`, `app/utils/__init__.py`, `decorators.py`, `helpers.py`, `validators.py` â€” all zero- or one-line comment files.

---

## G31 â€” templates: shell/auth/org/dashboard

### Summary
The Jinja presentation layer: one public shell (base.html) hosting landing, login and registration, a three-step email-verification funnel wired to `/api/v1/auth/*`, a standalone Bootstrap super-admin page, a Tailwind tenant shell whose sidebar `url_for`s every tenant blueprint, and tenant dashboard/reports pages fed by `/api/v1/dashboard` and `/api/v1/reports`. It sits between the view layer (context: `user`, `organization`, `stats`, `expiring_subscriptions`, `voucher_batches`, `recent_routers`, `recent_aps`) and the JSON APIs, implementing almost no validation itself. Three parallel shells coexist, and the fragment set under `dashboard/` plus all of `auth/` is empty, so only the legacy `web/login.html` and `web/register.html` are renderable.

### Files
All paths under `app/templates/web/`.
`base.html` â€” 569 â€” public shell â€” fixed navbar/footer, jQuery+AOS IIFE defining `showToast`/`showLoading`/`hideLoading`; unguarded `AOS.init`; `#features` anchor has no target.
`dashboard.html` â€” 264 â€” standalone super-admin page â€” renders `user.first_name/email/role`; counts hardcoded 0/1/0/0; sidebar `#`.
`index.html` â€” 279 â€” landing â€” captures email, POSTs `/api/v1/auth/send-verification`, stores `verificationEmail`, injects modal.
`login.html` â€” 149 â€” login â€” POSTs `email`/`password` to same URL, no CSRF, `#remember` has no name.
`register.html` â€” 792 â€” org signup â€” ~470 lines own CSS; gates on `verificationEmail`; POSTs `/api/v1/auth/register-organization`.
`verify_email.html` â€” 163 â€” verification â€” `const token='{{ token }}'`; POSTs `/api/v1/auth/verify-email`; stores `verified_email`.
`super_admin/dashboard.html` â€” 264 â€” duplicate â€” identical MD5 (57A90DC8â€¦) to `dashboard.html`.
`organization/base.html` â€” 451 â€” tenant shell â€” sidebar `url_for`s `subscriber_web`, `router_web`, `ap_web`, `network_web`, `billing_web`, `web.*`; flash + dropdowns.
`organization/dashboard.html` â€” 320 â€” tenant dashboard â€” `stats` cards, expiring/voucher/router/AP lists, 30s auto-refresh.
`organization/reports.html` â€” 468 â€” tenant analytics â€” six Chart.js charts from `/api/v1/reports/*`; output in undefined `scripts` block.
`organization/create.html` â€” 0 â€” empty stub.
`organization/edit.html` â€” 0 â€” empty stub.
`organization/show.html` â€” 0 â€” empty stub.
`auth/forgot_password.html`, `auth/login.html`, `auth/profile.html`, `auth/register.html`, `auth/reset_password.html` â€” 0 each â€” empty stubs.
`dashboard/index.html`, `dashboard/layout/{sidebar,header,footer}.html`, `dashboard/components/{stats_cards,charts,activity_table}.html` â€” 0 each â€” empty stubs.
`vouchers/struc` â€” 82 â€” stale ASCII tree â€” points at `app/modules/web/templates/` and plural `organizations/`, which do not exist.

### Defects
1. `organization/reports.html:{% block scripts %}` â€” parent defines only `extra_css`/`extra_js`, so Jinja drops the whole block: Chart.js never loads, all nine `load*` functions never run, page shows permanent skeletons.
2. `organization/dashboard.html:fetchDashboardData` â€” `querySelector('.stat-value')` matches nothing (cards use `text-xl` classes); TypeError swallowed by `catch`, auto-refresh never updates a value.
3. Auth/verification key split â€” `verify_email.html` writes `verified_email`, `register.html` reads `verificationEmail`; only `?email=` rescues the funnel, session-only carry-over breaks.
4. `organization/reports.html:fetchJSON` â€” builds `Bearer undefined` without the cookie and reads the JWT from `document.cookie`; also calls `/api/v1/reports/*` with no `org_id`, unlike `?org_id=` on the stats call.
5. `dashboard.html` vs `super_admin/dashboard.html` â€” byte-identical duplicates; a fix to one leaves the other stale.
6. `base.html` â€” unguarded `AOS.init()`: a CDN failure kills the IIFE, so mobile menu, `showToast`, `showLoading` are undefined and `index.html` submit throws.

### Stubs
Organization create/edit/show; all five `auth/*` pages; `dashboard/index.html` + six layout/component partials; hardcoded 0/1/0/0 stats and `#` nav links in both dashboard copies; `vouchers/struc` manifest.

---

## G32 â€” templates: subscriber

### Summary
The subscriber UI: list, detail, edit, hotspot and PPPoE signup, plan assignment, renewal, cancel/delete and device registration â€” Jinja pages extending `web/organization/base.html`, driven by the `subscriber_web` blueprint. `index.html` is the hub, `show.html` the aggregate detail view, `create_pppoe.html`/`create_hotspot.html` the only real creation paths. It assumes model helpers (`display_name`, `get_full_name()`, `active_subscription`, `validity_timedelta`) and server-side RADIUS/session effects.

### Files
`app/templates/web/subscriber/add_device.html` â€” 327 lines â€” add-device form â€” POSTs mac_address/device_name/device_type, inline MAC validation, no CSRF; two handlers fight over `#previewText`.
`app/templates/web/subscriber/create.html` â€” 0 lines â€” empty stub.
`app/templates/web/subscriber/create_hotspot.html` â€” 240 lines â€” hotspot signup â€” phone (required) + name; `validatePhoneNumber` has three successive `normalized` assignments and rejects every valid 07/01 number.
`app/templates/web/subscriber/create_pppoe.html` â€” 404 lines â€” PPPoE signup â€” username/password/names/phone/plan_id, password-strength meter; phone regex rejects 12-digit 254 numbers; password re-echoed into `value`.
`app/templates/web/subscriber/create_subscription.html` â€” 370 lines â€” assign-plan chooser â€” plan cards, radios `name=plan_id`, hidden `subscriber_id`, `auto_renew`; `selectPlan` bound twice; `plan.setup_fee > 0` raises on NULL.
`app/templates/web/subscriber/edit.html` â€” 373 lines â€” edit form â€” names/email always, phone hotspot-only, credentials PPPoE-only, status select; fragile `.p-3.rounded-lg.text-xs` banner selector.
`app/templates/web/subscriber/index.html` â€” 341 lines â€” subscriber list â€” stats/tabs/GET filter/pagination/delete modal; unencoded pagination query; `subscriber.active_sub`.
`app/templates/web/subscriber/pppoe.html` â€” 309 lines â€” PPPoE list â€” client-side username filter, soft-delete modal; `active_subscription`; self-recursive `showToast`.
`app/templates/web/subscriber/renew_subscription.html` â€” 329 lines â€” renewal form â€” Bootstrap classes on a Tailwind base; `plan_id` values `same`/`""`; summary hardcodes 30 days.
`app/templates/web/subscriber/show.html` â€” 446 lines â€” subscriber detail â€” devices, subscription history, renew/cancel/delete modals; modals dereference `active_subscription` unguarded.
`app/templates/web/subscriber/components/devices_list.html` â€” 0 lines â€” empty stub.
`app/templates/web/subscriber/components/subscription_card.html` â€” 0 lines â€” empty stub.

### Defects (ranked, max 6)
`show.html:renewModal/cancelSubModal` â€” rendered outside `{% if active_subscription %}` yet read `active_subscription.plan`/`.id`, so a subscriber without a subscription raises UndefinedError (500).
`create_hotspot.html:validatePhoneNumber` â€” final `'2547'+substring(3)` yields 11 digits, fails `/^254\d{9}$/`, and the blur handler writes the corrupt value into the field; all 07/01 numbers are rejected.
`create_pppoe.html:validatePhoneNumber` / `edit.html:validatePhoneNumber` â€” `/^(254|07|01)\d{8}$/` expects 254+8 digits, so legitimate 12-digit `254XXXXXXXXX` values fail at signup and on edit.
`index.html` / `pppoe.html:openDeleteModal` â€” delete action hardcodes `/organization/<id>/subscribers/${id}/delete` instead of `url_for`; names interpolated into `onclick` without `|tojson` allow quote-breakout JS injection.
`index.html` `subscriber.active_sub` vs `pppoe.html`/`show.html` `subscriber.active_subscription` â€” one attribute name is not on the model, so the Plan column and "With Active Plan" stat render empty.
`pppoe.html:showToast` â€” the declaration is itself `window.showToast`, so the guard is always true and any toast recurses to stack overflow; the fallback style block is dead. No POST form in the slice emits a CSRF token.

### Stubs
- `create.html` (0 bytes): generic create-subscriber page; rendering it serves a blank 200.
- `components/devices_list.html` (0 bytes): device-row partial duplicated inline in `show.html`.
- `components/subscription_card.html` (0 bytes): subscription summary partial duplicated in `show.html` and `create_subscription.html`.

---

## G33 â€” templates: billing

### Summary
This slice is the billing module's Jinja2 presentation layer: a Chart.js analytics dashboard with router-health panel, invoice and subscription lists with period/status filters and bulk remind or auto-renew fetches, plan CRUD forms, and prepaid voucher single/batch creation with a tabbed batch detail page. All pages extend `web/organization/base.html` and build URLs through the `billing_web` blueprint with `org_id`. It holds no SQL or business rules; rendering depends on view-supplied dicts (`stats`, `invoice_stats`, `pagination`, `form_data`, plan and voucher lists) and assumes the server derives tenant scope from the session.

### Files
`app/templates/web/billing/dashboard.html` â€” 624 lines â€” analytics page â€” `stats.*` cards, 4 Chart.js canvases, `router_health`, `expiring_soon`, `auto_renewal`; `|tojson|default('[]')` ordering broken; CDN script without SRI.
`app/templates/web/billing/invoices/index.html` â€” 371 lines â€” list page â€” daily/weekly/monthly period picker, `invoice_stats`, status tabs all/paid/pending/overdue, `.inv-checkbox` bulk remind; pagination drops month/week/date.
`app/templates/web/billing/invoices/show.html` â€” 0 lines â€” empty stub â€” target of `billing_web.invoice_show`.
`app/templates/web/billing/plans/create.html` â€” 316 lines â€” form page â€” posts name/plan_type/billing_cycle/validity_*/bandwidth/price/is_active/is_public with JS field toggling.
`app/templates/web/billing/plans/edit.html` â€” 370 lines â€” form page â€” bound to `plan.*`, peer-toggle status switches; weaker validation than create.
`app/templates/web/billing/plans/index.html` â€” 539 lines â€” list page â€” three duplicated plan tables, search and auto-renew filter, deactivate modal with hardcoded action URL.
`app/templates/web/billing/subscriptions/index.html` â€” 353 lines â€” list page â€” `row_status` derived from `days_remaining`, bulk `send_reminders`/`enable_auto_renew` via `/api/v1/billing/subscriptions/bulk`.
`app/templates/web/billing/subscriptions/show.html` â€” 0 lines â€” empty stub â€” target of `billing_web.subscription_show`.
`app/templates/web/billing/vouchers/batch_create.html` â€” 337 lines â€” form page â€” `batch_name`/`plan_id`/`quantity` (max 1000)/optional validity override; uses non-stock `escapejs` filter.
`app/templates/web/billing/vouchers/batch_detail.html` â€” 322 lines â€” detail page â€” active/used/expired tabs over `vouchers|selectattr('status')`, local `showToast` self-recurses.
`app/templates/web/billing/vouchers/create.html` â€” 303 lines â€” form page â€” plan select, `max_uses`, `activation_type` immediate/first_use, `escapejs` + raw `plan.price`.
`app/templates/web/billing/vouchers/index.html` â€” 267 lines â€” list page â€” singles/batches tabs from `request.args`, client search, `copyCode` toast, page-local `head_extra` CSS.

### Defects (ranked, max 6)
`invoices/show.html` / `subscriptions/show.html` â€” 0-byte templates behind live `url_for('billing_web.invoice_show')` and `url_for('billing_web.subscription_show')` links; every detail click raises `TemplateNotFound` (500).
`vouchers/batch_detail.html:showToast` â€” calls `window.showToast`, i.e. itself; unconditional recursion, RangeError on copy, fallback unreachable.
`invoices/index.html:bulkSendReminders` (and `subscriptions/index.html:bulkAction`) â€” call `showLoading`/`hideLoading`/`showToast` with no local definition; ReferenceError in `.then` re-throws in `.catch`, overlay sticks, no toast.
`plans/index.html:openDeleteModal` / `invoices/index.html:sendInvoiceReminder` â€” Jinja interpolated unescaped into single-quoted `onclick` JS strings (`|e` decodes back to `'`); apostrophes break the handler and enable stored XSS.
`dashboard.html:revenueChart` â€” `{{ stats.revenue_labels|tojson|default('[]') }}` runs `tojson` before `default`; an Undefined attr raises TypeError, so the fallback never applies and any missing chart key 500s.
`invoices/index.html` â€” CSRF-less fetch/forms plus a status vocabulary of all/paid/pending/overdue that cannot match the rendered draft/sent/overdue/cancelled states; `pending` returns nothing and `sent`/`draft` are unfilterable.

### Stubs
- `invoices/show.html`, `subscriptions/show.html`: 0 bytes.
- Dashboard CSV export items are `href="#"`; `#invoices` anchors have no matching element.

---

## G34 â€” templates: router

### Summary
Eleven Jinja2 templates render the `router_web` blueprint (prefix `/organization/<org_id>/routers`): the router inventory with stat tiles and client-side filters, a create page that posts to `/api/v1/routers` and surfaces the RADIUS secret, an edit page managing `radius_secret`/`radius_config_status`, a fleet health table polling `/api/v1/routers/stats`, a tabbed detail view, and create/edit forms for `HotspotServer` and `PppoeServer`. All extend `web/organization/base.html` and are pure presentation, but they are where MikroTik operations fire â€” test connection, sync, discover, check/retry RADIUS, regenerate secret. Quality is mixed: duplicated inline CSS, contradictory RADIUS server addresses, invalid nested forms, dead JS on the create path, no CSRF tokens, two unusable component stubs.

### Files
`app/templates/web/router/create.html` â€” 942 lines â€” Jinja page â€” submit handler bound to undefined `form`, so the AJAX create + RADIUS modal path never runs.
`app/templates/web/router/edit.html` â€” 555 lines â€” Jinja page â€” retry/regenerate RADIUS forms are nested inside the outer edit form.
`app/templates/web/router/health.html` â€” 426 lines â€” Jinja page â€” reads `router.cpu_load`/`memory_usage` as columns; 60s auto-refresh.
`app/templates/web/router/hotspot_create.html` â€” 126 lines â€” Jinja page â€” HotspotServer create; no `action`, no `form_data`, no CSRF.
`app/templates/web/router/hotspot_edit.html` â€” 146 lines â€” Jinja page â€” prefilled HotspotServer edit; near-mirror of create.
`app/templates/web/router/index.html` â€” 222 lines â€” Jinja page â€” 6 stat tiles, client filters, pagination, JS-built delete URL.
`app/templates/web/router/pppoe_create.html` â€” 112 lines â€” Jinja page â€” PppoeServer create; mtu 1492, max_sessions 100.
`app/templates/web/router/pppoe_edit.html` â€” 122 lines â€” Jinja page â€” prefilled edit; prints literal `None` for NULL numbers.
`app/templates/web/router/show.html` â€” 327 lines â€” Jinja page â€” tabbed detail; health from `router.settings['health']`, full secret printed.
`app/templates/web/router/components/connection_status.html` â€” 0 lines â€” empty stub.
`app/templates/web/router/components/status_badge.html` â€” 1 line â€” empty stub â€” content is the literal text `status_badge.html`.

### Defects (ranked, max 6)
`create.html:825 form.addEventListener` â€” `form` is undefined (id is `routerForm`); ReferenceError kills the handler, so AJAX create fails and native POST wins.
`edit.html:473/502 nested <form>` â€” inner retry/regenerate forms are dropped by the parser; buttons submit the outer edit form, so RADIUS actions never fire.
`show.html:296 radius terminal` â€” full `router.radius_secret` printed plaintext, and server address hardcoded `10.0.0.1` vs `163.245.217.16` in create/edit; generated commands cannot work.
`health.html:241 / index.html:154 inline onclick` â€” `|e` is HTML-escaping, not JS-escaping; an apostrophe in a router name breaks out of the handler (XSS).
All POST forms â€” no CSRF token rendered anywhere in the slice; write endpoints are either 400 under CSRFProtect or exposed.
`health.html:361/380/400 fetch` â€” no Authorization header, tenant scope from client `org_id` query param; create.html uses a Bearer localStorage token instead.

### Stubs
- `components/connection_status.html` â€” 0 bytes; meant to hold the `.status-ring` indicator now inlined in show.html.
- `components/status_badge.html` â€” placeholder text only; status pill duplicated across index/health/show.
- No shared macros for form fields, buttons, or pills; `.form-control`/`.pill` re-declared in five templates.

---

## G35 â€” templates: hotspot / access_point / network

### Summary
This slice is the UI surface for the platform's hotspot and WiFi-infrastructure domains: two public captive-portal clients (theme-driven `portal.html` and the older jQuery wizard `hotspot/index.html`), a bare `error.html`, four access-point screens and three network screens plus a 0-byte edit stub. All operator pages extend `web/organization/base.html` and are tenant-scoped only through `organization.id` / org slug embedded in `url_for` and hardcoded delete URLs, assuming controller-supplied context (`access_points`, `routers`, `stats`, `pagination`, `form_data`, `pre_selected_router`, `theme`, `plans`, `client_mac`). They touch no ORM or RADIUS code directly â€” they POST to `ap_web`/`network_web`/hotspot routes and poll M-Pesa status, so failures surface as TemplateNotFound, pagination TypeError, JS breakage or dead controls.

### Files
`web/hotspot/error.html` â€” 36 lines â€” captive-portal error page â€” renders raw `{{ message }}` with no default, retry link or branding.
`web/hotspot/index.html` â€” 656 lines â€” hotspot signup wizard â€” extends bare `base.html`; posts `/connect`, `/plans`, `/purchase`, polls `/api/v1/payments/.../verify`; fabricates MAC.
`web/hotspot/portal.html` â€” 925 lines â€” standalone mobile portal â€” self-contained CSS/JS from `theme.*`; tabs POST `/pay`, `/redeem`, `/verify-mpesa`, poll `/status/<tx>`.
`web/access_point/create.html` â€” 406 lines â€” create form â€” POST `router_id/name/ssid/mac_address/ip_address/location/description/is_active`; ~300 lines duplicated CSS.
`web/access_point/edit.html` â€” 409 lines â€” edit form â€” mirrors create but all fields optional; links `ap_web.show`; CSS duplicated verbatim.
`web/access_point/index.html` â€” 450 lines â€” AP list â€” client-side filters over `data-*`; pagination keeps only status; delete posts hardcoded URL.
`web/access_point/show.html` â€” 451 lines â€” AP detail â€” `.strftime` on `last_seen_at`; Bootstrap delete modal to `ap_web.delete`.
`web/access_point/stats.html` â€” 26 lines â€” AP statistics â€” counters plus `aps_with_issues`; needs `|datetimeformat` and undefined `status-badge` classes.
`web/network/create.html` â€” 267 lines â€” create network â€” POST `name/slug/type/description/is_active/settings`; JS slug + JSON validation.
`web/network/edit.html` â€” 0 lines â€” empty stub â€” no edit-network template at all.
`web/network/index.html` â€” 240 lines â€” network list â€” GET filters, table, `**request.args` pagination, unreachable delete modal.
`web/network/show.html` â€” 319 lines â€” network detail â€” header, stats, settings JSON, routers/AP tabs; `scrollToTab` selector collides.

### Defects (ranked)
1. `network/edit.html` â€” 0-byte template; `url_for('network_web.edit')` from `network/index.html` and `network/show.html` raises TemplateNotFound on click.
2. `network/index.html:pagination` â€” `url_for('network_web.index', page=p, **request.args)` supplies `page` twice â†’ TypeError on any page â‰¥ 2.
3. `access_point/index.html:last_seen_at[:16]` vs `access_point/show.html:last_seen_at.strftime` â€” one breaks depending on datetime-vs-str; pagination links also drop `status`.
4. `hotspot/index.html:startTimer` â€” `clearInterval(timerInterval)` hits the TDZ of a const declared after first call â†’ ReferenceError for expired sessions; interval leaks.
5. All POST forms â€” no `{% csrf_token %}` (AP create/edit/delete, network create/delete) â†’ 400 under CSRFProtect; inline `onclick` interpolation of `ap.name`/`plan.name` breaks on apostrophes.
6. `hotspot/portal.html:ORG_SLUG` â€” `{{ theme.org_slug }}` unset yields `/hotspot//pay` 404; `data.error`/`plan_name` injected via innerHTML (DOM XSS); plans div renders even when `show_plans` is false.

### Stubs
- `network/edit.html` â€” empty.
- `hotspot/index.html` â€” `goBackToPhone()`, `subscriberId` unused.
- `network/index.html` â€” delete modal never invoked.
- `access_point/stats.html` â€” assumes external `status-badge` CSS.

---

## G36 â€” static css + tests + scripts

### Summary

This slice is the presentation-asset and developer-tooling edge of the ISP platform: one compiled Tailwind 3.4.0 stylesheet (3822 lines) that styles every Jinja page, a three-line Tailwind entry file, a test tree that collects nothing, and four ops scripts of which only one has code. It carries no application logic; the CSS encodes the product's visual vocabulary (brand greens `#2d6a4f`/`#40916c`, blue `#1a56db`, `w-64` sidebar, `peer-checked` toggle switch, table `min-w` up to 800px). The database bootstrap, demo seeding, backup and test coverage implied by these filenames exist only as intent, so the platform's safety net and operational runbook are missing while the UI layer is fully built.

### Files

`app/static/css/output.css` â€” 3822 lines â€” compiled CSS build artifact â€” Tailwind 3.4.0 preflight + utilities, unminified, no sourcemap; supports breakpoints smâ€“xl and a 1536px container.
`app/static/src/input.css` â€” 3 lines â€” Tailwind entry stylesheet â€” only `@tailwind base/components/utilities`; no component layer or `Inter` @font-face.
`tests/conftest.py` â€” 1 line â€” pytest config stub â€” comment only, no app/client/db fixtures.
`tests/org.py` â€” 102 lines â€” manual HTTP smoke script â€” hardcoded live credentials; prints only, asserts nothing.
`tests/e2e/test_example.py` â€” 1 line â€” empty stub â€” no browser flow.
`tests/integration/test_example.py` â€” 1 line â€” empty stub â€” no DB-backed test.
`tests/unit/test_example.py` â€” 1 line â€” empty stub â€” no unit tests.
`scripts/backup.py` â€” 1 line â€” empty stub â€” no pg_dump/restore.
`scripts/generate_encryption_key.py` â€” 11 lines â€” one-shot CLI utility â€” prints a Fernet key and `.env` snippet.
`scripts/init_db.sql` â€” 1 line â€” empty stub â€” no DDL; `#` is invalid PostgreSQL syntax.
`scripts/seed_data.py` â€” 1 line â€” empty stub â€” no seed rows.

### Defects

`tests/org.py:20` â€” live credentials `[redacted-email]` / `[redacted-password]` committed in plaintext and posted over http://; secret exposure, no TLS.
`tests/org.py:test_organization` â€” zero assertions; steps 3â€“6 gate on `status_code == 200`, so failures are skipped and it returns True, printing "All tests passed!" regardless; exit code always 0.
`tests/org.py:11` â€” `BASE_URL` hardcoded to `http://localhost:5000`, creates "Test Hospital" in the dev database with no teardown; permanent residue.
`tests/conftest.py` â€” no fixtures at all; any future test module fails at collection with "fixture not found".
`tests/{unit,integration,e2e}/test_example.py` â€” one comment each; pytest collects 0 tests, so CI is green with no coverage of auth, tenant scoping, RADIUS or M-Pesa.
`scripts/init_db.sql` â€” one comment line; `#` is not a PostgreSQL comment, so piping to psql errors before creating any table.

### Stubs

`tests/conftest.py`, `tests/unit/test_example.py`, `tests/integration/test_example.py`, `tests/e2e/test_example.py`, `scripts/backup.py`, `scripts/init_db.sql`, `scripts/seed_data.py` â€” one comment line each, no executable code. `app/static/src/input.css` is a three-line directive shell with no customization layer.
