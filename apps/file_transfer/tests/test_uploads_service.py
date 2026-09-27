import pytest
from django.core.exceptions import ValidationError

from ..models import TransferFile, TransferStatus
from ..services import uploads

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_create_draft(funded_user):
    transfer = uploads.create_draft(funded_user)
    assert transfer.owner == funded_user
    assert transfer.status == TransferStatus.DRAFT


def test_add_file_starts_a_multipart_upload(draft_transfer, fake_storage):
    file = uploads.add_file(draft_transfer, 'report.pdf', 2048, storage=fake_storage)
    assert file.transfer_id == draft_transfer.id
    assert file.upload_id in fake_storage.active_uploads
    assert file.storage_key == f'transfers/{draft_transfer.id}/{file.id}/report.pdf'


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
    file = uploads.add_file(draft_transfer, 'a.bin', 1000, storage=fake_storage)
    urls = uploads.presign_parts(file, [1, 2, 3], storage=fake_storage)
    assert set(urls) == {1, 2, 3}
    assert all(file.upload_id in url for url in urls.values())


def test_presign_parts_requires_active_upload(draft_transfer, fake_storage):
    file = TransferFile.objects.create(
        transfer=draft_transfer, name='a', size=1, storage_key='k', upload_id=''
    )
    with pytest.raises(ValidationError):
        uploads.presign_parts(file, [1], storage=fake_storage)


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
