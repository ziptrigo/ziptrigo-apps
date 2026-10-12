import hashlib
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import CreditTransaction, CreditTransactionType
from apps.billing.services import add_credits, get_balance, spend_credits

from ..models import (
    FailureReason,
    Receipt,
    ReceiptCategory,
    ReceiptsSettings,
    ReceiptStatus,
)
from ..services import ai, processing
from ..services.accounts import get_account
from ..services.ai import InvalidAIResponseError, PermanentAIError, TransientAIError
from ..services.storage import preview_key
from . import samples
from .factories import create_inbound_email, create_receipt
from .fakes import sample_extraction

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def run(receipt) -> Receipt:
    processing.process(str(receipt.pk))
    receipt.refresh_from_db()
    return receipt


def spends(user):
    return CreditTransaction.objects.filter(user=user, type=CreditTransactionType.SPEND)


def refunds(user):
    return CreditTransaction.objects.filter(user=user, type=CreditTransactionType.REFUND)


class TestHappyPath:
    def test_extracts_fills_the_fields_and_charges_once(self, make_receipt, funded_user, fake_ai):
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.PROCESSED
        assert receipt.failure_reason == ''
        assert receipt.vendor == 'Staples'
        assert receipt.amount == Decimal('42.10')
        assert receipt.currency == 'USD'
        assert receipt.receipt_date == date(2026, 10, 9)
        assert receipt.document_type == 'receipt'
        assert receipt.document_number == 'R-55102'
        assert receipt.payment_method == 'Visa ••4242'
        assert receipt.category is not None
        assert receipt.category.name == 'Office supplies'
        assert receipt.ai_confidence == 0.95
        assert receipt.ai_result is not None and receipt.ai_result['vendor'] == 'Staples'
        assert receipt.processed_at is not None
        assert receipt.processing_started_at is None
        assert receipt.charged_attempt_id == receipt.attempt_id
        assert receipt.credits_charged == 1
        assert get_balance(funded_user) == 99
        assert spends(funded_user).count() == 1
        assert spends(funded_user).get().source == 'receipts'

    def test_the_ai_gets_the_image_and_the_users_categories(self, make_receipt, fake_ai):
        receipt = make_receipt()
        ReceiptCategory.objects.filter(user=receipt.user, name='Other').update(name='Misc')

        run(receipt)

        [request] = fake_ai.calls
        assert len(request.images) == 1
        assert request.images[0].mime_type == 'image/jpeg'
        assert request.images[0].data_uri().startswith('data:image/jpeg;base64,')
        assert 'Misc' in request.categories and 'Other' not in request.categories
        assert request.text == ''

    def test_a_text_pdf_is_sent_as_text(self, make_receipt, fake_ai):
        receipt = make_receipt(data=samples.text_pdf_bytes(), original_filename='x.pdf')

        run(receipt)

        [request] = fake_ai.calls
        assert request.images == []
        assert 'Staples' in request.text

    def test_a_scanned_pdf_is_sent_as_images(self, make_receipt, fake_ai):
        receipt = make_receipt(data=samples.scanned_pdf_bytes(), original_filename='x.pdf')

        run(receipt)

        [request] = fake_ai.calls
        assert request.text == ''
        assert len(request.images) == 1

    def test_an_html_email_body_is_sent_as_text(self, make_receipt, fake_ai):
        receipt = make_receipt(data=samples.HTML_BODY, original_filename='')

        run(receipt)

        [request] = fake_ai.calls
        assert 'Staples' in request.text and 'alert' not in request.text
        assert receipt.status == ReceiptStatus.PROCESSED

    def test_unknown_category_is_left_empty_and_currency_defaults(self, make_receipt, fake_ai):
        fake_ai.default = sample_extraction(category='Dinosaurs', currency=None)
        receipt = make_receipt()
        account = get_account(receipt.user)
        account.default_currency = 'EUR'
        account.save()

        run(receipt)

        assert receipt.category is None
        assert receipt.currency == 'EUR'
        assert receipt.status == ReceiptStatus.PROCESSED

    def test_category_matching_ignores_case(self, make_receipt, fake_ai):
        fake_ai.default = sample_extraction(category='office SUPPLIES')
        receipt = make_receipt()

        run(receipt)

        assert receipt.category is not None and receipt.category.name == 'Office supplies'

    def test_the_task_runs_on_the_receipts_queue(self):
        assert processing.process_receipt.queue_name == 'receipts'

    def test_a_long_filename_is_truncated_in_the_ledger(self, make_receipt, funded_user):
        receipt = make_receipt(original_filename='x' * 255)

        run(receipt)

        assert len(spends(funded_user).get().description) <= 255

    def test_the_task_wrapper_processes(self, make_receipt, django_capture_on_commit_callbacks):
        receipt = make_receipt()

        processing.process_receipt.enqueue(str(receipt.pk))

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED


