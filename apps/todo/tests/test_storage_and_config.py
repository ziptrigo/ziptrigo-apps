"""The storage wiring (keys, the SigV4 client), the `todo.W002` check and `ENVIRONMENT=prod`
refusing to start without the bucket and its keys (the check lives at module level in
`config/settings.py`, so it is exercised by importing that module in a fresh interpreter)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.checks import Warning
from django.test.utils import override_settings

from .. import checks
from ..models import TodoSettings
from ..services import storage

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestKeys:
    def test_attachment_keys_are_by_id_under_the_users_prefix(self):
        assert storage.attachment_key('u1', 'a1') == 'todo/users/u1/attachments/a1'
        assert storage.user_prefix('u1') == 'todo/users/u1/'
        assert storage.attachment_key('u1', 'a1').startswith(storage.user_prefix('u1'))

    def test_the_prefix_does_not_match_a_longer_id(self):
        assert not 'todo/users/10/attachments/a'.startswith(storage.user_prefix('1'))


class TestGetStorage:
    @pytest.fixture(autouse=True)
    def _real_storage(self, monkeypatch):
        monkeypatch.setattr(storage, '_storage', None)

    def test_builds_a_sigv4_client_from_the_todo_settings(self):
        with override_settings(
            TODO_S3_BUCKET='todo-bucket',
            TODO_S3_REGION='eu-west-1',  # a region where botocore signs presigned URLs with SigV2
            TODO_S3_ENDPOINT_URL='http://localhost:4566',
            TODO_AWS_ACCESS_KEY_ID='test',
            TODO_AWS_SECRET_ACCESS_KEY='test',
        ):
            built = storage.get_storage()

            assert built.bucket == 'todo-bucket'
            assert storage.get_storage() is built  # cached
            # It fails closed on a client that doesn't sign Content-Length, so this only works under
            # SigV4: that is what makes S3 refuse a body of another size.
            url = built.presigned_put_url(
                storage.attachment_key('u', 'a'), content_length=10, checksum_sha256='x' * 44
            )
            assert 'content-length' in url.lower()
            assert 'x-amz-checksum-sha256' in url.lower()
            assert 'X-Amz-Signature' in url

    def test_reset_rebuilds_it(self):
        with override_settings(
            TODO_S3_BUCKET='one', TODO_AWS_ACCESS_KEY_ID='k', TODO_AWS_SECRET_ACCESS_KEY='s'
        ):
            first = storage.get_storage()
        storage.reset_storage_cache()
        with override_settings(
            TODO_S3_BUCKET='two', TODO_AWS_ACCESS_KEY_ID='k', TODO_AWS_SECRET_ACCESS_KEY='s'
        ):
            assert storage.get_storage().bucket == 'two'
        assert first.bucket == 'one'


class TestCheck:
    def run(self, **values) -> list[Warning]:
        base = {
            'TODO_S3_BUCKET': '',
            'TODO_AWS_ACCESS_KEY_ID': '',
            'TODO_AWS_SECRET_ACCESS_KEY': '',
        }
        with override_settings(**{**base, **values}):
            return checks.check_todo_storage_configuration()

    def test_nothing_set_is_fine(self):
        assert self.run() == []

    def test_everything_set_is_fine(self):
        assert (
            self.run(TODO_S3_BUCKET='b', TODO_AWS_ACCESS_KEY_ID='k', TODO_AWS_SECRET_ACCESS_KEY='s')
            == []
        )

    @pytest.mark.parametrize(
        'values',
        [
            {'TODO_S3_BUCKET': 'b'},
            {'TODO_S3_BUCKET': 'b', 'TODO_AWS_ACCESS_KEY_ID': 'k'},
            {'TODO_S3_BUCKET': 'b', 'TODO_AWS_SECRET_ACCESS_KEY': 's'},
            {'TODO_AWS_ACCESS_KEY_ID': 'k', 'TODO_AWS_SECRET_ACCESS_KEY': 's'},
            {'TODO_AWS_ACCESS_KEY_ID': 'k'},
        ],
    )
    def test_a_partial_configuration_warns(self, values):
        [warning] = self.run(**values)

        assert isinstance(warning, Warning)
        assert warning.id == 'todo.W002'

    def test_the_check_is_registered(self):
        from django.core.checks import registry

        assert checks.check_todo_storage_configuration in registry.registry.registered_checks


def test_the_attachment_limits_default_to_50_mib_and_20_files(db):
    config = TodoSettings.load()

    assert config.max_attachment_bytes == 50 * 1024**2
    assert config.max_attachments_per_task == 20


# -- ENVIRONMENT=prod ----------------------------------------------------------------------------

REQUIRED = {
    'TODO_S3_BUCKET': 'ziptrigo-apps-123',
    'TODO_AWS_ACCESS_KEY_ID': 'AKIAREALKEY',
    'TODO_AWS_SECRET_ACCESS_KEY': 'real-secret',
}

# Everything else production needs, so a failure can only be about the To-do settings.
OTHER_PROD_SETTINGS = {
    'ENVIRONMENT': 'prod',
    'DATABASE_URL': 'postgres://u:p@localhost:5432/db',
    'SECRET_KEY': 'a-real-secret-key',
    'JWT_SECRET': 'a-real-jwt-secret',
    'FILE_TRANSFER_AWS_ACCESS_KEY_ID': 'AKIAFT',
    'FILE_TRANSFER_AWS_SECRET_ACCESS_KEY': 'ft-secret',
    'OPENAI_API_KEY': 'sk-real-key',
    'OPENAI_RECEIPTS_MODEL': 'gpt-4o-mini',
    'RECEIPTS_S3_BUCKET': 'ziptrigo-apps-123',
    'RECEIPTS_AWS_ACCESS_KEY_ID': 'AKIAREC',
    'RECEIPTS_AWS_SECRET_ACCESS_KEY': 'rec-secret',
}

# See `apps/receipts/tests/test_prod_settings.py` for why the child pretends to run under pytest
# and stubs `load_dotenv`.
CHILD = (
    "import sys, types, dotenv; sys.modules['pytest'] = types.ModuleType('pytest'); "
    'dotenv.load_dotenv = lambda *args, **kwargs: False; '
    'import config.settings'
)


def import_settings(**overrides: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENAI_', 'TODO_'))}
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
@pytest.mark.parametrize('placeholder', ['', '<TODO_ACCESS_KEY_ID>', 'change-me'])
def test_prod_refuses_a_placeholder(name, placeholder):
    result = import_settings(**{name: placeholder})

    assert result.returncode != 0
    assert name in result.stderr


def test_the_error_names_every_missing_setting():
    result = import_settings(TODO_S3_BUCKET=None, TODO_AWS_SECRET_ACCESS_KEY=None)

    assert 'TODO_S3_BUCKET' in result.stderr and 'TODO_AWS_SECRET_ACCESS_KEY' in result.stderr


@pytest.mark.parametrize('name', ['TODO_S3_ENDPOINT_URL', 'TODO_S3_REGION'])
def test_the_optional_settings_are_not_required(name):
    result = import_settings(**{name: None})

    assert result.returncode == 0, result.stderr


def test_local_does_not_require_any_of_them():
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENAI_', 'TODO_'))}
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
