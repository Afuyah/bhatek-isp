# Moving the app from Fly.io to Railway

`railway.env` in the repository root holds every variable the application needs,
read from the **running Fly machine's process environment** (Fly secrets are
write-only through the API — `fly secrets list` only returns names and salted
digests, so the values had to come from inside the container).

`railway.env` contains live secrets and is git-ignored. Do not commit it.

## 1. Before importing

Open `railway.env` and replace every `<railway-domain>` with the domain Railway
assigns once the service exists (or your custom domain):

| Variable | Why |
|---|---|
| `BASE_URL` | used in emails, provisioning links and redirects |
| `BASE_DOMAIN` | host only, no scheme |
| `API_URL` | `BASE_URL` + `/api` |
| `CORS_ORIGINS` | must match the new origin exactly |
| `MPESA_CALLBACK_BASE_URL` | currently the placeholder `https://api.yourdomain.com` |

Two values were already wrong on Fly and are corrected in the file:

- `RADIUS_SERVER_IP` — was the stale `163.245.217.16`, now `10.0.0.1` (the VPS
  WireGuard gateway that actually serves RADIUS)
- `SMS_SENDER_ID` — was the placeholder `YourISP`

## 2. Import

Railway dashboard → your service → **Variables** → **Raw Editor** → paste the
contents of `railway.env` → Save. Or with the CLI:

```
railway variables --set "$(cat railway.env | grep -v '^#' | tr '\n' ' ')"
```

(any variable that came from the container's own image, such as `PATH` or
`PYTHON_VERSION`, was deliberately excluded)

## 3. Deploy

- Source: GitHub `Afuyah/bhatek-isp`, branch `master`
- `railway.json` sets the Dockerfile builder, the `/health` healthcheck and a
  single replica
- The container binds `$PORT` (gunicorn.conf.py honours Railway's `PORT`)

## 4. Database is already Railway

`DATABASE_URL` points at `mainline.proxy.rlwy.net:33316/railway` and
`REDIS_URL` at `hayabusa.proxy.rlwy.net:33992` — the same instances the app has
been using. All migrations are applied (head: `d9e0f1a2b3c4`), so a fresh
deploy needs no migration step.

**Do not run Fly and Railway at the same time.** Both would write to the same
database and both would serve RADIUS/provisioning; run one, then destroy the
other.

## 5. Things to fix after the move

- `RATELIMIT_STORAGE_URL` points at a *different* Redis instance
  (`nozomi.proxy.rlwy.net:19789`) than `REDIS_URL` (`hayabusa...`). That is
  probably unintended — decide which instance owns rate limiting.
- `MPESA_ENVIRONMENT=sandbox` and the placeholder callback: M-Pesa is not
  configured yet.
- `BREVO_API_KEY` is present and valid; `SMTP_PASSWORD` is a Brevo SMTP key.
  Both were verified working.
- Rotate `SECRET_KEY`, `JWT_*` and `ENCRYPTION_KEY` only knowingly:
  `ENCRYPTION_KEY` decrypts the stored router API passwords, so changing it
  makes existing routers unreachable until their credentials are re-entered.

## 6. After Railway is healthy

```
fly apps destroy bhatek-isp     # only after Railway serves traffic
```

Then point the custom domain (`*.bhalinks.com`) at Railway and update
`BASE_URL` / `BASE_DOMAIN` / `CORS_ORIGINS` / `API_URL` again to the final host.