class TestCharging:
    def test_a_rerun_of_the_same_attempt_never_charges_again(self, make_receipt, funded_user):
        receipt = make_receipt()
        run(receipt)
        assert get_balance(funded_user) == 99

        # At-least-once delivery: the task runs again for the same attempt.
        Receipt.objects.filter(pk=receipt.pk).update(status=ReceiptStatus.RECEIVED)
        run(receipt)

        assert get_balance(funded_user) == 99
        assert spends(funded_user).count() == 1
        assert receipt.status == ReceiptStatus.PROCESSED

    def test_running_a_settled_receipt_does_nothing(self, make_receipt, fake_ai, funded_user):
        receipt = make_receipt()
        run(receipt)

        run(receipt)

        assert len(fake_ai.calls) == 1
        assert get_balance(funded_user) == 99

    def test_two_runs_at_once_only_one_wins(self, make_receipt, fake_ai):
        receipt = make_receipt()
        Receipt.objects.filter(pk=receipt.pk).update(status=ReceiptStatus.PROCESSING)

        run(receipt)

        assert fake_ai.calls == []

    def test_a_new_attempt_is_charged_again(self, make_receipt, funded_user):
        from ..services import actions

        receipt = make_receipt()
        run(receipt)

        actions.reprocess(receipt)
        run(receipt)

        assert get_balance(funded_user) == 98
        assert spends(funded_user).count() == 2

    def test_a_price_of_zero_is_free(self, make_receipt, funded_user, receipts_settings):
        receipts_settings.price_per_file = 0
        receipts_settings.save()
        spend_credits(funded_user, 100, source='test')  # nothing left to pay with
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.PROCESSED
        assert receipt.credits_charged == 0
        assert receipt.charged_attempt_id == receipt.attempt_id
        assert not spends(funded_user).filter(source='receipts').exists()

    def test_the_price_comes_from_the_settings(self, make_receipt, funded_user, receipts_settings):
        receipts_settings.price_per_file = 5
        receipts_settings.save()

        receipt = run(make_receipt())

        assert receipt.credits_charged == 5
        assert get_balance(funded_user) == 95

    def test_not_enough_credits_waits(self, make_receipt, funded_user, fake_ai):
        spend_credits(funded_user, 100, source='test')
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.WAITING_FOR_CREDITS
        assert receipt.charged_attempt_id is None
        assert fake_ai.calls == []
        assert get_balance(funded_user) == 0

    def test_a_frozen_account_waits_without_being_charged(self, make_receipt, funded_user, fake_ai):
        account = get_account(funded_user)
        account.frozen_at = timezone.now()
        account.save()
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.WAITING_FOR_CREDITS
        assert get_balance(funded_user) == 100
        assert fake_ai.calls == []

    def test_topping_up_processes_a_waiting_receipt_exactly_once(
        self, make_receipt, funded_user, django_capture_on_commit_callbacks
    ):
        spend_credits(funded_user, 100, source='test')
        receipt = make_receipt()
        run(receipt)
        assert receipt.status == ReceiptStatus.WAITING_FOR_CREDITS

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 10, source='test')

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED
        assert get_balance(funded_user) == 9
        assert spends(funded_user).filter(source='receipts').count() == 1

    def test_a_trashed_receipt_is_not_processed(self, make_receipt, fake_ai):
        receipt = make_receipt(deleted_at=timezone.now())

        run(receipt)

        assert receipt.status == ReceiptStatus.RECEIVED
        assert fake_ai.calls == []

    def test_an_upload_still_in_progress_is_not_processed(self, make_receipt, fake_ai):
        receipt = make_receipt(uploaded=False)

        run(receipt)

        assert fake_ai.calls == []


