"""Fixtures for the file_transfer test suite. `user` comes from the root `conftest.py`."""

import pytest

from apps.billing.models import BillingSettings
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
def billing_settings(db) -> BillingSettings:
    """The shared storage price (`BillingSettings`) -- metering reads it, not `ft_settings`."""
    return BillingSettings.load()


@pytest.fixture
def anon_enabled(ft_settings) -> FileTransferSettings:
    """`FileTransferSettings` with anonymous sending switched on -- off by default (spec section
    9 / CLAUDE.md: #53 rate limiting must land before this is opened to the public)."""
    ft_settings.anonymous_enabled = True
    ft_settings.save()
    return ft_settings


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
    fake_storage.seed_object(file.storage_key, 1024)
    services.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag-1'}], storage=fake_storage)
    file.refresh_from_db()
    return file


# -- File requests (issue #306) --


@pytest.fixture
def make_request(funded_user):
    """Create a file request with the given options (default: anyone, no limits) owned by
    `funded_user` (or `owner=`)."""
    from ..services.requests import RequestOptions, create_request

    def _make(owner=None, *, title='Wedding photos', **options):
        return create_request(owner or funded_user, RequestOptions(title=title, **options))

    return _make


@pytest.fixture
def anon_uploader():
    """An anonymous uploader (a browser token digest and a typed name)."""
    from ..services.request_uploader import Uploader

    return Uploader(user=None, token_hash='a' * 64, name='Jane', email='')


@pytest.fixture
def user_uploader():
    from apps.accounts.tests.factories import UserFactory

    from ..services.request_uploader import Uploader

    account = UserFactory(name='John', email='john@example.com', email_confirmed=True)
    return Uploader(user=account, token_hash='', name=account.name, email=account.email)


@pytest.fixture
def upload_to_request(fake_storage):
    """Upload a whole file to a request through the services (add, simulate the browser's PUT,
    complete), returning the finished `TransferFile`."""
    from ..services import request_upload

    def _upload(transfer, uploader, name='photo.jpg', size=1024, *, ip='9.9.9.9'):
        file = request_upload.add_file(
            transfer, uploader, name, size, ip=ip, storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, size)
        return request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'etag-1'}],
            uploader,
            ip=ip,
            storage=fake_storage,
        )

    return _upload


@pytest.fixture
def sent_emails(monkeypatch):
    """Every email `services.emails` sends, as the keyword arguments of `send_email` (`to`,
    `subject`, `text_body`, `html_body`)."""
    sent: list[dict] = []

    def _record(**kwargs):
        sent.append(kwargs)
        return 1, 0

    monkeypatch.setattr('apps.file_transfer.services.emails.send_email', _record)
    return sent
