import base64

import pytest
from django.core.exceptions import ValidationError

from ..models import TransferFile, TransferStatus
from ..services import uploads
from ..services.storage import PART_SIZE_BYTES

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

#: A well-formed (but meaningless) base64-encoded SHA-256 digest, for tests that don't care about
#: the actual checksum value -- only that it's the right shape.
_CHECKSUM = base64.b64encode(b'\x00' * 32).decode()


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
