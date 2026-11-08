# bhatek-isp — verified structural findings

These facts were produced by direct inspection/execution against
`C:\Users\Bhatek Space\Documents\deepseek-harness\bhatek-isp` on 2026-11-06,
independently of the per-slice reader agents. Anything the slice reports add
on top of this; where a slice contradicts this file, this file wins.

## 1. Copy integrity

| Check | Result |
|---|---|
| Source | `C:\Users\Bhatek Space\Desktop\newseting\bhatek-isp` |
| Target | `C:\Users\Bhatek Space\Documents\deepseek-harness\bhatek-isp` |
| Files copied | 299 (including `.git`) |
| Directories | 81 |
| Size | 12.22 MB (source == target) |
| Name+size comparison | identical for all 299 files |
| Target before copy | existed, empty |

## 2. Repository

- Branch `master`, 101 commits, 2026-04-29 → 2026-08-02.
- `origin` = https://github.com/Afuyah/bhatek-isp (public).
- 271 tracked files. No README anywhere. No CI config, no pre-commit, no linter config.
- Authors: `afuyah` (71), `unknown` (16), `Afuyah` (7), `railway-app[bot]` (5), `Fly.io` (1).
- Recent history is dominated by "fix: ... invalid url_for() calls" commits — evidence the web layer has been patched reactively.

## 3. Shape of the codebase

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

## 4. Verified defects (reproduced by command, not inferred)

### 4.1 `app/tasks.py` is dead code — shadowed by an empty package
Both `app/tasks.py` (879 lines, all 12 real Celery tasks) and the package
directory `app/tasks/` exist. Python resolves the **package** first, so
`import app.tasks` loads `app/tasks/__init__.py` (0 bytes).

Consequences:
- `create_app()` sets `celery.conf.imports = ('app.tasks',)` → imports an empty package → **no task is ever registered**.
- All 11 `CELERY_BEAT_SCHEDULE` entries in `settings.py` target `app.tasks.<task>` → beat fires, workers answer `Received unregistered task`.
- `Makefile` and `docker-compose.yml` run `celery -A app.tasks.celery_app:celery`, but `app/tasks/celery_app.py` contains only the comment `# Celery app` → **worker crashes on start**.
- `app/integrations/mpesa/stk_push.py:177` does `from app.tasks.payment import query_payment_status` → `ModuleNotFoundError` (no `app/tasks/payment.py`).

### 4.2 Ten `render_template()` paths point at files that do not exist
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

### 4.3 Three routes render templates that are 0 bytes
`web/billing/invoices/show.html`, `web/billing/subscriptions/show.html`,
`web/network/edit.html` — the page renders blank (layout-less empty response).

### 4.4 One broken template inheritance
`app/templates/web/hotspot/index.html` starts with `{% extends "base.html" %}`,
but the file is `web/base.html`; template lookup is rooted at `templates/`, so
`base.html` does not resolve → `TemplateNotFound` for the hotspot landing page.

### 4.5 Three broken internal imports
| Location | Import | Why it fails |
|---|---|---|
| `app/integrations/mpesa/stk_push.py:177` | `from app.tasks.payment import query_payment_status` | module does not exist |
| `app/modules/auth/validators.py:3` | `from app.core.exceptions import ValidationError` | `core/exceptions/__init__.py` is empty; class lives in `.handlers` |
| `manage.py:28` | `from scripts.seed_data import seed_all` | `scripts/seed_data.py` is a one-line comment |

All three are in modules that nothing imports, so they are latent, not live.

### 4.6 Template `url_for()` targets — clean
All 190 `url_for('endpoint')` calls in templates resolve to a registered
endpoint (verified against the 250 collected endpoints). The url_for breakage
that recent commits chased is currently fixed.

### 4.7 The schema cannot be rebuilt from migrations
32 tables are declared across `app/models/*`; the single revision
`f248ffbfc9be_` has `down_revision = None` (i.e. it claims to be the base) yet
only does `op.drop_table('radpostauth')` plus a `batch_alter_table('routers')`.
`flask db upgrade` on an empty database fails at the first statement, and 31 of
32 app tables have no migration at all. Schema creation in practice depends on
`db.create_all()`-style bootstrapping that does not exist in the repo either
(`scripts/init_db.sql` is a 22-byte comment).

### 4.8 Live credential committed to a public repo
`alembic.ini:5`:
`sqlalchemy.url = postgresql://postgres:<32-char password>@interchange.proxy.rlwy.net:39029/railway`

- Present in the working tree **and** in git history (`git log -S` finds it introduced in `95c3ef4 first commit`).
- Pushed to `github.com/Afuyah/bhatek-isp`.
- This is a Railway-hosted Postgres. **Rotate it.** Removing it from HEAD does not remove it from history.

