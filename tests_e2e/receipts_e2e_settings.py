"""Settings for the server the Receipts e2e test drives: the real settings, plus fakes.

The browser needs a real server, but a Receipts upload needs S3 and a receipt needs an AI model,
neither of which an end-to-end test should touch. This module (only ever selected through
`DJANGO_SETTINGS_MODULE` by `tests_e2e/conftest.py`'s `receipts_server` fixture) keeps production
code free of test switches:

- an `INSTALLED_APPS` entry (`receipts_e2e`) whose `ready()` swaps the receipts storage and AI
  client for in-memory fakes (`apps.receipts.tests.fakes`) -- the very ones the unit tests use;
- its own `ROOT_URLCONF`, which adds `/__e2e__/s3/<key>`: the "bucket" the fake presigned URLs
  point at, so the browser's direct PUT (and the preview `<img>`) work against the same process;
- tasks run inline (`ImmediateBackend`), so no worker is needed: a receipt is processed during the
  `complete` request;
- its own SQLite file, created by the fixture with `migrate`, so the developer's `db.sqlite3` is
  never touched.

Needs the same `.env.local` as the other e2e tests (the real settings are imported).

`E2E_SERVICES=real` (set by `tests_e2e/ai/serve.py`, see `tests_e2e/e2e_env.py`) swaps the SQLite
file and the in-memory buckets and mailbox for the local services: the Postgres database in
`E2E_DATABASE_URL`, one Floci bucket for every product (`E2E_S3_BUCKET` at `E2E_S3_ENDPOINT_URL`,
whatever `.env.local` says, so a run can never reach real AWS) and SMTP/IMAP from
`tests_e2e/ai/emails.yaml` (`tests_e2e/real_services.py`). The AI models stay fakes either way.
"""

import os

import dj_database_url

from config.settings import *  # noqa: F403
from config.settings import ALLOWED_HOSTS, INSTALLED_APPS, PROJECT_ROOT

DEBUG = True  # so runserver serves the apps' static files (no `collectstatic` needed)

# `<anything>.localhost` resolves to the loopback address in browsers, and cookies are per host
# name (not per port): `tests_e2e/ai/serve.py --host qr.localhost` gives a server its own cookies.
ALLOWED_HOSTS = [*ALLOWED_HOSTS, '.localhost']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # `tests_e2e/ai/serve.py` (the AI-agent tests' server) uses its own file.
        'NAME': os.environ.get('E2E_DB_NAME') or PROJECT_ROOT / 'db.e2e.sqlite3',
        # runserver is threaded: parallel uploads write at once. Take the write lock when a
        # transaction starts and wait for it, instead of failing with "database is locked".
        'OPTIONS': {'transaction_mode': 'IMMEDIATE', 'timeout': 20},
    }
}

E2E_SERVICES = os.environ.get('E2E_SERVICES') or 'fakes'
E2E_RUN_ID = os.environ.get('E2E_RUN_ID', '')
E2E_S3_ENDPOINT_URL = os.environ.get('E2E_S3_ENDPOINT_URL', '')
E2E_S3_BUCKET = os.environ.get('E2E_S3_BUCKET', '')

if E2E_SERVICES == 'real':
    DATABASES = {'default': dj_database_url.parse(os.environ['E2E_DATABASE_URL'])}
    # One bucket, every product's keys under its own prefix, as in the deployments.
    FILE_TRANSFER_S3_BUCKET = RECEIPTS_S3_BUCKET = TODO_S3_BUCKET = E2E_S3_BUCKET
    FILE_TRANSFER_S3_ENDPOINT_URL = RECEIPTS_S3_ENDPOINT_URL = TODO_S3_ENDPOINT_URL = (
        E2E_S3_ENDPOINT_URL
    )
    FILE_TRANSFER_S3_REGION = RECEIPTS_S3_REGION = TODO_S3_REGION = 'us-east-1'
    FILE_TRANSFER_AWS_ACCESS_KEY_ID = FILE_TRANSFER_AWS_SECRET_ACCESS_KEY = 'test'
    RECEIPTS_AWS_ACCESS_KEY_ID = RECEIPTS_AWS_SECRET_ACCESS_KEY = 'test'
    TODO_AWS_ACCESS_KEY_ID = TODO_AWS_SECRET_ACCESS_KEY = 'test'

# Whatever the env file selects (`ses` locally, through Floci), the e2e app replaces the backend:
# the in-memory mailbox, or SMTP in real-services mode.
EMAIL_BACKENDS = 'console'

INSTALLED_APPS = [*INSTALLED_APPS, 'tests_e2e.receipts_e2e.E2EConfig']

ROOT_URLCONF = 'tests_e2e.receipts_urls'

TASKS = {
    'default': {
        'BACKEND': 'django.tasks.backends.immediate.ImmediateBackend',
        'QUEUES': ['default', 'receipts'],
    }
}

RATELIMIT_ENABLE = False
