"""Attachments (issue #184): start / complete against the fake storage, the caps, frozen refusals,
deleting, the Trash and purge, deleting a user, and the download URL's dispositions."""

import base64
import hashlib
import logging
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import Attachment, TodoAccount, TodoSettings
from ..services import (
    AccountFrozenError,
    attachments,
    complete_upload,
    create_task,
    delete_attachment,
    delete_forever,
    download_url,
    get_account,
    lifecycle,
    purge_trash,
    start_upload,
    trash_task,
)
from ..services.storage import attachment_key, user_prefix
from .factories import PNG_BYTES, make_attachment, make_task

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def start(task, data: bytes = PNG_BYTES, **overrides):
    values = {
        'filename': 'photo.png',
        'size': len(data),
        'declared_type': 'image/png',
        'checksum_sha256': checksum(data),
    }
    values.update(overrides)
    return start_upload(task, **values)


def upload(task, fake_storage, data: bytes = PNG_BYTES, **overrides) -> Attachment:
    """The whole happy path: start, the browser's PUT (seeded), complete."""
    ticket = start(task, data, **overrides)
    fake_storage.seed(attachment_key(task.user_id, ticket.attachment.pk), data)
    return complete_upload(ticket.attachment)


@pytest.fixture
def task(user):
    return make_task(user)


def stored(user) -> int:
    return get_account(user).stored_bytes


class TestFilenames:
    @pytest.mark.parametrize(
        ('raw', 'cleaned'),
        [
            ('report.pdf', 'report.pdf'),
            ('a/b\\c.txt', 'a_b_c.txt'),
            ('../../etc/passwd', '.._.._etc_passwd'),
            ('tab\there\x00nul\n.txt', 'tabherenul.txt'),
            ('  padded.txt  ', 'padded.txt'),
            ('', 'file'),
            ('\x01\x02', 'file'),
            ('   ', 'file'),
            ('é' * 300, 'é' * 255),
        ],
    )
    def test_clean_filename(self, raw, cleaned):
        assert attachments.clean_filename(raw) == cleaned

    def test_the_limit_in_megabytes(self):
        assert attachments.format_megabytes(50 * 1024**2) == '50'
        assert attachments.format_megabytes(1024**2) == '1'
        assert attachments.format_megabytes(512 * 1024) == '0.5'


