"""The "download all" zip (spec section 5): streaming build, claim-once concurrency, failure/retry,
and download counting/password gating.
"""

import io
import zipfile
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import DownloadEvent, Transfer, TransferStatus, ZipStatus
from ..services.downloads import record_download
from ..services.lifecycle import delete_transfer_files
from ..services.storage import S3_MIN_PART_SIZE_BYTES
from ..services.uploads import add_file, complete_file_upload
from ..services.zip import (
    BUILD_LEASE,
    _S3MultipartWriter,
    build_zip,
    ensure_zip_build_started,
    zip_key,
)

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


def test_build_zip_dedupes_duplicate_file_names(funded_user, fake_storage):
    transfer = Transfer.objects.create(owner=funded_user, status=TransferStatus.DRAFT)
    for content in (b'first', b'second', b'third'):
        file = add_file(transfer, 'report.pdf', len(content), storage=fake_storage)
        fake_storage.put_object(file.storage_key, len(content))
        fake_storage.objects[file.storage_key] = content
        complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag'}], storage=fake_storage)
    transfer.status = TransferStatus.ACTIVE
    transfer.save()

    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.READY
    with zipfile.ZipFile(io.BytesIO(fake_storage.objects[transfer.zip_key])) as zf:
        names = sorted(zf.namelist())
        assert names == ['report (1).pdf', 'report (2).pdf', 'report.pdf']


def test_build_zip_flattens_path_separators_in_entry_names(funded_user, fake_storage):
    transfer = Transfer.objects.create(owner=funded_user, status=TransferStatus.DRAFT)
    # `services.limits.validate_filename` only strips control characters -- `/`, `\` and `..`
    # reach the stored name unchanged, so the zip builder itself must neutralize them (a "zip
    # slip" guard for a naive/vulnerable extractor).
    file = add_file(transfer, '../../etc/passwd', 5, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 5)
    fake_storage.objects[file.storage_key] = b'12345'
    complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag'}], storage=fake_storage)
    transfer.status = TransferStatus.ACTIVE
    transfer.save()

    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.READY
    with zipfile.ZipFile(io.BytesIO(fake_storage.objects[transfer.zip_key])) as zf:
        (name,) = zf.namelist()
        assert '/' not in name
        assert '\\' not in name


def test_build_zip_sets_entry_dates_from_created_at(funded_user, fake_storage):
    transfer = Transfer.objects.create(owner=funded_user, status=TransferStatus.DRAFT)
    file = add_file(transfer, 'a.txt', 2, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 2)
    fake_storage.objects[file.storage_key] = b'hi'
    complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'etag'}], storage=fake_storage)
    file.refresh_from_db()
    transfer.status = TransferStatus.ACTIVE
    transfer.save()

    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert file.created_at is not None
    with zipfile.ZipFile(io.BytesIO(fake_storage.objects[transfer.zip_key])) as zf:
        info = zf.getinfo('a.txt')
        assert info.date_time[:3] == file.created_at.timetuple()[:3]
        assert info.date_time != (1980, 1, 1, 0, 0, 0)


def test_stale_building_zip_can_be_reclaimed_after_lease_expires(funded_user, fake_storage):
    transfer = _make_active_transfer(funded_user, fake_storage, files={'a.txt': b'hi'})
    ensure_zip_build_started(transfer)
    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.BUILDING

    # A second request right away must not re-claim it -- the build might still be running.
    ensure_zip_build_started(transfer)
    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.BUILDING

    # Simulate a worker that died mid-build: the lease has expired, with nothing having moved
    # `zip_status` off of `BUILDING`.
    stale = timezone.now() - BUILD_LEASE - timedelta(minutes=1)
    Transfer.objects.filter(pk=transfer.pk).update(zip_build_started_at=stale)
    transfer.refresh_from_db()

    ensure_zip_build_started(transfer)
    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.READY


def test_orphaned_zip_object_is_deleted_if_transfer_ends_mid_build(
    funded_user, fake_storage, monkeypatch
):
    transfer = _make_active_transfer(
        funded_user, fake_storage, files={'a.txt': b'hello', 'b.txt': b'world'}
    )
    ensure_zip_build_started(transfer)
    b_file = transfer.files.get(name='b.txt')

    original_get_stream = fake_storage.get_object_stream

    def _end_transfer_while_reading_last_file(key):
        # Grab the real bytes first, then simulate the transfer ending (files deleted) exactly
        # while the build is still copying -- `delete_prefix` finds nothing yet, since the
        # in-progress multipart upload isn't an S3 object, but `writer.finish()` below still
        # succeeds and would otherwise leave an orphaned `all.zip` with no owner left to clean it
        # up.
        stream = original_get_stream(key)
        if key == b_file.storage_key:
            Transfer.objects.filter(pk=transfer.pk).update(
                status=TransferStatus.DELETED, deleted_at=timezone.now()
            )
            delete_transfer_files(Transfer.objects.get(pk=transfer.pk), storage=fake_storage)
        return stream

    monkeypatch.setattr(fake_storage, 'get_object_stream', _end_transfer_while_reading_last_file)

    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.NONE
    assert transfer.zip_key == ''
    assert zip_key(transfer.id) not in fake_storage.objects


def test_build_zip_recovers_if_the_writer_itself_fails_to_start(funded_user, fake_storage):
    """A failure in `_S3MultipartWriter`'s own constructor (`create_multipart_upload`) happens
    before any file is copied -- it must still mark the build `FAILED` rather than leave
    `zip_status` stuck at `BUILDING` with nothing having claimed responsibility for it."""
    transfer = _make_active_transfer(funded_user, fake_storage, files={'a.txt': b'hi'})
    ensure_zip_build_started(transfer)

    def _broken_create_multipart_upload(key):
        raise RuntimeError('S3 is down')

    fake_storage.create_multipart_upload = _broken_create_multipart_upload

    build_zip.enqueue(str(transfer.id))

    transfer.refresh_from_db()
    assert transfer.zip_status == ZipStatus.FAILED


class TestMultipartWriterPartSize:
    def test_every_part_but_the_last_meets_the_s3_minimum(self, fake_storage):
        writer = _S3MultipartWriter(fake_storage, 'some/key', part_size=S3_MIN_PART_SIZE_BYTES)
        writer.write(b'x' * (S3_MIN_PART_SIZE_BYTES + 100))
        writer.write(b'y' * 42)
        writer.finish()

        # The fake itself enforces the real S3 rule at completion time (see `fakes.py`) -- if it
        # hadn't raised, this call already proves every part but the last met the minimum.
        assert fake_storage.objects['some/key'] == b'x' * (S3_MIN_PART_SIZE_BYTES + 100) + b'y' * 42

    def test_fake_storage_rejects_a_part_below_the_minimum(self, fake_storage):
        """Guards the guard above: a writer configured with too small a part size must be caught
        by the fake, proving `test_every_part_but_the_last_meets_the_s3_minimum` isn't passing
        merely because nothing enforces the rule."""
        writer = _S3MultipartWriter(fake_storage, 'some/key', part_size=1024)
        writer.write(b'x' * 2048)
        writer.write(b'y' * 10)

        with pytest.raises(Exception, match='EntityTooSmall|too small|smaller than the minimum'):
            writer.finish()
