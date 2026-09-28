# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository shape

One Django 6 project (a modular monolith) serving a website made of independent products that share
an account, a credit balance and a layout. Django + HTMX. It started as two microservices
(`user-service/`, `qr_code/`) merged with `git subtree`; those were consolidated into this layout in
#47 — `README.md` explains why.

```
manage.py
config/               The Django project: settings, urls, api (one NinjaAPI), environment.py.
apps/core/            Site shell: base.html, landing page, ProductApp registry, admin site, email,
                      email verification, rate limiting (`apps.core.ratelimit`), client IP.
apps/accounts/        User model, auth pages + API, JWT auth classes.
apps/billing/         CreditAccount (balance) + CreditTransaction (ledger), credit services.
apps/qr_code/         QR codes: dashboard/editor pages, API, `/go/<code>` short links.
apps/file_transfer/   File transfer: logged-in + anonymous sending, dashboard/download pages,
                      S3 uploads, metering, "download all" zip, jobs.
admin/                Typer CLIs for lint/test/server/pip/openapi/aws/email/qrcode/filetransfer. Via `inv`.
tests_e2e/            Playwright end-to-end tests (separate `pytest_e2e.ini`).
conftest.py           Fixtures shared by every app's tests (`user`, `api_client`, ...).
pyproject.toml        Deps, ruff/ty/pytest/import-linter config, `inv` module registry.
.github/workflows/    CI (`ci.yml`): `inv lint all --check` then `inv test unit` on every push/PR.
```

`AGENTS.md` just redirects here.

## Commands

Tooling is exposed through `typer-invoke`, which mounts each `admin/*.py` module as an `inv`
subcommand group. The module registry is `[tool.typer-invoke].modules` in `pyproject.toml`.

```bash
inv lint all                  # ruff check --fix + ruff format + ty + import-linter
inv lint all --check          # CI mode: report only, non-zero exit
inv lint ruff <path>
inv lint ty [target]          # target: an app under apps/ or `admin`; omit to check everything
inv lint imports              # import-linter: the dependency rules between apps
inv test unit                 # all apps
inv test unit qr_code billing # some apps (directory names under apps/)
inv test e2e [--no-headless]  # Playwright, uses pytest_e2e.ini
inv server run [dev|prod]     # runserver on :8000
inv pip sync                  # uv sync --frozen, all groups
inv openapi generate --format json --file <path>
inv qrcode login dev <email> <password>       # JWT CLIs over /api/qr/ and /api/ft/
inv filetransfer send dev report.pdf --to a@example.com
```

Every command accepts `--dry` to print the shell command without running it. The app names `inv`
accepts come from `admin/django_app.py`, which discovers them from `apps/*/apps.py`.

`admin/server.py` and `admin/openapi.py` each define one typer command, which typer collapses away
when the module is run directly (`python3 -m admin.server`) but which `inv` preserves
(`inv server run`).

### Running tests directly

