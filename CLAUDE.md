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
                      email verification, rate limiting (`apps.core.ratelimit`), client IP,
                      generic S3 storage (`apps.core.services.storage`).
apps/accounts/        User model, auth pages + API, JWT auth classes.
apps/billing/         CreditAccount (balance) + CreditTransaction (ledger), credit services, the
                      shared storage price (BillingSettings).
apps/feedback/        Footer "Feedback" page: saves a user's message, emails a receipt and a notice
                      to superusers; admin triage by status.
apps/qr_code/         QR codes: dashboard/editor pages, API, `/go/<code>` short links.
apps/file_transfer/   File transfer: logged-in + anonymous sending, dashboard/download pages,
                      S3 uploads, metering, "download all" zip, jobs.
apps/receipts/        Receipts (issue #98): receipts and invoices in, extracted fields out. Models,
                      uploads, email intake, AI extraction, billing, jobs, admin, the web pages, the
                      JWT API (`/api/receipts/`) and the `admin/receipts.py` CLI (see "Receipts").
admin/                Typer CLIs for lint/test/server/pip/openapi/aws/email/qrcode/filetransfer/receipts.
                      Via `inv`.
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
inv server run [local|prod]     # runserver on :8000
inv pip sync                  # uv sync --frozen, all groups
inv openapi generate --format json --file <path>
inv qrcode login local <email> <password>       # JWT CLIs over /api/qr/, /api/ft/ and /api/receipts/
inv filetransfer send local report.pdf --to a@example.com
inv receipts login local <email>                # password: prompt, or RECEIPTS_PASSWORD
inv receipts upload local invoice.pdf ./scans   # files or folders; unsupported types are skipped
inv receipts list local --status needs_review --from 2026-10-01 --to 2026-10-31 [--vendor <id>]
inv receipts get local <receipt-id>              # also the vendor's vetted state and "read as" name
inv receipts export local --out receipts.csv [--status ... --vendor ... --q ... --from ... --to ...]
inv receipts vendors local [--to-vet] [--q acme] # id, name, vetted, receipt count
inv receipts vendor-rename local <id> "New name" [--not-vetted]  # a clash (409) names the other
inv receipts vendor-vet local <id>... [--unvet]
inv receipts vendor-merge local <keep-id> <id>... [--yes]
inv receipts inbox local                        # the address receipts can be emailed to
inv receipts regenerate local [--yes]           # retire the address and issue a new one
```

Every command accepts `--dry` to print the shell command without running it (the JWT HTTP-client
CLIs -- `qrcode`, `filetransfer`, `receipts` -- run no shell command, so they have no `--dry`). The app names `inv`
accepts come from `admin/django_app.py`, which discovers them from `apps/*/apps.py`.

`admin/server.py` and `admin/openapi.py` each define one typer command, which typer collapses away
when the module is run directly (`python3 -m admin.server`) but which `inv` preserves
(`inv server run`).

### Running tests directly

Run pytest from the repo root; `DJANGO_SETTINGS_MODULE=config.settings` and
`testpaths = ['apps', 'admin']` are set in `pyproject.toml`, and default addopts include
`--reuse-db --no-migrations`. `admin` is there for `admin/tests/` (currently
`admin/filetransfer.py`'s, `admin/receipts.py`'s and the token-file handling of `qrcode`/`utils`) -- plain HTTP-client CLI logic with no Django/database
dependency of its own, picked up by the same `pytest`/`inv test unit` run as every app's tests:

```bash
ENVIRONMENT=local pytest apps/accounts/tests/unit/test_jwt.py::test_access_token
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
qr_code | file_transfer | receipts   products; may not import each other
billing | feedback        may not import each other either
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
- **The storage price is shared** (issue #98): credits per GB stored per day is one number for every
  product that stores files for a logged-in user (file transfer now, receipts later), the
  `BillingSettings` singleton's `storage_price_per_gb_per_day` (`BillingSettings.load()`, admin at
  `/admin/billing/billingsettings/`). Products read it through
  `apps.billing.services.get_storage_price_per_gb_per_day()` and do the daily accrual arithmetic
  with `storage_charge(accrued, size_bytes, price)` -> `(whole_credits_to_spend, new_remainder)`
  (floor, remainder carried, quantized to 6 decimal places like `Transfer.accrued`) rather than
  each rolling their own. `file_transfer`'s
  `meter_transfer` takes the price as an optional argument so the daily job loads it once per run.
  It used to be `FileTransferSettings.price_per_gb_per_day`: `file_transfer` migration
  `0006_copy_price_to_billing_settings` copied any customised value into `BillingSettings` (it
  lives in `file_transfer` because `billing` can't depend on a product; it only copies when no
  `BillingSettings` row exists yet), and `0007` dropped the old column (#102, a release after the
  copy, so an old container serving during that deploy never lost the column it read).
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

The convention: `.env.<environment>` at the repo root where `<environment>` ∈ {`local`, `prod`}; if
`ENVIRONMENT` is set, use it; otherwise require exactly one `.env.*` file (ignoring
`.env.example` and `.env.staging`). `.env.*` is gitignored — copy `.env.example` to `.env.local`. `apps/core/checks.py`
re-validates env selection and `EMAIL_BACKENDS` at `runserver` startup.

The deployments' env files are `.env.prod` and `.env.staging` at the repo root, next to `.env.local`,
scp'd to `/opt/docker/ziptrigo-apps/<env>/.env` on the VPS. This repo is their only home; the
`infra` repo holds none. Both deployments run with `ENVIRONMENT=prod` (staging mounts its own file
as `.env.prod`), which is why `.env.staging` is in `IGNORED_ENV_FILE_SUFFIXES`. With `.env.local` and
`.env.prod` both present, local runs need `ENVIRONMENT` set. All three are listed in
`admin/secrets_files.txt` for `inv secrets backup` / `restore`.

With `ENVIRONMENT=prod`, settings raise `ImproperlyConfigured` if `SECRET_KEY`, `JWT_SECRET` or
the file transfer S3 credentials (`FILE_TRANSFER_AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`) are
missing or still placeholders (`config/secret_checks.py`, also Django-free). The same goes for
Receipts' `OPENAI_API_KEY`, `OPENAI_RECEIPTS_MODEL`, `RECEIPTS_S3_BUCKET`,
`RECEIPTS_AWS_ACCESS_KEY_ID` and `RECEIPTS_AWS_SECRET_ACCESS_KEY`: Receipts has no feature flag, so
infra and the env files must land before the app deploy (local and pytest need none of them).

File transfer storage is one private S3 bucket per environment (`FILE_TRANSFER_S3_BUCKET`, keys
`transfers/<transfer_id>/<file_id>` -- deliberately not `.../<filename>` as spec section 11 says,
see `apps.file_transfer.services.storage`'s module docstring for why), defined with its CORS,
lifecycle rule and IAM user in the `infra` repo's `apps/ziptrigo-apps` (see its README); local points
`FILE_TRANSFER_S3_ENDPOINT_URL` at Floci. Each part of a multipart upload carries its own SHA-256
checksum (`x-amz-checksum-sha256`, computed client-side and passed into the presigned URL --
see `services.uploads.presign_parts`), so that bucket's CORS config (issue #60) must
allow the `x-amz-checksum-sha256` request header and expose the `ETag` response header, or uploads
fail in the browser with a CORS error rather than an S3 one. The checksum and each part's
`Content-Length` are *signed* into the URL, so S3 rejects a PUT whose body differs in size or
content. That only holds under SigV4, so `get_storage()` builds its client with
`signature_version='s3v4'` (#100) and `presign_part_url` refuses a client whose URL doesn't sign
them; `services.uploads.complete_file_upload`'s `head_object` size check against the declared
size stays as a second guard.

The S3 wrapper itself is generic and lives in `core`: `apps.core.services.storage.S3Storage` is a
dataclass over a `bucket` and a boto3 client (`build_client(region, endpoint_url, access_key_id,
secret_access_key)` builds one; an app wires its own settings into it, so apps can use different
buckets/credentials). Besides the multipart methods it has `put_object`, `copy_object`,
`get_object_bytes` (optional `max_bytes`), `list_keys` (returns a plain list that may be truncated
at `max_keys`; pass `None` when completeness matters), `presigned_put_url` (a single PUT with
`Content-Length` and `x-amz-checksum-sha256` signed in; it raises
`UnsupportedSignatureVersionError` when the URL it generated doesn't sign `Content-Length`, i.e. a
non-SigV4 client, and so does `presign_part_url` when given a size or checksum; note `client.meta.config.signature_version` reads `'s3v4'` even for a default
client whose presigned URLs are SigV2, so it inspects the URL's signed headers instead) and a `presigned_get_url` with
`disposition` / `content_type` (an explicit `content_type` with `inline` must be one of
`INLINE_CONTENT_TYPES`: PDF, JPEG, PNG, WebP, GIF, plain text).
`apps.file_transfer.services.storage` keeps `get_storage()` (builds that class from the
`FILE_TRANSFER_*` settings), the key helpers and the part-size constants, and re-exports
`S3Storage` / `ObjectInfo`. botocore signs presigned URLs with legacy SigV2 in older regions
(`us-east-1`, `eu-west-1`, ...) unless the client is built with `signature_version='s3v4'`, and
SigV2 does **not** sign `Content-Length`; `build_client` keeps botocore's default unless asked,
so every app passes `'s3v4'` (file transfer since #100, receipts from the start).

### Auth

One `AUTH_USER_MODEL`: `accounts.User` (UUID pk, email login, `status`). Two mechanisms coexist:

- **Django sessions** for the web UI: every page and every HTMX form view is `@login_required`
  (`LOGIN_URL='accounts:login'`). The login page is a session form view
  (`apps/accounts/views/login.py`, CSRF-protected, honours a same-site `next`); logout is
  POST-only. The browser never holds a JWT.
- **JWTs** for `/api/`, i.e. external clients like `admin/qrcode.py`, `admin/filetransfer.py` and
  `admin/receipts.py`
  (`apps.accounts.auth.JWTAuth` / `AsyncJWTAuth` / `AdminAuth`, which also reject non-`ACTIVE`
  users). Claim `sub`, signed with `JWT_SECRET`, token classes in `apps/accounts/tokens.py`.
  **Tokens are bound to the password**: every access and refresh token carries `pwd`
  (`tokens.PASSWORD_CLAIM`, a `SECRET_KEY`-keyed HMAC of the password hash, never the hash itself),
  and `JWTAuth`/`AsyncJWTAuth` (so `AdminAuth` too) and `POST /api/auth/refresh` reject a token
  whose `pwd` doesn't match the user's current password. Any password change -- the account page,
  a reset, the admin, `set_unusable_password` -- therefore revokes every token issued before it,
  like Django's session auth hash does for sessions. A token without the claim is rejected, so
  tokens issued before this shipped stopped working and CLI users had to log in again once.

`POST /api/auth/login` only issues JWTs; it never starts a session. Register, forgot-password,
reset-password and resend-confirmation (issue #52) are session form views too, the same as login
-- CSRF-protected, POST-only, following the "HTMX form views" convention below; the browser never
sees a JWT on any of them. `/api/auth/…` still exposes `forgot-password`, `reset-password` and
`resend-confirmation` for non-browser clients; both surfaces share the same
`apps.accounts.services` logic (`password_reset.get_password_reset_service`,
`email_confirmation.get_email_confirmation_service`) and the same `apps.core.ratelimit` rules, so
validation and throttling can't drift between them. A reset also confirms the account's email (see
below).

**Accounts are created only on the web register page** (issue #117): `POST /api/auth/signup` was
removed, so an API client (or, later, an MCP client -- issue #121) always works on behalf of an
account a person created in the browser. The register page has a **Cloudflare Turnstile captcha**
(`apps.accounts.services.captcha`), on only when `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`
are both set (`apps.accounts.checks` warns, `accounts.W002`, about one without the other; pytest
runs with both blanked by an autouse fixture in `apps/accounts/tests/conftest.py`). The widget is
rendered explicitly by `accounts/js/turnstile.js` so the form htmx swaps in after a 422 gets a fresh
one (tokens are single-use), and the submit button stays disabled until it yields a token; after a
429 (retargeted to `#register-msg`, so the form isn't swapped and the token may already be spent)
it resets the widget. The view checks, in order: the form (an invalid one spends neither the token
nor a rate limit), the captcha (`SIGNUP_CAPTCHA_IP` bounds the calls to Cloudflare; an empty token
is rejected without one), then `SIGNUP_IP`, then `create_account`. `RegisterForm` never checks
whether the email is taken (issue #117 review): it runs before the captcha and every rate limit.
`captcha.verify` **fails closed** (timeout, non-200, bad JSON: rejected) and, with a real secret,
requires the reply's `hostname` to be `BASE_URL`'s host and its `action` to be `register`; Cloudflare's test
secrets skip those two checks, since they answer with placeholder values -- so a test secret with a
non-local `BASE_URL` triggers `accounts.W003` (shown by `manage.py check` and the container's
`migrate` step). Login, and "Sign up with Google", have no captcha. README's "Signup captcha" has
the Cloudflare setup.

**No page or endpoint reveals whether an email address has an account** (issue #125; forgot-password
and resend-confirmation already worked this way). The register page gives the same answer for every
valid submission that passed the captcha and `SIGNUP_IP`: the same redirect to `accounts:created`
(whose copy is neutral: "Check your email", not "Account created"), whatever the address. A new
address gets an account and a confirmation email, as before. For an address that already has one,
`create_account` raises `EmailAlreadyRegistered` (also for an `IntegrityError` from a concurrent
signup) and the view -- outside its `except` block, so a logged traceback doesn't chain the address
-- calls `services.existing_account.handle_signup_for_existing_email(email, password)`, which
creates nothing, never stores or logs what the visitor typed, and never raises (a failure there is
logged with the account's id, never the address, so it can't turn into an error page only an
existing address produces):

- **Confirmed account:** queues the `send_account_exists_email` task ("someone tried to create an
  account with this address, you already have one": absolute `BASE_URL` links to sign-in and
  forgot-password, or only to sign-in with "Sign in with Google" for an account with a `google_sub`
  and no usable password). It carries nothing from the form, so the form can't make it say
  something of an outsider's choosing; the task skips if the account has moved to another address
  since. Nothing about the account changes.
- **Unconfirmed account: locked down, and the confirmation link is *not* resent** (review of
  #127). Whoever left it unconfirmed chose its password, and that may be someone other than the
  address's owner -- the "pre-hijack": register a victim's address with a password you know and
  wait; if the victim's own signup resent the link and they clicked it, the account would be
  confirmed with the attacker's password (and a later "Sign in with Google" would link it, exactly
  what the Google-linking rules above refuse). So `_lock_down_unconfirmed` (on *every* attempt, even
  when the email below is rate limited, since it is what closes the hole) makes the password
  unusable -- which also revokes its JWTs and sessions, being derived from the password hash -- and
  invalidates every pending confirmation link for the address (`invalidate_pending_for_email`);
  name and email are untouched. Then the `send_finish_setup_email` task mails the address "Finish
  setting up your ZipTrigo account" with a **set-password link** (the ordinary reset token,
  `PasswordResetService.reset_url_for`, valid `PASSWORD_RESET_TOKEN_TTL_HOURS`, made when the task
  runs). It has no `user.name` greeting and nothing from the form (on an unconfirmed row the name
  is the creator's; `render_finish_setup_email` doesn't even take the user), and is autoescaped like
  the other notices. An unconfirmed account that has a `google_sub` (only an email change can
  produce one: someone is using it), a staff/superuser one (`createsuperuser` leaves the email
  unconfirmed, and an admin's account was never a stranger's to pre-register) and an
  inactive/deleted one are left alone, no email.
  **Trade-off, accepted:** an owner who signed up legitimately but never confirmed, and whose
  address someone else then submits, finds their password dead until they follow that email (or
  use forgot-password, below); an account whose email was changed through `PUT /api/account` and
  not yet confirmed suffers the same if someone submits that address. The way back is always the
  same: forgot-password accepts an unconfirmed account, so an owner over the email budget, or whose
  link expired, requests a reset there.
- Both emails share one per-address budget: `SIGNUP_EXISTING_EMAIL` (1 per hour) and
  `SIGNUP_EXISTING_EMAIL_DAY` (3 per day), per lower-cased address across IPs. `_may_email`
  `peek`s both and counts both only when an email is going to be queued; over either, the email is
  skipped silently and the response is unchanged.
- **Inactive/deleted account:** nothing is sent.

**A password reset through an emailed link confirms the account's email** (review of #127; the
shared `PasswordResetService.reset_password`, used by the reset page and `POST
/api/auth/reset-password`, which both also invalidate any confirmation link still out for the
address): the link went to that address. That is how the "finish setting up" email completes, and
it makes forgot-password work end to end for an unconfirmed account. Because that only holds for the
address the link was *sent to*, the reset token carries it (`tokens.RESET_EMAIL_CLAIM`, set by
`PasswordResetToken.for_user`): a link for an address the account has since left is invalid (it used
to be able to reset the password too), a link without the claim (sent before it existed) still
resets the password but confirms nothing, and the confirming write is conditional on the address
still being the one on the link. Claim-on-login (file transfer) needs `email_confirmed`, so it
becomes possible at the next login after such a reset, the same as after a confirmation link.
Residual, not new: the confirmation email sent to the victim at the attacker's *first* signup (and
resend-confirmation) still confirms an account with its creator's password if the victim clicks it;
that is inherent to confirming by link and password signup, and is not closed here.

**Timing is roughly equalised** (review of #127): the existing-address path hashes the submitted
password once and discards it (`make_password`, like `ModelBackend` does for an unknown user), the
new-account path hashes it in `create_user`, and both then queue exactly one email with
`enqueue_after_commit` -- a new account's confirmation is the `send_signup_confirmation_email` task
(`services.email_confirmation`, skipped if the address moved or was confirmed; a queue failure is
logged and the page's resend-confirmation is the recovery), not an inline send. Resend-confirmation
and `PUT /api/account` still call `send_confirmation_email` inline. What's left is a database round
trip or two, not the SES latency or a password hash. The confirmation email itself now renders from
an autoescaped template (`accounts/emails/email_confirmation.html`, as does the password-reset
one): `user.name` is free text, an attacker's on an account they created.
Not covered by this rule, on purpose: `PUT /api/account` still answers `400 Email already in use`
for an address that has an account, but only to a logged-in caller, and every attempted *change*
spends `ACCOUNT_EMAIL_CHANGE_USER` (5/hour per user) before that check, so an authenticated,
attributable and throttled caller can probe a handful of addresses an hour, which was judged
acceptable next to an anonymous page; hiding it would mean redesigning the email-change flow. And "Sign in with Google"
(see below) links an existing confirmed account or refuses with a specific message, which needs an
identity Google has verified, so it is no free oracle either; left as is.

Changing the email through `PUT /api/account` un-confirms the account and sends a new
confirmation email. If the address being left had been confirmed, it also gets a notice
(`services.email_change`, "your email address was changed from X to Y"). It's a notice only, with
no "do this if it wasn't you" step (a product decision): the old owner can't use forgot-password
(it goes to the new address) or a password login (it needs a confirmed email), and the project has
no support address.

**Change password** is a session form view on the account page (`POST /account/password/`,
`views/password.py`, `forms.ChangePasswordForm`; web only, no `/api/` endpoint). It needs the
current password and refuses an unchanged one; the new one goes through `PasswordValidator` *and*
Django's `AUTH_PASSWORD_VALIDATORS` (too common, all digits, too similar to the email or name --
signup and reset still apply only `PasswordValidator`). `services.password_change.change_password`
saves it and, on commit, queues the `send_password_changed_email` task ("your password was
changed", with a link to `accounts:forgot-password` in case it wasn't the owner -- sent only to a
*confirmed* address, like the feedback receipt). The view calls `update_session_auth_hash`, so this
session stays signed in while the user's other sessions are signed out, and every JWT issued
before the change stops working (see the `pwd` claim above). **Every** attempt counts against
`CHANGE_PASSWORD_USER` (per user, `hit_user` before the current password is checked): a
"`peek`, then record failures" counter like login's `LOGIN_ACCOUNT` lets a burst of parallel
requests through before the first failure is recorded, and only the session's own user can spend
this budget, so there's no lockout of someone else to avoid. A "Sign in with Google" account with
no usable password doesn't get the form (and a POST just redirects, `hx_redirect`).

Tasks queued from inside a transaction go through `apps.core.services.queue.enqueue_after_commit`
(on commit; a failure to enqueue is logged and swallowed, since the domain write is already
committed and raising would only turn it into an error page). `accounts`, `feedback` and
`receipts` use it.

**Sign in with Google** (issue #91) is a third way in to the *web UI*, and it is a session login
like the password form, not a JWT one: `GET /auth/google/login` and `GET /auth/google/callback`
(`apps/accounts/views/google.py`, URLs in `google_urls.py` under their own `google_auth:` namespace
since Django can't serve two URLconfs under `accounts:`; exact paths, no trailing slash -- the
callback is a registered redirect URI at Google) run an OpenID Connect authorization code flow with
Authlib's Django client, and the callback ends in the same `django.contrib.auth.login()` the
password form uses, so `user_logged_in` fires (claim-on-login, `last_login`) and a same-site `next`
captured at `/auth/google/login` (kept in the session, validated by the shared
`views.redirects.redirect_target`, which the password login uses too) is honoured. Authlib owns
`state`/`nonce`; no token or code is ever logged.
`apps.accounts.services.google_oauth` holds the lazily built, cached client and `resolve_user`:
require Google's `email_verified` (else a 403 page), match `User.google_sub` first (nullable,
unique), then link an existing account by case-insensitive email (storing the `sub`), else create
one (confirmed email, unusable password, no confirmation email). **Signing in by `sub` does not
require `email_confirmed`**, deliberately unlike the password login: the Google identity is the
proof, so a linked user whose address was later changed (and so un-confirmed) through
`PUT /api/account` can still sign in with Google. **Linking rules (anti-takeover):** an existing
account is linked by email only if (a) its `email_confirmed` is true -- an unconfirmed account is
*never* linked (`UnconfirmedAccountConflict`, "confirm it or reset your password, then try
again"), because its holder never proved the address and may hold a password or JWTs that outlive
an email change, which would otherwise survive the link; and (b) Google is authoritative for the
address: a `gmail.com`/`googlemail.com` domain (case-insensitive) or a non-empty `hd` claim
(Google Workspace) -- otherwise `LinkNotAllowed` ("sign in with your email and password
instead"), since `email_verified` is only "verified at some point" for a domain Google doesn't
host. Creating a *new* account from any verified email stays allowed. Linking changes nothing but
`google_sub` (no confirmation, no password change), so it needs no
`invalidate_pending_for_email`. An account already linked to a different
`sub`, or two accounts matching the email, are refused rather than guessed at; inactive users
(`status`/`is_active`) are refused like the password login ("User not active"). Every failure
(denied consent, bad state, failed exchange, unexpected error) logs and redirects to the login page
with a `messages` banner. The feature is on only when `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`GOOGLE_REDIRECT_URI` are all set (`google_oauth.is_configured()`, passed to the login/register
templates as `google_enabled`); unset, the button is hidden and both routes redirect with a message,
and `apps.accounts.checks` warns (`accounts.W001`) about a *partial* configuration. The callback is
rate limited per IP (`GOOGLE_LOGIN_IP`).
The linking refusals ("an account with this email already exists ...") do say that an address has
an account, which the no-enumeration rule above (issue #125) otherwise forbids; that was left on
purpose: they appear only after Google has verified the caller controls that very address, so
nobody learns anything about an address they don't hold.

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
  issue): `'db'` (local/prod's default, and pytest's, when `CACHE_URL` isn't set) -- a dedicated
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
- **`RATELIMIT_ENABLE`** is the project-wide kill switch (env var, default on in local/prod, off
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
  login (as above), signup (plus `SIGNUP_CAPTCHA_IP` and the per-address limits on the email an
  existing address gets, `SIGNUP_EXISTING_EMAIL` and `SIGNUP_EXISTING_EMAIL_DAY`, see "Auth"), `ACCOUNT_EMAIL_CHANGE_USER` (email changes through
  `PUT /api/account`, per user), forgot-password (as above,
  and its response shape never changes so existence still isn't leaked), resend-confirmation, QR preview/create (per user, shared between
  the web editor and `/api/qr/`), the `/go/<code>` redirect (per IP,
  generous, charged *before* the code lookup so made-up codes spend the budget too -- over the limit
  a known code still redirects, only the scan-count write is skipped, protecting
  `QRCode.scan_count`'s accuracy under a shared-IP/NAT burst rather than shedding load, and an
  unknown one is still a 404, never a 429, since a real visitor must never see an error just
  because someone else scanned the same code), and every file_transfer surface called
  out in issue #53 (anonymous upload/confirm endpoints, logged-in uploads, the download page,
  password attempts on it -- per IP *and* per transfer, so brute-forcing one transfer is throttled
  even from many IPs -- and the manage link), plus abuse reports (issue #59: `FT_REPORT_IP` strict
  per IP, `FT_REPORT_TRANSFER` a looser per-transfer ceiling, same split reasoning as the download
  password). `LOGIN_IP` (20/5 min) and `SIGNUP_IP` (5/hour) can
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
- **nginx** (issue #119): a coarse per-IP `limit_req` sits in front of the whole site as
  defense-in-depth below the application layer. It is configured in the separate `infra` repo
  (`apps/ziptrigo-apps/nginx.conf`, both `app.ziptrigo.com` and `app-staging.ziptrigo.com`), not
  here, and is described here as designed, not as deployed (check the issue's checkboxes). Per
  environment there are two zones, routed by a pair of `map`s on the raw `$request_uri` (what
  gunicorn receives; `$uri` is normalized, so it would let `/x/../go/abc` pass for a short link):
  the main zone (`ziptrigo_apps_prod` / `_staging`, keyed on `$binary_remote_addr` -- the visitor,
  since both HTTPS server blocks include `cloudflare-realip.conf` -- for everything except the
  `/go/<code>` shape) at 20 r/s with `burst=100 nodelay`, and **`/go/<code>` on its own looser
  zone** (`ziptrigo_apps_go_prod` / `_staging`, 50 r/s, `burst=500`) instead of an exemption: a scan
  burst from one NAT address must never see an error, the same reason the app itself only skips the
  scan-count write there, but a flood of made-up codes (one lookup each, which the app's own rule
  does not shed: an unknown code over its limit is still looked up and 404s) is still shed. The
  `/go/` shape is Django's route (`^go/(?P<short_code>[^/]+)/?$`) read off the raw URI, so
  dot-segment, percent-encoded and `/go/a/b/c` paths are counted by the main zone. The numbers are
  far above ordinary use (the receipts list polls every 3 s, htmx search per keystroke pause, upload
  JSON calls), so the app's own limits above remain the real ones. Over the limit nginx answers
  **429** with a short plain-text body and `Retry-After: 1`, not the app's JSON or htmx partial:
  `core/base.html`'s `htmx-config` swaps 429, so an htmx request rejected by nginx swaps that text
  into its target, acceptable during a flood. nginx keys IPv6 on the full /128, unlike the app's
  /64. Presigned S3 uploads never reach nginx at all. Nothing in this repo depends on it.

### API

`config/api.py` builds one `NinjaAPI`; each app exposes a `router` from its `api` module/package
and is mounted under its prefix (`/api/` for accounts, `/api/billing/`, `/api/qr/`, `/api/ft/` for
file_transfer, `/api/receipts/` for receipts). Docs at `/api/docs`.

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

`apps/receipts/api/` (issue #98 phase 4; `/api/receipts/`) is JWT-only like the file_transfer one
(router-level `auth=JWTAuth()` in `api/router.py`, so no endpoint can be added without it; endpoint
modules `receipts.py`, `uploads.py`, `export.py`, `categories.py`, `vendors.py`, `inbox.py` share
that one `Router`; schemas in `apps/receipts/schemas/`) and calls the exact same
`apps.receipts.services` the web views do. Paths are relative to the mount, so the list is
`/api/receipts/receipts`: `GET /receipts` (`status` -- one or several comma-separated, plus the
`in_progress` shorthand for the three in-flight ones --, `category` an id or `none`, `vendor` a
vendor id (an id that isn't the caller's matches nothing, like an unknown category id), `q`,
`date_from`/`date_to` on `receipt_date`, `sort`, `duplicates`, `trashed` to list the Trash, `limit`
1-100 default 20 / `offset`, answering `{count, limit, offset, results}` like the ft list),
`GET|PATCH|DELETE /receipts/{id}` (`GET` also returns a trashed receipt; `PATCH` is partial via
`payload.dict(exclude_unset=True)`, an explicit `null` clears a field -- unlike `PATCH /inbox`,
where `null` means unchanged -- and both update schemas are `extra='forbid'`, so an unknown key such
as `status` or a typo is a 422, never a silent no-op; `DELETE` moves to the Trash and answers 204,
idempotently), `POST /receipts/{id}/restore|reprocess|dismiss-duplicate`, `GET
/receipts/{id}/file?disposition= attachment|inline` (a JSON `{url, disposition, filename,
expires_in}` with a short-lived presigned URL -- never a redirect, never the S3 key; `filename` is
`services.file_name`, the very name the URL downloads under, so an email body is
`email.html`/`email.txt`, not empty), `POST /uploads` (`{filename, size, content_type?,
checksum_sha256}` -> 201 `{receipt_id, url, headers, expires_in}`, the receipt's `source` being
`api`) and `POST /uploads/{id}/complete` (idempotent; answers the receipt), `GET /export.csv` (same
filters, a streamed `text/csv`; filters are validated before streaming starts so a bad one is a 400
and not a broken download, and before `RECEIPTS_EXPORT_USER` is charged so it costs no budget -- the
web export parses its lenient filters before charging too, to keep the two in step; it takes
`vendor=` too), `GET|POST /categories`, `POST /categories/reorder` (`{ids}`),
`PATCH|DELETE /categories/{id}`, the **vendor endpoints** (issue #123, `api/vendors.py`; the same
`services.vendors` the Vendors page calls, with no new rate-limit rule, like categories):
`GET /vendors` (`status=all|to_vet|vetted`, `q` over names and alias names, `limit`/`offset`,
`{count, limit, offset, results}`; ordered by name), `GET|PATCH|DELETE /vendors/{id}` and
`POST /vendors/merge` (`{keep, merge: [ids]}`, answers the kept vendor). A vendor is `{id, name,
vetted, vetted_at, aliases, receipt_count, trashed_count, last_receipt_date}`: `aliases` are the
*other* names it is known by (names only, oldest first; `services.with_alias_names`), and the
counts are the Vendors page's -- `receipt_count` and `last_receipt_date` over **live** receipts,
`trashed_count` the ones in the Trash (the page shows them as "+N in Trash"; a vendor is
deletable only when both are 0). `PATCH` takes `name` and/or `vetted` (`extra='forbid'`, so an
unknown key is a 422; an explicit `null` means unchanged, as in `PATCH /inbox`): a `name` renames
through `rename_vendor` and, like the web dialog, leaves the vendor vetted unless the same body
says `"vetted": false`; `vetted` alone calls `set_vetted`. A rename another vendor's name or key
clashes with is a **409** `{detail, conflicting_vendor_id, conflicting_vendor_name}` (declared as
the 409 response of the operation, from `VendorNameTaken.other`; nothing is changed) so a client
can offer `POST /vendors/merge`. `DELETE` answers 204, or a 400 when the vendor still has receipts
(live or trashed) with the service's "merge it instead" message. In `merge`, a repeated id or
`keep` itself is ignored, no other id is a 400 (`Choose at least two vendors to merge.`), and one
that isn't the caller's (including `keep`) is a 404 before anything is merged; `merge` holds at
most `MAX_MERGE_IDS` (100, `schemas/vendor.py`) ids, a longer list is a 422, so one request can't
lock an unbounded set of vendors in a single transaction. A vendor deleted between the ownership
check and the service call (merged away or deleted from another tab; `rename_vendor` re-reads the
row under its lock) is a 404 too, not a 500 (`_vendor_errors` in `api/vendors.py`); the check
itself is a plain lookup, and the statistics and alias names are built only for the response.
`GET|PATCH /inbox` (address, `sender_allowlist`, `default_currency`, the latest
`forwarding_confirmation` of the last 7 days via `services.forwarding`, shared with the settings
page) and `POST /inbox/regenerate`. Receipts carry every field of the model except the S3 keys, the
raw AI response and the upload checksum; `amount` is a string with two decimals, `category` is `{id,
name}`, `possible_duplicate_of` an id, `missing_fields` lists which of vendor/amount/receipt date
are unset. A receipt's `vendor` is still the vendor's name; `vendor_id` (`null` while none),
`vendor_vetted` and `vendor_extracted` (what the AI read, before matching) came with issue #123, and
`PATCH /receipts/{id}` takes `vendor_id` next to `vendor`: either moves *that receipt* through
`update_receipt` (so matching, vetting and teaching apply as on the web; but sending the vendor the
receipt already has -- the same `vendor_id` or the same name -- changes nothing, its vetted state
included, so to vet a vendor use `PATCH /vendors/{id}` with `{"vetted": true}`), both at once is a
400 (`vendor_id: Send either vendor or vendor_id, not both.`), `null` clears the vendor, and a
`vendor_id` that isn't the caller's is a **400** (`vendor_id: Unknown vendor.`, like a foreign
`category`: the receipt in the path exists, the value is what is refused), not a 404.
Status codes (`api/common.py`): **400** for a service `ValidationError` (field-keyed ones as
`field: message`, including the daily file cap) or one of the few hand-parsed query parameters
(`date_from`/`date_to` and `disposition`), **422** for everything django-ninja validates itself
before an endpoint runs -- a non-UUID `{receipt_id}`, `limit=abc`, `duplicates=maybe`, a wrongly
typed body field, an unknown `PATCH` key -- whose `detail` is a *list* of `{loc, msg, ...}` rather
than a string (the CLI's `_error_text` flattens it), **402** for
both `AccountFrozenError` (storage unpaid: new uploads, re-runs, `.../file`) and
`InsufficientCreditsError` (a re-run the balance can't cover) -- one documented code, as in the
file_transfer API; reading, listing, editing and exporting stay available while frozen, **404** for
another user's (or an unfinished upload's) receipt, category or vendor (also a foreign id inside a
merge) and never 403, **409** for a vendor rename that clashes with another vendor (above),
**429** from the site-wide `RateLimitExceeded` handler with `Retry-After`. The rate limits are the
web's, so the budgets are shared: `RECEIPTS_UPLOAD_USER` (`POST /uploads`),
`RECEIPTS_UPLOAD_COMPLETE_USER` (`.../complete`), `RECEIPTS_REPROCESS_USER`, `RECEIPTS_EXPORT_USER`
and `RECEIPTS_REGENERATE_USER`.
Where the endpoint looks up a receipt, the lookup (404) comes before its rate limit is charged
(`reprocess`); `POST /uploads/{id}/complete` is the exception and charges
`RECEIPTS_UPLOAD_COMPLETE_USER` first, like the web view. Tests: `apps/receipts/tests/
test_api_*.py` over Django's test client (the real `/api/receipts/...` URLs), helpers in
`tests/api_support.py`.

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
  same way and need the same resume-matching logic. The same file's `initDropZone` makes each page's drop zone accept dropped files *and* folders (walked recursively, flat like the "Add folder" picker), feeding the same `handleFileSelection`. Presigned part URLs are requested a small batch
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
#58), `BillingSettingsAdmin` (`/admin/billing/billingsettings/`, the storage price shared by all
products -- issue #98) and `FileTransferSettingsAdmin`
(`/admin/file_transfer/filetransfersettings/`, spec section 9, plus the issue #59
`auto_hold_report_threshold`). All three share the
`apps.core.admin_singleton.SingletonSettingsAdmin` base (superusers only, add only while no row
exists, never delete, changelist redirects to the change form; the URL name is derived from the
model), and each loads its row with the model's own `.load()` classmethod
(`apps.core.models.singleton.load_singleton`, which re-reads the row if a concurrent first use
loses the insert race) rather than a fixture.

`AbuseReportAdmin` (`/admin/file_transfer/abusereport/`, issue #59) is a read-only report list --
filter by status/reason/date, a link to the reported transfer -- with three bulk actions: "Dismiss",
and "Take down"/"Block sender" (both two-step, an intermediate confirmation page collecting a
reason, same shape as `CreditTransactionAdmin`'s adjustment tool). `TransferAdmin`
(`/admin/file_transfer/transfer/`) stays read-only in the ordinary sense (its change form still has
no save button) but gets the same "Take down"/"Block sender" bulk actions plus "Release abuse-review
hold" -- an admin *action* is independent of `has_change_permission`, the same way Django's own
built-in `delete_selected` needs only `has_delete_permission`, so these work without loosening the
model's read-only-ness at all. `BlockedSenderAdmin` (`/admin/file_transfer/blockedsender/`) is plain
CRUD; `save_model` stamps `created_by` from `request.user` on creation. See "File transfer
specifics" below for the takedown/block-list/auto-hold mechanics these actions drive.

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

QR code specifics: a tracked code encodes `<BASE_URL>/go/<code>`, so in prod every printed code
reads `https://app.ziptrigo.com/go/...` -- the chosen short-link host (issue #49; there is no
separate short-link setting), and prod's `BASE_URL` must never change once codes are out.
Previews are returned as PNG `data:` URIs and never written to disk. Short codes
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
email gets confirmed. Only the session login page and the Google callback fire
`user_logged_in` (signup never starts a session), so those are the two places
claiming happens. **User decision:** claiming is also deferred while the account's balance is
below `services.send.MIN_BALANCE_TO_SEND` (1 credit, the same minimum required to start a logged-in transfer) -- claiming turns a free transfer into a
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

**Abuse reports, takedown and the block list** (issue #59, deferred from #55's phase 3 spec
section 16): the download page has a "Report this transfer" link (an htmx-collapsed
`AbuseReportForm`, `views.download.report_transfer`, `POST /t/<slug>/report/`) -- reason, optional
free-text details (2,000 chars) and an optional reporter email, no login and **no password**
(reporting must work for anyone who merely has the link, not just someone who could unlock it).
Only for a transfer that's currently *available*: an already-unavailable one returns `HX-Refresh`
(this endpoint is only ever posted to via htmx, so `_unavailable`'s full 404 page -- a status
outside this app's `htmx-config` swap list -- would otherwise just sit there doing nothing; a
full-page reload lands on the ordinary neutral "no longer available" page instead). Rate limited
per IP (`FT_REPORT_IP`, strict) and per transfer (`FT_REPORT_TRANSFER`, looser -- many different
IPs genuinely reporting the same transfer shouldn't be throttled by the IP rule alone); the
per-transfer rule is *peeked* before validation/creation and only actually counted (`hit_value`)
once `services.reports.create_report` succeeds, so a run of failed attempts (bad reason, the
dedupe window) can't exhaust a transfer's whole budget before a genuine report gets through, and
the slug is looked up before this key is ever touched so a made-up slug can't create one either.
`create_report` also dedupes an obvious repeat (same *normalized* IP, same transfer, within a
10-minute window) as a `ValidationError`, shown as a form error rather than a second row --
normalized with `apps.core.ratelimit.normalize_ip_for_key` (an IPv6 address collapsed to its /64,
an IPv4-mapped IPv6 address unwrapped to plain IPv4), the same helper rate limiting itself uses,
so the same visitor can't dodge the window just by reporting from a different address in their own
/64.

**Takedown** is a distinct terminal status, `TransferStatus.TAKEN_DOWN`, not a reuse of `DELETED`
-- so the dashboard, the API and any future reporting can always tell an admin takedown apart from
the sender's own delete-now, even though both end the transfer and delete its files (including the
"download all" zip) the same way (`services.lifecycle.end_transfer`). `services.takedown.take_down_transfer`
records `taken_down_by`/`taken_down_at`/`takedown_reason` and marks every pending report against
the transfer `ACTIONED` -- **inside the same transaction as the status change**, via
`end_transfer`'s `before_delete` hook, and *before* the S3 delete is even attempted: if that call
raises (an S3 outage), the admin still sees an error, but the transfer isn't left stuck `ACTIVE`
with its reports still `PENDING` -- `files_deleted_at` simply stays null until a retry (or the
deferred-deletion sweep, see below) succeeds. It's reached only from the admin
(`apps.file_transfer.admin`), never a dashboard or API action a sender can trigger:
`TransferAdmin`'s bulk "Take down selected transfer(s)" action and `AbuseReportAdmin`'s "Take down
the reported transfer(s)" action (which dedupes the selected reports down to their distinct
transfers first) both go through the same two-step confirmation flow as `CreditTransactionAdmin`'s
credit adjustment tool -- an intermediate page (`admin/file_transfer/takedown_confirm.html`)
collects a `reason` (**always** shown to the sender, on the dashboard and in the email if
notified -- never who reported it), a separate `internal_note` (staff-only, never shown to the
sender anywhere), and a "notify sender" toggle, **default off**: the notification email only goes
out when staff explicitly opt in. That choice is recorded on the transfer itself
(`Transfer.takedown_notify`) rather than only passed as an argument, so if the S3 delete above
fails and `jobs.expire_transfers`'s deferred-deletion sweep is the one that eventually finishes it,
the sweep honours the choice staff actually made for a `TAKEN_DOWN` transfer instead of its own
always-notify default (every other ended transfer still gets the sweep's normal behaviour). The
notification email itself (`services.emails.send_files_deleted_notification`) uses wording distinct
from an ordinary expiry/deletion ("your transfer was removed", optionally including the reason --
already visible on the dashboard either way) since "deleted and no longer available" reads like the
link simply ran its course; it's sent to `sender_email` when the transfer has no `owner`
(anonymous), rather than silently doing nothing. The public page shows the same neutral "no longer
available" message as any other unavailable transfer; the owner's dashboard shows `TAKEN DOWN` plus
`takedown_reason` (never `takedown_internal_note`, never the reporter's identity); the API reports
`status: "taken_down"` like any other terminal status, no schema change needed.

**The block list** (`BlockedSender`: `kind` `EMAIL`/`IP`, an exact email or a `*@domain` wildcard
(matches only that exact domain, not its subdomains) or an IP address/CIDR -- IPv4 and IPv6 both,
`services.blocklist`) is checked at every point a transfer's sender identity is established,
confirmed, or used to send again: logged-in draft creation (`views.send.send_page`, on the owner's
email + client IP) and `POST /api/ft/transfers/`; logged-in finalize/send
(`services.send.finalize_send`, now on the owner's email *and*, when the caller has one, the
client IP -- threaded through from both the web send page and the JWT API's finalize endpoint);
anonymous draft creation (`views.anonymous.send_page`, IP only -- no sender email exists yet);
anonymous email confirmation, both when it starts (`services.anonymous.start_confirmation`, the
address just typed in) and when it completes (`services.anonymous._activate`, the confirmed
address plus the confirming IP); and, on an *existing* transfer, adding recipients
(`services.actions.add_recipients`), resending a recipient's email
(`resend_recipient_email`), and re-enabling (`reenable_transfer_action`) -- all three are send
paths too (a blocked spammer could otherwise keep emailing new addresses, or re-enable a disabled
transfer, from anything they already had running), checked against the owner's email plus `ip`
when the caller has it. **Decision**: blocking a sender otherwise leaves their *existing* transfers
running -- it only stops these send paths and starting a new one. The block-sender confirmation
form has two opt-in (default off) extras: "also take down this sender's other active transfers"
(`services.blocklist.active_transfers_for_sender` + `services.take_down_transfer` per match;
requires the `takedown_transfer` permission even for a staff user who otherwise only has
`add_blockedsender`, checked separately so the checkbox can't be used to bypass that permission),
and, for an owned transfer, "also deactivate the sender's account" (sets `User.status` to
`STATUS_INACTIVE`, which `JWTAuth` and session login already reject on their own -- belt-and-
suspenders alongside the email block, since a logged-in sender could otherwise dodge it via
`PUT /api/account`). `block_ip` itself defaults **off** on this form (a shared/CGNAT or office IP
can put unrelated senders behind the same address). A block never says *why* --
`blocklist.BLOCKED_MESSAGE`, "Sorry, you can't send transfers at this time" -- rendered as a plain
403 page for the two draft-creation checkpoints (nothing to submit yet) and surfaced through the
existing `ValidationError`/422 machinery everywhere else (`blocklist.BlockedSenderError` is a
`ValidationError` subclass, so every existing `except ValidationError` already handles it
correctly, and a new `services.actions.TransferHeldError` -- see auto-hold below -- follows the
same pattern); the JWT API instead maps `BlockedSenderError` to 403 specifically (catching it
before the generic `ValidationError`-to-400 mapping), per the issue's decision that a block is
worth its own status there. Re-blocking a value whose earlier block had lapsed *reactivates* that
row (clears `expires_at`) rather than silently creating nothing, which is what a plain
`get_or_create` used to do since it matched an expired row without checking `expires_at` at all;
looking only among *active* rows also means two identical active rows (nothing stops that through
the admin's own CRUD) can no longer make that lookup raise `MultipleObjectsReturned`.
`BlockedSender.clean()` validates `value` against `kind` (a real email or `*@domain` wildcard; a
parseable IP/CIDR) and refuses a second *active* duplicate -- an *expired* duplicate is fine, since
that's just the value's block history and a `UniqueConstraint` can't distinguish the two without a
condition awkward enough (`expires_at IS NULL OR expires_at > NOW()`, not something either SQLite
or Postgres can index portably as a fixed condition) that it wasn't judged worth adding; the
application-level check in `clean()` plus `services.blocklist`'s own "look only among active
entries" logic cover the same ground without it. `TransferAdmin` and `AbuseReportAdmin` both offer
a "Block sender of this transfer" convenience action (`services.blocklist.block_transfer_sender`,
same two-step confirmation shape as takedown); `BlockedSenderAdmin` itself is plain CRUD for staff,
with `expires_at` for a temporary block.

**Auto-hold** (optional, `FileTransferSettings.auto_hold_report_threshold`, `0`/default = off) puts
a transfer on hold once it has that many *pending* reports from *distinct, normalized* IPs
(`services.hold.maybe_hold_for_reports`, called from `create_report`) -- counting normalized keys
(`apps.core.ratelimit.normalize_ip_for_key`, same as the report dedupe above and rate limiting
itself) rather than raw report rows or exact stored addresses, so one visitor mashing "report"
can't hold a transfer alone, and one host can't reach the threshold alone just by rotating its
address within its own /64. **Caveat, worth documenting because it's easy to assume otherwise**:
this only raises the bar, it doesn't remove it -- anyone genuinely controlling N distinct real IPs
(N distinct /64s, for IPv6) can still trigger a hold alone, so pick `auto_hold_report_threshold`
with that in mind rather than as a hard guarantee of N distinct reporters. **Decision:** the hold
itself is a plain flag, `Transfer.held_for_review_at`, never `TransferStatus.SUSPENDED` -- that
status is owned by `services.metering` (out-of-credits) and is undone by the `credits_added` signal
and deleted by that status's own grace-period job (`delete_files_past_grace_period`); an abuse hold
must survive both untouched, so it needed a mechanism neither of those two look at. A held transfer
keeps whatever status it already had (normally `ACTIVE`) underneath: `services.downloads.is_available`
treats a non-null `held_for_review_at` as unavailable (same neutral page, so every public download
endpoint -- the page itself, per-file/zip download, zip status -- already refuses it for free), and
`services.metering.meter_transfer` skips billing while it's set (re-checked under the row lock, and
`jobs.meter_transfers`'s own query also excludes held rows up front) -- no backlog accumulates, the
same way none does across a disabled/suspended stretch. `jobs.expire_transfers` also skips the
"expires tomorrow" reminder for a held transfer (misleading when the link doesn't work right now)
but still lets it expire naturally on schedule if its `expires_at` passes -- the retention promise
holds regardless of an ongoing review.

**A hold freezes the transfer** (`services.actions._require_not_held`, `TransferHeldError`): while
`held_for_review_at` is set, every owner action on it is refused except *disabling* it outright --
add recipients, resend, extend/shorten expiry, change max downloads/password, delete now, and
re-enable are all blocked, web-side with a plain error in the dashboard row (422, same as any other
`ValidationError`) and API-side with **409** (`TransferHeldError` mapped before the generic
`ValidationError`-to-400 mapping, the same pattern `BlockedSenderError`/403 uses -- 409 was picked
over 400 as a better fit for "the resource is in a conflicting state", and is documented here since
the issue left the choice open). Disabling stays available regardless: taking the link down
entirely is never something a hold should prevent.

Staff release a hold from `TransferAdmin`'s "Release abuse-review hold" action (dismisses its
pending reports at the same time, so the very next report doesn't immediately re-trigger it, and
resets the billing clock -- `last_billed_at = now`, same as `services.metering.reenable_transfer`
does for a suspended transfer coming back, so a held stretch is never billed for after the fact) or
take the transfer down instead; dismissing the *last* pending report against a held transfer through
`AbuseReportAdmin`'s "Dismiss" action releases the hold too (`services.reports._release_hold_if_clear`).
No staff-notification email on a hold -- there's no `ADMINS`/admin-email setting in this project to
send one to, so this was skipped rather than invented (see Known gaps).

**Admin permissions** (issue #59 code review): a plain `ModelAdmin` action is independent of
`has_change_permission` (same as Django's own built-in `delete_selected`), so without an explicit
check, any staff user who could merely *view* the transfer or report list could take a transfer
down (permanently deleting its S3 objects), release a hold, dismiss a report, or add block-list
entries. `take_down_action`, `release_hold_action` (both on `TransferAdmin`) and `dismiss_action`
(on `AbuseReportAdmin`) all require a new custom permission, `file_transfer.takedown_transfer`
(`Transfer.Meta.permissions`); `block_sender_action` on both admins requires the model-default
`file_transfer.add_blockedsender`. Superusers pass implicitly, same as every other permission
check in this project. `take_down_action` and `block_sender_action` go through an intermediate
confirmation page before applying; `release_hold_action` and `dismiss_action` apply immediately
from the changelist, same as Django's own built-in actions.

### Feedback

`apps.feedback` (issue #82) is the footer's "Feedback" page (`core/base.html` links to it with
`{% url 'feedback:submit' %}`; a template link isn't an import). It lives above `accounts` rather
than in `core` because it needs the user (the `created_by` FK, the submitter's address, a lookup of
superusers) and `core` can't import `accounts`; it sits in the same layer as `billing`.

- **Login required.** `GET/POST /feedback/` is an HTMX form view (`views/feedback.py`) that follows
  the 422 convention; success is `hx_redirect` to `/feedback/thanks/` (post/redirect/get, so a
  refresh never sends it twice). The `FEEDBACK_USER` rate limit (10/hour per user, because every
  submission emails every superuser) is checked only after the form validates, so rejected
  submissions don't spend it. No `/api/` endpoint.
- **`Feedback`** (UUID id, `created_at`, `updated_at`, `created_by` `SET_NULL`, `description` up to
  5,000 characters, `status` `new`/`in_process`/`closed`). `services.validate_description` strips
  the text and normalizes `\r\n` to `\n` (a browser submits `\r\n`, which would otherwise count
  twice against the cap); the form and `submit_feedback` both go through it.
- **Emails** (`services/emails.py`) are `django.tasks` tasks enqueued in `transaction.on_commit`
  from `submit_feedback`: a receipt to the submitter and a notification to every superuser who is
  `is_active`, has `status=ACTIVE` and a non-empty email (one `send_email` each; no active
  superuser logs a warning). Both pass an explicit autoescaped `html_body` -- without one
  `SesEmailBackend` wraps `text_body` in an unescaped `<pre>`, so feedback containing HTML would
  render as HTML in the admins' inboxes -- and no feedback text ever goes into a subject.
  The receipt goes only to a user whose `email_confirmed` is true: `PUT /api/account` un-confirms
  an account on an email change but leaves the session valid, so otherwise someone could aim a
  receipt (their text and name) at a third party's address. Submitting is *not* blocked for an
  unconfirmed user, and superusers are still notified; the form and thanks page just leave out the
  "a copy will be emailed to ..." wording in that case.
- **Thanks page** is reachable only right after a submission: `feedback_page` sets a one-shot
  session flag and `thanks_page` pops it, redirecting to the form without it. A 422 response also
  clears a leftover rate-limit message with an out-of-band `#feedback-msg` swap.
- **Admin** (`FeedbackAdmin`): filter by status and date, search, everything read-only except
  `status`, no add permission, and bulk "Mark selected as New / In process / Closed" actions that
  set `updated_at` explicitly (`QuerySet.update` skips `auto_now`); the actions require the
  `change` permission (`permissions=['change']`), since an action ignores `has_change_permission`.

### Receipts

`apps.receipts` (issue #98; app label `receipts`) takes receipts and invoices the user *receives*
and extracts vendor, amount, date and a category with an OpenAI-compatible vision model. It is a
product (same layer as `qr_code`/`file_transfer`). **Built so far:** models, settings, the
`receipts` queue, uploads, the processing/AI pipeline, billing, jobs, admin (phase 1), email
intake (phase 2: the S3 poller, MIME parsing, `ingest_message`, `receipts_ingest_eml`) and the web
UI (phase 3, "Web UI" below) and the JWT API and `admin/receipts.py` CLI (phase 4, "API and CLI"
below; the API itself is described under "API" above).
Layout like `file_transfer`: `models/`, `services/`, `jobs.py`, `admin.py`, `checks.py`,
`management/commands/`.

- **Models.** `ReceiptsAccount` (pk = the user; `default_currency`, `sender_allowlist`, and the
  storage-billing state `stored_bytes`/`last_billed_at`/`accrued`/`billed_days`/`frozen_at`) is
  created lazily by `services.accounts.get_account`, which also seeds the 12 default
  `ReceiptCategory` rows and issues the first `InboxCode`. Codes are 8 lowercase characters from an
  alphabet without look-alikes (`i l o 0 1`), unique across *all* rows and never reissued: a
  regenerated code is retired (`active=False`, `retired_at`) and kept forever, with a partial unique
  constraint allowing one active code per account. `Receipt` is one stored file: `receipt_date`
  (the date on the document) is separate from `created_at`; `vendor`, `amount` (Decimal, 2 dp) and
  `receipt_date` are the *required* fields (all nullable/blank until filled); `notes` is the user's
  alone (max 5,000) and never touched by the AI or a re-run; trash is `deleted_at`, not a status.
  `status`: `received` -> `processing` -> `processed` | `needs_review` | `failed`, plus
  `waiting_for_credits`; `failure_reason`: `multiple_receipts`, `not_a_receipt`, `unreadable`,
  `unsupported_type`, `too_large`, `extraction_error`. Beyond the issue's list `Receipt` also has
  `preview_size_bytes` (so a purge subtracts exactly what was added), `upload_checksum_sha256`,
  `processing_started_at` (what `retry_due` uses to find a receipt stuck in `processing`) and
  `last_error`, and (issue #123) `vendor_ref` / `vendor_extracted` (see "Vendors");
  `InboundEmail` has `attention_notified_at`; `InboundMessage` has `failed_key`. `ReceiptsSettings` is a
  `SingletonSettingsAdmin` singleton (`price_per_file`, `max_file_bytes` 15 MB, `allowed_types`,
  `min_inline_image_px`, `max_files_per_user_per_day`, `max_inbound_rows_per_day`, `trash_days`,
  `max_pdf_pages_to_ai`, `max_ai_attempts`); the per-GB storage price is the shared `BillingSettings` one.
- **Storage.** `services.storage.get_storage()` builds the core `S3Storage` from the `RECEIPTS_*`
  settings with `signature_version='s3v4'` (required: `presigned_put_url` fails closed on a client
  that doesn't sign `Content-Length`). One bucket shared with file transfer; keys
  `receipts/users/<user_id>/files/<receipt_id>` (id only, never the filename),
  `.../previews/<receipt_id>.jpg`, `.../emails/<email_id>.eml`, `receipts/inbound/` for SES, and
  `receipts/failed/` for inbound messages that failed to ingest (see "Email intake").
  Everything of a user sits under one prefix so deleting an account is one `delete_prefix`: the
  `pre_delete(User)` receiver (`apps/receipts/apps.py`, `weak=False`) queues `delete_user_storage`
  on commit. Tests swap in `FakeReceiptsStorage` (autouse in `apps/receipts/tests/conftest.py`).
- **Uploads** (`services.uploads`): `start_upload` validates (frozen account, size, allowed type --
  the claimed type is normalized, falling back to the extension for browsers that send none -- a
  base64 SHA-256, and the daily cap), creates a `Receipt(uploaded=False)` and presigns a single PUT with the exact
  `Content-Length` and `x-amz-checksum-sha256` signed in. The **daily cap** is a counter on the account
  (`ReceiptsAccount.intake_count` over a 24-hour window starting at the first file,
  `intake_window_started_at`; `accounts.take_intake_slot`, run under a row lock in the same
  transaction that creates the `Receipt`), deliberately not a count of `Receipt` rows (deleting
  receipts would free slots) and not the rate limiter (static rules, fails open, off under pytest);
  being a fixed window it allows up to twice the cap across a boundary, and email intake calls
  the same function, once per file. `complete_upload` checks the object's size, reads it back, compares
  `base64(sha256(data))` with the declared checksum **unconditionally** (not only when S3 echoes one
  back), sniffs the **magic bytes** (`documents.sniff_content_type`; a
  mismatch with the claimed type deletes the object and fails the receipt `unsupported_type`),
  computes the SHA-256 server-side, adds to `stored_bytes` and queues processing on commit.
  `cleanup_uploads` removes uploads not completed within 24 h.
- **Processing** (`services.processing`, task `process_receipt` on the `receipts` queue; read its
  module docstring). A conditional `UPDATE` claims `received`/`waiting_for_credits` -> `processing` (not before
  `next_attempt_at`, so a stray duplicate task can't skip a backoff);
  in the same `atomic()` it charges `price_per_file` **once per `attempt_id`** (`charged_attempt_id`;
  a price of 0 skips `spend_credits`). A frozen account or `InsufficientCreditsError` parks the
  receipt in `waiting_for_credits`. Input preparation (`services.documents`: HEIC -> JPEG via
  `pillow-heif` with the JPEG kept as the preview, downscale, an explicit `MAX_IMAGE_PIXELS` check (Pillow's process-wide limit is left alone), PDF
  text via `pypdf` or rendered pages via `pypdfium2` -- each page is sized with `get_size()` first
  and rendered at a scale that keeps its bitmap under `MAX_IMAGE_PIXELS` (a page over 14,400 pt on a
  side is unreadable), the PIL image going straight to the JPEG encoder -- HTML -> text with the stdlib parser) and the AI call
  (`services.ai`) happen outside any transaction; the result is applied under a row lock only if
  the receipt is still `processing` for the same `attempt_id`. A HEIC preview is
  written before its conditional `UPDATE`; if that matches nothing and no row references the key
  (the receipt was purged, or its user deleted, meanwhile) the object is deleted again. The AI client is
  `ai.get_client()` (tests: autouse `FakeAIClient`; the suite never touches the network); its reply
  is validated with pydantic (`ai.parse_response` first unwraps one code fence, one-item list or
  JSON string around the object -- an OpenAI-compatible endpoint need not enforce the strict
  `json_schema`, and `qwen/qwen3.6-plus` replies `[{...}]` -- and logs the start of a reply that
  still fails, card-like digits masked), `payment_method` is masked to its last 4 digits (a run of 5+ digits
  joined by single spaces/hyphens, or 12+ digits joined by any whitespace, dots, hyphens or slashes;
  also for hand-typed values and the copy kept in `ai_result`), a `category` is matched case-insensitively against the user's own list.
  Outcomes: more than one receipt -> `failed/multiple_receipts`; not a receipt ->
  `failed/not_a_receipt` (neither fills any field); a required field missing or confidence below
  0.5 (unless the user supplied all three) -> `needs_review`; else `processed`. `user_edited_fields`
  are never overwritten. **Transient** AI errors (timeouts, 5xx, 429, malformed reply) put the
  receipt back to `received` with `next_attempt_at` (5 min / 30 min / 2 h backoff) for the
  `retry_due` job -- neither `django.tasks` retries nor `run_after` (the Immediate backend rejects
  it) -- and after `max_ai_attempts` it fails `extraction_error`. **Refunds** go through
  `add_credits(tx_type=REFUND, source='receipts')` guarded by a compare-and-swap on `refunded_at`:
  for retries exhausted, a permanent AI error, and a file that could not even be read (no AI call
  was made -- a deliberate extension of the issue, which only mentions exhausted retries). A receipt
  the AI *did* answer about (not a receipt, several receipts, needs review) stays charged.
  `credits_added` fires for a refund too; that is fine, the receiver is idempotent. Duplicates
  (`services.duplicates`): another live receipt of the user with the same `sha256`, or the same
  vendor + date + amount (oldest wins; a receipt flagged *as a duplicate of this one* isn't offered
  back); dismissing sets `duplicate_dismissed`. Tasks are queued with
  `enqueue_after_commit` (`apps.core.services.queue`; on commit, a failure logged and swallowed -- `retry_due`
  re-queues receipts nobody picked up).
- **Vendors** (issue #123; `models/vendor.py`, `services/vendors.py`, migrations `0003_vendors`,
  `0004_backfill_vendors` and `0005_unlinked_vendor_index`). A `Vendor` is one of a *user's* vendors (per user, like categories;
  `UniqueConstraint(Lower('name'), 'user')`); `vetted_at` null means only the AI read it. A
  `VendorAlias` is a name it is known by, stored under a normalized `key` (`normalize_key`: NFKC,
  casefold, Latin accents stripped, `&` -> "and", dots and apostrophes removed, other punctuation a
  word break, a leading "the" and trailing legal forms like LLC/Inc/Ltd/GmbH dropped, never down to
  nothing; `ACME Industries, LLC`, `Acme Industries LLC` and `ACME INDUSTRIES L.L.C.` are all
  `acme industries`); **a key belongs to one vendor per user** (`UniqueConstraint(user, key)`), which
  is what makes matching deterministic. `Receipt.vendor_ref` is a nullable FK with `on_delete=RESTRICT`
  (a vendor with receipts can only go through a merge; deleting the *user* still cascades through
  both tables) and `Receipt.vendor_extracted` is what the AI read, before matching. **`Receipt.vendor`
  (the text) stays**, kept equal to `vendor_ref.name`: search, duplicates, the CSV, the API schema,
  the CLI and the "required fields" all read it unchanged. Only processing, `update_receipt`,
  `rename_vendor` and `merge_vendors` write it (the last two with one `UPDATE ... WHERE vendor_ref`,
  setting `updated_at` by hand). `vendor_extracted` has a `db_default`, so the container still
  serving while `0003` runs (it doesn't know the column) can keep inserting receipts (the #102
  lesson).
  - **Matching** (`match_vendor`, run by `processing._fill_fields` unless `'vendor'` is in
    `user_edited_fields`), in the issue's order: (1) a *vetted* vendor, by alias (or by exact name)
    or by a near match (`difflib` ratio >= 0.92, both keys >= 6 characters, exactly one such
    vendor -- never a guess between two); (2) an *unvetted* vendor, by alias or exact name (the
    pipeline never builds on its own guesses); (3) otherwise a new unvetted vendor named as the AI read it, created in a savepoint
    that on `IntegrityError` (a sibling receipt of the same email creating it at the same moment)
    re-reads the winner's, with no lock on the account row. `_find` looks up the alias key first and
    the exact name (case-insensitive) only when nobody holds the key; when they are two different
    vendors (a backfill leaves a vendor without the alias for its own name when another group claimed
    the key) the vetted one wins, the key's holder if both or neither are, so `match_vendor('Staples')`
    and `match_vendor('STAPLES INC')` can't disagree. A near match does not teach an alias. A
    new or unvetted vendor does **not** change the receipt's status. A re-run that matches another
    vendor deletes the unvetted one it left once it has no receipts (`drop_if_orphan`, called after the
    receipt row is saved, because until then it still references it; `lifecycle.purge_receipt` does the
    same).
  - **Teaching and vetting.** `update_receipt` takes `vendor` (a name) or `vendor_id` (one of the
    user's, `vendor_ref` after cleaning; both is an error keyed `vendor_id`) and calls
    `assign_vendor`: it moves *that receipt*, vets the chosen vendor ("the user confirmed it": a name
    typed in, picked, renamed, merged into or marked), points the alias for the name the AI read
    (`vendor_extracted`) at it so the next receipt read that way lands there, and drops the vendor
    left behind if it is unvetted and empty. **Teaching never takes a vetted vendor's own-name key**
    (`_is_own_name_key`: `normalize_key(vendor.name)`): moving a receipt read as "Staples" away from
    the vetted `Staples` still moves the receipt but leaves `staples` with `Staples`, otherwise
    matching would send "Staples" and "STAPLES INC" to different vendors and renaming `Staples` would
    be refused as a clash with the thief. `teaches_extracted_name(user, extracted)` says whether a
    move teaches; the receipt page renders the "future receipts read as ... will be filed under ..."
    hint only when it does (`vendor_field_context`'s `teaches`), so the page never promises it. The
    alias of a typed name found by exact name is tagged `user`, not `extracted`. The vendor the receipt already has, by name or id, is a
    no-op, so saving other fields never vets anything. A typed name resolves to the user's vendor
    with that name or key, else creates a vetted one: typing `Staples Inc` over `Staples` files the
    receipt under the existing `Staples` (the page says so; "Rename vendor" is how to change a name).
    **Renaming** (`rename_vendor`) is the separate action that changes what every receipt of a vendor
    shows; it raises `VendorNameTaken(other)` (a `ValidationError`) when another vendor has the name or
    its key, which the page turns into an offer to merge into that vendor, and keeps both the old and
    the new name as aliases. `merge_vendors` moves receipts (link and text) and aliases to the vendor
    kept, in one transaction under row locks taken in id order, and vets it; it is also the one place
    that re-checks something, running `refresh_duplicate` on the live receipts it moved whose text
    changed (newest first, each flag saved before the next check so two copies don't flag each other,
    bounded by `MERGE_DUPLICATE_CHECK_LIMIT`, a dismissed flag respected), which is fine because a
    merge is an explicit, retroactive user action. `delete_vendor` refuses while it has receipts,
    trashed ones included. **No retroactive matching**: nothing but a rename or a merge changes
    receipts that already exist. **Lock order**: rename and merge lock vendors with
    `select_for_update(no_key=True)` (`FOR NO KEY UPDATE`: excludes each other but not a receipt being
    filed under the vendor) and then update their receipts, while processing holds a receipt lock and
    may then drop an orphan vendor; `drop_if_orphan` therefore locks the vendor `skip_locked` and
    leaves it for the next orphan check or the user when someone has it, instead of waiting (the
    other order would deadlock on Postgres).
  - **Duplicates and search.** `find_duplicate` compares `vendor_ref` (or the text for a receipt
    without one); `ReceiptFilters.vendor` filters by vendor id; `q` also matches `vendor_extracted`
    (so a renamed vendor's receipts are still found by the old name); the CSV has a trailing
    `vendor_as_read` column.
  - **Backfill** (`0004`): per user, receipts with a vendor are grouped by stripped, casefolded text;
    each group becomes a vendor named by its most common spelling (receipts spelled differently get
    that text), vetted if the user typed any of them, with aliases claimed by vetted groups first, then
    by receipt count (own key, then the names the AI read), each only if still free -- a group whose
    key is taken stays a separate vendor, under "To vet", for the user to merge. Reversible (clears
    the links and the vendors). Its tests call `backfill_vendors` directly (the suite runs with
    `--no-migrations`). The migration carries its own frozen copy of `normalize_key` and imports
    nothing from the app (a test checks), so the keys it writes keep their meaning whatever the
    matcher becomes.
  - **Receipts the backfill missed** (`link_unlinked_receipts`). The migration runs while the
    previous release is still serving, and a receipt that release processes afterwards has a vendor
    text and no `vendor_ref` (no chip, absent from the Vendors page and the `?vendor=` filter, not
    updated by a rename or merge). `services.vendors.link_unlinked_receipts(user_id=None)` links
    every such receipt, live or trashed, to the user's vendor *called* that (exact, case-insensitive),
    else to a new unvetted one with its alias when the key is free. It never rewrites `Receipt.vendor`
    and never uses `match_vendor`'s key or near tiers (they could file a receipt under a differently
    named vendor, which is retroactive); it reads in id order in batches, groups by user and name,
    and its `UPDATE` only touches receipts still unlinked (a vendor it just created and no one used
    is dropped again; losing a creation race uses the winner's). It runs as the hourly job
    `receipts.link_vendors` (per-user `try/except`) -- a partial index (`0005`,
    `receipts_unlinked_vendor`, `WHERE vendor_ref_id IS NULL AND vendor <> ''`) makes a run with
    nothing to do a single index probe -- and as `./manage.py receipts_link_vendors`, to run by hand
    right after the deploy.
  - **Web.** The receipt page's vendor field is an ARIA combobox (`static/receipts/js/
    vendor_combobox.js`, suggestions from `GET vendors/suggest/` into a listbox) with a Vetted / New
    vendor chip, "Read on the receipt as ...", "Mark as vetted" and "Rename vendor...";
    `forms.ReceiptForm` carries the picked `vendor_id` but keeps it only while the text still is that
    vendor's name. The **Vendors page** (`/receipts/vendors/`, tabs All / To vet / Vetted, search over
    names and aliases, 50 per page, row menu, bulk bar) follows the dashboard's pattern; its bulk
    checkboxes belong to a hidden `#vendor-bulk` form through their `form` attribute, so the search
    form around the list never sends them. The rename and merge dialogs are fragments fetched into a
    `#vendor-modal` host (`fragmentModal` in `common.js`); a refusal re-renders the form in place
    (422), success is an `HX-Redirect` to `next` with a flash message, and bulk/row actions
    re-render the list from `HX-Current-URL`. htmx 2.0 can't resolve an inherited
    `hx-disabled-elt="find ..."` for a control with no such button inside it (it throws), so every
    htmx control inside the receipt form sets its own, or `unset`. The dashboard has a "Vendors" link
    with "N to vet" and a `?vendor=<id>` filter chip (a hidden input in `#receipt-list`, so it
    travels with search, tabs, paging and the export link; an id that isn't the user's is dropped).
  - **API and CLI.** `/api/receipts/vendors` (list, get, rename/vet, merge, delete), the
    `vendor_id`/`vendor_vetted`/`vendor_extracted` receipt fields, the `vendor=` filter on the list
    and the export, and the `admin/receipts.py` vendor commands are described under "API" and
    "API and CLI" below; the API only maps `services.vendors` (`VendorNameTaken` -> 409 with the
    conflicting vendor, any other `ValidationError` -> 400), it adds no rule of its own.
  - **Admin**: a read-only `VendorAdmin` (aliases inline, vetted filter, searches names and alias
    names, receipt count); `ReceiptAdmin` lists and searches `vendor_extracted`.
- **Notifications** (`services.emails`, tasks on the `receipts` queue, owner only, only to a
  *confirmed* address, autoescaped `html_body`, no sender-controlled text in a subject):
  `send_attention_email` is queued once per `InboundEmail` (CAS on `attention_notified_at`) when its
  last receipt settles and any failed or need review (a settling receipt row-locks its
  `InboundEmail` right after its own row, `emails.lock_inbound_email`, so concurrent siblings run
  the "everyone done?" check one after the other; always receipt, then email) -- email intake
  creates every receipt of an email in one transaction and queues their processing on commit, so
  all siblings exist before the first settles; `send_storage_paused_email` on freeze. Links are
  absolute: `settings.BASE_URL` + `reverse('receipts:dashboard')` / `reverse('receipts:detail', ...)`.
- **Billing** (`services.metering`). `receipts.meter_storage` (daily) bills *per account*:
  `stored_bytes` (originals, previews, stripped emails; trashed ones included until purged) x the
  shared `BillingSettings` price through `billing.storage_charge`, row lock, `last_billed_at +=
  24h`, floor of `accrued`, like file transfer's per-transfer metering. `stored_bytes` changes only
  through `services.accounts.add_stored_bytes` (floored at 0; the billing clock restarts when an
  empty account gets its first bytes). `InsufficientCreditsError` **freezes** the account
  (`frozen_at`) and emails the owner: nothing is deleted, but previews, downloads, new uploads,
  re-runs and processing are refused (`AccountFrozenError`; the API answers 402), and the frozen
  days are never billed (unfreezing sets `last_billed_at = now`). The `credits_added` receiver
  (`weak=False`, wrapped in `try/except` because `send` would otherwise skip file transfer's
  receiver) only queues `resume_account(user_id)`, and only for users with a Receipts account;
  `resume_account` unfreezes only when the balance covers **one day of that account's storage**
  (`metering.daily_storage_cost`: `ceil(price * stored_bytes / 1 GiB)` at the shared price, at least
  1) -- a flat "1 credit" would let a 5-credit-a-day account buy a forgiven day with every 1-credit
  top-up -- and then queues `waiting_for_credits` receipts oldest first, stopping at the first the
  balance can't cover. The daily job also resumes frozen accounts as a fallback, but only those
  that were **already frozen before its metering pass** (it snapshots their ids first); otherwise
  an account frozen in that very run would be unfrozen again at once, its day forgiven and its
  owner told "paused" every day for nothing.
- **Actions** (`services.actions`, shared by the web views and the API; `ValidationError` -> 422/400,
  `AccountFrozenError`/`InsufficientCreditsError` -> 402): `update_receipt` (partial; records the
  extracted fields that actually changed in `user_edited_fields`, `notes` aside; the vendor goes
  through `assign_vendor`, see "Vendors"; refuses extracted
  fields while the AI owns the receipt; moves `needs_review` <-> `processed` as the required fields
  are filled/cleared), `reprocess` (new `attempt_id`, charged again, refused up front when the
  balance can't cover it), `trash`/`restore` (both bump `updated_at`, which `.update()` skips, so `retry_due`'s "never queued"
  branch doesn't re-queue restored rows) /`delete_forever` (refused while the receipt is
  `processing`; `purge_trash` skips such rows too), `dismiss_duplicate`,
  `regenerate_inbox_code`, `update_inbox_settings` (allowlist of emails and `*@domain`, normalized
  by `services.addresses`, plus default currency as an ISO 4217 code), category create/rename/
  reorder/delete (deleting uncategorizes). `services.filters` has `filter_receipts` (status,
  category, `vendor` (an id), `q` over vendor (also the name the AI read) + notes + amount -- a numeric term (`75`, `275,5`, `$75`) also matches
  the amount as text with two decimals, built from whole cents so `75` finds 275.50 the same way on
  Postgres and SQLite --, `receipt_date` range, duplicates, sort; default `-receipt_date` with
  undated first) and the CSV export (`iter_csv`/`export_csv`, columns per the
  issue plus a trailing `vendor_as_read`; every cell goes through the formula guard, which also catches leading whitespace); `services.files` has the presigned inline/attachment URLs
  (an HTML body is *never* served inline from S3 -- `views.detail.body_view` streams it with a CSP; a nameless
  file downloads as `email.html`/`email.txt`/`receipt`).
  The `RECEIPTS_*_USER` rate-limit rules exist in `RATELIMIT_RULES`; the web views and the API both enforce them, sharing each budget.
  `filter_receipts` and `dashboard_counts` (the tab and Trash numbers, one query) never include a
  receipt with `uploaded=False` (a direct upload still in flight or abandoned).
- **Jobs** (`apps/receipts/jobs.py`, registered in `ready()`): `receipts.meter_storage` (daily),
  `receipts.purge_trash` (daily; deletes S3 objects, then rows, past `trash_days`, and the stripped
  `.eml` with the email's last receipt, deciding that under a row lock on the `InboundEmail`), `receipts.retry_due` (5 min; due retries, `processing`
  stuck over 30 min counted as a failed try, `received` rows nobody queued for 15 min),
  `receipts.cleanup_uploads` (hourly), `receipts.poll_inbound` (every minute, 2 min lease; see
  "Email intake" below), `receipts.link_vendors` (hourly; links receipts with a vendor text and no
  vendor, see "Vendors"). Idempotent, per-row `try/except` + `logger.exception`.
- **Queue.** `TASKS['default']['QUEUES']` is `['default', 'receipts']`; every receipts task is
  `@task(queue_name='receipts')`, served by its own `db_worker --queue-name receipts` process (see
  "Queue and scheduler" and "Docker"). Tests: `ImmediateBackend` runs a task inline at
  `.enqueue()` and swallows its exceptions, so assert on database state and use
  `django_capture_on_commit_callbacks(execute=True)` around anything queued on commit; call a task
  as `<task>.call(...)` (or the underlying service function) in a test, not `<task>(...)`.
- **Email intake** (`services.inbound` for the flow and the decisions, `services.mime` for reading a
  raw message; issue #98 phase 2). SES drops each accepted message in `receipts/inbound/`; nothing
  is pushed to us. The `receipts.poll_inbound` job lists up to 100 keys under `receipts/inbound/`
  (`list_keys`), `get_or_create`s an `InboundMessage` (`pending`) per new key and queues
  `ingest_message(id)` on commit, and nothing more: no parsing. It also re-queues `pending` rows
  that are *due* (never claimed and older than 10 min, or a retry whose `next_attempt_at` has
  come; throttled by `queued_at` to once per 5 min so a slow queue doesn't collect a duplicate per
  poll), deletes the object of a `done` message that is still listed (its delete failed) and
  finishes parking a `failed` one (below). `ingest_message` runs on the `receipts` queue:
  - **Claim and retries.** One conditional `UPDATE` (`pending`, `next_attempt_at` null or past)
    bumps `attempts` and sets `next_attempt_at` to a 10-minute lease, so a duplicate task can't
    ingest twice and a worker that dies mid-ingest just lets the lease run out (a message claimed
    more than 3 times is failed as "interrupted"). A failure records `last_error` and schedules the
    retry (1 min, then 5 min; `poll_inbound` re-queues it); the third failure sets `failed`. Over the
    40 MB guard (`get_object_bytes(max_bytes)`), a message `mime.parse_message` refuses
    (`MessageTooLargeError`: more than `mime.MAX_PARTS` (500) MIME parts, more than
    `mime.MAX_HEADER_LINES` (10,000) header lines in total, continuation lines included, a header
    over `mime.MAX_HEADER_BYTES` (64 KB) or headers over `mime.MAX_TOTAL_HEADER_BYTES` (256 KB)),
    unparseable, or an object that is gone (with no work done) fail at once. The task never raises
    (the queue backends don't retry; the row *is* the retry mechanism). The object is deleted only
    after the row is marked `done`. The final writes (`done`, `failed`, the retry schedule) are
    conditional on `attempts` still being the value the worker claimed with (a claim token), so a
    slow worker whose lease ran out can't disturb the one that took over (and doesn't delete the
    object).
  - **Parser limits.** `MAX_RAW_BYTES` caps bytes, not parts, lines or header sizes: 40 MB can be
    millions of tiny `Message` objects, or one part with millions of `a:b` header lines (the parser
    holds a part's header lines in a list before reading them), or one multi-megabyte `To` /
    `Subject` / `Content-Type` (parsing and reading a header with `policy.default` costs far more
    than its length: a 1.5 MB `To` of 80k addresses took 28 s and ~450 MB in `scan_recipients`, a
    1.6 MB `Subject` 20 s, a 250 KB `Content-Type` 2.9 s inside `parse_message` itself, since
    feedparser calls `get_content_type()` while parsing), and any of them is gigabytes or minutes
    -- an OOM kill of `db_worker` restarts the whole container. So `parse_message` counts all of it
    *as the parser reads* and stops at once, per call (no shared state). Parts are the `Message` objects its factory
    creates, so any delimiter shape counts (a `-`-leading or over-70-character boundary, trailing
    whitespace, CR-only endings -- which a pre-scan of the raw bytes missed). Header lines are
    counted by a `BufferedSubFile` subclass put in place of the `BytesFeedParser`'s private
    `_input` (there is no public hook), and only the lines read between a part's creation and its
    first non-header line count, so a body of `Name: value` or indented lines does not. The same
    counter budgets header **bytes**: one logical header (first line plus its continuation lines)
    may have `MAX_HEADER_BYTES` = 64 KB and all header lines of the message, nested parts included,
    `MAX_TOTAL_HEADER_BYTES` = 256 KB (`HeadersTooLargeError`). The margin: SMTP servers cap a
    whole message's headers at 64-100 KB, a normal big message (30 `Received`, 10 DKIM, a
    600-entry `References`, 400 recipients) is ~50 KB, and the cost is ~10-20 microseconds per
    byte, so the worst message that passes is a few seconds of parsing. Because it replaces the
    private `_input`, `parse_message` first checks that it is a plain `BufferedSubFile` and raises
    `RuntimeError` otherwise (the ingest then fails permanently and the parked message can be
    retried from the admin once fixed), and a test spies on `_HeaderLineCounter.readline` so a
    CPython change that stops the parser reading through it fails the suite. The readers that still
    use `get_all` (`_header_addresses`, `From`) are therefore bounded by `MAX_HEADER_LINES` and the
    byte caps. Nothing is stored on the message or its policy, so copying and re-serialising never
    trips a counter.
    Readers that want only the topmost of a repeated header (`Received`, in `scan_recipients`,
    `received_at`, `_first`) use `mime._first_n`: `get_all` builds a header object for every one
    before any slicing.
  - **Failed messages are parked.** Anyone can make a message fail, and a failed object left under
    `receipts/inbound/` would stay in the poller's 100-key listing forever, crowding new mail out
    (the first version listed extra keys per failure as a workaround, which just moved the
    problem). So `_mark_failed` copies the object to `receipts/failed/<same name>`, records it in
    `InboundMessage.failed_key` (`s3_key` stays the original key: it is what identifies the
    message, and a leftover original is still recognised), then deletes the original
    (`inbound.park_failed_object`; the poller repeats it for a `failed` row whose object is still
    listed). Admin "retry" (`reset_message_for_retry`) copies it back, resets the row and deletes
    the parked copy. Nothing expires `receipts/failed/` (the infra lifecycle rule only covers
    `receipts/inbound/`, and the IAM user is scoped to `receipts/*`, which includes it).
  - **Recipients.** One `InboundEmail` per recipient that is an *active* `InboxCode`
    (`receipts+<code>@<RECEIPTS_INBOUND_ADDRESS domain>`, case-insensitive, matched by
    `addresses.inbox_code_from_address`); unknown or retired codes, and inactive users, are logged
    and dropped without a row. Each recipient's work is a single transaction that also creates its
    `InboundEmail`, so "the row exists" means "done" and a retry skips recipients already handled;
    objects written for a transaction that then fails are deleted again (no leaked cap slots).
    The email id and each receipt id are `uuid5` of (message pk, recipient[, leaf index])
    (`inbound.email_id_for` / `receipt_id_for`), so every try writes the *same* S3 keys: objects
    left by a try that was killed hard (OOM, SIGKILL -- no cleanup ran) are overwritten by the
    next one rather than orphaned and billed-never. A try that loses the unique-constraint race to
    a concurrent duplicate leaves the objects alone when the winner's `InboundEmail` row exists.
  - **Header shapes -- confirmed on real staging mail by the infra spike** (issue #98, comment
    "Spike results: SES inbound headers on staging",
    https://github.com/ziptrigo/ziptrigo-apps/issues/98#issuecomment-6124695849; all in
    `services.mime`). SES prepends its headers, so the *topmost* instance wins and nothing a
    sender wrote lower down can fake a verdict. Envelope recipient: the `for <addr>` of the
    *first* `mime.SES_RECEIVED_HEADER_COUNT` (1) `Received` header (`from <host> (...) by
    inbound-smtp.us-east-1.amazonaws.com with SMTP id <id> for <recipient>; <date>`), only if its
    `by` host is `inbound-smtp.*.amazonaws.com` (a sender-written copy sits directly beneath SES's,
    so a "contiguous run" would have trusted it); else `Delivered-To`, `X-Original-To`, `To`/`Cc`
    (first source with an inbox address wins -- a Gmail auto-forward has the user's own address in
    `To`). **Not yet verified on real mail: a BCC and a Gmail auto-forward**, the two cases where
    `To` is not our address and the `Received ... for` header is all we have. Spam/virus:
    `X-SES-Spam-Verdict`/`X-SES-Virus-Verdict` (topmost; only `FAIL` rejects; missing = "not
    flagged").
  - **Sender authentication reads one header.** SES writes **no** `X-SES-Spf-Verdict` /
    `X-SES-Dkim-Verdict` (the spike found none), so those and `Received-SPF` are *ignored
    entirely*: a sender could put `X-SES-Spf-Verdict: PASS` in their own message, and trusting
    it was an authentication bypass. SPF, DKIM and DMARC come **only** from the *first*
    `Authentication-Results` header, and only if its authserv-id is `amazonses.com` (a later copy,
    or Google's `ARC-Authentication-Results`, is not it).
    Its real shape: `amazonses.com; spf=pass (spfCheck: ...) client-ip=...; envelope-from=<addr>;
    helo=...; dkim=pass header.i=@gmail.com; dmarc=pass header.from=gmail.com;` -- the SPF
    clause's `envelope-from` is a *separate* `;`-segment (properties belong to the clause before
    them), and DKIM may carry `header.i` only, no `header.d` (we read `header.d`, else the domain
    of `header.i`). The parsed result is `mime.Verdicts` (`spf_pass`, `spf_envelope_from`,
    `dkim_pass`, `dkim_domains`, `dmarc_result`, `dmarc_from_domain`); anything missing means "not
    authenticated" (fail closed). Envelope sender: `Return-Path`.
  - **That header echoes sender-controlled values, so it is parsed defensively** (the
    "header injection" hole found in review of #108): MAIL FROM (`envelope-from`) and EHLO (`helo`)
    land in it, and a `;` in either used to start a `dkim=pass` / `dmarc=pass` clause of the
    sender's choosing (a quoted MAIL FROM local part and an RFC 5321 address-literal EHLO can hold
    `;`, `=`, `,` and `@`). `mime._auth_clauses` therefore (1) splits on `;` only outside
    quoted-strings and comments, with quote- and quoted-pair-aware comment stripping (RFC 8601);
    (2) accepts exactly SES's structure: at most one `spf` clause, first, then its `client-ip` /
    `envelope-from` / `helo` segments (in that order), then any `dkim` clauses, then at most one
    `dmarc`; a repeated or out-of-order method, an unknown method/segment/property, or properties
    not separated by whitespace reject it; (3) validates every value against a strict grammar
    (`envelope-from` = dot-atom `@` LDH domain, no quotes/spaces/`;`/`,`; `helo` = hostname --
    underscores tolerated -- or a plain `[IPv4]` / `[IPv6:...]` literal; `header.i` = optional
    dot-atom `@` domain; `header.d` / `header.from` = domain); (4) cross-checks the echoed values
    against headers SES wrote elsewhere: `envelope-from` must equal the `Return-Path` address
    (case-insensitive, exact -- the `+tag` normalization applies only later, in `vouches_for`) and
    `helo` the `from <host>` of SES's topmost `Received`, so a value cut short by a `;` no longer
    matches; (5) reads the raw header values, since the decoded ones have RFC 2047 encoded words
    expanded after the fact. If **any** check fails the whole header is dropped -- no SPF, DKIM or
    DMARC credit, `Verdicts` as if SES had said nothing -- and `logger.warning` records the reason
    plus the first 80 characters of the header (never the whole value), so a real change in SES's
    format shows up in the logs. An unparseable but legitimate header therefore costs the sender
    their authentication (it fails closed), never the other way round.
  - **Order of decisions per recipient:** spam `FAIL` -> `rejected_spam`; virus `FAIL` ->
    `rejected_virus`; a forwarding confirmation (below); the allowlist -> `rejected_sender`;
    then attachments / body / `no_receipt` / `rejected_too_large`; then the daily cap per file.
    Rejected rows keep only metadata (from, envelope sender, subject, `received_at`, status,
    `note`); no S3 objects, no `stored_bytes`, no cap slot, no charge.
  - **Allowlist** (`ReceiptsAccount.sender_allowlist`, exact addresses and `*@domain`): empty
    accepts anyone, with no authentication needed. Otherwise the `From` address **or** the envelope
    sender must match after `addresses.normalize_address` (lowercase, `+tag` stripped, so Gmail's
    `orders+caf_=user=gmail.com@vendor.com` becomes `orders@vendor.com`), **and** SES must have
    authenticated *the address that matched* (`mime.Verdicts.vouches_for`):
    - a matching **envelope sender** needs an SPF pass for *that address's domain* with the
      `envelope-from` SES checked equal to the `Return-Path` (after `normalize_address`). An SPF
      pass for some other address -- the attacker's own domain -- does not count. Note SPF proves
      the sending IP may send for the domain, not who wrote the local part, so an exact-address
      entry (`alice@bigprovider.com`) on a shared provider or ESP is **domain-level proof**: anyone
      who can pass SPF for that domain matches it;
    - a matching **`From`** needs a DMARC pass whose `header.from` is exactly the `From` address's
      domain (case-insensitive; not a subdomain or parent). A DMARC `fail`, `temperror` or
      `permerror` is final. Only when SES reports no DMARC clause, or `none` (a domain that
      publishes no policy), does it fall back to a DKIM pass whose signing domain is **strictly
      aligned** with the `From` domain (`mime.strictly_aligned`): equal, or one is a subdomain of
      the other (`mail.vendor.com` signs for `vendor.com`, and the reverse). There is no
      organizational-domain list on this path, so siblings under a shared suffix
      (`attacker.github.io` vs `victim.github.io`) never align; the cost is an ESP that signs with
      an unrelated domain no longer authenticates a no-DMARC `From`. With no signing domain, a
      `From` match is **not** authenticated (fail closed).

    So passing SPF/DKIM for a domain of your own never lets you pose as an allowlisted address, and
    a header you write yourself (`X-SES-*-Verdict`, `Received-SPF`, a second `Authentication-
    Results`) authenticates nothing. A Gmail auto-forward (SPF broken, vendor DKIM intact, so DMARC
    still passes for the vendor's domain) is accepted through `From`.
  - **Forwarding confirmations.** A message from `mime.FORWARDING_CONFIRMATION_SENDERS`
    (`forwarding-noreply@google.com`; Outlook.com/Microsoft 365 forwarding sends no such
    confirmation that we found, so there is no entry for it) that SES authenticated *as that
    address* (the same `vouches_for` check: DMARC for `google.com` / a strictly aligned DKIM signature, or
    SPF on a matching envelope sender -- a pass for some other domain does not count) skips the
    allowlist and is stored as `forwarding_confirmation`: free, no receipts, no `.eml`, no cap slot,
    with the code (`Confirmation code: 123456789`, else `(#123456789)` in the subject) and the link
    in `forwarding_confirmation_code`/`_url` for the settings page to show later. The link is kept
    only if it is `https` to `mail-settings.google.com` (a candidate that doesn't parse, like
    `https://[x`, is skipped); without that authentication the email is an ordinary one (the
    allowlist applies and nothing is extracted).
  - **What becomes a receipt** (`mime.analyze`). The tree is walked through `multipart/*` *and*
    `message/rfc822`, so a forward-as-attachment is searched too. Every non-text (or
    attachment-disposition) part is an attachment candidate: eligible when its **magic-byte** type
    (`documents.sniff_content_type`, never the declared type or the name) is in
    `ReceiptsSettings.allowed_types` and it is within `max_file_bytes`; skipped (with a reason on
    the part in the stripped copy) when empty, unsupported, too large, or an image referenced from
    an HTML body by `cid:` with **both sides** under `min_inline_image_px` (size read from the
    header with Pillow; an unreadable image is not assumed to be a logo). With no eligible
    attachment the body is the receipt: HTML preferred, else text, needing at least 20 visible
    characters, re-encoded as UTF-8, stored with content type `text/html`/`text/plain` and an empty
    `original_filename` (the notification email then calls it "the email body"); for a message
    carrying a forwarded message, the body with the most visible text wins over a cover note. An
    over-size attachment with nothing else eligible is `rejected_too_large` (the body is *not*
    substituted); nothing usable is `no_receipt`.
  - **Cap and storing** (`inbound._store`). How many files fit is planned from an unlocked read of
    the cap (`accounts.intake_slots_remaining`); none -> `rejected_cap`; fewer than there are ->
    the rest are skipped and `note` says "N of M files skipped: daily limit ...". The objects are
    uploaded **first**, outside any transaction (files to `receipts/users/<user>/files/<receipt>`,
    the stripped `.eml` to `.../emails/<email>.eml`), then **one short transaction** takes the
    slots (`accounts.take_intake_slot`, the uploads' counter, under the account row lock -- held
    only for this, not for the uploads), creates the `InboundEmail` and the `Receipt` rows
    (`source=email`, `inbound_email`, sniffed type, `sha256`), adds exactly files + `.eml` to
    `stored_bytes` and queues `process_receipt` per receipt on commit; if the transaction fails the
    objects are deleted. If another upload took slots in between (the plan no longer fits), it is
    re-planned, up to 3 rounds, then the ordinary retry. **Stripped `.eml`** = the original
    re-serialized (from a deep copy of the tree parsed once, not re-parsed per recipient) with
    every attachment-like part's payload emptied and one header added: `X-Receipts-Receipt-Id:
    <id>` or `X-Receipts-Skipped: <reason>`; a body that became a receipt is stripped the same way.
    **It is bounded**, because the user pays for it and the sender chooses what is in it: any
    other text part over `mime.MAX_KEPT_TEXT_BYTES` (256 KB) is emptied (`X-Receipts-Skipped: too
    large`), leaves past `MAX_LEAVES` (300) and anything below `MAX_DEPTH` (30) are emptied, and if
    the result is still over `MAX_EML_BYTES` (1 MB) -- or can't be serialized -- only the top-level
    headers are kept (`X-Receipts-Stripped: headers only`). Likewise `mime` never keeps the bytes
    of a part it skipped, and does not even choose a body when an attachment is eligible. One depth
    measure (`mime._walk`: each `multipart/*` and `message/rfc822` is a level) serves every walker,
    and `_choose_body` skips a part the main walk didn't reach (≈20+ nested forwards used to raise
    `KeyError` there). **Rows ceiling:** an email that stores nothing (rejected, ignored, a
    forwarding confirmation) gets its `InboundEmail` row only while the user has fewer than
    `ReceiptsSettings.max_inbound_rows_per_day` (500) rows from the last 24 hours; beyond that it
    is only logged, so a flood of junk can't fill the list or the database. Accepted mail is never
    dropped by it (the file cap limits that). A frozen account still gets everything stored
    (`process_receipt` parks the receipts in `waiting_for_credits`). An email with receipts that fail or need
    review triggers the usual single "needs your attention" email once the last one settles.
  - **Local runs.** `./manage.py receipts_ingest_eml file.eml --to receipts+<code>@ziptrigo.com`
    uploads the file to `receipts/inbound/local-<uuid>` and runs the real `ingest` inline (claim,
    download, parse, allowlist, cap, store, delete); `--to` (repeatable) replaces the headers'
    recipient, omit it to use them. The verdict headers are read from the file, so add them to
    exercise the allowlist or a rejection.
- **Web UI** (`apps/receipts/{urls,views,forms,templatetags}`, `templates/receipts/`, `static/receipts/js/`;
  mounted at `/receipts/`; every view `@login_required`, another user's (or an unfinished upload's)
  receipt is a 404; all pages follow `docs/ui-design/README.md` and extend `receipts/layout.html`,
  which adds the **"Storage is paused" banner** (with a Top up link to `billing:credits-history`)
  while the account is frozen -- every page renders through `views.common.render_page`, which also
  creates the `ReceiptsAccount` on first visit). Views call `services.actions`/`filters`/`uploads`/
  `files`, never re-implementing a rule; `ValidationError` is a 422, `AccountFrozenError` and
  `InsufficientCreditsError` are 422 too on htmx views (htmx does not swap 402) with a Top up link.
  - **Pages**: `/receipts/` (dashboard), `/receipts/list/` (its partial), `/receipts/export.csv`,
    `/receipts/upload/` (+ JSON `upload/start/`, `upload/<id>/complete/`), `/receipts/<id>/` (page
    on GET, the form on POST), `<id>/body/`, `<id>/download/`, `<id>/reprocess/`, `<id>/trash/`
    (move to the Trash), `<id>/restore/`, `<id>/delete/` (forever), `<id>/dismiss-duplicate/`,
    `/receipts/trash/`, `/receipts/settings/` (+ `settings/inbox/`, `settings/regenerate/`,
    `settings/categories/...`), `/receipts/vendors/` (+ `vendors/list/`, `suggest/`, `vet/`,
    `merge/`, `<id>/rename/`, `<id>/delete/`). URL names are in `urls.py` (`receipts:detail`, ...).
  - **Dashboard**: status tabs with counts (All, Needs review, Processing = received + processing +
    waiting for credits, Failed, Duplicates), search (vendor, notes, amount; `autocomplete="off"` so
    the browser's past-search suggestions don't cover the list), category, a receipt-date dropdown
    (`date_range`: Any date, Last 15/30 days -- ending today --, This month/quarter/year -- the whole
    calendar period --, or Custom, which alone shows and enables the `date_from`/`date_to` inputs;
    `forms.filters.resolve_date_range` turns a preset into dates against `timezone.localdate()` on
    every request, so a "This month" link stays relative, and ignores the date inputs; dates without
    a `date_range` -- older links, and the canonical URL of a custom range -- mean Custom), sortable
    Receipt date (default newest first, undated on top) and Added columns, 25 per page, a
    "Vendors" link (with the number to vet) next to Trash and the vendor filter chip (see "Vendors").
    `forms.parse_filters` reads the query string *leniently* (a bad value is dropped) into
    `ReceiptFilters` for the services; the page, `receipt_list` and the CSV export all use it, so
    "current filters" means one thing. The filter form `hx-get`s `/receipts/list/` into
    `#receipt-list` (which sits *inside* the form, so its hidden `status`/`sort` inputs travel with
    the next request) and the response's `HX-Push-Url` puts the **dashboard** URL in the address bar
    (a reload or shared link renders the full page; the list URL opened directly redirects there).
    The tabs and the Export CSV link are re-sent out of band (`hx-swap-oob`) with every refresh, so
    they carry the filters. While a row is `received`/`processing` the list re-fetches itself every
    3 s (`polls=` in the URL counts, capped at 40; `waiting_for_credits` is not polled, nor is a
    `received` receipt with a retry scheduled -- `Receipt.retry_scheduled`, shown as "trying again in
    ..." on the row and the detail page).
  - **Detail**: preview left, form right (stacked on phones); the vendor field is described under
    "Vendors". Required fields (vendor, receipt date,
    amount) are marked `*`, and while one is missing on a settled receipt it gets an amber border and
    a hint. A save is an htmx form: 422 puts `Label: message` lines in `#receipt-form-msg` (the form
    keeps what was typed), success flashes "Saved." and redirects back (so the heading, status pill
    and duplicate banner are all fresh). `forms.ReceiptForm` only gathers the fields and maps
    `actions.clean_changes` errors to fields (dates are ISO only). A failed receipt shows the reason
    and next step (`templatetags.receipts_ui.FAILURE_HELP`) instead of the form, with Re-run
    (a confirmation modal naming the credit cost, `RECEIPTS_REPROCESS_USER`), Move to trash and Download still available; a receipt
    being read shows a progress panel that polls `GET <id>/?polls=n` and swaps only the right-hand
    panel (`hx-select`) plus the heading, duplicate banner and added line (`hx-select-oob`), so the
    preview doesn't flicker. A trashed receipt opens read-only with a Restore button. Images and
    PDFs are previewed through a presigned *inline* S3 URL generated when the page renders (a HEIC
    through its stored JPEG); downloads go through `<id>/download/`, which redirects to a fresh
    presigned *attachment* URL (frozen: back to the page with a message).
  - **Email bodies** (`body_view`): the HTML an email carried is the sender's, so it is served by
    Django, never by S3, as an isolated document: `Content-Security-Policy: default-src 'none';
    img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors
    'self'; sandbox` (`views.detail.BODY_CSP`; the bare `sandbox` makes the document opaque-origin even
    if its URL is opened top-level, where the iframe's own attribute does not apply), `X-Content-Type-Options: nosniff`, `Referrer-Policy:
    no-referrer`, `Cache-Control: private, no-store`, shown only inside `<iframe sandbox>` (a bare
    attribute: no `allow-scripts`, forms, popups or same-origin). So no script runs and nothing remote
    loads (a tracking pixel is blocked). The detail page that frames it also sends `Content-Security-
    Policy: frame-src 'self'`, so a `<meta http-equiv="refresh">` in the email cannot navigate the
    frame to a remote URL. A `text/plain` body is HTML-escaped into a `<pre>` and served the same
    way. A frozen account gets a 402 notice (with the same headers), a missing object a 404 notice,
    a body over `MAX_BODY_BYTES` a 413.
  - **Upload** (`views/uploads.py`, `static/receipts/js/upload.js`): drop zone (files and folders,
    copied from file_transfer's `initDropZone`), "Choose files" and "Take photo" (`accept="image/*"
    capture="environment"`). Per file: refuse an unsupported type/size/empty file in the row; the
    browser computes SHA-256 (base64); `POST upload/start/` (session + CSRF, JSON in/out,
    `RECEIPTS_UPLOAD_USER`; 422 rejected, 402 frozen, 429 waits for `Retry-After` up to 3 times) ->
    XHR `PUT` to the presigned URL with exactly the headers the server returned (never
    `Content-Length`, which the browser sets) and a type-less Blob so no `Content-Type` is sent (it
    would otherwise need to be in the bucket's CORS `AllowedHeaders`) -> `POST upload/<id>/complete/`.
    Three files at a time, a progress bar and an error line per row. Both JSON endpoints are rate
    limited per user: `start` by `RECEIPTS_UPLOAD_USER`, `complete` by the looser
    `RECEIPTS_UPLOAD_COMPLETE_USER` (it is *not* bounded by `start`: a receipt whose object never
    arrived stays `uploaded=False`, so each `complete` on it is a free S3 HEAD); the script waits out
    a 429 on either. Once the PUT has succeeded the row's cancel button is hidden and a cancel can no
    longer stop the `complete` (which would verify, queue and bill the receipt).
  - **Trash** (`/receipts/trash/`): plain POST forms ending in a redirect with a flash message (restore;
    delete forever through a confirmation modal, an S3 error is a message, not a 500); how long is
    left comes from `ReceiptsSettings.trash_days`.
  - **Settings**: the address (Copy; Regenerate in a modal, `RECEIPTS_REGENERATE_USER`, then redirect), the
    latest forwarding confirmation of the last 7 days (code, and an Open link only if it is https
    to `mail-settings.google.com`, re-checked before it becomes an `href`), accepted senders
    (Anyone / Only these, `forms.InboxSettingsForm` over `addresses.parse_allowlist`) with the default
    currency in one htmx form, the category editor (add, rename inline, move up/down, delete in a modal;
    each action re-renders `#categories`; deleting names how many receipts become uncategorized) and
    the 20 most recent inbound emails with their outcome and `note`.
  - **UI parts**: `{% status_pill receipt.status %}` (`receipts_ui`; colours in the design doc),
    `partials/confirm_modal.html` driven by the `confirmModal` Alpine component and the `copyText`
    component, both in `static/receipts/js/common.js` -- loaded as a *classic* script (not `defer`) so
    its `alpine:init` listener exists before Alpine starts. Multi-line template comments use
    `{% comment %}`: `{# #}` is single-line only.
  - **Tests**: `tests/test_views_*.py`, `test_ui.py` (every page, 404s, 422 paths, rate limits via the
    `settings` fixture, frozen account, CSV, the body headers, the `sandbox` attribute, the upload flow
    against the fake storage and AI). One Playwright test file (`tests_e2e/test_receipts.py`: upload ->
    processed -> edit -> trash -> restore, the filters and URL, the sandboxed body) runs against
    `tests_e2e/receipts_e2e_settings.py`: the real site with an `INSTALLED_APPS` entry that swaps
    the receipts storage and AI for the unit tests' fakes, an extra URL (`/__e2e__/s3/<key>`) acting
    as the bucket the fake presigned URLs point at (it checks `x-amz-checksum-sha256` like S3),
    inline tasks, and its own `db.e2e.sqlite3` -- no test switch in production code. Run it with
    `inv test e2e` (needs `.env.local`).
- **API and CLI** (phase 4). The endpoints, status codes (400/402/404/429) and shared rate-limit
  budgets are in "API" above. Two service additions came with it: `services.forwarding`
  (`forwarding_confirmation(user)`, the Gmail confirmation code/link of the last 7 days, moved out of
  the settings view so the API's `/inbox` and the page share it) and `services.list_categories`
  (a user's categories with `receipt_count`, **ordered explicitly**: `Meta.ordering` is dropped from
  a query that aggregates, so the settings page's earlier `annotate(Count(...))` left the order to
  the database). `admin/receipts.py` mirrors `admin/filetransfer.py`: `login` (password as an
  optional argument, a hidden prompt, or `RECEIPTS_PASSWORD`), `list` (`--status --category --vendor
  --q --from --to --sort --duplicates --trashed --limit --offset`, the full receipt id in the table
  because `get` needs all of it), `upload <files or folders>` (SHA-256 base64 of the file, `POST
  /uploads`, a `PUT` of the bytes to the presigned URL with exactly the headers the API returned,
  `POST .../complete`; an extension outside PDF/JPG/PNG/HEIC/WebP, or an empty file, is skipped with
  a warning, one file failing doesn't stop the rest, and the exit code is 1 if any failed), `get`
  (the vendor with `(vetted)` / `(not vetted)`, its id, and "Read as" -- the name the AI read --
  when that differs from the vendor's),
  `export --out file.csv` (also `--vendor`; streamed to `.<name>.part` next to `--out` and renamed
  into place only when complete, so a dropped connection never leaves a cut-off CSV), `inbox` and
  `regenerate` (confirms unless `--yes`), and the vendor commands (issue #123): `vendors` (`--to-vet
  --q --limit --offset`; id, name, vetted, receipts incl. "+N in trash", last receipt date),
  `vendor-rename <id> <name> [--not-vetted]` (a 409 clash names the other vendor, quoted, by id and
  name, prints the `vendor-merge <env> <other> <id>` command that would combine them and then the
  `vendor-rename <env> <other> <name>` that gives the merged vendor the new spelling -- the merge
  keeps the other vendor's -- exit 1),
  `vendor-vet <id>... [--unvet]` (one `PATCH` per distinct id; a failure doesn't stop the rest, the
  exit code is 1 if any failed) and `vendor-merge <keep> <id>...` (unless `--yes`, it first `GET`s
  every vendor so the prompt shows which is kept and which are merged away -- and a wrong id fails
  before the question --, then `POST /vendors/merge`; the kept id and repeats are dropped from the
  list). Its token is **per environment**, `~/.receipts_token.<env>` (so the prod JWT is
  never sent to the local server; the single `~/.receipts_token` of earlier versions is ignored --
  log in again, then delete it), written by `admin.utils.write_private_file` (mode 0600 set on the
  open descriptor so a pre-existing looser file is tightened, `O_NOFOLLOW`; `filetransfer` and
  `qrcode` use it too). Every request goes through `_request`: default timeout; a 429 is retried
  after its `Retry-After` only when that is <= 60 s (`MAX_RETRY_AFTER_SECONDS`) and the call's total
  wait stays within `MAX_TOTAL_RETRY_WAIT_SECONDS` -- the hourly rules (export, regenerate, re-run,
  login) are not slept on, the error says "rate limited, try again in N minutes" instead; and any
  `requests` exception exits 1 with a message (or raises `NetworkError` for the upload loop, which
  carries on with the next file). Errors print the API's `detail`, a ninja 422 list flattened to
  `field: message`. The site URL is read
  when a command runs, after `set_environment` has loaded the env file: `API_BASE_URL`, else the
  file's `BASE_URL`, else `http://localhost:8000` (the older CLIs read `API_BASE_URL` at import
  time, before that file is loaded). Unit tests: `admin/tests/test_receipts.py`, HTTP mocked, no
  Django.
- **Admin.** `ReceiptsSettingsAdmin` (singleton, superusers), read-only `ReceiptAdmin`
  (status/reason/source filters; `VendorAdmin`, see "Vendors"; no add/change/delete -- deleting a row would orphan its S3 objects
  and skew `stored_bytes`), `InboundMessageAdmin` (status, attempts, next attempt, last error; a "retry" action
  needing `change_inboundmessage`, which moves the parked object back to `receipts/inbound/`,
  resets `failed` -> `pending` and queues `ingest_message` again through
  `services.inbound.reset_message_for_retry`), `InboundEmailAdmin`, `ReceiptsAccountAdmin` (codes inline, frozen state, stored bytes).
- **Settings/env**: `OPENAI_API_KEY`, `OPENAI_BASE_URL` (optional), `OPENAI_RECEIPTS_MODEL`,
  `RECEIPTS_AI_TIMEOUT_SECONDS`/`_MAX_RETRIES` (explicit timeout, bounded SDK retries),
  `RECEIPTS_S3_BUCKET`/`_REGION`/`_ENDPOINT_URL`, `RECEIPTS_AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`,
  `RECEIPTS_INBOUND_ADDRESS`; the five required ones are checked in prod (see "Settings and
  environment"), `apps.receipts.checks` warns about a *partial* local configuration.

### Queue and scheduler

Two background-work mechanisms, both used by `file_transfer` (spec issue #55) and available to any
future app:

- **Queue**: Django 6's built-in `django.tasks`, backed by `django_tasks_db` (an ORM-based backend
  -- Django core only ships Immediate/Dummy backends). `TASKS` in `config/settings.py` selects
  `django_tasks_db.DatabaseBackend` normally and `ImmediateBackend` under pytest, so tests never
  need a worker. A function decorated `@task` (see `apps/file_transfer/services/emails.py`) is
  queued with `.enqueue(...)`, never called directly, and its arguments must be plain
  strings/ids/etc (never model instances) since the backend serializes them. There are two queues,
  `default` and `receipts` (`TASKS[...]['QUEUES']`): Receipts' AI/PDF/image work is slow and must
  not delay file transfer's emails and zip builds, so it has its own queue and its own worker
  process. In the deployed image `supervise.py` runs `./manage.py db_worker` (the `default` queue)
  *and* `./manage.py db_worker --queue-name receipts` as extra processes next to gunicorn (they
  install signal handlers, so they can't be threads); locally the `worker` and `worker-receipts`
  compose services run them. A task whose queue has no worker is never executed, so a new queue
  needs a worker added in both places.
- **Scheduler**: `apps.core.scheduler` -- a `JobSpec` registry (name, callable, interval, lease)
  filled by each app's `AppConfig.ready()` (`core` can't import a product, so it never discovers
  jobs itself), a `ScheduledJob` row per job whose claim is one conditional `UPDATE` guarded by the
  database's own clock throughout (`try_claim` in `apps/core/scheduler/runner.py` -- both the lease
  and the due-by-interval check use the database's `Now()`, not the calling process's clock, so
  two runners whose clocks disagree can't disagree about whether a job is due), and a
  `SchedulerRunner` that ticks every `SCHEDULER_TICK_SECONDS` running whatever's due. At-least-once,
  so every job must be idempotent. In the deployed image it runs as a daemon thread inside each
  gunicorn worker (`gunicorn.conf.py`'s `post_worker_init` calls
  `apps.core.scheduler.start_scheduler_thread()`, a no-op unless `SCHEDULER_ENABLED`), which is
  safe with N workers because the claim is atomic, so only one thread wins each job per interval;
  never start it from `AppConfig.ready()`, which also runs for `migrate`/`collectstatic`/shell.
  `gunicorn.conf.py` imports the scheduler inside the hook (gunicorn loads that file in the master
  before Django is configured), and `settings.LOGGING` routes everything under the `apps` logger
  (INFO, console, `propagate: False`) to the container logs, so "Scheduler thread started", the
  file_transfer job logs and the ratelimit "over limit" lines all show up (`conftest.py` re-enables
  propagation under pytest so `caplog` still works).
  Jobs therefore run inside web workers, and **an interrupted job keeps its lease until the lease
  expires**: a deploy, a SIGHUP reload or a worker timeout kill that lands mid-job leaves that
  job's `locked_until` set, so no runner picks it up before then (up to the job's lease, e.g. 1 h
  for the daily jobs), and a deploy can delay a daily job by up to that long. Correct, because
  every job is idempotent and runs at-least-once; leases are deliberately not cleared on exit.
  A job that *finishes* (successfully or not) does release its lease (`runner._run_job`), so it is
  due again after its `interval` even when its `lease` is longer (`receipts.retry_due`: 5 min
  interval, 10 min lease) -- before that fix every job ran every max(interval, lease). The release
  only applies while no later claim exists (`last_started_at <= ` this claim's), so a run that
  outlived its lease can't free the lease of the runner that took over.
  `gunicorn.conf.py`'s `worker_exit` stops the runner and joins `runner.thread` for up to 5 s, which
  only covers a thread idle between jobs. `SchedulerRunner.run_forever` calls
  `close_old_connections()` every tick; with `DATABASE_URL` (prod/staging: `conn_max_age=600`,
  `conn_health_checks`) each scheduler thread keeps one health-checked persistent connection, which
  is what recovers it from a database restart, and it means **each gunicorn worker holds one extra
  Postgres connection** when the scheduler is enabled (3 by default).
  `./manage.py run_scheduler` remains for local development (the `scheduler` compose service) or a
  dedicated container; job status is a read-only `ScheduledJob` admin.

`file_transfer`'s four jobs (`apps/file_transfer/jobs.py`): `meter_transfers` (daily -- charges at
the shared `BillingSettings` storage price, suspends, re-enables as a fallback, deletes files
past the suspension grace period),
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
shared by every service in `docker-compose.yml`: `web`, `worker`, `worker-receipts`, `scheduler` and `db` (that
split is the local-development layout only; a deployment runs one container, below). The build
runs `collectstatic` so WhiteNoise can serve static files with `DEBUG=False`. `.env.local` is mounted
into `web`, `worker`, `worker-receipts` and `scheduler` because settings require an env file.

- The image's `CMD` is `python supervise.py` (stdlib only, PID 1 after the entrypoint's `migrate`).
  It starts gunicorn (`config.wsgi`, `-c gunicorn.conf.py`, which starts the scheduler thread in
  each worker when `SCHEDULER_ENABLED`) and, unless `RUN_TASK_WORKER` is `0`/`false` (default on),
  two queue workers, `manage.py db_worker` (the `default` queue) and `manage.py db_worker
  --queue-name receipts` (issue #98). It forwards SIGTERM/SIGINT to all its children, gives them 30
  s to exit before SIGKILL, and exits as soon as any child exits (non-zero if one died on its own) so
  `restart: unless-stopped` recovers the whole container. This is how a deployment runs web,
  scheduler and queue worker in one container; the queue worker can't start before migrations
  because the entrypoint finishes them first. Exit status: a child killed by a signal maps to
  `128 + signum`, and a child that had to be SIGKILLed after the grace period makes it 137. With
  `ENVIRONMENT=prod` and `SCHEDULER_ENABLED` unset it logs a prominent warning at start.
- **Coupling, deliberately**: because the supervisor exits when *any* child exits, a `db_worker`
  crash (an OOM kill, an `OperationalError` during a database restart) restarts the whole container,
  i.e. web downtime and dropped in-flight requests, and a crash-looping queue worker is a
  crash-looping site. This was chosen over restarting `db_worker` in place because it is simple and
  leaves Docker's restart policy as the single recovery mechanism; revisit it if the queue worker
  proves flaky.
- **Shutdown timing**: three values must keep this order, each strictly below the next:
  gunicorn's `graceful_timeout` (25 s, `gunicorn.conf.py`) < `supervise.py`'s grace (30 s, then
  SIGKILL) < the container's stop grace period (40 s, `stop_grace_period` in the infra compose,
  joaonc/infra#370). Docker's default is 10 s, which would cut all of it short.
- Locally, `web` is overridden with `runserver` (so neither the scheduler thread nor the
  supervisor runs) and has a TCP healthcheck on port 8000 that the other two services key their
  startup off of.
- Local compose only: `worker` runs `./manage.py db_worker`, `worker-receipts` runs `./manage.py
  db_worker --queue-name receipts` and `scheduler` runs `./manage.py run_scheduler`, each its own
  service/container. (A shell script backgrounding both
  with `&` was tried and never worked: `/bin/sh` in `python:3.14-slim` is dash, which has no
  `wait -n`, and `sh` as PID 1 doesn't forward `SIGTERM`; hence the Python supervisor above.)
- `db` is this stack's own Postgres (`postgres:18-alpine`), separate from the shared
  `docker-compose.postgres.yml` used across repos for local dev tooling (see that file's header) --
  `db` is part of the deployable stack the other services depend on.

Only `web` runs `docker-entrypoint.sh`'s `migrate` step (`RUN_MIGRATIONS=1`; `worker`,
`worker-receipts` and `scheduler` explicitly set `RUN_MIGRATIONS=0`) -- the others `depends_on: web:
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
proxy"). A coarse per-IP `limit_req` in nginx itself is configured in the `infra` repo
(`apps/ziptrigo-apps/nginx.conf`, issue #119; main zone plus a looser `/go/<code>` zone, see
"Rate limiting" above).

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
  paragraph -- flipping it on is now a rollout decision, not a known gap. Abuse reports, admin
  takedown, a sender block list and an optional per-transfer auto-hold (originally phase 3, spec
  section 16, deferred to issue #59) are built -- see "File transfer specifics" above. No
  staff-notification email on an auto-hold: skipped rather than invented, since this project has no
  `ADMINS`/admin-email setting to send one to.
- Receipts (#98) is fully built (phases 1 to 4: backend, email intake, the web pages, the JWT API
  and CLI; see "Receipts" and "API" above). The email-header shapes in `services.mime` were
  confirmed on staging by the infra spike (see "Email intake"), except a BCC and a Gmail
  auto-forward, which have not been seen on real mail yet. Its prerequisites live elsewhere: the infra
  bucket policy, IAM user, SES rules and `receipts_env` (the `infra` repo), and the owner putting
  `receipts_env` plus `OPENAI_*` into `.env.prod`/`.env.staging` before the first deploy that
  contains Receipts (prod refuses to start without them).
- Receipts sender authentication (#98 / #108): how real SES accepts and renders a *crafted* MAIL FROM
  or EHLO (a quoted local part holding `;`, an address-literal EHLO) is **untested on real SES**. The
  parser in `services.mime` is built to be safe whatever SES does (strict structure, value grammars
  and the `Return-Path` / `Received` cross-checks; see "Email intake"), but a `swaks` test against
  staging (`--from '"x;dkim=pass header.i=@victim.com;dmarc=pass header.from=victim.com"@attacker.test'`,
  `--ehlo 'x;dkim=pass,header.i=@victim.com'`, then look at the stored `Authentication-Results`,
  `Return-Path` and `Received`) is recommended to confirm what SES writes, and that a legitimate
  EHLO or MAIL FROM it renders differently from what the grammars expect is not being rejected.
- Rate limiting (#53) is in place site-wide (see "Rate limiting" above) using an in-house limiter
  on a dedicated DB-backed model (or Redis, when `CACHE_URL` is set); the coarse nginx `limit_req`
  that backs it up as defense-in-depth is configured in the separate `infra` repo (#119), not
  yet deployed when this was written.

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
