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
apps/file_transfer/   Skeleton product: one page + one HTMX partial, no models yet.
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
- Cross-app links in templates (`{% url 'credits-history-page' %}` in an accounts template) are
  fine; they aren't imports.

### Settings and environment

`config/settings.py` is the only settings module. It calls `select_env()` from
`config/environment.py` and **raises on failure** rather than falling back to defaults — except
under pytest, which must not depend on machine-local config. `config/environment.py` must stay
Django-free (settings imports it before Django is configured); `admin/environment.py` is a thin
binding over it for the CLIs.

The convention: `.env.<environment>` at the repo root where `<environment>` ∈ {`dev`, `prod`}; if
`ENVIRONMENT` is set, use it; otherwise require exactly one `.env.*` file (ignoring
`.env.example`). `.env.*` is gitignored — copy `.env.example` to `.env.dev`. `apps/core/checks.py`
re-validates env selection and `EMAIL_BACKENDS` at `runserver` startup.

### Auth

One `AUTH_USER_MODEL`: `accounts.User` (UUID pk, email login, `status`). Two mechanisms coexist:

- **Django sessions** for the server-rendered pages (`@login_required`, `LOGIN_URL='login-page'`).
- **JWTs** for `/api/` (`apps.accounts.auth.JWTAuth` / `AsyncJWTAuth` / `AdminAuth`, which also
  reject non-`ACTIVE` users). Claim `sub`, signed with `JWT_SECRET`, token classes in
  `apps/accounts/tokens.py`.

`POST /api/auth/login` returns JWTs *and* starts a session, so both work after the login page.
The browser keeps the JWTs in `localStorage` via `apps/core/static/core/js/auth.js`, which also
adds the `Authorization` header to every HTMX request. The intended end state is sessions + HTMX
form posts for the web UI, with JWTs only for external API clients.

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

### Templates and static files

Always namespaced: `apps/<app>/templates/<app>/…` and `apps/<app>/static/<app>/…`. Every page
extends `core/base.html`, which loads Tailwind (CDN, with the sage palette plus `brand-*` aliases),
htmx, Alpine.js and Font Awesome. The only un-namespaced templates are core's `admin/` overrides.

### Docker

One `Dockerfile` (multi-stage: `uv sync --frozen` in a builder, venv copied to `python:3.14-slim`)
and one `web` service in `docker-compose.yml` on port 8000. `.env.dev` is mounted into the
container because settings require an env file.

## State of the test suites

Run everything with `inv test unit`. 84 pass, 31 fail, 1 skipped. The failures are **not** layout
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

- **HTMX forms post form-encoded bodies to JSON-only Ninja endpoints** (`account.html`'s profile
  form → `PUT /api/account`, `qrcode_editor.html` → `POST /api/qr/`), so they 400. This predates
  the consolidation. Fixing it belongs with moving the web UI to sessions + HTMX form views.
- The admin credits API (`POST /api/billing/users/{id}/credits`) now refuses to take a balance
  below zero (`CreditAccount.balance` is unsigned); it used to allow it.
- `file_transfer` has no models yet. Expected shape: `Transfer`/`TransferFile`, direct-to-S3
  presigned uploads, expiry and notification jobs on a task worker, credits through `billing`.
- Settings hardcode SQLite; `DATABASE_URL` is passed by compose but not read.

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
- Models, schemas, routers/api, services and views are packages with one domain per file,
  re-exported from `__init__.py`. Follow this when adding to any app.
- New apps namespace their URLs (`app_name = '<app>'`, see `file_transfer`). The older apps still
  use global URL names (`'dashboard'`, `'login-page'`, ...).
- Admin CLIs: typer apps with `no_args_is_help=True`, a module docstring as `help`, and a `--dry`
  option threaded through `admin.utils.run`.

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