### 4.9 Placeholder files that look real
- **46 zero-byte files**: 19 `.py` (`__init__.py` for most packages, plus `app/modules/session/schemas.py`) and **27 templates** (all of `web/auth/*`, all of `web/dashboard/*` component/layout partials, `web/billing/invoices/show.html`, `web/billing/subscriptions/show.html`, `web/network/edit.html`, `web/organization/{create,edit,show}.html`, `web/subscriber/create.html` + both components, `web/vouchers/*`, `web/router/components/connection_status.html`).
- **23 more `.py` files contain only a comment**, no code: `app/tasks/{__init__,celery_app,cleanup,expiry,notifications,sync}.py`, `scripts/{backup,seed_data}.py`, `tests/conftest.py`, `app/utils/*`, `app/core/logging/formatters.py`, `app/core/config/{development,production,testing}.py`.
- Net: **42 of 169 Python files (25%) contain no executable statement.**
- `app/templates/web/vouchers/struc` is a leftover mojibake directory-tree planning note.

### 4.10 Dead modules (no importer anywhere)
52 app modules are never imported. The meaningful ones:
`app/utils/{helpers,decorators,validators}.py`, all four `validators.py`
(`auth`, `subscriber`, `router`, `payment`), `auth/permissions.py`,
`organization/permissions.py`, `core/extensions.py`,
`core/logging/formatters.py`, `reporting/{repository,schemas}.py`,
`session/schemas.py`, `integrations/mpesa/stk_push.py`,
`integrations/sms/{africa_talking,twilio}.py`, and the whole `app/tasks*` tree.

### 4.11 `register_middlewares()` is dead code that would break the app
`app/core/middleware/__init__.py` defines `register_middlewares(app)` which
wraps the **Flask app object** rather than `app.wsgi_app`, producing
`RecursionError` on every request. Nothing calls it — the real stack is built
inline in `app/__init__.py:264-272` (RequestID → Tenant → Auth over
`app.wsgi_app`), which is correct. Treat the middleware package's
`register_middlewares`/`get_middleware_stack` as poison: do not wire it up.

### 4.12 The app cannot boot in this environment (dependency gap, not a code bug)
`python -c "from app import create_app; create_app()"` fails immediately:

```
app/core/logging/logger.py:5: ModuleNotFoundError: No module named 'pythonjsonlogger'
```

Seven third-party imports are not installed here — `bcrypt`, `cryptography`,
`flask_redis`, `paramiko`, `pythonjsonlogger`, `requests`, `twilio`. All seven
*are* declared in `requirements.txt`, so this is a missing-venv problem, not a
repo defect. `pip install -r requirements.txt` was blocked by this session's
sandbox; it must be done in a real venv before any runtime testing.

### 4.13 Dependency manifest is not installable as written
- `requirements.txt` is entirely unpinned (no `==` anywhere) and lists
  `cryptography` twice (lines 30 and 53).
- `setup.py` does `install_requires=[line.strip() for line in open("requirements.txt")]`
  — that keeps `# Core`, `# Database` and blank lines, so `pip install .`
  raises `InvalidRequirement`.
- `make lint` calls `flake8`/`black`/`isort`, none of which are in requirements.

## 5. What the product is

A multi-tenant ISP management platform for Kenyan wireless ISPs, built as a
Flask monolith with four surfaces:

1. **JSON API** (`/api/v1/*`) — 11 blueprints: auth, organizations, networks,
   routers, access-points, subscribers, billing, payments, sessions, hotspot,
   reports.
2. **Server-rendered web UI** (`/…`) — 6 web blueprints serving Jinja pages for
   the same entities, organiser as an admin console.
3. **FreeRADIUS REST integration** (`/api/radius/*`) — `rlm_rest` calls back
   into the app for `authenticate`/`disconnect` and accounting; this is how
   MikroTik hotspots and PPPoE sessions actually get authorised.
4. **Celery workers** — plan expiry, auto-renewal, router health, RADIUS cache
   cleanup, session cleanup, billing reports.

Domain model: `Organization` → `Network` → `Router` (MikroTik) →
`HotspotServer`/`PPPoeServer`/`AccessPoint`; `Subscriber` → `Device`,
`Subscription` → `Plan`/`Invoice`/`Voucher`; `Transaction`/`Refund` for M-Pesa;
`ActiveSession` + FreeRADIUS `radcheck/radreply/radusergroup/radacct` for
live auth and accounting.

Billing is M-Pesa-first (STK push + callback), with vouchers as the prepaid
path and Brevo/SMTP email + Africa's Talking/Twilio SMS as notification
channels.
