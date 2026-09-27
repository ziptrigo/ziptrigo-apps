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
apps/core/            Site shell: base.html, landing page, ProductApp registry, admin site, email.
apps/accounts/        User model, auth pages + API, JWT auth classes.
apps/billing/         CreditAccount (balance) + CreditTransaction (ledger), credit services.
apps/qr_code/         QR codes: dashboard/editor pages, API, `/go/<code>` short links.
apps/file_transfer/   File transfer: send/dashboard/download pages, S3 uploads, metering, jobs.
admin/                Typer CLIs for lint/test/server/pip/openapi/aws/email/qrcode. Via `inv`.
tests_e2e/            Playwright end-to-end tests (separate `pytest_e2e.ini`).
conftest.py           Fixtures shared by every app's tests (`user`, `api_client`, ...).
pyproject.toml        Deps, ruff/ty/pytest/import-linter config, `inv` module registry.
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
```

Every command accepts `--dry` to print the shell command without running it. The app names `inv`
accepts come from `admin/django_app.py`, which discovers them from `apps/*/apps.py`.

`admin/server.py` and `admin/openapi.py` each define one typer command, which typer collapses away
when the module is run directly (`python3 -m admin.server`) but which `inv` preserves
(`inv server run`).

### Running tests directly

Run pytest from the repo root; `DJANGO_SETTINGS_MODULE=config.settings` and `testpaths = ['apps']`
are set in `pyproject.toml`, and default addopts include `--reuse-db --no-migrations`:

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
under `transfers/`), defined with its CORS, lifecycle rule and IAM user in the `infra` repo's
`apps/ziptrigo-apps` (see its README); dev points `FILE_TRANSFER_S3_ENDPOINT_URL` at Floci.

### Auth

One `AUTH_USER_MODEL`: `accounts.User` (UUID pk, email login, `status`). Two mechanisms coexist:

- **Django sessions** for the web UI: every page and every HTMX form view is `@login_required`
  (`LOGIN_URL='accounts:login'`). The login page is a session form view
  (`apps/accounts/views/login.py`, CSRF-protected, honours a same-site `next`); logout is
  POST-only. The browser never holds a JWT.
- **JWTs** for `/api/`, i.e. external clients like `admin/qrcode.py` (`apps.accounts.auth.JWTAuth` /
  `AsyncJWTAuth` / `AdminAuth`, which also reject non-`ACTIVE` users). Claim `sub`, signed with
  `JWT_SECRET`, token classes in `apps/accounts/tokens.py`.

`POST /api/auth/login` only issues JWTs; it never starts a session. The other unauthenticated
account pages (register, password reset, resend confirmation) still post JSON to `/api/auth/…`.
Changing the email through `PUT /api/account` un-confirms the account and sends a new
confirmation email.

For typed views, use `AuthenticatedHttpRequest` / `MaybeAuthenticatedHttpRequest` from
`apps/accounts/http.py`.

### API

`config/api.py` builds one `NinjaAPI`; each app exposes a `router` from its `api` module/package
and is mounted under its prefix (`/api/` for accounts, `/api/billing/`, `/api/qr/`). Docs at
`/api/docs`.

### Admin

`apps/core/admin_site.py` owns `custom_admin_site` (mounted at `/admin/`) and the tools page (test
email, masked environment). Every app registers its `ModelAdmin`s on it from its own `admin.py`.
The manual credit adjustment tool lives on `CreditTransactionAdmin` (`/admin/billing/credittransaction/adjust/`).
The user admin builds on Django's `UserAdmin` with email-based forms (`apps/accounts/forms/admin.py`),
so passwords are only ever set through hashed password fields. Jazzmin's top menu links to the site
and to the admin tools page.

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
(`apps/file_transfer/views/uploads.py`) are a deliberate exception to "form-encoded" above -- the
browser uploads directly to S3 with presigned multipart URLs and only coordinates with Django over
JSON, so those views speak JSON in and out. The options form (recipients, message, expiry, max
downloads, password) that finishes the send *is* a normal HTMX form and follows the 422 convention.
The public download page (`/t/<slug>/`, `apps/file_transfer/download_urls.py`) needs no login and
never explains *why* a transfer isn't available (expired, disabled, suspended, deleted, or its
download limit reached all render the same neutral page). A password gates the download links, not
the file list itself.

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
  database's own clock (`try_claim` in `apps/core/scheduler/runner.py`), and a `SchedulerRunner`
  that ticks every `SCHEDULER_TICK_SECONDS` running whatever's due. At-least-once, so every job
  must be idempotent. `./manage.py run_scheduler` runs it forever; job status is a read-only
  `ScheduledJob` admin. The `worker` compose service also runs this, alongside `db_worker`.

`file_transfer`'s four jobs (`apps/file_transfer/jobs.py`): `meter_transfers` (daily -- charges,
suspends, re-enables as a fallback, deletes files past the suspension grace period),
`expire_transfers` (every 5 min), `cleanup_drafts` (hourly), `purge_download_ips` (daily).

### Templates and static files

Always namespaced: `apps/<app>/templates/<app>/…` and `apps/<app>/static/<app>/…`. Every page
extends `core/base.html`, which loads Tailwind (CDN, with the sage palette plus `brand-*` aliases),
htmx, Alpine.js and Font Awesome. The only un-namespaced templates are core's `admin/` overrides.

### Docker

One `Dockerfile` (multi-stage: `uv sync --frozen` in a builder, venv copied to `python:3.14-slim`),
shared by every service in `docker-compose.yml`: `web`, `worker` and `db`. The build runs
`collectstatic` so WhiteNoise can serve static files with `DEBUG=False`. `.env.dev` is mounted into
`web` and `worker` because settings require an env file.

- `web` runs gunicorn (`config.wsgi`) in the image; local compose overrides it with `runserver`.
- `worker` runs the two background-work mechanisms (see "Queue and scheduler" above) as two
  processes in one container: `./manage.py run_scheduler` and `./manage.py db_worker`, backgrounded
  with `&` and kept alive with `wait -n` so the container exits if either dies. One container for
  both since they're always deployed together and neither needs to scale independently; split them
  into their own services if that changes.
- `db` is this stack's own Postgres (`postgres:18-alpine`), separate from the shared
  `docker-compose.postgres.yml` used across repos for local dev tooling (see that file's header) --
  `db` is part of the deployable stack the other two services depend on.

Both `web` and `worker` run `docker-entrypoint.sh`, which applies `migrate` first when
`RUN_MIGRATIONS=1` (both set it, since they start concurrently and neither can assume the other has
migrated yet; migrations are idempotent). The database is `DATABASE_URL` (Postgres, via
`dj-database-url`; compose points it at `db`) when set, SQLite otherwise; `ENVIRONMENT=prod` refuses
to start without it -- a separate `worker` container can't share a SQLite file with `web`, which is
why Postgres was a prerequisite for this issue. Behind nginx, `SECURE_PROXY_SSL_HEADER` and
`CSRF_TRUSTED_ORIGINS` (from `BASE_URL`) keep HTTPS form posts passing the CSRF check.

## State of the test suites

Run everything with `inv test unit`. 285 pass, 31 fail, 1 skipped. The failures are **not** layout
problems — they are drift between the suites and a codebase that migrated from DRF to
django-ninja and from sync to async. Don't try to fix them by moving files around.

| file | n | cause |
|---|---|---|
| `billing/tests/test_credits_api.py` | 11 | calls DRF's `api_client.force_authenticate()`; the fixture is a ninja `TestClient`. Never ported off DRF |
| `qr_code/tests/test_services.py` | 8 | `SynchronousOnlyOperation` — async tests touching the ORM without `sync_to_async` |
| `accounts/tests/unit/test_authentication.py` | 4 | expects `JWTAuth.authenticate` to return `None` for a bad token; ninja_jwt raises `InvalidToken` before `authenticate` runs |
| `qr_code/tests/test_setup_integration.py` | 3 | same `SynchronousOnlyOperation` |
| `accounts/tests/test_auth.py` | 2 | signup lets a `ValidationError` escape as a 500 instead of returning 400 — a real app bug in the router |
| `accounts/tests/api/test_auth_login_api.py` | 1 | same login/JWT surface |
| `core/tests/test_admin_tools.py` | 1 | expects 403; Django admin redirects 302 to its login |
| `qr_code/tests/test_setup_unit.py` | 1 | same `SynchronousOnlyOperation` |

Each needs a product decision (what *should* signup return? should `JWTAuth` swallow an invalid
token?) or a real port of a DRF-era module.

## Known gaps

- Register and password-reset pages still post JSON to `/api/auth/…` (they work, but aren't
  session form views yet).
- No rate limiting anywhere (login, previews, API, file transfer send/download/password attempts --
  file transfer's is tracked as #53).
- The admin credits API (`POST /api/billing/users/{id}/credits`) now refuses to take a balance
  below zero (`CreditAccount.balance` is unsigned); it used to allow it.
- `file_transfer` phase 1 (logged-in sending, S3 uploads, download page, dashboard, metering,
  emails, queue/scheduler) is built (issue #55); phase 2 is not: anonymous sending and its email
  confirmation codes, the anonymous manage link, claiming a transfer on login, "download all" as a
  zip, and the per-download log UI (the underlying `DownloadEvent` rows and IP purge job exist
  already). Phase 3 (resumable uploads, a JWT `/api/ft/` router, `admin/filetransfer.py`, takedown
  tooling) is not either.

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
