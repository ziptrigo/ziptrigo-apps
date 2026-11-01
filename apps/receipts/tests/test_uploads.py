from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import FailureReason, Receipt, ReceiptsAccount, ReceiptSource, ReceiptStatus
from ..services import uploads
from ..services.accounts import add_stored_bytes, get_account
from ..services.errors import AccountFrozenError, DailyLimitExceededError
from ..services.storage import file_key
from . import samples
from .fakes import b64_sha256

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def start(user, data=None, *, filename='receipt.jpg', content_type='image/jpeg', **kwargs):
    data = samples.jpeg_bytes() if data is None else data
    return uploads.start_upload(
        user, filename, len(data), content_type, kwargs.pop('checksum', b64_sha256(data)), **kwargs
    )


class TestStartUpload:
    def test_creates_a_pending_receipt_and_presigns_a_put(self, funded_user, fake_storage):
        data = samples.jpeg_bytes()

        ticket = start(funded_user, data)

        receipt = ticket.receipt
        assert receipt.uploaded is False
        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.source == ReceiptSource.UPLOAD
        assert receipt.user_id == funded_user.pk
        assert receipt.size_bytes == len(data)
        assert receipt.content_type == 'image/jpeg'
        assert receipt.file_key == file_key(funded_user.pk, receipt.pk)
        assert Receipt.objects.get(pk=receipt.pk).file_key == receipt.file_key
        assert fake_storage.put_urls == [
            {
                'key': receipt.file_key,
                'content_length': len(data),
                'checksum_sha256': b64_sha256(data),
                'content_type': None,
            }
        ]
        assert ticket.url.startswith('https://fake-s3.test/')
        assert ticket.headers == {'x-amz-checksum-sha256': b64_sha256(data)}
        assert ticket.expires_in > 0

    def test_nothing_is_billed_or_queued_until_it_completes(self, funded_user):
        start(funded_user)

        assert get_account(funded_user).stored_bytes == 0

    def test_the_api_source(self, funded_user):
        assert start(funded_user, source=ReceiptSource.API).receipt.source == 'api'

    def test_creates_the_account(self, funded_user):
        start(funded_user)

        assert ReceiptsAccount.objects.filter(user=funded_user).exists()

    def test_filename_is_cleaned(self, funded_user):
        ticket = start(funded_user, filename='../../etc/pass\x00wd\n.jpg')

        assert ticket.receipt.original_filename == 'passwd.jpg'

    def test_blank_filename_gets_a_default(self, funded_user):
        assert start(funded_user, filename='  ').receipt.original_filename == 'receipt'

    @pytest.mark.parametrize(
        ('filename', 'claimed', 'expected'),
        [
            ('a.heic', '', 'image/heic'),
            ('a.HEIC', 'application/octet-stream', 'image/heic'),
            ('a.heif', 'image/heif', 'image/heic'),
            ('a.jpg', 'image/jpg', 'image/jpeg'),
            ('a.pdf', 'application/pdf; charset=binary', 'application/pdf'),
            ('scan.webp', '', 'image/webp'),
        ],
    )
    def test_declared_type_is_normalized(self, funded_user, filename, claimed, expected):
        ticket = start(funded_user, filename=filename, content_type=claimed)

        assert ticket.receipt.content_type == expected

    @pytest.mark.parametrize(
        ('filename', 'claimed'),
        [
            (
                'notes.docx',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            ),
            ('a.zip', 'application/zip'),
            ('a.txt', 'text/plain'),
            ('a.svg', 'image/svg+xml'),
            ('a.gif', 'image/gif'),
            ('noext', ''),
        ],
    )
    def test_unsupported_types_are_refused(self, funded_user, filename, claimed):
        with pytest.raises(ValidationError, match='not supported'):
            start(funded_user, filename=filename, content_type=claimed)
        assert not Receipt.objects.exists()

    def test_the_admin_can_narrow_the_allowed_types(self, funded_user, receipts_settings):
        receipts_settings.allowed_types = 'application/pdf'
        receipts_settings.save()

        with pytest.raises(ValidationError) as refused:
            start(funded_user)
        # The message names only what is allowed now.
        assert refused.value.messages == ['This file type is not supported. Use PDF.']

        receipts_settings.allowed_types = 'image/png,application/pdf,image/heic'
        assert receipts_settings.allowed_types_text() == 'PDF, PNG or HEIC'
        assert receipts_settings.allowed_extensions() == ['.pdf', '.png', '.heic', '.heif']
        assert receipts_settings.allowed_browser_types() == [
            'application/pdf',
            'image/png',
            'image/heic',
            'image/heif',
        ]

    def test_size_limits(self, funded_user, receipts_settings):
        receipts_settings.max_file_bytes = 1000
        receipts_settings.save()

        with pytest.raises(ValidationError, match='too large'):
            uploads.start_upload(funded_user, 'a.jpg', 1001, 'image/jpeg', b64_sha256(b'x'))
        with pytest.raises(ValidationError, match='empty'):
            uploads.start_upload(funded_user, 'a.jpg', 0, 'image/jpeg', b64_sha256(b'x'))
        assert uploads.start_upload(funded_user, 'a.jpg', 1000, 'image/jpeg', b64_sha256(b'x'))

    @pytest.mark.parametrize('checksum', ['', 'abc', 'not base64!!', 'AAAA'])
    def test_the_checksum_must_be_a_base64_sha256(self, funded_user, checksum):
        with pytest.raises(ValidationError, match='checksum'):
            start(funded_user, checksum=checksum)

    def test_a_frozen_account_cannot_upload(self, funded_user):
        account = get_account(funded_user)
        account.frozen_at = timezone.now()
        account.save()

        with pytest.raises(AccountFrozenError):
            start(funded_user)
        assert not Receipt.objects.exists()

    def test_the_daily_cap_counts_files_started_in_the_24_hour_window(
        self, funded_user, receipts_settings
    ):
        receipts_settings.max_files_per_user_per_day = 3
        receipts_settings.save()

        for _ in range(3):
            start(funded_user)

        with pytest.raises(DailyLimitExceededError):
            start(funded_user)
        assert Receipt.objects.filter(user=funded_user).count() == 3

    def test_the_window_restarts_after_24_hours(self, funded_user, receipts_settings):
        receipts_settings.max_files_per_user_per_day = 1
        receipts_settings.save()
        start(funded_user)
        with pytest.raises(DailyLimitExceededError):
            start(funded_user)

        ReceiptsAccount.objects.filter(pk=funded_user.pk).update(
            intake_window_started_at=timezone.now() - timedelta(hours=25)
        )

        assert start(funded_user)
        assert ReceiptsAccount.objects.get(pk=funded_user.pk).intake_count == 1

    def test_deleting_receipts_does_not_free_up_the_cap(
        self, funded_user, fake_storage, receipts_settings
    ):
        from ..services import lifecycle

        receipts_settings.max_files_per_user_per_day = 2
        receipts_settings.save()
        first = start(funded_user).receipt
        second = start(funded_user).receipt
        Receipt.objects.update(deleted_at=timezone.now())
        assert lifecycle.purge_receipt(first)
        assert lifecycle.purge_receipt(second)
        assert not Receipt.objects.filter(user=funded_user).exists()

        with pytest.raises(DailyLimitExceededError):
            start(funded_user)

    def test_a_rejected_start_does_not_use_a_slot(self, funded_user, receipts_settings):
        receipts_settings.max_files_per_user_per_day = 1
        receipts_settings.save()
        with pytest.raises(ValidationError):
            start(funded_user, content_type='text/html', filename='x.html')

        assert start(funded_user)

    def test_the_cap_is_per_user(self, funded_user, regular_user, receipts_settings):
        receipts_settings.max_files_per_user_per_day = 1
        receipts_settings.save()
        start(regular_user)

        assert start(funded_user)

    def test_the_cap_error_is_a_validation_error(self):
        assert issubclass(DailyLimitExceededError, ValidationError)