class TestOutcomes:
    def test_several_receipts_in_one_file_fail(self, make_receipt, fake_ai, funded_user):
        fake_ai.default = sample_extraction(receipt_count=3)
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == FailureReason.MULTIPLE_RECEIPTS
        assert receipt.vendor == ''  # the AI's guesses are not shown
        assert receipt.amount is None
        assert receipt.ai_result is not None
        # The AI did the work: no refund.
        assert not refunds(funded_user).exists()
        assert get_balance(funded_user) == 99

    @pytest.mark.parametrize(
        'overrides', [{'is_receipt': False}, {'receipt_count': 0}], ids=['flag', 'count']
    )
    def test_not_a_receipt_fails(self, make_receipt, fake_ai, overrides):
        fake_ai.default = sample_extraction(**overrides)
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == FailureReason.NOT_A_RECEIPT
        assert receipt.vendor == ''

    @pytest.mark.parametrize(
        ('overrides', 'missing'),
        [
            ({'vendor': None}, ['vendor']),
            ({'amount': None}, ['amount']),
            ({'receipt_date': None}, ['receipt_date']),
            (
                {'vendor': None, 'amount': None, 'receipt_date': None},
                ['vendor', 'amount', 'receipt_date'],
            ),
        ],
    )
    def test_a_missing_required_field_needs_review(self, make_receipt, fake_ai, overrides, missing):
        fake_ai.default = sample_extraction(**overrides)
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.NEEDS_REVIEW
        assert receipt.missing_required_fields == missing
        assert receipt.processed_at is None
        assert receipt.failure_reason == ''

    def test_low_confidence_needs_review(self, make_receipt, fake_ai):
        fake_ai.default = sample_extraction(confidence=0.2)
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.NEEDS_REVIEW
        assert receipt.vendor == 'Staples'  # what it found is kept for the user to confirm

    def test_a_zero_amount_is_fine(self, make_receipt, fake_ai):
        fake_ai.default = sample_extraction(amount=Decimal('0'))
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.PROCESSED
        assert receipt.amount == Decimal('0.00')

    def test_the_masked_payment_method_is_what_gets_stored(self, make_receipt, fake_ai):
        fake_ai.default = sample_extraction(payment_method='Visa 4111 1111 1111 1111')
        receipt = make_receipt()

        run(receipt)

        assert receipt.payment_method == 'Visa ••1111'
        assert receipt.ai_result is not None
        assert '4111' not in str(receipt.ai_result)


class TestUserEditsAreKept:
    def test_user_edited_fields_are_never_overwritten(self, make_receipt, fake_ai):
        receipt = make_receipt(
            vendor='My vendor',
            amount=Decimal('1.00'),
            user_edited_fields=['vendor', 'amount'],
            notes='printer toner',
        )

        run(receipt)

        assert receipt.vendor == 'My vendor'
        assert receipt.amount == Decimal('1.00')
        assert receipt.receipt_date == date(2026, 10, 9)  # not edited: from the AI
        assert receipt.user_edited_fields == ['vendor', 'amount']
        assert receipt.status == ReceiptStatus.PROCESSED

    def test_a_user_cleared_field_stays_cleared(self, make_receipt, fake_ai):
        receipt = make_receipt(document_number='', user_edited_fields=['document_number'])

        run(receipt)

        assert receipt.document_number == ''

    def test_notes_are_never_touched(self, make_receipt):
        receipt = make_receipt(notes='front office toner')

        run(receipt)

        assert receipt.notes == 'front office toner'

    def test_notes_edited_while_the_ai_runs_survive(self, make_receipt, fake_ai):
        receipt = make_receipt()

        def edit_notes_during_the_call(request):
            Receipt.objects.filter(pk=receipt.pk).update(notes='written meanwhile')
            return sample_extraction()

        fake_ai.extract = edit_notes_during_the_call

        run(receipt)

        assert receipt.notes == 'written meanwhile'

    def test_non_edited_fields_are_replaced_by_a_rerun(self, make_receipt, fake_ai):
        receipt = make_receipt(vendor='Old AI vendor', amount=Decimal('9.99'))

        run(receipt)

        assert receipt.vendor == 'Staples'
        assert receipt.amount == Decimal('42.10')

    def test_a_rerun_blanks_a_field_the_ai_no_longer_finds(self, make_receipt, fake_ai):
        fake_ai.default = sample_extraction(document_number=None)
        receipt = make_receipt(document_number='OLD-1')

        run(receipt)

        assert receipt.document_number == ''

    def test_low_confidence_is_trusted_when_the_user_supplied_every_required_field(
        self, make_receipt, fake_ai
    ):
        fake_ai.default = sample_extraction(confidence=0.1)
        receipt = make_receipt(
            vendor='V',
            amount=Decimal('5'),
            receipt_date=date(2026, 1, 1),
            user_edited_fields=['vendor', 'amount', 'receipt_date'],
        )

        run(receipt)

        assert receipt.status == ReceiptStatus.PROCESSED

    def test_a_result_for_a_superseded_attempt_is_discarded(self, make_receipt, fake_ai):
        import uuid

        receipt = make_receipt()

        def rerun_requested_meanwhile(request):
            Receipt.objects.filter(pk=receipt.pk).update(
                attempt_id=uuid.uuid4(), status=ReceiptStatus.RECEIVED
            )
            return sample_extraction()

        fake_ai.extract = rerun_requested_meanwhile

        run(receipt)

        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.vendor == ''


