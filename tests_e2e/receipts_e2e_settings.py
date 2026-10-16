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
"""

import os

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

INSTALLED_APPS = [*INSTALLED_APPS, 'tests_e2e.receipts_e2e.E2EConfig']

ROOT_URLCONF = 'tests_e2e.receipts_urls'

TASKS = {
    'default': {
        'BACKEND': 'django.tasks.backends.immediate.ImmediateBackend',
        'QUEUES': ['default', 'receipts'],
    }
}

RATELIMIT_ENABLE = False
