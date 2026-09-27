# ZipTrigo Apps

One website made of several independent products — QR codes today, file transfer next — that share
one account, one credit balance and one look. Built with Django and HTMX.

## Architecture

A single Django project (a "modular monolith") with one Django app per concern:

- **core** — the site shell: base layout, navigation and landing page, design-system static files,
  the admin site, email sending.
- **accounts** — the user model, sign-up/login/password reset, account pages.
- **billing** — credits: balances, the transaction ledger, the credits history page.
- **qr_code** — QR code generation, the dashboard/editor and the `/go/<code>` short links.
- **file_transfer** — file transfers (skeleton for now).

`core`, `accounts` and `billing` are shared by every product. Products never import each other, so
each can grow (or be removed) on its own; see [Dependency rules](#dependency-rules).

Why one project rather than a service per product: the products share a login, a credit balance
and a page layout, and HTMX works best with server-rendered pages on one origin with one session
cookie. Separate services would each need their own copy of the templates and a single-sign-on
layer before a page could render. A product that later needs its own scaling can still run as a
separate process of the same codebase, routed by URL prefix.

## Project Structure

```
ziptrigo-apps/
├── manage.py
├── config/                  # The Django project
│   ├── settings.py
│   ├── environment.py       # `.env.<environment>` selection (Django-free)
│   ├── urls.py              # Mounts each app under its prefix
│   └── api.py               # One Django Ninja API; each app adds a router
├── apps/
│   ├── core/
│   ├── accounts/
│   ├── billing/
│   ├── qr_code/
│   └── file_transfer/
├── admin/                   # Project CLIs (lint, test, server, pip, ...), run via `inv`
├── tests_e2e/               # Playwright end-to-end tests
├── conftest.py              # Fixtures shared by every app's tests
├── Dockerfile
└── docker-compose.yml
```

Each app has the same shape:

```
apps/<app>/
├── apps.py                  # AppConfig; products register themselves with `core` here
├── models/  services/  schemas/  views/  api/     # packages, one domain per file
├── urls.py
├── admin.py                 # registers on `apps.core.admin_site.custom_admin_site`
├── migrations/
├── templates/<app>/         # namespaced
├── static/<app>/            # namespaced
└── tests/
```

### URL map

| Prefix | App |
|---|---|
| `/` | core landing page |
| `/account/…` | accounts (login, register, logout, password reset, email confirmation, settings) |
| `/billing/…` | billing (credits history) |
| `/qr/…` | qr_code (dashboard, create, edit, duplicate) |
| `/go/<code>` | qr_code short links — at the root because they're printed on QR codes |
| `/transfer/…` | file_transfer |
| `/api/…` | the API: `/api/auth/…`, `/api/account`, `/api/users/…`, `/api/billing/…`, `/api/qr/…` |
| `/admin/` | Django admin (Jazzmin) |

### Dependency rules

```
qr_code, file_transfer   (products: may not import each other)
        ↓
     billing
        ↓
     accounts
        ↓
       core
```

An app may only import from the layers below it. Products use `billing.services` (e.g.
`spend_credits(user, 5, source='qr_code')`) and never touch another product. `import-linter`
enforces this (`inv lint imports`, contract in `pyproject.toml`).

## Getting Started

### Prerequisites

- Python 3.14+
- `uv`
- Docker and Docker Compose (for containerized development)

### Local Development

```bash
uv venv --python 3.14
source .venv/bin/activate
inv pip sync
cp .env.example .env.dev          # then fill in the placeholders
python manage.py migrate
inv server run                    # http://localhost:8000
```

### Docker

```bash
cp .env.example .env.dev
docker compose up --build         # http://localhost:8000
```

- Site: http://localhost:8000
- Admin: http://localhost:8000/admin/
- API docs: http://localhost:8000/api/docs

### Local AWS/S3 emulation (Floci)

No app code here uses S3 yet, but when it does (`admin/aws.py` is unrelated — it's SSO login for
the AWS CLI), it'll run against [Floci](https://github.com/floci/floci), a local, MIT-licensed
LocalStack replacement, instead of a real AWS account. It lives in its own compose file,
`docker-compose.floci.yml`, rather than `docker-compose.yml`, because the same container is shared
with the `wsa` and `pfo` repos (see the file's header comment for why and how).

```bash
docker compose -f docker-compose.floci.yml up -d --wait   # start
docker compose -f docker-compose.floci.yml ps             # check
docker compose -f docker-compose.floci.yml down           # stop
```

Stopping it also stops it for `wsa`/`pfo` if either has it running — it's the same container. See
wsa's `docs/playbooks/backend/LOCAL_AWS.md` for the fuller rationale, troubleshooting and
version-bump procedure.

## Configuration

Environment variables are loaded from `.env.<environment>` at the repo root (`dev` or `prod`); see
`.env.example` for the full list. If `ENVIRONMENT` is set, that file is used; otherwise there must
be exactly one `.env.*` file.

With `ENVIRONMENT=prod`, the site refuses to start unless `SECRET_KEY` and `JWT_SECRET` are set to
real values.

The database is SQLite (`db.sqlite3`) for now.

## Development Workflow

```bash
inv test unit                     # all apps
inv test unit qr_code billing     # some apps
inv test e2e
inv lint all                      # ruff + ty + import-linter
inv lint all --check              # CI mode
```

### Package Management

`uv` with one `uv.lock`, wrapped by `inv pip`:

```bash
inv pip sync                      # everything
inv pip sync dev                  # main + dev tools
inv pip package dev -p django     # upgrade one package
inv pip compile --clean           # re-lock from scratch
```

Scopes: `main` (runtime, `[project.dependencies]`) and `dev` (tooling, `[dependency-groups].dev`).

### Adding an App

1. Create `apps/<name>/` with the layout above; set `name = 'apps.<name>'` and `label = '<name>'`
   in its `AppConfig`.
2. For a product, register a `ProductApp` in `AppConfig.ready()` (see `apps/qr_code/apps.py`) so it
   shows up in the navigation and on the landing page.
3. Add it to `INSTALLED_APPS`, mount its URLs in `config/urls.py` (use `app_name` to namespace
   them) and, if it has an API, add its router in `config/api.py`.
4. Add it to the products layer of the import-linter contract in `pyproject.toml`.

## Deployment

1. **Environment Variables**: Use production-ready secrets and configurations
2. **Database**: Move off SQLite
3. **Static Files**: WhiteNoise serves them; run `collectstatic`
4. **Media Files**: Configure media file storage (S3, cloud storage, etc.)
5. **Migrations**: Run migrations during deployment
6. **WSGI Server**: Replace `runserver` with gunicorn or uvicorn

## Design System

### Color Palette

The ZipTrigo brand uses a sage green color palette derived from the logos. Use these colors when
building web pages and interfaces.

#### Primary Colors
- **Sage Green**: `#8FA89E` - Main brand color (mid-tone green-gray)
- **Dark Slate**: `#3B4A47` - Dark gray-green for text and accents
- **Light Sage**: `#B5C7BE` - Lighter variant for backgrounds and subtle elements

#### Supporting Colors
- **Deep Charcoal**: `#2C3432` - Darkest tone for primary text and borders
- **Soft Mint**: `#D4E0DA` - Very light green-gray for backgrounds
- **White**: `#FFFFFF` - For contrast and backgrounds

#### Suggested Usage
- **Headers/Primary Text**: Deep Charcoal or Dark Slate
- **Backgrounds (Light Mode)**: White or Soft Mint
- **Backgrounds (Dark Mode)**: Deep Charcoal with Dark Slate accents
- **Buttons/CTAs**: Sage Green with white text
- **Hover States**: Dark Slate
- **Borders/Dividers**: Light Sage or Soft Mint

#### Tailwind CSS Configuration

```css
colors: {
  sage: {
    50: '#f4f7f6',
    100: '#d4e0da',
    200: '#b5c7be',
    300: '#8fa89e',
    400: '#728e84',
    500: '#5a736a',
    600: '#475a53',
    700: '#3b4a47',
    800: '#2c3432',
    900: '#1e2422'
  }
}
```

## Git History

This repository was created by merging two separate repositories (a users service and a QR code
service) using git subtree, then consolidated into a single Django project.

## License

MIT — see [LICENSE](LICENSE).
