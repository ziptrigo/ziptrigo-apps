"""`ENVIRONMENT=prod` refuses to start without the receipts settings (there is no feature flag, so
infra and the env files must land before the app does). The check lives at module level in
`config/settings.py`, so it is exercised by importing that module in a fresh interpreter."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[3]

REQUIRED = {
    'OPENAI_API_KEY': 'sk-real-key',
    'OPENAI_RECEIPTS_MODEL': 'gpt-4o-mini',
    'RECEIPTS_S3_BUCKET': 'ziptrigo-apps-123',
    'RECEIPTS_AWS_ACCESS_KEY_ID': 'AKIAREALKEY',
    'RECEIPTS_AWS_SECRET_ACCESS_KEY': 'real-secret',
}

# Everything else production needs, so a failure can only be about receipts.
OTHER_PROD_SETTINGS = {
    'ENVIRONMENT': 'prod',
    'DATABASE_URL': 'postgres://u:p@localhost:5432/db',
    'SECRET_KEY': 'a-real-secret-key',
    'JWT_SECRET': 'a-real-jwt-secret',
    'FILE_TRANSFER_AWS_ACCESS_KEY_ID': 'AKIAFT',
    'FILE_TRANSFER_AWS_SECRET_ACCESS_KEY': 'ft-secret',
    # To-do attachments (issue #184) are required in production too.
    'TODO_S3_BUCKET': 'ziptrigo-apps-123',
    'TODO_AWS_ACCESS_KEY_ID': 'AKIATODO',
    'TODO_AWS_SECRET_ACCESS_KEY': 'todo-secret',
}

# `config/settings.py` skips its "env file must exist" failure when pytest is imported (tests must
# not depend on machine-local files), so the child pretends it is under pytest while keeping every
# production check, which keys off `ENVIRONMENT` alone. `load_dotenv` is stubbed out so a developer's
# real `.env.prod` / `.env.local` can't fill in the values a test is deliberately leaving out.
CHILD = (
    "import sys, types, dotenv; sys.modules['pytest'] = types.ModuleType('pytest'); "
    'dotenv.load_dotenv = lambda *args, **kwargs: False; '
    'import config.settings'
)


def import_settings(**overrides: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENAI_', 'RECEIPTS_'))}
    env.update(OTHER_PROD_SETTINGS)
    env.update(REQUIRED)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, '-c', CHILD],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_prod_starts_when_everything_is_set():
    result = import_settings()

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name', list(REQUIRED))
def test_prod_refuses_to_start_when_a_setting_is_missing(name):
    result = import_settings(**{name: None})

    assert result.returncode != 0
    assert 'ImproperlyConfigured' in result.stderr
    assert name in result.stderr


@pytest.mark.parametrize('name', list(REQUIRED))
@pytest.mark.parametrize('placeholder', ['', '<OPENAI_API_KEY>', 'change-me'])
def test_prod_refuses_a_placeholder(name, placeholder):
    result = import_settings(**{name: placeholder})

    assert result.returncode != 0
    assert name in result.stderr


def test_the_error_names_every_missing_setting():
    result = import_settings(OPENAI_API_KEY=None, RECEIPTS_S3_BUCKET=None)

    assert 'OPENAI_API_KEY' in result.stderr and 'RECEIPTS_S3_BUCKET' in result.stderr


@pytest.mark.parametrize(
    'name', ['OPENAI_BASE_URL', 'RECEIPTS_S3_ENDPOINT_URL', 'RECEIPTS_S3_REGION']
)
def test_the_optional_settings_are_not_required(name):
    result = import_settings(**{name: None})

    assert result.returncode == 0, result.stderr


def test_local_does_not_require_any_of_them():
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENAI_', 'RECEIPTS_'))}
    env['ENVIRONMENT'] = 'local'
    result = subprocess.run(
        [sys.executable, '-c', CHILD],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