class TestCompleteUpload:
    def put(self, fake_storage, ticket, data):
        fake_storage.seed(ticket.receipt.file_key, data)

    def test_completes_sniffs_hashes_counts_bytes_and_queues_processing(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        data = samples.jpeg_bytes()
        ticket = start(funded_user, data)
        self.put(fake_storage, ticket, data)

        with django_capture_on_commit_callbacks(execute=True):
            receipt = uploads.complete_upload(ticket.receipt)

        import hashlib

        assert receipt.uploaded is True
        assert receipt.content_type == 'image/jpeg'
        assert receipt.sha256 == hashlib.sha256(data).hexdigest()
        assert receipt.size_bytes == len(data)
        assert get_account(funded_user).stored_bytes == len(data)
        # Queued on commit, and (the test backend runs tasks inline) processed.
        assert len(fake_ai.calls) == 1
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED

    def test_processing_is_queued_only_after_commit(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        data = samples.jpeg_bytes()
        ticket = start(funded_user, data)
        self.put(fake_storage, ticket, data)

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            uploads.complete_upload(ticket.receipt)

        assert fake_ai.calls == []
        assert len(callbacks) == 1

    def test_the_sniffed_type_replaces_the_claimed_one(self, funded_user, fake_storage):
        data = samples.heic_bytes()
        ticket = start(funded_user, data, filename='IMG.heic', content_type='')
        self.put(fake_storage, ticket, data)

        receipt = uploads.complete_upload(ticket.receipt)

        assert receipt.content_type == 'image/heic'

    def test_completing_twice_is_harmless(
        self, funded_user, fake_storage, django_capture_on_commit_callbacks
    ):
        data = samples.jpeg_bytes()
        ticket = start(funded_user, data)
        self.put(fake_storage, ticket, data)

        with django_capture_on_commit_callbacks(execute=True):
            uploads.complete_upload(ticket.receipt)
            uploads.complete_upload(Receipt.objects.get(pk=ticket.receipt.pk))

        assert get_account(funded_user).stored_bytes == len(data)

    def test_a_concurrent_completion_does_not_double_count(self, funded_user, fake_storage):
        data = samples.jpeg_bytes()
        ticket = start(funded_user, data)
        self.put(fake_storage, ticket, data)
        stale = Receipt.objects.get(pk=ticket.receipt.pk)  # another tab's view of the row

        uploads.complete_upload(ticket.receipt)
        uploads.complete_upload(stale)

        assert get_account(funded_user).stored_bytes == len(data)

    def test_missing_object(self, funded_user):
        ticket = start(funded_user)

        with pytest.raises(ValidationError, match='not been uploaded'):
            uploads.complete_upload(ticket.receipt)
        ticket.receipt.refresh_from_db()
        assert ticket.receipt.uploaded is False  # can be retried

    def test_contents_that_do_not_match_the_claimed_type_are_rejected(
        self, funded_user, fake_storage
    ):
        data = samples.png_bytes()
        ticket = start(funded_user, data, filename='x.pdf', content_type='application/pdf')
        self.put(fake_storage, ticket, data)

        with pytest.raises(ValidationError, match='do not match'):
            uploads.complete_upload(ticket.receipt)

        receipt = Receipt.objects.get(pk=ticket.receipt.pk)
        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == FailureReason.UNSUPPORTED_TYPE
        assert receipt.file_key == ''
        assert ticket.receipt.file_key not in fake_storage.objects
        assert get_account(funded_user).stored_bytes == 0

    def test_an_executable_renamed_to_pdf_is_rejected(self, funded_user, fake_storage):
        data = b'MZ\x90\x00 not a pdf at all'
        ticket = start(funded_user, data, filename='x.pdf', content_type='application/pdf')
        self.put(fake_storage, ticket, data)

        with pytest.raises(ValidationError, match='not a supported type'):
            uploads.complete_upload(ticket.receipt)

        assert Receipt.objects.get(pk=ticket.receipt.pk).failure_reason == (
            FailureReason.UNSUPPORTED_TYPE
        )
        assert fake_storage.objects == {}

    def test_a_rejected_upload_is_never_processed(self, funded_user, fake_storage, fake_ai):
        data = b'PK\x03\x04zip'
        ticket = start(funded_user, data, filename='x.pdf', content_type='application/pdf')
        self.put(fake_storage, ticket, data)

        with pytest.raises(ValidationError):
            uploads.complete_upload(ticket.receipt)

        assert fake_ai.calls == []

    def test_a_size_different_from_the_declared_one_is_rejected(self, funded_user, fake_storage):
        ticket = start(funded_user, samples.jpeg_bytes())
        self.put(fake_storage, ticket, samples.jpeg_bytes((50, 50)) + b'extra')

        with pytest.raises(ValidationError, match='size declared'):
            uploads.complete_upload(ticket.receipt)

        assert Receipt.objects.get(pk=ticket.receipt.pk).status == ReceiptStatus.FAILED
        assert fake_storage.objects == {}

    def test_a_checksum_different_from_the_declared_one_is_rejected(
        self, funded_user, fake_storage
    ):
        data = samples.jpeg_bytes()
        ticket = start(funded_user, data, checksum=b64_sha256(b'something else'))
        self.put(fake_storage, ticket, data)

        with pytest.raises(ValidationError, match='corrupted'):
            uploads.complete_upload(ticket.receipt)

    def test_the_checksum_is_compared_even_when_the_backend_does_not_echo_one(
        self, funded_user, fake_storage, monkeypatch
    ):
        from apps.core.services.storage import ObjectInfo

        data = samples.jpeg_bytes()
        ticket = start(funded_user, data, checksum=b64_sha256(b'something else'))
        self.put(fake_storage, ticket, data)
        monkeypatch.setattr(
            fake_storage,
            'head_object',
            lambda key: ObjectInfo(size=len(data), checksum_sha256=''),
        )

        with pytest.raises(ValidationError, match='corrupted'):
            uploads.complete_upload(ticket.receipt)

        assert Receipt.objects.get(pk=ticket.receipt.pk).status == ReceiptStatus.FAILED
        assert fake_storage.objects == {}

    def test_a_matching_checksum_passes_without_a_backend_echo(
        self, funded_user, fake_storage, monkeypatch, fake_ai
    ):
        from apps.core.services.storage import ObjectInfo

        data = samples.jpeg_bytes()
        ticket = start(funded_user, data)
        self.put(fake_storage, ticket, data)
        monkeypatch.setattr(
            fake_storage,
            'head_object',
            lambda key: ObjectInfo(size=len(data), checksum_sha256=''),
        )

        assert uploads.complete_upload(ticket.receipt).uploaded is True

    def test_a_file_over_the_limit_lowered_since_the_upload_started(
        self, funded_user, fake_storage, receipts_settings
    ):
        data = samples.jpeg_bytes()
        ticket = start(funded_user, data)
        self.put(fake_storage, ticket, data)
        receipts_settings.max_file_bytes = len(data) - 1
        receipts_settings.save()

        with pytest.raises(ValidationError, match='too large'):
            uploads.complete_upload(ticket.receipt)

        assert Receipt.objects.get(pk=ticket.receipt.pk).failure_reason == FailureReason.TOO_LARGE


class TestAbandonUpload:
    def test_removes_the_object_and_the_row(self, funded_user, fake_storage):
        ticket = start(funded_user)
        fake_storage.seed(ticket.receipt.file_key, b'partial')

        uploads.abandon_upload(ticket.receipt)

        assert not Receipt.objects.exists()
        assert fake_storage.objects == {}

    def test_a_completed_upload_is_left_alone(self, funded_user, fake_storage, make_receipt):
        receipt = make_receipt()

        uploads.abandon_upload(receipt)

        assert Receipt.objects.filter(pk=receipt.pk).exists()
        assert receipt.file_key in fake_storage.objects


def test_the_bytes_counter_never_goes_below_zero(funded_user):
    get_account(funded_user)

    add_stored_bytes(funded_user.pk, -500)

    assert get_account(funded_user).stored_bytes == 0
