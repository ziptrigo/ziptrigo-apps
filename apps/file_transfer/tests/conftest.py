"""Fixtures for the file_transfer test suite. `user` comes from the root `conftest.py`."""

import pytest

from apps.billing.services import add_credits

from ..models import FileTransferSettings, Transfer, TransferStatus
from .fakes import FakeS3Storage


@pytest.fixture
def fake_storage(monkeypatch):
    """Replace the process-wide `S3Storage` (`get_storage()`'s cache) with an in-memory fake, for
    every module that calls `get_storage()` -- see `apps.file_transfer.services.storage`."""
    from ..services import storage as storage_module

    fake = FakeS3Storage()
    monkeypatch.setattr(storage_module, '_storage', fake)
    yield fake
    monkeypatch.setattr(storage_module, '_storage', None)


@pytest.fixture
def ft_settings(db) -> FileTransferSettings:
    return FileTransferSettings.load()


@pytest.fixture
def funded_user(user):
    """`user`, topped up with credits to send/re-enable transfers."""
    add_credits(user, 100, description='Test top-up', source='test')
    return user


@pytest.fixture
def draft_transfer(funded_user) -> Transfer:
    return Transfer.objects.create(owner=funded_user, status=TransferStatus.DRAFT)


@pytest.fixture
def uploaded_file(draft_transfer, fake_storage):
    """A `TransferFile` on `draft_transfer` that has finished uploading."""
    from .. import services

    file = services.add_file(draft_transfer, 'report.pdf', 1024, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 1024)
    services.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag-1'}], storage=fake_storage)
    file.refresh_from_db()
    return file
