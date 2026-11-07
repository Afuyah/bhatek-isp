# bhatek-isp refactor — plan and progress

Branch: `refactor/industry-grade` (work tree: `Documents\deepseek-harness\bhatekpos\work\bhatek-isp`).
Everything here is synced back into `Documents\deepseek-harness\bhatek-isp` at each milestone.

Goal: a clean, industry-grade, high-performance, real-time system — no dead files, no duplicated
implementations, no external CDN dependencies, consistent structure, fast request paths.

## Discovery baseline (measured, not estimated)

| Item | Before |
|---|---|
| Files (non-git) | 271 |
| Python files | 169 (42 with no executable statement) |
| Templates | 96 files (27 empty) |
| External CDN/font references | 14 distinct URLs, 7 hosts |
| Image assets | 4.89 MB (11 of 13 unused = 4.65 MB) |
| Inline `<style>`/`<script>` in templates | 35 templates over 3 KB; worst 28.6 KB |
| Unreferenced templates | 25 |
| Route decorators | 254 |
| Missing `render_template` targets | 10 |
| FK columns without an index | 38 of 43 |
| Cache call sites | 5 |
| Pagination (`.paginate`) | 0 (only `.limit()` ×59) |
| Real-time (WebSocket/SSE) | none — polling only (`setInterval` 1 s, 60 s) |
| `SEND_FILE_MAX_AGE_DEFAULT` / compression / cache config | absent |

## Phases

### Phase 1 — purge dead weight ✅ done (commit 4df9602)
- 24 unreferenced empty/junk templates deleted; 3 *referenced* empties kept for Phase 3.
- 12 zero-reference Python stubs deleted.
- 11 unused images deleted (4.65 MB freed).
- `app/tasks/` shadow package removed → the real 879-line task module is importable again.
- Celery entrypoints corrected to `app.celery_app` in Makefile + docker-compose.
- Hotspot render paths corrected (`web/hotspot/*`).
- Result: 219 files, 157 Python files, 0 syntax errors, 254 routes intact, missing templates 10 → 6.

### Phase 2 — structure and boot ✅ mostly done
- Blueprint registration, template filters and the middleware pipeline moved out of the factory:
  `app/core/blueprints.py`, `app/core/template_filters.py`, `app/core/middleware/__init__.py`.
  `app/__init__.py` is 177 lines (was 381).
- Dead, broken `register_middlewares()` (wrapped the app object, recursed) replaced by
  `install_middleware_stack()`.
- Time: `app/core/time.py` is now the single source of truth. 169 deprecated `datetime.utcnow()`
  calls across 32 files replaced with `utcnow()` (same naive-UTC semantics, no behaviour change),
  plus `in_timezone()` for display conversion to `Africa/Nairobi`. Deprecation warnings in the
  suite: 157 -> 0.
- Still to do: consolidate the duplicate implementations — two email transports
  (`integrations/email_service.py` vs `integrations/email/{service,brevo_service}.py`, the former is
  Celery-safe, the latter needs an app context) and three super-admin bootstrap paths
  (`tm.py`, `manage.py create_admin`, `AuthService.create_super_admin`).

### Phase 2b — responsive
- 20 of 23 tables already sit in `overflow-x-auto`; 27 fixed widths remain.
- Deliberately **not** attempted blind: tap targets, mobile nav and table behaviour need a browser to
  verify. Deferred to the verification pass rather than guessed at.

### Phase 3 — templates, de-CDN, responsiveness ✅ done except responsive polish
- Local SVG sprite (49 symbols) + `icon()` Jinja macro replaced 635 Font Awesome tags across 43 templates.
- `static/js/app.js` replaces jQuery in the public shell (navbar, mobile menu, smooth anchors, toasts, loader).
- `static/js/icons.js` renders dynamic icons in JS from the same sprite.
- `static/js/chart.js` is a local drop-in for the 10 Chart.js charts (line/bar/doughnut/pie, SVG, responsive).
- Shell CSS extracted to `static/css/app.css`; AOS replaced by CSS reveal + `prefers-reduced-motion`.
- Google Fonts removed everywhere; system font stack.
- Bootstrap removed from the 3 pages that loaded it (only ~8 utility classes were actually used).
- `web/super_admin/dashboard.html` deduped into `web/dashboard.html`.
- **Asset CDN references: 14 → 0.**
- 23 inline `<style>` blocks (84 KB) extracted to `static/css/pages/*.css`, duplicates merged.
  Trade-off: one extra cacheable request per page instead of 6–12 KB of per-response HTML;
  a win on repeat visits, roughly neutral on first load.
- Last jQuery consumer (`hotspot/index.html`, 30 call sites) converted to native DOM.
- Still to do: responsive polish (mobile nav/tables/tap targets); `hotspot/portal.html` keeps
  14.3 KB of inline CSS because it interpolates Jinja values.



