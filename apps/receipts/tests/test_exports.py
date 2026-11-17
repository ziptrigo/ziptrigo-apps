"""Exports (`services/exports.py`): the quote, creating (charging, every refusal), building the
zips against the fake storage (what is in them, part 1 last, the CSV), skipped files, failures and
both refunds, resuming after a dead worker, downloading, ending (delete, expiry, the job) and the
notification and email."""

import csv
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import CreditTransaction, CreditTransactionType
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core.models import Notification
from apps.core.services import exports as engine

from .. import jobs
from ..models import (
    ExportStatus,
    ReceiptExport,
    ReceiptExportItem,
    ReceiptExportPart,
    ReceiptsSettings,
    ReceiptStatus,
)
from ..services import emails
from ..services import exports as svc
from ..services.errors import AccountFrozenError, ExportCostChangedError, ExportInProgressError
from ..services.filters import ReceiptFilters
from ..services.storage import export_key, export_prefix, user_prefix
from .factories import create_receipt, create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PRICE = 3


@pytest.fixture(autouse=True)
def price(receipts_settings):
    receipts_settings.export_price_per_file = PRICE
    receipts_settings.save()
    return receipts_settings


@pytest.fixture
def three(make_receipt):
    """Three live receipts with distinct, known files."""
    return [
        make_receipt(
            data=b'one-' * 5,
            original_filename='one.pdf',
            vendor='Acme',
            receipt_date=date(2026, 3, 4),
            amount=Decimal('10.00'),
            currency='USD',
            document_type='invoice',
            status=ReceiptStatus.PROCESSED,
        ),
        make_receipt(
            data=b'two-' * 5,
            original_filename='two.pdf',
            vendor='Beta Co',
            receipt_date=date(2025, 1, 2),
            amount=Decimal('20.50'),
            currency='EUR',
            document_type='receipt',
            status=ReceiptStatus.PROCESSED,
        ),
        make_receipt(
            data=b'three-' * 5,
            original_filename='three.pdf',
            vendor='',
            receipt_date=None,
            status=ReceiptStatus.NEEDS_REVIEW,
        ),
    ]


def build_all(export: ReceiptExport) -> None:
    """Run every part, in the order the service chooses (what the chain of tasks does)."""
    while (number := svc.next_part_to_build(export)) is not None:
        ReceiptExport.objects.filter(pk=export.pk).update(lease_until=None)
        svc.build_part.call(str(export.pk), number)
        export.refresh_from_db()
        if export.status != ExportStatus.BUILDING:
            break


def zip_of(fake_storage, export: ReceiptExport, number: int) -> zipfile.ZipFile:
    part = export.parts.get(number=number)
    return zipfile.ZipFile(io.BytesIO(fake_storage.objects[part.key]))


def make_export(user, **kwargs) -> ReceiptExport:
    return svc.create_export(user, None, 'year_vendor', expected_cost=kwargs.pop('cost', None))


# -- Quote --------------------------------------------------------------------------------------


class TestQuote:
    def test_counts_size_parts_and_cost(self, funded_user, three):
        quote = svc.quote_export(funded_user)
        assert quote.file_count == 3
        assert quote.total_bytes == 20 + 20 + 30
        assert quote.part_count == 1
        assert quote.price_per_file == PRICE and quote.cost == 9
        assert quote.balance == 100 and quote.affordable and not quote.frozen

    def test_the_price_is_the_admin_setting(self, funded_user, three, price):
        price.export_price_per_file = 7
        price.save()
        assert svc.quote_export(funded_user).cost == 21

    def test_free_when_the_price_is_zero(self, funded_user, three, price):
        price.export_price_per_file = 0
        price.save()
        quote = svc.quote_export(funded_user)
        assert quote.cost == 0 and quote.affordable

    def test_the_default_price_is_three_credits(self, db):
        ReceiptsSettings.objects.all().delete()
        assert ReceiptsSettings.load().export_price_per_file == 3

    def test_filters_narrow_it(self, funded_user, three):
        quote = svc.quote_export(funded_user, ReceiptFilters(q='Acme'))
        assert quote.file_count == 1 and quote.cost == 3

    def test_the_trash_and_unfinished_uploads_are_not_counted(
        self, funded_user, three, make_receipt
    ):
        make_receipt(deleted_at=timezone.now())
        make_receipt(uploaded=False)
        assert svc.quote_export(funded_user).file_count == 3

    def test_every_status_counts_a_failed_receipt_included(self, funded_user, make_receipt):
        make_receipt(status=ReceiptStatus.FAILED)
        make_receipt(status=ReceiptStatus.PROCESSING)
        assert svc.quote_export(funded_user).file_count == 2

    def test_another_users_receipts_are_not_counted(self, funded_user, other_user, fake_storage):
        create_receipt(other_user, fake_storage)
        assert svc.quote_export(funded_user).file_count == 0

    def test_several_parts_when_the_files_are_big(self, funded_user, three, price):
        price.export_part_bytes = 25
        price.save()
        assert svc.quote_export(funded_user).part_count == 3

    def test_an_unknown_layout_is_refused(self, funded_user):
        with pytest.raises(ValidationError):
            svc.quote_export(funded_user, None, 'sideways')

    def test_a_frozen_account_is_reported(self, funded_user, three, freeze):
        assert svc.quote_export(funded_user).frozen


# -- Creating -----------------------------------------------------------------------------------


