# ZipTrigo Apps

One website made of several independent products — QR codes and WeTransfer-style file transfer —
that share one account, one credit balance and one look. Built with Django and HTMX.

## Architecture

A single Django project (a "modular monolith") with one Django app per concern:

- **core** — the site shell: base layout, navigation and landing page, design-system static files,
  the admin site, email sending, the background job scheduler (`apps.core.scheduler`).
- **accounts** — the user model, sign-up/login/password reset, account pages.
- **billing** — credits: balances, the transaction ledger, the credits history page.
- **qr_code** — QR code generation, the dashboard/editor and the `/go/<code>` short links.
- **file_transfer** — WeTransfer-style file transfer: send large files via a direct-to-S3 upload,
  an expiring download link, a dashboard, and credit-metered storage. Logged-in and anonymous
  (email-confirmed, per-IP-capped, gated off by default) senders alike, resumable uploads, a JWT
  API (`/api/ft/`) and `admin/filetransfer.py` CLI (#55 phases 1-3); abuse reports, admin takedown,
  a sender block list and an optional auto-hold on repeated reports (#59).

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
├── admin/                   # Project CLIs (lint, test, server, pip, qrcode, filetransfer, ...), run via `inv`
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
| `/transfer/…` | file_transfer: send page (`send/`) and anonymous send page (`send/anon/`), dashboard, upload endpoints |
| `/t/<slug>/` | file_transfer public download links (and `/t/<slug>/manage/<token>/`, the anonymous manage link) — at the root, same reason as `/go/<code>` |
| `/api/…` | the API: `/api/auth/…`, `/api/account`, `/api/users/…`, `/api/billing/…`, `/api/qr/…`, `/api/ft/…` |
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

The web process alone is enough to browse the site, but file transfer's background jobs (metering,
expiry, cleanup) and queued emails need the worker processes too, in separate terminals:

```bash
python manage.py run_scheduler    # apps.core.scheduler: the four file_transfer jobs
python manage.py db_worker        # django_tasks_db: queued emails, deleting a transfer's objects
```

### Docker

```bash
cp .env.example .env.dev
docker compose up --build         # http://localhost:8000
```

Brings up all four services: `web` (the site), `worker` (`db_worker`), `scheduler`
(`run_scheduler`, see [Queue and scheduler in CLAUDE.md](CLAUDE.md#queue-and-scheduler)) and `db`
(this stack's own Postgres). `worker` and `scheduler` wait for `web`'s healthcheck before starting,
since only `web` applies migrations. `web`'s `DATABASE_URL` defaults to that `db` service; override
it in `.env.dev` to point elsewhere.

- Site: http://localhost:8000
- Admin: http://localhost:8000/admin/
- API docs: http://localhost:8000/api/docs

### Local AWS/S3 emulation (Floci)

File transfer stores uploaded files in S3 (settings `FILE_TRANSFER_S3_*`, see `.env.example`;
`admin/aws.py` is unrelated — it's SSO login for the AWS CLI). In dev it runs against
[Floci](https://github.com/floci/floci), a local, MIT-licensed LocalStack replacement, instead of a
real AWS account. It lives in its own compose file, `docker-compose.floci.yml`, rather than
`docker-compose.yml`, because the same container is shared with the `wsa` and `pfo` repos (see the
file's header comment for why and how).

```bash
docker compose -f docker-compose.floci.yml up -d --wait   # start
docker compose -f docker-compose.floci.yml ps             # check
docker compose -f docker-compose.floci.yml down           # stop
```

Stopping it also stops it for `wsa`/`pfo` if either has it running — it's the same container. See
wsa's `docs/playbooks/backend/LOCAL_AWS.md` for the fuller rationale, troubleshooting and
version-bump procedure.

### Shared local Postgres

The app uses Postgres when `DATABASE_URL` is set and SQLite otherwise (see
[Configuration](#configuration)). Running via `docker compose up` already gets its own Postgres for
free (the `db` service, above) — this section is for running the Django dev server directly on the
host (`inv server run`) against Postgres instead of SQLite. A shared local Postgres server is
available for that. It lives in its own compose file,
`docker-compose.postgres.yml`, rather than `docker-compose.yml`, because the same container is
shared with the `wsa` and `pfo` repos (see the file's header comment for why and how).

```bash
docker compose -f docker-compose.postgres.yml up -d --wait   # start
docker compose -f docker-compose.postgres.yml ps             # check
docker compose -f docker-compose.postgres.yml down           # stop
```

Stopping it also stops it for `wsa`/`pfo` if either has it running — it's the same container.

To use it, create a role and database on the shared server once (two commands: `CREATE DATABASE`
can't run in the same transaction as `CREATE ROLE`):

```bash
PG=postgresql://postgres:postgres@127.0.0.1:5432/postgres
psql "$PG" -c "CREATE ROLE ziptrigo LOGIN PASSWORD '...';"
psql "$PG" -c "CREATE DATABASE ziptrigo OWNER ziptrigo;"
```

then set `DATABASE_URL=postgres://ziptrigo:...@127.0.0.1:5432/ziptrigo` in `.env.dev` and run
`python manage.py migrate`.

## Configuration

Environment variables are loaded from `.env.<environment>` at the repo root (`dev` or `prod`); see
`.env.example` for the full list. If `ENVIRONMENT` is set, that file is used; otherwise there must
be exactly one `.env.*` file. `.env.example` and `.env.staging` never count as environments.

All env files live at the repo root and are gitignored:

| File | Used by | Uploaded to (on `caia`) |
|---|---|---|
| `.env.dev` | local runs | |
| `.env.prod` | `app.ziptrigo.com` | `/opt/docker/ziptrigo-apps/prod/.env` |
| `.env.staging` | `app-staging.ziptrigo.com` | `/opt/docker/ziptrigo-apps/staging/.env` |

```bash
scp .env.prod caia:/opt/docker/ziptrigo-apps/prod/.env
```

With both `.env.dev` and `.env.prod` present, set `ENVIRONMENT` to run anything directly
(`inv server run` and `inv test` already do). All three are listed in `admin/secrets_files.txt`;
back them up (encrypted, to S3) with `inv secrets backup` and get them back with
`inv secrets restore`.

The server runs both with `ENVIRONMENT=prod`, mounting the file at `/app/.env.prod`. How the rest
of the deployment works (compose services, nginx, the file transfer bucket and its credentials) is
in the `infra` repo, `apps/ziptrigo-apps/README.md`; nothing there holds a copy of these files.

With `ENVIRONMENT=prod`, the site refuses to start unless `SECRET_KEY`, `JWT_SECRET` and the file
transfer storage credentials (`FILE_TRANSFER_AWS_ACCESS_KEY_ID`,
`FILE_TRANSFER_AWS_SECRET_ACCESS_KEY`) are set to real values.

The database is Postgres when `DATABASE_URL` is set (`postgres://user:pass@host:5432/name`), and
SQLite (`db.sqlite3`) otherwise, for local development and tests. Production requires
`DATABASE_URL`: in a container, the SQLite file would be lost on every redeploy.

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

The Docker image runs gunicorn (`config.wsgi`, `WEB_CONCURRENCY` workers, 3 by default; plus the
scheduler and queue worker, see Background jobs) behind
nginx on the VPS; the deployment itself lives in the `infra` repo (`apps/ziptrigo-apps`).

- **Environment**: `.env.prod` / `.env.staging`, see [Configuration](#configuration). Needs `DATABASE_URL`.
- **Migrations**: with `RUN_MIGRATIONS=1` (set by the deployment), `docker-entrypoint.sh` runs
  `migrate` before starting gunicorn.
- **Static files**: collected at build time, served by WhiteNoise.
- **HTTPS**: nginx terminates TLS and sets `X-Forwarded-Proto`, which `SECURE_PROXY_SSL_HEADER`
  trusts; `BASE_URL`'s origin is in `CSRF_TRUSTED_ORIGINS`, and cookies are `Secure` when
  `BASE_URL` is `https://`.
- **Media files**: a host volume for now.
- **Background jobs**: one container runs everything. `supervise.py` (the image's `CMD`) starts
  gunicorn, whose workers each run the scheduler as a thread, plus the `db_worker` queue worker
  (see [Queue and scheduler in CLAUDE.md](CLAUDE.md#queue-and-scheduler)). Set
  `SCHEDULER_ENABLED=1` and leave `RUN_TASK_WORKER` at its default (`1`); without the scheduler
  flag, file transfer's metering, expiry and cleanup jobs never run (`supervise.py` logs a
  warning at start when `ENVIRONMENT=prod` and the flag is off).
  Because the supervisor exits when either child exits, **a queue-worker crash restarts the whole
  container, so it shows up as web downtime**; this was chosen on purpose (simple, Docker's restart
  policy is the single recovery mechanism). The container's stop grace period must exceed 30 s
  (40 s in the infra compose, joaonc/infra#370; Docker's default is 10 s): gunicorn's
  `graceful_timeout` 25 s < the supervisor's 30 s < 40 s.
- **nginx**: must set `X-Real-IP` from the real client address (not passed through from a
  client-supplied header) -- `file_transfer`'s download-IP logging trusts it and falls back to
  `REMOTE_ADDR` otherwise.

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
