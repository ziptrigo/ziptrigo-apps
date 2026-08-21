# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository shape

Monorepo of two independently-deployable Django 6 microservices plus shared packages. Created by
`git subtree`-merging two standalone repos, then refactored twice (`src/` layout → flat layout,
then `users/` → `user-service/` and `common/` → `shared/`). Both services run; the test suites are
partly stranded on the pre-DRF-migration codebase — see "State of the test suites" below.

```
user-service/         Django project (config/) + `users` app. SSO / identity. Port 8010.
qr_code/              Django project (config/) + `qr_code` app. QR generation. Port 8020.
shared/utils/         Installable pkg `utils` — base Django settings + `.env` selection.
shared/auth_client/   Installable pkg `auth_client` — intended cross-service auth. EMPTY STUB.
admin/                Typer CLIs for lint/test/server/pip/openapi/aws/email/qrcode. Via `inv`.
tests_e2e/            Playwright end-to-end tests (separate `pytest_e2e.ini`).
pyproject.toml        Single source of deps, ruff/ty/pytest config, `inv` module registry.
uv.lock               One lockfile shared by both services.
```

Each service also has a `README.md` and `WARP.md`; **both are stale** (they document the old `src/`
layout). The root `README.md` is mostly accurate but its command examples are wrong (see below).
`AGENTS.md` just redirects here.

## Commands

Tooling is exposed through `typer-invoke`, which mounts each `admin/*.py` module as an `inv`
subcommand group. The module registry is `[tool.typer-invoke].modules` in `pyproject.toml`.

```bash
inv lint all                  # ruff check --fix + ruff format + ty
inv lint all --check          # CI mode: report only, non-zero exit
inv lint ruff <path>
inv lint ty [target]          # target: a web app dir name or `admin`; omit to check everything
inv test unit                 # all services
inv test unit qr_code         # one service (positional, repeatable; names are directories)
inv test e2e [--no-headless]  # Playwright, uses pytest_e2e.ini
inv server run user-service [dev|prod]   # runserver for one service
inv pip sync                  # uv sync --frozen, all groups
inv openapi generate --format json --file <path>
```

Every command accepts `--dry` to print the shell command without running it.

The two entry points are *not* interchangeable for single-command modules. `admin/server.py` and
`admin/openapi.py` each define one typer command, which typer collapses away when the module is run
directly but which `inv` preserves:

```bash
inv server run qr_code            # via inv: subcommand required
python3 -m admin.server qr_code   # direct: subcommand collapsed away
```

Multi-command modules (`lint`, `test`, `pip`) take the subcommand either way.

### Running tests directly

`inv test unit` shells out to pytest with `cwd` set to the service directory and `PYTHONPATH`
pointing at the repo root, with `ENVIRONMENT=dev`. To run a single test, reproduce that yourself:

```bash
cd user-service && PYTHONPATH=.. ENVIRONMENT=dev pytest tests/unit/test_jwt.py::test_access_token
```

`DJANGO_SETTINGS_MODULE=config.settings` is set globally in `pyproject.toml`, which is why pytest
must run from inside a service directory — both services name their config package `config`.
Default addopts include `--reuse-db --no-migrations`.

### Dependencies

`admin/pip.py` wraps `uv` over the shared lockfile. Dependency scopes are `main` / `dev`; app groups
are `users` / `qr_code` (`--app` / `-a`). The lockfile is always global — locking is never filtered
by scope, only syncing is.

```bash
inv pip sync dev -a qr_code       # dev tools + qr_code group
inv pip package dev -p django     # upgrade one declared package
inv pip compile --clean           # delete uv.lock and re-lock from scratch
inv pip install                   # sync --inexact (won't prune unrelated packages)
```

Add shared runtime deps to `[project.dependencies]`; app-specific or tooling deps to
`[dependency-groups]`. `admin/pip.py` validates `-p` names against what's declared, so update
`pyproject.toml` first.

## Architecture

### Settings composition

Both services' `config/settings.py` follow the same prologue: compute `PROJECT_ROOT`, then
`sys.path.insert` the repo root, `shared/utils`, and `shared/auth_client`, then import shared
constants from `utils.settings.base` (hence the `# noqa: E402` on those imports). Each service owns
its `SECRET_KEY`, `INSTALLED_APPS`, `DATABASES`, and `NINJA_JWT` block.

