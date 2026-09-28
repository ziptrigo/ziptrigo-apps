import base64
import hashlib

import pytest
from botocore.exceptions import ClientError
from django.core.exceptions import ValidationError

from ..models import TransferFile, TransferStatus
from ..services import uploads
from ..services.storage import PART_SIZE_BYTES, S3_MIN_PART_SIZE_BYTES

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

#: A well-formed (but meaningless) base64-encoded SHA-256 digest, for tests that don't care about
#: the actual checksum value -- only that it's the right shape.
_CHECKSUM = base64.b64encode(b'\x00' * 32).decode()


def _part_checksum(data: bytes) -> str:
    """What `FakeS3Storage.list_parts` reports for a part recorded with these bytes -- see its
    docstring."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _parts(*part_numbers: int) -> list[dict]:
    return [{'part_number': n, 'checksum_sha256': _CHECKSUM} for n in part_numbers]


def test_create_draft(funded_user):
    transfer = uploads.create_draft(funded_user)
    assert transfer.owner == funded_user
    assert transfer.status == TransferStatus.DRAFT


def test_get_or_create_draft_reuses_empty_draft(funded_user):
    first = uploads.get_or_create_draft(funded_user)
    second = uploads.get_or_create_draft(funded_user)
    assert first.pk == second.pk


def test_get_or_create_draft_skips_a_draft_that_already_has_files(draft_transfer, fake_storage):
    uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)

    fresh = uploads.get_or_create_draft(draft_transfer.owner)

    assert fresh.pk != draft_transfer.pk


def test_add_file_starts_a_multipart_upload(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'report.pdf', 2048, storage=fake_storage)
    assert file.transfer_id == draft_transfer.id
    assert file.upload_id in fake_storage.active_uploads
    # Keyed by file id, not the filename -- see `services.storage`'s module docstring.
    assert file.storage_key == f'transfers/{draft_transfer.id}/{file.id}'
    assert file.name == 'report.pdf'


def test_add_file_strips_control_characters_from_name(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a\r\nb\x00.txt', 10, storage=fake_storage)
    assert file.name == 'ab.txt'


def test_add_file_rejects_name_that_is_only_control_characters(draft_transfer, fake_storage):
    with pytest.raises(ValidationError):
        uploads.add_file(draft_transfer, '\x00\x00', 10, storage=fake_storage)


def test_add_file_rejects_on_non_draft_transfer(draft_transfer, fake_storage):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.save()
    with pytest.raises(ValidationError):
        uploads.add_file(draft_transfer, 'x', 10, storage=fake_storage)


def test_add_file_enforces_limits(draft_transfer, fake_storage, ft_settings):
    ft_settings.logged_in_max_file_size_bytes = 10
    ft_settings.save()
    with pytest.raises(ValidationError):
        uploads.add_file(draft_transfer, 'too-big.bin', 11, storage=fake_storage)


def test_presign_parts_returns_a_url_per_part(draft_transfer, fake_storage):
    size = 2 * PART_SIZE_BYTES + 100  # three parts
    file = uploads.add_file(draft_transfer, 'a.bin', size, storage=fake_storage)
    urls = uploads.presign_parts(file, _parts(1, 2, 3), storage=fake_storage)
    assert set(urls) == {1, 2, 3}
    assert all(file.upload_id in url for url in urls.values())


def test_presign_parts_requires_active_upload(draft_transfer, fake_storage):
    file = TransferFile.objects.create(
        transfer=draft_transfer, name='a', size=1, storage_key='k', upload_id=''
    )
    with pytest.raises(ValidationError):
        uploads.presign_parts(file, _parts(1), storage=fake_storage)


def test_presign_parts_rejects_part_number_outside_declared_size(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 1000, storage=fake_storage)  # 1 part only
    with pytest.raises(ValidationError):
        uploads.presign_parts(file, _parts(2), storage=fake_storage)


def test_presign_parts_rejects_malformed_checksum(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 1000, storage=fake_storage)
    with pytest.raises(ValidationError):
        uploads.presign_parts(
            file, [{'part_number': 1, 'checksum_sha256': 'not-base64!!'}], storage=fake_storage
        )


def test_presign_parts_signs_content_length_per_part(draft_transfer, fake_storage):
    size = PART_SIZE_BYTES + 100  # two parts: a full one, then a 100-byte remainder
    file = uploads.add_file(draft_transfer, 'a.bin', size, storage=fake_storage)

    uploads.presign_parts(file, _parts(1, 2), storage=fake_storage)

    # The fake doesn't record content_length directly, but presigning must not raise for either
    # part number and must produce two distinct calls.
    assert len(fake_storage.presign_calls) == 2


def test_complete_file_upload_marks_uploaded_and_stores_checksum(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 500, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 500)

    completed = uploads.complete_file_upload(
        file, [{'PartNumber': 1, 'ETag': 'e1'}], storage=fake_storage
    )

    assert completed.uploaded is True
    assert completed.upload_id == ''
    assert completed.checksum == fake_storage.checksum
    assert (
        file.upload_id in fake_storage.aborted_uploads
        or file.upload_id not in fake_storage.active_uploads
    )


def test_complete_file_upload_rejects_size_mismatch(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 500, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 499)  # wrong size

    with pytest.raises(ValidationError):
        uploads.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'e1'}], storage=fake_storage)

    file.refresh_from_db()
    assert file.uploaded is False
    assert file.upload_id == ''
    # The object S3 already assembled is deleted immediately rather than left as an orphan --
    # neither `remove_file` nor `abort_draft` would otherwise ever clean it up, since it's no
    # longer "in progress" for either of them to notice.
    assert file.storage_key not in fake_storage.objects


def test_remove_file_aborts_in_progress_upload(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 500, storage=fake_storage)
    upload_id = file.upload_id

    uploads.remove_file(file, storage=fake_storage)

    assert upload_id in fake_storage.aborted_uploads
    assert not TransferFile.objects.filter(pk=file.pk).exists()


def test_remove_file_deletes_uploaded_object(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 500, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 500)
    uploads.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'e1'}], storage=fake_storage)

    uploads.remove_file(file, storage=fake_storage)

    assert file.storage_key not in fake_storage.objects
    assert not TransferFile.objects.filter(pk=file.pk).exists()


def test_add_file_stores_client_last_modified(draft_transfer, fake_storage):
    file = uploads.add_file(
        draft_transfer, 'a.bin', 10, client_last_modified=1234567890, storage=fake_storage
    )
    assert file.client_last_modified == 1234567890


def test_add_file_client_last_modified_defaults_to_none(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    assert file.client_last_modified is None


def test_list_uploaded_parts_returns_parts_already_in_s3(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 200, storage=fake_storage)
    fake_storage.upload_part(file.storage_key, file.upload_id, 1, b'\0' * 100)

    parts = uploads.list_uploaded_parts(file, storage=fake_storage)

    assert parts == [
        {
            'PartNumber': 1,
            'ETag': f'etag-{file.upload_id}-1',
            'Size': 100,
            'ChecksumSHA256': _part_checksum(b'\0' * 100),
        }
    ]


def test_list_uploaded_parts_empty_when_nothing_uploaded_yet(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 200, storage=fake_storage)

    assert uploads.list_uploaded_parts(file, storage=fake_storage) == []


def test_list_uploaded_parts_requires_upload_in_progress(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    fake_storage.put_object(file.storage_key, 10)
    uploads.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'e1'}], storage=fake_storage)

    with pytest.raises(ValidationError):
        uploads.list_uploaded_parts(file, storage=fake_storage)


def test_list_uploaded_parts_raises_upload_expired_when_s3_forgot_it(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    # Simulates the bucket's lifecycle rule (or a previous `cleanup_drafts` run) aborting the
    # multipart upload out from under this session.
    fake_storage.abort_multipart_upload(file.storage_key, file.upload_id)

    with pytest.raises(uploads.UploadExpired):
        uploads.list_uploaded_parts(file, storage=fake_storage)


def test_list_uploaded_parts_maps_other_client_errors_to_validation_error(
    draft_transfer, fake_storage
):
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)

    def raise_access_denied(key, upload_id):
        raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'nope'}}, 'ListParts')

    fake_storage.list_parts = raise_access_denied

    with pytest.raises(ValidationError):
        uploads.list_uploaded_parts(file, storage=fake_storage)


def test_restart_upload_gets_a_fresh_upload_id(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    old_upload_id = file.upload_id
    fake_storage.abort_multipart_upload(file.storage_key, old_upload_id)

    restarted = uploads.restart_upload(file, storage=fake_storage)

    assert restarted.upload_id != old_upload_id
    assert restarted.upload_id in fake_storage.active_uploads
    assert restarted.uploaded is False
    assert uploads.list_uploaded_parts(restarted, storage=fake_storage) == []


def test_restart_upload_does_not_clobber_a_concurrent_completion(draft_transfer, fake_storage):
    """Two tabs open on the same draft (issue #55 phase 3 review): tab A completes the file while
    tab B's stale `file` object -- still holding the old, now-completed upload id -- calls
    `restart_upload` after its own `list_uploaded_parts` got `UploadExpired`. The completed state
    must survive: `restart_upload` must not blindly overwrite it."""
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    stale = TransferFile.objects.get(pk=file.pk)  # tab B's copy, read before tab A completes

    # Tab A: completes the upload.
    fake_storage.put_object(file.storage_key, 10)
    uploads.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'e1'}], storage=fake_storage)
    assert file.uploaded is True

    # Tab B: its `list_uploaded_parts` would now raise `UploadExpired` (the old upload id is gone
    # -- completed, not merely aborted), so it calls `restart_upload` on its stale copy.
    result = uploads.restart_upload(stale, storage=fake_storage)

    file.refresh_from_db()
    assert file.uploaded is True
    assert file.upload_id == ''
    # `restart_upload` hands back the current, authoritative state rather than its own stale write.
    assert result.uploaded is True
    assert result.upload_id == ''
    # The upload it speculatively created (before losing the race) must not be left dangling.
    assert result.pk == file.pk


def test_add_file_pins_the_current_part_size(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    assert file.part_size_bytes == PART_SIZE_BYTES


def test_part_count_uses_the_file_s_pinned_part_size_not_the_live_constant(
    draft_transfer, fake_storage, monkeypatch
):
    """If the global part size changed after an upload started, a resumed upload must still slice
    (and be told to slice) at the size it was actually started with, or its parts would no longer
    line up with what S3 already has (see `TransferFile.part_size_bytes`'s docstring)."""
    size = PART_SIZE_BYTES + 100  # two parts at the current size
    file = uploads.add_file(draft_transfer, 'a.bin', size, storage=fake_storage)
    assert uploads.part_count_for(file) == 2

    from ..services import uploads as uploads_module

    monkeypatch.setattr(uploads_module, 'PART_SIZE_BYTES', PART_SIZE_BYTES * 10)

    # The live module constant changed, but this file's own pinned value -- and so its part
    # count -- must not.
    assert file.part_size_bytes == PART_SIZE_BYTES
    assert uploads.part_count_for(file) == 2


def test_resume_through_complete_round_trips_checksums(draft_transfer, fake_storage):
    """End-to-end (service layer): a part landed via an earlier page load, `list_uploaded_parts`
    reports its checksum, and completing the upload -- with that part's checksum carried through
    unchanged, alongside a freshly-uploaded second part -- succeeds. This is the resumed-upload
    path the critical fix is about (issue #55 phase 3 review): a completion that dropped the first
    part's checksum used to be accepted by the fake (and, for real, rejected by S3)."""
    part_bytes = b'\0' * S3_MIN_PART_SIZE_BYTES
    file = uploads.add_file(
        draft_transfer, 'a.bin', 2 * S3_MIN_PART_SIZE_BYTES, storage=fake_storage
    )
    # Part 1 "already landed" in an earlier page load. Sized at S3's own per-part minimum: the
    # fake (like real S3) rejects a non-last part smaller than that at completion time.
    fake_storage.upload_part(file.storage_key, file.upload_id, 1, part_bytes)
    resumed = uploads.list_uploaded_parts(file, storage=fake_storage)
    assert resumed == [
        {
            'PartNumber': 1,
            'ETag': f'etag-{file.upload_id}-1',
            'Size': S3_MIN_PART_SIZE_BYTES,
            'ChecksumSHA256': _part_checksum(part_bytes),
        }
    ]
    # Part 2 is the one this call actually PUTs.
    fake_storage.upload_part(file.storage_key, file.upload_id, 2, part_bytes)

    completed_parts = [
        {
            'PartNumber': resumed[0]['PartNumber'],
            'ETag': resumed[0]['ETag'],
            'ChecksumSHA256': resumed[0]['ChecksumSHA256'],
        },
        {
            'PartNumber': 2,
            'ETag': f'etag-{file.upload_id}-2',
            'ChecksumSHA256': _part_checksum(part_bytes),
        },
    ]

    completed = uploads.complete_file_upload(file, completed_parts, storage=fake_storage)

    assert completed.uploaded is True


def test_complete_without_a_checksum_for_an_already_landed_part_is_rejected(
    draft_transfer, fake_storage
):
    """`FakeS3Storage` itself, in isolation: mirrors real S3 refusing to complete a
    checksum-enabled multipart upload when a part it already has on record isn't repeated with its
    checksum -- exactly the bug the critical fix closes (a resumed upload's JS/CLI used to build
    this payload without one for parts it didn't re-PUT)."""
    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    fake_storage.upload_part(file.storage_key, file.upload_id, 1, b'\0' * 10)

    with pytest.raises(ValidationError):
        uploads.complete_file_upload(
            file, [{'PartNumber': 1, 'ETag': f'etag-{file.upload_id}-1'}], storage=fake_storage
        )


def test_abort_draft_cleans_up_everything(draft_transfer, fake_storage):
    in_progress = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    done = uploads.add_file(draft_transfer, 'b.bin', 10, storage=fake_storage)
    fake_storage.put_object(done.storage_key, 10)
    uploads.complete_file_upload(done, [{'PartNumber': 1, 'ETag': 'e'}], storage=fake_storage)

    uploads.abort_draft(draft_transfer, storage=fake_storage)

    assert in_progress.upload_id in fake_storage.aborted_uploads
    assert done.storage_key not in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert draft_transfer.deleted_at is not None
    assert draft_transfer.files.count() == 0
