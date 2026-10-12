import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.services import InsufficientCreditsError, get_balance, spend_credits

from ..models import (
    DEFAULT_CATEGORIES,
    FailureReason,
    InboxCode,
    Receipt,
    ReceiptCategory,
    ReceiptStatus,
)
from ..services import actions, processing
from ..services.accounts import active_inbox_code, get_account
from ..services.errors import AccountFrozenError
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def category(user, name):
    get_account(user)
    return ReceiptCategory.objects.get(user=user, name=name)


@pytest.fixture
def needs_review(make_receipt):
    return make_receipt(status=ReceiptStatus.NEEDS_REVIEW, vendor='Staples')


class TestUpdateReceipt:
    def test_records_only_the_extracted_fields_that_changed(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED, vendor='Staples', currency='USD')

        actions.update_receipt(
            receipt, {'vendor': 'Staples', 'currency': 'EUR', 'document_number': 'A-1'}
        )

        receipt.refresh_from_db()
        assert receipt.currency == 'EUR'
        assert receipt.document_number == 'A-1'
        assert sorted(receipt.user_edited_fields) == ['currency', 'document_number']

    def test_notes_are_saved_but_not_tracked(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.update_receipt(receipt, {'notes': 'for the office\r\nrefund pending'})

        receipt.refresh_from_db()
        assert receipt.notes == 'for the office\nrefund pending'
        assert receipt.user_edited_fields == []

    def test_an_edit_that_changes_nothing_is_a_no_op(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED, vendor='Staples')

        actions.update_receipt(receipt, {'vendor': 'Staples'})

        receipt.refresh_from_db()
        assert receipt.user_edited_fields == []

    def test_filling_the_required_fields_moves_needs_review_to_processed(self, needs_review):
        actions.update_receipt(needs_review, {'amount': '42.10', 'receipt_date': '2026-10-09'})

        needs_review.refresh_from_db()
        assert needs_review.status == ReceiptStatus.PROCESSED
        assert needs_review.processed_at is not None
        assert needs_review.amount == Decimal('42.10')
        assert needs_review.receipt_date == date(2026, 10, 9)

    def test_it_stays_in_needs_review_while_a_required_field_is_missing(self, needs_review):
        actions.update_receipt(needs_review, {'amount': '42.10'})

        needs_review.refresh_from_db()
        assert needs_review.status == ReceiptStatus.NEEDS_REVIEW

    def test_clearing_a_required_field_sends_a_processed_receipt_back(self, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='Staples',
            amount=Decimal('1'),
            receipt_date=date(2026, 1, 1),
        )

        actions.update_receipt(receipt, {'vendor': ''})

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW
        assert receipt.processed_at is None

    def test_a_failed_receipt_stays_failed(self, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.FAILED, failure_reason=FailureReason.NOT_A_RECEIPT
        )

        actions.update_receipt(
            receipt, {'vendor': 'V', 'amount': '1', 'receipt_date': '2026-01-01'}
        )

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.vendor == 'V'

    def test_category_by_id_object_or_cleared(self, make_receipt, funded_user):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)
        travel = category(funded_user, 'Travel')

        actions.update_receipt(receipt, {'category': travel.pk})
        receipt.refresh_from_db()
        assert receipt.category == travel

        actions.update_receipt(receipt, {'category': category(funded_user, 'Other')})
        receipt.refresh_from_db()
        assert receipt.category is not None and receipt.category.name == 'Other'

        actions.update_receipt(receipt, {'category': None})
        receipt.refresh_from_db()
        assert receipt.category is None
        assert 'category' in receipt.user_edited_fields

    def test_another_users_category_is_refused(self, make_receipt, regular_user):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)
        get_account(regular_user)
        theirs = ReceiptCategory.objects.filter(user=regular_user).first()

        with pytest.raises(ValidationError) as exc:
            actions.update_receipt(receipt, {'category': theirs.pk})

        assert 'category' in exc.value.message_dict

    @pytest.mark.parametrize(
        ('field', 'value'),
        [
            ('amount', 'abc'),
            ('amount', '-1'),
            ('amount', '99999999999999'),
            ('amount', 'NaN'),
            ('receipt_date', '10/09/2026'),
            ('receipt_date', '2026-13-01'),
            ('currency', 'dollars'),
            ('currency', 'ZZZ'),
            ('document_type', 'bill'),
            ('vendor', 'v' * 256),
            ('document_number', 'n' * 101),
            ('payment_method', 'p' * 101),
            ('notes', 'n' * 5001),
            ('category', 'abc'),
            ('category', 999999),
            ('color', 'red'),
            ('status', 'processed'),
        ],
    )
    def test_invalid_values_are_refused_with_the_field_named(self, make_receipt, field, value):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        with pytest.raises(ValidationError) as exc:
            actions.update_receipt(receipt, {field: value})

        assert field in exc.value.message_dict
        receipt.refresh_from_db()
        assert receipt.user_edited_fields == []

    def test_several_errors_are_reported_together(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        with pytest.raises(ValidationError) as exc:
            actions.update_receipt(receipt, {'amount': 'x', 'currency': 'nope'})

        assert set(exc.value.message_dict) == {'amount', 'currency'}

    def test_boundary_lengths_are_accepted(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.update_receipt(receipt, {'vendor': 'v' * 255, 'notes': 'n' * 5000})

        receipt.refresh_from_db()
        assert len(receipt.vendor) == 255 and len(receipt.notes) == 5000

    def test_values_are_normalized(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.update_receipt(
            receipt,
            {
                'vendor': '  Staples  ',
                'amount': '1,234.567',
                'currency': ' usd ',
                'document_type': 'INVOICE',
                'receipt_date': date(2026, 10, 9),
            },
        )

        receipt.refresh_from_db()
        assert receipt.vendor == 'Staples'
        assert receipt.amount == Decimal('1234.57')
        assert receipt.currency == 'USD'
        assert receipt.document_type == 'invoice'

    def test_a_card_number_with_dots_typed_by_hand_is_masked(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.update_receipt(receipt, {'payment_method': 'Visa 4111.1111.1111.1111'})

        receipt.refresh_from_db()
        assert receipt.payment_method == 'Visa ••1111'

    def test_a_full_card_number_typed_by_hand_is_masked(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.update_receipt(receipt, {'payment_method': 'Visa 4111 1111 1111 1111'})

        receipt.refresh_from_db()
        assert receipt.payment_method == 'Visa ••1111'

    def test_blank_optional_values_clear_the_field(self, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED, amount=Decimal('5'), receipt_date=date(2026, 1, 1)
        )

        actions.update_receipt(receipt, {'amount': '', 'receipt_date': None})

        receipt.refresh_from_db()
        assert receipt.amount is None and receipt.receipt_date is None

    @pytest.mark.parametrize(
        'status',
        [ReceiptStatus.RECEIVED, ReceiptStatus.PROCESSING, ReceiptStatus.WAITING_FOR_CREDITS],
    )
    def test_extracted_fields_cannot_be_edited_while_processing_but_notes_can(
        self, make_receipt, status
    ):
        receipt = make_receipt(status=status)

        with pytest.raises(ValidationError, match='still being processed'):
            actions.update_receipt(receipt, {'vendor': 'X'})

        actions.update_receipt(receipt, {'notes': 'ok'})
        receipt.refresh_from_db()
        assert receipt.notes == 'ok'

    def test_a_trashed_receipt_cannot_be_edited(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED, deleted_at=timezone.now())

        with pytest.raises(ValidationError, match='Trash'):
            actions.update_receipt(receipt, {'notes': 'x'})

    def test_editing_into_a_duplicate_flags_it(self, make_receipt):
        make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='Staples',
            receipt_date=date(2026, 10, 9),
            amount=Decimal('42.10'),
        )
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)

        actions.update_receipt(
            receipt, {'vendor': 'staples', 'receipt_date': '2026-10-09', 'amount': '42.10'}
        )

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED
        assert receipt.possible_duplicate_of_id is not None

    def test_edits_survive_a_rerun_of_the_extraction(self, make_receipt, fake_ai):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)
        actions.update_receipt(receipt, {'vendor': 'My vendor', 'notes': 'mine'})

        actions.reprocess(receipt)
        processing.process(str(receipt.pk))

        receipt.refresh_from_db()
        assert receipt.vendor == 'My vendor'
        assert receipt.notes == 'mine'
        assert receipt.amount == Decimal('42.10')  # the fields the user did not touch are refreshed


class TestReprocess:
    def test_takes_a_new_attempt_and_queues_processing(
        self, make_receipt, funded_user, django_capture_on_commit_callbacks
    ):
        receipt = make_receipt(
            status=ReceiptStatus.FAILED,
            failure_reason=FailureReason.NOT_A_RECEIPT,
            attempts=2,
            last_error='x',
            refunded_at=timezone.now(),
        )
        old_attempt = receipt.attempt_id

        with django_capture_on_commit_callbacks(execute=True):
            actions.reprocess(receipt)

        receipt.refresh_from_db()
        assert receipt.attempt_id != old_attempt
        assert receipt.status == ReceiptStatus.PROCESSED  # (the test backend runs it inline)
        assert receipt.attempts == 0
        assert receipt.refunded_at is None
        assert get_balance(funded_user) == 99

    def test_the_new_attempt_is_charged_again(
        self, make_receipt, funded_user, django_capture_on_commit_callbacks
    ):
        receipt = make_receipt()
        processing.process(str(receipt.pk))
        assert get_balance(funded_user) == 99

        with django_capture_on_commit_callbacks(execute=True):
            actions.reprocess(receipt)

        assert get_balance(funded_user) == 98

    def test_queues_only_after_commit(
        self, make_receipt, fake_ai, django_capture_on_commit_callbacks
    ):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            actions.reprocess(receipt)

        assert len(callbacks) == 1
        assert fake_ai.calls == []
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.RECEIVED

    def test_not_enough_credits_is_refused_up_front(self, make_receipt, funded_user):
        spend_credits(funded_user, 100, source='test')
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)
        attempt = receipt.attempt_id

        with pytest.raises(InsufficientCreditsError):
            actions.reprocess(receipt)

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW
        assert receipt.attempt_id == attempt

    def test_a_free_price_needs_no_balance(self, make_receipt, funded_user, receipts_settings):
        receipts_settings.price_per_file = 0
        receipts_settings.save()
        spend_credits(funded_user, 100, source='test')
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)

        actions.reprocess(receipt)

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.RECEIVED

    def test_a_frozen_account_is_refused(self, make_receipt, funded_user):
        account = get_account(funded_user)
        account.frozen_at = timezone.now()
        account.save()
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)

        with pytest.raises(AccountFrozenError):
            actions.reprocess(receipt)

    @pytest.mark.parametrize(
        'status',
        [ReceiptStatus.RECEIVED, ReceiptStatus.PROCESSING, ReceiptStatus.WAITING_FOR_CREDITS],
    )
    def test_not_while_it_is_being_processed(self, make_receipt, status):
        receipt = make_receipt(status=status)

        with pytest.raises(ValidationError, match='already being processed'):
            actions.reprocess(receipt)

    @pytest.mark.parametrize('reason', [FailureReason.UNSUPPORTED_TYPE, FailureReason.TOO_LARGE])
    def test_a_file_that_can_never_be_read_is_refused(self, make_receipt, reason):
        receipt = make_receipt(status=ReceiptStatus.FAILED, failure_reason=reason)

        with pytest.raises(ValidationError, match='cannot be processed again'):
            actions.reprocess(receipt)

    def test_a_rejected_upload_with_no_file_is_refused(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.FAILED, file_key='', store=False)

        with pytest.raises(ValidationError):
            actions.reprocess(receipt)

    def test_a_trashed_receipt_is_refused(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED, deleted_at=timezone.now())

        with pytest.raises(ValidationError, match='Trash'):
            actions.reprocess(receipt)


