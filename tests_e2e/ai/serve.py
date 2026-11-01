"""Start the site for the AI-agent browser tests (the Markdown files next to this one).

    python tests_e2e/ai/serve.py [--port 8765] [--host localhost] [--keep-db] [--fakes]
                                 [--purge-old-mail]

By default it runs against the local services (see `tests_e2e/e2e_env.py`):

- **Postgres** of `docker-compose.postgres.yml`: its own database, `ziptrigo_e2e_ai`
  (`ziptrigo_e2e_ai_<port>` on another port), dropped and created again on every start;
- **Floci** of `docker-compose.floci.yml`: one bucket, `ziptrigo-e2e-ai[-<port>]`, emptied on every
  start, for every product's files (presigned browser uploads and downloads go to Floci);
- **email over SMTP, read back over IMAP**, with the credentials in `tests_e2e/ai/emails.yaml`
  (copy `emails.example.yaml`); `/__e2e__/mail/` lists what was delivered in this run.

Before serving it checks all three (`preflight.py`; also at `/__e2e__/preflight/`) and refuses to
start when one fails. `--fakes` is the old self-contained mode: a SQLite file, in-memory buckets
and an in-memory mailbox, nothing else needed. Either way the AI models are fakes, tasks run inline
and rate limiting is off; `serve.py` seeds the accounts in `ACCOUNTS` below.

To run several test files at once in one browser, start one server per file, each on its own port
*and* host name (`--port 8771 --host qr.localhost`): cookies are shared across ports of one host
name, so two servers on plain `localhost` would sign each other's sessions out.

Shell steps of the plans run through `python tests_e2e/ai/manage.py [--port N] <command>`, which
uses the environment this script recorded for that port.

Needs `.env.local` at the repo root, like the other e2e tests. Run from the repo root.
"""

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests_e2e import e2e_env  # noqa: E402

#: Test-only credentials for the local e2e server (never used anywhere else).
PASSWORD = 'QaE2e-Passw0rd!'
ACCOUNTS = [
    # (email, name, confirmed, credits, superuser)
    ('qa.user@example.com', 'QA User', True, 100, False),
    ('qa.admin@example.com', 'QA Admin', True, 100, True),
    ('qa.broke@example.com', 'QA Broke', True, 0, False),
    ('qa.unconfirmed@example.com', 'QA Unconfirmed', False, 0, False),
]

SEED = f"""
from apps.accounts.models import User
from apps.billing.models import BillingSettings
from apps.billing.services import add_credits

# The seeded accounts hold exactly the credits listed above. They are confirmed with a plain
# save() (no `email_confirmed` signal), so with the welcome credits on they would keep a pending
# WelcomeCredit row that the daily job could later settle (+1000, so "qa.broke" would stop being a
# 0-credits account). Switch the setting off while seeding (no row is opened) and put it back, so
# accounts registered during the test still get the welcome credits.
billing_settings = BillingSettings.load()
welcome_credits = billing_settings.welcome_credits
BillingSettings.objects.filter(pk=billing_settings.pk).update(welcome_credits=0)

for email, name, confirmed, credits, superuser in {ACCOUNTS!r}:
    if User.objects.filter(email=email).exists():
        continue
    if superuser:
        user = User.objects.create_superuser(email=email, password={PASSWORD!r}, name=name)
    else:
        user = User.objects.create_user(email=email, password={PASSWORD!r}, name=name)
    user.email_confirmed = confirmed
    user.save()
    if credits:
        add_credits(user, credits, description='e2e seed', source='e2e')

BillingSettings.objects.filter(pk=billing_settings.pk).update(welcome_credits=welcome_credits)
"""

POSTGRES_HELP = 'docker compose -f docker-compose.postgres.yml up -d'
FLOCI_HELP = 'docker compose -f docker-compose.floci.yml up -d'


def fail(message: str) -> None:
    print(f'\nE2E server not started: {message}\n', file=sys.stderr, flush=True)
    raise SystemExit(1)


