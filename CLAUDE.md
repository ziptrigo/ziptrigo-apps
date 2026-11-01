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
                      generic S3 storage (`apps.core.services.storage`), a QR image helper
                      (`apps.core.services.qr_image`), in-app notifications (the header bell, issue
                      #169), Markdown rendering / search text / checklist editing
                      (`apps.core.markdown`, issue #176), the OpenAI client factory and reply
                      unwrapping Receipts and To-do share (`apps.core.services.ai`, issue #183),
                      the inbound-email parser and sender-authentication code they share
                      (`apps.core.services.mail`, issue #186, see "Mail parsing"),
                      per-field form errors for the 422 partials
                      (`apps.core.field_errors`, issue #219, see "HTMX form views"),
                      and the Alpine components every product
                      shares (`core/js/components.js`: `copyText`, `confirmModal`,
                      `notificationBell`).
apps/accounts/        User model, auth pages + API, JWT auth classes, the memory of confirmed
                      addresses (`KnownEmail`, issue #160).
apps/billing/         CreditAccount (balance) + CreditTransaction (ledger), credit services, the
                      shared storage price (BillingSettings), promo codes (issue #70, see "Promo
                      codes": PromoCode + PromoRedemption, the redeem form and link, the admin
                      section), the welcome credits of a new account (issue #160, see "Welcome
                      credits").
apps/feedback/        Footer "Feedback" page: saves a user's message, emails a receipt and a notice
                      to superusers; admin triage by status.
apps/qr_code/         QR codes: dashboard/editor pages, API, `/go/<code>` short links.
apps/file_transfer/   File transfer: logged-in + anonymous sending, dashboard/download pages,
                      S3 uploads, metering, "download all" zip, jobs.
apps/receipts/        Receipts (issue #98): receipts and invoices in, extracted fields out. Models,
                      uploads, email intake, AI extraction, billing, jobs, admin, the web pages, the
                      JWT API (`/api/receipts/`) and the `admin/receipts.py` CLI (see "Receipts").
apps/short_url/       Short URLs (issue #162): links with editable destinations, the public `/s/<code>`
                      redirect (root-mounted), click analytics, password and preview pages, abuse
                      tooling, one-off billing of its extras, dashboard/stats pages, the JWT API
                      (`/api/s/`) and the `admin/shorturl.py` CLI (see "Short URLs").
apps/todo/            To-do (issue #161, foundation #178): tasks and subtasks with per-user numbers
                      from 1000, labels, due dates in the user's time zone, Today / Upcoming / All /
                      Completed / Trash pages, task billing (a monthly free allowance, then a price),
                      tasks added by email, the JWT API (`/api/todo/`) and the `admin/todo.py` CLI
                      (see "To-do").
admin/                Typer CLIs for lint/test/server/pip/openapi/aws/email/users/qrcode/filetransfer/
                      receipts/shorturl/todo. Via `inv`.
tests_e2e/            Playwright end-to-end tests (separate `pytest_e2e.ini`); `tests_e2e/ai/`: Markdown
                      test plans run by an AI agent driving a browser, one per area (see its README).
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
inv qrcode login local <email>                  # JWT CLIs over /api/qr/, /api/ft/, /api/receipts/, /api/s/, /api/todo/
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
inv shorturl login local <email>                # password: prompt, or SHORTURL_PASSWORD
inv shorturl create local https://example.com/long [--alias spring-sale --tag promo --max-clicks 100 --yes]
inv shorturl quote local https://example.com --alias spring-sale   # what a create would cost
inv shorturl list local [--status active --tag promo --q sale --sort=-click_count]
inv shorturl get local <link-id>                 # also what its next paid actions would cost
inv shorturl update local <link-id> [--destination ... --clear-expires --password --yes]
inv shorturl enable|disable|delete local <link-id>   # enable and delete ask first (--yes skips)
inv shorturl analytics local <link-id>           # open the detailed statistics (paid once per link)
inv shorturl stats local <link-id> [--range 7|30|90|all]
inv shorturl preview local <link-id>             # what /s/<code>+ shows, and whether it works now
inv shorturl qr local <link-id> --out link.png
inv todo login local <email>                    # password: TODO_PASSWORD, else a hidden prompt (never an argument)
inv todo add local "pay rent every month on the 1st"   # the date is read from the title (--no-parse: don't)
inv todo add local call the dentist --due 2026-11-05T15:00 --priority 2 --label health --parent 1200
inv todo list local [--view today|upcoming|all|completed|trash --q "rent*" --regex --include-completed]
inv todo list local --priority 1,2 --label home --from 2026-11-01 --to 2026-11-30 --sort priority
inv todo get local 1234                         # everything: description, comments, reminders, files, referenced by
inv todo edit local 1234 [--title ... --priority 1 --due 2026-11-05T09:00 --clear-due --label home --remove-label later]
inv todo done local 1234 1235 | undone local 1234 | trash local 1234 | restore local 1234
inv todo postpone local 1234 tomorrow|next-week|next-weekend|2026-12-24
inv todo overdue-to-today local
inv todo comment local 1234 asked the landlord  # comments local 1234 lists them
inv todo labels local | label-add local home --color "#336699" | label-edit local home --name house | label-delete local home [--yes]
inv todo remind local 1234 60 [--channel in_app|email|both] | unremind local 1234 <reminder-id>
inv todo upload local 1234 contract.pdf photo.jpg   # SHA-256, presigned PUT, complete; exit 1 if any failed
inv todo download local <attachment-id> [--out contract.pdf --force]   # streams to .part, then renames
inv todo filters local | filter-run local <id-or-name> [--limit 20]
inv todo settings local [--default-due-time 09:00 --default-reminder 15|none --default-channel both]
inv todo inbox local                            # the address tasks can be emailed to
inv todo regenerate local [--yes]               # retire the address and issue a new one
inv users create local me@example.com [<password>] [--superuser | --staff] [--confirm]
inv users confirm local me@example.com          # what following the emailed link does, without the email
```

Every command accepts `--dry` to print the shell command without running it (the JWT HTTP-client
CLIs -- `qrcode`, `filetransfer`, `receipts`, `shorturl`, `todo` -- run no shell command, so they have no `--dry`). The app names `inv`
accepts come from `admin/django_app.py`, which discovers them from `apps/*/apps.py`.

**`inv users`** (`admin/users.py`) is not an HTTP client: it runs the management commands
`create_user` and `confirm_email` (`apps/accounts/management/commands/`) in the environment you
name, so it acts on that environment's database. `create <env> <email> [<password>]` takes
`--superuser` (also staff) or `--staff` (mutually exclusive) and `--confirm`; the password may be
omitted (typed with the echo hidden) or come from `USERS_PASSWORD`, and reaches the management
command through its environment, never its arguments, so it isn't in `ps`; an argument stays in
shell history. It must pass the sign-up rules (`PasswordValidator`), and an email that differs
from an existing one only in case is refused (unlike `createsuperuser`). Creation is one
transaction with the confirmation. `confirm <env> <email>` goes through
`accounts.services.email_confirmation.confirm_user`, the function the emailed link uses too, so
the `email_confirmed` signal fires (billing grants a promo code's credits then), pending
confirmation links for the address are retired, and an already confirmed account is untouched.
`prod` loads `.env.prod`, whose `DATABASE_URL` host is only reachable inside the server's
network: use a tunnel with `DATABASE_URL` overridden, or run the management command in the
container. Tests: `apps/accounts/tests/test_user_commands.py`, `admin/tests/test_users.py`.

`admin/server.py` and `admin/openapi.py` each define one typer command, which typer collapses away
when the module is run directly (`python3 -m admin.server`) but which `inv` preserves
(`inv server run`).

### Running tests directly

Run pytest from the repo root; `DJANGO_SETTINGS_MODULE=config.settings` and
`testpaths = ['apps', 'admin']` are set in `pyproject.toml`, and default addopts include
`--reuse-db --no-migrations`. `admin` is there for `admin/tests/` (currently
`admin/filetransfer.py`'s, `admin/receipts.py`'s, `admin/shorturl.py`'s and the token-file and request handling of `qrcode`/`utils`) -- plain HTTP-client CLI logic with no Django/database
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
qr_code | file_transfer | receipts | short_url | todo   products; may not import each other
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
  suspended transfers (`apps/file_transfer/apps.py`). A suspended transfer is re-enabled only when
  the balance covers *its own* next day (`services.metering.daily_cost`: `ceil(price x size / GiB)`,
  at least 1; several of a user's: oldest suspension first, running sum) -- the receiver, the daily
  fallback and `reenable_transfer_action` all apply it, since a flat 1 credit would forgive the day
  it couldn't pay on every run (free storage). Re-enabling a *disabled* transfer needs a day's cost
  too, on purpose: it is the next charge the transfer incurs. The write is conditional on the status
  that was read (`reenable_transfer` returns whether it matched), so racing the grace-period delete
  or delete-now can't resurrect an ended transfer. A suspended transfer's billing clock restarts at
  now; a disabled one keeps its `last_billed_at`, clamped to no earlier than 24h ago, so
  disable + re-enable can't dodge the day in progress and a long disabled stretch is still not a
  backlog. A transfer held for abuse review is re-enabled without counting toward the running sum.
  The suspension email states the credits needed; a suspension also creates an in-app notification
  (see "Notifications").
- The same shape covers background jobs: `core` can't import a product to discover its jobs, so
  `apps.core.scheduler` is a registry (like `apps.core.products`) that a product fills from its own
  `AppConfig.ready()`. See "Queue and scheduler" below.
- `accounts` can't import `billing` either, so the register page's optional promo code field
  (issue #70) is a hook: `apps.accounts.signup_codes` holds one `SignupCodeHandler` (`validate(code,
  email)` raising a `ValidationError` the form shows, `attach(user, code)` after the row exists)
  that `BillingConfig.ready()` registers; without one a typed code is "Invalid code". And
  `accounts` emits `signals.email_confirmed` (on commit, `send_robust`, from `confirm_token` and
  from a password reset that confirms the address) for `billing` to grant the credits a signup
  code reserved. See "Promo codes". It also emits `signals.account_created` (every new `User` row,
  in the creating transaction, `send_robust`, from a `post_save` receiver in `AccountsConfig`) for
  `billing` to open the welcome credits of a genuinely new account, including one created already
  confirmed by "Sign in with Google/Microsoft", which gets no `email_confirmed`. See "Welcome
  credits".
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
To-do attachments (issue #184) are the same: `TODO_S3_BUCKET`, `TODO_AWS_ACCESS_KEY_ID` and
`TODO_AWS_SECRET_ACCESS_KEY` are required with `ENVIRONMENT=prod` (`config/secret_checks.py`; the
infra side is issue #185 and must land first), `TODO_S3_REGION` defaults to `AWS_REGION` and
`TODO_S3_ENDPOINT_URL` is optional (Floci locally). The same bucket again, keys under `todo/`.
`apps.todo.checks` warns (`todo.W002`) about a partial local configuration.

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
`delete_objects` / `delete_prefix` (both raise `RuntimeError` naming the keys when `DeleteObjects`
answers 200 with per-key `Errors`, after deleting the rest, so a caller retries instead of
recording the files as gone),
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
  (`apps/accounts/views/login.py`, CSRF-protected, honours a same-site `next`; without one every
  sign-in -- password, Google, Microsoft -- lands on the home page, see
  `redirects.redirect_target`); logout is
  POST-only. The browser never holds a JWT. `accounts.backends.EmailBackend` is plain
  `ModelBackend` (no overrides; the class stays because sessions and `google_oauth.LOGIN_BACKEND`
  name its path): it hashes once for an unknown email, so login timing doesn't reveal accounts,
  and refuses `is_active=False` users at login and on every session load. Such a login answers
  "Invalid credentials" (the `status` check, "User not active", comes after `authenticate`).
- **JWTs** for `/api/`, i.e. external clients like `admin/qrcode.py`, `admin/filetransfer.py`,
  `admin/receipts.py` and `admin/shorturl.py`
  (`apps.accounts.auth.JWTAuth` / `AsyncJWTAuth` / `AdminAuth`, which also reject non-`ACTIVE`
  users). Claim `sub`, signed with `JWT_SECRET`, token classes in `apps/accounts/tokens.py`.
  **Tokens are bound to the password**: every access and refresh token carries `pwd`
  (`tokens.PASSWORD_CLAIM`, a `SECRET_KEY`-keyed HMAC of the password hash, never the hash itself),
  and `JWTAuth`/`AsyncJWTAuth` (so `AdminAuth` too) and `POST /api/auth/refresh` reject a token
  whose `pwd` doesn't match the user's current password. Any password change -- the account page,
  a reset, the admin, `set_unusable_password` -- therefore revokes every token issued before it,
  like Django's session auth hash does for sessions. So does anything else that rewrites
  `User.password`, e.g. Django rehashing it on login after a hasher upgrade: it kills outstanding
  JWTs and password reset links alike. A token without the claim is rejected, so
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

**Email case** (audit #15): `User.email` is stored with `normalize_email` (domain lower-cased, local
part as typed) and its unique constraint is **case-sensitive**, so the database alone does not stop
`Alice@gmail.com` next to `alice@gmail.com`; only application checks do, and not everywhere:

- *Web forms.* The login, forgot-password, resend-confirmation and register forms share
  `forms.email.NormalizedEmailMixin.clean_email` (like the API's `EmailStr`), so a mixed-case
  *domain* finds the account on every surface. Login and the reset/resend lookups stay **exact on
  the local part, on purpose**: the local part is the stored spelling, legacy rows may differ only by
  case (so a case-folded lookup could hit the wrong one of two, or several), and a case-insensitive
  lookup on those paths would also make them behave differently from the API's.
- *Checks that ignore case entirely* (`email__iexact`): `create_account`,
  `handle_signup_for_existing_email`, `PUT /api/account` and the admin user forms
  (`forms/admin.py`: `UserAdminCreationForm` and `UserAdminChangeForm` refuse another account's
  address in any case and normalize the domain; an unchanged address isn't re-checked, so one of two
  legacy rows stays editable). A case-variant signup takes the existing-account path (see "Auth"),
  never a second row (Google linking matches `iexact` and refuses two rows as `AccountConflict`).
  Where legacy case-duplicates exist, that path picks the row spelled exactly like the signup
  address, else the oldest (`created_at`, `pk`), and locks down / mails the *stored* spelling.
- *Not covered:* `createsuperuser` and the Django shell still rely on the
  case-sensitive constraint alone (`inv users create` / `manage.py create_user` check `iexact`). **Open race:** two concurrent signups of a *new* address in
  different case both pass the `iexact` pre-check and both insert; only a database constraint
  closes it. `UniqueConstraint(Lower('email'))` is deliberately not added yet: prod data needs
  checking for existing duplicates first (`SELECT lower(email), count(*) FROM accounts_user GROUP BY
  1 HAVING count(*) > 1`), and they need resolving, since the migration would fail on them.
  **Index caveat:** Django compiles `email__iexact` on Postgres to `UPPER(email) = UPPER(%s)`, so an
  index only helps if it is on `Upper('email')` (or the lookups are switched to a `Lower`
  annotation to match a `Lower('email')` index); until then the `iexact` checks are a sequential
  scan over `accounts_user`. Check with `EXPLAIN` when the table is large enough to matter.

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
still being the one on the link. **A link works once** (audit #7): it also carries the `pwd`
fingerprint of the password the account had when it was sent (`tokens.PASSWORD_CLAIM`, as login
tokens do; checked by `validate_token`, so the reset page's GET and POST and the API all refuse
it), so any password change -- this link, another link, the account page -- kills every link
issued before it; a link without `pwd` (emailed before this shipped, which also covers the
"without the address claim" case above in practice) is refused, like login tokens were. The check
is repeated inside `reset_password`'s transaction against the user row it locks
(`select_for_update`), because `validate_token` only reads: two requests with the same link can
both pass it, and the loser gets `InvalidResetTokenError` (the API answers 400, the page redirects
to the expired-link page) with nothing written. For an unconfirmed account the first lockdown
(`existing_account._lock_down_unconfirmed`) retires every reset link issued under the creator's
password; later signup attempts don't change the (already unusable) hash, so all the finish-setup
links emailed since stay valid until one of them is used (or expires). Claim-on-login (file transfer) needs
`email_confirmed`, so it becomes possible at the next login after such a reset, the same as after a
confirmation link.
Residual, not new: the confirmation email sent to the victim at the attacker's *first* signup (and
resend-confirmation) still confirms an account with its creator's password if the victim *clicks*
it; that is inherent to confirming by link and password signup, and is not closed here. A mail
scanner or link previewer no longer does it for them (audit #11): `GET /account/confirm-email/<token>/`
(`views.pages.confirm_email_page`) only renders a CSRF-protected "Confirm my email" button, without
reading the token, and only the **POST** from that button calls `confirm_token` (then redirects to
the success page, or -- post/redirect/get -- to `accounts:email-confirmation-expired`, which shows
the expired message and the resend form). The POST is rate limited per IP (`CONFIRM_EMAIL_IP`, 429
page); the GET is not. The URL is unchanged, so links already sent work; file transfer's anonymous
confirm link works the same way. `POST /api/auth/confirm-email` takes the token in a JSON body, which
no scanner sends. The button stops prefetchers and link previewers, which only fetch; it does not
stop every automated sandbox, since some mail-security products render the page and click buttons.
The email's copy ("open the link below and press Confirm my email") is neutral between signup and
an email change, as `send_confirmation_email` serves both.

**Timing is roughly equalised** (review of #127): the existing-address path hashes the submitted
password once and discards it (`make_password`, like `ModelBackend` does for an unknown user), the
new-account path hashes it in `create_user`, and both then queue exactly one email with
`enqueue_after_commit` -- a new account's confirmation is the `send_signup_confirmation_email` task
(`services.email_confirmation`, skipped if the address moved or was confirmed; a queue failure is
logged and the page's resend-confirmation is the recovery), not an inline send. Forgot-password
and resend-confirmation (web views and `/api/auth/...` alike) queue a task for **every** submitted
address, known or not -- `send_password_reset_email(email)` and `resend_confirmation_email(email)`,
which look the account up by that address when they run (nothing is sent for an unknown one, one
the account has since left, or an already confirmed one) -- so the request does the same work
either way. `PUT /api/account` (logged-in) still calls `send_confirmation_email` inline. What's
left is a database round trip or two, not the SES latency or a password hash. The confirmation email itself now renders from
an autoescaped template (`accounts/emails/email_confirmation.html`, as does the password-reset
one): `user.name` is free text, an attacker's on an account they created.
Not covered by this rule, on purpose: `PUT /api/account` still answers `400 Email already in use`
for an address that has an account, but only to a logged-in caller, and every attempted *change*
spends `ACCOUNT_EMAIL_CHANGE_USER` (5/hour per user) before that check, so an authenticated,
attributable and throttled caller can probe a handful of addresses an hour, which was judged
acceptable next to an anonymous page; hiding it would mean redesigning the email-change flow. And "Sign in with Google"
(see below) links an existing confirmed account or refuses with a specific message, which needs an
identity Google has verified, so it is no free oracle either; left as is.

Changing the email through `PUT /api/account` needs `current_password` (a stolen bearer token
alone would otherwise be the first step of a takeover: change the address, then forgot-password):
400 if it is missing or wrong -- a wrong guess is charged to `ACCOUNT_EMAIL_CHANGE_USER` like any
attempted change, and the password is checked before the "Email already in use" probe -- and 400
for an account with no usable password (Google-only: nothing to re-authenticate with; it can create
one via forgot-password). A name-only update needs none; `name` is capped at the model's 255
characters (422). It un-confirms the account and sends a new
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

**Theme** (issue #166) is chosen under "Appearance" on the account page (system / light / dark;
the header has no toggle) and stored on the account, `User.theme` (default `system`, with a
`db_default` too: the #102 lesson), so it follows the user across devices instead of living in
`localStorage` (the old key is simply ignored). It is a session form view like the two above
(`POST /account/theme/`, `views/theme.py`, `forms.ThemeForm`, `services.theme.set_theme`; web only,
no `/api/` endpoint, no rate limit: one column write per call; `save(update_fields=['theme',
'updated_at'])`, like the Google/Microsoft linking saves). The three radios submit on `change` (htmx,
`hx-sync="this:queue last"` so quick clicks save in order; a `<noscript>` Save button covers no JS);
success answers 200 with the re-rendered partial but `HX-Reswap: none` (swapping the radios would
drop the keyboard focus from the one the user is on) and `HX-Trigger: {"themeChanged": {"theme":
...}}`, which a listener in `core/base.html` applies to the page at once, **with no reload** (the
Tailwind Play CDN generates the `dark:` variants whatever the class, so toggling `dark` is enough);
without htmx it is a redirect, and a bad value is a 422 with `partials/theme_form.html` swapped over
the form, saving nothing. `views.pages.account_page_context` builds the account page's context for
the profile, password and theme views. `core/base.html` renders it for a signed-in user (`core`
can't import `accounts`, but the template just reads `user.theme`): `<html data-theme="...">` plus
`class="dark"` for "dark", server-side, so there is no flash; the early boot script in `<head>` has
one `apply` that reads `data-theme` each time (`dark` for "dark", none for "light",
`prefers-color-scheme` for "system"), so its live follow of the OS setting only matters while the
theme is `system`, and the `themeChanged` listener sets `data-theme` and calls it. `User.theme` is
read-only in the user admin ("Personal info"). A visitor without a session is rendered as `system`. The admin's own toggle (`core/js/admin_theme_toggle.js`) is separate.

**Time zone** (issue #177) is a property of the account, for "today", due dates with a time and
reminders in the user's local time (the To-do app, #161). `User.timezone` is an IANA name or `''` for
"not set yet", treated as UTC everywhere (`CharField(max_length=64, blank=True, default='',
db_default='')`: the #102 lesson again). A non-empty value must be in `zoneinfo.available_timezones()`
(`validators.is_valid_timezone`, cached); the field's validator (`validators.validate_timezone`, so
a `ModelForm` without the field is unaffected by a stale value), the profile and admin forms, the
detection endpoint and the API schema all refuse anything else with `Unknown time zone.`.
`services.timezones` is the one place to compute with it: `user_tz(user)` (a `ZoneInfo`, UTC for an
empty or stale name, which logs a warning once per process per value and never raises),
`user_today(user)`, `local_datetime(user, date, time | None)` (the instant in UTC; a skipped local
time lands forward by the gap, a repeated one is its first occurrence, `fold=0`), `timezone_choices()`
(UTC first, then the `Area/City` names, without `Etc/`, `US/`, `posix/`, bare aliases such as `GMT`
or `Japan` and the `Canada/`-style links) and `set_timezone(user, name, *, only_if_unset=False)`,
which compares with the row under a lock, saves with `update_fields`, returns whether it changed
and, **on commit and only on a change**, sends `signals.timezone_changed` (`sender=User`, `user`,
`old`, `new`; `send_robust`, a failing receiver is logged). A product connects to it from its own
`AppConfig.ready()` (the To-do reminders recompute their instants); `accounts` never imports one.
`TimezoneMiddleware` (`apps/accounts/middleware.py`, right after `AuthenticationMiddleware`)
activates the signed-in user's zone and deactivates it again after the response (a streamed body
is read after that, in UTC), which is why every product's `|date`/`localtime()` output follows the
user; see "Templates and static files" for how it relates to the browser-side helpers. It never
touches `request.user` (a session and a user query) for a path starting with one of
`settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES` = `('/go/', '/s/', '/t/', '/static/', '/api/')`: the
public redirects and download page, static files and the JWT API, none of which has a session
zone, so they render in `TIME_ZONE` (UTC) even for a signed-in owner (a visitor's browser-side
`{% local_time %}` is unaffected). A new hot public prefix goes in that setting. **Behaviour change
for existing users once they have a zone:** everything that asks Django for "now" or "today" while
a request is active follows the saved zone, notably file transfer's end-of-day expiry picker (a
bare date means the end of *that* day in the user's zone) and Receipts' date-range presets ("This
month", "Last 30 days" ending today); with no zone they stay UTC, as before. Expect a "my expiry
is off by a few hours" report to mean the saved zone differs from where the user is. Ways to set it:

- *Account page:* the profile form (`ProfileForm`, model field `name` plus a hand-validated
  `timezone`, a select with `UTC` first; a user with no zone gets a leading "Not set (UTC)" option
  so saving the name alone doesn't set one, a valid zone outside the usual list that the account
  already has is added to it, and a saved name that is no longer in the tz database is still
  offered, labelled "<name> (no longer recognised)"). The zone is not a model-form field: `save()`
  writes only `name` and `updated_at` (`update_fields`) and the zone only through `set_timezone`
  (so the signal fires, and a zone changed since the request loaded the user is never reverted by
  a snapshot of the row; `PUT /api/account` does the same). The form posts a hidden
  `timezone_rendered`, the zone it was built against (for a 422 re-render, the stored one, not the
  typed one): a posted `timezone` equal to it means "untouched" and never writes, and is not even
  validated, so detection saving a zone while the page still showed "Not set", or an untouched
  stale zone, survives saving the name. A post without `timezone`, or without `timezone_rendered`
  (an older client), still works: the former keeps the zone, the latter compares with the database.
  Under the select, `accounts/js/timezone.js` shows "Detected from your browser: ..." with a "Use
  it" link when the browser's zone differs from the selected one and is one of the options, and,
  when detection saves the zone while the select shows "Not set", selects it there.
- *Detection:* the same script (loaded deferred by `core/base.html` for signed-in users only, with
  the endpoint in its `data-url`; `<html data-user-timezone>` carries the saved zone) posts the
  browser's zone, form-encoded with the CSRF token, to `POST /account/timezone/` (`accounts:timezone`)
  once per browser session (`sessionStorage` `tzSent`), only while the account has no zone. The view
  writes with `set_timezone(..., only_if_unset=True)`, so a zone saved meanwhile is never overridden
  (only the account page changes a saved one); it answers 204, 400 `{"detail": "Unknown time
  zone."}`, or the JSON 429 of `ACCOUNT_TIMEZONE_USER` (10/hour per user, charged on every call).
- *API:* `PUT /api/account` takes an optional `timezone` (`""` clears it; an unknown name is a 422
  from the schema validator; no password needed, it isn't security-relevant) applied through
  `set_timezone`, and returns it in `user`; `GET /api/account` returns `{id, name, email, timezone}`.
- *Admin:* a plain editable field in "Personal info" (not a list filter); `UserAdmin.save_model`
  writes a changed zone through `set_timezone` so the signal fires for staff edits too.

Tests: `apps/accounts/tests/unit/test_timezone.py` and the Playwright `tests_e2e/test_timezone.py`
(detection on the page after sign-in, the "Use it" hint, a saved zone surviving a new session).

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

**Sign in with Microsoft** (issue #156) is a fourth way in to the *web UI* and mirrors Google's in
structure: a session login ending in `django.contrib.auth.login()` (so `user_logged_in` fires), an
Authlib OpenID Connect authorization code flow against the Microsoft identity platform (Entra ID),
`GET /auth/microsoft/login` and `GET /auth/microsoft/callback` (`apps/accounts/views/microsoft.py`,
`microsoft_urls.py`, namespace `microsoft_auth:`, exact paths with no trailing slash since the
callback is a registered redirect URI), a same-site `next` kept in the session, every failure a
logged redirect to the login page with a `messages` banner, rate limited per IP
(`MICROSOFT_LOGIN_IP`). The login always sends `prompt=select_account`. On only when
`MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` and `MICROSOFT_REDIRECT_URI` are all set
(`microsoft_oauth.is_configured()`, `microsoft_enabled` in the login/register context; unset, the
button is hidden and both routes redirect with a message; `accounts.W004` warns about a *partial*
configuration). `MICROSOFT_TENANT` (default `common`) is the sign-in authority: `common`,
`consumers`, `organizations` or one tenant id; it must agree with the app registration's supported
account types. The client is built with `client_secret_post` token auth. Setup (Entra app
registration, redirect URIs, secret, optional claims) is in README's "Sign in with Microsoft".

What is *different* from Google, and why: a Microsoft ID token has no `email_verified` claim, and
in a work/school (Entra) tenant `email` is whatever a tenant admin set, so an attacker with their
own tenant can put a victim's address on a user and sign in with it ("nOAuth"). Microsoft's own
guidance is to identify users by `sub` or `oid` and never by email. So
`services.microsoft_oauth`:

- **Matches `User.microsoft_id` first** (nullable, unique): `<tid>:<oid>`, lower-cased
  (`MicrosoftIdentity.account_id`), the ID token's tenant and object id. Not `sub`: that is unique
  per user *and per app registration*, so recreating a registration (a new client id) would give
  every linked user a new one, and the email path would then refuse them as "linked to a
  different" identity, whereas `oid` (requested with the `profile` scope we ask for) is the same
  for every app. The tenant is part of the key because an `oid` is only unique within its tenant
  (a guest has a different one in each). A token without `oid` or `tid` is refused
  (`InvalidClaims`). This needs **neither an email nor `email_confirmed`**: a linked user keeps
  signing in even if the token carries no email, the tenant stops vouching for it, or the address
  was changed through `PUT /api/account`.
- **Creating an account and linking one by email both need an address Microsoft vouches for**
  (`_vouches_for_email`; stricter than Google, where any verified address may create). A *personal*
  account (`tid` = `9188040d-6c67-4c5b-b112-36a304b66dad`) counts only for a mailbox Microsoft
  hosts, from the explicit table `MICROSOFT_MAIL_DOMAINS` (`outlook.com`, `hotmail.com`,
  `live.com`, `msn.com`, `windowslive.com` and their country variants; deliberately a list, not a
  pattern over country suffixes, which would also match domains Microsoft doesn't run -- one
  missing from it **fails closed**: such a user is told to use their password or Google, so add
  it), because a personal account can carry a third-party address verified once, at signup, that
  may since have changed hands. A *work/school* account counts only with the `xms_edov` claim true
  ("email domain owner verified"), an **optional ID-token claim that has to be added to the app
  registration** (Token configuration); without it the email is just text. The `email` claim must
  be a well-formed address (`validate_email`; `live.com`, or one with whitespace, is treated as no
  email at all). `identity_from_claims` falls back to `preferred_username` (a personal account's
  sign-in name, which Microsoft assigns: no tenant admin can set it) **only for the personal
  tenant and only when `email` is absent**, and it then still has to pass the mail-domain rule;
  for a work/school account `preferred_username` and `upn` are never used. A token with no email
  can only sign in a linked user (`NoEmail` otherwise); an unvouched one is `UnverifiedEmail`;
  both write nothing.
  Why creating needs it too: the new account is confirmed and has no password, so an unproven
  address would be a pre-hijack of whoever owns it (the attacker's identity would stay linked after
  the owner reset the password).
- **Linking** then follows Google's rules: only an account whose `email_confirmed` is true
  (`UnconfirmedAccountConflict` otherwise, nothing written), changing nothing but `microsoft_id`;
  an account linked to a *different* Microsoft identity, or two accounts matching the email, are refused
  (`AccountConflict`); inactive users are refused before anything is written. An unconfirmed
  account with a `microsoft_id` is left alone by the signup-for-an-existing-address path, like a
  Google one, and a password-less Microsoft account is told to "Sign in with Microsoft".
- **The issuer is checked by us, not Authlib.** The `common`/`organizations`/`consumers`
  discovery documents name a *templated* issuer (`https://login.microsoftonline.com/{tenantid}/v2.0`)
  that no token carries, so Authlib's own `iss` check would reject every sign-in. The callback
  passes `claims_options=CLAIMS_OPTIONS` (`iss` essential, no values) -- Authlib still validates
  the signature, audience (the client id), expiry and nonce -- and `identity_from_claims` requires
  `iss == https://login.microsoftonline.com/<tid>/v2.0` for the token's own `tid`.
  `tests/test_microsoft_auth.py` signs a real JWT and runs Authlib's validation for this.
- The login and register pages render one "or" divider and the enabled buttons through
  `partials/social_buttons.html` (`google_button.html` and `microsoft_button.html` are the buttons
  alone, each taking `verb`).

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

### Mail parsing

`apps.core.services.mail` (issue #186) is the code that reads a raw inbound email, moved out of
Receipts so To-do reads mail with the very same parser and sender authentication; it imports only
the stdlib (and Pillow in `images`), so `core` gains no dependency. **Receipts' behaviour did not
change**: `apps.receipts.services.mime` and `.addresses` are now thin modules that import from here,
re-export everything and bind Receipts' parameters (small wrapper functions with the signatures the
Receipts call sites always had; Receipts' `inbound.py` is untouched).

- `mime.py` (the old `receipts/services/mime.py`, whose module docstring is still the long version
  of the SES header shapes, the parser limits and the defensive `Authentication-Results` reading --
  see "Receipts" > "Email intake"): `parse_message`, `scan_recipients`, `read_verdicts` /
  `Verdicts.vouches_for`, `iter_leaves`, `analyze`, `build_stripped_eml` and the forwarding-
  confirmation helpers. What was product-specific is a parameter:
  - `scan_recipients(msg, *, inbound_address)` and `recipients_from_addresses(addresses, *,
    inbound_address)` take the product's `<product>@<domain>` base address (Receipts passes
    `settings.RECEIPTS_INBOUND_ADDRESS`, To-do `settings.TODO_INBOUND_ADDRESS`);
    `addresses.inbox_code_from_address(address, *, inbound_address)` likewise, and the canonical
    `<product>+<code>@<domain>` is built from it (the Receipts-only `accounts.inbox_address` import
    is gone).
  - `analyze(msg, *, allowed_types, max_file_bytes, min_inline_image_px, sniff, html_to_text=None,
    image_dimensions=None, image_types=())`. The three things that used `receipts.services.
    documents` are parameters: `sniff` (`documents.sniff_content_type`), `html_to_text` (to choose a
    body; **without it no body is chosen**) and `image_dimensions` + `image_types` (the tiny-inline-
    image check; without them nothing is skipped as an inline image). **`allowed_types=None` means
    any type** -- an unrecognised one included (`content_type` is then `''`) -- which To-do uses;
    a list filters as before.
  - `build_stripped_eml(raw, parsed, markers, *, marker_headers, stripped_header)`: the names of the
    headers that mark a stripped part (`('X-Receipts-Receipt-Id', 'X-Receipts-Skipped')`) and of the
    "headers only" line (`X-Receipts-Stripped`) are the caller's (Receipts' wrapper has them as
    `RECEIPT_ID_HEADER`, `SKIPPED_HEADER`, `STRIPPED_HEADER`). To-do keeps no copy of the message.
- `addresses.py`: `normalize_address`, `parse_allowlist`, `matches_allowlist`, `inbox_code_from_address`.
- `text.py`: `html_to_text(html, *, max_chars)`, the stdlib-parser conversion To-do uses for a
  description (block elements become lines, `li` starts `- `, a link becomes `text (url)` -- only
  `http`, `https` and `mailto`; scripts, styles and the head are dropped). Receipts keeps its own
  `documents.html_to_text` (it runs in the document child process and has no links).
- `images.py`: `image_dimensions` (Pillow, header only) and `IMAGE_TYPES`, the To-do binding for the
  inline-image check (Receipts passes its HEIC-aware `documents` versions).
- Tests: `apps/core/tests/mail/` (`test_mime.py` and `test_addresses.py` are Receipts' old tests moved
  and adjusted to pass the parameters -- the `_HeaderLineCounter.readline` spy test moved with the
  parser --, `test_text_and_images.py`); `apps/receipts/tests/test_mail_wrappers.py` checks the
  wrappers bind the right values. The tests build messages with `apps/receipts/tests/emails.py`
  (tests are exempt from the import contract, and To-do's use it too). The one edit to a Receipts
  test that stayed: `test_inbound.py` patches `MAX_EML_BYTES` on the core module now, and
  `test_admin.py` looks its permissions up by app label too (To-do has an `InboundMessage` model).

### Notifications

`apps.core` has a site-wide **in-app notification system** (issue #169): a bell in the header of
every page for a signed-in user, a dropdown, and a `/notifications/` page. A notification is
generic because `core` cannot import a product: a product creates one through a `core` service and
hands it plain text and a relative URL it built itself with `reverse(...)`.

```python
from apps.core.services import notifications

notifications.notify(
    user,  # a user, or just its id
    source='file_transfer',  # the creating app's label
    title='Transfer suspended: report.pdf',  # one line, <= 140 chars
    body='It ran out of credits ...',  # optional plain text, <= 500 chars
    url=reverse('file_transfer:dashboard'),  # optional, relative; where a click goes
    key=f'transfer_suspended:{transfer.id}',  # optional; what the notice is about, to clear it later
)
```

- **`notify`** writes one `core.Notification` row (UUID id, `user` FK with `CASCADE`, `source`,
  `title`, `body`, `url`, `key`, `created_at`, `read_at`, `opened_at`, `dismissed_at`; indexes on
  `(user, read_at)`, `(user, created_at)`, `(user, key)` and `created_at` for the retention job) inside a
  savepoint in the caller's transaction, so it commits or rolls back with the caller's work and a
  failed insert cannot poison it. It does not queue anything, and **there is no email channel in v1**:
  products keep sending their own emails and call `notify` next to them. The title's whitespace is
  collapsed and title/body are *truncated* (with an ellipsis) to the model's limits rather than
  refused, because they often carry something a user typed (a file name) and the event reported must
  not fail over it. No HTML: every template escapes everything (a test renders a `<script>` title). A
  missing `source`/`title`, or a `url` that is not a **same-site path** (`is_safe_url`: one leading
  `/`, so not `//host`; no scheme, backslash, space or control character; Django's
  `url_has_allowed_host_and_scheme` with no host allowed) is a programming error and raises
  `ValueError`. The open view checks the stored URL again, so even a row written another way can
  never become an open redirect (it then sends the user to the notifications page).
- **`key`** (optional, <= 200 characters, not shown, not unique) is a string the creating app
  chooses to say *what the notice is about* (`transfer_suspended:<transfer id>`,
  `receipts_account:frozen`), so the app can delete exactly that notice when the subject is fixed:
  `delete_for_key(user, key, source=None)` (an empty key is a `ValueError`, since every keyless
  notice carries one). It exists because `url` can't do that job: every suspended transfer of a
  user links to the same dashboard, so `delete_for_url` would wipe the notices of the ones still
  suspended. `delete_for_url` stays for a link that names one thing.
- **Two products use it so far**, next to their existing emails and in the same transaction:
  Receipts "storage paused" (`receipts.services.metering.freeze_account`, url: the Receipts
  dashboard, key `receipts_account:frozen`) and file transfer "suspended"
  (`file_transfer.services.metering.suspend_transfer`, url: the transfer dashboard, key
  `transfer_suspended:<transfer id>`, from `metering.suspended_notice_key`; its body says how many
  credits are needed, `daily_cost`, like the email). Each hook runs in **its own savepoint**
  (`transaction.atomic()` around the whole body, inside a `try/except` that logs), not just
  `notify`'s: on Postgres a database error that is merely caught still aborts the surrounding
  transaction, and the commit would then roll the freeze or suspension back. For the same reason the
  hook queries nothing it can be handed: `meter_transfers` loads `suspension_grace_days` and the
  storage price once per run and passes them through `meter_transfer` -> `suspend_transfer`
  (loaded in the hook only when a caller passes nothing).
  **Both delete their notice when the subject is resumed**, in the resume's own transaction and
  savepoint, logged and never raised, so tidying a notice can't undo or fail a resume: file
  transfer in `metering.reenable_transfer`, only on a successful re-enable of a *suspended* transfer
  (the credits receiver, the daily fallback and the dashboard/API action all go through it; a lost
  race or a transfer that stays suspended keeps its notice, as does a disabled one's re-enable), and
  Receipts in `metering.unfreeze_account` when it wins the write. A notice for a transfer that is
  instead deleted at the end of its grace period stays, as a record of what happened.
- **Read, opened, dismissed** (`apps.core.services.notifications`' module docstring is the long
  version). *New* means `read_at` is null. The bell counts what is new and not dismissed
  (`unread_count`, one indexed `COUNT`). The dropdown lists the latest 10 (`DROPDOWN_LIMIT`) that
  are neither dismissed nor *opened*, newest first, and **rendering it marks the ones it lists
  read** (so the badge clears when the user looks; the response still highlights the ones that were
  new). A notification stays in the dropdown, read, until the user clicks it (`opened_at`) or
  dismisses it (`dismissed_at`, which also counts as read). "Mark all as read" clears the *new*
  state of everything, including what the 10-item cap left out, and leaves the notifications listed.
  The page lists everything not dismissed, 20 per page, newest first; read ones are visibly
  different (lighter weight, no dot, no "Mark as read" button), and looking at the page marks
  nothing. One consequence of the rules: a notification **without a `url`** is not a link, so it can
  only leave the dropdown by being dismissed. **Keyboard focus survives the swaps** (`components.js`):
  a container marked `data-keep-focus` (`#notification-dropdown-body`, `#notification-list`) remembers,
  when a request starts from inside it, the place of the control used among the container's controls and
  focuses the control now at that place after the swap (else the container), instead of dropping to
  `<body>`; and Escape closes the dropdown from anywhere (`@keydown.escape.window`).
- **Scoping.** Every service function takes the requesting user and filters on it; someone else's
  notification raises `Notification.DoesNotExist`, which the views turn into a **404, never a 403**.
  `mark_read(user, ids)` ignores ids that are not the user's; `mark_all_read(user)`;
  `mark_opened(user, id)`; `dismiss(user, id)` (idempotent); `get_notification`;
  `dropdown_notifications`; `page_notifications`; and `delete_for_key(user, key, source=None)` /
  `delete_for_url(user, url, source=None)`, for a product whose target no longer needs attention
  (a stale notification).
- **Views** (`apps/core/views/notifications.py`, mounted at the site root under `core:`):
  `GET /notifications/` (page), `GET notifications/list/` (the page's list partial, htmx
  pagination; opened directly it redirects to the page), `GET notifications/bell/` (the badge, polled),
  `POST notifications/dropdown/` (opens the dropdown: a POST because it writes, `read_at`),
  `POST notifications/read-all/`, `POST notifications/<id>/read/`, `POST notifications/<id>/dismiss/`
  and `GET notifications/<id>/open/` (marks read and opened, then redirects to the stored URL, a
  plain link so middle-click works; a write on a GET, the deliberate trade-off being that a link
  previewer following it would take the item out of the dropdown). All are `@login_required` and session + CSRF; the actions follow
  the htmx convention (an htmx request gets the list it came from back, chosen by the posted `scope`
  of `dropdown` or `page`, plus the bell's badge out of band; without htmx they redirect to the
  page). The two fragments htmx fetches in the background (the bell and the dropdown) answer an htmx
  request with no session an empty **204** instead of the login redirect, so the login page is never
  swapped into the header. `core`'s views use `apps.core.http.AuthenticatedHttpRequest`
  (`user: PermissionsMixin`), since `core` can't import `accounts`. No rate-limit rule: the endpoints
  are cheap, session-bound and covered by nginx's coarse limit.
- **The bell** (`core/partials/notification_bell.html`, included by `core/base.html` inside
  `{% if user.is_authenticated %}`, so an anonymous page does nothing and costs nothing). The count
  comes from the `{% unread_notification_count %}` tag (`templatetags/notification_tags.py`), not a
  context processor, so only a page that renders the bell pays for the one `COUNT`. The badge
  (`#notification-badge`) polls `notifications/bell/` **every 60 seconds, only while the tab is
  visible** (`every 60s [document.visibilityState === 'visible']`), and fetches once when a hidden
  tab becomes visible again (`visibilitychange[...] from:document`), then replaces itself; it is
  always rendered (hidden at 0) so the polling element persists, and the dropdown, list and action
  responses swap it out of band to keep the count in step. It shows `99+` past 99 (the digits are
  `aria-hidden`; an `sr-only` "N new notifications" carries the full count). **The button has no
  `aria-label`**: that would replace the name made from its content and hide the badge, so its
  accessible name is its `sr-only` "Notifications" plus the badge's sentence, which lives inside
  the button and so survives every swap (`test_notification_bell.py` parses the rendered button
  to check it). It uses `aria-controls="notification-dropdown"` and `aria-expanded`, not
  `aria-haspopup` (the panel is not a `role="menu"`). The `notificationBell` Alpine component
  (`core/js/components.js`) opens the panel and triggers its htmx POST (so closing asks for
  nothing). The dropdown's "new" highlight lasts only for the response that opened it: an action
  inside it re-renders the list after the opening request marked its rows read (accepted). On a phone the panel
  spans the header (`<header class="relative">`, `inset-x-4`); from `sm` it hangs under the bell.
- **Retention**: `NOTIFICATION_RETENTION` (90 days, `core/jobs.py`), applied to `created_at`
  whatever the state, by the daily `core.purge_old_notifications` job in `PURGE_BATCH` batches
  (registered from `CoreConfig._register_jobs`, like `core.purge_old_task_results`).
- **Admin**: read-only `NotificationAdmin` (filter by source, read state, dismissed, date; search by
  the user's email or name and by title). Deleting a user removes their notifications through the
  cascade; `accounts`' `UserAdmin` lists `core.notification` in `CASCADE_WITH_USER_MODELS` (a
  model-scoped twin of `CASCADE_WITH_USER_APP_LABELS`, so a future `core` model isn't exempted
  silently) so the read-only admin doesn't refuse that.
- **Not in v1**: an email or push channel, per-user preferences, grouping, a JWT API, websockets
  (the count is polled), a browser-extension or mobile client.

### Markdown

`apps.core.markdown` (issue #176) is a library with no views, for any app that stores Markdown
(the TODO app's descriptions and comments, #161): it renders a fixed subset of Markdown to
sanitised HTML, produces the plain text search runs over, and flips a checklist box inside a
document. It has no dependency on a product; the TODO-specific pieces, turning `#1234` into a link
and knowing which URL is a task page, are a callback and a pattern the caller passes in.
Dependencies: `markdown-it-py` (CommonMark; `>=3.0,<5.0`, the lock selects 4.x, the version `rich`
also uses), `mdit-py-plugins` (`tasklists`), `linkify-it-py` (bare URLs) and `nh3` (sanitiser;
`bleach` is deprecated). Layout: `parser.py` (the one parser), `renderer.py` (token -> HTML
rules), `references.py` (task references), `extract.py` (`extract_references`), `tasklist.py`
(checklist items), `links.py` (which destinations are links), `inline_text.py` (the text of inline tokens, shared by plain text and
image labels), `limits.py` (the length guard), and the public `render.py`, `plain.py`,
`checklist.py`, all re-exported from `__init__.py`.

- **`render(markdown, *, reference_resolver=None, task_url_path=None) -> SafeString`.** Raises
  `ValueError('Document too long')` over `MAX_MARKDOWN_CHARS` (50,000); callers validate their own
  field caps first (description 20,000, comment 10,000). The same guard (the same `ValueError`)
  is applied by `to_plain_text` and `search_text`, so the functions that turn a document into HTML
  or text share one contract; `normalize_for_search` takes plain text, not a document, and has none, and the
  checklist functions don't either (the caller has validated what it stored). `task_url_path` is
  the caller's `re.Pattern` for a task page's site-relative path, with a named group `number` and
  matching the whole path (`fullmatch`; `^/todo/(?P<number>[0-9]{1,9})/?$` for the To-do app --
  use `[0-9]`, not `\d`, which matches other scripts' digits); a pattern without the group is a
  `ValueError`. **Core knows no task URL itself**: with `None`, URLs are never task references.
  `task_number_from_href(href, task_url_path)` (exported) is the function behind it, for a caller
  that needs the same recognition elsewhere. `extract_references(markdown, *, task_url_path=None)
  -> list[int]` (exported; issue #180) is the same traversal `render` does to find what to hand
  its resolver, without rendering: every task number a document mentions (`#1234`, and links to a
  task page when the caller passes `task_url_path`), once each, in order of appearance, with the
  same size guard and pattern check as `render`; what the TODO app stores as `TaskReference` rows. The `markdown` template filter
  (`{% load core_markdown %}{{ text|markdown }}`, `templatetags/core_markdown.py`) calls `render`
  without a resolver or pattern; a caller with references renders through its own tag. **The
  filter never raises**: a value over the limit (a `ValueError` from `render`) comes out
  unrendered, HTML-escaped with line breaks kept, in one `<p>` (and a warning with its length,
  never its content, is logged), so a page showing an over-long field is not a 500.
- **Supported syntax, exactly:** paragraphs, `*italic*`/`_italic_`, `**bold**`/`__bold__`,
  `` `code` ``, fenced and indented code blocks, headings `#` to `######` (no `id`, no anchors),
  ordered and unordered (nested) lists, checklists, block quotes, horizontal rules, links, bare
  URLs and task references. A single newline is a `<br>` (`breaks`). **Not supported, and shown as
  literal text:** tables, footnotes, strikethrough, and raw HTML (`html=False`: escaped, never
  rendered). `[^1]: note` is still a CommonMark link reference definition, so it disappears into
  the reference-style link it defines. **Images are never rendered**: `![alt](url)` is a link with
  the alt text (the URL when the alt is empty), or just that text when the URL is not a safe link
  or the image sits inside a link. The alt text is `inline_text`, the very text `to_plain_text`
  keeps: literal text, code spans, `#1234` references (as text, not resolved) and nested alt
  text, emphasis marks dropped. Whether an image is inside a link is known from a stack of open
  links in the render `env` (`link_open` pushes, `link_close` pops), so rendering is linear in
  the document (a 50,000-character document of `![](a) ` renders in well under a second). A fence's first info word becomes `class="language-<tag>"` on
  `<code>`: lower-cased, anything but `a-z 0-9 - +` dropped, cut to 20 characters, no class when
  nothing is left.
- **Links.** `parser._Parser.validateLink` accepts everything (markdown-it's own check would leave
  `[x](javascript:...)` as literal source) and `links.classify_href` decides: `http`/`https`,
  `mailto:` or a site-relative path (one leading `/`, not `//host` or `/\host`) are anchors; any
  other scheme or shape renders the link's text with no anchor. External `http`/`https` get
  `target="_blank" rel="noopener noreferrer nofollow ugc"`, `mailto` gets `rel="nofollow ugc"`, a
  site-relative link neither. **A same-site absolute link** (`LinkKind.SAME_SITE`: `http`/`https`
  with exactly the authority of `settings.BASE_URL`, case-insensitive; a different port, a
  userinfo or a longer name like `app.example.com.evil.com` is another site) renders like a
  relative one, no `target`, no `rel`, its `href` unchanged. The URL is only what markdown-it's
  `normalizeLink` makes of it. **Bare URLs** (`parser._configure_linkify`): linkify-it's
  `fuzzy_link` is off, so only text that names its scheme (`http://...`, `https://...`), starts
  with `www.` or is an e-mail address (`fuzzy_email` stays on: `mailto:`) becomes a link;
  `manage.py`, `README.md`, `settings.py`, `example.com` stay text (`.py`, `.md`, `.sh`, `.io` are
  country TLDs). linkify-it has no separate switch for `www.`, so it is added back as a schema of
  its own (`www.` followed by at least one more label, normalised to `http://www....`).
- **Task references and the resolver.** `ReferenceResolver = Callable[[list[int]], dict[int,
  str]]` is called **once per render** (not at all when the document mentions no task) with every
  task number found, deduplicated, in order of appearance, and returns the numbers it recognises
  mapped to the URL each links to; a number missing from the result stays plain text. What counts
  as a mention: `#` plus 4 to 9 digits (`#1` to `#999` never; numbers start at 1000) at the start
  of the text or after whitespace or one of `( [ { " ' , * _`, with the end of the text,
  whitespace or one of `) ] } " ' , . ; : ! ? * _` right after (`references.task_reference`, an
  inline rule before `emphasis`, which is why `**#1234**` and `_#1234_` are references: it sees
  the raw `*`/`_`; never inside a code span or block, nor inside a link's text,
  `state.linkLevel`); and a bare or `[text](...)` link whose path matches the caller's
  `task_url_path`, site-relative or on the host of `settings.BASE_URL`, with no query or fragment
  (`links.task_number_from_href`, marked by a core rule after `linkify` so bare URLs count; the
  pattern travels in `env['task_url_path']`, and the rule does nothing without one). A resolved one renders
  `<a href="<url>" class="task-ref">`, with the text unchanged; the URL the resolver returns must
  itself be a safe link or it is ignored. Without a resolver both render as plain text and
  ordinary links.
- **Checklists.** `- [ ]`, `- [x]` and `- [X]` render
  `<li class="task-list-item"><input class="task-list-item-checkbox" type="checkbox" disabled
  data-index="<n>">`. The plugin's raw `html_inline` checkbox is replaced in the token stream by a
  `task_checkbox` token (`tasklist.number_checkboxes`, a core rule after the plugin's) that carries
  the index, so no HTML is rewritten; `n` counts checklist items in document order from 0. The
  boxes stay `disabled`: the caller's own script removes it when the viewer may edit. What is an
  item is the plugin's rule (`[ ]`, `[x]` or `[X]` then whitespace and more text, first thing in a
  list item's first paragraph), shared with the functions below through
  `tasklist.checklist_inlines`, so the indexes always agree.
- **`toggle_checklist_item(markdown, index, checked)` / `count_checklist_items(markdown)`** work
  on the source text: the parser's token map gives the source line of the `index`th item and only
  its three-character marker changes (`[x]` or `[ ]`); whitespace, the rest of the line and the
  line endings (`\r\n`, `\r`, `\n`) are kept byte for byte. Items in nested lists and block
  quotes count; a fence's content does not. `IndexError` when there is no such item. The caller
  validates and saves the result (and its own search text) in its own endpoint.
- **`to_plain_text(markdown)`, `normalize_for_search(text)`, `search_text(markdown)`.** Plain text
  keeps the text of paragraphs, headings, code (inline and blocks), link text (a bare URL is its
  own text), image alt text and the literal `#1234` of a reference; it drops emphasis and heading
  marks, checklist boxes, link destinations and fence markers. Paragraphs and list items end in a
  newline, other blocks (headings, quotes, lists, code blocks, rules) are separated by one space.
  `normalize_for_search` is NFKD, characters with a combining class (`unicodedata.combining`,
  the accents NFKD splits off Latin, Greek, Cyrillic ...) dropped, `casefold`, everything that is
  not a letter, number, mark (categories L, N, M, any script) or whitespace turned into a space,
  whitespace collapsed: `Cão, Ação & Coração!` is `cao acao coracao`. Marks with combining class 0
  (the vowel signs of Devanagari, Bengali, Thai ...) stay, so `किताब` is one word, not three
  fragments; a nukta or virama has a class and goes with the accents (`पढ़ो` is `पढो`, on the
  stored text and the query alike, so matching is unaffected; so is NFKD splitting voiced
  Japanese kana, `ガ` -> `カ`). `search_text` is the two
  in a row, what the TODO app stores next to every Markdown column.
- **Sanitiser.** The renderer's output goes through `nh3.clean` with an explicit allow-list, and
  only then `mark_safe`: tags `p br strong em code pre h1`-`h6 ul ol li input blockquote hr a`;
  attributes `a: href target rel class`, `code: class`, `input: type checked disabled class
  data-index`, `ol: start`, `li: class`; URL schemes `http`, `https`, `mailto`; `link_rel=None`
  (the renderer sets `rel`). A consequence: the plugin's `contains-task-list` class on `ul`/`ol`
  does not survive (style `li.task-list-item`). nh3 writes boolean attributes as `disabled=""`.
- **One parser per thread** (`parser.get_parser`): `linkify-it-py` keeps match state on the
  instance, so a shared parser is not thread-safe under a threaded gunicorn worker.
- **Tests:** `apps/core/tests/test_markdown.py`.

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
  alias for tests that opt into the cache storage path. A test that makes two requests against a
  limit per fixed window, or asserts on `Retry-After` / `retry_message`, uses the root
  `conftest.py`'s `ratelimit_clock` fixture (freezes the limiter's own clock 1,000 s into an hour,
  `advance()` to move it): windows are aligned to the wall clock, so with the real one such a test
  fails whenever it runs within a window length of a boundary.
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
  `PUT /api/account`, per user), `ACCOUNT_TIMEZONE_USER` (the browser's time zone detection, 10/hour per
  user, see "Auth" > "Time zone"), forgot-password (as above,
  and its response shape never changes so existence still isn't leaked), resend-confirmation, `CONFIRM_EMAIL_IP` (the confirming POST of the emailed link, per IP, generous), QR preview/create (per user, shared between
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
  password), and `FT_RECIPIENT_EMAIL_USER` (30/hour per user: "Resend" and "Add recipients" on an
  existing transfer, web and `/api/ft/` sharing one budget). It is charged **one hit per email
  queued** (a resend is 1, an add is one per new address), so 30 is the most such emails an account
  sends an hour, not calls. The charge is a `charge=` callback
  (`services.actions.charge_recipient_emails`, bound to the user by the dashboard view and the API
  endpoint) that `add_recipients` / `resend_recipient_email` call only after every other check
  (actionable, held, blocked, address format and recipient cap, cooldown, "anything new") and
  before anything is created or queued: a refusal costs no budget, and a call that does not fit
  the budget emails nobody (a `RateLimitExceeded` whose message says "try again in N minutes",
  built by `ratelimit.retry_message`; the htmx 429 re-renders the dashboard row with it rather
  than replacing it). So a held transfer is a 409 (422 on the web) even when the budget is spent.
  The budget bounds how much one account sends, not at whom: `services.actions.RESEND_COOLDOWN`
  (10 min) refuses a resend (a `ValidationError`, 422/400) while the recipient's `last_sent_at`
  (set by the initial send, an add and every resend) is newer, so one address gets at most 6 an
  hour from a resend. `limits.validate_recipients` also checks the address format now (the JWT API
  passed raw strings through). `LOGIN_IP` (20/5 min) and `SIGNUP_IP` (5/hour) can
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
- **nginx** (issues #119, #170): a coarse per-IP `limit_req` sits in front of the whole site as
  defense-in-depth below the application layer. It is configured in the separate `infra` repo
  (`apps/ziptrigo-apps/nginx.conf`, both `app.ziptrigo.com` and `app-staging.ziptrigo.com`), not
  here, and is described here as designed, not as deployed (check the issue's checkboxes). Per
  environment there are two zones, routed by a pair of `map`s on the raw `$request_uri` (what
  gunicorn receives; `$uri` is normalized, so it would let `/x/../go/abc` pass for a short link):
  the main zone (`ziptrigo_apps_prod` / `_staging`, keyed on `$binary_remote_addr` -- the visitor,
  since both HTTPS server blocks include `cloudflare-realip.conf` -- for everything except the short
  link shapes below) at 20 r/s with `burst=100 nodelay`, and **short links on their own looser
  zone** (`ziptrigo_apps_go_prod` / `_staging`, 50 r/s, `burst=500`; the `go` in the name is
  historical, it now serves `/s/` too) instead of an exemption: a scan burst from one NAT address is
  sized to stay well inside it, so a crowd does not see an error, the same reason the app itself
  never refuses the redirect, but a flood of made-up codes (one lookup each, which the app's own
  rule does not shed: an unknown code over its limit is still looked up and 404s) is still shed. The
  short link shapes are Django's routes read off the raw URI, each followed by the end or a query
  string: QR's `/go/<code>` (`^go/(?P<short_code>[^/]+)/?$`) and, since issue #170, Short URLs'
  `/s/<code>`, `/s/<code>+` and `/s/<code>/unlock` (`apps/short_url/redirect_urls.py`: code
  `[A-Za-z0-9_-]+`, an optional trailing slash). The "a crowd never sees an error" reasoning covers
  the redirects and `GET /s/<code>/unlock` (the form, which a scan of a password-protected link
  reaches through its 302): the app answers those whatever the load (over `SU_REDIRECT_IP` it only
  skips recording the click, and for a link with `max_clicks` still writes the counter, without the
  event). The preview (`SU_PREVIEW_IP`, 60/min, a plain 429) and `POST /s/<code>/unlock`
  (`SU_PASSWORD_IP`, 15 per 10 min) have much stricter per-IP limits of their own in the app, so for
  those the zone is only a ceiling. Dot-segment, percent-encoded, `/go/a/b/c` and `/s/a/b/c` paths
  are counted by the main zone, and so is `/s/<code>/report` (a form POST that the app limits
  strictly per IP, `SU_REPORT_IP`). The numbers are far above ordinary use (the receipts list polls
  every 3 s, htmx search per keystroke pause, upload JSON calls), so the app's own limits above
  remain the real ones. Over the limit nginx answers **429** with a short plain-text body and
  `Retry-After: 1`, not the app's JSON or htmx partial: `core/base.html`'s `htmx-config` swaps 429,
  so an htmx request rejected by nginx swaps that text into its target, acceptable during a flood.
  nginx keys IPv6 on the full /128, unlike the app's /64. Presigned S3 uploads never reach nginx at
  all. Nothing in this repo depends on it.

### API

`config/api.py` builds one `NinjaAPI`; each app exposes a `router` from its `api` module/package
and is mounted under its prefix (`/api/` for accounts, `/api/billing/`, `/api/qr/`, `/api/ft/` for
file_transfer, `/api/receipts/` for receipts, `/api/s/` for short_url, described under "Short URLs"
above, including how it handles credits for a client with no one to confirm with, and `/api/todo/`
for the To-do app, described just below and under "To-do" > "API and CLI"). Docs at `/api/docs`.

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
`expiry_date` without `expiry_choice`, or `?filter=` outside `active`/`ended`/`all`; an `expiry_date`
with no UTC offset is read in `TIME_ZONE` by `resolve_expiry`, not a 500; a date alone, `2027-12-31`, means the end of that day like the web date picker -- `schemas.transfer.ExpiryDate`), **402** for
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

`apps/todo/api/` (issue #187; `/api/todo/`) is JWT-only like the receipts one (router-level
`auth=JWTAuth()` in `api/router.py`; endpoint modules `tasks.py`, `labels.py`, `comments.py`,
`reminders.py`, `attachments.py`, `filters.py`, `settings.py`, `parse.py` share that one `Router`;
schemas in `apps/todo/schemas/`) and calls the exact same `apps.todo.services` the web views do,
adding no rule of its own. A task is addressed by its **number** (`/tasks/{number}`), everything
else by its UUID. Endpoints, relative to the mount: `GET /tasks` (`view`, `q`, `mode`,
`include_completed`, `priority`, `label`, `date_range`/`date_from`/`date_to`, `sort`, `parent`,
`limit` 1-100 default 50, `offset`; `{count, limit, offset, results}`), `POST /tasks` (201),
`GET|PATCH|DELETE /tasks/{number}` (`DELETE` moves to the Trash, 204, idempotent; `GET` also returns
a trashed task, with its uploaded `attachments` and `referenced_by`), `POST /tasks/{number}/` with
`complete`, `uncomplete`, `restore`, `delete-forever`, `postpone`, `checklist`, `recurrence/stop`,
`POST /tasks/reschedule-overdue` and `POST /tasks/reorder` (both `{count}`), labels (`GET|POST
/labels`, `PATCH|DELETE /labels/{id}`, `POST /labels/reorder`), comments
(`GET|POST /tasks/{number}/comments`, `PATCH|DELETE .../comments/{id}`, `POST .../restore`, `GET
.../history`), reminders (`POST /tasks/{number}/reminders`, `PATCH|DELETE .../reminders/{id}`,
`GET /reminders/history`), attachments (`POST /tasks/{number}/attachments`, `POST
.../attachments/{id}/complete`, `POST /attachments/{id}/cancel`, `GET /attachments/{id}/url`,
`DELETE /attachments/{id}`), saved
filters (`GET|POST /filters`, `PATCH|DELETE /filters/{id}`, `POST /filters/reorder`, `GET
/filters/{id}/tasks`), `GET|PATCH /settings`, `GET /inbox`, `POST /inbox/regenerate` and `POST
/parse`. Status codes (`api/common.py`): **400** a service `ValidationError` (`field: message`, a
message that belongs to no field plain) or a hand-read query parameter (`view`, `mode`, `sort`,
`priority`, `label`, `date_range`, `date_from`/`date_to`, `disposition`); **402**
`InsufficientCreditsError` and `AccountFrozenError`; **404** another user's task, label, comment,
reminder, attachment or filter (never 403), also `delete-forever` of a task that isn't in the Trash
(as the web) and an attachment that never completed (except for `cancel` and `DELETE`, which cancel
it); **409** `POST /tasks` while a repeating task's next occurrence is waiting to be paid for,
`{detail, unpaid_task_numbers}` (raised by the service as `TaskCreationBlockedError`, never matched
by its text); **422** ninja's own validation (a malformed number or id, a mistyped field, an unknown
body key: every body schema is `extra='forbid'`; a title over `TITLE_MAX` (500) in `POST /tasks` or
`PATCH`; a reorder list over its cap: 500 tasks or labels, `MAX_SAVED_FILTERS` filters) and an empty
or over-long title for `POST /parse`; **429** from the site handler;
**503** only for a storage failure while deleting an attachment. A reference in a body to something
that isn't the caller's (`labels`, `parent`) is a 400 with the service's own message, like Receipts'
foreign `vendor_id`. The rate limits are the web's, so the budgets are shared: `TODO_CREATE_USER`
(`POST /tasks`), `TODO_EDIT_USER` (every other change; an empty `PATCH`, and deleting or restoring
a comment that already is, spend nothing), `TODO_COMMENT_USER` (adding and editing a comment),
`TODO_SEARCH_USER` (a `GET /tasks` with `q` or any of `priority`, `label`, `date_range`, and
`GET /filters/{id}/tasks`; charged before the query, so a refused pattern still spends it),
`TODO_PARSE_USER` and `TODO_PARSE_USER_DAY` (`POST /parse` and `parse_title`; peeked first and hit
only when the model is called), `TODO_UPLOAD_USER`, `TODO_UPLOAD_COMPLETE_USER` (charged first),
`TODO_DOWNLOAD_USER` and `TODO_REGENERATE_USER`. **A refused request spends no budget**
(`POST /tasks`, `PATCH /tasks/{number}`, postpone, reorder and the label, comment, reminder, filter
and settings writes check first, as Short URLs' `check_new_link` / `check_update` do, then charge,
then write): the fields, the 409, the cap on open tasks and the balance are told by
`services.check_new_task` / `check_update_task` (no writes, no lock). What only the locked write can
decide still spends one hit: a race for the last task slot or credit, a task trashed or completed
since it was read, a task with subtasks moved under a parent, a subtask given a repeat rule, a
sender allowlist the service refuses, and a `parse_title` reading that then fails the create. The
attachment endpoints keep the web's order (charged before the lookup; `cancel` answers its 400 for a
completed file first). Where the endpoint looks up a task the lookup (404) comes before its limit is
charged. Tests: `apps/todo/tests/test_api_*.py` over Django's test
client (the real `/api/todo/...` URLs), helpers in `tests/api_support.py`.

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
  same way and need the same resume-matching logic. `initDropZone`, shared with the Receipts upload page and loaded before it from `apps/core/static/core/js/drop_zone.js`, makes each page's drop zone accept dropped files *and* folders (walked recursively, flat like the "Add folder" picker), feeding the same `handleFileSelection`. Presigned part URLs are requested a small batch
  at a time (`PART_URL_BATCH_SIZE`) rather than for the whole file up front, so a later part's URL
  on a slow connection doesn't sit long enough to expire before it's used; the same batching (plus
  streaming the file a part at a time rather than holding it all in memory) applies to the CLI's
  upload, below. A "paused" (not-yet-finished) row is silently left out of the transfer at send
  time (`services.send.finalize_send`/`start_confirmation` only count `uploaded=True` files), so
  the script asks for confirmation before letting the options form's htmx submit through
  (`htmx:confirm`) whenever one exists. The logged-in send page additionally has to solve
  identifying the *same draft* again across a reload -- `get_or_create_draft` deliberately never
  reuses a draft that already has files, nor an empty one older than `DRAFT_REUSE_WINDOW` (1 h; the
  anonymous twin the same) -- see its own docstring: that behaviour predates hydration and stays as
  the default for a fresh, un-parameterized visit, so the moment a draft's first file
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
"Show Environment" masks (`display_env_value`) any variable whose name has a `_`-separated word
like SECRET, PASSWORD, TOKEN or KEY (so `*_ACCESS_KEY_ID` too) or starts with `AWS_`/`GCP_`/`AZURE_`,
except a few harmless names and duration-named ones (`*_TTL_HOURS`, `*_SECONDS`), showing at most
the last 4 characters of a long value; a password inside any URL value (`DATABASE_URL`,
`CACHE_URL`) is masked whatever the name (E2E run #152 found both shown in clear).
`core/templates/admin/login.html` overrides jazzmin's login template to add the hidden `next`
field Django's own has (without it a login from a bare `/admin/login/` went to
`LOGIN_REDIRECT_URL`'s default `/accounts/profile/`, a 404) and keep the email after a failed
login; keep it in step with jazzmin's when upgrading.
The manual credit adjustment tool lives on `CreditTransactionAdmin` (`/admin/billing/credittransaction/adjust/`).
The ledger is read-only in the admin (no add/change/delete, so no `delete_selected`; the same for
`CreditAccountAdmin`), because the balance only changes through `apps.billing.services`. The tool
checks `billing.add_credittransaction` itself with `has_perm` (not `has_add_permission`, which is
False on purpose), the changelist shows its link only to users with it, and
`POST /api/billing/users/{id}/credits` (`AdminAuth` is only `is_staff`) requires the same
permission (403 otherwise). A dedicated `adjust_credits` permission, with a data migration granting
it to whoever holds the add permission, would be cleaner and is a follow-up. `readonly_fields` on
both admins is what shows `id`/`created_at`/`updated_at` on the detail page: those are
non-editable, so without it the page omits them. **Deleting a user still works with a credit
history:** `CreditAccount`/`CreditTransaction` cascade from the user and Django asks each cascaded
row's admin `has_delete_permission`, so `UserAdmin.get_deleted_objects` (`apps/accounts/admin.py`,
`CASCADE_WITH_USER_APP_LABELS`) drops a billing model from `perms_needed` when the caller holds its
`delete_<model>` permission (what the default check asked before): superusers pass, staff still
need it granted. `receipts`, `short_url` and `todo` are in that list too, so their rows go with the
user (Receipts' files go through its own `pre_delete(User)` receiver; a user who had opened Receipts
used to be undeletable, E2E run #152). The file_transfer read-only admins still refuse delete, so a
user with transfers can't be deleted from the admin.
The user admin builds on Django's `UserAdmin` with email-based forms (`apps/accounts/forms/admin.py`),
so passwords are only ever set through hashed password fields; its add page has its own
`admin/accounts/user/add_form.html` (jazzmin's says "enter a username"). Jazzmin's top menu links to
the site and to the admin tools page, the latter only for superusers: jazzmin can only test
permissions, so the link asks for `core.superuser_only`, which exists nowhere and so only a
superuser has (`test_admin_tools.py` fails if it is ever created); the dashboard's "Admin Tools" box is also superuser-only, and a staff user who opens
the page anyway gets a 403 that shows none of the tools. The light/dark toggle
(`core/js/admin_theme_toggle.js`) sets jazzmin 3's `data-bs-theme` and stores it under jazzmin's
own `jazzmin-theme-mode` key, which jazzmin's early script reads before the page paints.
**Admin actions leave History** (`apps.core.admin_log.log_change` / `log_addition`, one `LogEntry`
per object): Django logs only change-form saves, so the moderation actions (take down, block
sender/domain, deactivate owner, release hold, dismiss) and the feedback status actions log what
they did themselves; a takedown's line carries the reason the sender sees, never the internal note.

Singleton settings pages (superusers only, one row, `changelist_view` redirects straight to the
change form): `CoreSettingsAdmin` (`/admin/core/coresettings/`, email verification knobs -- issue
#58), `BillingSettingsAdmin` (`/admin/billing/billingsettings/`, the storage price shared by all
products -- issue #98) and `FileTransferSettingsAdmin`
(`/admin/file_transfer/filetransfersettings/`, spec section 9, plus the issue #59
`auto_hold_report_threshold`). All three share the
`apps.core.admin_singleton.SingletonSettingsAdmin` base (superusers only, never "Add" -- the
changelist creates the row on first visit --, never delete, changelist redirects to the change form; the URL name is derived from the
model), and each loads its row with the model's own `.load()` classmethod
(`apps.core.models.singleton.load_singleton`, which re-reads the row if a concurrent first use
loses the insert race) rather than a fixture. The free-text fields are checked in the model's
`clean()` (which the admin form runs): `FileTransferSettings.anonymous_allowed_expiry_days` must be
whole days >= 1 (it is `int()`-parsed on every anonymous send) and `ReceiptsSettings.allowed_types`
only the supported content types.

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
- **An htmx request without a session** gets `HX-Redirect` to the login page instead of the 302
  `@login_required` answers (`apps.core.middleware.HtmxLoginRedirectMiddleware`, after
  `TimezoneMiddleware`; sync and async): htmx's XHR would follow the 302 and swap the whole login
  page into the target. The `next` it carries is the page htmx was on (`HX-Current-URL`, same-site
  only, else `/`), not the partial endpoint. Only a redirect to `LOGIN_URL` is rewritten, so the
  notification bell's own 204 and every other redirect are untouched.
- Put shared create/update logic *and its validation rules* in the app's `services/`, so the web
  view and the API endpoint both call it and enforce the same rules (e.g.
  `apps.qr_code.services.create_qrcode` / `validate_content`). Forms call the service validators
  to show friendly errors; the API maps the service's `ValidationError` to a 400.

- **Field errors** (issue #219): the message slot keeps its `Label: message` lines, and each field's error
  also shows under the field that failed.
  - *Server side.* `apps.core.field_errors` collects them: `from_form`, `form_lines(form)`,
    `validation_lines(exc, labels, names)` (a non-field line is plain and first, as in `form_lines`) and
    `message_lines(exc, names)`; the last three return an `ErrorLines`, the slot's list of lines that also carries
    `.field_errors`, `{html input name: [messages]}`. `names` maps a service field to the input's name and is only
    needed when they differ (the products' `views/common.py` wrappers say when). `field_errors_of(source)` reads a
    form, an `ErrorLines` or a dict (a string or lazy translation is one message). The errors partial adds
    `{% load field_errors %}{% field_errors source %}` (`core/templatetags/field_errors.py`,
    `core/partials/field_errors.html`), which renders only a hidden `<ul data-field-errors>` with one
    `<li data-field-error="<input name>">` per message (`scope="<id>"` names the element to look in when the data
    isn't inside the form and the request wasn't sent by it; `fallback=True`, for a form with no slot, also lists the
    messages in a `<noscript>`). The products' `form_error_lines` / `validation_lines` (receipts, short_url and todo
    `views/common.py`) are thin wrappers over the core ones, so every `message_response` / `refuse` call carries the
    field data with no change at the call site; a view that builds the response by hand passes the form (QR editor,
    accounts, feedback, file transfer's send forms) or a dict.
  - *Browser side.* `core/js/field_errors.js` (loaded by `base.html`) applies the data after every htmx settle
    and on `DOMContentLoaded`. **Which form a list belongs to is decided per request**: `data-scope`, else the form the
    list sits in, else the form that sent the request whose answer swapped it in (a `WeakMap` xhr -> form filled in
    `htmx:beforeRequest`; `afterSettle` runs for each element a response swapped in, out-of-band ones too, so only
    the lists that response brought are applied). Never a "latest request" global: the Receipts list polls from
    inside its filter form. It puts a `<p id="…-error">` under the control (after its wrapping label, `.relative` box
    or flex/grid row; a `<form>` that is itself a flex row or grid needs its fields wrapped in a block), sets
    `aria-invalid="true"` and puts the message's id first in `aria-describedby`, moves focus to the first invalid
    control unless the person is typing in one of the form's fields (an `autofocus` field the swap itself
    rendered, one the person was not on -- the register form's Name after a refused submit -- is not
    typing: the script remembers the focused element at `htmx:beforeSwap`), and removes a field's message as soon as it is
    edited. A form's messages are cleared when the **form or one of its submit buttons** sends a request, not for a
    poll or a live check from a control inside it. Two names that resolve to one control each keep their message
    (the control's `data-field-error-id` holds every id) and editing it removes both. A widget made of buttons (the
    date picker) marks its wrapper `data-field-wrap`: a click inside it counts as an edit. Every attribute selector
    escapes with `CSS.escape`. A name with no matching control and every non-field error stay in the slot only.
    `site.css` draws the red border from `aria-invalid`. `skeleton.js` ties its in-flight bookkeeping to the xhr's
    own `loadend` for the same reason (a refused request retargeted away from its list must still end the shimmer).
  - *Not covered, on purpose:* the file transfer dashboard row (it re-renders collapsed, its error is the row's
    banner), the promo-code form and the vendor rename dialog (single-field forms with their own message), and the
    plain-POST password pages (`/t/<slug>/`, `/s/<code>/unlock`), which keep their server-rendered message. *Known
    limitation:* a message about two related inputs ("A time needs a date.") stays under the field it names until
    that field is edited; clearing every message of a form on its first edit, or letting a message name related
    inputs, is a product decision left for a follow-up.
  - *Tests:* `apps/core/tests/test_field_errors.py` (helpers, tag, the date picker's `data-field-wrap`), a
    `test_field_errors.py` in `qr_code`, `short_url` and `todo` (real forms), `tests_e2e/test_field_errors.py`
    (browser, including a request racing a refused submit, a poll inside the form and two names for one control) and
    `tests_e2e/test_list_skeleton.py`.

QR code specifics: a tracked code encodes `<BASE_URL>/go/<code>`, so in prod every printed code
reads `https://app.ziptrigo.com/go/...` -- the chosen short-link host (issue #49; there is no
separate short-link setting), and prod's `BASE_URL` must never change once codes are out.
Previews are returned as PNG `data:` URIs and never written to disk. A saved code's image is
rendered on demand from its row (`qr_code:image`, `/qr/image/<id>/`, and `GET /api/qr/{id}/image`
for JWT clients; `QRCodeGenerator.render_png`), because the `image_file` that `create_qrcode` still
writes lives in `MEDIA_ROOT` on the container's disk, which `static()` serves only with
`DEBUG=True` and a redeploy empties: nothing displays it. Short codes
for tracked QR codes are issued by the server (`qr_code:short-code`) and kept in the session until
the save, so users can't choose their own; a code taken in the meantime is replaced.
A scan of an unknown or a *deleted* code gets the same neutral 404 page (`qr_code/unavailable.html`,
standalone rather than the site layout and rendered without the request, so a made-up code costs no
session or user query; a deleted code used to send the public to the login-only dashboard), and the edit and duplicate pages 404 for a
deleted code. The dashboard sorts by Newest (default) or Name (`?sort=`, the search URL-encoded
into the links); it has no row checkboxes. The editor can't set the error-correction level, so its
"too long" message only says to shorten the text (or, for a URL, to turn on tracking).
The image options only the API sets (`size`, `border`, `qr_format`, `error_correction`, the two
colours) are checked by `services.validate_render_options`, called by `create_qrcode` and
`render_preview` before anything is saved or rendered (so a bad one is a 400 and leaves no row):
`size` 1-50 (segno's pixels per module; the image is built in memory), `border` 0-20, the model's
format and error-correction choices, and colours segno itself accepts for that format (a dry
render of a tiny code per colour; `transparent` is allowed for either colour except a PDF's
foreground, and not for both at once, which segno 1.6.6 cannot draw as a PNG). It ends with one dry render of
the content that will really be encoded (the `/go/<code>` link under URL shortening, checked at the
longest short code) with both colours together, scale 1 and no border, so a payload too big for
a QR code (`MAX_CONTENT_LENGTH` counts characters, capacity is bytes: 1,000 emoji fit no level) is
a 400 before anything is saved; the web editor's form calls `check_content_fits` for the same
reason and its views turn a late `ValidationError` into a 422.

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

**Status changes are conditional writes** (audit #16). `end_transfer` claims the move with
`UPDATE ... WHERE status IN from_statuses` (default: any non-terminal status; the grace-period job
passes `SUSPENDED`, the expiry job `ACTIVE`/`DISABLED`, delete-now the actionable ones, the
anonymous activation failure `PENDING_CONFIRMATION`) and returns whether it won; only the winner
runs `before_delete`, deletes S3 objects and notifies, so a stale instance never overwrites a
newer state (a second call on an ended transfer is a no-op; a failed delete is retried by the
deferred-deletion sweep). `take_down_transfer` and `abort_draft` return that bool too,
`delete_transfer_now` raises "already ended" on a loss, and `disable_transfer` writes only
`WHERE status = ACTIVE`. `abort_draft` claims `DRAFT`/`PENDING_CONFIRMATION` -> `DELETED` first, in
one transaction with its S3 work (a storage error rolls the claim back so `cleanup_drafts`
retries; `finalize_send`'s row lock serialises with it). That rollback cannot un-delete S3
objects already removed, and the claim holds the row lock across the S3 calls (both accepted, see
`abort_draft`'s docstring).

Status is not the only precondition a caller acted on from a stale read, so `end_transfer` also
takes `only_if`, a `Q` ANDed into the claim's `WHERE`: delete-now passes
`held_for_review_at__isnull=True` (a hold applied after its check still freezes the transfer), the
expiry job `expires_at__lte=now`, the grace-period job `suspended_at__lte=cutoff` and the
max-downloads fallback `max_downloads__lte=<count it saw>`; an owner who extended, raised or
re-enabled in the meantime wins. A service that loses a claim (`disable_transfer`,
`delete_transfer_now`) refreshes the instance before raising, so the 422 row shows the state that
won; the API's draft delete returns 204 when the draft was already `DELETED` and only falls through
to delete-now when it became an actionable transfer. The admin takedown and block-sender messages
count only transfers this call took down and say how many were skipped because they had already
ended.

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

### Promo codes

`apps.billing` (issue #70; `models/promo.py`, `services/promo.py`, `views/promo.py`,
`forms/redeem.py`, `admin_promo.py`, `jobs.py`, migration `0003_promo_codes`). A **promo code grants
credits** on redemption (`PromoCode.effect`, only `credits` so far: there is no purchase flow yet, #54,
so a discount has nothing to attach to; the field is the hook for percent-off / bonus-on-purchase
effects later, and "apply automatically at checkout" waits for #54 too). The design decisions are on
the issue (comment of 2026-10-31); the short version:

- **`PromoCode`**: `code` (unique, stored upper-case, letters/digits/hyphens 4-32,
  `models.promo.CODE_PATTERN`; matched ignoring case through `services.promo.normalize_code`; blank
  in the admin generates one from an alphabet without `0 O 1 I`), `credits` (1 to 1,000,000),
  `campaign` (free text, filterable, shown in the ledger line), `notes`, `active`, optional
  `starts_at`/`ends_at`, optional `max_redemptions` across all users, `claimed_count` (granted
  redemptions plus pending signup reservations, bumped with one conditional `UPDATE` so racing
  redemptions can't overshoot the cap), optional `new_accounts_within_days` (the account's
  `created_at` must be within it), `allowed_emails` (lower-cased list; empty = anyone),
  `created_by`. **`PromoRedemption`** is one user's use of one code: `status` `pending` (reserved
  at signup) / `granted` (`transaction` is the ledger row) / `released`; `source` `form`/`signup`;
  `credits` as granted. A partial unique constraint on `(code, user)` for pending+granted rows is
  the **once per user** rule (a user may redeem any number of *different* codes; a released
  reservation lets them redeem that code on the form later). `code` is `PROTECT`: a code with
  redemptions is deactivated, never deleted (the admin refuses it first). The ledger row is
  `CreditTransactionType.PROMO`, `source='billing'`, description `Promo code LAUNCH2026 (campaign)`,
  which is how the credits history names the code.
- **`services.promo.redeem(user, raw, source=)`**: one transaction with the code row locked:
  `find_code` (unknown, malformed or inactive -> `InvalidPromoCode`; `PromoCodeNotStarted`;
  `PromoCodeExpired`), `check_eligibility` (in this order: the email binding, where an outsider gets
  `InvalidPromoCode` so a guesser never learns whose a code is; `PromoCodeAlreadyRedeemed`;
  `PromoCodeExhausted`; `PromoCodeNewAccountsOnly`; `EmailNotConfirmed`, always required: no farming
  codes with throwaway signups), the conditional claim, the row (an `IntegrityError` is
  `AlreadyRedeemed`), `add_credits`. Refusals are `PromoCodeError` subclasses whose `message` is
  what the user sees, **specific where harmless**. An inactive or deleted user is `InvalidPromoCode`.
- **Signup** (the register page's optional field, through `apps.accounts.signup_codes`, see
  "Dependency rules"): `validate_for_signup(raw, email)` judges the code, its dates and the email
  binding (no account yet, so not the new-accounts window or the confirmed email) before the captcha,
  rate limited per IP; after `create_account` the handler's `attach` calls `reserve_for_signup`,
  which claims a use and writes a `pending` row (a cap reached in between is logged, never raised).
  **A reservation is honoured as judged at signup**: `grant_reservations(user)`, run by the
  `email_confirmed` receiver in `BillingConfig.ready()`, adds the credits without re-checking the
  code's validity, and leaves an in-app notification ("Your promo code added N credits", key
  `promo_granted:<code id>`, since the user isn't on a page that could show a flash).
  `release_stale_reservations()` (the daily `billing.release_promo_reservations` job) gives back the
  uses of reservations whose account is still unconfirmed `RESERVATION_MAX_AGE` (30 days) later. For
  an address that already has an account the register page's answer is unchanged whatever the code
  (#125) and nothing is reserved.
- **Web**: `billing/partials/redeem_form.html` ("Have a promo code?") is included by the account
  page's Credits section, the credits history page and the redeem link page; it needs no form object
  (`promo_code`, `promo_next`, `promo_error` are optional context). `POST /billing/redeem/`
  (`billing:redeem`, `views/promo.py`, the 422 convention: success is a flash "Added N credits with
  promo code X." and `hx_redirect` to the same-site `next`, a refusal re-renders the partial with the
  message; without htmx a refusal is an error flash). **Rate limits** `PROMO_REDEEM_USER` (10/hour)
  and `PROMO_REDEEM_IP` (30/hour) are charged before the lookup, so codes can't be guessed; the
  register page's code check shares the IP rule. The **redeem link** `GET /billing/redeem/<code>/`
  (`billing:redeem-code`) only shows the form prefilled; without a session it redirects to the login
  page with `next` and `promo=<code>`, the login page's "Create one" link forwards `?promo=` to the
  register page, which prefills its field from it (`signup_codes.QUERY_PARAM`).
- **Admin** (`admin_promo.py`, icons `fa-ticket-alt` / `fa-tags`): `PromoCodeAdmin` (list: usage
  "granted (+pending) / max", credits granted, last redeemed; `StateFilter` live / scheduled /
  expired / fully redeemed / inactive; search over code, campaign, notes and emails; the add form
  generates a blank code and takes `allowed_emails` as a textarea; **once claimed the code, effect and
  credits are read-only**, dates, cap, eligibility and notes stay editable; **delete only while never
  redeemed**; actions **Duplicate** (a copy with a fresh code) and **Export CSV**; the **Create many**
  tool at `bulk/` makes up to `BULK_MAX` (1,000) single-use codes with an optional prefix
  (`FAIR-7K3M9QX2WD`) under one required campaign and redirects to the list filtered by it with a
  "Download them as CSV" link, `export/?campaign=`), a read-only `PromoRedemptionInline` on the code
  page, and the read-only `PromoRedemptionAdmin` (search by user email or code, filter by status,
  source, campaign). `billing` is in `CASCADE_WITH_USER_APP_LABELS`, so deleting a user takes their
  redemptions (the code's use stays spent).
- **Not in v1** (deferred on the issue): discount effects and automatic application (#54), credit
  expiry (welcome credits, #160, are built apart from promo codes: see "Welcome credits"), revoking
  a redemption, email-domain eligibility, a per-user count, a credits budget per code, a Campaign
  model, a JWT endpoint, an email on redemption.
- **Tests**: `apps/billing/tests/test_promo_services.py`, `test_promo_views.py`, `test_promo_admin.py`,
  `apps/accounts/tests/test_register_promo.py`.

### Welcome credits

Issue #160 (`billing/models/welcome.py`, `billing/services/welcome.py`, `accounts/models/known_email.py`,
`accounts/services/known_emails.py`; migrations `accounts.0006_known_emails`,
`billing.0004_welcome_credits`; `docs/welcome-credits.md` has the admin-facing write-up). A
genuinely new account gets `BillingSettings.welcome_credits` credits (default 1,000, `db_default`
too; **0 turns it off**) once its email is confirmed, with no code to type. It is its own mechanism,
not a `PromoCode`: a promo code needs someone to type it and says nothing about whether the address
was ever used.

- **The rule.** A `WelcomeCredit` row (`billing`, one per user: a `OneToOneField`, the once-per-user
  guard) is opened `pending` by the `accounts.account_created` receiver
  (`welcome.on_account_created`, in the creating transaction and its own savepoint) for every new
  account **except staff and superusers, and only while the setting is above 0**. Accounts that
  existed before, and accounts created while it was 0, have no row and never get anything (turning
  it on later is not retroactive). `welcome.settle(user_id)` -- the `settle_welcome_credit` task,
  queued on commit by `on_account_created` for an account created already confirmed and by
  `on_email_confirmed` (the `email_confirmed` receiver) for the emailed link and a reset -- does
  nothing unless the row is `pending`, the user is confirmed and active and the setting is still
  above 0 (set to 0 meanwhile, the row waits) and is not staff (made staff later, e.g. by the
  admin's add form, it waits forever). Then it asks `known_emails.remember(user)`: true only
  for the **first account to have confirmed the address, or any variant of it, ever**. True: one
  transaction with `add_credits(type=WELCOME, description='Welcome credits', source='billing')`
  (`CreditTransactionType.WELCOME`), the row `granted` (`credits`, `transaction`) and an in-app
  notification (key `welcome_credits`, its own savepoint). False: the row is `refused` with reason
  `address_seen`, and **final** (moving to a fresh address afterwards does not reopen it), and the
  user is emailed once.
- **Remembered addresses** (`accounts.KnownEmail`: `fingerprint` the primary key, `first_user_id` a
  plain UUID, `created_at`). The fingerprint is `HMAC-SHA256(SECRET_KEY, 'known-email:' +
  canonical)`, so the table keeps no address; **rows are never deleted with the user**, which is the
  point: closing an account, or moving it to another address, does not make the old address new.
  `known_emails.canonical_email`: lower-case, everything from the first `+` of the local part
  dropped for **every** domain, and for `gmail.com`/`googlemail.com` only also the dots, with
  `googlemail.com` read as `gmail.com`. It only ever *refuses* a grant, so it errs on being generous.
  **It is written when an address is confirmed, not at signup** (a deliberate narrowing of the
  issue's "at signup"): a signup proves nothing about the address, and if registering claimed it,
  anyone could register a victim's address, or a `+tag` of it, and have the owner refused. The places
  an address becomes confirmed call `remember`: `email_confirmation.send_email_confirmed` (the link,
  a reset, `inv users confirm`, all of which also use it) and the `post_save` receiver for an
  account created already confirmed, and the user admin when staff tick `email_confirmed` (it
  goes through `send_email_confirmed`). Both callers use `remember_quietly` (own savepoint, errors
  logged): remembering an address must never fail a confirmation or a sign-up. `remember` is
  first-come and idempotent, the primary key decides a race. `settle` calls it again itself, so
  an account that has a welcome row is covered even if a path forgot; one without a row is not. Two unconfirmed `a@` and `a+shop@` signups: whoever confirms first is the holder and the
  other is refused. The migration backfills confirmed accounts, oldest first; an address an existing
  account has already moved away from cannot be recovered. **Rotating `SECRET_KEY` silently makes
  every remembered address new again** (nothing else here is keyed so persistently): don't, or
  re-fingerprint first.
- **The refusal email** (`welcome.send_welcome_refused_email`, a `default` queue task through
  `enqueue_after_commit`; template `billing/emails/welcome_refused.html`, autoescaped, the greeting
  carries the user's free-text name; a fixed subject; nothing about the other account): sent to the
  confirmed address of an active user whose row is `refused`, claimed by a conditional `UPDATE` of
  `refusal_emailed_at`, so once per account however many times it runs. A switched-off setting sends
  nothing (there is no row to refuse).
- **Safety net.** The daily `billing.settle_welcome_credits` job (`welcome.settle_pending`) settles
  `pending` rows of confirmed, active, non-staff users and sends the email of a `refused` row whose
  task was lost (or whose send failed: when every backend fails the claim on `refusal_emailed_at` is
  released); both are idempotent, paged oldest first by keyset so rows it can never settle cannot
  starve the rest, and it does nothing while the setting is 0.
- **Admin.** The setting is on `BillingSettings`; `WelcomeCreditAdmin` is read-only (status, reason,
  credits, dates; filter by status and reason). `KnownEmail` has no admin on purpose (hashes).
- **Tests.** `apps/billing/tests/test_welcome_credits.py` (marked `welcome_credits`: the root
  `conftest.py` has an autouse fixture that patches `welcome.configured_credits` to 0 for every other
  test, so no test creates rows or credits by accident) and `apps/accounts/tests/test_known_emails.py`.

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
  on commit; `delete_prefix` raises on a per-key S3 error and the user row is gone, so the task
  queues itself again (`attempt` + 1, `run_after` a backoff when the backend can defer, else at
  once) up to `len(USER_STORAGE_RETRY_DELAYS)` times, then logs the prefix at error level and gives
  up. Tests swap in `FakeReceiptsStorage` (autouse in `apps/receipts/tests/conftest.py`).
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
  `pillow-heif` with the JPEG kept as the preview, downscale, an explicit `MAX_IMAGE_PIXELS` check
  (Pillow's process-wide limit is left alone) and an image shrunk to `MAX_IMAGE_SIDE_PX` *before*
  the EXIF rotation and the RGBA flattening copy it (a palette or 1-bit one is cut down with NEAREST
  to twice that first, before it is expanded to RGB), PDF text via `pypdf` under `PDF_TEXT_LIMITS`
  (2 MB decompressed **per stream** instead of its 75 MB default, `pypdf.apply_configuration`, per
  call; a PDF over it is refused, not rendered, since pdfium decodes the same stream with no limit of
  its own) or rendered pages via `pypdfium2` -- each page is sized with `get_size()` first and
  rendered at a scale that keeps its longer side within `MAX_IMAGE_SIDE_PX` (a page over 14,400 pt on
  a side is refused), the PIL image going straight to the JPEG encoder -- HTML -> text with the
  stdlib parser) **runs in a child process under a resource budget**, because a per-stream
  cap bounds nothing else: a 5 KB PDF whose page draws one 1.9 MB Form XObject 40 times needs 1.4 GB
  in pdfium (it decodes the form again for every use; 40 different forms do the same) and 40 big
  streams cost pypdf minutes of CPU. `prepare_input` starts `python -I documents.py` (the module run
  as a script: importing it as `apps.receipts.services.documents` would import the whole
  Django-dependent package, so it keeps no relative imports) with bytes on stdin and JSON on stdout,
  never touching the database, with an environment cut to `PATH`/locale (no keys), in its own
  session (`start_new_session`, so a Ctrl-C on a local worker doesn't reach it), with stderr dropped
  (the child also silences `logging` and `warnings`; nothing it writes is kept or logged -- only
  its exit status or signal name), under three limits: `RLIMIT_AS` = `RECEIPTS_DOCUMENT_MEMORY_MB`
  (default 1024; address space, not resident memory. Measured on Linux: ordinary receipts need
  96-128 MB, the worst legitimate input, a 48-megapixel HEIC or a 50-megapixel PNG, passes at 512
  MB, so the default leaves 2x; **Linux only**: macOS ignores `RLIMIT_AS`), `RLIMIT_CPU` =
  `RECEIPTS_DOCUMENT_CPU_SECONDS` (default 25; ordinary receipts measured 0.1-0.7 CPU s in the
  image, the worst legitimate ones, a 48 MP noise PNG or a 12 MP HEIC, 1.7-2.6 s; SIGXCPU, with
  `RLIMIT_CORE` 0) and a wall-clock `RECEIPTS_DOCUMENT_TIMEOUT_SECONDS` (default 60, 2-3x the CPU
  limit on purpose: CPU time doesn't grow with host load, so it is what refuses a CPU-heavy file,
  and the wall clock only catches a hang). `apps.receipts.checks` flags bad values, but **only a
  value that cannot work at all is an Error** (`receipts.E001` memory <= 0, `E002` timeout <= 0):
  `migrate` runs the checks and the deployed entrypoint has `set -e`, so an Error stops the whole
  site from starting. The rest are Warnings: `W003` memory under 512 MB (the measured value that
  passes a 48 MP photo), `W004` CPU under 1 s, `W005` timeout under 1.5x the CPU limit (or not above
  it). `W006` (`check_document_child`) runs the real child on a tiny text payload and warns if it
  can't read it (a missing capability for the user switch, an unreadable venv, a broken import); it
  is skipped with the sandbox off and under `DEBUG`, since a child start costs ~0.3 s. Before it
  sets its limits the child writes `1000` to `/proc/self/oom_score_adj`, so if the container's
  cgroup limit is hit first (RLIMIT_AS counts address space, not RSS) the kernel kills the child,
  not the worker (whose death restarts the container). **Container headroom: the worker plus
  `RECEIPTS_DOCUMENT_MEMORY_MB`** (one child at a time per `receipts` worker). A root worker starts
  the child as `RECEIPTS_DOCUMENT_CHILD_USER` (default `nobody`, no supplementary groups; empty
  disables; skipped when not root; a user that doesn't exist logs a warning once and runs it as
  root; a `PermissionError` on the switch, e.g. a container with `cap_drop` SETUID/SETGID, logs a
  warning and retries once as the worker's own user), so it can't read a 0600 env file; verified in
  the image. **The deployed `.env` is bind-mounted from the host, so that depends on the host
  file's mode: `chmod 600` it on the VPS** (`scp` without `-p` keeps an existing file's mode, and
  Docker Desktop ignores bind-mount permissions, so only Linux proves it). The Dockerfile sets
  `UV_COMPILE_BYTECODE=1` in the builder so the unprivileged child doesn't recompile pypdf and
  friends on every start. One child per receipt (~0.1-0.5 s). A pdfium failure (`_RenderFailedError`: pdfium reports a failed allocation as its
  own error, not `MemoryError`) after the child had mapped over 85% of its budget (`VmPeak`) is
  reported as over the budget; any other unreadable error whose `__cause__`/`__context__` chain
  holds a `MemoryError` is too.
  **Two kinds of failure.** Only outcomes that mean "this file is over the budget" raise
  `DocumentTooComplexError` (a subclass of `UnreadableDocumentError`, also raised for the per-stream
  cap, an image header over `MAX_IMAGE_PIXELS` and an absurd page size): the child ended with
  SIGABRT (a failed allocation under `RLIMIT_AS`), SIGSEGV, SIGBUS, SIGILL, SIGTRAP (native crashes;
  pdfium's out-of-memory crash on x86-64 is `int3`/`ud2`) or SIGXCPU, **exit status
  127 after the child announced it had started** (it writes `#started` to stdout once its limits are
  set; on x86-64 glibc's dynamic loader can end a child that used up its address space with
  `_exit(127)`, "cannot allocate memory for thread-local data", instead of SIGABRT -- found by CI;
  127 *without* the marker is a broken install and is retried), raised `MemoryError`, or replied
  `too_complex`. **SIGKILL is not one of them**: with the limits a file
  over its budget fails an allocation instead, so SIGKILL is the kernel's OOM killer, i.e. memory
  pressure on the container or host (the child is its first victim), and is retried.
  `processing._run` logs a warning with the receipt id (never content) and fails it as
  **`too_large`** (reused rather than a new reason, so no migration; its help text says "too large
  or too complex", and, like any `too_large`, it can't be re-run), refunded like every file that
  could not be read. Everything that says nothing about the file -- an ordinary non-zero exit, no or
  a malformed or undecodable reply (the reply is untrusted: `ok: null`, bad base64),
  SIGINT/SIGTERM/SIGHUP/SIGKILL, the wall-clock timeout, the child failing to start or to import a
  library (reply `failed`) -- raises `DocumentChildError`, deliberately *not* an
  `UnreadableDocumentError`: `_run` logs an **error** and treats it as a transient failure
  (`_transient_failure`, backoff, `max_ai_attempts`), so a hostile file still ends after a few
  budgets while a broken image or a mistaken setting doesn't permanently fail every receipt.
  Both the child and the in-process path go through `documents._prepare_guarded`, which maps
  unexpected exceptions (-> unreadable) and `MemoryError` (-> too complex) in one place, so unit
  tests with the sandbox off exercise the production branches. **This is a resource budget, not an
  isolation boundary**: the child has no keys in its environment and (as root's child) no root, but
  can use the network and read what its user can read, and a memory-corruption bug in pdfium,
  libheif or libde265 isn't contained by it; keep secret files 0600. `RECEIPTS_DOCUMENT_SANDBOX`
  (default on; off under pytest, which prepares in-process and monkeypatches inside; the `tests_e2e`
  server runs it for real) switches the child off. Preparation
  and the AI call (`services.ai`) happen outside any transaction; the result is applied under a row lock only if
  the receipt is still `processing` for the same `attempt_id`. A HEIC preview is
  written before its conditional `UPDATE`; if that matches nothing and no row references the key
  (the receipt was purged, or its user deleted, meanwhile) the object is deleted again. The AI client is
  `ai.get_client()` (tests: autouse `FakeAIClient`; the suite never touches the network); its
  `openai.OpenAI` comes from `apps.core.services.ai.build_openai_client` (shared with To-do, issue
  #183) and its reply is validated with pydantic (`ai.parse_response` first unwraps, through
  `apps.core.services.ai.unwrap_json`, one code fence, one-item list or
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
    other order would deadlock on Postgres). Saving a receipt that moves it to a vendor vets that
    vendor (an `UPDATE`) under the receipt's lock, so `update_receipt` first takes the target
    vendor's `FOR NO KEY UPDATE` (`lock_vendor_for_assignment`, before locking the receipt): vendor
    then receipt on every path. SQLite can't show a deadlock, so `TestLockOrder` asserts the order
    `select_for_update` is called in.
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
  (`frozen_at`), emails the owner and notifies them in-app (see "Notifications"): nothing is deleted, but previews, downloads, new uploads,
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
  `AccountFrozenError`/`InsufficientCreditsError` -> 402): `update_receipt` (partial; an amount goes
  through `ai.parse_amount`, also the AI reply's, which rounds to cents half up: a comma before
  exactly three digits is always thousands (`1,234` is 1234; the first group can't start with 0), a
  lone `,` + 1-2 digits is a decimal comma (`12,50`), any other comma (`0,500`) is refused, and so
  are space/dot thousands separators (`1 234,50`, `1.234,56`); the search box reads a term the same
  way; records the
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
  `receipts.purge_trash` (daily; deletes S3 objects and rows past `trash_days`, and the stripped
  `.eml` with the email's last receipt. `lifecycle.purge_receipt` decides from the row as it is now,
  under its lock and before any S3 delete: still trashed, not `processing` and, for the job, trashed before the
  cutoff -- else it returns `False` and leaves a receipt restored meanwhile alone --, and the
  `.eml` under a row lock on the `InboundEmail` too), `receipts.retry_due` (5 min; due retries, `processing`
  stuck over 30 min counted as a failed try, `received` rows nobody queued for 15 min),
  `receipts.cleanup_uploads` (hourly), `receipts.poll_inbound` (every minute, 2 min lease; see
  "Email intake" below), `receipts.purge_inbound_messages` (daily; deletes `done` `InboundMessage`
  rows that went `done` more than 30 days ago and have no `InboundEmail`, i.e. mail for
  unknown/retired codes; `pending`/`failed` and ones with emails stay; see "Email intake"),
  `receipts.link_vendors` (hourly; links receipts with a vendor text and no
  vendor, see "Vendors"). Idempotent, per-row `try/except` + `logger.exception`.
- **Queue.** `TASKS['default']['QUEUES']` is `['default', 'receipts']`; every receipts task is
  `@task(queue_name='receipts')`, served by its own `db_worker --queue-name receipts` process (see
  "Queue and scheduler" and "Docker"). Tests: `ImmediateBackend` runs a task inline at
  `.enqueue()` and swallows its exceptions, so assert on database state and use
  `django_capture_on_commit_callbacks(execute=True)` around anything queued on commit; call a task
  as `<task>.call(...)` (or the underlying service function) in a test, not `<task>(...)`.
- **Email intake** (`services.inbound` for the flow and the decisions, `services.mime` for reading a
  raw message -- now a thin wrapper over `apps.core.services.mail.mime`, see "Mail parsing"; issue
  #98 phase 2). To-do's intake (#186) is this one reproduced for `todo/inbound/`. SES drops each accepted message in `receipts/inbound/`; nothing
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
  - **Old `done` messages are purged** (`inbound.purge_done_messages`, daily job
    `receipts.purge_inbound_messages`, audit #18): a `done` row with no `InboundEmail` (mail for an
    unknown or retired code) is deleted `DONE_MESSAGE_RETENTION` (30 days) after `processed_at`
    (`created_at` for a row without one; `created_at` is not used because an admin retry leaves it
    alone while the object copied back gets a fresh lifecycle clock), in batches of `PURGE_BATCH`.
    `poll` registers any key under `receipts/inbound/` it has no row for, so a row may only go once
    its object is gone: each batch's objects (`s3_key`, and `failed_key` if set) are deleted first
    with `S3Storage.delete_objects` (batched `DeleteObjects`; a missing key is a no-op, a per-key
    error raises), then the rows; a storage error propagates, leaving the rows for the next run, and
    the runner records it as `last_error`. As a second guard `DONE_MESSAGE_RETENTION` must stay well
    above the 14-day expiry the `infra` repo puts on `receipts/inbound/`, so an object whose delete
    keeps failing is gone before its row is. `pending`/`failed` rows and ones with emails stay.
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
    `orders+caf_=user=gmail.com@vendor.com` becomes `orders@vendor.com`; it takes a bare addr-spec
    and removes angle brackets only around the *whole* value, so a quoted local part holding
    `<listed@vendor.com>` is not "listed" while authentication vets the domain after the last
    `@`), **and** SES must have
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
    only if `mime.is_forwarding_link` accepts it (`https` and the *whole* authority is
    `mail-settings.google.com`: no userinfo, port, backslash or control character, since browsers
    read `\` as `/`; a candidate that doesn't parse, like `https://[x`, is skipped); without that
    authentication the email is an ordinary one (the allowlist applies and nothing is extracted).
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
    a `date_range` -- older links, and the canonical URL of a custom range -- mean Custom, while
    "Any date" sends an explicit `date_range=any` that wins over them: picking it fires the list
    request before Alpine disables the date inputs, so the old dates still arrive), sortable
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
    core's shared `initDropZone`, `core/js/drop_zone.js`, with the empty-drop alert turned off), "Choose files" and "Take photo" (`accept="image/*"
    capture="environment"`). Per file: refuse an unsupported type/size/empty file in the row; the
    browser computes SHA-256 (base64); `POST upload/start/` (session + CSRF, JSON in/out,
    `RECEIPTS_UPLOAD_USER`; a `size` that isn't a whole number of bytes, `1e400` included, is a 422 -- every JSON size,
    timestamp and part number in the web upload views, file transfer's too, goes through
    `apps.core.json_numbers.whole_number`, never a bare `int()`; 422 rejected, 402 frozen, 429 waits for `Retry-After` up to 3 times) ->
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
    to `mail-settings.google.com`, re-checked with `is_forwarding_link` before it becomes an
    `href`; none at all when the code is empty and the link is withheld), accepted senders
    (Anyone / Only these, `forms.InboxSettingsForm` over `addresses.parse_allowlist`) with the
    default currency in one htmx form, the category editor (add, rename inline, move up/down, delete
    in a modal; each action re-renders `#categories`; deleting names how many receipts become
    uncategorized) and the 20 most recent inbound emails with their outcome and `note`.
  - **UI parts**: `{% status_pill receipt.status %}` (`receipts_ui`; colours in the design doc),
    `core/partials/confirm_modal.html` driven by the `confirmModal` Alpine component and the `copyText`
    component, both shared with Short URLs and so in `apps/core/static/core/js/components.js`
    (loaded *deferred* by `core/base.html`, ahead of Alpine; the modal also posts the trigger's
    `data-cost` as `confirm_cost`, empty when there is none). Receipts' own
    `static/receipts/js/common.js` keeps `fragmentModal` and is loaded as a *classic* script (not
    `defer`) so its `alpine:init` listener exists before Alpine starts. Multi-line template comments
    use `{% comment %}`: `{# #}` is single-line only.
  - **Tests**: `tests/test_views_*.py`, `test_ui.py` (every page, 404s, 422 paths, rate limits via the
    `settings` fixture, frozen account, CSV, the body headers, the `sandbox` attribute, the upload flow
    against the fake storage and AI). One Playwright test file (`tests_e2e/test_receipts.py`: upload ->
    processed -> edit -> trash -> restore, the filters and URL, the sandboxed body) runs against
    `tests_e2e/receipts_e2e_settings.py`: the real site with an `INSTALLED_APPS` entry that swaps
    the receipts storage and AI for the unit tests' fakes, an extra URL (`/__e2e__/s3/<key>`) acting
    as the bucket the fake presigned URLs point at (it checks `x-amz-checksum-sha256` like S3),
    inline tasks, and its own `db.e2e.sqlite3` -- no test switch in production code. The same
    server also swaps in an in-memory file transfer bucket (`tests_e2e/site_fakes.py`: `FakeS3Storage`
    with part and download URLs pointing back at `/__e2e__/ft-s3/<key>`, so a real browser send and
    download work), for `tests_e2e/test_drop_zone.py` (a file and a folder dropped on the shared drop
    zone of the Receipts upload and the file transfer send pages), keeps every email the site sends
    at `/__e2e__/mail/`, answers CORS on both fake buckets, and opens SQLite with
    `transaction_mode: IMMEDIATE` (threaded runserver, parallel uploads). Run it with `inv test e2e`
    (needs `.env.local`). The AI-agent plans in `tests_e2e/ai/` use the same settings through
    `tests_e2e/ai/serve.py` (own `db.e2e-ai*.sqlite3`, seeded accounts, `--port`/`--host
    <name>.localhost` to run several at once).
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
  list). The JWT CLIs share their plumbing in `admin/utils.py` (audit follow-up): the token
  is **per CLI and environment**, `~/.<cli>_token.<env>` (`token_file`, so the prod JWT is never
  sent to the local server; the single `~/.<cli>_token` of earlier versions is ignored -- log in
  again, then delete it), written by `write_private_file` (mode 0600 set on the open descriptor so
  a pre-existing looser file is tightened, `O_NOFOLLOW`); `get_headers(cli)`,
  `password_from_prompt_or_env` (`QRCODE_PASSWORD` / `FILETRANSFER_PASSWORD` /
  `RECEIPTS_PASSWORD` / `SHORTURL_PASSWORD` / `TODO_PASSWORD`, and `SHORTURL_LINK_PASSWORD` for the password of a
  short link itself, else a hidden prompt: no CLI takes a password on the command line any
  more) and `select_environment` (loads the env file and remembers which, for `token_file`; prints
  `Site: <url> (<env>)` to stderr and warns when `API_BASE_URL` differs from the file's `BASE_URL`,
  since the token is chosen by environment but sent wherever the URL points). Every request goes through `_request`: default timeout; a 429 is retried
  after its `Retry-After` only when that is <= 60 s (`MAX_RETRY_AFTER_SECONDS`) and the call's total
  wait stays within `MAX_TOTAL_RETRY_WAIT_SECONDS` -- the hourly rules (export, regenerate, re-run,
  login) are not slept on, the error says "rate limited, try again in N minutes" instead; and any
  `requests` exception exits 1 with a message (or raises `NetworkError` for the upload loop, which
  carries on with the next file). Errors print the API's `detail`, a ninja 422 list flattened to
  `field: message`. The site URL (`utils.site_url` /
  `api_url`) is read when a command runs, after `select_environment` has loaded the env file:
  `API_BASE_URL`, else the file's `BASE_URL`, else `http://localhost:8000` (`qrcode` and
  `filetransfer` used to read `API_BASE_URL` at import time, before that file was loaded, so
  `<cli> prod ...` talked to localhost). Unit tests: `admin/tests/` (`test_receipts.py`,
  `test_cli_utils.py`, ...), HTTP mocked, no Django.
- **Admin.** `ReceiptsSettingsAdmin` (singleton, superusers), read-only `ReceiptAdmin`
  (status/reason/source filters; `VendorAdmin`, see "Vendors"; no add/change/delete -- deleting a row would orphan its S3 objects
  and skew `stored_bytes`), `InboundMessageAdmin` (status, attempts, next attempt, last error; a "retry" action
  needing `change_inboundmessage`, which moves the parked object back to `receipts/inbound/`,
  resets `failed` -> `pending` and queues `ingest_message` again through
  `services.inbound.reset_message_for_retry`), `InboundEmailAdmin`, `ReceiptsAccountAdmin` (codes inline, frozen state, stored bytes).
- **Settings/env**: `OPENAI_API_KEY`, `OPENAI_BASE_URL` (optional), `OPENAI_RECEIPTS_MODEL`,
  `RECEIPTS_AI_TIMEOUT_SECONDS`/`_MAX_RETRIES` (explicit timeout, bounded SDK retries),
  `RECEIPTS_DOCUMENT_SANDBOX`/`_MEMORY_MB`/`_CPU_SECONDS`/`_TIMEOUT_SECONDS`/`_CHILD_USER` (the document-preparation budget),
  `RECEIPTS_S3_BUCKET`/`_REGION`/`_ENDPOINT_URL`, `RECEIPTS_AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`,
  `RECEIPTS_INBOUND_ADDRESS`; the five required ones are checked in prod (see "Settings and
  environment"), `apps.receipts.checks` warns about a *partial* local configuration. To-do's AI
  date parsing (#183) reuses `OPENAI_API_KEY` and `OPENAI_BASE_URL` and adds `OPENAI_TODO_MODEL`
  (falls back to `OPENAI_RECEIPTS_MODEL`) and `TODO_AI_TIMEOUT_SECONDS` (6) /
  `TODO_AI_MAX_RETRIES` (0); none is required in prod (see "AI date parsing" under "To-do").

### Short URLs

`apps.short_url` (issue #162; app label `short_url`) is a URL shortener: `<BASE_URL>/s/<code>` redirects
to a destination the owner can edit. A product (same layer as `qr_code`/`file_transfer`/`receipts`),
**logged-in owners only** (no anonymous shortening). Creating and following a link is free; each
extra costs credits once per link. Built in four phases (links and billing; password, preview and
analytics; abuse tooling; API, CLI, docs and E2E). The QR app's `/go/<code>` links are untouched, and
the two products share nothing: a link's QR image comes from the small `core` helper
`apps.core.services.qr_image.render_png` (segno), not from `qr_code`.
Layout like `receipts`: `models/`, `services/`, `views/`, `forms/`, `api/`, `schemas/`, `jobs.py`,
`admin.py`, `checks.py`.

- **Models** (`models/`). `ShortLink` (UUID pk, `owner` FK that cascades with the user, `code` as
  displayed, `code_key` its lower-case form, **unique**, what every lookup uses, `is_alias`,
  `destination` up to 2,048, `title`, `tags` JSON list, `status` `active`/`disabled`/`taken_down`/
  `deleted`, `expires_at`, `max_clicks`, `password_hash`, `filter_bots` (default true), `click_count`,
  `bot_click_count`, `last_clicked_at`, the four `*_paid_at` columns, `held_for_review_at`, the
  takedown columns, `deleted_at`). The `status` is what the owner or staff did; whether the link
  works *right now* is `services.is_available` (status `active`, no hold, not expired, not past its
  cap, destination not blocked) and what the owner sees is `services.display_state` (`active`,
  `disabled`, `expired`, `capped`, `held`, `blocked`, `taken_down`). `ShortUrlSettings` is a
  `SingletonSettingsAdmin` singleton (`/admin/short_url/shorturlsettings/`): the four prices
  (`custom_alias_price`, `password_price`, `analytics_price`, `extra_link_price`, default 1 credit
  each; 0 makes an extra free), `free_active_links` (25), `max_active_links` (500, the hard cap),
  `click_event_retention_days` (90) and `auto_hold_report_threshold` (0 = off). Click analytics:
  `ClickEvent`, `DailyLinkStat`, `DailyLinkDimension`; abuse: `LinkReport`, `BlockedDomain`;
  `RetiredCode`.
- **Codes** (`services.codes`). A random code is 7 base62 characters (`secrets`); an alias is 3-32 of
  `[A-Za-z0-9_-]`. Both live in one namespace compared **case-insensitively** (`/s/Sale` and `/s/sale`
  can't both exist; the redirect accepts any case, the display keeps the case as typed). `RESERVED`
  words (`admin`, `api`, `s`, `go`, `login`, ...) are refused as an alias. **A code is never issued
  twice**: a deleted or taken-down link keeps its row, the alias can't be edited (changing it would
  free the old code), and deleting the *user* (their links cascade) first copies their `code_key`s into
  `RetiredCode` (a `pre_delete(User)` receiver in `apps.py`, `weak=False`), which `codes.is_available`
  also checks. `AliasTakenError` (taken, reserved, retired) deliberately says nothing about whose.
- **Destinations** (`services.destinations`, the one check every create and destination edit goes
  through, web and API): `http`/`https` only, at most 2,048 characters, a syntactically valid public
  host (IP literals in loopback/private/link-local/multicast/reserved ranges, `localhost`, `.local`/
  `.internal` names and hosts a browser would read as an IP, like `2130706433`, are refused), no
  credentials in the URL, and **no loops**: a destination on this site's own `/s/` or `/go/` paths is
  refused after percent-decoding and resolving dot segments. Stored in normalized form (IDNA host,
  percent-encoded path). The UTM builder (`apply_utm`) merges `utm_source/medium/campaign/term/content`
  into the query first and the result is validated as a whole; an empty value never removes a
  parameter. `services.blocklist` then refuses a **blocked domain** (below). This checks what is
  written, not what a name resolves to; only `titles.fetch_title` resolves a host, and it checks.
- **The redirect** (`views/redirect.py`, mounted at the site root by `redirect_urls.py`,
  `app_name = 's'`: `^s/<code>(\+)?/?$` plus `/s/<code>/unlock` and `/s/<code>/report`, spelled
  without a trailing slash everywhere in docs and links). `SU_REDIRECT_IP` is charged **before** the
  lookup, so made-up codes spend the budget too. Every unavailable state (unknown, disabled, expired,
  past its cap, deleted, taken down, held, destination blocked) renders the same neutral
  `unavailable.html`, HTTP 404, `Cache-Control: no-store, private`; it never says why, and the
  preview, password and report pages answer the same. A password link redirects to `/s/<code>/unlock`
  first (not counted). Otherwise a **`GET` is a click** (a `HEAD` redirects without recording) and the
  answer is a **`302`, never a `301`** (the destination can be edited) with `Cache-Control: no-store,
  private`. **Over the rate limit** a known link still redirects and an unknown code is still a 404,
  never a 429 (a visitor must never see an error because someone else used the same address): the
  click is simply not recorded (no counter, no event). **The exception is a link with
  `max_clicks`**: it is still counted over the limit (the counter without the event,
  `clicks.record(with_event=False)`), because there the counter is what ends the link, not a
  statistic. Don't "fix" this back to `/go/`'s behaviour.
- **Counting and bot filtering** (`services.clicks`, `services.useragents`). `record` bumps the
  counters with one conditional `UPDATE` (the cap is in the same statement, so racing clicks can't
  overshoot it; it returns False and the view answers the neutral page when the cap was reached
  meanwhile) and inserts the `ClickEvent` in a savepoint (an analytics failure never breaks a redirect).
  A click is a bot's when its `User-Agent` matches `BOT_PATTERN` (crawler words, link-preview fetchers,
  scanners, headless browsers, HTTP libraries; a **missing `User-Agent` counts as a bot**) or
  `ua-parser` calls the device `Spider`. It is a filter for statistics, not a security control. The
  event is always recorded, with `is_bot`. The two counters: `click_count` is the headline number and
  what `max_clicks` caps; `bot_click_count` is the clicks flagged as bots. With `filter_bots` on (the
  default) a bot click adds only to `bot_click_count`; with it off every click adds to `click_count`
  and a bot's also to `bot_click_count` ("of these, this many were bots"). **A link with `max_clicks`
  counts every redirected click, bot or not**, whatever `filter_bots` says (otherwise anyone could
  follow a "first 100 visitors" link without limit by setting a header); its `click_count` therefore
  includes bots. The counters record the choice in force at the time; switching `filter_bots` later
  doesn't rewrite them, while the stats page re-reads the events with the current choice. The bot list
  only names crawler tokens, never a bare brand, so the DuckDuckGo, Pinterest, Flipboard and Yandex
  in-app browsers count as people.
- **Analytics** (`services.clicks`, `rollup`, `stats`, `geoip`, `jobs`). A `ClickEvent` holds the UTC
  time, an **HMAC-SHA256 of the UTC day and the normalized client address keyed with `SECRET_KEY`**
  (an IPv6 address as its /64, an IPv4-mapped one unwrapped: the rate limits' grouping; the address
  itself is never stored, and the hash changes daily, so uniques are per day and nothing links a
  visitor across days), the referrer **host** only (never path or query; own-site referrers are
  dropped), the country, device class (`desktop`/`mobile`/`tablet`/`bot`), browser and OS family and
  `is_bot`. Country comes from a MaxMind GeoLite2-Country database at `GEOIP_PATH` (`geoip2`, opened
  once per process, memory-mapped); without it every country is unknown and `manage.py check` warns
  `short_url.W001` (never an error: `migrate` runs the checks under `set -e`). Setup is in README's
  "Short URLs: country statistics"; getting the file into the deployment is a follow-up (below). Two
  scheduler jobs, both idempotent, errors propagating to `ScheduledJob.last_error`:
  `short_url.rollup_clicks` (hourly) rewrites whole **complete UTC days** into `DailyLinkStat` (clicks,
  uniques, bot clicks, uniques with bots) and `DailyLinkDimension` (referrer/country/device/browser
  per day, a day's tail folded into one `(other)` row past 50 values so made-up referrers can't grow
  the table), starting *at* the newest rolled-up day (repairing it) and stopping at the first day that
  fails; `short_url.purge_click_events` (daily) deletes events of whole days older than
  `click_event_retention_days`, **never a day that isn't rolled up yet nor the newest rolled-up
  day** (the rollup rewrites that one). Rollups are kept forever. `services.stats` reads rollups up to
  the watermark (`rolled_up_through`, read once per page/request so every figure splits at the same
  day) and the raw events after it, with the same arithmetic, so the numbers are exact whatever the
  rollup's lag. "Unique visitors" over a range is the sum of the days: someone back on another day
  counts again.
- **Password** (`services.passwords`, `views/password.py`). Set or cleared on the form (a paid extra,
  charged once per link, `None` removes it). Stored with Django's hasher. `/s/<code>/unlock` shows a
  form; a correct password is remembered in the session as a **fingerprint of the hash** per link (at
  most 100 remembered), so changing or removing the password locks everyone out again, then goes
  through the ordinary `/s/<code>` path, which counts the click. `SU_PASSWORD_IP` (strict, every
  attempt) and `SU_PASSWORD_LINK` (looser, across IPs, **failed attempts only**, peeked before the
  guess and hit after it fails) mirror `FT_UNLOCK_*`, so someone who merely knows a code can't lock
  the real recipients out. An over-long guess is refused before hashing. Nothing on the page, nor on
  the preview, says anything about the destination or title until unlocked.
- **Preview** (`views/preview.py`, `/s/<code>+`): the destination's host, the title, "created N ago", a
  Continue button, `noindex` and `no-store`, `SU_PREVIEW_IP` (a plain 429 over the limit: nobody
  follows a printed code to it). A protected link's preview shows neither destination nor title before
  unlock. It hosts the report form.
- **Billing** (`services.billing`, `limits`; `source='short_url'`). Four extras, each charged **once
  per link** by `charge_extra`: a compare-and-swap on `alias_paid_at`/`password_paid_at`/
  `analytics_paid_at`/`extra_link_paid_at` under the link's row lock in the same transaction as
  `spend_credits` (a refused spend rolls the claim back; a price of 0 still stamps it as paid). The alias
  is charged at creation, the password the first time one is set, the detailed analytics when the
  owner opens them (`enable_analytics`; **while the price is 0 the first view stamps it paid**, so a
  later price rise doesn't take away what a link already had) and **an active link beyond
  `free_active_links`** when it is created or enabled (`is_extra_link`: the *unpaid* active links;
  a link that paid keeps its place for good, disabling and re-enabling it is free). Turning an extra
  off and on, changing a paid password, or re-enabling are free; there are no refunds and no recurring
  charge. The **hard cap** (`max_active_links`) counts active *and* disabled links, so disabling
  doesn't free a slot; only deleting does. `InsufficientCreditsError` is a 422 with a Top up link on
  the web and a 402 on the API.
- **Cost confirmation and `expected_cost`.** Credits are never spent unseen. The web quotes the cost
  (`quote_create`/`quote_update`/`quote_enable`/`quote_analytics`), answers a first POST with a
  confirmation panel or the shared `confirmModal` (which posts `confirm_cost`), and passes the
  confirmed total to the service as `expected_cost`; `create_link`, `update_link`, `set_enabled` and
  `enable_analytics` **recompute the cost inside the charging transaction (after the owner's row is
  locked) and raise `CostChangedError` (nothing charged or changed) when it differs**, so a free
  allowance used up in another tab, or a price change, asks again. `expected_cost=None` means
  "accept whatever it costs", for a caller with no one to ask. The API uses both: see "API" below.
  The services leave what they charged on the link as `credits_spent` (not stored) so the API can
  report it.
- **Limits and validation** (`services.links`): `title` up to 255 characters; up to 20 `tags`, ASCII
  `[a-z0-9_-]`, lower-cased, spaces turned into hyphens (so the tag filter works on SQLite and
  Postgres); `max_clicks` 1 to 2,000,000,000; `expires_at` must be in the future when set or changed
  (an unchanged one that has since passed is not an error); the cap on links above. `update_link` is
  partial and all-or-nothing, diffed against the **locked** row; the code and alias never change. An
  owner whose `User.status` isn't `ACTIVE` can create, edit or enable nothing (`OwnerInactiveError`,
  checked under the lock, since a deactivated owner's web session outlives the deactivation); disabling
  and deleting stay open.
- **Title fetching** (`services.titles`, the `fetch_title` task, queued by `enqueue_after_commit` when
  a link is created without a title, and again when the destination of a title-less link changes):
  **the one place the server requests a URL a user chose**, so it has SSRF guards. Every host is
  resolved and refused unless *all* its addresses are public; the connection then goes to the address
  that was checked (not a second lookup: DNS rebinding) with the host kept for `Host`, SNI and
  certificate verification; the same after each redirect (at most 3), every target also passing
  `validate_destination`; 5 seconds in total (a watchdog timer, so a drip-feeding server can't stretch
  it), at most 256 KB read, no compression, only `text/html` is parsed, the title is cut to 255
  characters; any failure leaves the title empty and logs only the link's id. The final write is
  conditional on the title still being empty **and the destination unchanged**, so a slow fetch can't
  save a title for a page the link left. **Trade-off, accepted:** it runs on the `default` queue (no
  `short_url` queue or worker) because a fetch is bounded to 5 s and a `default` worker is free right
  after; Receipts has its own queue because its work is minutes long. If title fetching ever
  starves file transfer's emails and zip builds, give it its own queue and `db_worker` in
  `supervise.py` and compose.
- **Abuse tooling**, modelled on file transfer's (#59) and written up in the same terms:
  - **Blocked domains** (`BlockedDomain`, `services.blocklist`; admin CRUD with an optional
    `expires_at`): an exact host, or `*.host`, which blocks the host **and every subdomain** (a wildcard
    covers its own bare host; it needs two labels; an IP can only be blocked exactly). Refused at
    create and destination edit (`BlockedDomainError`, the neutral "That address cannot be used.", an
    unchanged destination stays editable) and checked by `is_available` **and by `set_enabled(True)`**
    (so an owner can't pay to enable a link that can't work), which takes existing links offline too.
    It costs one indexed query on every redirect, deliberately uncached for now.
  - **Reports** (`services.reports`, `views/report.py`, `POST /s/<code>/report`): reason, optional
    details (2,000) and reporter email, no login, no password (it works on a protected link without
    unlocking it). `SU_REPORT_IP` strict per IP; `SU_REPORT_LINK` a looser per-link ceiling that is
    *peeked* first and *hit* only once a report was created, with the link looked up before the key
    is touched (a made-up code creates none). A repeat from the same normalized IP against the same
    link within 10 minutes is a form error, not a second row. The htmx path answers `HX-Refresh`
    when the link became unavailable (htmx doesn't swap a 404).
  - **Auto-hold** (`services.hold`, off at `auto_hold_report_threshold` 0): `held_for_review_at`, a
    plain flag (not a status) set once the link has that many pending reports from *distinct
    normalized IPs* (the same /64 caveat as file transfer's: it raises the bar, it isn't a guarantee
    of N people). A held link is unavailable and **frozen for its owner except disabling it**
    (`LinkHeldError`; the API answers 409). Staff release it, or dismiss the last pending report.
  - **Takedown** (`services.takedown.take_down_link`, only from the admin): a distinct terminal status
    via one conditional `UPDATE ... WHERE status IN (active, disabled)`, recording who, when, the
    `takedown_reason` (**always shown to the owner**, never who reported it) and a staff-only
    `takedown_internal_note` (shown nowhere else), clearing a hold and marking pending reports
    actioned, all in one transaction. The owner is emailed ("one of your links was removed",
    `emails.send_link_taken_down_email`, autoescaped, no free text in the subject, confirmed address
    of an active account only) **only if staff opt in** on the confirmation page.
  - **Admin** (`admin.py`): read-only `ShortLinkAdmin` (search code/alias/destination/owner, state
    filter, pending reports), `LinkReportAdmin`, `BlockedDomainAdmin` (CRUD), `ShortUrlSettingsAdmin`,
    read-only `DailyLinkStatAdmin`. The abuse actions are admin *actions* (independent of
    `has_change_permission`), so each names a permission: `short_url.takedown_link`
    (`ShortLink.Meta.permissions`) for **Take down** (two-step page: reason, internal note, notify
    toggle default off), **Release abuse-review hold** and **Dismiss**; `short_url.add_blockeddomain`
    for **Block the destination domain** (two-step; subdomains toggle); `takedown_link` *and*
    `accounts.change_user` for **Deactivate the owner's account** (two-step; skips staff and the
    caller; optionally also takes down the owner's other active links). Deactivation sets
    `User.status` inactive, which the API and new sign-ins refuse, and `create_link`/`set_enabled`
    refuse for a session that outlived it. Superusers pass implicitly.
- **Safe Browsing** (issue #163; `services/safe_browsing.py` the client, `services/reputation.py`
  the policy, `hold.hold_for_threat`; migration `0005_safe_browsing`). Destinations are checked
  against Google's **Lookup API v4** (`threatMatches:find`: malware, social engineering, unwanted
  software, harmful apps), only when `SAFE_BROWSING_API_KEY` is set. Unset, every check is skipped
  and `manage.py check` warns `short_url.W002` (a Warning, never an Error: `migrate` runs the
  checks), like `GEOIP_PATH`. Settings: `SAFE_BROWSING_API_KEY`, `SAFE_BROWSING_TIMEOUT_SECONDS`
  (3: a person is waiting). The Lookup API sends the **URL itself** to Google (the hash-prefix
  privacy is the Update API's, which keeps a local database and was not built); the key is a
  query parameter, so errors raise `SafeBrowsingUnavailable` carrying only the exception type or
  status, never the request. Google offers it for non-commercial use and points commercial use to
  Web Risk (same shape, `uris:search`): a swap would be a change to `safe_browsing.py` only.
  - **Create and edit.** `reputation.check_destination(url)` raises `UnsafeDestinationError` (a
    `BlockedDomainError` subclass: the same neutral "That address cannot be used.", the same 403
    from the API, nothing about the list or the threat) and returns whether the URL was verified
    clean. It is called by `links._prepare` (so by `create_link` *and* the dry run
    `check_new_link`, before the transaction opens), by `update_link` (for a destination or UTM
    change, **before the row lock** is taken; a destination that only shows up under the lock is
    looked up there) and by `check_update`, so a refused request spends no rate-limit budget. An
    unchanged destination is never looked up. A verdict is remembered per process for 10 minutes
    (`CACHE_TTL`, at most `CACHE_MAX` entries), so a dry run and its real call make one lookup. A
    verified destination stamps `ShortLink.reputation_checked_at`; an unverified one (off, or
    failed open) leaves it null, which is what the recheck looks for.
  - **Fails open**, everywhere: not configured, a timeout, a non-200 or an unreadable reply never
    refuses a create or edit and never holds a link (logged at warning level). The destination
    rules, the block list, reports and takedown remain the baseline.
  - **The recheck** (`short_url.recheck_destinations`, hourly, lease 10 min; `reputation.recheck`):
    active, not-held links whose `reputation_checked_at` is null or older than `RECHECK_INTERVAL`
    (24 h), oldest first, up to `RECHECK_BATCH` (500) **distinct destinations** per run, which is
    **one API request** (hourly runs are 24 requests a day, far below the default quota; capacity
    is about 12,000 links a day, the backlog is worked oldest first). Clean destinations get
    `reputation_checked_at = now`; a flagged one puts each link on the abuse-review hold from #162
    (`hold.hold_for_threat`: unavailable, frozen for the owner except disabling) and files a
    **pending `LinkReport`** (`source='safe_browsing'`, reason phishing for social engineering,
    malware for the other threats, details naming the threat, no reporter) so it shows in the
    admin's report list (new `source` column and filter) for staff to take down or release. That
    report has no address, so it never counts toward `auto_hold_report_threshold`. An API failure
    **propagates** (it shows in `ScheduledJob.last_error`) and marks nothing, so the next run
    takes the same links. Disabled links are not rechecked (enabling one makes it due again soon).
  - **Releasing is remembered.** Dismissing the automated report, or "Release abuse-review hold",
    stores the destination in `ShortLink.reputation_cleared_destination`, and the recheck skips a
    link whose destination equals it: otherwise a false positive would be held again within a day.
    Changing the destination makes the link due again. (Editing a link *back* to a cleared
    destination is still refused at the edit, since Google still flags it.)
  - **Not done:** no check at redirect time (a link flagged since its last check keeps working
    until the next recheck, at most about a day), no Update API, and nothing for the owner beyond
    the "under review" state.
- **Web** (`urls.py` under `/links/`, `views/`, `templates/short_url/`): the dashboard (`/links/`, tabs
  All/Active/Disabled/Expired/Click limit reached/Taken down matching `display_state`, search over code,
  destination, title and tags, tag filter, sort by created/clicks, 25 per page, htmx list with
  `HX-Push-Url`: the receipts dashboard's pattern; the shared filtering is `services.filters`), create
  and edit forms (422 convention; the "your link will be ..." line; the cost panel), the link page
  (`/links/<id>/`, copy, QR from `/links/<id>/qr.png` rendered on demand with `SU_QR_USER`, never
  stored), row actions (`enable`, `disable`, `delete`: plain POSTs with a flash message), and the stats
  page (`/links/<id>/stats/?range=7|30|90|all`: totals always; a server-rendered SVG chart with a table
  fallback and the four top lists behind the paid analytics extra). Another user's link, or one the
  owner deleted, is a 404 on every page. `copyText`/`confirmModal` are the shared Alpine components in
  `core/js/components.js`. The Expires field and the link page's times use core's local-time
  helpers (see "Templates and static files"); the edit form is prefilled in UTC with `Z`
  (`views.form._initial`) for the script to show locally.
- **Rate limits** (`RATELIMIT_RULES`): `SU_REDIRECT_IP` (120/min, also charged by `/unlock` GETs),
  `SU_CREATE_USER` (30/h), `SU_EDIT_USER` (60/h), `SU_QR_USER` (60/min), `SU_PASSWORD_IP` (15/10 min),
  `SU_PASSWORD_LINK` (30/10 min), `SU_PREVIEW_IP` (60/min), `SU_REPORT_IP` (5/h), `SU_REPORT_LINK`
  (20/h), and `SU_QUOTE_USER` (120/min, API only: see "Status codes"). The web and the API share
  the other user rules' budgets.
- **API** (`apps/short_url/api/`, `schemas/`; mounted at `/api/s/`, JWT only: router-level
  `auth=JWTAuth()` in `api/router.py`, so no endpoint can be added without it; `links.py` and
  `stats.py` share that one `Router`). It calls the same `apps.short_url.services` as the web views.
  Paths are relative to the mount: `GET /links` (`q`, `tag`, `status` = `all|active|disabled|expired|
  capped|taken_down`, `sort` = `-created_at|created_at|-click_count|click_count`, `limit` 1-100 default
  20, `offset`; answers `{count, limit, offset, results}`; the same `services.filters` as the
  dashboard; an unknown `status` or `sort` is a 400), `POST /links`, `POST /links/quote`,
  `GET|PATCH|DELETE /links/{id}`, `POST /links/{id}/enable|disable|analytics`, `GET
  /links/{id}/stats?range=`, `GET /links/{id}/qr.png` (`image/png`, `SU_QR_USER`) and `GET
  /links/{id}/preview` (what the public preview shows, plus `available`; the owner always gets the
  destination). `PATCH` is partial via `model_dump(exclude_unset=True)`; both bodies are
  `extra='forbid'` (an unknown key, including `alias` on a `PATCH`, is a 422); an explicit `null`
  clears `expires_at`, `max_clicks` and `password` and is a 422 for the other fields; `utm_*` are flat
  fields merged into the destination; an empty `PATCH` changes nothing and spends no rate-limit
  budget. `expires_at` takes an ISO 8601 date-time (no offset: read in `TIME_ZONE`) or a bare date
  (`2027-12-31`), which means the end of that day in `TIME_ZONE` -- `schemas.link.ExpiresAt`, like
  file transfer's `ExpiryDate`, so every client means the same thing and the CLI sends the date as
  it is. A `LinkSchema` is the link without the password (only `password_protected`), with `state`
  (`display_state`, the block list queried once per page), `analytics_enabled` and `takedown_reason`
  (only when taken down, never the internal note or the reporter); the single-link endpoints return
  `LinkDetailSchema` (adds `costs` and `credits_spent`).
  - **Costs for a client with no one to confirm with.** The API accepts whatever a request costs
    **unless the client says what it expects**: `expected_cost` (the body of `POST /links` and
    `PATCH`, a query parameter of `enable` and `analytics`) is passed to the services as their
    `expected_cost`, so a mismatch is a **409** (nothing charged or changed) whose detail names the real
    total. Every write reports **`credits_spent`**, what it actually charged (0 on a read, on a free
    write, and on a no-op such as enabling an active link). `POST /links/quote` validates a create body
    exactly like the create (`services.check_new_link`: fields, alias shape and availability, owner
    status, the cap; no writes, no password hash) and answers `{credits, charges, balance,
    affordable}`; it spends none of `SU_CREATE_USER` (only `SU_QUOTE_USER`). `GET /links/{id}` reports `costs`
    (`{enable, analytics, password}`: what each paid action would cost that link now, or **`null`**
    for an action the link's state would refuse: `enable` unless the link is disabled, not under
    review and not pointing at a blocked destination; `analytics` for a taken-down link;
    `password` for one under review or taken down. 0 still means free or already paid). The CLI
    uses all of it: quote, ask, create with `expected_cost`; for a `null` it says "not possible
    right now" instead of asking to pay (and enabling an already active link just says so).
    Write responses carry the real `updated_at` (the services set it on the instance they write
    through `QuerySet.update`, which skips `auto_now`).
  - **Status codes** (`api/common.py`): **400** a service `ValidationError` (a bad field as `field:
    message`, the cap on links, a link that can no longer be changed, an unknown `status`/`sort`/
    `range`); **402** `InsufficientCreditsError` (one documented code, as in the other APIs);
    **403** `BlockedDomainError` (the neutral "That address cannot be used.") or `OwnerInactiveError`;
    **404** another user's link, or one the caller deleted (never 403; deleting twice is a 404 the
    second time); **409** the alias is taken/reserved/once someone's (`AliasTakenError`, never saying
    whose), a link held for review (`LinkHeldError`, only disabling works) or `CostChangedError`;
    **422** ninja's own validation (a list-valued `detail`); **429** `RateLimitExceeded` with
    `Retry-After`. `POST /links/quote` and every `POST /links` first charge the loose per-user
    `SU_QUOTE_USER` (120/min), so polling a quote or retrying a refused create is bounded. A refused
    create (400/403/409, anything `check_new_link` catches) spends none of `SU_CREATE_USER`: that
    budget is charged after the check and before `create_link`. `PATCH` runs `services.check_update`
    (not editable, held, inactive owner, any field that is invalid, a blocked destination: 404/409/
    400/403) *before* it charges `SU_EDIT_USER`, so those refusals spend none of it; a 409 for a
    changed cost needs the transaction and **does spend one edit**. Enable/disable/delete/
    analytics/stats/preview have no rule, as on the web.
    Tests: `apps/short_url/tests/test_api_*.py` (helpers in `api_support.py`) over Django's test
    client.
- **CLI** (`admin/shorturl.py`, `inv shorturl ...`; `SHORTURL_PASSWORD`, token `~/.shorturl_token.<env>`):
  `login`, `create <url> [--alias --title --tag... --expires --max-clicks --password --include-bots
  --utm-* --yes]`, `quote`, `list`, `get`, `update`, `enable`, `disable`, `delete`, `analytics`,
  `stats [--range]`, `preview`, `qr --out`. It is the first CLI on the shared plumbing in
  `admin/utils.py` (`api_request`: default timeout, a bounded 429 retry that doesn't sleep on an
  hourly rule's `Retry-After`, a connection error exiting 1; `api_error_text`/`raise_for_api_error`
  flattening ninja's 422 list; `receipts` and `filetransfer` still carry their own copies, to be moved
  over). Anything that can charge **asks first and sends the confirmed amount as `expected_cost`**
  (`create` quotes, `update --password`/`enable`/`analytics` read `costs` from `GET /links/{id}`; a
  refused or unaffordable one exits 1 before charging); `--yes` skips the question and the guard and
  accepts any cost; `delete` asks too. The link's own password is never an argument (shell history):
  `--password` is a flag that reads `SHORTURL_LINK_PASSWORD` or prompts with echo hidden. `--expires`
  takes an ISO date (the end of that day) or date-time; `update` has `--clear-tags|expires|max-clicks|
  password` and `--include-bots/--filter-bots`. Tables show the full link id (what the other commands
  take). Tests: `admin/tests/test_shorturl.py` (HTTP mocked) and the shared helpers in
  `admin/tests/test_cli_utils.py`.
- **Tests**: `apps/short_url/tests/` (the redirect matrix, every service, the web views, admin,
  analytics, abuse, the API and the services' API support), `admin/tests/test_shorturl.py`, the
  Playwright `tests_e2e/test_short_url.py` (create with an alias and the cost panel, follow, edit,
  preview, stats, disable and the neutral page, enable; run on the same server as the Receipts test,
  `receipts_server`, which hosts every product; a follow is Playwright's HTTP client with
  `max_redirects=0` and a person's `User-Agent`, because the destination is a made-up public host a
  browser couldn't load, and headless Chromium would count as a bot) and the AI plan
  `tests_e2e/ai/short_url.md`. No `inv openapi` spec is committed in this repo, so there is none to
  regenerate.
- **Not done / follow-ups.** Safe Browsing (#163) is built (see its bullet above; it needs
  `SAFE_BROWSING_API_KEY` in the env files, a manual step). One infra follow-up: the GeoLite2 database and
  `GEOIP_PATH` have to reach the containers (#171). A dedicated short domain, anonymous shortening,
  bulk create, a trash with restore and device/geo routing are out of scope for v1.

### To-do

`apps.todo` (issue #161, foundation #178; app label `todo`) is a task manager in the spirit of
Todoist: tasks with a Markdown description, a due date with an optional time, a priority, labels
and subtasks one level deep, shown on Today / Upcoming / All / Completed / Trash pages. A product
(same layer as `qr_code`/`file_transfer`/`receipts`/`short_url`), logged-in users only. **Built so
far:** the foundation (this section), repeating tasks (#179, "Recurrence"), comments with task
references (#180, "Comments and references" below), search with saved filters (#181, "Search and
saved filters" below), attachments (#184, "Attachments and storage billing" below), reminders
(#182, "Reminders" below), AI date parsing in the title (#183, "AI date parsing" below) and tasks
added by email (#186, "Email intake" below) and the JWT API with the `inv todo` CLI (#187, "API and
CLI" below).
Layout like `receipts`/`short_url`: `models/`, `services/`, `forms/`, `views/`, `templatetags/`,
`templates/todo/`, `static/todo/`, `jobs.py`, `admin.py`, `checks.py`, `urls.py` (mounted at
`/todo/`, `app_name = 'todo'`).

- **Constants** (`apps/todo/limits.py`, re-exported by `services/limits.py`: the models need them
  and a model can't import the `services` package, whose `__init__` imports the models):
  `TITLE_MAX` 500, `DESCRIPTION_MAX` 20,000, `LABEL_NAME_MAX` 50, `LABEL_DESCRIPTION_MAX` 255,
  `FIRST_TASK_NUMBER` 1000.
- **Models.** `TodoSettings` (a `SingletonSettingsAdmin` singleton, `/admin/todo/todosettings/`):
  `free_tasks_per_month` (10), `task_price` (10 credits; 0 makes tasks free), `trash_days` (30),
  `max_open_tasks_per_user` (10,000, a hard cap on non-trashed open tasks) and `max_labels_per_user`
  (200). `TodoAccount` (pk = the user) is created lazily by `services.accounts.get_account`: the
  next task number, `default_due_time` (09:00), the month count (`month_key` `YYYY-MM` in the user's
  zone + `month_task_count`) and `creation_blocked_at`. `Label` (per user, `#rrggbb` lower-case
  colour, a description shown as the chip's native `title`, `position`; unique per user ignoring
  case; `Meta.ordering` is `position, name`, but an aggregating query drops it, so
  `services.labels.list_labels` orders explicitly). `Task`: UUID pk, `number` (**per user**,
  unique with the user, never reused: a trashed or purged task keeps its number out of circulation),
  `parent` (self FK, `CASCADE`, one level: a subtask's parent has no parent, enforced by the services
  and `clean()`), `title`, `description` (Markdown), `description_text` and `title_text` (the
  normalised copies search will run over; `Task.save()` keeps them in step, also with
  `update_fields`), `priority` (0 none, 1 highest to 3), `due_date` and `due_time` (the user's local
  date and wall-clock time, **not an instant**; a time only with a date), `completed_at`,
  `deleted_at` (the Trash is a column, not a status), `position` (manual order among a parent's
  subtasks) and `today_position` (manual order of the Today list: two columns, see "Ordering"),
  `source` (`web`, `api`, `email`, `recurrence`), `charged_credits`, `unpaid_at`, `labels` (M2M). The user's time zone
  turns the local date into an instant only when something needs one: `services.dates.due_at(task,
  account)` uses `local_datetime` with `due_time` or the account's `default_due_time`; "today" is
  always `user_today(user)` (`apps.accounts.services.timezones`).
- **Numbering and billing** (`services.accounts`, `services.billing`). `tasks.create_task` is one
  `transaction.atomic()` that calls `allocate_number(account)` (locks the account row with
  `select_for_update`, so two creations can't read the same number, reads and bumps
  `next_task_number`, and refreshes the passed account from the locked row), then
  `charge_task_creation(account, settings, *, number, allow_unpaid)`: it counts the task in the
  user's calendar month (`month_key` in **their** zone; a new month resets the count), and beyond
  `free_tasks_per_month` spends `task_price` credits (`spend_credits`, `source='todo'`, description
  `To-do: task #<number>`) unless the price is 0. Subtasks and every occurrence count, from any
  source. `InsufficientCreditsError` **rolls the whole creation back** (the number and the count
  too), the web showing a 422 with a Top up link. `allow_unpaid=True` is for a task the user did not
  ask for (the next occurrence of a repeating task, #179): it is created owed (`unpaid_at` set,
  `charged_credits` = the price) and `TodoAccount.creation_blocked_at` is set, which makes
  `create_task` refuse (`'Top up your credits to add tasks: a repeating task is waiting to be
  paid.'`, `__all__`) for everything that is not `allow_unpaid`. The `credits_added` receiver
  (`apps.py`, `weak=False`, `try/except`; connected next to Receipts' and file transfer's) calls
  `billing.on_credits_added`, which only queues `settle_unpaid_tasks(user_id)` (a `default` queue
  task, through `enqueue_after_commit`, and only for a user whose account is blocked). It settles the
  user's unpaid tasks oldest first, each in its own transaction with the task row locked, stopping at
  the first the balance can't cover, and clears the block when none remain; idempotent. That last
  step (`billing._unblock_if_settled`) runs its "none remain" check and the clearing in one
  transaction under the account row lock (the one `allocate_number` takes), so a concurrent
  `create_task(..., allow_unpaid=True)` can't have its block cleared over its new unpaid task.
  **An unpaid task cannot be deleted forever** (decision on the review of #192): deleting the debt
  would let the user top up 1 credit, have `settle` find nothing owed and unblock creation. So
  `delete_forever` refuses a task that is unpaid, or whose subtasks are (`tasks.owes_credits`; a
  `ValidationError` with `UNPAID_MESSAGE`, which the web shows as a 422 / flash message, the 422
  with a Top up link to `billing:credits-history`), and `purge_trash` skips such tasks, and a
  parent holding one, until a settle pays them; they then follow the normal Trash rules. Trashing,
  restoring and completing an unpaid task stay allowed, and deleting one does not clear the block
  (only a settle does). Nothing is ever refunded: completing, trashing, deleting forever or
  purging keep what was paid. `get_account(user)` hands back an account whose `.user` is the
  `user` passed in, so billing and the pages cost no extra query. `month_usage` is
  `(tasks created this month, free allowance)`; the pages show `min(used, free)`
  (`views.common.free_line`) as "N of F free tasks used this month".
- **Services** (`services/tasks.py`; a refusal is a `django.core.exceptions.ValidationError` keyed
  by field, `__all__` for none). `create_task` validates before writing anything: title (stripped,
  1 to 500), description (`\r\n` normalised, at most 20,000; the form has no `max_length` of its own and calls
  this same check, so a browser submission at the limit is accepted), priority 0 to 3, a time needs a date,
  the parent is the user's own open non-trashed top-level task (`'A subtask cannot have
  subtasks.'`; re-read under its row lock inside the creation transaction, after the account lock,
  so it can't be moved under another task in between), labels are the user's own, the blocked state and the open-task cap (both checked
  under the account lock). `update_task(task, *, changes)` is partial (`UPDATABLE` keys), computes
  every value before assigning any (a refusal leaves the instance untouched), clears the time when
  the date is cleared, lets a task move under a parent only when it has no subtasks of its own (decided inside the
  write's transaction with the task and the new parent locked, `select_for_update` in pk order, so
  two concurrent moves can't leave two levels of nesting), and refuses a trashed task. A date
  change resets `today_position`. `complete_task` is a conditional write (`completed_at IS NULL`) that also
  completes the open, non-trashed subtasks in the same transaction (the web asks first, see below;
  `complete_task` also creates the next occurrence of a repeating task, see "Recurrence"); `uncomplete_task` leaves a subtask's parent as it is.
  `postpone_task(task, preset=, date=)`: `tomorrow`, `next_week` (the next Monday *after* today,
  so on a Monday it means in 7 days), `next_weekend` (the next Saturday after today, on a Saturday
  in 7 days), `date` (today or later), "today" being the owner's, keeping the time; a task without a
  date gets one. `reschedule_overdue(user)` sets `due_date` to today on every open overdue task.
  `trash_task` stamps the task and its not-yet-trashed subtasks with **one** `deleted_at`;
  `restore_task` clears it on the task and the subtasks with that same value (a subtask trashed on
  its own stays trashed) and refuses a subtask whose parent is still trashed; `delete_forever`
  cascades; `purge_trash()` (the daily `todo.purge_trash` job, lease 1 h) deletes what was trashed
  more than `trash_days` ago in batches, logging and skipping a row that fails.
  `toggle_checklist(task, index, checked)` is `apps.core.markdown.toggle_checklist_item` applied to
  the description *as it is now* (the row is re-read with `select_for_update` in the same
  transaction as the save, so two boxes ticked at once both survive) and saved through
  `update_task`; it returns the fresh task. `reorder(user, scope=, ids=, parent=)` writes the
  index of each id into the scope's column (`today`: `today_position` of the tasks due today,
  `dates.todays_tasks`, the overdue ones excluded; `subtasks`: `position` of the children of
  `parent`) and ignores the rest.
  `services/labels.py`: `create_label` (name whitespace-collapsed, unique ignoring case with a
  friendly message, the per-user cap, colour validated and lower-cased, empty = `#8fa89e`),
  `update_label` (`None` leaves a field), `delete_label` (tasks keep their other labels),
  `reorder_labels`, `list_labels` (with `task_count` of tasks not in the Trash).
  `services/references.py`: `resolver_for(user)` is the `ReferenceResolver` for
  `apps.core.markdown.render` (one query; only the user's own non-trashed tasks link) and
  `TASK_URL_PATH` the pattern of a task page's path (`/todo/<number>/`) that makes a pasted task
  URL a reference too; comments render through both (see "Comments and references").
- **Ordering** (`services/dates.py`). The default sort, in every list: due date ascending with
  undated last, then date-only before timed, then the time, then priority (1 first, none last),
  then oldest; done in the database (`order_tasks`, so pagination works). `?sort=` on All:
  `priority` (then date), `created` (newest), `title` (the normalised title). **`?dir=asc|desc` reverses it** (`dates.sort_direction`; each sort has a default: `date`, `priority` and `title` ascending, `created` descending; `order_tasks(qs, sort, direction)` returns the exact reverse of the default order for the opposite direction, ties included, and an unknown `dir` means the default). The "Sort by" bar (`views.lists.sort_links`) links the active sort to the opposite direction, so a second click reverses it, shows a small up/down arrow (with an `sr-only` "sorted ascending/descending") on it, and writes `dir` into the URL only when it isn't the sort's default. Only the All page has the bar; the search page's sort `<select>` is unchanged. **Manual order exists
  in two lists only, each with its own column**, so ordering one never changes the other: Today
  (the tasks due *today*, subtasks included; the overdue ones keep the default sort above them) in
  `Task.today_position`, and a parent's subtasks in `Task.position`. Judgement call, since a task
  can't otherwise tell "never positioned" from "positioned first": `reorder` writes 0, 1, 2, ...,
  so a list with any value above 0 in its column is *manually ordered* (`dates.has_manual_order`)
  and gets that column then the default order, otherwise the default sort. **The rule: the set
  that decides, the set a new task's value is taken over and the set the view orders are the same
  set.** Today: `dates.todays_tasks`; `create_task` gives a new task due today `max
  today_position + 1` over that set when it is manual, and every other task 0 (so a task created
  for another day, or while nobody reordered Today, never changes how any Today sorts, and the
  default stays due time then priority unless the user reordered *that* list). A date change
  (`update_task`, `postpone_task`, `reschedule_overdue`) resets `today_position` to 0, since a
  place in one day's order means nothing on another. Subtasks: `dates.subtasks_of(parent)`;
  `next_position` takes the max over that parent's subtasks, so a drag in one parent's list never
  orders another's. A top-level task's `position` stays 0. SortableJS (`sortablejs@1.15.6` from
  cdnjs, loaded only on Today, the task page, Labels and Filters) posts the order to `POST /todo/reorder/`
  (`scope`, `ids[]`, `parent`: the parent's number), answered 204.
- **Views and URLs** (`views/`; every one `@login_required`; another user's task, and a *trashed*
  task where a view doesn't accept one, is a **404**, never a 403: `views.common.owned_task`).
  `todo:today` `/todo/`, `todo:upcoming` `/todo/upcoming/` (14 days, every day listed, "Nothing
  due"), `todo:all` `/todo/all/` (50 per page, `?sort=`), `todo:completed`, `todo:trash`,
  `todo:list` `/todo/list/` (the partial every page fetches with htmx: `view`, `sort`, `page`; the
  response's `HX-Push-Url` is the *page* URL, a direct GET redirects to the page),
  `todo:create` `POST /todo/new/` (the quick-add form: on success the list of the hidden `view` is
  re-rendered into `#task-list`, with the cleared form `#quick-add` and the sidebar count swapped
  out of band), `todo:task` `/todo/<number>/` (also a trashed task, read-only with Restore),
  `todo:edit` (`part=title|description|details`, so each part of the page saves on its own; no part
  is the whole form), `todo:complete`, `uncomplete`, `postpone` (`preset`, `date`), `trash-task`,
  `restore`, `delete` (a trashed task, forever; an unpaid one is a 422, see "Numbering and billing"), `checklist` (`index`, `checked`; answers the
  re-rendered `#task-description`), `subtask-add`, `todo:reorder`, `todo:reschedule-overdue`,
  `todo:labels` (GET the page, POST add) with `labels/<id>/edit/`, `labels/<id>/delete/`,
  `labels/reorder/`, and `todo:settings` (GET, POST the default due time). Every page renders
  through `views.common.render_page` (creates the `TodoAccount` on first visit; adds the sidebar
  entries, the count beside Today, `todo_blocked` and the free-tasks line). **htmx answers**: an
  action on a row (`ctx=row`, the default, with the `view` the row is in) answers the re-rendered
  `task_row.html`, or an *empty* body when the row no longer belongs to that list
  (`views.lists.belongs`: completed from Today, postponed past the date, trashed anywhere), which
  htmx swaps in to remove it, plus the sidebar count out of band (completing a parent that had open subtasks adds an out-of-band
  `<li id="task-N" hx-swap-oob="delete">` per subtask it completed, so their rows leave the same
  list instead of staying open until a reload); from the task page (`ctx=page`)
  it answers the whole `task_panel.html` (`#task-panel`); without htmx it redirects back (a POSTed
  same-site `next`, else the `Referer`, else Today). Errors follow the 422 convention with
  `HX-Retarget` to the slot of the control that was used (`#quick-add-msg`, `#task-msg`,
  `#subtask-msg`, `#label-msg`, `#settings-msg`, else `#todo-msg`); `InsufficientCreditsError` is a
  422 with a Top up link to `billing:credits-history`. A parent with open subtasks asks first: its
  circle and its Complete button open the shared `confirmModal` (plain POST, so the redirect-back
  path above), no script of its own. The Postpone menu is Alpine in the template.
  `static/todo/js/tasks.js` (deferred, wired through `htmx.onLoad` so it covers swapped-in HTML)
  enables the description's checklist boxes (the server renders them `disabled`; a change posts to
  the block's `data-checklist-url`) and makes `[data-sortable]` lists sortable by their
  `.drag-handle`. Template tags (`templatetags/todo_ui.py`): `{% render_description task %}` (the
  Markdown through `apps.core.markdown.render` with `resolver_for` and `TASK_URL_PATH`; a text over
  the Markdown size guard is shown escaped instead of failing the page), `{% due_badge task %}` (red
  when overdue, `Today`, `Tomorrow`, the weekday for the next 6 days, else `5 Nov`, with the year
  when it isn't this year's; the time when set), `{% priority_flag task %}` (red / amber / sky for
  1 / 2 / 3, each with a dark twin), `{% label_chip label %}` (the colour at 20% as the background
  and as the text, the description as `title`). Times a page shows (`completed_at`, `deleted_at`) go
  through `|date`, so they follow the user's saved zone.
- **A row action refreshes what else on the page it changes** (`views.tasks.after_action` /
  `_refreshed_neighbours`, out of band, ignored by htmx when the target is not on the page): the other
  open occurrences of a repeating task (`offers_stop_repeating` depends on how many are left) and, for a
  subtask, the parent's Complete button (`partials/complete_button.html`, its question names the open
  subtasks). A success on the Labels and Filters pages clears the earlier refusal in `#label-msg` /
  `#filter-msg` (`clear_msg`).
- **Rate limits** (`RATELIMIT_RULES`): `TODO_CREATE_USER` (60 per minute: task and subtask
  creation, web and later the API) and `TODO_EDIT_USER` (240 per minute: every other mutation), plus `TODO_COMMENT_USER` (60 per
  minute: adding and editing a comment; deleting and restoring one spend `TODO_EDIT_USER`).
  A form is validated first and the limit charged after, so a refused form spends nothing; over the
  limit `ratelimit.web_response` (retargeted to the control's message slot).
- **Admin**: `TodoSettingsAdmin` (singleton, superusers), and read-only `TaskAdmin` (search title,
  number, user email; filters for priority, source, completed, trash, unpaid), `TodoAccountAdmin`,
  `LabelAdmin`, `CommentAdmin` (revisions inline) and `RecurrenceAdmin`, none with add, change or
  delete (the user admin lists `todo` in `CASCADE_WITH_USER_APP_LABELS` so deleting a user still
  works).
- **Recurrence** (issue #179; `models/recurrence.py`, `services/recurrence.py`,
  `static/todo/js/recurrence.js`, migration `0003_recurrence`). A repeating task is a chain of
  ordinary `Task` rows, each billed, numbered, completed and searched like any task, sharing one
  `Recurrence` (`Task.recurrence`, `SET_NULL`, `related_name='tasks'`): `rrule` (an RFC 5545 `RRULE`
  value, canonical, no `RRULE:` prefix, no `DTSTART`), `mode`, `anchor_date`, `ended_at` and
  `anchor_day` and `occurrences_created` (1 for the first task). Each occurrence also stores the
  anchor it was scheduled for, `Task.recurrence_anchor`. Only a top-level task repeats. Dependency:
  `python-dateutil` (`dateutil.rrule`).
  - **Rules** (`services.recurrence.parse_rule`, the one place a rule is validated; the form and
    `create_task` / `update_task` all go through it): `FREQ` `DAILY|WEEKLY|MONTHLY|YEARLY`
    (required), `INTERVAL` 1..365, `BYDAY` (an ordinal `-1`, `1`..`4` only for `MONTHLY`: `1MO`,
    `-1FR`), `BYMONTHDAY` 1..31 or -1, `BYMONTH` 1..12, `COUNT` 1..1000 *or* `UNTIL` `YYYYMMDD`;
    `WKST` is ignored and anything else (`BYHOUR`, `BYSETPOS`, `SECONDLY`, ...) is
    `ValidationError('Unsupported repeat rule.')`. It returns the canonical form (upper case, parts in
    the order FREQ, INTERVAL, BYDAY, BYMONTHDAY, BYMONTH, COUNT/UNTIL, `INTERVAL=1` dropped, which
    `recurrence.js` also writes); a rule over 255 characters, or with no date from yesterday on
    (`UNTIL` in the past, 30 February), is refused. "No date from yesterday on" is judged on the
    **user's** today, not UTC's: `parse_rule(value, today)` and `clean_rule(value, today)` take
    `user_today(user)` (the form, `create_task` and `update_task` pass it), so `UNTIL` of the user's
    own today is accepted at 23:30 in Chicago. `describe(rrule)` is the sentence for the UI
    (`Every 2 weeks on Monday, Wednesday`, `Every month on the last day`, `Every year on 3 March,
    5 times`, `..., until 31 Dec 2026`; any other combination `Custom repeat (<rule>)`).
    `next_date(rrule, after, day=None)` is `rrulestr(..., dtstart=after).after(dtstart, inc=False)`:
    **an explicit `BYMONTHDAY` beyond the month's length is skipped by dateutil, not clamped** (`31`
    has no February) and `-1` is the last day. **A monthly/yearly rule with no day of its own** (no
    `BYMONTHDAY`, no `BYDAY`) repeats on the day of the month the schedule was set on, **clamped to
    a short month's last day** instead of skipping it: 31 Jan, 28 Feb (29 in a leap year), 31 Mar,
    30 Apr, 31 May; a yearly one on 29 Feb falls on 28 Feb in other years and on the 29th again in
    leap years. Over the 28th (`day`, default `after`'s day) `_iterate` runs the rule on the 1st of
    each period (`BYMONTHDAY=1`, from the 1st of `after`'s month so `INTERVAL` still counts from it;
    `BYMONTH` = `after`'s month for a yearly one) and moves each result to `min(day, month length)`,
    checking `UNTIL` against the moved date. `Recurrence.anchor_day` keeps the intended day once
    `anchor_date` itself has been clamped (set with the anchor when the schedule is set: creation or
    a rule change; fixed mode passes it as `day`, from completion uses the completion date's own).
    **`COUNT` is stripped from
    `next_date`**: dateutil would restart the count at every computed date, so
    `occurrences_created` counts instead.
  - **Anchor and the two modes.** `anchor_date` is the date the *current* occurrence was scheduled
    for by the rule. Postponing or editing an occurrence's `due_date` never changes it (rent
    postponed from the 1st to the 5th still has its next one computed from the 1st). Editing the
    *rule* (a different rule or mode, through `update_task`'s `rrule`/`recurrence_mode`, which
    the details form posts) sets it to the task's date; the same rule and mode change nothing. A
    task given a rule and no date gets `services.recurrence.first_date`: the first date on or
    after today (the series starts at today and includes it, so a rule with no day of its own,
    "every 3 months", is due today). **fixed** counts the next one from the anchor, **from
    completion** from `user_today(user)` (the completion date in the user's zone; completing early
    moves the next one earlier).
  - **Completion** (`complete_task` -> `recurrence.on_completed(task)`, inside its transaction,
    after `completed_at` is set and *before* the open subtasks are completed, so they can be
    copied). **An occurrence spawns its successor at most once.** The `Recurrence` row is
    `select_for_update()`d and the next occurrence is created only while the task's own
    `recurrence_anchor` (the anchor it was scheduled for: set when it is created as an occurrence,
    to its due date, and again when a rule change re-anchors it) equals the recurrence's
    `anchor_date`. Creating the successor moves `anchor_date` on, so completing the same
    occurrence again after an uncomplete (in either mode), completing an older open occurrence while
    a newer one exists, and two tabs completing at once all create and charge nothing. It ends
    the rule (`ended_at`, no task) when `next_date` is `None` or `occurrences_created >= COUNT`;
    otherwise it creates the next occurrence with `create_task(..., source='recurrence',
    allow_unpaid=True)` (title, description, priority, labels, `due_time`, the `recurrence`,
    `due_date` = the next date) and each **open** subtask the same way (title, description,
    priority, labels, no date; a manual order is kept), then `anchor_date = next`,
    `occurrences_created += 1`. Every row counts toward the month and is charged (or created
    owed, see "Numbering and billing": completing always succeeds). A refusal creating the next
    one (a `ValidationError`, usually the open-task cap) is rolled back to a savepoint and retried
    **once without the copied subtasks**; the user is told in-app either way, with one notice per
    recurrence (`recurrence.failure_key`: `todo_recurrence_failed:<recurrence id>`, replacing the
    last, and cleared by the next clean creation; url: the completed task's page; written in its own
    savepoint and never raising): "Couldn't create the next "<title>"" when even that fails, in which
    case the recurrence and its anchor are unchanged and the completion stands -- uncompleting and
    completing the task once there is room tries again, since the anchors still match --, or "The
    next "<title>" has no subtasks" when only the subtasks were left out. `copy_extras(from_task, to_task)` carries the task's reminders to the
    next occurrence (see "Reminders").
  - **Changing the rule** (`recurrence.change`, an edit of a running one) sets the anchor (and
    `anchor_day`) to the task's date, makes that task the one whose completion spawns the next
    (`Task.recurrence_anchor`; older open occurrences no longer do) and **resets
    `occurrences_created` to 1**: the edited task is the first occurrence of the changed series,
    the same as a new `Recurrence` (a rule given to a task with none, or after stopping) starting at
    1, so a `COUNT` means the same whether it was set at creation or by an edit. The same rule and mode change nothing.
    `update_task` judges the rule change (open task, not a subtask, not becoming one) against the
    task row it locks and re-reads, like its other checks.
  - **Uncompleting** an occurrence that already spawned the next leaves the next one (no refund, no
    deletion; the task page says so), and completing the old one again creates nothing more (see
    above). **Trashing** an occurrence does not end the rule: the confirmation of the trash button of
    a repeating task that is **the only open occurrence** of its running rule (`Task.
    offers_stop_repeating`, from the `open_occurrences` annotation `dates.with_open_occurrences`
    that `decorate` adds, so a list doesn't query per row) -- the shared
    `confirmModal`, which learnt an optional checkbox from `data-checkbox-name` /
    `data-checkbox-label` -- offers "Also stop repeating", posted as `stop_recurrence=1`. **Stop
    repeating** (`recurrence.stop(task)`, `POST /todo/<n>/recurrence/stop/`, `TODO_EDIT_USER`) sets
    `ended_at` and keeps the task; an empty `rrule` in the details form does the same.
    `Task.active_recurrence` is the rule while `ended_at` is null, which every chip and button reads.
  - **Web.** The task form (quick add and the task page's details) has a "Repeat" button opening a
    popover (`repeatControl`, an Alpine component in the classic script `recurrence.js`, which
    also parses an existing rule back into the controls and keeps one the controls cannot express
    until they are used) that builds `rrule` and `recurrence_mode` into two hidden inputs; the form
    only reads them when the request has the keys (a request without them leaves the rule alone,
    since an empty `rrule` means "does not repeat"). Month offers the same day of the month, a day,
    the last day, or the nth weekday; year a month and day or the same date. Rows and the task
    page show `{% repeat_chip task %}` (`fas fa-repeat`, the sentence, the mode: always on the page,
    in a row only for "from completion", the mode name as the tooltip); Completed lists each
    occurrence as its own row. The 422 for a bad rule is `Repeat: Unsupported repeat rule.`.
  - **Admin**: read-only `RecurrenceAdmin`; `TaskAdmin` lists `recurrence`. Tests:
    `apps/todo/tests/test_recurrence.py`; the AI plan has TODO-19 (fixed) and TODO-20 (from
    completion).
- **Tests**: `apps/todo/tests/` (`test_numbering_and_billing.py`: numbering, the lock, the allowance
  and price, the month in the user's zone, unpaid tasks and settling through the `credits_added`
  signal; `test_tasks_service.py`; `test_labels_and_references.py`; `test_views.py`;
  `test_wiring_and_ui.py`; `test_comments.py` and `test_references.py`, see "Comments and
  references") and the AI plan `tests_e2e/ai/todo.md`. A test that needs a fixed day
  uses the `wednesday`/`freeze_now` fixtures (`timezone.now` is replaced), billing tests the
  `config` fixture (2 free tasks, price 5).

#### Comments and references

Issue #180 (`models/comment.py`, `models/reference.py`, `services/comments.py`,
`services/references.py`, `views/comments.py`, migration `0002_comments_and_references`).

- **Models.** `Comment` (UUID id, `task` FK `related_name='comments'`, `user` the author -- always
  the task's owner today, kept for the future --, `body` Markdown up to `COMMENT_MAX` 10,000,
  `body_text`, `created_at`, `edited_at` null until the first edit, `deleted_at`, `revision_count`;
  ordered by `created_at`, indexed `(task, created_at)`). `CommentRevision` (`comment` FK
  `related_name='revisions'`, `body` = the text **before** the edit that made it, `replaced_at`;
  ordered by `replaced_at`). `TaskReference` (`from_task` -> `to_task`, `comment` null for a mention
  in the description; two unique constraints, `todo_reference_unique` on the three columns and
  `todo_reference_unique_description` on `(from_task, to_task)` where `comment` is null, because a
  unique constraint treats NULLs as distinct on SQLite and Postgres alike). All cascade from the
  task, so deleting a task (or its user) removes them.
- **The revision model.** `edit_comment` re-reads the comment under `select_for_update` and, in one
  transaction, writes `CommentRevision(body=<old body>, replaced_at=now)`, the new `body` and
  `body_text`, `edited_at` and `revision_count + 1`, then `sync_references`; two edits at once make
  two revisions, never one lost (the instance passed in is brought up to date either way). An edit
  to the same text (after stripping and `\r\n` normalisation) is a no-op: no revision. At most
  `MAX_REVISIONS` (100) edits per comment, then `'This comment has been edited too many times.'`;
  a deleted comment cannot be edited. `comment_history` is oldest first; the page shows newest
  first. **Deleting is soft** (`delete_comment`: `deleted_at`, `body_text=''`, the comment's
  `TaskReference` rows deleted; body and revisions kept); `restore_comment` clears `deleted_at`,
  rebuilds `body_text` and the references. Both are no-ops in the state they ask for. `add_comment`
  needs the user's own, non-trashed task (a completed one is fine) and strips the text
  (1 to 10,000 characters; empty is `'Write a comment.'`).
- **What is and is not searchable.** `Comment.body_text` is `search_text(body)`, the column the
  search sub-issue (#181) reads. It is `''` for a deleted comment, so a deleted comment is never
  found, and it follows every edit. **Revisions have no search column and are never searched**
  (`CommentRevision` carries only the raw `body`). Their text is still viewable by the owner: on the
  page and in the admin.
- **References.** `services.references.extract_task_numbers(markdown)` is
  `apps.core.markdown.extract_references(markdown, task_url_path=TASK_URL_PATH)`: the very
  traversal `render` does to find what to hand its resolver (`#` plus 4-9 digits not in code or a
  link's text, and links to `/todo/<n>/`, site-relative or on `BASE_URL`'s host), once each, in
  order; a text over the Markdown size guard mentions nothing. (**Judgement call:** the issue wrote
  `extract_references(markdown)`; core knows no task URL, so, like `render`, it takes the caller's
  pattern as the keyword `task_url_path` and without it only `#1234` counts.)
  `sync_references(task, comment=None)` resolves those numbers to the owner's tasks (open,
  completed **or in the Trash**; a number that isn't theirs, doesn't exist or is the task itself
  is dropped) and makes the rows of `(task, comment)` equal that set in one transaction (insert
  missing with `ignore_conflicts`, delete stale). It runs from `create_task` (when there is a
  description), `update_task` (when the description changed; so also a ticked checklist box), and
  `add_comment`, `edit_comment`, `delete_comment` (which just deletes the rows) and
  `restore_comment`. Description and comments keep separate rows. **Rows are stored for any of the
  owner's tasks, trashed ones included, and filtered only when read**, so trashing and restoring a
  task (as target or as source) needs no re-sync and "Referenced by" never disagrees with what the
  text says: a mention made while the target was in the Trash is listed once it is restored.
  What is displayed is decided by `referenced_by(task)` -> `[(Task, Comment | None)]`: open and
  completed non-trashed tasks that mention it, ordered by the mentioning task's number, the
  description before its comments (oldest first); the same task appears once per place it
  mentions it. It lists nothing for a task that is itself in the Trash, and hides a trashed
  source and a deleted comment (their rows stay). The links in rendered text are decided at
  render time by the resolver, which links only non-trashed tasks, so a trashed target shows as
  plain text until it is restored. `update_task` compares the description with the stored one,
  saves and syncs under the task's row lock in one transaction (so the rows always match the text
  that was saved, and a task trashed meanwhile is refused).
- **Rendering.** `references.resolver_for(user)` now remembers what it has been asked, so a page
  rendering every comment through one resolver looks each number up once.
  `resolver_for_request(request)` keeps one per request, which the description (`render_description`)
  and the comments (`comments_context`) share, so a task page looks each number up once in all. `render_markdown(text,
  resolver)` is `render` with `TASK_URL_PATH` and the fall-back to escaped text for a text over the
  size guard (what the comments, the history and the deleted text use).
- **Web** (all `@login_required`, htmx partials, 422 convention; another user's task, a comment of
  another task, a trashed task for every action that changes something, and the deleted-text
  partial of a comment that isn't deleted are a 404). The comments section
  (`partials/comments.html`, `#task-comments`) sits **beside** `#task-panel` on the task page, not in
  it, so an action on the task (complete, postpone, a label, an edit, a ticked checklist box) neither
  re-renders the comments nor loses a draft or an open inline edit; `panel_context` has no comments,
  `task_page` adds `comments_context`. The one exception is moving the task to the Trash and back:
  it makes the section read-only/editable (and decides whether `#1234` to the task itself links),
  so those two responses carry the section again out of band (`hx-swap-oob`, `refresh_comments`).
  It is what each comment action answers: `POST /todo/<n>/comments/`
  (`todo:comment-add`), `.../comments/<id>/edit/`, `/delete/`, `/restore/`, `GET .../history/`
  (the revisions partial, newest first, each rendered, with when it was replaced; works for a
  deleted comment and a trashed task) and `GET .../deleted/` (the deleted comment's rendered text
  with Restore and History). Each comment: initial avatar, relative time (`|ago`) with the exact
  local time as `title` (`{% local_time ... title=True %}`), "(edited)" (click: the history is
  fetched into `#comment-history-<id>`), the rendered body, and a row menu with Edit (inline
  textarea, same Markdown hint as the description, Save/Cancel; Cancel and Escape also reset the
  textarea to the saved text) and Delete (the
  page's shared `confirmModal`, a plain POST that redirects back to `#comment-<id>`). A deleted
  comment is a muted "Comment deleted <time>" with Show. The add box is at the bottom;
  Ctrl+Enter / Cmd+Enter submits it (and the edit box). Refusals go to `#comment-msg` (add,
  delete, restore) or `#comment-msg-<id>` (edit). **Rate limits:** `TODO_COMMENT_USER` (60 per
  minute, add and edit; a refused form spends nothing) and `TODO_EDIT_USER` for delete and
  restore; deleting a comment that is already deleted, or restoring one that isn't, is a no-op that
  spends nothing (the view checks the state before charging); without htmx they flash "Comment
  deleted." / "Comment restored.". "Referenced by" (`partials/referenced_by.html`, hidden when empty,
  in the panel): `#<number> <title>`
  per mention, "in a comment" and a `#comment-<id>` anchor when it is one. The section order on the
  page is the title, the description (right under it, above the action buttons: it is what the task
  *is*), the actions, details, reminders, subtasks, referenced by, attachments, comments (the comments
  last, with their add box). `task_row.html` shows a comment icon
  with the count of comments that aren't deleted (`with_comment_counts`, a subquery in
  `views.lists.decorate`; a join would multiply the subtask counts).
- **Admin.** Read-only `CommentAdmin` (newest first; task number, user, created, edited, revisions,
  deleted, the body; search over body, task and user) with `CommentRevision` as a read-only inline. A deleted
  comment's body is shown there too.
- **Tests.** `apps/todo/tests/test_comments.py` (add/edit/delete/restore rules, the revision
  chain, the cap, `body_text`, the views and partials, 404s, rate limits, the admin),
  `test_references.py` (extraction, `sync_references` on create, description edit, comment
  add/edit/delete/restore, self-reference, foreign and trashed targets, `referenced_by`, the
  panel), the `extract_references` tests in `apps/core/tests/test_markdown.py`, and TODO-17 and
  TODO-18 in `tests_e2e/ai/todo.md`. Also covered: a panel action's response has no comments
  section (`TestCommentsBesidePanel`), a mention made while the target is trashed shows after the
  restore, the shared resolver and `update_task`'s lock (`test_review_fixes.py`).

#### Attachments and storage billing

Issue #184 (migration `0004_attachments_and_storage_billing`). Files attached to a task, uploaded straight to S3 with a presigned PUT like Receipts'
uploads, downloaded through short-lived presigned URLs, billed by storage at the shared
`BillingSettings` price. Everything is the Receipts pattern re-applied; read its sections for the
long reasoning.

- **Storage** (`services/storage.py`): `get_storage()` builds the core `S3Storage` from the
  `TODO_*` settings with `signature_version='s3v4'` (required: `presigned_put_url` fails closed on a
  client that doesn't sign `Content-Length`), `reset_storage_cache()`, keys
  `todo/users/<user_id>/attachments/<attachment_id>` (`attachment_key`: id only, never the filename)
  and `user_prefix(user_id)` (one `delete_prefix` removes everything of a user). The test suite
  swaps in `tests/fakes.py::FakeTodoStorage` (autouse in `tests/conftest.py`).
- **Models.** `Attachment` (`models/attachment.py`): `task` FK (`related_name='attachments'`,
  `CASCADE`), `user`, `filename` (cleaned: control characters stripped, `/` and `\` become `_`,
  empty -> `file`, at most 255), `size_bytes`, `declared_type` (what the browser said; recorded,
  never trusted), `inline_type` (the type **sniffed from the bytes** when it is one a browser may
  show inline -- PDF, JPEG, PNG, GIF, WebP -- else empty), `sha256` (base64, declared by the client
  and verified by S3 through the signed checksum header), `uploaded`, `created_at`; index `(task,
  created_at)`. `TodoAccount` gained Receipts' four billing columns (`stored_bytes`,
  `last_billed_at`, `accrued`, `billed_days`) and `frozen_at`; `TodoSettings` gained
  `max_attachment_bytes` (50 MiB) and `max_attachments_per_task` (20). `stored_bytes` counts only
  uploaded attachments, changed only through `services.accounts.add_stored_bytes` (floored at 0, the
  billing clock restarts when an empty account gets its first byte).
  `apps.core.services.sniff.sniff_inline_type(head)` is the magic-byte check (Receipts keeps its own,
  wider sniffer).
- **Uploads** (`services/attachments.py`). `start_upload(task, *, filename, size, declared_type,
  checksum_sha256)` refuses a trashed or completed task, a size of 0, one over
  `max_attachment_bytes` ("The file is too large (max N MB)."), a malformed checksum, and -- under a
  lock on the task row, which also re-reads its state and **the account's frozen state**
  (`AccountFrozenError`, a `ValidationError` subclass with the fixed message "Storage is paused:
  top up your credits to upload or download files."; catch it *before* a generic
  `ValidationError`) -- more than `max_attachments_per_task` rows (pending ones count). A blank
  filename is not refused: it becomes `file`. It creates `Attachment(uploaded=False)` and
  presigns the PUT with `Content-Length` and `x-amz-checksum-sha256` signed in, returning the
  headers to send. `complete_upload` is idempotent: `head_object` must report the declared size, then
  the object is **read back and its SHA-256 compared with the declared one unconditionally** (the
  way Receipts does, never "only when S3 echoes a checksum"), streamed in `READ_CHUNK_BYTES` chunks
  so memory stays bounded for a 50 MiB file; a mismatch (size or checksum) deletes the row and then
  the object ("The upload did not complete correctly."), while a read-back that fails (S3 error,
  connection dropped) is a retryable 422 that keeps the row. The first `SNIFF_BYTES` (12, the
  sniffer's own constant in `apps.core.services.sniff`) of the same pass are sniffed into
  `inline_type`; one conditional `UPDATE ... WHERE uploaded=false` marks it uploaded and calls
  `add_stored_bytes` in the same transaction, so two racing completes count once. Nothing is billed
  before that. **It refuses a task in the Trash** (422, deleting the upload) and **a row that is
  gone** raises `Attachment.DoesNotExist` (the view answers 404, never a 500), deleting any object
  it finds (`discard_orphan_object`, which only runs while no row has that id and only on the
  requesting user's own prefix; a `complete` for a task that no longer exists does the same).
  `cancel_upload` (the Cancel button, `POST /todo/<n>/attachments/<id>/cancel/`, `TODO_EDIT_USER`)
  deletes a *pending* row and then its object so it stops counting toward the per-task cap
  (idempotent: a row that is gone is `ok`; an uploaded one is a 422 -- delete it instead; the
  script calls it when a row is cancelled or dismissed after `start`, and after a failed PUT).
  `abandon_uploads` (the hourly `todo.cleanup_uploads` job) and `cancel_upload` **claim the row
  first** (a conditional `DELETE ... WHERE uploaded=false`) and delete the object only after
  winning, so a late `complete` finds no row instead of an attachment counted and billed with no
  object behind it; if that object delete then fails the object is left behind, logged (the row is
  already gone), one bad row skipped.
- **Downloads.** `download_url(attachment, *, inline)` refuses a frozen account and an upload that
  never completed, and presigns a GET: `disposition='inline'` **only** when the caller asked and
  `inline_type` is set, `content_type=inline_type or None`. So a `.exe` named `photo.png` is always a
  download; the declared type is never consulted.
- **Deleting.** `delete_attachment` deletes the object first (a storage error raises and leaves
  everything as it was), then, under the row lock, the row and -- for an uploaded one, only by the
  call that actually deleted the row -- its bytes. **Trashing a task keeps its attachments, still
  billed.** `delete_forever` and `purge_trash` call `attachments.delete_for_tasks` (the task and its
  subtasks) inside the transaction that deletes the task: one `delete_objects` (a per-key S3 error
  raises after deleting the rest, so the task stays for the next run: the purge logs and skips it,
  `delete_forever` turns it into a "files could not be deleted" message), then the rows and the
  uploaded bytes under a lock, then the *pending* rows' keys are deleted once more (best effort,
  logged: the rows are already gone). **Known window, accepted:** keys are per attachment, not per
  task, so an upload whose PUT lands *after* its task was deleted for good has no prefix to sweep.
  The object is deleted when the browser then calls `complete` (no row: `discard_orphan_object`);
  what remains is a PUT landing after both deletes whose tab never calls `complete` (it was closed
  mid-upload). That object is never billed and goes with the user's prefix when the account is
  deleted. A `pre_delete(User)` receiver (`apps.py`) queues
  `lifecycle.delete_user_storage(user_id)` (`delete_prefix(user_prefix)`; Receipts' retry shape: a
  failure queues itself again after 5 min, 30 min, 2 h, 6 h, then logs the prefix and gives up; on
  the `default` queue).
- **Metering** (`services/metering.py`, the twin of Receipts'): `meter_account(account_pk, price)`
  adds one day of `stored_bytes` x the shared price to `accrued` per 24 h and spends the floor
  (`storage_charge`), under the account row lock, advancing `last_billed_at` by one period and
  `billed_days`; the ledger line is "To-do storage, day N" (`source='todo'`). An
  `InsufficientCreditsError` **freezes** the account (`freeze_account`: `frozen_at`, the
  `todo/emails/storage_paused.{txt,html}` email to a confirmed address, queued on commit, and an
  in-app notification "Your to-do file storage is paused" -> `todo:settings`, key
  `todo_account:frozen`, in its own savepoint like Receipts'). Frozen: uploads and downloads are
  refused, nothing is deleted, the clock stops. `resume(user_id)` (task `resume_account`, `default`
  queue) unfreezes only when the balance covers `daily_storage_cost(stored_bytes)` (`ceil(price x
  bytes / GiB)`, at least 1) with `last_billed_at = now` (the frozen days are never billed) and
  deletes the notice. `billing.on_credits_added` -- the existing `credits_added` receiver's work --
  now also queues `resume_account` for a user with a frozen `TodoAccount`, each of the two queueings
  guarded on its own so one failing never skips the other. The daily `todo.meter_storage` job meters
  every account with `stored_bytes > 0` and not frozen, then offers `resume` only to the accounts
  that were **already frozen before its pass** (snapshot first), so an account frozen by this very
  run isn't unfrozen again with the day forgiven.
- **Web** (`views/attachments.py`, `templates/todo/partials/attachments.html` and
  `attachment_list.html`, `static/todo/js/attachments.js`). The block sits on the task page *outside*
  `#task-panel` (an action on the page re-renders the panel, which would drop the rows of an upload
  in flight), like the comments. **Page order** (`todo/task.html`): the task panel, then Attachments,
  then Comments, so the task's content comes before its discussion; `task_page` merges
  `panel_context`, `comments_context` and `attachments_context`. Endpoints:
  `POST /todo/<n>/attachments/start/` (JSON in and out) and
  `POST /todo/<n>/attachments/<id>/complete/`, `POST /todo/<n>/attachments/<id>/cancel/`,
  `GET /todo/<n>/attachments/` (the list partial the
  script fetches after a file lands; opened directly it redirects to the task page),
  `GET /todo/attachments/<id>/download/` (`?inline=1` for Open; a redirect to the presigned URL,
  `no-store`; frozen: back to the task with a flash message), `POST /todo/attachments/<id>/delete/`
  (from the shared confirm modal; a plain POST ending in a redirect to the task with a message, an
  S3 error is a message, not a 500). Statuses of the JSON endpoints: **422** for a refusal (including
  a task trashed since the upload started) *and* for
  a frozen account (with `frozen: true` and `top_up_url`, which the script turns into a "Top up"
  link on the row), **429** with `Retry-After` from the rate limits, **404** for another user's task
  or attachment (never 403), for a trashed task on `start`, for an upload whose row is gone on
  `complete`, and for an attachment that never completed on download and delete. The size goes through `apps.core.json_numbers.whole_number`.
  `attachments.js` mirrors Receipts' `upload.js`: SHA-256 in the browser, `start`, an XHR `PUT` with
  exactly the returned headers and a type-less Blob, `complete`, three at a time, `Retry-After`
  honoured up to 3 times; the drop zone is core's shared `initDropZone`. A completed attachment can
  be removed from a trashed task and while frozen (it saves money); an open task takes uploads, a
  trashed or completed one doesn't (the block hides the drop zone and doesn't load the script).
  `layout.html` shows the "Storage is paused" banner (with Top up) while `frozen_at` is set, and the
  settings page's "Files" section shows "N MB stored, about 0.02 credits a day" -- the **actual**
  rate (`metering.daily_storage_rate`, `describe_daily_rate`: fractions kept, "less than 0.01
  credits a day" below a hundredth), not `daily_storage_cost`, whose 1-credit minimum is the resume
  threshold only.
- **Rate limits**: `TODO_UPLOAD_USER` (120/min, `start`), `TODO_UPLOAD_COMPLETE_USER` (240/min,
  `complete`, charged **first** like Receipts' because each call is an S3 HEAD even for an object
  that never arrived), `TODO_DOWNLOAD_USER` (120/min) and `TODO_EDIT_USER` for removing and for cancelling an upload.
- **Files on comments.** A comment's add and edit forms have an "Attach files" control
  (`partials/comment_files_field.html`, `static/todo/js/comment_files.js`). A comment file is the same
  `Attachment` row with `for_comment` true, plus a nullable `Attachment.comment` FK (migration
  `0008_comment_attachments`): the browser uploads it as soon as it is picked, as a **draft** (`comment`
  still null; the same `start` / PUT / `complete` calls with `for_comment: true` in the `start` body,
  which also works on a *completed* task, since a completed task can be commented on, and refuses a
  trashed one), and the finished file becomes a chip holding a hidden `attachment_ids` input.
  Submitting the form posts those ids: `add_comment(task, user, body, attachment_ids)` /
  `edit_comment(comment, body, attachment_ids, remove_attachment_ids)` tie the drafts to the comment
  inside its transaction (`attachments.link_to_comment`: each must be an uploaded, unlinked comment
  file of the same task and owner, else the whole comment is refused with an `attachment_ids` error --
  all or nothing) and delete the files an edit removes, after the transaction (`remove_from_comment`:
  a storage error is a refusal; the text is already saved). A files-only change is not an edit of the
  text (no revision, no "(edited)"); a comment still needs text. Removing a chip before saving posts
  `POST /todo/<n>/attachments/<id>/discard/` (`todo:attachment-discard`, `TODO_EDIT_USER`, idempotent;
  a saved file is a 422), and the hourly `todo.cleanup_uploads` job also deletes drafts older than a day
  (`attachments.abandon_comment_files`). Files show under the comment text (`comment_files.html`, Download
  and, for a sniffed image or PDF, Open, through the existing download view); the edit form lists them
  with a "Remove" checkbox each. **The task's own Attachments list, `attachment_count` and the API's
  task `attachments` leave `for_comment` files out**; everything else is shared: billing and the
  daily metering, the frozen state, the per-task cap on files (a comment's files count toward it), the
  size limit, deleting with the task or user. Deleting a comment keeps its files, still billed, until it
  is restored or the task is deleted. No API endpoint adds files to a comment yet. The upload engine is
  `static/todo/js/uploader.js` (`TodoUploader.create`), which `attachments.js` (the task's block) and
  `comment_files.js` both use; the script is wired from `htmx:load` because htmx swaps the comments
  section after every action, and the form's submit button is disabled (and a submit stopped) while a
  file is still uploading. Tests: `tests/test_comment_files.py` and the Playwright
  `tests_e2e/test_todo_comment_files.py` (on the shared e2e server, whose To-do bucket is
  `site_fakes.TodoE2EStorage` at `/__e2e__/todo-s3/`).
- **Admin**: read-only `AttachmentAdmin` (task, filename, size, uploaded, inline type, user);
  `TodoAccountAdmin` also lists `stored_bytes` and the frozen state. The new `TodoSettings` fields
  are on the singleton page.
- **Tests**: `tests/test_attachments.py` (start/complete against the fake, sniffing, caps, frozen
  refusals, delete, Trash, purge, user deletion, download dispositions), `tests/test_metering.py`
  (the arithmetic, the daily rate text, freeze, email and notice, resume, the job's snapshot rule, the
  `credits_added` wiring), `tests/test_attachment_views.py` (the JSON views, 422/429/404, `whole_number`, the
  banner, the settings line), `tests/test_storage_and_config.py` (keys, the SigV4 client, `todo.W002`,
  prod refusing to start) and `apps/core/tests/test_sniff.py`.

#### Reminders

Issue #182 (`models/reminder.py`, `services/reminders.py`, `views/reminders.py`, `forms/reminder.py`,
`templates/todo/partials/reminders.html`, `templates/todo/emails/reminder.{txt,html}`, migration
`0005_reminders`).

- **Models.** `Reminder` (UUID id, `task` FK `related_name='reminders'`, `minutes_before` 0 to
  `MAX_REMINDER_MINUTES` (43,200, 30 days), `channel` `in_app`/`email`/`both`, `fire_at` the UTC
  instant it is due, `sent_at` null until delivered or swept, `skipped` true when it was marked
  sent without a delivery, `fire_token` a UUID regenerated at every claim (what the email task
  dedupes on) and `failures` the deliveries that raised since it was last armed; unique `(task,
  minutes_before)`, at most `MAX_REMINDERS_PER_TASK` (10); one **partial index** on `fire_at`
  `WHERE sent_at IS NULL` (`todo_reminder_unsent_fire`: all the job ever looks up, and it stays as
  small as the reminders waiting, however many have been sent)).
  `ReminderDelivery` is the **history**, one row per channel actually used (`reminder` and `task`
  `SET_NULL`, `user` `CASCADE`, `task_number` and `task_title` copied at delivery so it survives a
  purged task, `channel` `in_app`/`email`, `note` `unconfirmed` (an email skipped for an
  unconfirmed address), `failed` (every email backend failed, or the email could not be queued) or
  `skipped` (the reminder was never delivered: see "Delivery"), `sent_at`, `fire_at`,
  `notification_id` of the core notification when in-app, `fire_token` of the firing); it does
  not depend on the notification purge. `TodoAccount.default_reminder_minutes` (null = new tasks get none, default 5) and
  `default_reminder_channel` (default `in_app`), both with a `db_default` (the #102 lesson).
- **Computation.** `fire_at_for(task, minutes_before, account)` is `dates.due_at` (the task's time,
  or the account's `default_due_time` for a date-only task, on the owner's wall clock, #177) minus
  the offset. **An offset of whole days (1440 minutes or a multiple, so "1 day before" and "1 week
  before") is taken on the wall clock**: the due local date minus N days at the same local time,
  then converted to UTC, so across a clock change it stays at the same hour of the day (due 1
  Nov 2026 09:00 in Chicago, "1 day before" is 31 Oct 09:00 CDT, 25 elapsed hours earlier, where
  24 elapsed hours would give 10:00 CDT). **Minute and hour offsets are real elapsed time.** `None`
  without a due date. The tests cross Chicago's 1 Nov 2026 change for both kinds.
- **Defaults.** `create_task` calls `reminders.apply_defaults` for every source once the row exists
  (a task with a date and no reminders gets `default_reminder_minutes` in `default_reminder_channel`),
  and so does a task that first *gains* a date (`update_task`, `postpone_task` on a task without
  one; `reminders.on_due_changed(task, had_date=...)`). A task that had a date and lost its
  reminders on purpose does not get the default back. A completed task is never given one.
- **Adding and changing** (`add_reminder`, `update_reminder`, `delete_reminder`; refusals are
  `ValidationError`s: `'Set a due date first.'`, `'There is already a reminder at that time.'`, the
  cap, a time outside 0..30 days, an unknown channel, `'Only an open task has reminders.'`). The task row
  is locked, so racing adds can't pass the cap together. A reminder is created with `sent_at`
  null even when its time has passed, so one added a little late fires at the next job run; one
  more than `LATE_GRACE` (24 h) overdue is created already sent and `skipped`, with a `skipped`
  history row. Editing the time recomputes `fire_at` and arms it again (`sent_at` null, same
  `LATE_GRACE` rule, `failures` reset); editing only the channel leaves a sent reminder sent.
- **Rescheduling.** Two kinds, on purpose:
  - *The user changed the due moment* (`update_task` when the date or the time changed,
    `postpone_task`, `reschedule_overdue`; `on_due_changed` -> `reschedule(task, rearm=True)`):
    **every** reminder of the task is recomputed, sent ones too, and a sent (or skipped) one whose
    new `fire_at` is ahead of now is **armed again** (`sent_at` null, `skipped` false, `failures`
    0), so a postponed task reminds the user for its new date, and "reschedule all overdue to
    today" (`reminders_of_tasks` before the move, `move_reminders` after: one query to load and
    one `bulk_update`, however many tasks) doesn't silently drop every reminder. A sent one whose
    new time is not ahead stays sent (with the new `fire_at`).
  - *The same due moment, a different instant* (`reschedule_user(user, untimed_only=)`): the
    `timezone_changed` receiver (`apps.py`, `weak=False`, `dispatch_uid`) and
    `update_default_due_time` (only when the time changed: the user's tasks with a date and no
    time) each queue the `default`-queue task `reschedule_user_reminders(user_id, untimed_only)`
    through `enqueue_after_commit`, never run in the request (a user can have thousands of
    tasks). They touch **unsent reminders only and never re-arm**: a re-arm there could fire a
    reminder twice for one due moment. They include completed and trashed tasks, so restoring one
    later finds its reminders already right.
  - **Clearing the due date deletes every reminder of the task** (`fire_at` can't be empty; the
    details form asks first, `tasks.js`: "Clearing the date removes its N reminders").
- **Recurrence.** `reminders.copy_extras` (called by `recurrence.copy_extras`) first deletes the
  reminders `create_task` gave the new occurrence by default, then copies each of the completed
  task's (`minutes_before`, `channel`, `fire_at` recomputed for the new date, `sent_at` null): the
  occurrence has exactly the reminders its predecessor had, none if the user removed them all.
- **Delivery.** `todo.send_reminders` (every minute, lease 5 minutes; `jobs.send_reminders`) calls
  `deliver_due(now)`. It first sweeps (`sweep_stale`, in batches under row locks): an unsent
  reminder more than `FIRE_WINDOW` (1 h) overdue is marked sent **and `skipped`** without a
  delivery (a long-dated edit, a job that was down), and, when its task is open and its owner
  active, gets a `ReminderDelivery` with `note='skipped'` so the history says why nothing came
  (a completed or trashed task or an inactive owner is only flagged: there was nothing to miss, and
  a row per reminder would bury the history). The task panel and the history show "Skipped" where a
  delivered one says "Sent". Then, in batches of 200 under `select_for_update(skip_locked=True,
  of=('self',))`, the reminders that are unsent, due and **whose task is open and owner active**
  (the query filters these, so a reminder of a completed or trashed task or an inactive user stays
  unsent, and uncompleting or restoring inside the window makes it live again; the sweep marks it
  sent once it is older than the window). Each is claimed with `UPDATE ... WHERE sent_at IS NULL`
  (the compare-and-swap, a lost claim delivers nothing; the claim also writes a new `fire_token`)
  and delivered in a savepoint of its own: a failure is left for the next run (claim included, until
  the sweep ends it an hour after its time) and counted in `Reminder.failures`, which is stored so it
  holds across runs and runners. **Only the first failure logs a traceback**; later ones log a
  one-line warning with the failure number, so a reminder that always fails doesn't log a stack
  every minute. **Because the job's window (1 h) is much shorter than `LATE_GRACE` (24 h), a reminder
  added between 1 and 24 hours late is created unsent but swept at the next run without a
  delivery; `LATE_GRACE` only decides whether such a row is created already sent.**
  - *In-app*: `notifications.notify(source='todo', title=task.title cut to 120, body='Due 28 Oct
    2026, 14:30', url=reverse('todo:task', ...))` (`Due today` for a date-only task due on the
    owner's today, `Due 30 Oct 2026` for a date-only one on another day, with a reminder set days
    ahead) and a `ReminderDelivery` with the notification's id. No `key`: nothing deletes the
    notice later.
  - *Email*: `send_reminder_email(reminder_id, fire_token)` (`default` queue, queued on commit by
    `_queue_email`; if queuing raises, it is logged and a `ReminderDelivery` with `note='failed'` is
    recorded, since the reminder is already marked sent) renders `todo/emails/reminder.{txt,html}` (autoescaped HTML; the text
    body has autoescape off; the description through `render_markdown(text, None)`, so no task
    links; an absolute `BASE_URL` link; the due date and time with the zone's name) and sends
    through `apps.core.services.email.send_email` **only to a confirmed address**; the subject is
    `Reminder: <title cut to 80>` and carries nothing else. The delivery row is written by the
    task, either way (`note='unconfirmed'`, or `failed` when every backend failed). The footer says
    "You set this reminder on <task>. Change or remove it on the task page; your default for new
    tasks is in To-do settings.", linking both with absolute `BASE_URL` URLs (the settings only
    change the default for *new* tasks, so it doesn't promise to switch existing reminders off). **The
    dedupe is per firing, not per instant**: it sends nothing when the reminder is gone, the task is
    no longer open, the owner is inactive, the reminder's current `fire_token` is not the one it was
    queued with (it has been claimed again since) or an email row with that token already exists (a
    duplicate run). A reminder armed again therefore emails again, even at the same `fire_at`.
  - `both` does both. There is no rate limit on delivery (the system's own job).
- **Web.** The task page's panel has a "Reminders" block (so each action answers the whole
  panel): each reminder as `5 minutes before (in-app)` (`describe_offset`: the largest of
  days, hours, minutes that divides evenly; `At time of task` for 0) with the time it fires or
  fired in the user's zone, an inline Edit (amount, unit, channel) and Remove; the "Add reminder"
  row (presets At time of task, 5 min, 15 min, 1 hour, 1 day, 1 week, Custom with amount and unit
  minutes/hours/days) with the channel defaulting to the account's; "Set a due date to add
  reminders." without a date; read-only for a completed task; and the task's last 20 deliveries
  under "History". `POST /todo/<n>/reminders/` (`todo:reminder-add`), `.../reminders/<id>/edit/`
  and `.../delete/`, all `TODO_EDIT_USER`, 422 into `#reminder-msg`, a task or reminder that isn't
  the user's (or a trashed task) a 404. `todo:reminder-history` (`/todo/reminders/history/`, linked
  from Settings and the task page): every delivery of the user, 50 per page, newest first, each
  showing `#task_number` and `task_title`, linking to the task while it exists and "task deleted"
  after. Rows show a bell for a task with reminders (`with_reminder_flags`, an `Exists`
  annotation added in `views.lists.decorate`). Settings: the "Default reminder" (no reminder, or an
  amount and a unit) and "Default channel" join the due-time form (one `SettingsForm`; a post
  without `default_reminder_mode` leaves the reminder default alone, and both settings are saved
  together or not at all).
- **Admin.** Read-only `ReminderAdmin` and `ReminderDeliveryAdmin`.
- **Tests.** `apps/todo/tests/test_reminders.py` (the fire time across zones and a clock change,
  defaults for each source, the rules, rescheduling on every trigger including a zone change,
  carrying over, re-arming, a real Chicago clock change for day and hour offsets, delivery, the
  claim, the skips, the stale window, email and its dedupe, failure logging, the job, the admin, the
  index, the pages and 404s, the history and the settings) and TODO-21 in `tests_e2e/ai/todo.md`.

#### AI date parsing

Issue #183. As the user types "pay rent every month on the 1st at 9am" the task form shows a chip
("Sun 1 Nov, 09:00 · Every month on the 1st"); accepting it fills the date, time and repeat and
strips those words from the title. The model is the one Receipts uses, through a client factory
shared in `core`.

- **Shared client** (`apps/core/services/ai.py`, tests in `apps/core/tests/test_ai.py`):
  `is_configured()` (`OPENAI_API_KEY` set), `build_openai_client(*, timeout, max_retries)` (an
  `openai.OpenAI` for `OPENAI_BASE_URL` / `OPENAI_API_KEY`, imported inside the function) and
  `unwrap_json(raw)` (one code fence, a one-item list and a JSON string around the object, then
  `json.loads`; raises `json.JSONDecodeError`, and returns whatever JSON it finds, unvalidated).
  Receipts' `OpenAIReceiptClient` and `ai._parse` call them (it keeps its own error types, its
  logging of a failed reply and `is_configured`, which also needs the model); its behaviour and
  tests did not change. Products may not import each other, so only this much is shared: the prompt,
  the pydantic model, the errors and the process-wide client are each product's own.
- **Settings.** `OPENAI_TODO_MODEL` (`os.getenv('OPENAI_TODO_MODEL') or OPENAI_RECEIPTS_MODEL`),
  `TODO_AI_TIMEOUT_SECONDS` (6: someone is waiting, and a late answer is worth nothing) and
  `TODO_AI_MAX_RETRIES` (0). They are short because the call holds a **sync gunicorn worker** (3 per
  container, 30 s `timeout`) for as long as it lasts: a 15 s timeout with a retry could outlast the
  worker timeout, get the worker SIGKILLed (taking its scheduler thread down mid-job, whose lease
  then stays held) and, with three people typing while the provider is slow, stop the whole site
  from answering. **Not** in
  the prod required-secrets check: without a key the feature is simply off (no chip, no error).
  `apps.todo.checks` warns `todo.W001` when a key is set but no model resolves (a Warning, never an
  Error: `migrate` runs the checks). `.env.example` documents `OPENAI_TODO_MODEL`.
- **Parser** (`services/parsing.py`; no migration, nothing is stored). `AIClient.parse(ParseRequest)
  -> ParsedTitle`; `OpenAITitleParser` implements it; `get_client()` / `set_client()` are
  process-wide like Receipts' (`PermanentAIError` when the key or model is missing; tests use the
  autouse `FakeTitleParser` of `apps/todo/tests/fakes.py`, installed by the `fake_title_parser`
  fixture in `apps/todo/tests/conftest.py`, so the suite never reaches the network).
  `ParseRequest` is the title, the user's local `today` and its `weekday`, the `timezone` name, the
  `default_time` (the account's default due time) and `now_local`. `ParsedTitle` (pydantic,
  `extra='ignore'`) is `has_date`, `clean_title`, `due_date`, `due_time`, `rrule`, `matched_text`
  and `confidence`, and re-validates what the model said: an unreadable date or time is dropped, a
  `due_date` before 2000 or more than 10 years after the user's today is dropped, `due_time` is
  rounded to the minute, an `rrule` that `recurrence.parse_rule` refuses is **dropped, not an
  error** (and an accepted one is stored canonical), a rule with no date gets
  `recurrence.first_date`, a time without a date is dropped, `has_date` ends up false when nothing
  usable is left, `clean_title` has its whitespace collapsed and falls back to the original title
  when empty (the title and the user's today go in as pydantic validation context), `confidence` is
  clamped (a reply without one counts as 0, so it shows no chip). `_SYSTEM_PROMPT` tells the model today's date and weekday, the zone, the time now and
  the default time, to extract only what is written, to leave `due_time` null unless a time is
  written, to express repeats with only `FREQ`, `INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`,
  `COUNT` and `UNTIL`, and never to invent a date; the title goes in the user message as data. The
  call is `chat.completions.create(model=OPENAI_TODO_MODEL, temperature=0, max_tokens=300,
  response_format=json_schema from ParsedTitle, strict=False)`. Any exception (timeout, HTTP error,
  bad JSON, validation) is raised as `ParseUnavailable`; a reply that fails validation is logged
  at warning level as its **length and a short SHA-256 prefix, never any of its text** (it echoes
  the user's task title), and the exception raised carries only the error's type name
  (`from None`, since pydantic's message holds the values). `looks_dated(title)` is the cheap pre-check: a digit or one of
  `DATE_WORDS` (English and Portuguese words, compared accent- and case-insensitively through
  `normalize_for_search`), else no AI call and `has_date=false`; the list only saves calls, the
  model decides what a title means. `parse_title(request, *, charge=None)` ties it together (it
  asks `get_client()` first, so an unconfigured site answers the same whatever the title) and
  applies, in order: the **concurrency limit**, the **rate limits** (`charge`), the call, the
  **confidence** (under `MIN_CONFIDENCE`, 0.5, the answer is "no date" and there is no chip, as
  Receipts sends a doubtful reading to review) and **`safe_title`**. `chip_text` builds the
  label: `Today`, `Tomorrow`, `Mon 3 Nov` within the next 6 days, else `3 Nov` (`3 Nov 2027` for
  another year), `, 09:00` when timed and ` · <describe(rrule)>` when repeating.
  - **Concurrency.** `parsing._slot` is a module-level `threading.BoundedSemaphore(1)`, taken
    **without waiting** around the model call. A request that finds it taken gets `ParseBusy` (a
    `ParseUnavailable`) without the model being called and without spending any rate limit; the
    endpoint answers it like a transient failure. It bounds a process to one open call; with sync
    workers a worker serves one request at a time anyway, so there the real bound is the 6 s timeout
    with no retries, and the semaphore is what keeps a threaded server (`runserver`, a `gthread`
    deployment) from piling calls up.
  - **`safe_title`.** The model's `clean_title` is free text that Accept writes into the title
    input (where `maxlength` does not stop a script), so it is used only when it is no longer than
    the original, within `TITLE_MAX`, has a word and every word of it (compared through
    `normalize_for_search`: case, accents and punctuation ignored) is in the original. Anything else
    (rewritten, extended, injected, empty) keeps the original title. `matched_text`, which is only a
    tooltip, is kept only when it is a substring of the title (case-insensitive).
- **Endpoint** (`views/parse.py`): `POST /todo/parse/` (`todo:parse`), `@login_required`, CSRF
  protected, JSON in and out (like the upload views of Receipts). Body `{"title": ...}` (whitespace
  collapsed; empty or over `TITLE_MAX` is a 422 `{"detail": ...}` that charges nothing). Answers 200
  `{"available": true, "has_date", "clean_title", "due_date", "due_time", "rrule", "rrule_text",
  "matched_text", "label"}`; `{"available": false}` when the AI is not configured (permanent: the
  page stops asking); or `{"available": true, "has_date": false, "retry": true}` when the call
  failed or the process is busy (transient: the page backs off). **Two rate limits, both on the
  per-user key and checked together** (the project's two-rule pattern, like `SIGNUP_EXISTING_EMAIL`
  and `_DAY`): `TODO_PARSE_USER` (60/min, a person typing never gets near it) and
  `TODO_PARSE_USER_DAY` (500/day, the ceiling on what a script can make the AI bill, since the call
  is free to the user). The view **peeks both** first (over either: 429 JSON through
  `ratelimit.json_response`, `Retry-After` of the longer wait) and **hits both only inside
  `parse_title`'s `charge` callback**, right before the model is called with the slot taken. So a
  title with no date in it (no call), an unconfigured site, a busy process and a refused request
  spend nothing, while a call that then fails or times out is charged. It saves nothing and spends
  no credits: the AI call is not charged on its own, a task has its price. The server never
  re-parses; the form posts the explicit `due_date`, `due_time`, `rrule` and `recurrence_mode`.
- **The chip** (`static/todo/js/title_parse.js`, `partials/parse_chip.html`, in `task_form.html`):
  a `data-title-parse` container around a text input (`data-parse-input`) and the chip
  (`data-parse-chip`). On `input`, the chip is hidden at once, and after a 600 ms pause, only when
  the text changed and has at least 3 characters, it is posted (an in-flight request is aborted by
  new input, `AbortController`). With `has_date` the chip shows the label (tooltip: `matched_text`)
  with Accept and Dismiss. **Accept** writes `due_date`, then `due_time` and the repeat **only when
  the reply has them** (a title without a time or a repeat leaves the form's own alone; the repeat
  goes through the Alpine component's `setRule`, `recurrence.js`, so its popover and label follow;
  the mode stays what the form has), replaces the title with `clean_title` and hides the chip.
  **This is a deliberate deviation from the issue**, which says Accept writes `due_date`,
  `due_time` and `rrule`: accepting "tomorrow" after picking 18:00 by hand, or typing it into the
  date box of a repeating task, should move the date, not silently drop the time or stop the repeat.
  To keep the chip honest it **says what stays**: when the form already has a time the reply lacks,
  or a repeat the reply lacks, the label gets ` (time kept)`, ` (repeat kept)` or ` (time and repeat
  kept)` (`labelFor` in the script; it follows the form if it changes while the chip is up).
  **Dismiss** hides it and remembers that text (not parsed again; other text is). Submitting with
  the chip showing does not apply it. The chip is keyboard-reachable: Tab to its buttons, Enter on
  the focused chip accepts, Escape dismisses. The task page's details form has the same script
  on an "Or type a date" box (`data-mode="date"`): `clean_title` is ignored, only the date fields
  are applied, the box is emptied, Enter reads it at once and "No date found." is its answer for
  nothing. **Failure handling** (`title_parse.js`): `available: false` (not configured) stops the
  script for the rest of the page load; a transient failure (`retry: true`, a non-2xx answer other
  than 429, a network error) only **backs off**: nothing is asked for 60 s, then the text then in
  the box is asked about; a 429 with a `Retry-After` up to 60 s is waited out silently, and a longer
  one (the daily limit) stops the script for the page, silently, like `available: false`. Neither
  shows any message. A text is marked **handled** (not asked again) only once its answer was
  applied, after the "user kept typing" check, so editing back to a text that never got an answer
  asks again. Each box gets `data-parse-ready="1"` once all its listeners are in place (the
  script is `defer` and wires from `htmx.onLoad`), which the browser tests wait for before typing.
- **Tests.** `apps/todo/tests/test_parsing.py` (the pre-check, `ParsedTitle`, the prompt, the
  OpenAI call with `openai.OpenAI` mocked, the failure paths, the chip text, the endpoint, rate
  limits, the concurrency slot, `safe_title`, confidence, log content and the page wiring),
  `apps/core/tests/test_ai.py`, and the Playwright `tests_e2e/test_todo_parse.py` (accept,
  dismiss/Escape, submit with the chip showing, the date box, the "kept" wording) on the shared e2e
  server, whose `receipts_e2e.E2EConfig.ready` installs a `FakeTitleParser` that understands two
  titles (`tests_e2e/todo_fakes.py`).

#### Search and saved filters

Issue #181 (`services/search.py`, `services/filters.py`, `services/saved_filters.py`,
`models/saved_filter.py`, `views/search.py`, `views/filters.py`, migration `0006_saved_filters`).

- **What is searched.** Three stored, normalised copies (`apps.core.markdown.normalize_for_search`: NFKD,
  accents stripped, case folded, punctuation turned into spaces, formatting gone): `Task.title_text`,
  `Task.description_text` and `Comment.body_text` (empty for a deleted comment, rebuilt on an edit or a
  restore). **Not searched:** deleted comments, comment revisions (they have no search column), tasks in the
  Trash, other users' tasks; completed tasks only with `include_completed`. A query is normalised the same
  way, so `Cão`, `cao` and `Pay the RENT!` find what they should. **The task number too:** a glob query
  that is only a number (`1000` or `#1000`, ASCII digits, up to 9; `PreparedQuery.number`) also matches the
  task with exactly that number (`search_condition(pattern, number)`, so `filters.FilterParams.condition`
  and the saved-filter counts do as well), next to everything whose text contains it. It is an exact
  match (`100` does not find #1000), not applied to wildcards, text around it or regex mode, and a task
  found only by its number has no snippet.
- **Glob (the default) and regex.** `prepare_query(q, mode)` validates and compiles. In glob mode the query goes
  through `normalize_glob` (`normalize_for_search` except `*` and `?`, which stay wildcards; a separator next
  to a wildcard keeps one space, so `pay *` still means "pay", a space, anything; **a run of `*` is
  collapsed into one** before `glob_to_regex`, so `a*****b` can't make a backtracking-heavy `.*.*.*...`),
  then `glob_to_regex` (`re.escape`, then `\*` -> `.*`, `\?` -> `.`). The pattern is **unanchored**: no wildcard
  is a substring match (`rent` finds `parent`). A query wrapped in slashes (`/.../`, at least one character
  between) is a regex whatever `mode` says; in regex mode the pattern is used as written **except that its
  literal characters are lower-cased** (`lower_literals`: the text it meets is lower-case, so `/Rent/` finds
  "rent"; safe because only an escape's letter is case-sensitive -- `\D` is not `\d` -- and escapes are copied
  untouched; a character whose lower case isn't one character is kept). It is still matched against the
  *normalised* text (no accents, punctuation as spaces); the help popover says both. `q` is at most `MAX_QUERY_CHARS` (200;
  `'The pattern is too long.'` in either mode: the slashes of `/.../` don't count). Refusals are
  `ValidationError`s: empty after normalisation `'Type something to search for.'` (a lone `*` is not empty),
  an invalid regex, a backreference (`\1`..`\9`, `(?P=name)`, `(?(1)..)`) or a lookaround (`(?=`, `(?!`,
  `(?<=`, `(?<!`), or Python-only syntax PostgreSQL's engine rejects -- atomic groups `(?>...)`, possessive
  quantifiers (`a*+`, `a++`, `a?+`, `a{2}+`), named groups `(?P<n>...)`, `\N{...}` -- is refused with
  `'Invalid regular expression.'` rather than the database's "took too long or is not valid" (the dialect is
  what Python's `re` and PostgreSQL's engine share; found by scanning with escapes skipped and bracket
  expressions read as such -- a `]` first in a class is a literal -- so `\\1` and `[(?=]` are fine).
  Nested quantifiers are not detected statically. **`\b` and `\B` are translated to `\y` and `\Y` for PostgreSQL only** (`to_postgres`,
  `PreparedQuery.db_pattern`; in PostgreSQL `\b` is a backspace; a `]` first in a bracket expression, `[]a]` or
  `[^]a]`, is a literal and doesn't end it); the snippets use Python's own pattern.
- **The query** (`search_queryset`): the user's non-trashed tasks, `title_text__regex` OR `description_text__regex`
  OR an `Exists` over the user's non-deleted comments with `body_text__regex`. `__regex`, never `__iregex`
  (`~` on PostgreSQL, a Python `REGEXP` function on SQLite, which has no timeout); the pattern is always a
  parameter. **Judgement call:** the issue wrote the comments as a join with `.distinct()`; an `Exists` is the
  same set without repeating a task per matching comment and without multiplying the subtask counts a list
  annotates (the labels filter is an `Exists` on the through table for the same reason).
- **The timeout** (`search_guard`, a context manager every evaluation of a search runs in): on PostgreSQL
  (`connection.vendor`) a `transaction.atomic()` with `SET LOCAL statement_timeout = '1000'`; an
  `OperationalError` whose message contains `statement timeout`, or a `DataError` (a pattern the server
  rejects), becomes `'That search took too long or is not a valid pattern. Simplify it and try again.'`;
  any other `OperationalError` propagates. `search_queryset` / `filter_tasks` validate when called but run
  when evaluated, so callers evaluate inside the guard (`paginate`, `attach_matches` and the counts do).
  **`SET LOCAL` lasts until the outermost transaction ends**, and releasing a savepoint keeps it, so a guard
  nested in someone's `atomic()` would leave a 1 s limit on everything after it. The guard therefore never
  assumes it is outermost: it reads the value it replaces (`SELECT current_setting('statement_timeout')`) and,
  before leaving *normally*, puts it back with `set_config('statement_timeout', previous, true)` (also local),
  in the same savepoint. When it leaves with an error the savepoint/transaction is rolled back, which reverts
  a `SET LOCAL` made inside it (so nothing is restored by hand, and a restore could not run in an aborted
  transaction anyway). Chosen over asserting `not connection.in_atomic_block`, which would also trip under
  pytest-django's wrapper transaction and would forbid a legitimate caller. Tested with a mocked PostgreSQL
  connection (the statements issued, nested in an `atomic()`, after an error).
- **The snippet pass is bounded** (review of #196: **the database's engine does not protect Python's**.
  PostgreSQL's matcher answers `a.*a.*a.*a.*c` -- the glob `a*a*a*a*c` -- or `(a|aa)+$` at once, so the 1 s
  timeout never fires, while Python's backtracking `re` spent minutes on the same 500-character title; the
  title can be a task's own text, up to `TITLE_MAX`, and gunicorn's 30 s worker timeout is the only other
  bound, with 60 searches a minute allowed per user). Snippets therefore use the third-party **`regex`
  package** (a runtime dependency, used here only), whose `Pattern.search(text, timeout=...)` raises
  `TimeoutError`: the same Python-dialect pattern, compiled with `search.snippet_regex` (a pattern it can't
  compile means no snippets). **One time budget for the page**, `SNIPPET_TIME_BUDGET` (0.2 s) from the moment
  `attach_matches` starts; each search gets what is left as its `timeout`; **the first timeout, or a spent
  budget, ends the snippets: the remaining rows are still listed, without a snippet or a field** (they match;
  only the highlight is missing). **Only the field the database said matched is scanned**: `_matched_texts`
  asks the database, for the page's rows only and under the search guard, which of title and description
  matched (a `Q(...__regex)` annotation each), and then, for the tasks that matched in neither, for the
  **first matching non-deleted comment** (a `Subquery` ordered by `created_at`, `Substr`-ed to the scan cap)
  -- one query for them all, never the bodies of every comment of the page. A title that did not match is
  never read, however hostile. **At most `SNIPPET_SCAN_MAX` (5,000) normalised characters of a field** are
  scanned (a match beyond them is listed without a snippet). If the database refuses those queries (a
  timeout) the page has no snippets. The other places a user's pattern meets Python's `re` -- `re.compile` in
  `prepare_query`, `_has_unsupported_syntax`, `lower_literals`, `to_postgres`, `normalize_glob` -- compile or
  scan the (at most 200-character) pattern once, linearly; nothing there *matches* user text. **SQLite's
  `REGEXP` is Python's `re` too, so the development/test database has no bound inside the query** (it is not
  a deployment target); the regression tests (`TestSnippetBounds`) stand in PostgreSQL's linear-time answers
  for the three hostile patterns (`a.*a.*a.*a.*c` against a 500-`a` title whose short description matches,
  `(a+)+b`, `(a|aa)+$` against a field that matches and backtracks) by replacing SQLite's `regexp` function
  and assert they finish in a couple of seconds with the row returned (and no snippet where the matching field
  is the hostile one).
- **Results.** `order_results`: the foundation's sort (`FilterParams.sort`), completed tasks after open ones
  (a `Case` prepended to the order), 50 per page (`paginate`: a `Paginator` on the ordered query, then the
  page's rows re-loaded through the lists' `decorate` so counts, labels and parent are there). Each task
  carries `search_field` (`title`, `description`, `comment`, or `''`) and `search_snippet`: the first of
  title, description and the first matching non-deleted comment (oldest first) the `regex`-compiled pattern
  finds in, within the budget above, computed in Python on the **normalised** text, `SNIPPET_CONTEXT` (60) characters either side, the match in `<mark>`
  (at most 100 characters of a long match; everything escaped), shown as "Title: ... rent ..." under the
  row and labelled "matches shown without accents or formatting". `search_tasks(user, *, q, mode,
  include_completed, filters, page, decorate)` is the issue's function (the explicit `q`, `mode` and
  `include_completed` win over the filters' own); `filters.filter_page(user, params)` is the same for a
  `FilterParams`.
- **Structured filters** (`FilterParams`, frozen): `q`, `mode`, `include_completed`, `priorities` (a subset of
  0..3, any of them), `labels` (the user's ids sorted, and `none`; **any of them**), `date_range` (`any`,
  `overdue` (open, before today), `today`, `next_7_days` / `next_30_days` (**today through today + N**,
  inclusive, overdue left out), `no_date`, `custom` with `date_from` / `date_to`, either open-ended; presets
  are resolved against `user_today` on every run, so a saved "Next 7 days" stays relative) and `sort`.
  `from_query` / `from_dict` (`parse_filter_params(query, user)` also drops label ids that aren't the user's)
  read **leniently** (bad values dropped; lists accept repeats and commas; dates without a range mean
  `custom`, an explicit `date_range=any` wins over dates), `to_query()` writes the canonical query (defaults
  left out, lists sorted; the canonical URL is `/todo/search/?<to_query()>`), `to_dict()` the full dict a
  `SavedFilter` stores. `has_criteria` is text, priorities, labels or a date range: the sort and the completed
  switch alone select nothing. `filter_tasks(user, params)` is the queryset (open tasks without `q`;
  `ordered=False` for counting); `describe_params(params, names)` the summary
  (`priority 1 · @home · next 7 days · "rent*"`).
- **Saved filters.** `SavedFilter` (UUID id, `user` with `related_name='todo_saved_filters'`, `name` 50 unique
  per user ignoring case, `params` JSON, `position`, `created_at`): `save()` stores `params` in its canonical
  form (parsed back through `FilterParams`) and `clean()` refuses one that isn't. Services: `save_filter`
  (name cleaned and unique, `MAX_SAVED_FILTERS` = 50 per user under the user's `TodoAccount` row lock, the search
  text validated, labels that aren't the user's dropped), `rename_filter`, `update_filter_params`,
  `delete_filter`, `reorder_filters(user, ids)`. Params are re-read leniently with the user's labels, so **a
  label deleted later simply drops out of the filter** (no cleanup hook in `delete_label`). Counts:
  `open_task_counts(user, {key: params})` counts the open tasks several filters select **in one query** -- one
  conditional `Count('pk', filter=params.condition(user))` per filter over the user's open tasks (completed
  never counted), under a single `search_guard` -- and gives `None` for a filter whose search can't run (an
  invalid pattern) and, **when the one query times out or the server rejects a pattern, `None` for every
  filter** (shown as a dash) rather than one timeout each; `filter_counts(user, filters)` keys it by filter id,
  `open_task_count` is the one-filter form. `FilterParams.condition(user)` / `structured_condition` are the
  filters as one `Q` (`narrow` is `queryset.filter(structured_condition)`), `search.search_condition` the
  text part. `filter_rows(user, filters)` (the filters page) is params + summary, with no count;
  `sidebar_filters(user)` is up to 20 `SavedFilter`s and how many more.
- **Web.** The search box (`q` with `autocomplete="off"`, **Regex** = `mode=regex`, **Include completed**, a `?`
  help popover) is above the menu in `todo/layout.html` (so at the top on phones) and the sidebar lists
  "Filters" below it with "and N more"; both come from `render_page`. **The counts are not part of the
  render** (review of #196: up to 20 counts, each its own regex scan, on every To-do page and 50 on the
  filters page): the sidebar renders an empty `#side-count-<id>` slot per filter and a hidden element that
  `hx-get`s `GET /todo/filters/counts/` (`todo:filter-counts`, `hx-trigger="load"`, `hx-swap="none"`), whose
  response is out-of-band `<span hx-swap-oob>`s for the slots -- the sidebar's first 20, and with `?scope=all`
  also `#page-count-<id>` for every row of the filters page, whose list is the one that asks (once; the
  sidebar's own request is left out on that page, and the re-rendered list after a rename asks again). One
  query for all of them, `204` for a user with no filters, a dash (with a screen-reader "count not
  available") for a count that is `None`. `GET /todo/search/`
  (`todo:search`) is the page: the form, the filter bar and `#search-results`; every change `hx-get`s
  `/todo/search/list/` (`todo:search-list`; opened directly it redirects to the page) whose `HX-Push-Url` is the
  canonical page URL; a refusal is a 422 line in `#search-msg` (a full page with status 422, or the partial
  retargeted). Nothing selected (no criteria) runs no query and spends no budget. The results use
  `task_row.html` with `view='search'` (`belongs` keeps a completed row; `_view_of` accepts it). "Save as
  filter" is a small Alpine dialog posting the canonical params as hidden inputs to `POST /todo/filters/`
  (`todo:filters`, which is also the filters page on GET): success is a flash message and `HX-Redirect` to
  `/todo/filters/<id>/` (`todo:filter`), a refusal a 422 in `#filter-save-msg`. A filter's page is the search
  page headed by its name with the filter's params applied (any filter key in the query string, `sort`
  included, means "edited": `sort` is written into every URL pushed there so that clearing everything isn't
  read as "as saved"); **Update filter** (`POST .../update/`) appears when the params differ from the saved
  ones; a filter shown as saved always runs, even if its label was deleted and it now selects "all open
  tasks". `/todo/filters/` lists them (summary, count, inline rename `.../rename/`, delete through
  `confirmModal` `.../delete/`, drag to reorder `filters/reorder/` with SortableJS and the shared `tasks.js`).
  Another user's filter is a 404.
- **Rate limits.** `TODO_SEARCH_USER` (60 per minute) is charged by the search page, its list partial and a
  filter's page, **before** the query, only when one runs (a refused pattern still spends it), and by
  **`GET /todo/filters/counts/`, once per call whatever the number of filters** (so each To-do page load
  spends one while the user has saved filters; over the limit the counts are dashes, with no query, never an
  error). The pages themselves, the filters page included, spend nothing. The filter mutations (save, rename,
  update, delete, reorder) spend `TODO_EDIT_USER`; `reorder` refuses more than `MAX_SAVED_FILTERS` ids (422
  for htmx, before any query).
- **Admin.** Read-only `SavedFilterAdmin`. **Tests.** `test_search.py` (`glob_to_regex`, normalisation,
  refusals, what is and isn't searched, snippets and their bounds (`TestSnippetBounds`), the PostgreSQL branch with
  `search.connection` replaced by a mock (the guard's statements, nested in an `atomic()`, after an error) and
  the SQLite branch for real, `FilterParams` parsing and applying, the views, the search rate limit),
  `test_saved_filters.py` (model, services, the one-query counts and their timeout, the pages and endpoints, the
  counts endpoint, 404s, the sidebar, rate limits, admin), TODO-22 and TODO-23 in `tests_e2e/ai/todo.md`.

#### Email intake

Issue #186 (`models/inbox.py`, `services/inbound.py`, `views/inbox.py`, `jobs.py`,
`management/commands/todo_ingest_eml.py`, migration `0007_email_intake`). Each user has an address
`todo+<code>@ziptrigo.com`; the subject becomes the title, the body the description, the email's
attachments the task's attachments, and the AI reads a due date from the subject. It is Receipts'
intake ("Receipts" > "Email intake": SES to S3, a poller, claim and lease, parked failures) for the
`todo/inbound/` prefix, on the mail code shared in `apps.core.services.mail` ("Mail parsing"); read
`services/inbound.py`'s module docstring for the long version.

- **Settings and storage.** `TODO_INBOUND_ADDRESS` (`todo@ziptrigo.com`, `todo-staging@...` on
  staging; **not** a required prod secret: it has a default; the bucket and the `TODO_AWS_*` keys it
  uses are the attachments' and already required). `services/storage.py`: `INBOUND_PREFIX =
  'todo/inbound/'` (where SES drops messages; the SES rule, the prefix's lifecycle expiry and the IAM
  scope are the `infra` repo, issue #185, not done when this was written), `FAILED_PREFIX =
  'todo/failed/'`, `failed_key_for`. `TODO_REGENERATE_USER` is `(5, 3600)`.
- **Models** (`models/inbox.py`). `InboxCode` (FK `account` -> `TodoAccount`, `related_name='codes'`;
  8 characters from Receipts' alphabet without look-alikes; `active`, `retired_at`; `code` unique
  across all rows, retired ones kept forever; a partial unique constraint allows one active per
  account; `InboxCode.issue(account)`). `TodoAccount.sender_allowlist` (JSON list, `db_default=[]`).
  `InboundMessage` (one row per S3 object; `status` `pending`/`done`/`failed`, `attempts` as claim
  token, `next_attempt_at` lease/retry time, `queued_at`, `failed_key`, `last_error`) and
  `InboundEmail` (one per matched recipient: `user` with `related_name='todo_inbound_emails'` --
  Receipts' is `inbound_emails` --, `recipient`, `from_address`, `envelope_sender`, `subject`,
  `received_at`, `sender_authenticated`, `status`, `note`, `task` FK `SET_NULL`; unique on
  `(message, recipient)`). `sender_authenticated` is whether SES authenticated the **From** address
  (`Verdicts.vouches_for` with only that address listed: DMARC for its domain, else an aligned DKIM
  signature, or SPF when it is also the envelope sender), recorded even with an empty allowlist,
  which accepts anyone. `Task.email_from` / `Task.email_from_authenticated` (real columns, with
  `db_default`s) copy the From address and that result onto a task made from an email, so the task
  page needs no lookup of the email.
  `InboundEmail.status`: `accepted`, `rejected_sender`, `rejected_spam`, `rejected_virus`,
  `rejected_credits`, `rejected_too_large` (files), `rejected_task_limit` (the open-task cap),
  `no_content` (no forwarding-confirmation status, and no
  `.eml` copy is stored). `TodoSettings.max_inbound_rows_per_day` (500, with a `db_default` too: the
  #102 lesson). The model names are the same as Receipts' (another app label), so a test that looks
  a permission up by codename (`view_inboundmessage`) filters by `content_type__app_label`.
- **Polling and retries** (`poll`, `ingest_message` on the `default` queue -- a To-do task, no new
  worker --, `ingest`, `reset_message_for_retry`, `purge_done_messages`): exactly Receipts'
  (`CLAIM_LEASE` 10 min, `MAX_ATTEMPTS` 3, `RETRY_BACKOFF` 1 and 5 min, `MAX_RAW_BYTES` 40 MB, a
  failed message's object parked under `todo/failed/`, `DONE_MESSAGE_RETENTION` 30 days, which must
  stay well above the 14-day expiry the infra repo puts on `todo/inbound/`). Jobs (`apps.py`):
  `todo.poll_inbound` (every minute, lease 2 min) and `todo.purge_inbound_messages` (daily); both let
  errors propagate to `ScheduledJob.last_error`.
- **Per recipient** (an *active* `InboxCode` of an active user; else logged and dropped), in this
  order, the first that applies deciding the status: SES spam `FAIL` -> `rejected_spam`; virus `FAIL`
  -> `rejected_virus`; the allowlist (empty accepts anyone with no authentication; otherwise `From` or
  the envelope sender must match **and** `Verdicts.vouches_for` must authenticate the matching
  address, exactly Receipts' rule and code) -> `rejected_sender`; nothing in it -> `no_content`
  (files that were all over the size limit: `rejected_too_large`); then, **before the AI call and the
  uploads**, a cheap look at the open-task cap -> `rejected_task_limit` ("Too many open tasks.") and
  at the credits and `creation_blocked_at` -> `rejected_credits`; then the task.
- **What becomes the task.** *Title*: the decoded `Subject` through `mime.clean_text(..., TITLE_MAX)`
  with leading `Fwd:`/`Fw:`/`Re:` (any case, repeated) removed; empty -> `(no subject)`. *Description*
  (`inbound.body_text`): the first non-attachment `text/plain` part of the message itself (as Markdown,
  unchanged), else the `text/html` one through `core`'s `html_to_text`, else empty; a message
  forwarded as an attachment is not searched for a body; quoted history is kept; control characters
  are dropped; capped at `DESCRIPTION_MAX`, a cut marked by a final line `…` (the result is still
  within the cap). The decoded body is **sliced right after decoding**, before any copy or clean-up
  (`MAX_TEXT_CHARS` = 2 x `DESCRIPTION_MAX` for `text/plain`, `MAX_HTML_CHARS` for HTML, whose
  markup is most of it), so a 30 MB body costs no more than a small one. *Attachments*: `mime.analyze(allowed_types=None, max_file_bytes=
  max_attachment_bytes, min_inline_image_px=INLINE_IMAGE_MIN_PX (150, `limits.py`), sniff=<inline
  sniffer>, image_dimensions=...)` -- any type, no body chosen, tiny `cid:` images (logos,
  signatures) skipped -- up to `max_attachments_per_task`; each is written with `put_object` at
  `attachment_key(user_id, attachment_id)` (the id a `uuid5` of message, recipient and part, so a
  retry overwrites instead of orphaning) and gets an `Attachment` row created `uploaded=True` with
  `inline_type` sniffed, its SHA-256, and its bytes added to `stored_bytes`. The email's `note` says
  what was left out: `'1 of 3 files skipped: too large'`, `'2 of 3 files skipped: 1 too large, 1 over
  the limit of N per task'`, and for a **frozen** storage account (the task is still created, no file
  is stored) `'files skipped: storage is paused'`. *Due date*: `parsing.parse_title` on the title
  (`looks_dated` first, so a subject with no digit or date word makes no AI call), outside any
  transaction and after the cheap refusals above; the result applied when `has_date`, with
  `clean_title` as the title (and the rule as a repeat); `ParseUnavailable`, `PermanentAIError` (not
  configured), an unexpected error or the user's `TODO_PARSE_USER`/`_DAY` limits (spent through
  `charge`, as on the web) leave the task undated. That is **the same per-user AI budget as the web
  quick add** (deliberately one budget: with an empty allowlist a stranger who knows the address can
  use some of it, and past it the task is just created undated). A date `create_task` refuses is
  dropped rather than losing the task.
- **Creation and billing.** `services.tasks.create_task(user, title=..., description=...,
  source='email', allow_unpaid=False)` -- so it counts toward the free allowance and costs what any
  task does. `InsufficientCreditsError`, or `BLOCKED_MESSAGE` (`creation_blocked_at`), is
  `rejected_credits`: nothing is stored (the uploaded objects are deleted again), and the user is told
  in-app by **one coalesced notice per user**: a fixed text ("Emailed tasks were not created: not
  enough credits", linking to `billing:credits-history`; nothing the sender wrote, subject included,
  reaches it) under the stable key `todo_email_credits`, written as `delete_for_key` then `notify`
  in its own savepoint next to the email row (a failing notice is logged, never loses the row), so a
  flood of rejected emails leaves a single, fresh notice and the rows ceiling below still bounds the
  writes. The open-task cap (`TOO_MANY_MESSAGE`) is `rejected_task_limit` with the note "Too many open
  tasks." -- normally from the cheap check before the AI call and the uploads, and from `create_task`
  under its lock when a race gets past it (a rejection, not a failing message).
- **One transaction per recipient.** The objects are uploaded first, outside any transaction; then
  one `atomic()` creates the task, the `InboundEmail` row (so "the row exists" means "done" and a retry
  skips it), the attachment rows and the `stored_bytes`; if it fails the objects are deleted again
  (unless another worker finished the same recipient meanwhile and the row exists). The email id is
  a `uuid5` too.
- **Rows ceiling.** An email that creates no task (every rejection, `no_content`) gets its
  `InboundEmail` row only while the user has fewer than `max_inbound_rows_per_day` rows from the last
  24 hours; else it is only logged. Accepted mail is never dropped by it.
- **Addresses** (`services.accounts`): `inbox_address(code)` / `inbox_address_for(user)`
  (`todo+<code>@<domain of TODO_INBOUND_ADDRESS>`; the first code is issued on first use, since
  accounts predate the intake; two first visits at once re-read the winner's code on the
  constraint's `IntegrityError`), `regenerate_inbox_code(user)` (locks the account row, retires the
  active code, issues a new one; a lost race returns the code that is active, never an error), `update_inbox_settings(user, sender_allowlist=...)` through the shared
  `addresses.parse_allowlist` (errors keyed `sender_allowlist`).
- **Web.** The settings page's "Add tasks by email" section (`templates/todo/settings.html`, outside
  the `#settings-form` that one htmx form re-renders): the address with the shared `copyText`
  component, Regenerate through the shared `confirmModal` (`POST /todo/settings/regenerate/`,
  `todo:regenerate`, `TODO_REGENERATE_USER`, a flash message and a redirect), the accepted senders
  (`POST /todo/settings/inbox/`, `todo:settings-inbox`, Anyone / Only these over a textarea of
  addresses and `*@domain`; a refusal is a 422 retargeted at `#inbox-settings-msg`; spends
  `TODO_EDIT_USER`) and the 20 most recent inbound emails (from, subject, received, outcome, note,
  a link to the task). The task page shows "Created from an email from <from address>" for
  `source='email'` only when SES authenticated that address (`Task.email_from_authenticated`), else
  "Created from an email claiming to be from <from address>" (no lookup: both are on the task).
- **Admin.** `InboundMessageAdmin` (status, attempts, last error; the "retry" action needs
  `todo.change_inboundmessage`, `reset_message_for_retry`), `InboundEmailAdmin`, an `InboxCode` inline
  on `TodoAccountAdmin`, all read-only. `./manage.py todo_ingest_eml file.eml --to
  todo+<code>@ziptrigo.com` runs the real ingest on a local file (uploaded to
  `todo/inbound/local-<uuid>`), like `receipts_ingest_eml`.
- **Tests.** `apps/todo/tests/test_inbound.py` (the decision order; the allowlist with and without
  authentication; subject cleaning; plain and HTML bodies and the cap; attachments stored, skipped over
  size, over the per-task cap and while frozen; `rejected_credits` and its notification; the AI date
  through the fake parser; retries, the lease, parking; the rows ceiling; `poll`; purging; atomicity)
  and `test_inbox_settings.py` (the address functions, the page and endpoints, rate limits, the
  command, jobs, admin). `FakeTodoStorage` gained `put_object`, `get_object_bytes` and `copy_object`.
- **Not verified on real mail** (like Receipts' list in "Known gaps"): a BCC and a Gmail auto-forward;
  and nothing here ran against real SES, which needs the infra side (#185).

#### API and CLI

Issue #187 (`api/`, `schemas/`, `admin/todo.py`; no migration). The endpoints, status codes and the
shared rate-limit budgets are listed under "API" above; this is how they are built and what the
CLI does with them.

- **Shape.** One module per domain on one `Router` (`api/router.py`, `auth=JWTAuth()`), the
  Receipts API's layout. `api/common.py` holds `mapped_errors()` (`AccountFrozenError` and
  `InsufficientCreditsError` -> 402, any other `ValidationError` -> 400), the lookups
  (`owned_task(request, number)`: a trashed task is returned unless the endpoint asks otherwise,
  and the service it calls says what is allowed on it, so changing or commenting on one is a 400
  with the service's message; `delete-forever` asks for a trashed one and is a 404 otherwise, as the
  web view is) and how a task becomes JSON. Responses are built as dicts (`task_data`) and validated
  by the schema, because a task needs the owner's account (the default due time for `due_at`) and
  one reference resolver for `description_html`, made once per request (`TaskContext`).
  `decorated(queryset)` loads what a task's JSON needs without a query per task: the subtask,
  comment and attachment counts (`services.with_attachment_counts`, new, a subquery like
  `with_comment_counts`), the parent, the repeat rule, the labels and the reminders; a test pins the
  number of queries of a 100-task page. Routes that are not a task number (`/tasks/reorder`,
  `/labels/reorder`, ...) are registered before the `{number}` / `{id}` ones: a router tries paths
  in the order they were added.
- **`GET /tasks`** is the Today / Upcoming / All / Completed / Trash pages and the search page in
  one. `view` picks the base set (`dates.today_set`, `upcoming_set`, `open_tasks`, `completed_set`,
  `trash_set`, which the web's list pages use too; `parent=<number>` alone lists that task's subtasks, completed ones too, in the
  user's manual order when they gave one), `FilterParams` the rest (`condition(user)` is the
  structured filters and the search text as one `Q`; the text is validated by `search.prepare_query`
  and evaluated inside `search.search_guard()` like every search). `limit`/`offset` replace the
  pages' 50-row pages, so the web's snippets are not computed. The default order is each page's own
  (`services.dates.order_today` is the Today list in one query: overdue first, then today's in
  `today_position` order once the user ordered it by hand, never the stale position of an overdue
  task; Completed latest first, Trash latest first, else by date); `sort` wins; with `view=all` and
  `include_completed` the completed ones go last (`search.order_results`). A search, or any of the
  structured filters, spends `TODO_SEARCH_USER`; a plain list spends nothing.
- **`POST /tasks`.** `source='api'`, billed like any task (the free allowance, then the price;
  `charged_credits` in the answer). `labels` are ids, `parent` a number. `reminders` replaces the
  default reminder `create_task` gave the task, inside one `transaction.atomic()` with the creation,
  so a refused reminder (no date, a duplicate time) creates and charges nothing; omitted keeps the
  default, `[]` means none. **Order of work:** the body is checked (`services.check_new_task`: the
  fields, then the 409, the cap on open tasks, a balance that can't pay -- the very checks email
  intake makes before its AI call -- and the reminders), then `TODO_CREATE_USER` is spent, then the
  title is read, then the task is created: a refused request never calls the model and spends neither
  budget. **`parse_title`** runs the title through `services.parsing.parse_title` (the same request
  and the same two rate limits as the web endpoint, spent through its `charge` callback) and uses what it found for each of the date (with its time) and the repeat
  **unless the body sets it**; the title is replaced by the cleaned one only when everything that was
  read was used. Nothing the AI does can fail the request: not configured, busy, failing, over the
  user's limits or no date found just creates the task as typed. `parsed` in the answer
  (`{applied, label, matched_text, reason}`, `null` when `parse_title` was not asked for) says what
  happened. `POST /parse` is the web endpoint's body exactly (`{available, has_date, ...}`), typed by
  `ParseAnswerSchema` (all fields optional, `exclude_unset`, so each of its three shapes is sent as
  the web sends it). Creation refused because of an unpaid occurrence is a 409 with the numbers of
  the user's unpaid tasks: `services.tasks.TaskCreationBlockedError` (a `ValidationError` subclass,
  as is `TooManyTasksError` for the cap), which `mapped_errors(keep=...)` lets through to the
  endpoint; email intake catches the same classes instead of comparing message text.
- **`PATCH`es** apply `model_dump(exclude_unset=True)`: a key left out is unchanged and an explicit
  `null` clears (`description` to empty, `priority` to none, `labels` to none, `parent` to a
  top-level task, `rrule` to stop repeating; `title` cannot be cleared, a 400; on `PATCH /filters/{id}`
  `name` and `params` cannot be cleared either: an explicit `null` is a 400, and a body with nothing
  in it changes nothing and spends nothing). `PATCH /settings` saves
  the due time, the default reminder and the allowlist in one transaction; `null` there clears
  `default_reminder_minutes` and `sender_allowlist` (accept anyone). Reminders' and comments'
  updates have nothing to clear (`null` there is "unchanged" / invalid).
- **Attachments** follow Receipts' upload API (presigned PUT with the signed `Content-Length` and
  `x-amz-checksum-sha256`; `complete` re-reads the object; a complete for a row that is gone is a 404
  that also deletes the object). `GET /attachments/{id}/url` returns the presigned URL and the
  disposition it really has (`inline` only for a sniffed PDF or image, whatever was asked), never the
  key. A pending upload counts toward the task's file cap, so a client whose `PUT` fails cancels it:
  `POST /attachments/{id}/cancel` (`attachments.cancel_upload`: the row, then the object; idempotent,
  204 also for an id that is gone or isn't the caller's, removing only an object under the caller's
  own prefix; a completed file is a 400 -- use `DELETE`; `TODO_EDIT_USER`), and `DELETE
  /attachments/{id}` on a pending row does the same. The CLI's `upload` calls it after any failure
  following the start (best effort: a failed cancel is a warning; the hourly job remains the net).
- **Saved filters** take and return `params` (the stored `FilterParams` dict, validated by
  `FilterParamsSchema`; labels that are not the caller's are dropped by the service) and a `summary`.
  `GET /filters/{id}/tasks` runs the filter as saved (a deleted label has dropped out of it).
- **CLI** (`admin/todo.py`, `inv todo ...`; `TODO_PASSWORD`, token `~/.todo_token.<env>`): every
  command takes the environment first. **`login <email>` has no password argument** (issue #187 had
  `[password]`, but this file's rule is that no CLI takes a password on the command line): it reads
  `TODO_PASSWORD`, else prompts with the echo hidden. (`receipts` and `filetransfer` still accept the
  optional positional argument; not changed here.) It goes through `admin/utils.py`'s `api_request` (default
  timeout, bounded 429 retry that does not sleep on an hourly rule), `get_headers('todo')`,
  `select_environment` and `password_from_prompt_or_env`; errors print the API's `detail` (a 422
  list flattened), a 409 names the unpaid task numbers and says to top up, exit 1. `add` sends
  `parse_title=true` unless `--no-parse` and prints what was read; labels are given by name (or
  id) and resolved with `GET /labels`, an unknown one stops before anything is created. `list`
  prints a table (number, due date and time as the owner wrote them, `!1`, title, labels, a repeat
  mark and a bell); `get` prints everything (the description as Markdown, comments, reminders, files
  and the tasks that mention it). `done` takes several numbers and carries on past a failure (exit 1
  if any failed); `upload` hashes each file, skips an empty file or a folder with a warning, carries
  on past a failed file (exit 1 if any) and PUTs with the exact headers the API returned;
  `download` asks for a presigned URL and streams it **without the API token** to
  `.<name>.part` next to the target, renaming it into place only when complete (and refusing to
  overwrite without `--force`); `edit --due DATE` alone keeps the time the task has (only
  `--due YYYY-MM-DDTHH:MM` sets one; `--clear-time` removes it, alone or with a date); `label-delete` and `regenerate` ask first (`--yes` skips). Everything
  that prints a title or a label name escapes Rich markup, so a title like `[bold]` shows as typed.
  Tests: `admin/tests/test_todo.py` (HTTP mocked, no Django).
- **Not in v1**: an `expected_cost` / quote for task creation (the price is the setting's and the
  answer reports `charged_credits`), search snippets in the API.
- **Judgement calls** where the issue left room: `GET /tasks/{number}` also returns the task's
  uploaded `attachments` and `referenced_by` (the CLI's `get` needs them and no list endpoint was
  specified); `POST /tasks` answers an extra `parsed`; `POST /tasks/reorder` answers `{count}` (the
  web answers 204); `POST /filters/reorder` exists next to the labels' for symmetry with the web;
  `GET /reminders/history` and the task list take `limit`/`offset` instead of the pages' page
  numbers.

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
  Each `process_receipt` also starts one short-lived child process to read the file (see
  "Receipts" > Processing): the `receipts` worker plus that child (up to
  `RECEIPTS_DOCUMENT_MEMORY_MB` of address space, one at a time) is the memory the container must
  have room for (the child asks the OOM killer to pick it first, `oom_score_adj` 1000; set the
  container's memory limit in the `infra` compose to at least worker + that budget, or a legitimate
  big photo is OOM-killed and retried), and an over-budget child fails the receipt, never the
  worker; a child that merely breaks (including one the OOM killer took) is retried.
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
  file_transfer job logs and the ratelimit "over limit" lines all show up. `django` (INFO) goes to
  the same handler, also `propagate: False`: Django's default config only prints with DEBUG=True and
  mails `ADMINS` (empty), and since that logger has handlers the last-resort stderr handler never
  fires, so every production 500 and `django.security` warning used to vanish. The root logger
  (WARNING) covers everything else (`django_tasks`, `authlib`, `botocore`); `django_tasks_db`'s
  `db_worker` therefore logs through root's console handler on stderr (it only adds its own stdout
  handler when its logger has none). `django.request` is held at ERROR (Django logs every 4xx there
  at WARNING; gunicorn's access log has them), `django.security.DisallowedHost` is dropped (scanner
  noise) and `django.server` is re-declared with Django's own formatter. `conftest.py` re-enables
  propagation of `apps` and `django` under pytest so `caplog` still works;
  `apps/core/tests/test_logging.py` checks the real config in a subprocess.
  Jobs therefore run inside web workers, and **an interrupted job keeps its lease until the lease
  expires**: a deploy, a SIGHUP reload or a worker timeout kill that lands mid-job leaves that
  job's `locked_until` set, so no runner picks it up before then (up to the job's lease, e.g. 1 h
  for the daily jobs), and a deploy can delay a daily job by up to that long. Correct, because
  every job is idempotent and runs at-least-once; leases are deliberately not cleared on exit.
  A job that *finishes* (successfully or not) does release its lease (`runner._run_job`), so it is
  due again after its `interval` even when its `lease` is longer (`receipts.retry_due`: 5 min
  interval, 10 min lease) -- before that fix every job ran every max(interval, lease). The release
  only applies while no later claim exists (`last_started_at` below this claim's plus 1 ms --
  not `<=`: SQLite stores `Now()` with milliseconds but renders a whole-second read-back without a
  fraction, so a text `<=` missed one claim in a thousand and left the lease held), so a run that
  outlived its lease can't free the lease of the runner that took over.
  `gunicorn.conf.py`'s `worker_exit` stops the runner and joins `runner.thread` for up to 5 s, which
  only covers a thread idle between jobs. `SchedulerRunner.run_forever` calls
  `close_old_connections()` every tick; with `DATABASE_URL` (prod/staging: `conn_max_age=600`,
  `conn_health_checks`) each scheduler thread keeps one health-checked persistent connection, which
  is what recovers it from a database restart, and it means **each gunicorn worker holds one extra
  Postgres connection** when the scheduler is enabled (3 by default).
  `./manage.py run_scheduler` remains for local development (the `scheduler` compose service) or a
  dedicated container; job status is a read-only `ScheduledJob` admin.

`core`'s jobs (`apps/core/jobs.py`) let exceptions propagate (the runner records them in
`ScheduledJob.last_error`; a swallowed one would show as a success). Besides the email-verification and
rate-limit purges, two daily ones keep tables from growing forever (audit #18):
`core.purge_old_task_results` (finished `DBTaskResult` rows past `TASK_RESULT_RETENTION`, 14 days,
succeeded and failed alike, in batches; ready/running rows never) and `core.clear_expired_sessions` (`SESSION_ENGINE`'s
`SessionStore.clear_expired()`; nothing else purges `django_session`). A third daily one,
`core.purge_old_notifications` (90 days, see "Notifications"), does the same for in-app notifications.

`file_transfer`'s four jobs (`apps/file_transfer/jobs.py`): `meter_transfers` (daily -- charges at
the shared `BillingSettings` storage price, suspends, re-enables as a fallback -- only transfers
already suspended when the run started, never one it suspended itself --, deletes files past the
suspension grace period),
`expire_transfers` (every 5 min -- also finishes the deferred file deletion described above),
`cleanup_drafts` (hourly; deletes a draft or unconfirmed transfer idle for 24 h, i.e.
`Transfer.last_activity_at`, which `uploads.touch_draft` bumps on add/presign/resume/complete/remove
file -- a column with a `db_default`, so an old container keeps inserting; `abort_draft`'s claim
re-checks the idleness via `only_if`, so a draft touched after the job's snapshot is kept),
`purge_download_ips` (daily). Each wraps its
per-transfer work in
`try`/`except Exception: logger.exception(...)` so one bad row can't abort the rest of that run's
batch the way an uncaught exception would (which, since `last_started_at` is already stamped by
the time the job's `func` runs, would otherwise leave every other transfer waiting a full extra
interval too).

### Templates and static files

Always namespaced: `apps/<app>/templates/<app>/…` and `apps/<app>/static/<app>/…`. Every page
extends `core/base.html`, which loads Tailwind (Play CDN pinned to 3.4.17, configured inline with the
sage palette, `brand-*` aliases, a sage-tinted `gray` scale that replaces Tailwind's and the `font-sans`
/ `font-mono` stacks), htmx, Alpine.js, Font Awesome and `core/css/site.css` (issue #214: the
self-hosted Figtree and IBM Plex Mono `@font-face`s under `core/static/core/fonts/`, the motion tokens
and keyframes, the opt-in htmx hooks `row-enter` / `row-leave`, the `btn` press
and the `:focus-visible` ring, and the `prefers-reduced-motion` switch-off). Templates carry no
`focus:ring-*` classes: `site.css` draws the keyboard ring for buttons and links and a sage halo for
fields. Three small shared scripts sit next to the Alpine components: `core/js/tabs.js` (the sliding
underline of `<nav data-tabs>`), `core/js/skeleton.js` (skeleton rows after 250 ms for a `data-list`
container that htmx refreshes) and the badge/copy pops in `components.js`. Statuses, chips, counts and
tags are one tinted pill from `apps/core/templatetags/core_ui.py` (`{% pill %}`, `pill_html`), which the
products' own tags (`status_pill`, `link_status_pill`, `due_badge`, ...) wrap; an empty list is
`core/partials/empty_state.html`. The only un-namespaced templates are core's `admin/` overrides.

**Times of day have two zones, on purpose.** `TIME_ZONE` is UTC and the database holds UTC. A
signed-in user may also have a saved time zone (`User.timezone`, issue #177, see "Auth" > "Time
zone"), which `TimezoneMiddleware` activates for the request. The two mechanisms below don't depend
on each other:

- **Server side, the user's saved zone.** With the middleware active, `{{ value|date }}`,
  `{% now %}`, `localtime()`, `localdate()` and `make_aware()` use the user's zone, so a `|date:"H:i"`
  on any page (receipts, file transfer dates, `timezone.localdate()` in Receipts' date-range presets,
  file transfer's end-of-day expiry, core's date picker) is local time for a user who has set a zone,
  and UTC for one who hasn't, for an anonymous visitor and for the JWT API (no session, so no zone;
  its values are explicit ISO 8601 anyway). Code that has to compute with the zone calls
  `apps.accounts.services.timezones` (`user_tz`, `user_today`, `local_datetime`) instead of relying
  on what is active.
- **Browser side, the visitor's own zone.** Before there was a saved zone, a time rendered with
  `|date:"H:i"` was UTC and a bare `datetime-local` value was read as UTC (found by the browser run
  of the Short URLs AI plan: from Chicago, an expiry "2 minutes from now" was refused as past).
  `core/js/local_time.js`, loaded on every page by `core/base.html` and re-run on whatever htmx swaps
  in (`htmx:load`), converts both ways **using the browser's zone, not the saved one** (the two are
  normally the same place; when they aren't, a `{% local_time %}` shows where the visitor is, while
  `|date` shows where the account says they live). It is independent of the middleware: the tag
  always renders the UTC value and the UTC fallback whatever zone is active
  (`apps/accounts/tests/unit/test_timezone.py` renders it under another active zone), and the
  inputs always submit UTC.

The browser-side pieces:

- **Showing**: `{% load local_time %}{% local_time value %}` (`core/templatetags/local_time.py`)
  renders `<time datetime="<UTC ISO>" data-local-time="long">Oct 27, 2026 13:51 UTC</time>`, the UTC
  fallback marked "UTC" for a visitor without JavaScript, which the script rewrites locally. Styles:
  `long` (default), `short` (no year), `iso` (`2026-10-27 08:51`, for tables); `title=True` renders
  only the attributes for an element of your own and localizes its tooltip instead of its text (the
  notification list's relative "3 hours ago"). Used by file transfer (dashboard row, download and
  manage pages), billing history, the receipt page, notifications and Short URLs. A bare date
  (`|date:"M j"`) needs none of this. **Emails stay in UTC** (no browser to convert them), and should
  say so.
- **Entering**: `<input type="datetime-local" data-utc-target="<id>">` with no `name`, next to a
  hidden input `<id>` that carries the field's `name` and is prefilled in UTC with an offset
  (`2026-10-27T13:51Z`); the script shows it locally and writes the UTC value
  (`2026-10-27T13:51:00.000Z`) back on every change, which Django's `DateTimeField` parses as aware.
  Without JavaScript the field is not sent. Only Short URLs' Expires uses it so far (file transfer's
  expiry is a date picker).

### Docker

One `Dockerfile` (multi-stage: `uv sync --frozen --no-dev` in a builder, so no dev tools ship;
venv copied to `python:3.14-slim`), shared by every service in `docker-compose.yml`: `web`,
`worker`, `worker-receipts`, `scheduler` and `db` (that split is the local-development layout
only; a deployment runs one container, below). The build runs `collectstatic` so WhiteNoise can
serve static files with `DEBUG=False`. `.env.local` is mounted into `web`, `worker`,
`worker-receipts` and `scheduler` because settings require an env file.

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
(`apps/ziptrigo-apps/nginx.conf`, issue #119; main zone plus a looser short link zone for
`/go/<code>` and the `/s/` short link shapes (#170), see "Rate limiting" above).

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
- Short URLs (#162) is fully built (phases 1 to 4: links and billing, analytics, password and
  preview, abuse tooling, the JWT API and CLI; see "Short URLs" above). Destinations are checked
  against Google Safe Browsing (#163) once `SAFE_BROWSING_API_KEY` is set in the env files; until
  then (`short_url.W002`) the only defences are the blocked-domain list, reports and takedown. One
  infra follow-up remains: the
  GeoLite2-Country database plus `GEOIP_PATH` have to be provided to the containers (#171: until
  then every click's country is unknown and `short_url.W001` shows at every deploy). nginx's coarse
  `limit_req` is designed to count `/s/<code>`, `/s/<code>+` and `/s/<code>/unlock` in the looser
  short link zone (#170), sized well above what a crowd behind one shared address produces.
  `admin/shorturl.py` is the first CLI on `admin/utils.py`'s shared `api_request`;
  `receipts.py` and `filetransfer.py` still carry their own copies of it.
- To-do (#161) is built in waves. Only the foundation (#178: tasks, subtasks, labels, numbering, task
  billing, the web pages) and repeating tasks (#179) exist; comments, search, reminders, attachments,
  AI date parsing, email intake and the JWT API/CLI are their own sub-issues. Completing a repeating
  task whose next occurrence the balance can't cover is what creates an *unpaid* task (and sets
  `creation_blocked_at`). The AI plan `tests_e2e/ai/todo.md` has not been run end to end by an agent
  yet.
- Welcome credits (#160) are built and **on by default** (1,000 credits; set `BillingSettings.
  welcome_credits` to 0 to turn them off). Limits worth knowing: the remembered addresses start with
  the confirmed accounts that exist when `accounts.0006` runs, so an address an existing account has
  already left is new again; rotating `SECRET_KEY` has the same effect (see "Welcome credits");
  someone with several mailboxes (not aliases of one) can still get the credits once per mailbox.
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
`File Transfer`, `Url` (short_url), `Todo`. Work that happens in the separate `infra` repository is tracked here under
`Infra`. An issue that touches several apps gets several labels.

### Adding an app

1. `apps/<name>/` with the layout above; `AppConfig` with `name = 'apps.<name>'`,
   `label = '<name>'`.
2. For a product: register a `ProductApp` in `AppConfig.ready()` (see `apps/qr_code/apps.py`).
3. Add to `INSTALLED_APPS`, mount URLs in `config/urls.py`, add its API router in `config/api.py`.
4. Add it to the products layer of the import-linter contract in `pyproject.toml`.

## Design system

**`docs/ui-design/README.md` is the source of truth for every web page** (principles, colour, type,
shape, motion, each component with its markup, and the checklist in section 16), with live pages in
`docs/ui-design/reference/`. A PR that adds or changes UI is reviewed against that checklist; its
"Known deviations" table lists what existing templates still do differently. Text is Figtree
(`font-sans`), codes and addresses IBM Plex Mono (`font-mono`); motion answers an action, uses the
tokens in `core/css/site.css` and is off under reduced motion. The look refresh is issue #214.

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

Neutrals are the `gray` scale, overridden in the Tailwind config with sage-tinted values (`50 #f6f8f7 ·
100 #eef1f0 · 200 #dfe4e2 · 300 #c6cecb · 400 #98a39f · 500 #66716d · 600 #4f5956 · 700 #3c4441 ·
800 #282e2c · 900 #1a1f1d · 950 #111514`); keep using `gray-*`, not `slate`/`zinc`/`neutral`.

The admin uses django-jazzmin, themed via `JAZZMIN_SETTINGS` in `config/settings.py` with
`core/css/jazzmin_custom.css` and a `core/js/admin_theme_toggle.js` light/dark toggle.
