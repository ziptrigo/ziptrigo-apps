"""The "download all" zip (spec section 5): streaming build, claim-once concurrency, failure/retry,
and download counting/password gating.
"""

import io
import zipfile

import pytest
from django.core.exceptions import ValidationError

from ..models import DownloadEvent, Transfer, TransferStatus, ZipStatus
from ..services.downloads import record_download
from ..services.uploads import add_file, complete_file_upload
from ..services.zip import build_zip, ensure_zip_build_started, zip_key

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _make_active_transfer(funded_user, fake_storage, *, files: dict[str, bytes]) -> Transfer:
    transfer = Transfer.objects.create(owner=funded_user, status=TransferStatus.DRAFT)
    for name, content in files.items():
        file = add_file(transfer, name, len(content), storage=fake_storage)
        fake_storage.put_object(file.storage_key, len(content))
        fake_storage.objects[file.storage_key] = content
        complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag'}], storage=fake_storage)
    transfer.status = TransferStatus.ACTIVE
    transfer.size_bytes = sum(len(c) for c in files.values())
    transfer.save()
    return transfer


def test_build_zip_produces_a_valid_zip_with_the_right_contents(funded_user, fake_storage):
    transfer = _make_active_transfer(
        funded_user, fake_storage, files={'a.txt': b'hello world', 'b.txt': b'x' * 5000}
    )
    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.READY
    assert transfer.zip_key == zip_key(transfer.id)

    zip_bytes = fake_storage.objects[transfer.zip_key]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert set(zf.namelist()) == {'a.txt', 'b.txt'}
        assert zf.read('a.txt') == b'hello world'
        assert zf.read('b.txt') == b'x' * 5000


def test_ensure_zip_build_started_only_claims_once(
    funded_user, fake_storage, django_capture_on_commit_callbacks
):
    transfer = _make_active_transfer(funded_user, fake_storage, files={'a.txt': b'hi'})

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        ensure_zip_build_started(transfer)
        transfer.refresh_from_db()
        assert transfer.zip_status == ZipStatus.BUILDING

        # A second, concurrent request for the same transfer must not enqueue a second build.
        ensure_zip_build_started(transfer)

    assert len(callbacks) == 1


def test_build_zip_marks_failed_on_source_read_error(funded_user, fake_storage):
    transfer = _make_active_transfer(funded_user, fake_storage, files={'a.txt': b'hi'})
    # Simulate the source object having vanished from S3 between upload and build.
    file = transfer.files.get(name='a.txt')
    del fake_storage.objects[file.storage_key]

    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.FAILED


def test_failed_build_can_be_retried(funded_user, fake_storage):
    transfer = _make_active_transfer(funded_user, fake_storage, files={'a.txt': b'hi'})
    Transfer.objects.filter(pk=transfer.pk).update(zip_status=ZipStatus.FAILED)
    transfer.refresh_from_db()

    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.READY


def test_build_zip_is_a_noop_for_a_transfer_not_in_building_state(funded_user, fake_storage):
    transfer = _make_active_transfer(funded_user, fake_storage, files={'a.txt': b'hi'})
    # zip_status is still NONE -- nobody claimed a build.
    build_zip.enqueue(str(transfer.id))
    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.NONE


def test_build_zip_fails_gracefully_if_transfer_ended_mid_build(funded_user, fake_storage):
    transfer = _make_active_transfer(funded_user, fake_storage, files={'a.txt': b'hi'})
    ensure_zip_build_started(transfer)
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DELETED)

    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.FAILED


def test_record_download_of_zip_requires_ready_status(funded_user, fake_storage):
    transfer = _make_active_transfer(
        funded_user, fake_storage, files={'a.txt': b'hi', 'b.txt': b'yo'}
    )
    with pytest.raises(ValidationError):
        record_download(transfer, None, '1.2.3.4', storage=fake_storage)


def test_record_download_of_zip_counts_once_and_returns_presigned_url(funded_user, fake_storage):
    transfer = _make_active_transfer(
        funded_user, fake_storage, files={'a.txt': b'hi', 'b.txt': b'yo'}
    )
    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))
    transfer.refresh_from_db()

    url = record_download(transfer, None, '1.2.3.4', storage=fake_storage)
    assert transfer.zip_key in url
    event = DownloadEvent.objects.get(transfer=transfer)
    assert event.file is None
    assert event.ip == '1.2.3.4'
    assert transfer.download_count == 1
