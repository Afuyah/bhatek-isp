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

### Phase 2 — structure and boot (next)
- Create the 6 remaining templates: `web/404.html`, `web/500.html`, `web/error.html`,
  `web/register_success.html`, `web/network/stats.html`, `web/subscriber/hotspot.html`.
- Fill the 3 referenced-but-empty templates (`billing/invoices/show`, `billing/subscriptions/show`, `network/edit`)
  or point their routes at existing pages.
- Consolidate duplicate implementations: email (×2), admin bootstrap (×3), RADIUS reply builder (×2),
  Redis client probe (×2), the dead `register_middlewares`.
- Populate the empty `__init__.py` re-exports (`app.core.*`, `app.modules.*`) or delete the packages.
- Split `app/__init__.py` (381 lines) into `app/factory.py` + `app/celery.py` + `app/error_pages.py`.
- Replace `datetime.utcnow()` with timezone-aware UTC throughout; declare one timezone source.

### Phase 3 — templates, de-CDN, responsiveness ✅ de-CDN done (commits f37c79a, next)
- Local SVG sprite (49 symbols) + `icon()` Jinja macro replaced 635 Font Awesome tags across 43 templates.
- `static/js/app.js` replaces jQuery in the public shell (navbar, mobile menu, smooth anchors, toasts, loader).
- `static/js/icons.js` renders dynamic icons in JS from the same sprite.
- `static/js/chart.js` is a local drop-in for the 10 Chart.js charts (line/bar/doughnut/pie, SVG, responsive).
- Shell CSS extracted to `static/css/app.css`; AOS replaced by CSS reveal + `prefers-reduced-motion`.
- Google Fonts removed everywhere; system font stack.
- Bootstrap removed from the 3 pages that loaded it (only ~8 utility classes were actually used).
- `web/super_admin/dashboard.html` deduped into `web/dashboard.html`.
- **Asset CDN references: 14 → 0.** (`captive.apple.com` remains by design — it is captive-portal detection, not an asset.)
- Still to do: extract the 35 templates with heavy inline CSS/JS into static files; unify the remaining
  shell duplication; responsive polish.


### Phase 4 — performance ✅ started (commit 4242af0)
- `app/core/performance.py`: WSGI gzip (unit-verified, 8/8 cases) + static `Cache-Control: immutable`,
  `no-store` for API, `no-cache` for HTML, `X-Content-Type-Options`.
- `index=True` on all 43 foreign-key columns (was 5).
- Migration `a1f2c3d4e5f6`: 43 FK indexes + 8 composite indexes, inspector-guarded and re-runnable.
- Still to do: eager loading (`lazy='dynamic'` blocks it — needs relationship changes), Redis caching
  of hot reads, request-scoped unit of work instead of 137 inline commits, gunicorn tuning.

### Phase 5 — real-time ✅ started (SSE for payments)
- `app/core/realtime.py`: `sse_response()` + `poll_stream()` (change-only frames, heartbeat, timeout,
  error frames). Unit-verified.
- `GET /hotspot/<slug>/status/<txn>/stream` pushes M-Pesa status; tenant-checked so one org can't
  observe another's transaction.
- Captive portal now uses `EventSource` with the original 3 s polling kept as a fallback for browsers
  without it; one connection instead of a request every 3 s.
- Still to do: dashboard/router-health streams (replace the 60 s poll), and document that SSE needs
  an async worker (gevent) in production.


### Phase 6 — verification
- Boot the app in a venv, run migrations on empty Postgres, exercise auth + RADIUS + billing happy paths.
- Test suite: auth, tenant isolation (cross-tenant test per module), RADIUS accept/reject, accounting
  idempotency, billing math.
- CI: lint + tests + migration check.

## Guard rails
- Every phase is a separate commit on `refactor/industry-grade`; nothing is force-pushed.
- Each phase ends with: AST parse of all Python, route count unchanged or intentionally changed,
  template reference check, and a file-count delta recorded here.
- Deletions are only made after proving zero references (import graph + template graph).