def check_postgres() -> None:
    import psycopg

    url = e2e_env.admin_database_url()
    if not e2e_env.is_local_url(url):
        fail(f'E2E_POSTGRES_URL must point at a local server, not {urlsplit(url).hostname}.')
    try:
        psycopg.connect(url, connect_timeout=5).close()
    except psycopg.Error as exc:
        fail(f'cannot reach Postgres ({type(exc).__name__}). Start it with: {POSTGRES_HELP}')


def recreate_database(port: int, *, keep: bool) -> None:
    """Drop and create the e2e database, or create it when missing (`keep`)."""
    import psycopg
    from psycopg import sql

    name = urlsplit(e2e_env.database_url(port)).path.lstrip('/')
    if not name.startswith(e2e_env.NAME_PREFIX):
        fail(f'refusing to recreate {name!r}: not an e2e database.')
    with psycopg.connect(e2e_env.admin_database_url(), autocommit=True) as connection:
        exists = connection.execute(
            'SELECT 1 FROM pg_database WHERE datname = %s', [name]
        ).fetchone()
        if exists and not keep:
            connection.execute(
                sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name))
            )
        if not exists or not keep:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))


def check_floci(env: dict[str, str]) -> None:
    endpoint = env['E2E_S3_ENDPOINT_URL']
    if not e2e_env.is_local_url(endpoint):
        fail(f'E2E_S3_ENDPOINT_URL must be local (Floci), not {endpoint}.')
    try:
        urllib.request.urlopen(f'{endpoint}/_localstack/health', timeout=5).close()
    except OSError as exc:
        fail(
            f'cannot reach Floci at {endpoint} ({type(exc).__name__}). Start it with: {FLOCI_HELP}'
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=e2e_env.DEFAULT_PORT)
    parser.add_argument('--host', default='localhost', help='localhost or <name>.localhost')
    parser.add_argument('--keep-db', action='store_true', help='keep the database and the bucket')
    parser.add_argument('--fakes', action='store_true', help='SQLite and in-memory S3 and mail')
    parser.add_argument(
        '--purge-old-mail',
        action='store_true',
        help='delete, from the IMAP mailbox, the emails of earlier runs (only test server mail)',
    )
    args = parser.parse_args()

    env = e2e_env.server_env(args.port, args.host, fakes=args.fakes)
    url = env['BASE_URL']

    def manage(*command: str) -> None:
        subprocess.run([sys.executable, 'manage.py', *command], env=env, cwd=ROOT, check=True)

    if args.fakes:
        database = Path(env['E2E_DB_NAME'])
        if not args.keep_db:
            database.unlink(missing_ok=True)
    else:
        try:
            e2e_env.load_mail_config()
        except e2e_env.MailConfigError as exc:
            fail(str(exc))
        check_postgres()
        check_floci(env)
        recreate_database(args.port, keep=args.keep_db)

    e2e_env.write_state(args.port, env)
    manage('migrate', '--noinput', '-v', '0')
    manage('shell', '-c', SEED)

    preflight = [sys.executable, 'tests_e2e/ai/preflight.py', '--port', str(args.port)]
    if not args.fakes:
        preflight.append('--prepare-bucket')
        if not args.keep_db:
            preflight.append('--empty-bucket')
        if args.purge_old_mail:
            preflight.append('--purge-old-mail')
    if subprocess.run(preflight, env=env, cwd=ROOT).returncode != 0:
        fail('a preflight check failed (above). Fix it and start again.')

    print(f'\nE2E site: {url}', flush=True)
    print(f'  mailbox:   {url}/__e2e__/mail/', flush=True)
    print(f'  preflight: {url}/__e2e__/preflight/', flush=True)
    print(
        f'  shell steps: python tests_e2e/ai/manage.py --port {args.port} <command>\n', flush=True
    )
    try:
        manage('runserver', str(args.port), '--noreload')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
