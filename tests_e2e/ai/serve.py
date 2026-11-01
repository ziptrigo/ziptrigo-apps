"""Start the site for the AI-agent browser tests (the Markdown files next to this one).

    python tests_e2e/ai/serve.py [--port 8765] [--host localhost] [--keep-db]

Runs the real site with `tests_e2e.receipts_e2e_settings` (in-memory S3 for Receipts and File
transfer, a fake AI model, tasks inline, rate limiting off, an in-browser mailbox at
`/__e2e__/mail/`) on its own SQLite file, `db.e2e-ai.sqlite3` (`db.e2e-ai-<port>.sqlite3` on
another port), recreated on every start unless `--keep-db`. Seeds the accounts in `ACCOUNTS`
below, then serves until Ctrl-C.

To run several test files at once in one browser, start one server per file, each on its own port
*and* host name (`--port 8771 --host qr.localhost`): cookies are shared across ports of one host
name, so two servers on plain `localhost` would sign each other's sessions out.

Needs `.env.local` at the repo root, like the other e2e tests. Run from the repo root.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='localhost', help='localhost or <name>.localhost')
    parser.add_argument('--keep-db', action='store_true')
    args = parser.parse_args()

    url = f'http://{args.host}:{args.port}'
    suffix = '' if args.port == 8765 else f'-{args.port}'
    database = ROOT / f'db.e2e-ai{suffix}.sqlite3'
    env = os.environ.copy()
    env.setdefault('ENVIRONMENT', 'local')
    env.update(
        DJANGO_SETTINGS_MODULE='tests_e2e.receipts_e2e_settings',
        E2E_DB_NAME=str(database),
        BASE_URL=url,
    )

    def manage(*command: str) -> None:
        subprocess.run([sys.executable, 'manage.py', *command], env=env, cwd=ROOT, check=True)

    if not args.keep_db:
        database.unlink(missing_ok=True)
    manage('migrate', '--noinput', '-v', '0')
    manage('shell', '-c', SEED)
    print(f'\nE2E site: {url}  (mailbox: {url}/__e2e__/mail/)\n', flush=True)
    try:
        manage('runserver', str(args.port), '--noreload')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
