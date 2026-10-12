"""Unit tests for `apps.file_transfer.services.storage` that don't need the full send/download
flow: the S3 key shape, and that `get_storage()` builds the generic `apps.core` `S3Storage` from
the `FILE_TRANSFER_*` settings. (`S3Storage` itself is tested in `apps/core/tests/test_storage.py`.)
"""

import pytest

from apps.core.services import storage as core_storage

from ..services import storage
from ..services.storage import storage_key, transfer_prefix

pytestmark = [pytest.mark.unit]


def test_storage_key_has_no_filename_segment():
    """Deliberately not `.../<filename>` -- see the module docstring for why (a `/`, `..` or
    otherwise unsafe name in the key itself)."""
    key = storage_key('11111111-1111-1111-1111-111111111111', 'file-id')
    assert key == 'transfers/11111111-1111-1111-1111-111111111111/file-id'


def test_transfer_prefix_covers_its_files():
    assert transfer_prefix('t1') == 'transfers/t1/'
    assert storage_key('t1', 'f1').startswith(transfer_prefix('t1'))


@pytest.fixture
def _fresh_cache():
    storage.reset_storage_cache()
    yield
    storage.reset_storage_cache()


def test_get_storage_builds_the_core_class_from_file_transfer_settings(
    settings, monkeypatch, _fresh_cache
):
    settings.FILE_TRANSFER_S3_BUCKET = 'ft-bucket'
    settings.FILE_TRANSFER_S3_REGION = 'eu-west-1'
    settings.FILE_TRANSFER_S3_ENDPOINT_URL = 'http://localhost:4566'
    settings.FILE_TRANSFER_AWS_ACCESS_KEY_ID = 'AKIATESTKEY'
    settings.FILE_TRANSFER_AWS_SECRET_ACCESS_KEY = 'test-secret'

    built = storage.get_storage()

    assert isinstance(built, core_storage.S3Storage)
    assert built.bucket == 'ft-bucket'
    assert built.client.meta.region_name == 'eu-west-1'
    assert built.client.meta.endpoint_url == 'http://localhost:4566'
    # Cached for the process, like before.
    assert storage.get_storage() is built


def test_core_names_are_still_importable_from_the_file_transfer_module():
    assert storage.S3Storage is core_storage.S3Storage
    assert storage.ObjectInfo is core_storage.ObjectInfo
    assert storage.GET_URL_EXPIRES_SECONDS == core_storage.GET_URL_EXPIRES_SECONDS
