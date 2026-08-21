# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository shape

Monorepo of two independently-deployable Django 6 microservices plus shared packages. Created by
`git subtree`-merging two standalone repos, then refactored twice (`src/` layout → flat layout,
then `users/` → `user-service/` and `common/` → `shared/`). **Both refactors left dangling
references** — see "Known-broken state" below before trusting any command.

```
user-service/         Django project (config/) + `users` app. SSO / identity. Port 8010.
qr_code/              Django project (config/) + `qr_code` app. QR generation. Port 8020.
shared/utils/         Installable pkg `utils` — base settings, AWS/email/qrcode CLI helpers.
shared/auth_client/   Installable pkg `auth_client` — intended cross-service auth. EMPTY STUB.
admin/                Typer CLIs for lint/test/server/pip/openapi. Exposed via `inv`.
tests_e2e/            Playwright end-to-end tests (separate `pytest_e2e.ini`).
pyproject.toml        Single source of deps, ruff/mypy/pytest config, `inv` module registry.
uv.lock               One lockfile shared by both services.
```

Each service also has a `README.md` and `WARP.md`; **both are stale** (they document the old `src/`
layout). The root `README.md` is mostly accurate but its command examples are wrong (see below).
`AGENTS.md` just redirects here.

## Commands

Tooling is exposed through `typer-invoke`, which mounts each `admin/*.py` module as an `inv`
subcommand group. The module registry is `[tool.typer-invoke].modules` in `pyproject.toml`.

```bash
inv lint all                  # ruff check --fix + ruff format + mypy
inv lint all --check          # CI mode: report only, non-zero exit
inv lint ruff <path>
inv lint mypy <path>
inv test unit                 # all services
inv test unit qr_code         # one service (positional, repeatable)
inv test e2e [--no-headless]  # Playwright, uses pytest_e2e.ini
inv server run qr_code [dev|prod]   # runserver for one service
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

### Environment loading — the two services differ

- **user-service**: plain `load_dotenv(PROJECT_ROOT / '.env')`. Simple, no env selection.
- **qr_code**: calls `select_env()` and **raises on failure** rather than falling back to defaults.
  It also registers Django system checks in `qr_code/qr_code/checks.py` that re-validate env
  selection and `EMAIL_BACKENDS` at startup.

The selection convention (implemented in `admin/environment.py`) is: `.env.<environment>` files
where `<environment>` ∈ {`dev`, `prod`}; if `ENVIRONMENT` is set, use it; otherwise require exactly
one `.env.*` file (ignoring `.env.example`) and fail if there are zero or several.

### Auth — two disconnected user models

The intended design is that `user-service` is the identity authority and other services verify via
`shared/auth_client`. **This is not implemented.** `auth_client/__init__.py` is a 0-byte file. Today
each service has its own `AUTH_USER_MODEL` (`users.User` and `qr_code.User`) with its own database
and its own JWT signing key, and `qr_code/config/settings.py` even lists `'users'` in
`INSTALLED_APPS`. Don't assume a shared identity when changing auth code.

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
python:3.13-slim runtime) and `pip install -e` the two shared packages. No database container —
services expect external DBs via `DATABASE_URL`, though `settings.py` currently hardcodes SQLite.

## Known-broken state

The repo does not currently run. These are refactor leftovers, not intentional design — fix the
reference rather than working around it, and check whether a sibling reference needs the same fix.

1. **`admin/web_app.py`**: `WebApp.USERS = 'users'`, but the directory is `user-service`. Any
   `inv server users` / `inv test unit users` dies with `FileNotFoundError: .../ziptrigo-apps/users`.
2. **`shared/utils/utils/environment.py` does not exist**, but `qr_code/config/settings.py` imports
   `from utils.environment import select_env`. All qr_code tests fail at collection with
   `ImportError: No module named 'utils.environment'`. The working implementation lives in
   `admin/environment.py` and needs to move (it is deliberately Django-free so settings can import it).
3. **`qr_code/qr_code/checks.py`** imports `from . import PROJECT_ROOT` and
   `from .common.environment import ...` — the package `__init__.py` is empty and `common/` is gone.
4. **user-service tests** import `from users.users.models import ...` (old nested layout). Correct
   is `users.models`. Affects every file under `user-service/tests/`, including `conftest.py`'s
   `AUTH_TOKEN_CLASSES` string.
5. **qr_code tests** import `from src.qr_code...` — a layout two refactors old.
6. **`shared/utils/utils/{aws,email,qrcode}.py`** are typer CLIs importing `from .utils import ...`,
   but there is no `shared/utils/utils/utils.py` — only `admin/utils.py`. Correspondingly
   `[tool.typer-invoke].modules` still lists `admin.aws`, `admin.email`, `admin.qrcode`, so `inv`
   prints three import warnings on every invocation.
7. **`qr_code/config/settings.py`** points `STATICFILES_DIRS` at `PROJECT_ROOT.parent / 'common' /
   'static'`; the assets are now at `shared/utils/utils/static`. user-service already has this right.
8. **`admin/environment.py:60`** builds a common env path from `PROJECT_ROOT / 'common'`.
9. **`tests_e2e/conftest.py`** launches the server with `cwd='users'`.
10. **No `.env.dev` files exist** (only `user-service/.env.example`), yet `docker-compose.yml`
    declares `env_file: user-service/.env.dev` and `qr_code/.env.dev`.
11. **`admin/pip.py`** defaults `VIRTUAL_ENV` to `.venv313`; the checked-out venv is `.venv` and runs
    Python 3.14, while `pyproject.toml` targets `py313`.
12. `ruff check .` reports 39 errors and `ruff format --check .` wants to reformat 13 files. Run
    `inv lint all` before committing, but scope fixes to files you touched — a repo-wide format
    would bury real changes.

## Conventions

- Python 3.13 target, 100-column lines, PEP 8. Ruff lint set is `E,F,W,I` with `E266,E501,E701,F811`
  ignored; migrations are excluded.
- **Single-quoted strings** (`ruff format --quote-style single`); triple-double-quoted docstrings.
- Modern type syntax: `str | None`, not `Optional[str]`. mypy runs with `check_untyped_defs` and
  `warn_return_any` on, `disallow_untyped_defs` off, using `mypy_django_plugin`.
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