class TestStartUpload:
    def test_creates_a_pending_attachment_and_a_signed_put(self, task, fake_storage):
        ticket = start(task)

        attachment = ticket.attachment
        assert Attachment.objects.get(pk=attachment.pk).uploaded is False
        assert attachment.filename == 'photo.png'
        assert attachment.size_bytes == len(PNG_BYTES)
        assert attachment.declared_type == 'image/png'
        assert attachment.sha256 == checksum(PNG_BYTES)
        assert attachment.user_id == task.user_id
        [put] = fake_storage.put_urls
        # The exact size and the SHA-256 are signed into the URL.
        assert put['key'] == f'todo/users/{task.user_id}/attachments/{attachment.pk}'
        assert put['content_length'] == len(PNG_BYTES)
        assert put['checksum_sha256'] == checksum(PNG_BYTES)
        assert ticket.headers == {'x-amz-checksum-sha256': checksum(PNG_BYTES)}
        assert ticket.url.startswith('https://fake-s3.test/')
        assert ticket.expires_in == 3600

    def test_nothing_is_billed_until_it_completes(self, task):
        start(task)

        assert stored(task.user) == 0

    def test_the_key_never_contains_the_filename(self, task, fake_storage):
        start(task, filename='secret-plan.pdf')

        assert 'secret-plan' not in fake_storage.put_urls[0]['key']

    def test_the_filename_is_cleaned_and_the_declared_type_normalised(self, task):
        ticket = start(task, filename='dir/evil\x00.png', declared_type=' Image/PNG; charset=x')

        assert ticket.attachment.filename == 'dir_evil.png'
        assert ticket.attachment.declared_type == 'image/png'

    def test_a_frozen_account_is_refused(self, task):
        get_account(task.user)
        TodoAccount.objects.filter(pk=task.user_id).update(frozen_at=timezone.now())

        with pytest.raises(AccountFrozenError) as excinfo:
            start(task)

        assert excinfo.value.messages == [
            'Storage is paused: top up your credits to upload or download files.'
        ]
        assert isinstance(excinfo.value, ValidationError)
        assert not Attachment.objects.exists()

    def test_a_trashed_task_is_refused(self, task):
        trash_task(task)

        with pytest.raises(ValidationError, match='in the trash'):
            start(task)

    def test_a_completed_task_is_refused(self, task):
        task.completed_at = timezone.now()
        task.save()

        with pytest.raises(ValidationError, match='completed task'):
            start(task)

    def test_a_stale_task_object_is_rechecked_under_the_lock(self, task):
        stale = make_task(task.user)  # the caller still holds an open task...
        type(stale).objects.filter(pk=stale.pk).update(
            deleted_at=timezone.now()
        )  # ...trashed since

        with pytest.raises(ValidationError, match='any more'):
            start(stale)

    @pytest.mark.parametrize('size', [0, -5])
    def test_an_empty_file_is_refused(self, task, size):
        with pytest.raises(ValidationError, match='empty'):
            start(task, size=size)

    def test_too_large_names_the_limit_in_megabytes(self, task):
        limit = TodoSettings.load().max_attachment_bytes
        assert limit == 50 * 1024**2

        with pytest.raises(ValidationError) as excinfo:
            start(task, size=limit + 1)

        assert excinfo.value.messages == ['The file is too large (max 50 MB).']

    def test_exactly_the_limit_is_accepted(self, task):
        limit = TodoSettings.load().max_attachment_bytes

        start(task, size=limit)

    def test_the_limit_is_an_admin_setting(self, task):
        TodoSettings.objects.update_or_create(pk=1, defaults={'max_attachment_bytes': 2 * 1024**2})

        with pytest.raises(ValidationError, match=r'max 2 MB'):
            start(task, size=2 * 1024**2 + 1)

    def test_the_cap_per_task_counts_pending_and_uploaded_alike(self, task, fake_storage):
        TodoSettings.objects.update_or_create(pk=1, defaults={'max_attachments_per_task': 3})
        make_attachment(task, fake_storage)
        make_attachment(task, fake_storage, uploaded=False)
        start(task)

        with pytest.raises(ValidationError, match='up to 3 files'):
            start(task)

        # Another task has its own count.
        start(make_task(task.user))

    @pytest.mark.parametrize(
        'bad', ['', 'not base64!', 'AAAA', base64.b64encode(b'x' * 31).decode()]
    )
    def test_a_malformed_checksum_is_refused(self, task, bad):
        with pytest.raises(ValidationError, match='SHA-256'):
            start(task, checksum_sha256=bad)

        assert not Attachment.objects.exists()