class TestTrash:
    def test_trash_and_restore(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.trash(receipt)
        assert receipt.deleted_at is not None
        assert Receipt.objects.get(pk=receipt.pk).deleted_at is not None

        actions.restore(receipt)
        assert receipt.deleted_at is None
        assert Receipt.objects.get(pk=receipt.pk).deleted_at is None

    def test_trash_and_restore_bump_updated_at(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)
        long_ago = timezone.now() - timedelta(days=2)
        Receipt.objects.filter(pk=receipt.pk).update(updated_at=long_ago)

        actions.trash(receipt)
        assert Receipt.objects.get(pk=receipt.pk).updated_at > long_ago

        Receipt.objects.filter(pk=receipt.pk).update(updated_at=long_ago)
        actions.restore(receipt)
        assert Receipt.objects.get(pk=receipt.pk).updated_at > long_ago

    def test_trashing_twice_keeps_the_first_time(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)
        actions.trash(receipt)
        first = receipt.deleted_at

        actions.trash(receipt)

        assert receipt.deleted_at == first

    def test_a_trashed_receipt_keeps_its_files(self, make_receipt, fake_storage):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.trash(receipt)

        assert receipt.file_key in fake_storage.objects

    def test_trashing_unflags_the_receipts_that_pointed_at_it(self, make_receipt):
        original = make_receipt(status=ReceiptStatus.PROCESSED)
        copy = make_receipt(status=ReceiptStatus.PROCESSED, possible_duplicate_of=original)

        actions.trash(original)

        copy.refresh_from_db()
        assert copy.possible_duplicate_of is None

    def test_restoring_rechecks_for_duplicates(self, make_receipt):
        fields = {
            'status': ReceiptStatus.PROCESSED,
            'vendor': 'Staples',
            'receipt_date': date(2026, 10, 9),
            'amount': Decimal('42.10'),
        }
        original = make_receipt(**fields)
        trashed = make_receipt(deleted_at=timezone.now(), **fields)

        actions.restore(trashed)

        assert trashed.possible_duplicate_of_id == original.pk

    def test_restoring_a_never_processed_receipt_queues_it(
        self, make_receipt, fake_ai, django_capture_on_commit_callbacks
    ):
        receipt = make_receipt(deleted_at=timezone.now())

        with django_capture_on_commit_callbacks(execute=True):
            actions.restore(receipt)

        assert len(fake_ai.calls) == 1

    def test_restoring_a_live_receipt_is_a_no_op(self, make_receipt, fake_ai):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.restore(receipt)

        assert fake_ai.calls == []

    def test_delete_forever_requires_the_trash(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        with pytest.raises(ValidationError, match='Trash'):
            actions.delete_forever(receipt)

    def test_delete_forever_is_refused_while_the_receipt_is_being_processed(
        self, make_receipt, fake_storage
    ):
        receipt = make_receipt(status=ReceiptStatus.PROCESSING, deleted_at=timezone.now())

        with pytest.raises(ValidationError, match='being processed'):
            actions.delete_forever(receipt)

        assert Receipt.objects.filter(pk=receipt.pk).exists()
        assert receipt.file_key in fake_storage.objects

    def test_delete_forever_removes_the_files_the_row_and_the_bytes(
        self, make_receipt, fake_storage, funded_user
    ):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED, deleted_at=timezone.now())
        assert get_account(funded_user).stored_bytes > 0

        actions.delete_forever(receipt)

        assert not Receipt.objects.exists()
        assert fake_storage.objects == {}
        assert get_account(funded_user).stored_bytes == 0


class TestDismissDuplicate:
    def test_clears_the_flag_for_good(self, make_receipt):
        original = make_receipt(status=ReceiptStatus.PROCESSED)
        copy = make_receipt(status=ReceiptStatus.PROCESSED, possible_duplicate_of=original)

        actions.dismiss_duplicate(copy)

        copy.refresh_from_db()
        assert copy.possible_duplicate_of is None
        assert copy.duplicate_dismissed is True


class TestInbox:
    def test_regenerating_retires_the_old_code_immediately(self, funded_user):
        account = get_account(funded_user)
        old = active_inbox_code(account)

        new = actions.regenerate_inbox_code(funded_user)

        old.refresh_from_db()
        assert old.active is False and old.retired_at is not None
        assert new.active is True and new.code != old.code
        assert InboxCode.objects.filter(account=account, active=True).get() == new

    def test_retired_codes_are_kept_forever(self, funded_user):
        account = get_account(funded_user)
        first = active_inbox_code(account).code

        for _ in range(3):
            actions.regenerate_inbox_code(funded_user)

        assert InboxCode.objects.filter(account=account).count() == 4
        assert InboxCode.objects.filter(code=first, active=False).exists()

    def test_update_allowlist_and_currency(self, funded_user):
        actions.update_inbox_settings(
            funded_user, sender_allowlist='Me@Acme.com, *@acme.com', default_currency='eur'
        )

        account = get_account(funded_user)
        assert account.sender_allowlist == ['me@acme.com', '*@acme.com']
        assert account.default_currency == 'EUR'

    def test_none_leaves_a_setting_alone_and_empty_clears_the_allowlist(self, funded_user):
        actions.update_inbox_settings(
            funded_user, sender_allowlist=['a@b.co'], default_currency='GBP'
        )

        actions.update_inbox_settings(funded_user, default_currency='USD')
        assert get_account(funded_user).sender_allowlist == ['a@b.co']

        actions.update_inbox_settings(funded_user, sender_allowlist='')
        account = get_account(funded_user)
        assert account.sender_allowlist == []
        assert account.default_currency == 'USD'

    def test_invalid_input_changes_nothing_and_names_the_fields(self, funded_user):
        actions.update_inbox_settings(funded_user, sender_allowlist=['a@b.co'])

        with pytest.raises(ValidationError) as exc:
            actions.update_inbox_settings(
                funded_user, sender_allowlist='not an address', default_currency='DOLLARS'
            )

        assert set(exc.value.message_dict) == {'sender_allowlist', 'default_currency'}
        account = get_account(funded_user)
        assert account.sender_allowlist == ['a@b.co']
        assert account.default_currency == 'USD'

    def test_currency_must_be_iso_4217(self, funded_user):
        with pytest.raises(ValidationError):
            actions.update_inbox_settings(funded_user, default_currency='ABC')


class TestCategories:
    def test_create_appends_after_the_defaults(self, funded_user):
        created = actions.create_category(funded_user, '  Client   gifts ')

        assert created.name == 'Client gifts'
        names = list(
            ReceiptCategory.objects.filter(user=funded_user).values_list('name', flat=True)
        )
        assert names == [*DEFAULT_CATEGORIES, 'Client gifts']

    def test_create_for_a_new_user_seeds_the_defaults_first(self, user):
        actions.create_category(user, 'Mine')

        assert ReceiptCategory.objects.filter(user=user).count() == len(DEFAULT_CATEGORIES) + 1

    @pytest.mark.parametrize('name', ['', '   ', 'x' * 101, '\x00\x01'])
    def test_names_are_validated(self, funded_user, name):
        with pytest.raises(ValidationError):
            actions.create_category(funded_user, name)

    def test_names_are_unique_ignoring_case(self, funded_user):
        get_account(funded_user)

        with pytest.raises(ValidationError, match='already have'):
            actions.create_category(funded_user, 'travel')

    def test_another_user_may_use_the_same_name(self, funded_user, regular_user):
        get_account(funded_user)

        assert actions.create_category(regular_user, 'Fancy') is not None
        assert actions.create_category(funded_user, 'Fancy') is not None

    def test_the_number_of_categories_is_capped(self, funded_user, monkeypatch):
        get_account(funded_user)
        monkeypatch.setattr(actions, 'MAX_CATEGORIES', len(DEFAULT_CATEGORIES))

        with pytest.raises(ValidationError, match='at most'):
            actions.create_category(funded_user, 'One too many')

    def test_rename(self, funded_user):
        target = category(funded_user, 'Travel')

        actions.rename_category(target, 'Trips')

        target.refresh_from_db()
        assert target.name == 'Trips'

    def test_renaming_to_its_own_name_with_different_case_is_allowed(self, funded_user):
        target = category(funded_user, 'Travel')

        actions.rename_category(target, 'TRAVEL')

        target.refresh_from_db()
        assert target.name == 'TRAVEL'

    def test_renaming_onto_another_category_is_refused(self, funded_user):
        target = category(funded_user, 'Travel')

        with pytest.raises(ValidationError, match='already have'):
            actions.rename_category(target, 'Equipment')

    def test_reorder_puts_the_given_ones_first_and_keeps_the_rest_in_order(self, funded_user):
        get_account(funded_user)
        other = category(funded_user, 'Other')
        travel = category(funded_user, 'Travel')

        actions.reorder_categories(funded_user, [other.pk, travel.pk])

        names = list(
            ReceiptCategory.objects.filter(user=funded_user).values_list('name', flat=True)
        )
        assert names[:2] == ['Other', 'Travel']
        remaining = [n for n in DEFAULT_CATEGORIES if n not in ('Other', 'Travel')]
        assert names[2:] == remaining

    @pytest.mark.parametrize('bad', ['abc', None, '1.5', [3]])
    def test_reorder_refuses_non_integer_ids_with_a_validation_error(self, funded_user, bad):
        get_account(funded_user)

        with pytest.raises(ValidationError, match='Unknown category'):
            actions.reorder_categories(funded_user, [bad])

    def test_reorder_refuses_foreign_ids(self, funded_user, regular_user):
        get_account(regular_user)
        foreign = ReceiptCategory.objects.filter(user=regular_user).first()
        get_account(funded_user)

        with pytest.raises(ValidationError):
            actions.reorder_categories(funded_user, [foreign.pk])

    def test_delete_leaves_its_receipts_uncategorized(self, make_receipt, funded_user):
        travel = category(funded_user, 'Travel')
        receipt = make_receipt(status=ReceiptStatus.PROCESSED, category=travel)

        actions.delete_category(travel)

        receipt.refresh_from_db()
        assert receipt.category is None
        assert not ReceiptCategory.objects.filter(pk=travel.pk).exists()


def test_clean_changes_validates_without_saving(funded_user):
    cleaned = actions.clean_changes(funded_user, {'amount': '3', 'vendor': ' A '})

    assert cleaned == {'amount': Decimal('3.00'), 'vendor': 'A'}


def test_a_receipt_of_another_user_is_not_found_by_id(make_receipt, regular_user, fake_storage):
    other = create_receipt(regular_user, fake_storage)

    assert not Receipt.objects.filter(pk=other.pk, user=make_receipt().user).exists()
    assert uuid.UUID(str(other.pk))


def test_week_old_trash_is_unaffected_by_restore_logic(make_receipt):
    receipt = make_receipt(
        status=ReceiptStatus.PROCESSED, deleted_at=timezone.now() - timedelta(days=7)
    )

    actions.restore(receipt)

    assert receipt.deleted_at is None
