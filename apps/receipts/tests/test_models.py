import re
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from ..models import (
    CODE_ALPHABET,
    DEFAULT_CATEGORIES,
    InboxCode,
    Receipt,
    ReceiptCategory,
    ReceiptsAccount,
    ReceiptsSettings,
    ReceiptStatus,
    generate_code,
)
from ..services.accounts import active_inbox_code, get_account, inbox_address

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


class TestAccountCreation:
    def test_get_account_creates_it_with_defaults_categories_and_a_code(self, user):
        account = get_account(user)

        assert account.pk == user.pk
        assert account.default_currency == 'USD'
        assert account.sender_allowlist == []
        assert account.stored_bytes == 0
        assert account.frozen_at is None
        assert account.last_billed_at is not None
        names = list(ReceiptCategory.objects.filter(user=user).values_list('name', flat=True))
        assert names == list(DEFAULT_CATEGORIES)
        assert InboxCode.objects.filter(account=account, active=True).count() == 1

    def test_default_categories_are_the_ones_from_the_spec(self):
        assert DEFAULT_CATEGORIES == (
            'Advertising & marketing',
            'Bank & payment fees',
            'Equipment',
            'Meals & entertainment',
            'Office supplies',
            'Professional services',
            'Rent & utilities',
            'Software & subscriptions',
            'Phone & internet',
            'Travel',
            'Vehicle & fuel',
            'Other',
        )

    def test_get_account_is_idempotent(self, user):
        first = get_account(user)
        second = get_account(user)

        assert first.pk == second.pk
        assert ReceiptCategory.objects.filter(user=user).count() == len(DEFAULT_CATEGORIES)
        assert InboxCode.objects.count() == 1

    def test_categories_are_per_user(self, user, regular_user):
        get_account(user)
        get_account(regular_user)

        assert ReceiptCategory.objects.filter(user=user).count() == len(DEFAULT_CATEGORIES)
        assert ReceiptCategory.objects.filter(user=regular_user).count() == len(DEFAULT_CATEGORIES)

    def test_category_names_are_unique_per_user(self, user):
        get_account(user)

        with pytest.raises(IntegrityError), transaction.atomic():
            ReceiptCategory.objects.create(user=user, name='Travel')

    def test_deleting_the_user_deletes_the_account(self, user):
        get_account(user)

        user.delete()

        assert not ReceiptsAccount.objects.exists()
        assert not ReceiptCategory.objects.exists()
        assert not InboxCode.objects.exists()


class TestInboxCodes:
    def test_generated_codes_are_8_lowercase_characters_without_lookalikes(self):
        for _ in range(200):
            code = generate_code()
            assert re.fullmatch(r'[a-z2-9]{8}', code)
            assert not set(code) & set('ilo01')

    def test_alphabet_has_no_lookalikes_or_uppercase(self):
        assert not set(CODE_ALPHABET) & set('ilo01')
        assert CODE_ALPHABET == CODE_ALPHABET.lower()

    def test_issue_retries_when_a_code_is_taken(self, user, monkeypatch):
        account = get_account(user)
        taken = active_inbox_code(account).code
        InboxCode.objects.filter(account=account).update(active=False)
        codes = iter([taken, taken, 'abcdefgh'])
        monkeypatch.setattr('apps.receipts.models.inbox_code.generate_code', lambda: next(codes))

        code = InboxCode.issue(account)

        assert code.code == 'abcdefgh'

    def test_a_retired_code_is_never_reissued(self, user, monkeypatch):
        account = get_account(user)
        old = active_inbox_code(account)
        InboxCode.objects.filter(pk=old.pk).update(active=False)
        codes = iter([old.code, 'zzzzzzzz'])
        monkeypatch.setattr('apps.receipts.models.inbox_code.generate_code', lambda: next(codes))

        assert InboxCode.issue(account).code == 'zzzzzzzz'

    def test_only_one_active_code_per_account(self, user):
        account = get_account(user)

        with pytest.raises(IntegrityError), transaction.atomic():
            InboxCode.objects.create(account=account, code='qqqqqqqq')

    def test_inbox_address_uses_the_configured_inbound_address(self, settings):
        settings.RECEIPTS_INBOUND_ADDRESS = 'receipts-staging@ziptrigo.com'

        assert inbox_address('k7m2xq9p') == 'receipts-staging+k7m2xq9p@ziptrigo.com'


class TestReceipt:
    def test_defaults(self, funded_user):
        receipt = Receipt.objects.create(user=funded_user)

        assert receipt.status == ReceiptStatus.RECEIVED
        assert receipt.uploaded is True
        assert receipt.amount is None
        assert receipt.receipt_date is None
        assert receipt.notes == ''
        assert receipt.user_edited_fields == []
        assert receipt.credits_charged == 0
        assert receipt.deleted_at is None

    def test_receipt_date_is_separate_from_created_at(self, funded_user):
        receipt = Receipt.objects.create(user=funded_user)

        assert receipt.created_at is not None
        assert receipt.receipt_date is None

    def test_missing_required_fields(self, funded_user):
        receipt = Receipt(user=funded_user, vendor='Staples')

        assert receipt.missing_required_fields == ['amount', 'receipt_date']

    def test_a_zero_amount_is_not_missing(self, funded_user):
        from datetime import date

        receipt = Receipt(
            user=funded_user,
            vendor='Free sample',
            amount=Decimal('0.00'),
            receipt_date=date.today(),
        )

        assert receipt.missing_required_fields == []

    def test_deleting_a_category_leaves_the_receipt_uncategorized(self, funded_user):
        category = ReceiptCategory.objects.filter(user=funded_user).first() or (
            ReceiptCategory.objects.create(user=funded_user, name='X')
        )
        receipt = Receipt.objects.create(user=funded_user, category=category)

        category.delete()
        receipt.refresh_from_db()

        assert receipt.category is None

    def test_a_duplicate_link_is_cleared_when_the_original_is_deleted(self, funded_user):
        original = Receipt.objects.create(user=funded_user)
        copy = Receipt.objects.create(user=funded_user, possible_duplicate_of=original)

        original.delete()
        copy.refresh_from_db()

        assert copy.possible_duplicate_of is None


class TestSettings:
    def test_load_creates_the_singleton_with_the_spec_defaults(self, db):
        settings_row = ReceiptsSettings.load()

        assert settings_row.pk == 1
        assert settings_row.price_per_file == 1
        assert settings_row.max_file_bytes == 15 * 1024 * 1024
        assert settings_row.trash_days == 30
        assert settings_row.max_pdf_pages_to_ai == 5
        assert settings_row.max_ai_attempts == 3
        assert settings_row.allowed_types_list() == [
            'application/pdf',
            'image/jpeg',
            'image/png',
            'image/webp',
            'image/heic',
        ]

    def test_max_pdf_pages_to_ai_is_bounded(self, db):
        row = ReceiptsSettings.load()
        row.max_pdf_pages_to_ai = 21
        with pytest.raises(ValidationError):
            row.full_clean()
        row.max_pdf_pages_to_ai = 0
        with pytest.raises(ValidationError):
            row.full_clean()
        row.max_pdf_pages_to_ai = 20
        row.full_clean()

    def test_load_is_a_singleton(self, db):
        ReceiptsSettings.load()
        ReceiptsSettings.load()

        assert ReceiptsSettings.objects.count() == 1

    def test_is_type_allowed_follows_the_admin_list(self, db):
        row = ReceiptsSettings.load()
        row.allowed_types = 'application/pdf'
        row.save()

        assert row.is_type_allowed('application/pdf')
        assert not row.is_type_allowed('image/png')