class TestCompleteUpload:
    def test_marks_it_uploaded_and_counts_its_bytes(self, task, fake_storage):
        attachment = upload(task, fake_storage)

        assert attachment.uploaded is True
        assert stored(task.user) == len(PNG_BYTES)

    def test_the_first_byte_starts_the_billing_clock(self, task, fake_storage):
        get_account(task.user)
        TodoAccount.objects.filter(pk=task.user_id).update(
            last_billed_at=timezone.now() - timedelta(days=30)
        )

        upload(task, fake_storage)

        assert timezone.now() - get_account(task.user).last_billed_at < timedelta(minutes=1)

    def test_a_png_is_sniffed_inline(self, task, fake_storage):
        assert upload(task, fake_storage).inline_type == 'image/png'

    def test_a_pdf_is_sniffed_inline(self, task, fake_storage):
        pdf = b'%PDF-1.7\n' + b'0' * 50

        assert upload(task, fake_storage, pdf, filename='doc.pdf').inline_type == 'application/pdf'

    def test_an_exe_named_photo_png_is_not_inline(self, task, fake_storage):
        exe = b'MZ\x90\x00\x03\x00\x00\x00' + b'\x00' * 60

        attachment = upload(task, fake_storage, exe, filename='photo.png')

        assert attachment.inline_type == ''
        assert attachment.declared_type == 'image/png'  # what the browser said is only recorded

    def test_html_is_never_inline(self, task, fake_storage):
        html = b'<!DOCTYPE html><script>alert(1)</script>'

        assert upload(task, fake_storage, html, filename='x.html').inline_type == ''

    def test_a_size_mismatch_deletes_the_object_and_the_row(self, task, fake_storage):
        ticket = start(task)
        key = attachment_key(task.user_id, ticket.attachment.pk)
        fake_storage.seed(key, PNG_BYTES + b'extra')

        with pytest.raises(ValidationError) as excinfo:
            complete_upload(ticket.attachment)

        assert excinfo.value.messages == ['The upload did not complete correctly.']
        assert key not in fake_storage.objects
        assert not Attachment.objects.exists()
        assert stored(task.user) == 0

    def test_a_checksum_mismatch_deletes_the_object_and_the_row(self, task, fake_storage):
        ticket = start(task)
        key = attachment_key(task.user_id, ticket.attachment.pk)
        fake_storage.seed(key, b'\x89PNG' + b'z' * (len(PNG_BYTES) - 4))  # same size, other bytes

        with pytest.raises(ValidationError, match='did not complete correctly'):
            complete_upload(ticket.attachment)

        assert key not in fake_storage.objects
        assert not Attachment.objects.exists()

    def test_an_object_that_never_arrived_is_not_an_error_that_destroys_the_row(self, task):
        ticket = start(task)

        with pytest.raises(ValidationError, match='has not been uploaded'):
            complete_upload(ticket.attachment)

        assert Attachment.objects.filter(pk=ticket.attachment.pk).exists()

    def test_completing_twice_counts_once(self, task, fake_storage):
        attachment = upload(task, fake_storage)

        again = complete_upload(attachment)
        fresh = complete_upload(Attachment.objects.get(pk=attachment.pk))

        assert again.uploaded and fresh.uploaded
        assert stored(task.user) == len(PNG_BYTES)

    def test_two_racing_completes_count_once(self, task, fake_storage):
        ticket = start(task)
        fake_storage.seed(attachment_key(task.user_id, ticket.attachment.pk), PNG_BYTES)
        stale = Attachment.objects.get(pk=ticket.attachment.pk)  # both read it as pending

        complete_upload(ticket.attachment)
        complete_upload(stale)

        assert stored(task.user) == len(PNG_BYTES)


class TestAbandonUploads:
    def test_removes_old_pending_rows_and_their_objects(self, task, fake_storage):
        old = make_attachment(task, fake_storage, uploaded=False)
        Attachment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))
        key = attachment_key(task.user_id, old.pk)
        fake_storage.seed(key, b'half an upload')

        assert attachments.abandon_uploads() == 1

        assert not Attachment.objects.filter(pk=old.pk).exists()
        assert key not in fake_storage.objects

    def test_leaves_recent_pending_and_uploaded_ones(self, task, fake_storage):
        recent = make_attachment(task, fake_storage, uploaded=False)
        done = make_attachment(task, fake_storage)
        Attachment.objects.filter(pk=done.pk).update(created_at=timezone.now() - timedelta(days=9))

        assert attachments.abandon_uploads() == 0

        assert Attachment.objects.filter(pk__in=[recent.pk, done.pk]).count() == 2
        assert stored(task.user) == done.size_bytes

    def test_one_failing_delete_does_not_stop_the_rest(self, task, fake_storage, caplog):
        bad = make_attachment(task, fake_storage, uploaded=False)
        good = make_attachment(task, fake_storage, uploaded=False)
        Attachment.objects.update(created_at=timezone.now() - timedelta(hours=25))
        fake_storage.failing_deletes.add(attachment_key(task.user_id, bad.pk))

        with caplog.at_level(logging.ERROR):
            assert attachments.abandon_uploads() == 1

        assert Attachment.objects.filter(pk=bad.pk).exists()
        assert not Attachment.objects.filter(pk=good.pk).exists()
        assert 'Failed to clean up abandoned upload' in caplog.text

    def test_the_job_runs_it(self, task, fake_storage):
        from .. import jobs

        old = make_attachment(task, fake_storage, uploaded=False)
        Attachment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))

        jobs.cleanup_uploads()

        assert not Attachment.objects.exists()