def retry_time_comes(receipt) -> None:
    """The backoff elapses (a claim refuses a receipt before its `next_attempt_at`)."""
    Receipt.objects.filter(pk=receipt.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )


class TestRetriesAndRefunds:
    def test_a_transient_error_backs_off(self, make_receipt, fake_ai, funded_user):
        fake_ai.queue = [TransientAIError('rate limited')]
        receipt = make_receipt()
        before = timezone.now()

        run(receipt)

        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.attempts == 1
        assert receipt.next_attempt_at is not None
        assert receipt.next_attempt_at >= before + processing.RETRY_BACKOFF[0]
        assert receipt.last_error == 'rate limited'
        assert receipt.processing_started_at is None
        assert get_balance(funded_user) == 99  # charged, not refunded: it will be retried

    def test_the_retry_succeeds_without_charging_again(self, make_receipt, fake_ai, funded_user):
        fake_ai.queue = [TransientAIError('timeout')]
        receipt = make_receipt()
        run(receipt)
        retry_time_comes(receipt)

        run(receipt)

        assert receipt.status == ReceiptStatus.PROCESSED
        assert receipt.next_attempt_at is None
        assert receipt.last_error == ''
        assert get_balance(funded_user) == 99
        assert spends(funded_user).count() == 1

    def test_backoff_grows_and_repeats_the_last_step(self):
        assert processing.retry_delay(1) == processing.RETRY_BACKOFF[0]
        assert processing.retry_delay(2) == processing.RETRY_BACKOFF[1]
        assert processing.retry_delay(3) == processing.RETRY_BACKOFF[2]
        assert processing.retry_delay(9) == processing.RETRY_BACKOFF[-1]

    def test_after_max_attempts_it_fails_and_the_charge_is_refunded_once(
        self, make_receipt, fake_ai, funded_user, receipts_settings
    ):
        assert receipts_settings.max_ai_attempts == 3
        fake_ai.queue = [TransientAIError('boom')] * 3
        receipt = make_receipt()

        run(receipt)
        retry_time_comes(receipt)
        run(receipt)
        assert receipt.status == ReceiptStatus.RECEIVED
        retry_time_comes(receipt)
        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == FailureReason.EXTRACTION_ERROR
        assert receipt.attempts == 3
        assert receipt.refunded_at is not None
        assert receipt.next_attempt_at is None
        assert get_balance(funded_user) == 100
        assert refunds(funded_user).count() == 1
        refund = refunds(funded_user).get()
        assert refund.amount == 1 and refund.source == 'receipts'

    def test_the_refund_is_guarded_against_a_second_run(self, make_receipt, fake_ai, funded_user):
        fake_ai.queue = [PermanentAIError('bad key')]
        receipt = make_receipt()
        run(receipt)
        assert refunds(funded_user).count() == 1

        # Something calls the failure path again for the same attempt.
        Receipt.objects.filter(pk=receipt.pk).update(status=ReceiptStatus.PROCESSING)
        processing._fail(receipt.pk, receipt.attempt_id, FailureReason.EXTRACTION_ERROR)

        assert refunds(funded_user).count() == 1
        assert get_balance(funded_user) == 100

    def test_max_attempts_is_configurable(self, make_receipt, fake_ai, receipts_settings):
        receipts_settings.max_ai_attempts = 1
        receipts_settings.save()
        fake_ai.queue = [TransientAIError('x')]
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED

    def test_a_permanent_error_fails_immediately_and_refunds(
        self, make_receipt, fake_ai, funded_user
    ):
        fake_ai.queue = [PermanentAIError('401')]
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == FailureReason.EXTRACTION_ERROR
        assert receipt.attempts == 0
        assert get_balance(funded_user) == 100

    def test_a_failure_with_a_free_price_has_nothing_to_refund(
        self, make_receipt, fake_ai, funded_user, receipts_settings
    ):
        receipts_settings.price_per_file = 0
        receipts_settings.save()
        fake_ai.queue = [PermanentAIError('x')]
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.refunded_at is None
        assert not refunds(funded_user).exists()

    def test_a_malformed_reply_counts_as_a_failed_try(self, make_receipt, fake_ai):
        fake_ai.queue = [InvalidAIResponseError('not json')]
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.attempts == 1

    def test_an_unexpected_exception_is_a_failed_try_not_a_stuck_receipt(
        self, make_receipt, fake_ai
    ):
        fake_ai.queue = [RuntimeError('bug')]
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.attempts == 1
        assert receipt.next_attempt_at is not None

    def test_an_unconfigured_ai_fails_the_receipt(self, make_receipt, monkeypatch, settings):
        monkeypatch.setattr(ai, '_client', None)
        settings.OPENAI_API_KEY = ''
        settings.OPENAI_RECEIPTS_MODEL = ''
        receipt = make_receipt()

        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == FailureReason.EXTRACTION_ERROR


