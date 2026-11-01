# ZipTrigo Apps

One website made of several independent products — QR codes and WeTransfer-style file transfer —
that share one account, one credit balance and one look. Built with Django and HTMX.

## Architecture

A single Django project (a "modular monolith") with one Django app per concern:

- **core** — the site shell: base layout, navigation and landing page, design-system static files,
  the admin site, email sending, the background job scheduler (`apps.core.scheduler`).
- **accounts** — the user model, sign-up/login/password reset, account pages.
- **billing** — credits: balances, the transaction ledger, the credits history page.
- **feedback** — the footer's feedback page: saves a logged-in user's message, emails them a
  receipt and the superusers a notification, and the admin triages it by status (#82).
- **qr_code** — QR code generation, the dashboard/editor and the `/go/<code>` short links.
- **file_transfer** — WeTransfer-style file transfer: send large files via a direct-to-S3 upload,
  an expiring download link, a dashboard, and credit-metered storage. Logged-in and anonymous
  (email-confirmed, per-IP-capped, gated off by default) senders alike, resumable uploads, a JWT
  API (`/api/ft/`) and `admin/filetransfer.py` CLI (#55 phases 1-3); abuse reports, admin takedown,
  a sender block list and an optional auto-hold on repeated reports (#59).
- **receipts** — receipts and invoices in, extracted fields out (#98): files go to S3 and an
  OpenAI-compatible vision model reads vendor, amount, date and a category from the user's own
  list. Credit-metered per file and per GB stored. Built: models, uploads, the AI pipeline, email
  intake, billing, jobs, admin and the web pages (dashboard, upload, detail, trash, settings), plus a
  JWT API (`/api/receipts/`) and the `admin/receipts.py` CLI (#98 phase 4).
- **short_url** — a URL shortener (#162): `<site>/s/<code>` redirects to a destination you can edit
  later. Logged-in owners only. Free to create and follow; a custom alias, a password, the
  detailed analytics and every active link beyond a free allowance cost credits once per link.
  Click analytics (bots filtered, country from GeoLite2), a password page, a preview page with a
  report form, blocked domains and takedown for staff, a QR image of every link, a JWT API
  (`/api/s/`) and the `admin/shorturl.py` CLI.
- **todo** — tasks in the spirit of Todoist (#161): a Markdown description, a due date with an
  optional time (in your own time zone), a priority, coloured labels and subtasks one level deep,
  with per-user task numbers from 1000, Today / Upcoming / All / Completed / Trash pages, manual
  order and postponing. The first 10 tasks of a calendar month are free, then each costs credits.
  Repeating tasks, comments with `#1234` references, search and saved filters, reminders,
  attachments, AI date parsing in the title, tasks added by email, a JWT API (`/api/todo/`) and the
  `admin/todo.py` CLI (`inv todo ...`) (#161, #187).

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
│   ├── feedback/
│   ├── qr_code/
│   ├── file_transfer/
│   ├── receipts/
│   ├── short_url/
│   └── todo/
├── admin/                   # Project CLIs (lint, test, server, pip, qrcode, filetransfer, receipts, ...), run via `inv`
├── tests_e2e/               # Playwright end-to-end tests; ai/: test plans an AI agent runs in a browser
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
| `/auth/google/login`, `/auth/google/callback` | accounts: "Sign in with Google" (the callback is the redirect URI registered with Google, so it never moves or gains a trailing slash) |
| `/auth/microsoft/login`, `/auth/microsoft/callback` | accounts: "Sign in with Microsoft" (the callback is the redirect URI registered with Microsoft, same rule) |
| `/billing/…` | billing (credits history) |
| `/feedback/…` | feedback (the form, and the thank-you page) |
| `/qr/…` | qr_code (dashboard, create, edit, duplicate) |
| `/go/<code>` | qr_code short links — at the root because they're printed on QR codes |
| `/links/…` | short_url: dashboard (`/links/`), create and edit, a link's page and its stats (`<id>/stats/`) |
| `/s/<code>` | short_url short links (`/s/<code>+` previews one, `/s/<code>/unlock` is the password page of a protected one) — at the root because they're printed and shared |
| `/todo/…` | todo: Today (`/todo/`), `upcoming/`, `all/`, `completed/`, `trash/`, a task's page (`<number>/`), `labels/`, `settings/` |
| `/receipts/…` | receipts: dashboard (`/receipts/`), upload, a receipt's page (`<id>/`), trash, settings, CSV export |
| `/transfer/…` | file_transfer: send page (`send/`) and anonymous send page (`send/anon/`), dashboard, upload endpoints |
| `/t/<slug>/` | file_transfer public download links (and `/t/<slug>/manage/<token>/`, the anonymous manage link) — at the root, same reason as `/go/<code>` |
| `/api/…` | the API: `/api/auth/…`, `/api/account`, `/api/billing/…`, `/api/qr/…`, `/api/ft/…`, `/api/receipts/…`, `/api/s/…` (short_url), `/api/todo/…` |
| `/admin/` | Django admin (Jazzmin) |

### Dependency rules

```
qr_code, file_transfer, receipts, short_url, todo   (products: may not import each other)
        ↓
 billing, feedback
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
cp .env.example .env.local          # then fill in the placeholders
python manage.py migrate
inv server run                    # http://localhost:8000
```

The web process alone is enough to browse the site, but file transfer's background jobs (metering,
expiry, cleanup) and queued emails need the worker processes too, in separate terminals:

```bash
python manage.py run_scheduler    # apps.core.scheduler: the four file_transfer jobs
python manage.py db_worker        # django_tasks_db: queued emails, deleting a transfer's objects
python manage.py db_worker --queue-name receipts   # Receipts' AI/PDF work, its own queue
```

### Docker

```bash
cp .env.example .env.local
docker compose up --build         # http://localhost:8000
```

Brings up all five services: `web` (the site), `worker` (`db_worker`), `worker-receipts`
(`db_worker --queue-name receipts`), `scheduler`
(`run_scheduler`, see [Queue and scheduler in CLAUDE.md](CLAUDE.md#queue-and-scheduler)) and `db`
(this stack's own Postgres). The workers and `scheduler` wait for `web`'s healthcheck before starting,
since only `web` applies migrations. `web`'s `DATABASE_URL` defaults to that `db` service; override
it in `.env.local` to point elsewhere.

- Site: http://localhost:8000
- Admin: http://localhost:8000/admin/
- API docs: http://localhost:8000/api/docs

### Local AWS/S3 emulation (Floci)

File transfer, To-do attachments and Receipts store files in S3 (settings `FILE_TRANSFER_S3_*`,
`TODO_S3_*` and `RECEIPTS_S3_*`, see `.env.example`; all three use the same bucket, `ziptrigo-dev`
locally; `admin/aws.py` is unrelated — it's SSO login for the AWS CLI). Locally it runs against
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

Point each `*_S3_ENDPOINT_URL` in `.env.local` at `http://localhost:4566` (from inside a container,
`http://host.docker.internal:4566`); any access key and secret work, e.g. `test`. Floci persists to
a named volume, so the bucket and its CORS rules survive a restart. Create them once (the browser
uploads straight to the bucket with presigned URLs, so CORS must allow the checksum header and expose
`ETag`):

```bash
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
aws --endpoint-url http://localhost:4566 s3 mb s3://ziptrigo-dev
aws --endpoint-url http://localhost:4566 s3api put-bucket-cors --bucket ziptrigo-dev \
  --cors-configuration '{"CORSRules":[{"AllowedOrigins":["http://localhost:8000","http://127.0.0.1:8000"],"AllowedMethods":["GET","PUT","HEAD"],"AllowedHeaders":["*"],"ExposeHeaders":["ETag"],"MaxAgeSeconds":3000}]}'
```

Floci also emulates SES, so local email can go through it instead of the console: set
`EMAIL_BACKENDS=ses`, `AWS_ENDPOINT_URL_SES=http://localhost:4566` and any `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` in `.env.local`, and verify the sender once:

```bash
aws --endpoint-url http://localhost:4566 ses verify-email-identity --email-address no-reply@ziptrigo.com
```

Floci stores what it is sent instead of delivering it: read the messages (subject, bodies and the
confirmation or reset links in them) from `http://localhost:4566/_aws/ses`. Unlike real SES it does
not enforce sender verification, so a send from an unverified address also succeeds locally.

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
psql "$PG" -c "CREATE ROLE ziptrigo LOGIN CREATEDB PASSWORD '...';"
psql "$PG" -c "CREATE DATABASE ziptrigo OWNER ziptrigo;"
```

then set `DATABASE_URL=postgres://ziptrigo:...@127.0.0.1:5432/ziptrigo` in `.env.local` and run
`python manage.py migrate`. `CREATEDB` is there so pytest can create its `test_ziptrigo` database
when it runs against this server (an existing role: `ALTER ROLE ziptrigo CREATEDB;`).

## Configuration

Environment variables are loaded from `.env.<environment>` at the repo root (`local` or `prod`); see
`.env.example` for the full list. If `ENVIRONMENT` is set, that file is used; otherwise there must
be exactly one `.env.*` file. `.env.example` and `.env.staging` never count as environments.

All env files live at the repo root and are gitignored:

| File | Used by | Uploaded to (on `caia`) |
|---|---|---|
| `.env.local` | local runs | |
| `.env.prod` | `app.ziptrigo.com` | `/opt/docker/ziptrigo-apps/prod/.env` |
| `.env.staging` | `app-staging.ziptrigo.com` | `/opt/docker/ziptrigo-apps/staging/.env` |

```bash
scp .env.prod caia:/opt/docker/ziptrigo-apps/prod/.env
```

With both `.env.local` and `.env.prod` present, set `ENVIRONMENT` to run anything directly
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

### Sign in with Google

Login and register pages offer "Sign in with Google" (a server-side OpenID Connect authorization
code flow, [Authlib](https://docs.authlib.org/)) when these variables are all set; leave them empty
and the button is hidden and `/auth/google/*` just redirects to the login page with a message. A
partial set triggers a warning at startup (`manage.py check`, `runserver`). Nothing is read from or
written to a file other than your `.env.<environment>`, and the client secret never leaves the
server.

| Variable | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | the OAuth client's ID, `….apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | the OAuth client's secret |
| `GOOGLE_REDIRECT_URI` | this site's `/auth/google/callback`, e.g. `https://app-staging.ziptrigo.com/auth/google/callback` (local: `http://localhost:8000/auth/google/callback`) |

To get them, in the [Google Cloud console](https://console.cloud.google.com/) under *APIs &
Services*:

1. *OAuth consent screen*: set the app up (name, support email; scopes `openid`, `email`,
   `profile` are non-sensitive). While it is in **Testing** mode only the *test users* you add
   there can sign in; publish it to allow anyone.
2. *Credentials* > *Create credentials* > *OAuth client ID*, application type **Web application**.
3. Under *Authorized redirect URIs* add `GOOGLE_REDIRECT_URI` **exactly** as it is in the env file
   (scheme, host, port and path; no trailing slash). A mismatch fails with `redirect_uri_mismatch`.
   Use one OAuth client (or at least one redirect URI) per environment.
4. Copy the client ID and secret into the environment's env file (`.env.dev`, `.env.staging`,
   `.env.prod`), never into the repo.

A Google sign-in uses the same session as the password login (no JWT is issued) and requires a
verified Google email. It signs in the account linked to the Google account, or else links the
existing account with that email -- only if that account's email is confirmed *and* it is a
`gmail.com`/`googlemail.com` address or the Google account belongs to a Workspace domain (`hd`);
otherwise it is refused and the user signs in with their password -- or else creates a new account
(any verified email). See CLAUDE.md, "Auth".

### Sign in with Microsoft

Login and register pages also offer "Sign in with Microsoft" (the Microsoft identity platform,
Entra ID; an OpenID Connect authorization code flow like Google's) when these variables are all
set; leave them empty and the button is hidden and `/auth/microsoft/*` redirects to the login page
with a message. A partial set triggers a warning at startup (`accounts.W004`).

| Variable | Value |
|---|---|
| `MICROSOFT_CLIENT_ID` | the app registration's *Application (client) ID* |
| `MICROSOFT_CLIENT_SECRET` | a client secret's **Value** (not its *Secret ID*; it is shown once) |
| `MICROSOFT_REDIRECT_URI` | this site's `/auth/microsoft/callback`, e.g. `https://app-staging.ziptrigo.com/auth/microsoft/callback` (local: `http://localhost:8000/auth/microsoft/callback`) |
| `MICROSOFT_TENANT` | optional: `common` (default), `consumers` (personal only), `organizations` (work/school only) or one Directory (tenant) ID |

To get them, in the [Microsoft Entra admin center](https://entra.microsoft.com/) (a free Azure
account gives a personal Microsoft account the tenant it needs), under *App registrations*:

1. *New registration* -- one per environment (prod, staging), so a leaked secret only opens one.
   Pick the **supported account types** (personal accounts only, or also work/school; this must
   agree with `MICROSOFT_TENANT`). A registration created as "single tenant" has to be moved to
   personal accounts in the *Manifest* (`signInAudience`, and `api.requestedAccessTokenVersion`
   set to `2`) before the portal allows the change.
2. Under *Authentication* add a **Web** redirect URI: `MICROSOFT_REDIRECT_URI` **exactly** as it
   is in the env file (scheme, host, port and path; no trailing slash). A mismatch fails with
   `AADSTS50011`.
3. *Certificates & secrets* > *New client secret*: copy the **Value** at once and note the expiry
   (at most 24 months). An expired secret turns the button into a failure page.
4. If work/school accounts are allowed, under *Token configuration* add the optional claims
   `email` and `xms_edov` to the **ID token**: without `xms_edov` such a user can still sign in
   once linked, but cannot create or link an account by email.
5. Copy the client ID and secret into the environment's env file, never into the repo.

A Microsoft sign-in uses the same session as the password login (no JWT is issued). It signs in the
account linked to the Microsoft account (its tenant and object id, `oid`, which survives
recreating the app registration, unlike `sub`), or else links an existing account with the same
email or creates a new one -- both only for an address Microsoft vouches for: a personal account
on a Microsoft-hosted mailbox (`outlook.com`, `hotmail.com`, `live.com`, `msn.com` and their
country variants, from an explicit list in `microsoft_oauth.py`; one missing there is refused, so
extend it), or a work/school account with `xms_edov`; linking also needs the existing account's email
to be confirmed. Otherwise it is refused and the user signs in with their password or Google. See
CLAUDE.md, "Auth", for why Microsoft's `email` claim is not trusted like Google's.

### Signup captcha (Cloudflare Turnstile)

The register page, the only way to create an account, has a Cloudflare Turnstile captcha when both
of these are set. Leave both empty and the widget is hidden and the check skipped. Setting only one
triggers a startup warning (`accounts.W002`).

| Variable | Value |
|---|---|
| `TURNSTILE_SITE_KEY` | the widget's site key (public, rendered in the page) |
| `TURNSTILE_SECRET_KEY` | the widget's secret key (server only) |

To get them, in the [Cloudflare dashboard](https://dash.cloudflare.com/?to=/:account/turnstile):

1. *Turnstile* > *Add widget*, mode **Managed**.
2. Add the environment's hostname (`app.ziptrigo.com`, `app-staging.ziptrigo.com`). The server
   rejects a token whose hostname isn't `BASE_URL`'s host, so the two must agree.
3. Copy the site key and secret into that environment's env file (`.env.prod`, `.env.staging`) and
   upload it (see the table above).

For local runs, Cloudflare's test keys work on any host: site key `1x00000000000000000000AA` with
secret `1x0000000000000000000000000000000AA` always passes (`2x...` always fails). They skip the
hostname check, so they must never reach a deployed env file: with a non-local `BASE_URL`, a test
secret triggers a warning (`accounts.W003`) in `manage.py check` and in the container's `migrate`
step at every deploy.

The check fails closed: if Cloudflare can't be reached, signup is refused until it can be. Login
and "Sign up with Google" have no captcha.

### Short URLs: country statistics (GeoLite2)

Short links record the country of each click from a MaxMind **GeoLite2-Country** database. It is
optional: without it every country is "unknown", `manage.py check` (and the container's `migrate`
step) prints `short_url.W001`, and the rest of the statistics are unaffected. Nothing about a visitor
but the country is stored: a click keeps a keyed hash of the address that changes every day, never
the address.

| Variable | Value |
|---|---|
| `GEOIP_PATH` | path to the `GeoLite2-Country.mmdb` file (unset: countries unknown) |

**Where to get the file** (the file is MaxMind's and its licence forbids committing it here):

1. Create a free [MaxMind account](https://www.maxmind.com/en/geolite2/signup) and accept the
   GeoLite2 licence.
2. Download *GeoLite2 Country* (format: GeoIP2 Binary, `.mmdb`) from
   [your account's database downloads](https://www.maxmind.com/en/accounts/current/geoip/downloads);
   the `.mmdb` is inside the tarball. For something that stays current, use MaxMind's
   [`geoipupdate`](https://dev.maxmind.com/geoip/updating-databases/) with a license key from the
   same account.
3. Point `GEOIP_PATH` at the file, e.g. in `.env.local`. The file is opened once per process
   (memory-mapped), so restart the server after replacing it.

**In staging and prod** the file is not managed from this repository. It lives on the host, is
refreshed twice a week by a cron job there, is mounted read-only into the containers (which get
`GEOIP_PATH` from the compose file, so the env files here need no `GEOIP_PATH`), and is shared with
any other container that wants it. How it is retrieved and kept current, the MaxMind credentials,
how to check it and how to use it from another container are documented in the `infra` repository:
[`docs/geoip.md`](https://github.com/joaonc/infra/blob/main/docs/geoip.md) (and
[`apps/ziptrigo-apps/README.md`](https://github.com/joaonc/infra/blob/main/apps/ziptrigo-apps/README.md)
for this app's part).

### Short URLs: Safe Browsing

Short link destinations are checked against [Google Safe Browsing](https://developers.google.com/safe-browsing)
(Lookup API v4: malware, phishing, unwanted software, harmful apps). It is optional: without a key
the check is skipped and `manage.py check` (and the container's `migrate` step) prints
`short_url.W002`.

| Variable | Value |
|---|---|
| `SAFE_BROWSING_API_KEY` | API key of a Google Cloud project with the *Safe Browsing API* enabled (unset: no checks) |
| `SAFE_BROWSING_TIMEOUT_SECONDS` | per-request timeout, default `3` |

To get a key, create (or pick) a Google Cloud project, enable *Safe Browsing API* under
*APIs & Services*, and create an API key under *Credentials*; restrict it to that API. Put it in
`.env.prod` and `.env.staging`, which are copied to the VPS (see `admin/secrets_files.txt`).

What it does:

- **Creating a link, or changing its destination**: a flagged destination is refused with the
  same neutral message as a blocked host ("That address cannot be used.", a 403 from the API).
- **Every day**: active links are checked again (a scheduler job, one API request an hour for up to
  500 destinations). A link whose destination became flagged is put on hold (unavailable, its owner
  can only disable it) and shows up in the admin's *Link reports* as a pending report from
  *Safe Browsing*, for staff to take down or release. Releasing it, or dismissing that report,
  tells the check to leave that destination alone until the owner changes it.
- **When Google can't be reached** (timeout, quota, outage) nothing is refused or held; the link is
  simply checked on a later run.

The Lookup API receives the destination URLs themselves, so the privacy policy should say that links
are checked with Google. Google offers it for non-commercial use; for commercial use it points to
[Web Risk](https://cloud.google.com/web-risk), which has the same shape and would only change
`apps/short_url/services/safe_browsing.py`.

## Development Workflow

```bash
inv test unit                     # all apps
inv test unit qr_code billing     # some apps
inv test e2e
inv lint all                      # ruff + ty + import-linter
inv lint all --check              # CI mode
```

### AI-agent end-to-end test plans

`tests_e2e/ai/` holds plain-language test plans, one per area, that an AI agent runs by driving a
browser against a local test server, on demand (they take a while; they are not part of CI). The
server uses the local services in docker plus a real mailbox, and checks them before it serves:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

```bash
docker compose -f docker-compose.floci.yml up -d
```

```bash
cp tests_e2e/ai/emails.example.yaml tests_e2e/ai/emails.yaml
```

```bash
python tests_e2e/ai/serve.py
```

Fill in the SMTP/IMAP credentials of a test mailbox in `emails.yaml` (gitignored) before starting.
See `tests_e2e/ai/README.md` for how to run the plans, and its "Keeping the plans current": a
change to anything a user or an admin can see or do updates the plan of its area in the same PR.

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
  gunicorn, whose workers each run the scheduler as a thread, plus two `db_worker` queue workers
  (`default` and `receipts`; see [Queue and scheduler in CLAUDE.md](CLAUDE.md#queue-and-scheduler)). Set
  `SCHEDULER_ENABLED=1` and leave `RUN_TASK_WORKER` at its default (`1`); without the scheduler
  flag, file transfer's metering, expiry and cleanup jobs never run (`supervise.py` logs a
  warning at start when `ENVIRONMENT=prod` and the flag is off).
  Because the supervisor exits when any child exits, **a queue-worker crash restarts the whole
  container, so it shows up as web downtime**; this was chosen on purpose (simple, Docker's restart
  policy is the single recovery mechanism). The container's stop grace period must exceed 30 s
  (40 s in the infra compose, joaonc/infra#370; Docker's default is 10 s): gunicorn's
  `graceful_timeout` 25 s < the supervisor's 30 s < 40 s.
- **nginx**: must set `X-Real-IP` from the real client address (not passed through from a
  client-supplied header) -- `file_transfer`'s download-IP logging trusts it and falls back to
  `REMOTE_ADDR` otherwise. The `infra` repo's `apps/ziptrigo-apps/nginx.conf` also applies a
  coarse per-IP `limit_req` (20 r/s, `burst=100`; short links on their own looser zone, 50 r/s,
  `burst=500`: QR's `/go/<code>` and Short URLs' `/s/<code>`, `/s/<code>+` and
  `/s/<code>/unlock`, sized well above what a crowd behind one shared address produces;
  `/s/<code>/report` stays in the main zone; both zones answer 429 with a short plain-text body and
  `Retry-After: 1`) as defense-in-depth below the app's own rate limits (issues #119, #170); those
  limits remain the real ones.

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