class TestCreate:
    def test_charges_the_cost_and_writes_everything(self, funded_user, three):
        export = make_export(funded_user, cost=9)

        assert get_balance(funded_user) == 91
        tx = CreditTransaction.objects.filter(user=funded_user, source='receipts').latest(
            'created_at'
        )
        assert tx.amount == -9 and 'Receipts export: 3 files' in tx.description
        assert export.status == ExportStatus.BUILDING
        assert (export.file_count, export.part_count, export.charged_credits) == (3, 1, 9)
        assert export.price_per_file == PRICE and export.layout == 'year_vendor'
        assert export.total_bytes == 70
        assert export.parts.count() == 1
        assert export.parts.get().key == export_key(funded_user.pk, export.pk, 1)
        paths = set(export.items.values_list('path', flat=True))
        assert paths == {
            '2026/Acme/one.pdf',
            '2025/Beta Co/two.pdf',
            'No date/No vendor/three.pdf',
        }

    def test_items_keep_what_the_csv_needs(self, funded_user, three):
        export = make_export(funded_user)
        item = export.items.get(path='2026/Acme/one.pdf')
        assert (item.vendor, item.amount, item.currency, item.document_type) == (
            'Acme',
            Decimal('10.00'),
            'USD',
            'invoice',
        )
        assert item.file_key == three[0].file_key and item.receipt_id == three[0].pk

    def test_the_filters_and_layout_are_recorded(self, funded_user, three):
        vendor = create_vendor(funded_user, 'Acme')
        three[0].vendor_ref = vendor
        three[0].save()
        export = svc.create_export(
            funded_user, ReceiptFilters(vendor=vendor.pk, q='acme'), 'vendor_year'
        )
        assert export.layout == 'vendor_year'
        assert export.filters['params'] == {'vendor': vendor.pk, 'q': 'acme'}
        assert export.filters['summary'] == ['Vendor: Acme', 'Search: "acme"']

    def test_queues_the_last_part_first_and_part_one_last(self, funded_user, three, price):
        price.export_part_bytes = 25
        price.save()
        assert svc.build_order(3) == [2, 3, 1]
        assert svc.first_part_to_build(3) == 2
        assert svc.build_order(1) == [1]

    def test_queues_the_first_build_on_commit(
        self, funded_user, three, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks() as callbacks:
            make_export(funded_user)
        assert len(callbacks) == 1

    def test_a_free_export_spends_nothing(self, funded_user, three, price):
        price.export_price_per_file = 0
        price.save()
        export = make_export(funded_user, cost=0)
        assert export.charged_credits == 0 and get_balance(funded_user) == 100

    def test_a_changed_cost_charges_nothing_and_says_the_new_one(self, funded_user, three):
        with pytest.raises(ExportCostChangedError) as caught:
            make_export(funded_user, cost=6)
        assert caught.value.quote == 9
        assert get_balance(funded_user) == 100 and not ReceiptExport.objects.exists()

    def test_receipts_added_after_the_dialog_change_the_cost(
        self, funded_user, three, make_receipt
    ):
        make_receipt()
        with pytest.raises(ExportCostChangedError) as caught:
            make_export(funded_user, cost=9)
        assert caught.value.quote == 12

    def test_a_price_change_changes_the_cost(self, funded_user, three, price):
        price.export_price_per_file = 5
        price.save()
        with pytest.raises(ExportCostChangedError):
            make_export(funded_user, cost=9)

    def test_no_expected_cost_accepts_any(self, funded_user, three):
        assert make_export(funded_user).charged_credits == 9

    def test_insufficient_credits_roll_everything_back(self, funded_user, three):
        from apps.billing.services import spend_credits

        spend_credits(funded_user, 95, description='x', source='test')
        with pytest.raises(InsufficientCreditsError):
            make_export(funded_user)
        assert get_balance(funded_user) == 5
        assert not ReceiptExport.objects.exists()
        assert not ReceiptExportItem.objects.exists()

    def test_a_frozen_account_is_refused(self, funded_user, three, freeze):
        with pytest.raises(AccountFrozenError):
            make_export(funded_user)
        assert get_balance(funded_user) == 100

    def test_one_at_a_time(self, funded_user, three):
        make_export(funded_user)
        with pytest.raises(ExportInProgressError):
            make_export(funded_user)
        assert ReceiptExport.objects.count() == 1

    def test_a_finished_one_does_not_block_the_next(self, funded_user, three):
        first = make_export(funded_user)
        ReceiptExport.objects.filter(pk=first.pk).update(status=ExportStatus.READY)
        assert make_export(funded_user)

    def test_nothing_to_export_is_refused(self, funded_user):
        with pytest.raises(ValidationError, match='no files'):
            make_export(funded_user)

    def test_an_unknown_layout_is_refused(self, funded_user, three):
        with pytest.raises(ValidationError):
            svc.create_export(funded_user, None, 'sideways')

    def test_check_export_refuses_without_writing(self, funded_user, three):
        svc.check_export(funded_user, None, 'year_vendor', expected_cost=9)
        with pytest.raises(ExportCostChangedError):
            svc.check_export(funded_user, None, 'year_vendor', expected_cost=1)
        assert not ReceiptExport.objects.exists() and get_balance(funded_user) == 100

    def test_check_export_notices_a_balance_that_is_too_low(self, funded_user, three):
        from apps.billing.services import spend_credits

        spend_credits(funded_user, 95, description='x', source='test')
        with pytest.raises(InsufficientCreditsError):
            svc.check_export(funded_user, None, 'year_vendor')

    def test_a_stale_plan_is_planned_again_inside_the_transaction(
        self, funded_user, three, make_receipt
    ):
        prepared = svc.prepare_export(funded_user)
        make_receipt()  # appeared after the plan was made
        with pytest.raises(ExportCostChangedError) as caught:
            svc.create_export(funded_user, None, 'year_vendor', expected_cost=9, prepared=prepared)
        assert caught.value.quote == 12
        assert get_balance(funded_user) == 100 and not ReceiptExport.objects.exists()

    def test_a_stale_plan_with_no_expected_cost_exports_what_is_there_now(
        self, funded_user, three, make_receipt
    ):
        prepared = svc.prepare_export(funded_user)
        make_receipt()
        export = svc.create_export(funded_user, None, 'year_vendor', prepared=prepared)
        assert export.file_count == 4 and export.charged_credits == 12
        assert export.items.count() == 4 and get_balance(funded_user) == 88

    def test_a_price_change_after_the_plan_is_charged_at_the_new_price_only_if_confirmed(
        self, funded_user, three, price
    ):
        prepared = svc.prepare_export(funded_user)
        price.export_price_per_file = 5
        price.save()
        with pytest.raises(ExportCostChangedError) as caught:
            svc.create_export(funded_user, None, 'year_vendor', expected_cost=9, prepared=prepared)
        assert caught.value.quote == 15

    def test_a_prepared_plan_is_not_read_again_when_nothing_changed(
        self, funded_user, three, monkeypatch
    ):
        prepared = svc.prepare_export(funded_user)
        calls = []
        real = svc.select_rows
        monkeypatch.setattr(svc, 'select_rows', lambda *a, **k: calls.append(1) or real(*a, **k))
        svc.check_export(funded_user, None, 'year_vendor', expected_cost=9, prepared=prepared)
        svc.create_export(funded_user, None, 'year_vendor', expected_cost=9, prepared=prepared)
        assert calls == []

    def test_a_frozen_account_is_noticed_inside_the_transaction_too(
        self, funded_user, three, freeze, monkeypatch
    ):
        monkeypatch.setattr(svc, '_refuse_early', lambda user: None)
        with pytest.raises(AccountFrozenError):
            make_export(funded_user)
        assert get_balance(funded_user) == 100 and not ReceiptExport.objects.exists()

    def test_two_requests_racing_for_the_one_slot_leave_one_export_and_one_charge(
        self, funded_user, three, monkeypatch
    ):
        make_export(funded_user)
        monkeypatch.setattr(svc, '_refuse_early', lambda user: None)  # both passed the early check
        with pytest.raises(ExportInProgressError):
            make_export(funded_user)
        assert ReceiptExport.objects.count() == 1 and get_balance(funded_user) == 91
        assert ReceiptExportItem.objects.count() == 3

    def test_the_database_allows_one_building_export_per_user(self, funded_user, three):
        make_export(funded_user)
        with pytest.raises(IntegrityError), transaction.atomic():
            ReceiptExport.objects.create(user=funded_user)

    def test_the_account_row_is_not_locked_while_planning(self, funded_user, three, monkeypatch):
        from ..models import ReceiptsAccount

        locked = []
        real = ReceiptsAccount.objects.__class__.select_for_update
        monkeypatch.setattr(
            ReceiptsAccount.objects.__class__,
            'select_for_update',
            lambda self, *a, **k: locked.append(self.model) or real(self, *a, **k),
        )
        make_export(funded_user)
        assert ReceiptsAccount not in locked

    def test_the_part_size_is_bounded(self, price):
        price.export_part_bytes = 2 * 1024**3
        with pytest.raises(ValidationError):
            price.full_clean()
        price.export_part_bytes = 1024**3
        price.full_clean()


# -- Building -----------------------------------------------------------------------------------


class TestBuild:
    def test_one_zip_with_the_files_and_the_csv(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        build_all(export)

        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        archive = zip_of(fake_storage, export, 1)
        assert sorted(archive.namelist()) == sorted(
            [
                '2026/Acme/one.pdf',
                '2025/Beta Co/two.pdf',
                'No date/No vendor/three.pdf',
                'receipts.csv',
            ]
        )
        assert archive.read('2026/Acme/one.pdf') == b'one-' * 5
        assert archive.read('No date/No vendor/three.pdf') == b'three-' * 5
        assert all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist())
        assert archive.testzip() is None

    def test_the_csv(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        build_all(export)

        rows = list(
            csv.reader(io.StringIO(zip_of(fake_storage, export, 1).read('receipts.csv').decode()))
        )
        assert rows[0] == ['date', 'vendor', 'type', 'amount', 'currency', 'file', 'part']
        assert sorted(rows[1:]) == sorted(
            [
                ['2026-03-04', 'Acme', 'invoice', '10.00', 'USD', '2026/Acme/one.pdf', '1'],
                ['2025-01-02', 'Beta Co', 'receipt', '20.50', 'EUR', '2025/Beta Co/two.pdf', '1'],
                ['', '', '', '', '', 'No date/No vendor/three.pdf', '1'],
            ]
        )

    def test_the_part_is_marked_built_with_its_size(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        build_all(export)
        part = export.parts.get()
        assert part.built_at is not None and part.file_count == 3
        assert part.size_bytes == len(fake_storage.objects[part.key]) > 0

    def test_several_parts_each_open_on_their_own_and_part_one_is_built_last(
        self, funded_user, three, fake_storage, price
    ):
        price.export_part_bytes = 25
        price.save()
        export = make_export(funded_user)
        assert export.part_count == 3

        build_all(export)

        built = list(export.parts.order_by('built_at').values_list('number', flat=True))
        assert built == [2, 3, 1]
        names = []
        for number in (1, 2, 3):
            archive = zip_of(fake_storage, export, number)
            assert archive.testzip() is None
            names += [n for n in archive.namelist() if n != 'receipts.csv']
        assert len(names) == 3 and len(set(names)) == 3
        # Only part 1 has the CSV, and it lists every part's files.
        assert 'receipts.csv' not in zip_of(fake_storage, export, 2).namelist()
        rows = list(
            csv.reader(io.StringIO(zip_of(fake_storage, export, 1).read('receipts.csv').decode()))
        )
        assert sorted(row[6] for row in rows[1:]) == ['1', '2', '3']
        assert {row[5] for row in rows[1:]} == set(names)

    def test_the_export_becomes_ready_with_an_expiry(self, funded_user, three, price):
        price.export_retention_days = 10
        price.save()
        export = make_export(funded_user)
        build_all(export)
        export.refresh_from_db()
        assert export.ready_at is not None
        assert export.expires_at and export.ready_at
        assert export.expires_at - export.ready_at == timedelta(days=10)
        assert export.attempts == 0 and export.lease_until is None

    def test_a_file_that_is_gone_is_left_out_and_refunded(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        del fake_storage.objects[three[1].file_key]

        build_all(export)

        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        assert export.skipped_count == 1 and export.refunded_credits == PRICE
        assert get_balance(funded_user) == 100 - 9 + 3
        refund = CreditTransaction.objects.get(user=funded_user, type=CreditTransactionType.REFUND)
        assert refund.amount == 3 and refund.source == 'receipts'
        archive = zip_of(fake_storage, export, 1)
        assert '2025/Beta Co/two.pdf' not in archive.namelist()
        csv_text = archive.read('receipts.csv').decode()
        assert 'two.pdf' not in csv_text and 'one.pdf' in csv_text
        assert export.items.get(path='2025/Beta Co/two.pdf').skipped

    def test_a_skipped_file_in_a_later_part_is_missing_from_the_csv_too(
        self, funded_user, three, fake_storage, price
    ):
        price.export_part_bytes = 25
        price.save()
        export = make_export(funded_user)
        del fake_storage.objects[three[2].file_key]  # 'No date/...' sorts into the last part
        build_all(export)
        csv_text = zip_of(fake_storage, export, 1).read('receipts.csv').decode()
        assert 'three.pdf' not in csv_text
        export.refresh_from_db()
        assert export.skipped_count == 1

    def test_everything_gone_still_finishes_and_refunds_it_all(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        for receipt in three:
            del fake_storage.objects[receipt.file_key]
        build_all(export)
        export.refresh_from_db()
        assert export.status == ExportStatus.READY and export.refunded_credits == 9
        assert get_balance(funded_user) == 100

    def test_a_task_for_a_finished_or_unknown_export_does_nothing(self, funded_user, three):
        export = make_export(funded_user)
        build_all(export)
        before = dict(export.parts.values_list('number', 'built_at'))
        svc.build_part.call(str(export.pk), 1)
        svc.build_part.call('00000000-0000-0000-0000-000000000000', 1)
        svc.build_part.call('not-a-uuid', 1)
        assert dict(export.parts.values_list('number', 'built_at')) == before

    def test_a_part_out_of_order_hands_over(self, funded_user, three, price, fake_storage):
        price.export_part_bytes = 25
        price.save()
        export = make_export(funded_user)
        svc.build_part.call(str(export.pk), 1)  # part 1 is last: not yet
        assert not export.parts.filter(built_at__isnull=False).exists()

    def test_a_held_lease_stops_a_second_worker(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        ReceiptExport.objects.filter(pk=export.pk).update(
            lease_until=timezone.now() + timedelta(minutes=5)
        )
        svc.build_part.call(str(export.pk), 1)
        assert not export.parts.filter(built_at__isnull=False).exists()
        assert not fake_storage.uploads and not any(
            k.endswith('.zip') for k in fake_storage.objects
        )

    def test_a_duplicate_that_finds_the_part_built_after_its_claim_changes_nothing(
        self, funded_user, three, price, fake_storage, monkeypatch
    ):
        price.export_part_bytes = 25
        price.save()
        export = make_export(funded_user)
        number = svc.next_part_to_build(export)
        real_claim = engine.claim

        def other_worker_builds_first(kind, export_id):
            # Worker A builds and finishes the part between B's read of it and B's claim.
            monkeypatch.setattr(engine, 'claim', real_claim)
            svc.build_part.call(str(export.pk), number)
            return real_claim(kind, export_id)

        writes = []
        real_write = engine.write_part
        monkeypatch.setattr(
            engine, 'write_part', lambda *a, **k: writes.append(1) or real_write(*a, **k)
        )
        monkeypatch.setattr(engine, 'claim', other_worker_builds_first)
        svc.build_part.call(str(export.pk), number)  # worker B

        part = export.parts.get(number=number)
        assert part.built_at is not None and part.key in fake_storage.objects
        assert len(writes) == 1 and not fake_storage.deleted  # only worker A wrote
        export.refresh_from_db()
        assert export.status == ExportStatus.BUILDING
        assert export.lease_until is None and export.attempts == 0

    def test_a_late_duplicate_never_deletes_the_object_of_a_ready_export(
        self, funded_user, three, fake_storage, monkeypatch
    ):
        export = make_export(funded_user)
        real = engine.write_part

        def other_worker_finishes_meanwhile(kind, exp, part):
            result = real(kind, exp, part)
            ReceiptExportPart.objects.filter(pk=part.pk).update(built_at=timezone.now())
            assert svc.finish_export(exp.pk)
            return result

        monkeypatch.setattr(engine, 'write_part', other_worker_finishes_meanwhile)
        svc.build_part.call(str(export.pk), 1)

        export.refresh_from_db()
        part = export.parts.get()
        assert export.status == ExportStatus.READY
        assert part.key in fake_storage.objects and part.key not in fake_storage.deleted
        assert zip_of(fake_storage, export, 1).namelist()

    def test_a_deleted_receipt_between_the_dialog_and_the_build_is_just_skipped(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        three[0].delete()
        del fake_storage.objects[three[0].file_key]
        build_all(export)
        export.refresh_from_db()
        assert export.status == ExportStatus.READY and export.skipped_count == 1

    def test_the_export_is_not_part_of_the_stored_bytes(self, funded_user, three, account):
        before = account.stored_bytes
        export = make_export(funded_user)
        build_all(export)
        account.refresh_from_db()
        assert account.stored_bytes == before

    def test_memory_stays_bounded_the_writer_uploads_in_parts(
        self, funded_user, make_receipt, fake_storage
    ):
        # A file bigger than the writer's buffer is sent in several S3 parts, never all at once.
        big = b'x' * (svc.WRITER_PART_BYTES + 1000)
        make_receipt(data=big, original_filename='big.pdf')
        export = make_export(funded_user)
        build_all(export)
        archive = zip_of(fake_storage, export, 1)
        assert archive.read('No date/No vendor/big.pdf') == big


# -- Failures and refunds -----------------------------------------------------------------------


class TestFailure:
    def test_a_failing_part_is_counted_and_retried_later(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        fake_storage.failing_reads.add(three[0].file_key)

        svc.build_part.call(str(export.pk), 1)

        export.refresh_from_db()
        assert export.status == ExportStatus.BUILDING and export.attempts == 1
        assert export.last_error
        assert export.lease_until > timezone.now()  # waits before the next try
        assert export.parts.get().built_at is None
        assert fake_storage.aborted_uploads and not any(
            k.endswith('.zip') for k in fake_storage.objects
        )

    def test_a_later_success_resets_the_attempts(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        fake_storage.failing_reads.add(three[0].file_key)
        svc.build_part.call(str(export.pk), 1)
        fake_storage.failing_reads.clear()
        build_all(export)
        export.refresh_from_db()
        assert export.status == ExportStatus.READY and export.attempts == 0

    def test_three_failures_fail_the_export_and_refund_everything(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        assert get_balance(funded_user) == 91
        fake_storage.failing_reads.add(three[0].file_key)

        for _ in range(svc.MAX_ATTEMPTS):
            ReceiptExport.objects.filter(pk=export.pk).update(lease_until=None)
            svc.build_part.call(str(export.pk), 1)

        export.refresh_from_db()
        assert export.status == ExportStatus.FAILED
        assert export.refunded_credits == 9 and get_balance(funded_user) == 100
        assert export.last_error
        assert not export.items.exists() and not export.parts.exists()
        note = Notification.objects.get(user=funded_user, key=svc.notice_key(export.pk))
        assert 'could not be prepared' in note.title and '9 credits' in note.body

    def test_failing_twice_refunds_only_once(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        assert svc.fail_export(export.pk, 'boom') is True
        assert svc.fail_export(export.pk, 'boom') is False
        assert (
            CreditTransaction.objects.filter(
                user=funded_user, type=CreditTransactionType.REFUND
            ).count()
            == 1
        )

    def test_finishing_twice_refunds_only_once(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        del fake_storage.objects[three[0].file_key]
        build_all(export)
        assert svc.finish_export(export.pk) is False
        assert (
            CreditTransaction.objects.filter(
                user=funded_user, type=CreditTransactionType.REFUND
            ).count()
            == 1
        )

    def test_a_failed_export_cannot_then_finish(self, funded_user, three):
        export = make_export(funded_user)
        svc.fail_export(export.pk)
        assert svc.finish_export(export.pk) is False

    def test_a_free_failed_export_refunds_nothing(self, funded_user, three, price):
        price.export_price_per_file = 0
        price.save()
        export = make_export(funded_user)
        svc.fail_export(export.pk)
        assert not CreditTransaction.objects.filter(
            user=funded_user, type=CreditTransactionType.REFUND
        ).exists()

    def test_a_part_finishing_after_the_export_failed_deletes_its_object(
        self, funded_user, three, fake_storage, monkeypatch
    ):
        export = make_export(funded_user)
        real = engine.write_part

        def write_then_fail_elsewhere(kind, exp, part):
            result = real(kind, exp, part)
            svc.fail_export(exp.pk, 'failed elsewhere')
            return result

        monkeypatch.setattr(engine, 'write_part', write_then_fail_elsewhere)
        svc.build_part.call(str(export.pk), 1)
        assert not any(k.endswith('.zip') for k in fake_storage.objects)

    def test_a_failure_does_not_count_as_progress(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        before = export.progress_at
        fake_storage.failing_reads.add(three[0].file_key)
        svc.build_part.call(str(export.pk), 1)
        export.refresh_from_db()
        assert export.progress_at == before

    def test_a_worker_that_keeps_dying_fails_the_export_and_refunds_it(
        self, funded_user, three, django_capture_on_commit_callbacks
    ):
        export = make_export(funded_user)
        assert get_balance(funded_user) == 91
        for _ in range(svc.MAX_ATTEMPTS):
            assert engine.claim(svc.KIND, export.pk)  # a worker takes the part ... and is killed
            ReceiptExport.objects.filter(pk=export.pk).update(
                lease_until=timezone.now() - timedelta(minutes=1),
                progress_at=timezone.now() - timedelta(minutes=30),
            )
        assert not engine.claim(svc.KIND, export.pk)  # the attempts are spent

        assert svc.resume_stalled() == 1

        export.refresh_from_db()
        assert export.status == ExportStatus.FAILED
        assert export.refunded_credits == 9 and get_balance(funded_user) == 100
        assert not export.items.exists() and not export.parts.exists()

    def test_a_task_that_finds_the_attempts_spent_fails_the_export(self, funded_user, three):
        export = make_export(funded_user)
        ReceiptExport.objects.filter(pk=export.pk).update(
            attempts=svc.MAX_ATTEMPTS, lease_until=timezone.now() - timedelta(minutes=1)
        )
        svc.build_part.call(str(export.pk), 1)
        export.refresh_from_db()
        assert export.status == ExportStatus.FAILED and get_balance(funded_user) == 100

    def test_dying_workers_are_counted_even_when_a_part_is_rebuilt_in_between(
        self, funded_user, three, price, fake_storage
    ):
        price.export_part_bytes = 25
        price.save()
        export = make_export(funded_user)
        # two interrupted claims, then the parts are built: progress resets the count
        for _ in range(svc.MAX_ATTEMPTS - 1):
            assert engine.claim(svc.KIND, export.pk)
            ReceiptExport.objects.filter(pk=export.pk).update(lease_until=None)
        build_all(export)
        export.refresh_from_db()
        assert export.status == ExportStatus.READY and export.attempts == 0

    def test_a_claim_released_for_nothing_is_not_an_attempt(self, funded_user, three):
        export = make_export(funded_user)
        assert engine.claim(svc.KIND, export.pk)
        engine.release(svc.KIND, export.pk)
        export.refresh_from_db()
        assert export.attempts == 0 and export.lease_until is None
        engine.release(svc.KIND, export.pk)  # never below zero
        export.refresh_from_db()
        assert export.attempts == 0


# -- Resuming ------------------------------------------------------------------------------------


class TestResume:
    def old(self, export, *, lease=None):
        ReceiptExport.objects.filter(pk=export.pk).update(
            progress_at=timezone.now() - timedelta(minutes=30), lease_until=lease
        )

    def test_a_build_nobody_is_working_on_is_picked_up(
        self, funded_user, three, django_capture_on_commit_callbacks
    ):
        export = make_export(funded_user)
        self.old(export)
        with django_capture_on_commit_callbacks(execute=True):
            assert svc.resume_stalled() == 1
        export.refresh_from_db()
        assert export.status == ExportStatus.READY

    def test_a_dead_workers_part_is_taken_over_once_its_lease_ran_out(
        self, funded_user, three, django_capture_on_commit_callbacks
    ):
        export = make_export(funded_user)
        self.old(export, lease=timezone.now() - timedelta(minutes=1))
        with django_capture_on_commit_callbacks(execute=True):
            svc.resume_stalled()
        export.refresh_from_db()
        assert export.status == ExportStatus.READY

    def test_a_part_being_built_is_left_alone(self, funded_user, three):
        export = make_export(funded_user)
        self.old(export, lease=timezone.now() + timedelta(minutes=10))
        assert svc.resume_stalled() == 0

    def test_a_recent_build_is_left_alone(self, funded_user, three):
        make_export(funded_user)
        assert svc.resume_stalled() == 0

    def test_a_ready_export_is_left_alone(self, funded_user, three):
        export = make_export(funded_user)
        build_all(export)
        self.old(export)
        assert svc.resume_stalled() == 0

    def test_a_build_with_every_part_done_but_not_finished_is_finished(self, funded_user, three):
        export = make_export(funded_user)
        ReceiptExportPart.objects.filter(export=export).update(built_at=timezone.now())
        self.old(export)
        svc.resume_stalled()
        export.refresh_from_db()
        assert export.status == ExportStatus.READY

    def test_retry_due_resumes_stalled_exports(
        self, funded_user, three, django_capture_on_commit_callbacks
    ):
        export = make_export(funded_user)
        self.old(export)
        with django_capture_on_commit_callbacks(execute=True):
            jobs.retry_due()
        export.refresh_from_db()
        assert export.status == ExportStatus.READY

    def test_a_failed_part_waits_out_its_backoff_before_it_is_retried(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        fake_storage.failing_reads.add(three[0].file_key)
        svc.build_part.call(str(export.pk), 1)
        ReceiptExport.objects.filter(pk=export.pk).update(
            progress_at=timezone.now() - timedelta(hours=1)
        )
        now = timezone.now()
        assert svc.resume_stalled(now + svc.RETRY_BACKOFF - timedelta(seconds=30)) == 0
        assert svc.resume_stalled(now + svc.RETRY_BACKOFF + timedelta(seconds=30)) == 1


# -- Notifications and email ---------------------------------------------------------------------


class TestReady:
    def test_notifies_with_a_link_to_the_export(self, funded_user, three):
        export = make_export(funded_user)
        build_all(export)
        note = Notification.objects.get(user=funded_user, key=svc.notice_key(export.pk))
        assert note.title == 'Your receipts export is ready'
        assert note.url == f'/receipts/exports/{export.pk}/'
        assert '3 receipt files in 1 zip file' in note.body and 'Available until' in note.body

    def test_one_notice_per_export(self, funded_user, three):
        export = make_export(funded_user)
        build_all(export)
        engine._notify_ready(svc.KIND, export)
        assert (
            Notification.objects.filter(user=funded_user, key=svc.notice_key(export.pk)).count()
            == 1
        )

    def test_the_email_goes_to_a_confirmed_address(self, funded_user, three, sent_emails):
        export = make_export(funded_user)
        build_all(export)

        emails.send_export_ready_email.call(str(export.pk))

        [mail] = sent_emails
        assert mail['to'] == funded_user.email
        assert mail['subject'] == 'Your receipts export is ready'
        assert f'/receipts/exports/{export.pk}/' in mail['text_body']
        assert f'/receipts/exports/{export.pk}/' in mail['html_body']
        assert '3 receipt files in 1 zip file' in mail['text_body']
        assert 'amazonaws' not in mail['text_body'] and 'fake-s3' not in mail['html_body']

    def test_no_email_to_an_unconfirmed_address(self, funded_user, three, sent_emails):
        export = make_export(funded_user)
        build_all(export)
        funded_user.email_confirmed = False
        funded_user.save()
        emails.send_export_ready_email.call(str(export.pk))
        assert sent_emails == []

    def test_no_email_to_an_inactive_account(self, funded_user, three, sent_emails):
        export = make_export(funded_user)
        build_all(export)
        User.objects.filter(pk=funded_user.pk).update(status=User.STATUS_INACTIVE)
        emails.send_export_ready_email.call(str(export.pk))
        assert sent_emails == []
        User.objects.filter(pk=funded_user.pk).update(status=User.STATUS_ACTIVE, is_active=False)
        emails.send_export_ready_email.call(str(export.pk))
        assert sent_emails == []

    def test_no_in_app_notice_for_an_inactive_account(self, funded_user, three):
        User.objects.filter(pk=funded_user.pk).update(status=User.STATUS_INACTIVE)
        export = make_export(funded_user)
        build_all(export)
        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        assert not Notification.objects.filter(user=funded_user).exists()

    def test_no_email_for_an_export_that_is_not_ready(self, funded_user, three, sent_emails):
        export = make_export(funded_user)
        emails.send_export_ready_email.call(str(export.pk))
        assert sent_emails == []

    def test_the_email_says_what_was_left_out(self, funded_user, three, fake_storage, sent_emails):
        export = make_export(funded_user)
        del fake_storage.objects[three[0].file_key]
        build_all(export)
        emails.send_export_ready_email.call(str(export.pk))
        text = sent_emails[0]['text_body']
        assert '1 file was no longer there' in text and 'refunded' in text

    def test_the_html_is_autoescaped_and_the_subject_fixed(self, funded_user, three, sent_emails):
        funded_user.name = '<script>alert(1)</script>'
        funded_user.save()
        export = make_export(funded_user)
        build_all(export)
        emails.send_export_ready_email.call(str(export.pk))
        assert '<script>' not in sent_emails[0]['html_body']
        assert '<script>' not in sent_emails[0]['subject']


# -- Downloading --------------------------------------------------------------------------------


class TestDownload:
    @pytest.fixture
    def ready(self, funded_user, three):
        export = make_export(funded_user)
        build_all(export)
        return export

    def test_a_presigned_attachment_url_named_like_the_part(self, ready, fake_storage):
        url = svc.part_download_url(ready, 1)
        [call] = fake_storage.get_urls
        assert call['key'] == export_key(ready.user_id, ready.pk, 1)
        assert call['disposition'] == 'attachment'
        assert call['filename'] == svc.part_name(ready, 1) and call['filename'].endswith('.zip')
        assert url.startswith('https://fake-s3.test/')

    def test_the_part_is_dated_where_the_user_is(self, ready):
        # 22:30 on the 16th in Los Angeles is already the 17th in UTC, the server's zone.
        User.objects.filter(pk=ready.user_id).update(timezone='America/Los_Angeles')
        ReceiptExport.objects.filter(pk=ready.pk).update(
            created_at=datetime(2026, 11, 17, 6, 30, tzinfo=UTC)
        )
        ready = ReceiptExport.objects.get(pk=ready.pk)
        assert svc.part_name(ready, 1) == 'receipts-2026-11-16.zip'
        User.objects.filter(pk=ready.user_id).update(timezone='UTC')
        ready = ReceiptExport.objects.get(pk=ready.pk)
        assert svc.part_name(ready, 1) == 'receipts-2026-11-17.zip'

    def test_a_multi_part_name_says_which(self, ready):
        ReceiptExport.objects.filter(pk=ready.pk).update(part_count=5)
        ready.refresh_from_db()
        assert '-part-01-of-05.zip' in svc.part_name(ready, 1)

    def test_refused_while_storage_is_paused(self, ready, freeze):
        with pytest.raises(AccountFrozenError):
            svc.part_download_url(ready, 1)

    def test_refused_while_still_building(self, funded_user, three):
        export = make_export(funded_user)
        with pytest.raises(ValidationError):
            svc.part_download_url(export, 1)

    @pytest.mark.parametrize('status', ['failed', 'expired', 'deleted'])
    def test_refused_once_ended(self, ready, status):
        ReceiptExport.objects.filter(pk=ready.pk).update(status=status)
        ready.refresh_from_db()
        with pytest.raises(ValidationError):
            svc.part_download_url(ready, 1)

    def test_an_unknown_part_is_a_typed_refusal(self, ready):
        from ..services.errors import ExportPartMissingError

        with pytest.raises(ExportPartMissingError):
            svc.part_download_url(ready, 9)

    def test_the_part_comes_with_the_url(self, ready):
        url, part = svc.part_download(ready, 1)
        assert part.number == 1 and url.startswith('https://fake-s3.test/')


# -- Ending -------------------------------------------------------------------------------------


class TestEnding:
    @pytest.fixture
    def ready(self, funded_user, three):
        export = make_export(funded_user)
        build_all(export)
        return export

    def test_deleting_removes_the_files_and_rows_without_a_refund(
        self, ready, funded_user, fake_storage
    ):
        key = ready.parts.get().key
        assert key in fake_storage.objects
        balance = get_balance(funded_user)

        assert svc.delete_export(ready) is True

        ready.refresh_from_db()
        assert ready.status == ExportStatus.DELETED
        assert key not in fake_storage.objects
        assert not ready.parts.exists() and not ready.items.exists()
        assert get_balance(funded_user) == balance

    def test_deleting_twice_is_fine(self, ready):
        svc.delete_export(ready)
        assert svc.delete_export(ready) is False

    def test_a_building_export_cannot_be_deleted(self, funded_user, three):
        export = make_export(funded_user)
        with pytest.raises(ValidationError, match='still being prepared'):
            svc.delete_export(export)

    def test_a_failed_export_can_be_deleted(self, funded_user, three):
        export = make_export(funded_user)
        svc.fail_export(export.pk)
        export.refresh_from_db()
        assert svc.delete_export(export) is True

    def test_expiry_deletes_what_is_past_and_keeps_the_rest(
        self, ready, funded_user, make_receipt, fake_storage
    ):
        ReceiptExport.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        make_receipt()
        later = make_export(funded_user)
        build_all(later)

        assert svc.expire_exports() == 1

        ready.refresh_from_db()
        later.refresh_from_db()
        assert ready.status == ExportStatus.EXPIRED and later.status == ExportStatus.READY
        assert not ready.parts.exists() and later.parts.exists()
        assert not any(
            k.startswith(export_prefix(funded_user.pk, ready.pk)) for k in fake_storage.objects
        )
        assert any(
            k.startswith(export_prefix(funded_user.pk, later.pk)) for k in fake_storage.objects
        )

    def test_the_job_expires_and_is_registered(self, ready, fake_storage):
        from apps.core.scheduler.registry import get_jobs

        assert 'receipts.purge_exports' in {job.name for job in get_jobs()}
        ReceiptExport.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        jobs.purge_exports()
        ready.refresh_from_db()
        assert ready.status == ExportStatus.EXPIRED

    def test_a_failed_delete_is_finished_by_the_next_run(self, ready, fake_storage):
        key = ready.parts.get().key
        fake_storage.failing_deletes.add(key)
        ReceiptExport.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        jobs.purge_exports()  # the object could not be deleted: the rows stay
        ready.refresh_from_db()
        assert ready.status == ExportStatus.EXPIRED and ready.parts.exists()
        assert key in fake_storage.objects

        fake_storage.failing_deletes.clear()
        jobs.purge_exports()
        assert not ready.parts.exists() and key not in fake_storage.objects

    def test_one_bad_export_does_not_stop_the_rest(
        self, funded_user, ready, three, fake_storage, monkeypatch
    ):
        other = make_export(funded_user)
        build_all(other)
        ReceiptExport.objects.update(expires_at=timezone.now() - timedelta(days=1))
        real = engine.end
        calls = []

        def flaky(kind, export, **kw):
            calls.append(export.pk)
            if len(calls) == 1:
                raise RuntimeError('boom')
            return real(kind, export, **kw)

        monkeypatch.setattr(engine, 'end', flaky)
        assert svc.expire_exports() == 1
        assert len(calls) == 2

    def test_the_users_whole_prefix_holds_the_exports(self, ready, funded_user):
        assert ready.parts.get().key.startswith(user_prefix(funded_user.pk))
        assert (
            ready.parts.get().key == f'{user_prefix(funded_user.pk)}exports/{ready.pk}/part-01.zip'
        )

    def test_deleting_the_account_deletes_the_zip_files(self, ready, funded_user, fake_storage):
        from ..services import lifecycle

        key = ready.parts.get().key
        lifecycle.delete_user_storage.call(str(funded_user.pk))
        assert key not in fake_storage.objects

    def test_the_receipts_files_are_untouched_by_ending(self, ready, three, fake_storage):
        svc.delete_export(ready)
        assert all(r.file_key in fake_storage.objects for r in three)