class TestUnreadableInput:
    def test_a_file_that_cannot_be_decoded_fails_without_calling_the_ai_and_is_refunded(
        self, make_receipt, fake_ai, funded_user
    ):
        receipt = make_receipt(data=b'\xff\xd8\xff garbage', content_type='image/jpeg')

        run(receipt)

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == FailureReason.UNREADABLE
        assert fake_ai.calls == []
        assert get_balance(funded_user) == 100  # no AI call, no charge

    def test_a_missing_object_fails_as_unreadable(self, make_receipt, fake_ai):
        receipt = make_receipt(store=False)

        run(receipt)

        assert receipt.failure_reason == FailureReason.UNREADABLE
        assert fake_ai.calls == []

    def test_an_unsupported_type_fails(self, make_receipt, fake_ai):
        receipt = make_receipt(data=b'PK\x03\x04zip', content_type='application/zip')

        run(receipt)

        assert receipt.failure_reason == FailureReason.UNSUPPORTED_TYPE
        assert fake_ai.calls == []

    def test_an_object_larger_than_expected_fails_as_too_large(
        self, make_receipt, fake_storage, receipts_settings
    ):
        receipt = make_receipt()
        receipts_settings.max_file_bytes = 10
        receipts_settings.save()
        Receipt.objects.filter(pk=receipt.pk).update(size_bytes=10)

        run(receipt)

        assert receipt.failure_reason == FailureReason.TOO_LARGE

    def test_a_storage_outage_is_retried(self, make_receipt, fake_storage, monkeypatch):
        from botocore.exceptions import ClientError

        receipt = make_receipt()

        def boom(*args, **kwargs):
            raise ClientError({'Error': {'Code': 'SlowDown', 'Message': 'x'}}, 'GetObject')

        monkeypatch.setattr(fake_storage, 'get_object_bytes', boom)

        run(receipt)

        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.attempts == 1