class TestDelete:
    def test_removes_the_object_the_row_and_the_billed_bytes(self, task, fake_storage):
        keep = make_attachment(task, fake_storage, data=PNG_BYTES + b'1')
        gone = make_attachment(task, fake_storage, data=PNG_BYTES + b'22')
        key = attachment_key(task.user_id, gone.pk)

        delete_attachment(gone)

        assert key not in fake_storage.objects
        assert list(Attachment.objects.all()) == [keep]
        assert stored(task.user) == keep.size_bytes

    def test_a_storage_failure_changes_nothing(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        fake_storage.failing_deletes.add(attachment_key(task.user_id, attachment.pk))

        with pytest.raises(attachments.STORAGE_ERRORS):
            delete_attachment(attachment)

        assert Attachment.objects.filter(pk=attachment.pk).exists()
        assert stored(task.user) == attachment.size_bytes

    def test_deleting_twice_subtracts_once(self, task, fake_storage):
        first = make_attachment(task, fake_storage)
        second = make_attachment(task, fake_storage)
        stale = Attachment.objects.get(pk=first.pk)

        delete_attachment(first)
        delete_attachment(stale)

        assert stored(task.user) == second.size_bytes

    def test_the_stored_bytes_never_go_below_zero(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        TodoAccount.objects.filter(pk=task.user_id).update(stored_bytes=3)

        delete_attachment(attachment)

        assert stored(task.user) == 0

    def test_a_pending_one_is_deleted_without_touching_the_bytes(self, task, fake_storage):
        done = make_attachment(task, fake_storage)
        pending = make_attachment(task, fake_storage, uploaded=False)

        delete_attachment(pending)

        assert stored(task.user) == done.size_bytes


class TestTrashAndPurge:
    def test_trashing_a_task_keeps_its_attachments_billed(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        trash_task(task)

        assert Attachment.objects.filter(pk=attachment.pk).exists()
        assert attachment_key(task.user_id, attachment.pk) in fake_storage.objects
        assert stored(task.user) == attachment.size_bytes

    def test_delete_forever_deletes_the_objects_and_the_bytes(self, task, fake_storage):
        other = make_task(task.user)
        gone = make_attachment(task, fake_storage)
        kept = make_attachment(other, fake_storage, data=PNG_BYTES + b'xx')
        trash_task(task)

        delete_forever(task)

        assert attachment_key(task.user_id, gone.pk) not in fake_storage.objects
        assert attachment_key(task.user_id, kept.pk) in fake_storage.objects
        assert not Attachment.objects.filter(pk=gone.pk).exists()
        assert stored(task.user) == kept.size_bytes

    def test_delete_forever_takes_the_subtasks_files_too(self, user, config, fake_storage):
        parent = create_task(user, title='Parent')
        child = create_task(user, title='Child', parent=parent)
        make_attachment(parent, fake_storage)
        make_attachment(child, fake_storage, data=PNG_BYTES + b'c')
        trash_task(parent)

        delete_forever(parent)

        assert fake_storage.objects == {}
        assert not Attachment.objects.exists()
        assert stored(user) == 0

    def test_a_storage_error_leaves_the_task_and_says_so(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        trash_task(task)
        fake_storage.failing_deletes.add(attachment_key(task.user_id, attachment.pk))

        with pytest.raises(ValidationError, match='files could not be deleted'):
            delete_forever(task)

        task.refresh_from_db()  # still there
        assert Attachment.objects.filter(pk=attachment.pk).exists()
        assert stored(task.user) == attachment.size_bytes

    def test_the_purge_deletes_objects_and_bytes(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        trash_task(task)
        type(task).objects.filter(pk=task.pk).update(deleted_at=timezone.now() - timedelta(days=40))

        assert purge_trash() == 1

        assert fake_storage.objects == {}
        assert not Attachment.objects.filter(pk=attachment.pk).exists()
        assert stored(task.user) == 0

    def test_the_purge_leaves_a_task_whose_files_could_not_be_deleted_for_the_next_run(
        self, user, fake_storage, caplog
    ):
        bad_task = make_task(user)
        good_task = make_task(user)
        bad = make_attachment(bad_task, fake_storage)
        make_attachment(good_task, fake_storage, data=PNG_BYTES + b'g')
        cutoff = timezone.now() - timedelta(days=40)
        type(bad_task).objects.filter(pk__in=[bad_task.pk, good_task.pk]).update(deleted_at=cutoff)
        fake_storage.failing_deletes.add(attachment_key(user.pk, bad.pk))

        with caplog.at_level(logging.ERROR):
            assert purge_trash() == 1

        assert type(bad_task).objects.filter(pk=bad_task.pk).exists()
        assert not type(good_task).objects.filter(pk=good_task.pk).exists()
        assert stored(user) == bad.size_bytes
        # The next run, with S3 healthy again, finishes it.
        fake_storage.failing_deletes.clear()
        assert purge_trash() == 1
        assert stored(user) == 0
        assert fake_storage.objects == {}


class TestUserDeletion:
    def test_deleting_a_user_deletes_their_objects_and_nobody_elses(
        self, user, other_user, fake_storage, django_capture_on_commit_callbacks, monkeypatch
    ):
        # Deleting a user also queues Receipts' own storage cleanup: keep it off the network.
        monkeypatch.setattr('apps.receipts.services.storage._storage', MagicMock())
        user_id = user.pk
        mine = make_attachment(make_task(user), fake_storage)
        theirs = make_attachment(make_task(other_user), fake_storage)
        fake_storage.seed(f'{user_prefix(user.pk)}attachments/stray', b'x')

        with django_capture_on_commit_callbacks(execute=True):
            user.delete()

        assert set(fake_storage.objects) == {attachment_key(other_user.pk, theirs.pk)}
        assert not Attachment.objects.filter(pk=mine.pk).exists()
        assert fake_storage.deleted_prefixes == [user_prefix(user_id)]

    def test_the_deletion_is_queued_not_done_inline(
        self, user, fake_storage, django_capture_on_commit_callbacks
    ):
        attachment = make_attachment(make_task(user), fake_storage)
        user_id = user.pk

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            user.delete()

        assert attachment_key(user_id, attachment.pk) in fake_storage.objects
        assert len(callbacks) >= 1

    def test_the_prefix_has_a_trailing_slash_so_user_1_is_not_user_10(self):
        assert user_prefix('1') == 'todo/users/1/'

    def test_a_failure_is_retried_a_bounded_number_of_times_then_logged(
        self, user, fake_storage, caplog
    ):
        fake_storage.failing_prefix_delete = True

        with caplog.at_level(logging.WARNING):
            lifecycle.delete_user_storage.call(str(user.pk))

        # The immediate test backend can't defer, so each retry runs at once: first try + 4 retries.
        assert len(lifecycle.USER_STORAGE_RETRY_DELAYS) == 4
        assert 'Giving up deleting the to-do objects' in caplog.text
        assert caplog.text.count('retrying in') == 4

    def test_the_task_runs_on_the_default_queue(self):
        assert lifecycle.delete_user_storage.queue_name == 'default'


class TestDownloadUrl:
    def test_an_image_opens_inline_with_its_sniffed_type(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        url = download_url(attachment, inline=True)

        [call] = fake_storage.get_urls
        assert call == {
            'key': attachment_key(task.user_id, attachment.pk),
            'filename': 'photo.png',
            'disposition': 'inline',
            'content_type': 'image/png',
        }
        assert url.startswith('https://fake-s3.test/')

    def test_download_is_an_attachment(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        download_url(attachment)
        download_url(attachment, inline=False)

        assert [call['disposition'] for call in fake_storage.get_urls] == ['attachment'] * 2

    def test_a_file_that_is_not_an_inline_type_is_always_a_download(self, task, fake_storage):
        # Declared as an image by the browser, but the bytes are an executable.
        attachment = make_attachment(
            task,
            fake_storage,
            data=b'MZ' + b'\x00' * 40,
            filename='photo.png',
            declared_type='image/png',
            inline_type='',
        )

        download_url(attachment, inline=True)

        [call] = fake_storage.get_urls
        assert call['disposition'] == 'attachment'
        assert call['content_type'] is None

    def test_a_pdf_opens_inline(self, task, fake_storage):
        attachment = make_attachment(
            task, fake_storage, data=b'%PDF-1.4 x', filename='d.pdf', inline_type='application/pdf'
        )

        download_url(attachment, inline=True)

        assert fake_storage.get_urls[0]['disposition'] == 'inline'
        assert fake_storage.get_urls[0]['content_type'] == 'application/pdf'

    def test_a_frozen_account_is_refused(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        TodoAccount.objects.filter(pk=task.user_id).update(frozen_at=timezone.now())

        with pytest.raises(AccountFrozenError):
            download_url(attachment)

        assert fake_storage.get_urls == []

    def test_an_upload_that_never_completed_has_no_file(self, task, fake_storage):
        attachment = make_attachment(task, fake_storage, uploaded=False)

        with pytest.raises(ValidationError, match='no file'):
            download_url(attachment)