`shared/utils/utils/settings/base.py` exports only `COMMON_*` constants (middleware, validators,
installed apps, Jazzmin, context processors) plus a few plain settings (`TIME_ZONE`, `USE_TZ`, …).
Services compose, e.g. `INSTALLED_APPS = COMMON_INSTALLED_APPS + [...]`.

### Environment loading

Both services call `select_env()` from `shared/utils/utils/environment.py` and **raise on failure**
rather than falling back to defaults — except under pytest, which must not depend on machine-local
config and falls through to the defaults baked into `settings.py`. (Static analysis used to get the
same treatment for mypy; `ty` is a standalone binary that never imports `settings.py`, so there's no
equivalent case to handle.) qr_code also
registers Django system checks in `qr_code/qr_code/checks.py` that re-validate env selection and
`EMAIL_BACKENDS` at startup.

The convention: `.env.<environment>` files where `<environment>` ∈ {`dev`, `prod`}; if
`ENVIRONMENT` is set, use it; otherwise require exactly one `.env.*` file (ignoring `.env.example`)
and fail if there are zero or several. `.env.*` is gitignored — copy each service's `.env.example`
to `.env.dev` to run locally.

`environment.py` lives in the shared package because `settings.py` imports it before Django is
configured; it must stay Django-free. `admin/environment.py` is a thin binding that supplies the
repo root as the default project root and accepts a `WebApp`. `admin/web_app.py`'s `WebApp` values
are **directory names**; the uv dependency-group names are a separate enum (`admin.pip.App`),
because a group name can't contain a dash.

### Auth — two disconnected user models

The intended design is that `user-service` is the identity authority and other services verify via
`shared/auth_client`. **This is not implemented.** `auth_client/__init__.py` is a 0-byte file. Today
each service has its own `AUTH_USER_MODEL` (`users.User` and `qr_code.User`) with its own database
and its own JWT signing key. Don't assume a shared identity when changing auth code. See "Still
outstanding" for how far the consolidation actually got.

The two services also differ in their Ninja stack and JWT claim shape:

| | user-service | qr_code |
|---|---|---|
| API | `NinjaAPI` (`users/api.py`) | `NinjaExtraAPI` + `NinjaJWTDefaultController` (`qr_code/api/router.py`) |
| user id claim | `sub` | `user_id` |
| signing key | `JWT_SECRET` env var | `SECRET_KEY` |
| access token TTL | 14 days (env-tunable) | 60 min |
| token class | `users.tokens.CustomAccessToken` | stock `ninja_jwt.tokens.AccessToken` |

`qr_code/qr_code/api/` contains both `auth.py`/`qrcode.py` and `auth_new.py`/`qrcode_new.py`. Only
the `_new` variants are wired into `router.py`; the others are dead DRF-era code.

### URL prefixing for a future gateway

Both `config/urls.py` files build a `base_patterns` list and assign `urlpatterns = base_patterns`,
with a commented-out `urlpatterns = [path('users/', include(base_patterns))]` line above it. Behind
an API gateway, swap those two lines and route `/users/*` → 8010, `/qr-code/*` → 8020.

### Docker

`docker-compose.yml` defines services `user-service` and `qr_code` (note: the compose service is
`user-service`, not `users`). Build context is the repo root so `shared/` can be copied in;
Dockerfiles are multi-stage (`uv sync --frozen --group <app>` in a builder, venv copied to a
python:3.14-slim runtime) and `pip install -e` the two shared packages. No database container —
services expect external DBs via `DATABASE_URL`, though `settings.py` currently hardcodes SQLite.

## State of the test suites

Both services import, pass `manage.py check`, and boot (`inv server run user-service` / `qr_code`).
The remaining test failures are **not** layout problems — they are drift between the suites and a
codebase that migrated from DRF to django-ninja and from sync to async. Don't try to fix them by
moving files around.

**user-service** — 41 passing, 19 failing:

| file | n | cause |
|---|---|---|
| `tests/api/test_credits_api.py` | 11 | calls DRF's `api_client.force_authenticate()`; the fixture is a ninja `TestClient`. Never ported off DRF |
| `tests/unit/test_authentication.py` | 4 | expects `JWTAuth.authenticate` to return `None` for a bad token; ninja_jwt raises `InvalidToken` before `authenticate` runs |
| `tests/test_auth.py` | 2 | signup lets a `ValidationError` escape as a 500 instead of returning 400 — a real app bug in the router |
| `tests/api/test_auth_login_api.py` | 1 | same login/JWT surface |
| `tests/unit/test_admin_tools.py` | 1 | expects 403; Django admin redirects 302 to its login |