class TestClaimBackoff:
    def test_a_receipt_waiting_for_its_retry_time_is_not_claimed(self, make_receipt, fake_ai):
        receipt = make_receipt(next_attempt_at=timezone.now() + timedelta(minutes=10))

        assert processing.claim(receipt.pk) is None

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.RECEIVED
        assert fake_ai.calls == []

    def test_a_receipt_whose_retry_time_has_come_is_claimed(self, make_receipt):
        receipt = make_receipt(next_attempt_at=timezone.now() - timedelta(seconds=1))

        claimed = processing.claim(receipt.pk)

        assert claimed is not None
        assert claimed.status == ReceiptStatus.PROCESSING
        assert claimed.next_attempt_at is None

    def test_a_receipt_with_no_retry_time_is_claimed(self, make_receipt):
        assert processing.claim(make_receipt().pk) is not None


class TestHeicPreview:
    def test_a_jpeg_preview_is_stored_and_counted(self, make_receipt, fake_storage, funded_user):
        original = samples.heic_bytes()
        receipt = make_receipt(data=original, original_filename='IMG.heic')
        before = get_account(funded_user).stored_bytes

        run(receipt)

        assert receipt.content_type == 'image/heic'
        assert receipt.preview_key == preview_key(funded_user.pk, receipt.pk)
        jpeg = fake_storage.objects[receipt.preview_key]
        assert jpeg.startswith(b'\xff\xd8')
        assert receipt.preview_size_bytes == len(jpeg)
        assert fake_storage.content_types[receipt.preview_key] == 'image/jpeg'
        assert get_account(funded_user).stored_bytes == before + len(jpeg)
        # The original is kept untouched.
        assert fake_storage.objects[receipt.file_key] == original

    def test_the_preview_is_written_once(self, make_receipt, funded_user):
        receipt = make_receipt(data=samples.heic_bytes())
        run(receipt)
        stored = get_account(funded_user).stored_bytes

        Receipt.objects.filter(pk=receipt.pk).update(status=ReceiptStatus.RECEIVED)
        run(receipt)

        assert get_account(funded_user).stored_bytes == stored

    def test_a_preview_written_for_a_receipt_purged_meanwhile_is_deleted(
        self, make_receipt, fake_storage, funded_user
    ):
        from ..services.storage import get_storage

        receipt = make_receipt(data=samples.heic_bytes())
        Receipt.objects.filter(pk=receipt.pk).delete()  # purged while the run was working
        before = get_account(funded_user).stored_bytes

        processing._store_preview(receipt, b'\xff\xd8\xff' + b'j' * 50)

        assert preview_key(funded_user.pk, receipt.pk) not in fake_storage.objects
        assert get_account(funded_user).stored_bytes == before
        assert get_storage() is fake_storage

    def test_a_preview_already_stored_by_a_concurrent_run_is_kept(self, make_receipt, fake_storage):
        receipt = make_receipt(data=samples.heic_bytes())
        key = preview_key(receipt.user_id, receipt.pk)
        Receipt.objects.filter(pk=receipt.pk).update(preview_key=key, preview_size_bytes=10)

        processing._store_preview(receipt, b'\xff\xd8\xff' + b'j' * 50)

        assert key in fake_storage.objects

    def test_other_types_get_no_preview(self, make_receipt):
        receipt = make_receipt(data=samples.png_bytes())

        run(receipt)

        assert receipt.preview_key == ''