### Phase 4 — performance ✅ mostly done (commits 4242af0, 5313596)
- `app/core/performance.py`: WSGI gzip (unit-verified, 8/8 cases) + static `Cache-Control: immutable`,
  `no-store` for API, `no-cache` for HTML, `X-Content-Type-Options`.
- `index=True` on all 43 foreign-key columns (was 5).
- Migration `a1f2c3d4e5f6`: 43 FK indexes + 8 composite indexes, inspector-guarded and re-runnable.
- `gunicorn.conf.py`: gthread workers sized for SSE, timeout 300 s (streams last up to 120 s),
  max-request recycling, preload, proxy-header handling.
- Docker: non-root, healthcheck, `.dockerignore`; compose now publishes 8080 (was 5000 while gunicorn
  listened on 8080 — the web container was unreachable), with healthchecks and a beat service.
- `app/core/cache.py`: Redis fragment cache that degrades to a miss (unit-verified), applied to
  network stats with invalidation on every write.
- Still to do: eager loading (`lazy='dynamic'` blocks it), 137 inline commits → request unit of work,
  authenticated dashboard reads.


### Phase 5 — real-time ✅ started (SSE for payments)
- `app/core/realtime.py`: `sse_response()` + `poll_stream()` (change-only frames, heartbeat, timeout,
  error frames). Unit-verified.
- `GET /hotspot/<slug>/status/<txn>/stream` pushes M-Pesa status; tenant-checked so one org can't
  observe another's transaction.
- Captive portal now uses `EventSource` with the original 3 s polling kept as a fallback for browsers
  without it; one connection instead of a request every 3 s.
- Router health: `GET /organization/<org_id>/routers/health/stream` (session-authenticated) pushes
  counters every 15 s. The old page polled `/api/v1/routers/stats`, a JWT endpoint a browser session
  could never authenticate to — so it could only ever receive a 401.
- Verified: both stream routes register, unauthenticated access redirects to `/login` (302),
  health page JS passes `node --check`.
- Still to do: document that SSE needs a threaded/async worker in production (the gunicorn config
  already uses `gthread`).


### Phase 6 — verification ◐ first real run done
The app now actually boots. Dependencies were installed into a local rig (`deps/`, gitignored) by
fetching wheels from PyPI with urllib — `pip` itself is blocked in this sandbox.

Measured, not inferred:
- `create_app()` succeeds; **257 routes** registered, **253 view functions**, **55 distinct endpoints**
  referenced by templates — **0 unresolved**.
- Public pages render: `/` 200, `/login` 200, `/register` 200, `/verify-email` 200,
  `/register-success` 200, `/health` 200.
- gzip confirmed end-to-end (`Content-Encoding: gzip` on HTML, CSS and JS).
- Static assets serve (app.css, output.css, app.js, chart.js).
- **Bug found and fixed:** browsers got raw JSON on 404/500 because the global JSON handler
  shadowed the HTML error pages. Now content-negotiated: HTML pages get `web/404.html` /
  `web/500.html`, API paths still get JSON. Verified: 404 → `text/html`, 20 KB, renders the shell.

Test suite: `tests/` now runs against SQLite (31 of 32 tables; `radacct` is skipped because SQLite
rejects its composite-PK autoincrement). `tests/support/sqlite_compat.py` compiles the three
Postgres-only column types for SQLite. **33 tests pass**, covering public pages, gzip, caching,
HTML-vs-JSON error pages, every operator page, auth protection, both SSE streams and endpoint
resolution.

Real bugs found by running the app (all fixed):
1. **Session auth was broken for every web page.** `session['user_id']` is a string; it was passed
   straight into `User.query.get(...)` on a PostgreSQL `UUID` column whose binder calls `.hex`.
   Every authenticated page would raise. Fixed with `app/core/database/identifiers.as_uuid()`,
   applied in `web_login_required` and `UserRepository.get_by_id`.
2. **`Network.to_dict()` crashed** — a regression from converting relationships off `lazy='dynamic'`:
   `self.routers.count()` became `list.count()` → TypeError. Now `len(self.routers)`.
3. **Three pages 500'd on missing template context** (`health`, `auto_renewal`, `stats`).
   `X.attr|default(...)` raises before the filter runs when `X` is undefined. Fixed those sites and
   switched the environment to `ChainableUndefined`, which is what the templates were written for.
4. Static responses did not carry `immutable` (Flask's own header won over `setdefault`).

### Phase 6b — verification (original plan)
- Boot the app in a venv, run migrations on empty Postgres, exercise auth + RADIUS + billing happy paths.
- Test suite: auth, tenant isolation (cross-tenant test per module), RADIUS accept/reject, accounting
  idempotency, billing math.
- CI: lint + tests + migration check.

## Guard rails
- Every phase is a separate commit on `refactor/industry-grade`; nothing is force-pushed.
- Each phase ends with: AST parse of all Python, route count unchanged or intentionally changed,
  template reference check, and a file-count delta recorded here.
- Deletions are only made after proving zero references (import graph + template graph).