**qr_code** — 37 passing, 15 failing, plus 3 modules that don't collect at all:

| file | n | cause |
|---|---|---|
| `tests/test_services.py` | 8 | `SynchronousOnlyOperation` — async tests touching the ORM without `sync_to_async` |
| `tests/test_setup_integration.py` | 3 | same |
| `tests/test_password_reset_email.py` | 2 | same |
| `tests/test_credits.py`, `tests/test_setup_unit.py` | 2 | same |
| `test_api.py`, `test_auth.py`, `test_email_confirmation.py` | — | import `rest_framework` (not a dependency) and the deleted `TimeLimitedToken` model, so collection errors |

Each needs a product decision (what *should* signup return? should `JWTAuth` swallow an invalid
token?) or a real port of a DRF-era module. `qr_code/qr_code/api/auth.py` and `qrcode.py` are the
matching dead DRF-era source modules — only the `_new` variants are wired into `router.py`.

## Still outstanding

- **`shared/auth_client` is an empty stub.** The refactor clearly intended qr_code to delegate
  identity to user-service: it deleted `qr_code`'s `User`/`CreditTransaction` models, added
  `'users'` to qr_code's `INSTALLED_APPS`, and moved the auth pages out of `qr_code/urls.py`. None
  of the receiving end was built. The models were restored from `97eb4b9^` to get the service
  running again, so today each service still has its own `AUTH_USER_MODEL`, database and signing
  key. Finishing the consolidation means implementing `auth_client`, rewriting
  `qr_code/qr_code/migrations/0001_initial.py`, and re-pointing `admin.py` — at which point those
  restored models get deleted again.
- Both services' `README.md` and `WARP.md` still describe the old `src/` layout.

## Conventions

- Python 3.14 (pinned by `.python-version`; Dockerfiles build on `python:3.14-slim`), 100-column
  lines, PEP 8. Ruff lint set is `E,F,W,I` with `E266,E501,E701,F811` ignored; migrations are
  excluded. `E402` is ignored per-file for the two qr_code service modules that must bind
  `User = get_user_model()` partway down the import block.
- **`ruff check .` and `ruff format --check .` both pass.** Keep them that way — run
  `inv lint ruff .` before committing.
- **Single-quoted strings** (`ruff format --quote-style single`); triple-double-quoted docstrings.
- Modern type syntax: `str | None`, not `Optional[str]`. Type-checked with `ty` (see
  `[tool.ty.src]` in `pyproject.toml` and `admin/lint.py:lint_ty`), which gates `inv lint all` /
  CI. Django model/queryset attributes that only exist via metaclass magic (`.objects`,
  `.DoesNotExist`, a `ForeignKey`'s auto `_id` companion attribute) or descriptor-based field
  typing that `ty` can't infer without a django-stubs-equivalent plugin are handled at the point of
  declaration with an explicit annotation or `cast(...)`, or -- where that's not practical --
  suppressed at the point of use with a targeted `# ty: ignore[rule-name]` and a comment.
- Models, schemas, routers/api, and services are packages with one domain per file, re-exported from
  `__init__.py`. Follow this when adding to either service.
- Admin CLIs: typer apps with `no_args_is_help=True`, a module docstring as `help`, and a `--dry`
  option threaded through `admin.utils.run`.

## Design system

Sage green palette derived from the service logos. Use it for any new web interface in either
service.

| Token | Hex | Use |
|---|---|---|
| Sage Green | `#8FA89E` | brand, buttons/CTAs |
| Dark Slate | `#3B4A47` | headers, hover states |
| Light Sage | `#B5C7BE` | borders, subtle fills |
| Deep Charcoal | `#2C3432` | primary text, dark-mode bg |
| Soft Mint | `#D4E0DA` | light backgrounds, dividers |

Tailwind scale: `50 #f4f7f6 · 100 #d4e0da · 200 #b5c7be · 300 #8fa89e · 400 #728e84 · 500 #5a736a ·
600 #475a53 · 700 #3b4a47 · 800 #2c3432 · 900 #1e2422`.

Both services use django-jazzmin for the admin, themed via `COMMON_JAZZMIN_SETTINGS` with a custom
`css/jazzmin_custom.css` and a `js/admin_theme_toggle.js` light/dark toggle.