class TestDuplicates:
    def test_same_file_contents_flag_a_duplicate(self, make_receipt, funded_user, fake_storage):
        digest = hashlib.sha256(b'same').hexdigest()
        first = make_receipt(sha256=digest, vendor='Original')
        second = make_receipt(sha256=digest, data=samples.jpeg_bytes((10, 10)))

        run(second)

        assert second.possible_duplicate_of_id == first.pk

    def test_same_vendor_date_and_amount_flag_a_duplicate(self, make_receipt):
        first = make_receipt(
            vendor='staples', receipt_date=date(2026, 10, 9), amount=Decimal('42.10')
        )
        second = make_receipt()

        run(second)

        assert second.possible_duplicate_of_id == first.pk

    def test_a_different_amount_is_not_a_duplicate(self, make_receipt):
        make_receipt(vendor='Staples', receipt_date=date(2026, 10, 9), amount=Decimal('42.11'))
        second = make_receipt()

        run(second)

        assert second.possible_duplicate_of_id is None

    def test_a_trashed_receipt_is_not_a_duplicate(self, make_receipt):
        make_receipt(
            vendor='Staples',
            receipt_date=date(2026, 10, 9),
            amount=Decimal('42.10'),
            deleted_at=timezone.now(),
        )
        second = make_receipt()

        run(second)

        assert second.possible_duplicate_of_id is None

    def test_other_users_receipts_are_never_matched(self, make_receipt, regular_user, fake_storage):
        create_receipt(
            regular_user,
            fake_storage,
            vendor='Staples',
            receipt_date=date(2026, 10, 9),
            amount=Decimal('42.10'),
        )
        mine = make_receipt()

        run(mine)

        assert mine.possible_duplicate_of_id is None

    def test_a_dismissed_flag_stays_dismissed_after_a_rerun(self, make_receipt):
        make_receipt(vendor='Staples', receipt_date=date(2026, 10, 9), amount=Decimal('42.10'))
        second = make_receipt(duplicate_dismissed=True)

        run(second)

        assert second.possible_duplicate_of_id is None

    def test_the_oldest_match_is_the_original(self, make_receipt):
        oldest = make_receipt(
            vendor='Staples', receipt_date=date(2026, 10, 9), amount=Decimal('42.10')
        )
        make_receipt(vendor='Staples', receipt_date=date(2026, 10, 9), amount=Decimal('42.10'))
        newest = make_receipt()

        run(newest)

        assert newest.possible_duplicate_of_id == oldest.pk

    def test_two_copies_do_not_flag_each_other(self, make_receipt):
        first = make_receipt()
        second = make_receipt()
        run(first)
        run(second)
        assert second.possible_duplicate_of_id == first.pk

        run(first)

        assert first.possible_duplicate_of_id is None