Run pytest from the repo root; `DJANGO_SETTINGS_MODULE=config.settings` and
`testpaths = ['apps', 'admin']` are set in `pyproject.toml`, and default addopts include
`--reuse-db --no-migrations`. `admin` is there for `admin/tests/` (currently
`admin/filetransfer.py`'s unit tests) -- plain HTTP-client CLI logic with no Django/database
dependency of its own, picked up by the same `pytest`/`inv test unit` run as every app's tests:

```bash
ENVIRONMENT=dev pytest apps/accounts/tests/unit/test_jwt.py::test_access_token
```

### Dependencies

`admin/pip.py` wraps `uv` over the single lockfile. Scopes are `main` (`[project.dependencies]`) and
`dev` (`[dependency-groups].dev`). There are no per-app groups: the site deploys as one unit.

```bash
inv pip sync dev                  # main + dev tools
inv pip package dev -p django     # upgrade one declared package
inv pip compile --clean           # delete uv.lock and re-lock from scratch
```

`admin/pip.py` validates `-p` names against what's declared, so update `pyproject.toml` first.

## Architecture

### Dependency rules between apps

```
qr_code | file_transfer   products; may not import each other
billing
accounts
core
```

Each app may import only from layers below it. Enforced by the import-linter contract in
`pyproject.toml` (tests are exempt). Consequences worth knowing:

- `core` can't import `accounts`, so the admin site's request type is `PermissionsMixin`, and the
  nav/landing page learn about products from the `apps.core.products` registry, which each
  product fills in its `AppConfig.ready()`.
- `accounts.User` has no `credits`; the balance lives in `billing.CreditAccount`. Change it only
  through `apps.billing.services` (`add_credits`, `spend_credits`, `apply_credits`,
  `get_balance`), passing `source='<app label>'` from a product. Templates get the balance as
  `credits_balance` from `apps.billing.context_processors.credits`.
- `billing` can't import a product either, so it exposes a `credits_added` signal
  (`apps.billing.signals`, sent from `add_credits` after commit) for a product to react to a top-up
  without `billing` knowing it exists. `file_transfer` listens for it to re-enable a user's
  suspended transfers (`apps/file_transfer/apps.py`).
- The same shape covers background jobs: `core` can't import a product to discover its jobs, so
  `apps.core.scheduler` is a registry (like `apps.core.products`) that a product fills from its own
  `AppConfig.ready()`. See "Queue and scheduler" below.
- Cross-app links in templates (`{% url 'billing:credits-history' %}` in a file_transfer template)
  are fine; they aren't imports.

### Settings and environment

`config/settings.py` is the only settings module. It calls `select_env()` from
`config/environment.py` and **raises on failure** rather than falling back to defaults — except
under pytest, which must not depend on machine-local config. `config/environment.py` must stay
Django-free (settings imports it before Django is configured); `admin/environment.py` is a thin
binding over it for the CLIs.

The convention: `.env.<environment>` at the repo root where `<environment>` ∈ {`dev`, `prod`}; if
`ENVIRONMENT` is set, use it; otherwise require exactly one `.env.*` file (ignoring
`.env.example` and `.env.staging`). `.env.*` is gitignored — copy `.env.example` to `.env.dev`. `apps/core/checks.py`
re-validates env selection and `EMAIL_BACKENDS` at `runserver` startup.

The deployments' env files are `.env.prod` and `.env.staging` at the repo root, next to `.env.dev`,
scp'd to `/opt/docker/ziptrigo-apps/<env>/.env` on the VPS. This repo is their only home; the
`infra` repo holds none. Both deployments run with `ENVIRONMENT=prod` (staging mounts its own file
as `.env.prod`), which is why `.env.staging` is in `IGNORED_ENV_FILE_SUFFIXES`. With `.env.dev` and
`.env.prod` both present, local runs need `ENVIRONMENT` set. All three are listed in
`admin/secrets_files.txt` for `inv secrets backup` / `restore`.

With `ENVIRONMENT=prod`, settings raise `ImproperlyConfigured` if `SECRET_KEY`, `JWT_SECRET` or
the file transfer S3 credentials (`FILE_TRANSFER_AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`) are
missing or still placeholders (`config/secret_checks.py`, also Django-free).

File transfer storage is one private S3 bucket per environment (`FILE_TRANSFER_S3_BUCKET`, keys
`transfers/<transfer_id>/<file_id>` -- deliberately not `.../<filename>` as spec section 11 says,
see `apps.file_transfer.services.storage`'s module docstring for why), defined with its CORS,
lifecycle rule and IAM user in the `infra` repo's `apps/ziptrigo-apps` (see its README); dev points
`FILE_TRANSFER_S3_ENDPOINT_URL` at Floci. Each part of a multipart upload carries its own SHA-256
checksum (`x-amz-checksum-sha256`, computed client-side and bound into the presigned URL's
signature -- see `services.uploads.presign_parts`), so that bucket's CORS config (issue #60) must
allow the `x-amz-checksum-sha256` request header and expose the `ETag` response header, or uploads
fail in the browser with a CORS error rather than an S3 one.

### Auth

One `AUTH_USER_MODEL`: `accounts.User` (UUID pk, email login, `status`). Two mechanisms coexist:

- **Django sessions** for the web UI: every page and every HTMX form view is `@login_required`
  (`LOGIN_URL='accounts:login'`). The login page is a session form view
  (`apps/accounts/views/login.py`, CSRF-protected, honours a same-site `next`); logout is
  POST-only. The browser never holds a JWT.
- **JWTs** for `/api/`, i.e. external clients like `admin/qrcode.py` and `admin/filetransfer.py`
  (`apps.accounts.auth.JWTAuth` / `AsyncJWTAuth` / `AdminAuth`, which also reject non-`ACTIVE`
  users). Claim `sub`, signed with `JWT_SECRET`, token classes in `apps/accounts/tokens.py`.

`POST /api/auth/login` only issues JWTs; it never starts a session. Register, forgot-password,
reset-password and resend-confirmation (issue #52) are session form views too, the same as login
-- CSRF-protected, POST-only, following the "HTMX form views" convention below; the browser never
sees a JWT on any of them. `/api/auth/…` still exposes the same operations (`signup`,
`forgot-password`, `reset-password`, `resend-confirmation`) for non-browser clients; both surfaces
share the same `apps.accounts.services` logic (`signup.create_account`,
`password_reset.get_password_reset_service`, `email_confirmation.get_email_confirmation_service`)
and the same `apps.core.ratelimit` rules, so validation and throttling can't drift between them.
Changing the email through `PUT /api/account` un-confirms the account and sends a new
confirmation email.

For typed views, use `AuthenticatedHttpRequest` / `MaybeAuthenticatedHttpRequest` from
`apps/accounts/http.py`.

### Email verification

`apps.core.services.email_verification` (issue #58) is generic email-address confirmation --
a code and a link -- for any app that needs to prove someone controls an email address without
necessarily having a user for it yet (`core` can't import `accounts`, and an anonymous
`file_transfer` sender isn't a user at all). It's keyed on
`(email, purpose)`, where `purpose` is a free string the caller picks (e.g.
`'accounts.email_confirmation'`) that `core` never interprets beyond scoping rows to it --
`confirm_by_code(id, code, purpose)` / `confirm_by_token(token, purpose)` both require it and
filter on it, so a code or token minted for one purpose can never confirm a row belonging to
another (indistinguishable from `NotFound` if it doesn't match). `start(email, purpose,
build_email=...)` generates a code and a token (via `secrets`, stored only as HMAC-SHA256 digests
keyed with `SECRET_KEY`, compared with `hmac.compare_digest`), emails them through the caller's
`build_email` callback (which builds the subject/body and any confirmation link from
`context.token` -- `core` doesn't know a caller's URL names) and `apps.core.services.email`, and
returns the new row's id; the two confirm functions return the verified email or raise a typed
`EmailVerificationError` subclass (`Expired`, `Burned` after too many wrong codes, `Superseded` by
a resend, `NotFound`, `AlreadyConfirmed`, `SendFailed`, or `IncorrectCode`/`ResendTooSoon` carrying
attempts-left/retry-after). Starting a new verification invalidates any previous pending one for
the same `email`/`purpose` -- only the latest is ever valid; `invalidate(email, purpose)` does the
same on demand, for a caller that needs to retire pending rows for a reason other than a resend
(`accounts` calls it for a user's *old* address right when it changes, so a link already sent
there can't later confirm whatever account claims that address next). `confirm_by_token` is
deliberately idempotent for a row its own token already confirmed, but only while that row is
still within its validity window -- past `expires_at` it raises `Expired` like any other stale
link, rather than succeeding forever (unlike `confirm_by_code`, which is strictly single-use from
the start); see the module docstring for why. Code length, max attempts and the resend cooldown
come from the admin-editable `CoreSettings` singleton (`CoreSettings.load()`, defaults: 6 digits,
5 attempts, 60s cooldown; each field is bounded with `Min`/`MaxValueValidator`s enforced by the
admin form, e.g. code length 6-10, max attempts 1-10, validity >= 1 minute). Validity normally
comes from there too (default 30 minutes) but `start(..., validity=timedelta(...))` lets a caller
override it per call -- `core` stays generic and doesn't know or care why; it just means one
purpose's needs don't force every other purpose onto the same window. Email matching (the resend
cooldown, "only the newest is valid", `invalidate`) is case-insensitive, but the stored/returned
`email` value is always exactly what the caller passed in -- never normalised -- so it round-trips
safely into `accounts`' own case-sensitive `User.objects.get(email=...)`.

Concurrency: `start`, `confirm_by_code` and `confirm_by_token` each run inside
`transaction.atomic()` with `select_for_update()` on the row(s) touched, which serialises
concurrent callers for real on Postgres (prod) and degrades to a harmless no-op on SQLite (dev/
test). Independently of that lock, the writes that matter under a race -- the attempt counter and
`confirmed_at` -- only ever go through a conditional `UPDATE` keyed on the value just read, never
a blind `instance.save()`, so a stale-snapshot request fails closed (`Burned` / `AlreadyConfirmed`)
instead of silently succeeding or undercounting guesses. Neither function ever raises from inside
its `atomic()` block -- every outcome is decided first as plain local variables, then acted on
once the block has exited normally -- so a business-rule rejection can never trigger a spurious
rollback of a concurrent request's already-committed work. A failed `start` (every email backend
failing) rolls back the whole call (the new row and the previous row's invalidation) and raises
`EmailVerificationSendFailed` rather than leaving a dangling, undelivered row or a cooldown for an
email nobody received.

`accounts`' signup/email-change confirmation (`apps.accounts.services.email_confirmation`) is
built on this instead of its own JWT (the now-removed `EmailConfirmationToken`); it only ever
calls `confirm_by_token` since today's flow is link-only, and it passes `validity` explicitly
from `EMAIL_CONFIRMATION_TOKEN_TTL_HOURS` (default 48h) to keep the historical signup-link
lifetime unchanged rather than adopting the shared 30-minute default. `send_confirmation_email`
catches `ResendTooSoon` and `EmailVerificationSendFailed` specifically (not the whole
`EmailVerificationError` base) and swallows both, matching the old JWT-based flow's behaviour: a
send failure was already silent from the caller's perspective before this port, since
`send_email` itself never raised on a total failure. `PUT /api/account` invalidates any pending
verification for the address a user is leaving (via `invalidate_pending_for_email`) before
sending a new one to the new address, so a stale link to the old address can't later confirm
whatever account claims it next. One migration-time consequence: any confirmation email sent
before this shipped used the old JWT and can no longer be validated at all, so those users need
to hit "resend confirmation" once (judged low-impact -- see the port's module docstring).

`file_transfer`'s anonymous-sender confirmation (`apps.file_transfer.services.anonymous`, purpose
`'file_transfer.anonymous_send'`) uses both entry points: the sender either types the 6-digit code
on the send page (`confirm_by_code`, which needs the specific `EmailVerification` row's id --
stored on `Transfer.email_verification_id` when confirmation starts, since a code alone doesn't
identify which row to check it against) or clicks the emailed link (`confirm_by_token`, looked up
by the token alone, from a different browser if the sender wants -- the link's URL carries the
transfer id only for routing, not as part of what proves the click legitimate). Both funnel into
the same `_activate` step: check the per-IP-per-day caps (see "File transfer specifics" below),
then flip the transfer `ACTIVE` and queue the recipient + sender emails, exactly like
`services.send.finalize_send` does for a logged-in sender.

### Rate limiting

`apps.core.ratelimit` (issue #53) is a small, in-house fixed-window limiter, not
`django-ratelimit`: this project's endpoints are a mix of plain sync Django views, async ninja
routers (`apps/qr_code/api/qrcode.py`, the `/go/<code>` redirect), and ninja routers whose
parameters are only resolved *inside* django-ninja's own request pipeline (so a decorator wrapping
the registered view function never actually sees the parsed payload before django-ninja's own
signature introspection runs) -- a plain function call at the top of a view composes with all
three without fighting any of that, whereas a decorator-based library would need different
integration paths for each. Also lives in `core` for the same reason `apps.core.services.email_verification`
does: everyone can import it, and `apps.core.services.client_ip.client_ip` (the trusted client-IP
reader every per-IP rule uses, moved here from `file_transfer` for this issue) needs the same reach.

- **`hit(key, rule)`** counts one hit against `key` under a named rule in `settings.RATELIMIT_RULES`
  (`{name: (limit, window_seconds)}`) and returns a `RateLimitResult` (`allowed`, `remaining`,
  `retry_after`); **`peek(key, rule)`** reports the same thing *without* counting the call -- for a
  caller that must decide whether to even attempt something sensitive (`authenticate()`) before
  recording a hit for it (see "Login" below). Fixed-window, keyed by a per-`(rule, key,
  time_bucket)` HMAC-SHA256 hash (`SECRET_KEY`-keyed, matching `email_verification`'s own digest)
  -- never the raw key -- so a long value (an email) can't overflow a storage column and nothing
  identifying ever sits in the cache/table in the clear. See `apps/core/ratelimit/limiter.py`'s
  docstring for the counting scheme in full.
- **Two storage backends** (`settings.RATELIMIT_STORAGE`, code review of the first version of this
  issue): `'db'` (dev/prod's default, and pytest's, when `CACHE_URL` isn't set) -- a dedicated
  model, `apps.core.models.RateLimitCounter`, one row per bucket, incremented with a single atomic
  upsert (`INSERT ... ON CONFLICT ("key") DO UPDATE SET "count" = "count" + 1 RETURNING "count"`,
  raw SQL through `connection.cursor()`; Postgres and SQLite >= 3.35 both support `RETURNING` on an
  upsert). `apps.core.jobs.purge_expired_rate_limit_counters` (an hourly scheduler job, like
  `purge_old_email_verifications`) deletes rows whose window has passed -- the DB path's stand-in
  for a cache entry's own TTL expiring. `'cache'` (when `CACHE_URL` is set) -- the `'ratelimit'`
  `RedisCache` alias, `cache.add` then `cache.incr`; `LocMemCache` under pytest when a test opts
  into this path with `override_settings(RATELIMIT_STORAGE='cache')`, to keep it covered too. The
  DB path replaced an original `DatabaseCache`-backed design with two bugs, both now covered by
  `apps/core/tests/test_ratelimit.py`: `DatabaseCache.incr` falls back to a plain `get`-then-`set`
  at the cache's *default* timeout once a bucket's second hit lands, silently shortening any window
  over 5 minutes; and its `MAX_ENTRIES` culling could evict a live, unrelated counter under
  ordinary traffic. `RedisCache`/`LocMemCache` never had either problem (`INCR`/`incr` are atomic
  and preserve the key's TTL) -- only `DatabaseCache` needed replacing, and only for that reason.
  `hit_ip(request, rule)` / `hit_user(user, rule)` / `hit_value(str, rule)` /
  `hit_ip_and_value(request, str, rule)` are the key shapes call sites need (IP, authenticated
  user, an arbitrary string like an email or a transfer slug, and the combination of the two);
  `peek_value` is `hit_value`'s read-only counterpart; `ahit_ip` / `ahit_value` are the async
  equivalents (thread the same `hit()` through `sync_to_async`, needed because the DB storage path
  touches the DB connection, which Django forbids calling straight from an async context).
- **Trusted proxy** (issue #53 code review): `apps.core.services.client_ip.client_ip` only honours
  `X-Real-IP` when `REMOTE_ADDR` -- the real TCP peer -- is itself in `settings.TRUSTED_PROXIES`
  (env var, IPs/CIDRs, default loopback + RFC 1918 + `fc00::/7`); otherwise, or when the header
  fails to parse, it falls back to `REMOTE_ADDR` -- never `None` (which every per-IP rule would
  read as *unlimited*). This is what makes trusting the header safe at all: our nginx reaches
  gunicorn over a private Docker bridge address, so **gunicorn must only ever be published on an
  internal interface in production**, never a public one directly.
- **IPv6**: per-IP keys collapse a real IPv6 address to its /64 (the block size most ISPs hand one
  customer) and unwrap an IPv4-mapped address to its plain IPv4 form first (`apps.core.ratelimit.keys`)
  -- otherwise a host that merely rotates within its own /64, or presents its IPv4 address in
  mapped form, would get a fresh budget for free.
- **Fails open, explicitly**: a storage error (a Redis outage, or anything else unexpected from
  either backend) is caught, logged with `logger.exception`, and treated as unlimited -- every
  limit here is headroom against abuse, and an outage turning into a site-wide 500 on every
  rate-limited view is a worse failure mode than letting requests through unmetered for a while.
  Every over-limit result also logs `logger.info` with the rule name (never the key) so 429 rates
  are visible in ordinary log monitoring.
- **`RATELIMIT_ENABLE`** is the project-wide kill switch (env var, default on in dev/prod, off
  under pytest) -- tests that exercise a limit turn it on with the `settings` fixture; the DB
  storage path needs no special test cleanup (each test's own DB transaction rolls it back), and
  `conftest.py`'s autouse `_clear_ratelimit_cache` fixture still clears the `'ratelimit'` cache
  alias for tests that opt into the cache storage path.
- **Responses**: ninja endpoints call `ratelimit.enforce(result)`, which raises `RateLimitExceeded`
  -- one exception handler in `config/api.py` turns that into `{"detail": ...}` with a
  `Retry-After` header for every router. Plain/HTMX views call `ratelimit.web_response(request,
  result)` (or `.htmx_response`/`.page_response` directly) -- `core/base.html`'s `htmx-config` swaps
  429 like 422, so a partial actually renders into the page rather than htmx discarding it as an
  error response. `register_page`/`forgot_password_page` (issue #52) both call
  `ratelimit.web_response` directly, retargeting the 429 partial onto their own message element,
  the same as every other rate-limited HTMX form view -- there's no client-side JS reading a
  JSON `data.detail` on these pages anymore.
- **Login and other password-style checks are throttled, never locked out** (issue #53 code
  review; see `apps.accounts.services.login_throttle`'s module docstring for the full reasoning):
  a single per-account rule that counts every attempt lets anyone who merely knows a victim's email
  lock them out by submitting it with wrong passwords from anywhere. Login instead splits into
  `LOGIN_ACCOUNT_IP` (strict, per (email, IP), every attempt) and `LOGIN_ACCOUNT` (looser, per
  email across every IP, but counting only *failed* `authenticate()` calls -- checked with `peek`
  before authenticating, recorded with `hit` only once it returns `None`) -- shared between the
  session view and `POST /api/auth/login` so one surface can't double the other's budget.
  `file_transfer`'s `FT_UNLOCK_TRANSFER` (a transfer's download password) gets the same
  failed-attempts-only treatment (`FT_UNLOCK_IP` stays strict); `forgot-password` gets a strict
  `FORGOT_PASSWORD_EMAIL_IP` paired with a looser, cross-IP `FORGOT_PASSWORD_EMAIL` (there's no
  authenticate-style outcome there to gate a failed-only counter on, so it's a flat cap instead,
  but still needs many different IPs to exhaust rather than a handful of requests from one).
- **The rules** (`settings.RATELIMIT_RULES`, one dict, each entry commented with its reasoning):
  login (as above), signup, forgot-password (as above, and its response shape never changes so
  existence still isn't leaked), resend-confirmation, QR preview/create (per user, shared between
  the web editor and `/api/qr/`), the `/go/<code>` redirect (per IP, generous -- over the limit it
  still redirects, only the scan-count write is skipped, protecting `QRCode.scan_count`'s accuracy
  under a shared-IP/NAT burst rather than shedding load, since a real visitor must never see an
  error just because someone else scanned the same code), and every file_transfer surface called
  out in issue #53 (anonymous upload/confirm endpoints, logged-in uploads, the download page,
  password attempts on it -- per IP *and* per transfer, so brute-forcing one transfer is throttled
  even from many IPs -- and the manage link). `LOGIN_IP` (20/5 min) and `SIGNUP_IP` (5/hour) can
  still legitimately bite a shared NAT/CGNAT/office IP; the `logger.info` above is there so that's
  visible if it becomes a real complaint, rather than tuned blind up front.
- **The per-email-address-per-day cap** on email verification (`EMAIL_VERIFICATION_START_EMAIL`) is
  enforced once, centrally, inside `apps.core.services.email_verification.start` itself (raising
  `EmailVerificationRateLimited`) rather than by each caller, within a caller-supplied
  `rate_limit_group` (`start`'s parameter, default: `purpose` itself -- issue #53 code review: a
  single cap shared unconditionally across *every* purpose would let one app's flows starve
  another's, e.g. an anonymous file_transfer send burning through a victim's budget and blocking
  their own signup confirmation). `accounts` needs no override (its one purpose is already shared
  across signup/resend/email-change). `file_transfer` gives every transfer's confirmation its own
  `purpose` (so one transfer's resend cooldown/guess limit can't interfere with another's) but
  passes one shared `rate_limit_group` (`anon_emails.PURPOSE`) across all of them, so the daily cap
  still closes the cross-transfer gap this was built for -- see `start`'s docstring for why a
  per-endpoint IP limit alone can't (issue #55 phase 2's per-transfer verification purpose means
  `file_transfer` has no per-address cooldown across different transfers at all).
  `accounts.services.email_confirmation.send_confirmation_email` and
  `file_transfer.views.anonymous`'s confirm views both catch and swallow/surface it like the
  existing `ResendTooSoon`.
- **nginx**: a coarse `limit_req` in front of the whole site is still recommended (the `infra`
  repo) as defense-in-depth below the application layer -- out of scope here, since `infra` is a
  separate repo, but worth adding there.

### API

`config/api.py` builds one `NinjaAPI`; each app exposes a `router` from its `api` module/package
and is mounted under its prefix (`/api/` for accounts, `/api/billing/`, `/api/qr/`, `/api/ft/` for
file_transfer). Docs at `/api/docs`.

`apps/file_transfer/api/` (issue #55 phase 3) is JWT-only (logged-in users; anonymous sending stays
web-only) and mirrors the web send/dashboard flow endpoint-for-endpoint, calling the exact same
`apps.file_transfer.services` functions the views do: create a draft (`POST /transfers/`), add/
presign/resume/complete/remove a file (`.../files/...`, see "Resumable uploads" below), finalize
with the send options (`POST /transfers/{id}/send`), list (`?filter=active|ended|all` +
`limit`/`offset` pagination), get, update (`PATCH`, partial -- only fields present in the body are
applied, via `payload.dict(exclude_unset=True)`), delete, add recipients and resend one's email.
`GET /transfers/{id}` (unlike the list endpoint) also returns a still-in-progress draft, since a
caller that already knows a specific id needs that to resume an interrupted upload. Its two
endpoint modules (`transfers.py`, `files.py`) share one `Router` instance from `router.py` rather
than nesting sub-routers, since `Router.add_router` only takes a static prefix and can't express a
nested path parameter like `/transfers/{id}/files/...`. `TransferSchema.recipients` is a list of
`{id, email, last_sent_at}` objects (not bare email strings), so a caller can resend to one without
a separate lookup (`POST /transfers/{id}/recipients/{recipient_id}/resend`, `admin/filetransfer.py
resend`). Status codes: 400 for a service `ValidationError` (including a malformed `PATCH` --
`expiry_date` without `expiry_choice`, or `?filter=` outside `active`/`ended`/`all`), **402** for
`InsufficientCreditsError` (a documented choice over 400 -- the request is well-formed, the account
just can't cover it right now), 404 for another user's transfer (never 403, so it doesn't confirm
the id exists), 429 via the site-wide rate-limit handler (`FT_UPLOAD_USER`, same rule and budget as
the web upload endpoints, applied per user -- including `POST /transfers/`, which also reuses the
caller's existing empty draft rather than creating a fresh one per call, same as
`get_or_create_draft`). `PATCH /transfers/{id}` applies its one-service-call-per-field updates
inside `transaction.atomic()` (as does the dashboard's own combined settings form,
`views.dashboard.update_settings`), so a later field failing can't leave an earlier one committed.

**Resumable uploads** (spec section 2, issue #55 phase 3): `TransferFile.client_last_modified`
(nullable, the browser/CLI's `lastModified`, ms since epoch) plus its name and size let a client
match a file it's re-uploading back to an existing, not-yet-finished `TransferFile` row after a
break. `S3Storage.list_parts` (`ListParts`) reports which parts of that file's multipart upload S3
already has; `services.uploads.list_uploaded_parts` wraps it, maps any other `ClientError` (e.g.
`AccessDenied`) to a plain `ValidationError` rather than letting it escape as a 500, and raises
`UploadExpired` when S3 no longer recognizes the upload id (the bucket's lifecycle rule aborted it,
or `cleanup_drafts` beat the client to it); `restart_upload` then abandons it and starts a fresh one
at the same storage key -- its DB write is conditional on the file still holding the exact
`upload_id`/`uploaded=False` state it was read in (a compare-and-swap), so a `resume` racing a
concurrent `complete` in another tab can't un-complete a file that just finished. Both the web JSON
endpoints (`views.uploads.resume_file` / `views.anonymous.resume_file`) and the JWT API
(`api/files.py::resume_file`) expose this as one `.../files/{id}/resume/` endpoint that
transparently restarts an expired upload rather than erroring, returning `{restarted,
part_size_bytes, part_count, uploaded_parts}` either way -- `part_size_bytes` is
`TransferFile.part_size_bytes`, pinned at `add_file` time rather than read live off
`services.storage.PART_SIZE_BYTES`, so a resumed upload's parts still line up even if that constant
changes while it's in flight.

Every part in `uploaded_parts` also carries its `checksum_sha256` (from S3's `ListParts`, since
every upload here is created with `ChecksumAlgorithm='SHA256'`): S3 requires that checksum again on
*every* part -- including ones a resume isn't re-uploading -- when completing the multipart upload,
or `CompleteMultipartUpload` fails outright. A resumed part's checksum must therefore round-trip
unchanged from `.../resume/`'s response into the eventual `.../complete/` call; `FakeS3Storage`
enforces this the same way S3 does, for any part it recorded through `upload_part` (see its
docstring). The server-side multipart write `services.zip._S3MultipartWriter` uses for the
"download all" zip opts out of this (`create_multipart_upload(key, checksum_algorithm=None)`) --
its parts never cross an untrusted network hop, so there's nothing for a checksum to verify.

- **Web**: `send.html`/`anon_send.html` share one script,
  `apps/file_transfer/static/file_transfer/js/resumable_upload.js`, since both pages upload the
  same way and need the same resume-matching logic. Presigned part URLs are requested a small batch
  at a time (`PART_URL_BATCH_SIZE`) rather than for the whole file up front, so a later part's URL
  on a slow connection doesn't sit long enough to expire before it's used; the same batching (plus
  streaming the file a part at a time rather than holding it all in memory) applies to the CLI's
  upload, below. A "paused" (not-yet-finished) row is silently left out of the transfer at send
  time (`services.send.finalize_send`/`start_confirmation` only count `uploaded=True` files), so
  the script asks for confirmation before letting the options form's htmx submit through
  (`htmx:confirm`) whenever one exists. The logged-in send page additionally has to solve
  identifying the *same draft* again across a reload -- `get_or_create_draft` deliberately never
  reuses a draft that already has files (see its own docstring: that behaviour predates hydration
  and stays as the default for a fresh, un-parameterized visit), so the moment a draft's first file
  lands, the page adds `?resume=<draft id>` to its own URL with `history.replaceState` (no
  navigation); `send_page` picks that query param up and fetches that specific draft, files and
  all, serialized into the page (`{% ... |json_script %}`) for the script to rebuild its file list
  and offer to resume whichever rows aren't uploaded yet -- the sender re-selects the same file (a
  browser can't reopen one on its own) and it's matched by name + size (+ `client_last_modified`
  when the row has one). The dashboard also links to the most recent draft that already has a file
  on it (`views.dashboard._resumable_draft`), since opening the send page from the nav rather than
  a literal reload otherwise starts a fresh, empty draft and strands the in-progress one. The
  anonymous flow needs none of this bookkeeping: `current_anonymous_transfer` already resumes the
  session's current draft (files included) on every reload regardless of file count.
- **CLI**: `admin/filetransfer.py send --draft-id <id>` re-fetches that draft
  (`GET /transfers/{id}`, which is why that endpoint returns drafts) and matches its local files
  against its `files` list by name, size *and* `client_last_modified` (tracking which draft rows
  it's already matched this run, so two distinct local files can't both claim the same one) before
  re-uploading anything, so re-running a `send` that was interrupted partway through only uploads
  what's missing. `_collect_files` names a file found inside an expanded folder by its path
  relative to that folder (not just its own name), and skips an empty one with a warning rather
  than aborting the whole send. Every request goes through `_request`, which applies a default
  timeout and a bounded retry on 429 honouring `Retry-After` (an ordinary multi-file folder send
  can otherwise hit `FT_UPLOAD_USER` easily); any failure once a draft exists prints the
  `--draft-id` hint. `login`/`set-password` accept their password as an optional positional
  argument -- omit it to be prompted with echo hidden, or set `FILETRANSFER_PASSWORD` for
  non-interactive use -- rather than always taking it in the clear on the command line.

### Admin

`apps/core/admin_site.py` owns `custom_admin_site` (mounted at `/admin/`) and the tools page (test
email, masked environment). Every app registers its `ModelAdmin`s on it from its own `admin.py`.
The manual credit adjustment tool lives on `CreditTransactionAdmin` (`/admin/billing/credittransaction/adjust/`).
The user admin builds on Django's `UserAdmin` with email-based forms (`apps/accounts/forms/admin.py`),
so passwords are only ever set through hashed password fields. Jazzmin's top menu links to the site
and to the admin tools page.

Singleton settings pages (superusers only, one row, `changelist_view` redirects straight to the
change form): `CoreSettingsAdmin` (`/admin/core/coresettings/`, email verification knobs -- issue
#58) and `FileTransferSettingsAdmin` (`/admin/file_transfer/filetransfersettings/`, spec section
9). Both load their row with the model's own `.load()` classmethod rather than a fixture.

### HTMX form views

Web forms post (form-encoded) to session-authenticated Django views in each app's `views/`
package, never to `/api/`. Conventions, with helpers in `apps/core/htmx.py`:

- Validate with a Django form (`forms/` package). On success, `hx_redirect()` or return the
  updated partial (`templates/<app>/partials/`); without htmx, fall back to a plain redirect.
- On validation errors, return the partial with status **422**. `core/base.html` configures htmx
  (`htmx-config` meta tag) to swap 422 responses; use `HX-Retarget` to send errors somewhere
  other than the request's target (see `apps/qr_code/views/editor.py`).
- Put shared create/update logic *and its validation rules* in the app's `services/`, so the web
  view and the API endpoint both call it and enforce the same rules (e.g.
  `apps.qr_code.services.create_qrcode` / `validate_content`). Forms call the service validators
  to show friendly errors; the API maps the service's `ValidationError` to a 400.

QR code specifics: previews are returned as PNG `data:` URIs and never written to disk. Short codes
for tracked QR codes are issued by the server (`qr_code:short-code`) and kept in the session until
the save, so users can't choose their own; a code taken in the meantime is replaced.

File transfer specifics: the send page's file upload endpoints
(`apps/file_transfer/views/uploads.py`, and their anonymous twins in `views/anonymous.py`) are a
deliberate exception to "form-encoded" above -- the browser uploads directly to S3 with presigned
multipart URLs and only coordinates with Django over JSON, so those views speak JSON in and out.
The options form (recipients, message, expiry, max downloads, password) that finishes the send *is*
a normal HTMX form and follows the 422 convention. The public download page (`/t/<slug>/`,
`apps/file_transfer/download_urls.py`) needs no login and never explains *why* a transfer isn't
available (expired, disabled, suspended, deleted, or its download limit reached all render the same
neutral page). A password gates the download links (including "download all", below), not the
file list itself. `DownloadEvent.ip` is read from `X-Real-IP`
(`apps.core.services.client_ip.client_ip`, shared by the download views, the anonymous send flow,
and every per-IP rate limit -- see "Rate limiting" above's "Trusted proxy" for the full story),
never the client-controlled `X-Forwarded-For` -- **this requires nginx to set `X-Real-IP` from the
real TCP peer** (stripping any client-supplied one) *and* `REMOTE_ADDR` to be a trusted proxy
address (`settings.TRUSTED_PROXIES`) for the header to be honoured at all; otherwise, or on a
malformed value, it falls back to `REMOTE_ADDR`.

Ending a transfer (natural expiry, the download that reaches `max_downloads`, disable, dashboard
delete-now, or the suspension grace period running out) normally deletes its S3 objects
immediately (`services.lifecycle.end_transfer`). The one exception: reaching `max_downloads`
defers the deletion (`Transfer.files_deleted_at` stays null) because that download's own presigned
GET URL must still resolve -- `expire_transfers` sweeps up the objects (and fires the "files
deleted" email) once `GET_URL_EXPIRES_SECONDS` has safely passed since `Transfer.ended_at`.

**Anonymous sending** (`apps.file_transfer.services.anonymous`, spec section 2 phase 2): the same
picker-then-options-then-send page a logged-in sender uses, session-owned instead of user-owned,
gated by `FileTransferSettings.anonymous_enabled`, and free within its own (smaller) limits
(`services.limits.validate_new_file_anonymous` / `validate_recipients_anonymous`,
`services.expiry_choices.resolve_expiry_anonymous` -- fixed day values only, from
`anonymous_allowed_expiry_days`). **`anonymous_enabled` defaults to `False`.** The plan originally
tied this to #53 (rate limiting) landing first -- it has (see "Rate limiting" above: the anonymous
upload/confirm endpoints, the download page and password attempts are all covered), so the
remaining `False` is now a plain product/rollout decision, not a known gap; flip it deliberately
when ready. `send_page` redirects an already-authenticated visitor to the normal, metered send page
instead (and `start_confirmation` refuses one directly, as a second guard) -- this flow is for
senders without an account, not a free lane for one.

Draft ownership (`services.anon_session`) is a random per-draft token generated on creation and
kept only in the session's own data (`request.session[...]`) -- never the session's own key
(`session.session_key`). Only an HMAC-SHA256 digest of the token is persisted, on
`Transfer.draft_token_hash`, compared with `owns_draft`. Two things this avoids: storing the raw
session key would put a real, authenticated session's own key -- session-takeover material -- into
`TransferAdmin`'s read-only list the moment an authenticated user (however that happened) used this
flow; and `django.contrib.auth.login()` rotates the session's own key (`cycle_key()`) but *keeps*
its data, so a sender who logs into an unrelated account mid-flow keeps their draft/pending
confirmation for free, with no special-casing needed. `get_or_create_anonymous_draft` /
`current_anonymous_transfer` resolve straight from the session's own "current draft id" rather than
a DB lookup keyed by session, so there's no longer a `session_key` column to index at all.

A draft only becomes a real transfer once its sender's email is confirmed
(`services.anonymous.start_confirmation` / `confirm_by_code` / `confirm_by_link` -- see the email
verification section above): unconfirmed transfers sit in `PENDING_CONFIRMATION` and are cleaned
up by the same `cleanup_drafts` job as plain drafts. Each transfer confirms through its own
`core.services.email_verification` "purpose" (`services.anon_emails.verification_purpose`,
`'file_transfer.anonymous_send:<transfer id>'` rather than one shared purpose per app) -- otherwise
two pending transfers from the same sender email would share one `(email, purpose)` row in `core`
and invalidate each other's verification, and worse, a confirmation link minted for one transfer
could `core`-side match *any* other transfer whose sender email happened to be the same (swap the
transfer id in the link's URL). `confirm_by_link` also independently checks the confirmed row's own
id (`core.services.email_verification.confirm_by_token_verbose`) against
`transfer.email_verification_id`, a second, cheap guard against exactly that. The trade-off: the
resend cooldown and guess-attempt limit are now per-transfer rather than per-sender-email --
closed at the `core` level instead by a per-email-address-per-day cap on verification starts
across every purpose (`EMAIL_VERIFICATION_START_EMAIL`, issue #53; see "Rate limiting" above),
since that's the one layer that sees every transfer's confirmation attempts against the same
address regardless of which transfer's purpose they were started under.

The confirmation link (`GET /send/anon/<id>/confirm/link/<token>/`) only ever *shows* a
CSRF-protected "Confirm this transfer" button; only the matching **POST** actually confirms.
Mail scanners and link-previewers fetch a URL automatically before a recipient ever clicks it --
if the bare GET confirmed, an attacker could upload files, enter a victim's address as
`sender_email`, and have the victim's own mail provider confirm (and, eventually, claim-on-login
meter) a transfer the victim never sent.

Per-IP-per-day caps (spec section 13, `services.anon_limits`) are enforced three times:
`check_upload_bytes_cap` at every file upload (summing the real `TransferFile.size` of every other
in-flight transfer's files -- never that transfer's own `size_bytes`, which stays `0` until it's
actually confirmed); a pre-check in `start_confirmation`, before a one-time code/link is even sent,
so a transfer that's already over the cap doesn't burn one pointlessly; and the authoritative
`check_send_caps` at actual confirmation (transfer count and bytes, only counting transfers that
actually got confirmed). Every check takes the *higher* of two counts -- by IP and by an opaque id
from a long-lived signed cookie (`services.anon_cookie`) -- so neither clearing cookies nor a
shared/rotating IP alone raises the effective limit; `check_send_caps` specifically checks against
*every* IP/cookie the transfer has ever presented -- the one recorded at draft creation
(`transfer.sender_ip`/`.anon_cookie_id`) as well as the confirming request's own -- since
confirming from a different network/browser than the one that uploaded is completely legitimate
(the point of the link at all) and checking only the confirming request's identifiers would let
that always see zero usage. If the authoritative check rejects at confirmation time -- the code/
token having already been burned (single-use) by then -- the transfer is ended outright
(`DELETED`) rather than left stuck `PENDING_CONFIRMATION` forever with no way to get a clear answer
out of it.

The anonymous sent page (`/transfer/sent/anon/<id>/`) and one-time manage link
(`/t/<slug>/manage/<token>/`, `views/manage.py`) both avoid leaking `sender_email` and the
download link to an arbitrary visitor who merely has (or guesses) the transfer's UUID. The manage
link offers disable and the download count; looked up by `slug` (already public) and compared with
`hmac.compare_digest` on **bytes** (not `str` -- a non-ASCII token would otherwise raise `TypeError`
and 500 the page), not a separate hashed column -- the slug alone already makes the row unguessable
to enumerate; disabling redirects back to the manage page (post/redirect/get) rather than
re-rendering it. The sent page is gated by `services.anon_session.can_view_sent_page`: either the
session that created the draft (`owns_draft`), or the session that clicked the confirmation link
(`mark_confirmed_via_link`, set right when that link's POST activates the transfer) -- the latter
is deliberately a *different* session than the one that drafted it (the sender opening their email
on their phone), so it needs its own, separate proof.

**Claim on login** (`services.claim.claim_transfers_for_user`, wired to Django's `user_logged_in`
signal in `apps/file_transfer/apps.py`, `weak=False` for the same reason as the `credits_added`
receiver above) hands a confirmed, unowned anonymous transfer to whichever user logs in with a
matching, *already-confirmed* email -- not merely matching, since an account that hasn't itself
proven it controls that address shouldn't be able to grab someone else's transfer by signing up
with their address first; claiming is simply deferred to that account's first login after its own
email gets confirmed. Only the session login page fires `user_logged_in` (signup is API-only and
never starts a session), so that's the one place claiming happens. **User decision:** claiming is
also deferred while the account's balance is below `services.send.MIN_BALANCE_TO_SEND` (1 credit,
the same minimum required to start a logged-in transfer) -- claiming turns a free transfer into a
metered one with no chance to opt out first, and at a near-zero balance the very next metering run
would immediately suspend it. Such a transfer stays anonymous and free (still fully usable by its
recipients) until a *later* login finds the balance topped up.

"Download all" (`services.zip`, spec section 5) is a lazily-built, per-transfer zip at
`transfers/<id>/all.zip`, not billed and not part of `size_bytes`. The first request claims the
build with one conditional `UPDATE` (`zip_status` `NONE`/`FAILED`, or a `BUILDING` row whose
`zip_build_started_at` is older than `services.zip.BUILD_LEASE` -- a build that never finished,
say a worker died mid-build -- -> `BUILDING`, so concurrent requests only ever enqueue one
`build_zip` task) and the download page polls a status partial (`hx-trigger="every 2s"`, capped at
`_MAX_AUTO_ZIP_POLLS` automatic polls before it asks for a manual click instead) until it's `READY`
or `FAILED` (retriable). The whole build -- including the writer's own constructor -- is wrapped so
any failure reliably lands on `FAILED` rather than leaving `BUILDING` stuck; after a successful
`finish()`, the transfer is re-read fresh and, if it ended (or its files were otherwise deleted)
while the build was running, the just-written object is deleted and `zip_status` reset instead of
being left an orphan (`services.lifecycle.delete_transfer_files` / `end_transfer` also reset
`zip_status` themselves, for the ordinary case where deletion runs after the zip already exists).
Zip entries de-duplicate a repeated `TransferFile.name` (`a (1).txt`), flatten `/`/`\` out of the
name (a "zip slip" guard against a naive/vulnerable extractor -- `services.limits.validate_filename`
only strips control characters, so a raw path stays in the stored name otherwise), and carry the
file's own `created_at` instead of zipfile's default 1980-01-01. The build streams both ends --
`S3Storage.get_object_stream` reads each source file a chunk at a time, and `_S3MultipartWriter`
turns `zipfile`'s output into an S3 multipart upload `PART_SIZE_BYTES` (64 MB) at a time -- so
neither a whole source file nor the whole zip is ever held in memory; this only works because
`_S3MultipartWriter` deliberately has no `.tell()`, which makes `zipfile.ZipFile` fall back to its
own non-seekable-stream support instead of assuming it can seek. A "download all" click counts as
one `DownloadEvent` (`file=None`) toward the same `max_downloads` limit as any other download.

### Queue and scheduler

Two background-work mechanisms, both used by `file_transfer` (spec issue #55) and available to any
future app:

- **Queue**: Django 6's built-in `django.tasks`, backed by `django_tasks_db` (an ORM-based backend
  -- Django core only ships Immediate/Dummy backends). `TASKS` in `config/settings.py` selects
  `django_tasks_db.DatabaseBackend` normally and `ImmediateBackend` under pytest, so tests never
  need a worker. A function decorated `@task` (see `apps/file_transfer/services/emails.py`) is
  queued with `.enqueue(...)`, never called directly, and its arguments must be plain
  strings/ids/etc (never model instances) since the backend serializes them. The `worker` compose
  service runs `./manage.py db_worker`.
- **Scheduler**: `apps.core.scheduler` -- a `JobSpec` registry (name, callable, interval, lease)
  filled by each app's `AppConfig.ready()` (`core` can't import a product, so it never discovers
  jobs itself), a `ScheduledJob` row per job whose claim is one conditional `UPDATE` guarded by the
  database's own clock throughout (`try_claim` in `apps/core/scheduler/runner.py` -- both the lease
  and the due-by-interval check use the database's `Now()`, not the calling process's clock, so
  two runners whose clocks disagree can't disagree about whether a job is due), and a
  `SchedulerRunner` that ticks every `SCHEDULER_TICK_SECONDS` running whatever's due. At-least-once,
  so every job must be idempotent. `./manage.py run_scheduler` runs it forever; job status is a
  read-only `ScheduledJob` admin. The `scheduler` compose service runs this, separately from
  `worker`.

`file_transfer`'s four jobs (`apps/file_transfer/jobs.py`): `meter_transfers` (daily -- charges,
suspends, re-enables as a fallback, deletes files past the suspension grace period),
`expire_transfers` (every 5 min -- also finishes the deferred file deletion described above),
`cleanup_drafts` (hourly), `purge_download_ips` (daily). Each wraps its per-transfer work in
`try`/`except Exception: logger.exception(...)` so one bad row can't abort the rest of that run's
batch the way an uncaught exception would (which, since `last_started_at` is already stamped by
the time the job's `func` runs, would otherwise leave every other transfer waiting a full extra
interval too).

### Templates and static files

Always namespaced: `apps/<app>/templates/<app>/…` and `apps/<app>/static/<app>/…`. Every page
extends `core/base.html`, which loads Tailwind (CDN, with the sage palette plus `brand-*` aliases),
htmx, Alpine.js and Font Awesome. The only un-namespaced templates are core's `admin/` overrides.

### Docker

One `Dockerfile` (multi-stage: `uv sync --frozen` in a builder, venv copied to `python:3.14-slim`),
shared by every service in `docker-compose.yml`: `web`, `worker`, `scheduler` and `db`. The build
runs `collectstatic` so WhiteNoise can serve static files with `DEBUG=False`. `.env.dev` is mounted
into `web`, `worker` and `scheduler` because settings require an env file.

- `web` runs gunicorn (`config.wsgi`) in the image; local compose overrides it with `runserver`. It
  has a TCP healthcheck on port 8000 that the other two services key their startup off of.
- `worker` runs `./manage.py db_worker` and `scheduler` runs `./manage.py run_scheduler` -- the two
  background-work mechanisms from "Queue and scheduler" above, each its own compose service (and so
  its own container, each running its one process as PID 1) rather than one container running both
  backgrounded with `&`: `/bin/sh` in `python:3.14-slim` is dash, which has no `wait -n`, so that
  shape actually never ran either process (the shell hit `wait: Illegal option -n` and exited,
  taking the container down with it, `restart: unless-stopped` looping it forever); separately,
  `sh` as PID 1 doesn't forward `SIGTERM` to backgrounded children either, so even a working
  supervisor script would have kept `run_scheduler`'s own graceful-stop handling from ever firing.
  Two plain services sidestep both problems and can restart or scale independently later.
- `db` is this stack's own Postgres (`postgres:18-alpine`), separate from the shared
  `docker-compose.postgres.yml` used across repos for local dev tooling (see that file's header) --
  `db` is part of the deployable stack the other services depend on.

Only `web` runs `docker-entrypoint.sh`'s `migrate` step (`RUN_MIGRATIONS=1`; `worker` and
`scheduler` explicitly set `RUN_MIGRATIONS=0`) -- `worker`/`scheduler` `depends_on: web:
condition: service_healthy`, and `web`'s healthcheck can only pass once gunicorn/`runserver` is
actually listening, which the entrypoint only starts after `migrate` finishes, so this also rules
out the migration race two concurrently-starting containers would otherwise hit. The database is
`DATABASE_URL` (Postgres, via `dj-database-url`; compose points it at `db`) when set, SQLite
otherwise; `ENVIRONMENT=prod` refuses to start without it -- a separate `worker`/`scheduler`
container can't share a SQLite file with `web`, which is why Postgres was a prerequisite for this
issue -- it's also what backs `apps.core.ratelimit`'s DB storage path by default (see "Rate
limiting" above), so no extra compose service is needed for that either. Behind nginx,
`SECURE_PROXY_SSL_HEADER` and `CSRF_TRUSTED_ORIGINS` (from `BASE_URL`) keep HTTPS form posts
passing the CSRF check, and nginx must also set `X-Real-IP` from the real client address for
`file_transfer`'s download-IP logging and every per-IP rate limit to be trustworthy -- **and
gunicorn must only ever be reachable from nginx's own private Docker bridge address, never
published on a public interface directly**, since `settings.TRUSTED_PROXIES`' default trust list is
exactly that private range (see "File transfer specifics" and "Rate limiting" above's "Trusted
proxy"). A coarse `limit_req` in nginx itself is a recommended defense-in-depth addition in the
`infra` repo, not done here.

## State of the test suites

Run everything with `inv test unit`: 0 failures, gated by CI (`.github/workflows/ci.yml`, `inv lint
all --check` then `inv test unit`). Two contracts are worth knowing since they aren't obvious from
the code alone:

- `JWTAuth`/`AsyncJWTAuth` (`apps/accounts/auth.py`) catch ninja_jwt's `InvalidToken`/
  `AuthenticationFailed` and return `None` rather than let it escape -- Django Ninja's own auth
  contract is "`None` means 401"; letting the exception propagate instead is a 500 in production on
  any malformed/expired/unknown-user `Authorization` header.
- An async test that touches the ORM directly wraps each call in `sync_to_async` and runs under
  `pytest.mark.django_db(transaction=True)` (see `apps/qr_code/tests/test_services.py`) --
  `sync_to_async`'s executor runs on a different thread than the one pytest-django's default,
  non-transactional `django_db` fixture opens its connection on, and a second SQLite connection
  touching the same file while that transaction is open deadlocks.

See issue #51 for the history of how the rest of the suite (a stale DRF-era test client, an
admin-redirect-vs-403 expectation, an unconfirmed-email test fixture) got to green.

## Known gaps

- The admin credits API (`POST /api/billing/users/{id}/credits`) now refuses to take a balance
  below zero (`CreditAccount.balance` is unsigned); it used to allow it.
- `file_transfer` phases 1 through 3 (issue #55) are built: logged-in *and* anonymous sending (email
  confirmation, per-IP-per-day caps, the anonymous manage link, claim on login), the download page
  with per-file and "download all" (zip) downloads, dashboard with all actions and a per-download
  log, metering, all emails, settings, queue and scheduler, resumable uploads, a JWT `/api/ft/`
  router and `admin/filetransfer.py` (see "API" above for all three). **Anonymous sending is gated
  off by default** (`FileTransferSettings.anonymous_enabled = False`); rate limiting (#53) no longer
  blocks turning it on, see "Rate limiting" above and "File transfer specifics"' anonymous-sending
  paragraph -- flipping it on is now a rollout decision, not a known gap. Takedown tooling
  (originally phase 3, spec section 16) is tracked separately as issue #59, not built here.
- Rate limiting (#53) is in place site-wide (see "Rate limiting" above) using an in-house limiter
  on a dedicated DB-backed model (or Redis, when `CACHE_URL` is set), not a host-level guard: a
  coarse nginx `limit_req` is still recommended as defense-in-depth, tracked in the separate
  `infra` repo, not done here.

## Conventions

- Python 3.14 (pinned by `.python-version`; the Dockerfile builds on `python:3.14-slim`),
  100-column lines, PEP 8. Ruff lint set is `E,F,W,I` with `E266,E501,E701,F811` ignored;
  migrations are excluded.
- **`ruff check .` and `ruff format --check .` both pass.** Keep them that way — run
  `inv lint ruff .` before committing.
- **Single-quoted strings** (`ruff format --quote-style single`); triple-double-quoted docstrings.
- Modern type syntax: `str | None`, not `Optional[str]`. Type-checked with `ty` over `apps/`,
  `config/` and `admin/`, which gates `inv lint all` / CI. Django model/queryset attributes that
  only exist via metaclass magic (`.objects`, `.DoesNotExist`, a `ForeignKey`'s auto `_id`
  companion attribute, reverse accessors) or descriptor-based field typing that `ty` can't infer
  without a django-stubs-equivalent plugin are handled at the point of declaration with an
  explicit annotation or `cast(...)`, or -- where that's not practical -- suppressed at the point
  of use with a targeted `# ty: ignore[rule-name]` and a comment.
- Models, schemas, forms, routers/api, services and views are packages with one domain per file,
  re-exported from `__init__.py`. Follow this when adding to any app.
- Every app namespaces its URLs with `app_name` (its label): `'accounts:login'`,
  `'qr_code:dashboard'`, `'core:home'`, … The `/go/<code>` short links are `'go:redirect'`.
- Admin CLIs: typer apps with `no_args_is_help=True`, a module docstring as `help`, and a `--dry`
  option threaded through `admin.utils.run`.

### GitHub issues

Label every issue with the app(s) it applies to: `Users` (accounts), `Billing`, `Core`, `QR Code`,
`File Transfer`. Work that happens in the separate `infra` repository is tracked here under
`Infra`. An issue that touches several apps gets several labels.

### Adding an app

1. `apps/<name>/` with the layout above; `AppConfig` with `name = 'apps.<name>'`,
   `label = '<name>'`.
2. For a product: register a `ProductApp` in `AppConfig.ready()` (see `apps/qr_code/apps.py`).
3. Add to `INSTALLED_APPS`, mount URLs in `config/urls.py`, add its API router in `config/api.py`.
4. Add it to the products layer of the import-linter contract in `pyproject.toml`.

## Design system

Sage green palette derived from the logos. Use it for any new web interface.

| Token | Hex | Use |
|---|---|---|
| Sage Green | `#8FA89E` | brand, buttons/CTAs |
| Dark Slate | `#3B4A47` | headers, hover states |
| Light Sage | `#B5C7BE` | borders, subtle fills |
| Deep Charcoal | `#2C3432` | primary text, dark-mode bg |
| Soft Mint | `#D4E0DA` | light backgrounds, dividers |

Tailwind scale: `50 #f4f7f6 · 100 #d4e0da · 200 #b5c7be · 300 #8fa89e · 400 #728e84 · 500 #5a736a ·
600 #475a53 · 700 #3b4a47 · 800 #2c3432 · 900 #1e2422`.

The admin uses django-jazzmin, themed via `JAZZMIN_SETTINGS` in `config/settings.py` with
`core/css/jazzmin_custom.css` and a `core/js/admin_theme_toggle.js` light/dark toggle.