class TestAttentionEmail:
    def test_one_email_when_the_last_receipt_of_an_inbound_email_settles(
        self, funded_user, fake_storage, fake_ai, sent_emails, django_capture_on_commit_callbacks
    ):
        email = create_inbound_email(funded_user)
        good = create_receipt(funded_user, fake_storage, inbound_email=email, source='email')
        bad = create_receipt(
            funded_user,
            fake_storage,
            inbound_email=email,
            source='email',
            original_filename='scan.pdf',
        )
        fake_ai.queue = [sample_extraction(), sample_extraction(receipt_count=2)]

        with django_capture_on_commit_callbacks(execute=True):
            run(good)
            assert sent_emails == []  # one of them is still unsettled
            run(bad)

        [mail] = sent_emails
        assert mail['to'] == funded_user.email
        assert mail['subject'] == '1 receipt needs your attention'
        assert 'scan.pdf' in mail['text_body']
        assert 'more than one receipt' in mail['text_body']
        email.refresh_from_db()
        assert email.attention_notified_at is not None

    def test_the_inbound_email_is_locked_before_the_siblings_are_checked(
        self, funded_user, fake_storage, fake_ai, monkeypatch
    ):
        """The lock is a no-op on SQLite, so assert on the order of the calls instead."""
        from ..services import emails

        events = []
        real_lock, real_queue = (
            processing.lock_inbound_email,
            emails.queue_attention_email_if_settled,
        )
        monkeypatch.setattr(
            processing,
            'lock_inbound_email',
            lambda email_id: (events.append(('lock', email_id)), real_lock(email_id)),
        )
        monkeypatch.setattr(
            processing,
            'queue_attention_email_if_settled',
            lambda receipt: (
                events.append(('check', receipt.inbound_email_id)),
                real_queue(receipt),
            ),
        )
        email = create_inbound_email(funded_user)
        receipt = create_receipt(funded_user, fake_storage, inbound_email=email, source='email')

        run(receipt)

        assert events == [('lock', email.pk), ('check', email.pk)]

    def test_two_siblings_settling_one_after_the_other_send_exactly_one_email(
        self, funded_user, fake_storage, fake_ai, sent_emails, django_capture_on_commit_callbacks
    ):
        fake_ai.default = sample_extraction(receipt_count=2)
        email = create_inbound_email(funded_user)
        first = create_receipt(funded_user, fake_storage, inbound_email=email, source='email')
        second = create_receipt(funded_user, fake_storage, inbound_email=email, source='email')

        with django_capture_on_commit_callbacks(execute=True):
            run(first)
            run(second)

        assert len(sent_emails) == 1
        assert sent_emails[0]['subject'] == '2 receipts need your attention'

    def test_nothing_is_sent_when_everything_processed(
        self, funded_user, fake_storage, sent_emails, django_capture_on_commit_callbacks
    ):
        email = create_inbound_email(funded_user)
        receipt = create_receipt(funded_user, fake_storage, inbound_email=email)

        with django_capture_on_commit_callbacks(execute=True):
            run(receipt)

        assert sent_emails == []

    def test_needs_review_names_what_is_missing(
        self, funded_user, fake_storage, fake_ai, sent_emails, django_capture_on_commit_callbacks
    ):
        fake_ai.default = sample_extraction(amount=None, receipt_date=None)
        email = create_inbound_email(funded_user)
        receipt = create_receipt(
            funded_user, fake_storage, inbound_email=email, original_filename='invoice-oct.pdf'
        )

        with django_capture_on_commit_callbacks(execute=True):
            run(receipt)

        [mail] = sent_emails
        assert "couldn't read the amount, date" in mail['text_body']
        assert str(receipt.pk) in mail['text_body']

    def test_it_is_sent_at_most_once_per_inbound_email(
        self, funded_user, fake_storage, fake_ai, sent_emails, django_capture_on_commit_callbacks
    ):
        fake_ai.default = sample_extraction(receipt_count=2)
        email = create_inbound_email(funded_user)
        receipt = create_receipt(funded_user, fake_storage, inbound_email=email)

        with django_capture_on_commit_callbacks(execute=True):
            run(receipt)
            Receipt.objects.filter(pk=receipt.pk).update(status=ReceiptStatus.RECEIVED)
            run(receipt)

        assert len(sent_emails) == 1

    def test_uploads_without_an_inbound_email_send_nothing(
        self, make_receipt, fake_ai, sent_emails, django_capture_on_commit_callbacks
    ):
        fake_ai.default = sample_extraction(receipt_count=2)
        receipt = make_receipt()

        with django_capture_on_commit_callbacks(execute=True):
            run(receipt)

        assert sent_emails == []

    def test_a_refunded_failure_still_notifies(
        self, funded_user, fake_storage, fake_ai, sent_emails, django_capture_on_commit_callbacks
    ):
        fake_ai.queue = [PermanentAIError('x')]
        email = create_inbound_email(funded_user)
        receipt = create_receipt(funded_user, fake_storage, inbound_email=email)

        with django_capture_on_commit_callbacks(execute=True):
            run(receipt)

        assert len(sent_emails) == 1


class TestStuckAndRequeue:
    def test_a_stuck_processing_receipt_counts_as_a_failed_try(self, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSING,
            processing_started_at=timezone.now() - timedelta(hours=1),
        )

        assert processing.requeue_stuck(receipt.pk, timedelta(minutes=30)) is True

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.attempts == 1
        assert receipt.next_attempt_at is not None

    def test_a_recent_one_is_left_alone(self, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSING, processing_started_at=timezone.now()
        )

        assert processing.requeue_stuck(receipt.pk, timedelta(minutes=30)) is False

    def test_a_receipt_that_keeps_getting_stuck_ends_up_failed_and_refunded(
        self, make_receipt, funded_user
    ):
        receipt = make_receipt()
        run_claim = processing.claim(receipt.pk)
        assert run_claim is not None
        for _ in range(3):
            Receipt.objects.filter(pk=receipt.pk).update(
                status=ReceiptStatus.PROCESSING,
                processing_started_at=timezone.now() - timedelta(hours=1),
            )
            processing.requeue_stuck(receipt.pk, timedelta(minutes=30))

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.FAILED
        assert get_balance(funded_user) == 100


def test_settings_row_is_loaded_by_processing(make_receipt):
    """Processing creates the settings singleton on first use, like every other reader."""
    receipt = make_receipt()
    assert not ReceiptsSettings.objects.exists()

    run(receipt)

    assert ReceiptsSettings.objects.exists()
