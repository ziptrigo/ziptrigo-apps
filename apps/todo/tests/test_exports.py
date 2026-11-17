"""Exports (`services/exports.py`, issue #326): what is selected, the quote, creating (charging,
every refusal), building the zips against the fake storage (what is in them, part 1 last, the
workbook), skipped files, failures and both refunds, resuming after a dead worker, downloading,
ending (delete, expiry, the jobs) and the notification and email. The lifecycle itself is shared
with Receipts (`apps.core.services.exports`); its twin tests are `apps/receipts/tests/test_exports.py`.
"""

import io
import zipfile
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounts.models import User
from apps.billing.models import CreditTransaction, CreditTransactionType
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core.models import Notification
from apps.core.services import exports as engine

from .. import jobs
from ..models import (
    Attachment,
    ExportStatus,
    Project,
    TodoAccount,
    TodoExport,
    TodoExportItem,
    TodoExportPart,
    TodoSettings,
)
from ..services import emails
from ..services import exports as svc
from ..services.errors import AccountFrozenError
from ..services.export_range import ExportRange
from ..services.exports import (
    NOTHING_TO_EXPORT,
    ExportCostChangedError,
    ExportInProgressError,
)
from ..services.storage import attachment_key, export_key, export_prefix, user_prefix
from .factories import created, make_attachment, make_comment, make_task, utc

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PRICE = 3


@pytest.fixture(autouse=True)
def price(config):
    config.export_price_per_file = PRICE
    config.save()
    return config


@pytest.fixture
def freeze(funded_user):
    """Freeze `funded_user`'s To-do account (storage unpaid)."""
    from ..services import get_account

    get_account(funded_user)
    TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())


@pytest.fixture
def three(funded_user, fake_storage):
    """Three live files: one on a task in a project, one on a comment of that task, one on a task
    in no project."""
    home = Project.objects.create(user=funded_user, name='Home')
    rent = make_task(funded_user, number=7001, title='Pay rent', project=home)
    notes = make_task(funded_user, number=7002, title='Call the bank')
    comment = make_comment(rent, 'proof attached')
    return [
        make_attachment(rent, fake_storage, data=b'rent-' * 5, filename='rent.pdf'),
        make_attachment(
            rent,
            fake_storage,
            data=b'proof-' * 5,
            filename='proof.png',
            comment=comment,
            for_comment=True,
        ),
        make_attachment(notes, fake_storage, data=b'notes-' * 5, filename='notes.txt'),
    ]


PATHS = {
    'Home/7001 - Pay rent/rent.pdf',
    'Home/7001 - Pay rent/comments/proof.png',
    'No project/7002 - Call the bank/notes.txt',
}


def build_all(export: TodoExport) -> None:
    """Run every part, in the order the service chooses (what the chain of tasks does)."""
    while (number := svc.next_part_to_build(export)) is not None:
        TodoExport.objects.filter(pk=export.pk).update(lease_until=None)
        svc.build_part.call(str(export.pk), number)
        export.refresh_from_db()
        if export.status != ExportStatus.BUILDING:
            break


def zip_of(fake_storage, export: TodoExport, number: int) -> zipfile.ZipFile:
    part = export.parts.get(number=number)
    return zipfile.ZipFile(io.BytesIO(fake_storage.objects[part.key]))


def workbook_of(fake_storage, export: TodoExport):
    return load_workbook(io.BytesIO(zip_of(fake_storage, export, 1).read('tasks.xlsx')))


def make_export(user, export_range: ExportRange | None = None, **kwargs) -> TodoExport:
    return svc.create_export(user, export_range, expected_cost=kwargs.pop('cost', None))


# -- What is selected ---------------------------------------------------------------------------


class TestSelection:
    def rows(self, user, export_range=None):
        start, end = (export_range or ExportRange()).bounds(user)
        return svc.select_rows(user, start, end)

    def test_the_tasks_files_and_the_comments_files(self, funded_user, three):
        rows = self.rows(funded_user)
        assert {row.file_name for row in rows} == {'rent.pdf', 'proof.png', 'notes.txt'}
        by_name = {row.file_name: row for row in rows}
        assert by_name['rent.pdf'].comment_id is None
        assert by_name['proof.png'].comment_id == three[1].comment_id
        assert by_name['rent.pdf'].project == 'Home' and by_name['notes.txt'].project == ''
        assert by_name['rent.pdf'].task_number == 7001
        assert by_name['rent.pdf'].file_key == attachment_key(funded_user.pk, three[0].pk)
        assert by_name['rent.pdf'].size_bytes == 25

    def test_a_file_whose_upload_never_completed_is_left_out(
        self, funded_user, three, fake_storage
    ):
        make_attachment(three[0].task, fake_storage, uploaded=False, filename='pending.png')
        assert len(self.rows(funded_user)) == 3

    def test_a_draft_comment_upload_is_left_out(self, funded_user, three, fake_storage):
        make_attachment(three[0].task, fake_storage, for_comment=True, filename='draft.png')
        assert 'draft.png' not in {row.file_name for row in self.rows(funded_user)}

    def test_the_files_of_a_deleted_comment_are_left_out(self, funded_user, three):
        three[1].comment.deleted_at = timezone.now()
        three[1].comment.save()
        assert {row.file_name for row in self.rows(funded_user)} == {'rent.pdf', 'notes.txt'}

    def test_a_restored_comments_files_are_back(self, funded_user, three):
        three[1].comment.deleted_at = timezone.now()
        three[1].comment.save()
        three[1].comment.deleted_at = None
        three[1].comment.save()
        assert len(self.rows(funded_user)) == 3

    def test_the_files_of_a_task_in_the_trash_are_left_out(self, funded_user, three):
        three[2].task.deleted_at = timezone.now()
        three[2].task.save()
        assert {row.file_name for row in self.rows(funded_user)} == {'rent.pdf', 'proof.png'}

    def test_another_users_files_are_left_out(self, funded_user, other_user, fake_storage):
        make_attachment(make_task(other_user), fake_storage)
        assert self.rows(funded_user) == []

    def test_closed_tasks_subtasks_and_every_state_are_included(self, funded_user, fake_storage):
        parent = make_task(funded_user, number=7001)
        make_attachment(parent, fake_storage, filename='a.png')
        make_attachment(
            make_task(funded_user, number=7002, completed_at=utc(2026, 11, 1)),
            fake_storage,
            filename='closed.png',
        )
        make_attachment(
            make_task(funded_user, number=7003, parent=parent, state='on_hold'),
            fake_storage,
            filename='sub.png',
        )
        assert {row.file_name for row in self.rows(funded_user)} == {
            'a.png',
            'closed.png',
            'sub.png',
        }

    def test_the_range_is_when_the_task_was_created_not_the_file(self, funded_user, fake_storage):
        old = created(make_task(funded_user, number=7001), utc(2025, 5, 1))
        new = created(make_task(funded_user, number=7002), utc(2026, 11, 5))
        make_attachment(old, fake_storage, filename='old-task.png')
        make_attachment(new, fake_storage, filename='new-task.png')
        # A file attached today to a task made last year is the old task's.
        Attachment.objects.filter(task=old).update(created_at=utc(2026, 11, 6))

        chosen = ExportRange('custom', date(2026, 11, 1), date(2026, 11, 30))
        assert {row.file_name for row in self.rows(funded_user, chosen)} == {'new-task.png'}

    def test_the_range_is_read_in_the_users_time_zone(self, funded_user, fake_storage):
        funded_user.timezone = 'Pacific/Auckland'  # UTC+13 in November
        funded_user.save()
        inside = created(make_task(funded_user, number=7001), utc(2026, 10, 31, 23, 30))
        outside = created(make_task(funded_user, number=7002), utc(2026, 11, 1, 12, 0))
        make_attachment(inside, fake_storage, filename='inside.png')
        make_attachment(outside, fake_storage, filename='outside.png')

        # 31 Oct 23:30 UTC is 1 Nov 12:30 in Auckland; 1 Nov 12:00 UTC is already 2 Nov there.
        chosen = ExportRange('custom', date(2026, 11, 1), date(2026, 11, 1))
        assert {row.file_name for row in self.rows(funded_user, chosen)} == {'inside.png'}

    def test_a_preset_is_resolved_against_the_users_today(
        self, funded_user, fake_storage, freeze_now
    ):
        freeze_now(2026, 11, 16, 12)
        make_attachment(
            created(make_task(funded_user, number=7001), utc(2026, 11, 3)),
            fake_storage,
            filename='this-month.png',
        )
        make_attachment(
            created(make_task(funded_user, number=7002), utc(2026, 10, 31, 12)),
            fake_storage,
            filename='last-month.png',
        )
        chosen = ExportRange('this_month')
        assert {row.file_name for row in self.rows(funded_user, chosen)} == {'this-month.png'}
        assert len(self.rows(funded_user, ExportRange('last_30_days'))) == 2
        assert len(self.rows(funded_user, ExportRange())) == 2

    def test_an_open_ended_custom_range(self, funded_user, fake_storage):
        make_attachment(created(make_task(funded_user, number=7001), utc(2026, 1, 1)), fake_storage)
        make_attachment(created(make_task(funded_user, number=7002), utc(2026, 6, 1)), fake_storage)
        assert len(self.rows(funded_user, ExportRange('custom', date(2026, 3, 1), None))) == 1
        assert len(self.rows(funded_user, ExportRange('custom', None, date(2026, 3, 1)))) == 1
        assert len(self.rows(funded_user, ExportRange('custom'))) == 2


# -- The range ----------------------------------------------------------------------------------


class TestRange:
    def test_bounds_are_the_local_midnights(self, funded_user):
        funded_user.timezone = 'Europe/Lisbon'  # UTC+0 in winter, +1 in summer
        funded_user.save()
        start, end = ExportRange('custom', date(2026, 7, 1), date(2026, 7, 31)).bounds(funded_user)
        assert (start, end) == (utc(2026, 6, 30, 23), utc(2026, 7, 31, 23))

    def test_a_date_at_the_end_of_the_calendar_is_no_bound_rather_than_an_error(self, funded_user):
        funded_user.timezone = (
            'Asia/Tokyo'  # ahead of UTC: midnight of year 1 is before UTC's calendar
        )
        funded_user.save()
        start, end = ExportRange('custom', date.min, date.max).bounds(funded_user)
        assert start is None and end is None
        start, end = ExportRange('custom', date(2026, 1, 1), date(9999, 12, 30)).bounds(funded_user)
        assert start is not None and end is not None

    def test_any_date_has_no_bounds(self, funded_user):
        assert ExportRange().bounds(funded_user) == (None, None)

    def test_params_reproduce_the_range(self):
        assert ExportRange().params == {}
        assert ExportRange('this_year').params == {'date_range': 'this_year'}
        assert ExportRange('custom', date(2026, 1, 2), date(2026, 3, 4)).params == {
            'date_from': '2026-01-02',
            'date_to': '2026-03-04',
        }
        assert ExportRange('custom', None, date(2026, 3, 4)).params == {'date_to': '2026-03-04'}

    def test_described_in_words(self, funded_user):
        assert ExportRange().describe(funded_user) == []
        assert ExportRange('custom', date(2026, 1, 2), date(2026, 3, 4)).describe(funded_user) == [
            'Task created: Jan 2, 2026 to Mar 4, 2026'
        ]
        assert ExportRange('custom', date(2026, 1, 2), None).describe(funded_user) == [
            'Task created: from Jan 2, 2026'
        ]
        assert ExportRange('custom', None, date(2026, 3, 4)).describe(funded_user) == [
            'Task created: until Mar 4, 2026'
        ]


class TestParseRange:
    def parse(self, **data):
        from ..forms import parse_range

        return parse_range(data)

    def test_nothing_is_any_date(self):
        assert self.parse() == ExportRange('any')

    def test_a_preset(self):
        assert self.parse(date_range='this_month') == ExportRange('this_month')

    def test_a_preset_ignores_the_dates(self):
        assert self.parse(date_range='this_month', date_from='2026-01-01') == ExportRange(
            'this_month'
        )

    def test_custom_with_dates(self):
        assert self.parse(date_range='custom', date_from='2026-01-02', date_to='2026-03-04') == (
            ExportRange('custom', date(2026, 1, 2), date(2026, 3, 4))
        )

    def test_dates_with_no_range_are_custom(self):
        assert self.parse(date_from='2026-01-02') == ExportRange('custom', date(2026, 1, 2), None)

    def test_any_wins_over_dates(self):
        assert self.parse(date_range='any', date_from='2026-01-02') == ExportRange('any')

    @pytest.mark.parametrize(
        'data',
        [
            {'date_range': 'nope'},
            {'date_from': 'garbage'},
            {'date_range': 'custom', 'date_to': '99'},
        ],
    )
    def test_a_bad_value_is_dropped(self, data):
        chosen = self.parse(**data)
        assert chosen.date_from is None or chosen.date_range == 'custom'
        assert chosen.date_to is None


# -- Quote --------------------------------------------------------------------------------------


class TestQuote:
    def test_counts_size_parts_and_cost(self, funded_user, three):
        quote = svc.quote_export(funded_user)
        assert quote.file_count == 3
        assert quote.total_bytes == 25 + 30 + 30
        assert quote.part_count == 1
        assert quote.price_per_file == PRICE and quote.cost == 9
        assert quote.balance == 100 and quote.affordable and not quote.frozen
        assert quote.retention_days == 30 and quote.part_bytes == 100 * 1024**2

    def test_the_price_is_the_admin_setting(self, funded_user, three, price):
        price.export_price_per_file = 7
        price.save()
        assert svc.quote_export(funded_user).cost == 21

    def test_free_when_the_price_is_zero(self, funded_user, three, price):
        price.export_price_per_file = 0
        price.save()
        quote = svc.quote_export(funded_user)
        assert quote.cost == 0 and quote.affordable

    def test_the_default_price_is_three_credits_called_export_per_file(self, db):
        TodoSettings.objects.all().delete()
        row = TodoSettings.load()
        assert row.export_price_per_file == 3
        assert row.export_retention_days == 30 and row.export_part_bytes == 100 * 1024**2
        field = TodoSettings._meta.get_field('export_price_per_file')  # ty: ignore[missing-attribute]
        assert field.verbose_name == 'Export per file'

    def test_a_range_narrows_it(self, funded_user, three):
        created(three[2].task, utc(2024, 1, 1))
        chosen = ExportRange('custom', date(2026, 1, 1), None)
        quote = svc.quote_export(funded_user, chosen)
        assert quote.file_count == 0 or quote.file_count == 2

    def test_a_price_is_per_file_not_per_task(self, funded_user, three):
        assert (
            svc.quote_export(funded_user).cost == 3 * PRICE
        )  # two files on one task, one on another

    def test_nothing_is_nothing(self, funded_user):
        quote = svc.quote_export(funded_user)
        assert quote.file_count == 0 and quote.part_count == 0 and quote.cost == 0

    def test_several_parts_when_the_files_are_big(self, funded_user, three, price):
        price.export_part_bytes = 40
        price.save()
        assert svc.quote_export(funded_user).part_count == 3

    def test_a_frozen_account_is_reported(self, funded_user, three, freeze):
        assert svc.quote_export(funded_user).frozen

    def test_the_part_size_is_bounded(self, price):
        price.export_part_bytes = 2 * 1024**3
        with pytest.raises(ValidationError):
            price.full_clean()
        price.export_part_bytes = 1024
        with pytest.raises(ValidationError):
            price.full_clean()
        price.export_part_bytes = 1024**3
        price.full_clean()

    def test_the_retention_is_at_least_a_day(self, price):
        price.export_retention_days = 0
        with pytest.raises(ValidationError):
            price.full_clean()


# -- Creating -----------------------------------------------------------------------------------


class TestCreate:
    def test_charges_the_cost_and_writes_everything(self, funded_user, three):
        export = make_export(funded_user, cost=9)

        assert get_balance(funded_user) == 91
        tx = CreditTransaction.objects.filter(user=funded_user, source='todo').latest('created_at')
        assert tx.amount == -9 and 'To-do export: 3 files' in tx.description
        assert export.status == ExportStatus.BUILDING
        assert (export.file_count, export.part_count, export.charged_credits) == (3, 1, 9)
        assert export.price_per_file == PRICE and export.total_bytes == 85
        assert export.parts.count() == 1
        assert export.parts.get().key == export_key(funded_user.pk, export.pk, 1)
        assert set(export.items.values_list('path', flat=True)) == PATHS

    def test_items_keep_the_ids_the_workbook_needs(self, funded_user, three):
        export = make_export(funded_user)
        item = export.items.get(path='Home/7001 - Pay rent/comments/proof.png')
        assert item.attachment_id == three[1].pk and item.task_id == three[1].task_id
        assert item.comment_id == three[1].comment_id
        assert item.file_key == attachment_key(funded_user.pk, three[1].pk)
        assert item.size_bytes == 30 and item.part_number == 1 and not item.skipped
        assert export.items.get(path='Home/7001 - Pay rent/rent.pdf').comment_id is None

    def test_the_range_is_recorded(self, funded_user, three):
        chosen = ExportRange('custom', date(2020, 1, 1), date(2030, 1, 31))
        export = make_export(funded_user, chosen)
        assert export.filters['params'] == {'date_from': '2020-01-01', 'date_to': '2030-01-31'}
        assert export.filters['summary'] == ['Task created: Jan 1, 2020 to Jan 31, 2030']
        assert export.filters['start'] == '2020-01-01T00:00:00+00:00'
        assert export.filters['end'] == '2030-02-01T00:00:00+00:00'

    def test_every_task_records_no_bounds(self, funded_user, three):
        export = make_export(funded_user)
        assert export.filters == {'summary': [], 'params': {}, 'start': None, 'end': None}

    def test_queues_the_last_part_first_and_part_one_last(self, funded_user, three, price):
        assert svc.build_order(3) == [2, 3, 1]
        assert svc.first_part_to_build(3) == 2
        assert svc.build_order(1) == [1]
        assert svc.build_order(0) == []

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
        assert get_balance(funded_user) == 100 and not TodoExport.objects.exists()

    def test_files_added_after_the_dialog_change_the_cost(self, funded_user, three, fake_storage):
        make_attachment(three[0].task, fake_storage, filename='late.png')
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
        assert not TodoExport.objects.exists() and not TodoExportItem.objects.exists()

    def test_a_frozen_account_is_refused(self, funded_user, three, freeze):
        with pytest.raises(AccountFrozenError):
            make_export(funded_user)
        assert get_balance(funded_user) == 100

    def test_one_at_a_time(self, funded_user, three):
        make_export(funded_user)
        with pytest.raises(ExportInProgressError):
            make_export(funded_user)
        assert TodoExport.objects.count() == 1

    def test_a_finished_one_does_not_block_the_next(self, funded_user, three):
        first = make_export(funded_user)
        TodoExport.objects.filter(pk=first.pk).update(status=ExportStatus.READY)
        assert make_export(funded_user)

    def test_nothing_to_export_is_refused_with_the_xlsx_hint(self, funded_user):
        with pytest.raises(ValidationError) as caught:
            make_export(funded_user)
        assert caught.value.messages == [NOTHING_TO_EXPORT]
        assert 'use Export XLSX' in NOTHING_TO_EXPORT
        # A task with no files is nothing to download either.
        make_task(funded_user)
        with pytest.raises(ValidationError, match='No files in this range'):
            make_export(funded_user)

    def test_check_export_refuses_without_writing(self, funded_user, three):
        svc.check_export(funded_user, None, expected_cost=9)
        with pytest.raises(ExportCostChangedError):
            svc.check_export(funded_user, None, expected_cost=1)
        assert not TodoExport.objects.exists() and get_balance(funded_user) == 100

    def test_check_export_notices_a_balance_that_is_too_low(self, funded_user, three):
        from apps.billing.services import spend_credits

        spend_credits(funded_user, 95, description='x', source='test')
        with pytest.raises(InsufficientCreditsError):
            svc.check_export(funded_user, None)

    def test_a_stale_plan_is_planned_again_inside_the_transaction(
        self, funded_user, three, fake_storage
    ):
        prepared = svc.prepare_export(funded_user)
        make_attachment(three[0].task, fake_storage, filename='late.png')
        with pytest.raises(ExportCostChangedError) as caught:
            svc.create_export(funded_user, None, expected_cost=9, prepared=prepared)
        assert caught.value.quote == 12
        assert get_balance(funded_user) == 100 and not TodoExport.objects.exists()

    def test_a_stale_plan_with_no_expected_cost_exports_what_is_there_now(
        self, funded_user, three, fake_storage
    ):
        prepared = svc.prepare_export(funded_user)
        make_attachment(three[0].task, fake_storage, filename='late.png')
        export = svc.create_export(funded_user, None, prepared=prepared)
        assert export.file_count == 4 and export.charged_credits == 12
        assert export.items.count() == 4 and get_balance(funded_user) == 88

    def test_a_price_change_after_the_plan_is_caught(self, funded_user, three, price):
        prepared = svc.prepare_export(funded_user)
        price.export_price_per_file = 5
        price.save()
        with pytest.raises(ExportCostChangedError) as caught:
            svc.create_export(funded_user, None, expected_cost=9, prepared=prepared)
        assert caught.value.quote == 15

    def test_a_prepared_plan_is_not_read_again_when_nothing_changed(
        self, funded_user, three, monkeypatch
    ):
        prepared = svc.prepare_export(funded_user)
        calls = []
        real = svc.select_rows
        monkeypatch.setattr(svc, 'select_rows', lambda *a, **k: calls.append(1) or real(*a, **k))
        svc.check_export(funded_user, None, expected_cost=9, prepared=prepared)
        svc.create_export(funded_user, None, expected_cost=9, prepared=prepared)
        assert calls == []

    def test_a_frozen_account_is_noticed_inside_the_transaction_too(
        self, funded_user, three, freeze, monkeypatch
    ):
        monkeypatch.setattr(svc, '_refuse_early', lambda user: None)
        with pytest.raises(AccountFrozenError):
            make_export(funded_user)
        assert get_balance(funded_user) == 100 and not TodoExport.objects.exists()

    def test_two_requests_racing_for_the_one_slot_leave_one_export_and_one_charge(
        self, funded_user, three, monkeypatch
    ):
        make_export(funded_user)
        monkeypatch.setattr(svc, '_refuse_early', lambda user: None)  # both passed the early check
        with pytest.raises(ExportInProgressError):
            make_export(funded_user)
        assert TodoExport.objects.count() == 1 and get_balance(funded_user) == 91
        assert TodoExportItem.objects.count() == 3

    def test_the_database_allows_one_building_export_per_user(self, funded_user, three):
        make_export(funded_user)
        with pytest.raises(IntegrityError), transaction.atomic():
            TodoExport.objects.create(user=funded_user)

    def test_the_account_row_is_not_locked_while_planning(self, funded_user, three, monkeypatch):
        locked = []
        real = TodoAccount.objects.__class__.select_for_update
        monkeypatch.setattr(
            TodoAccount.objects.__class__,
            'select_for_update',
            lambda self, *a, **k: locked.append(self.model) or real(self, *a, **k),
        )
        make_export(funded_user)
        assert TodoAccount not in locked


# -- Building -----------------------------------------------------------------------------------


class TestBuild:
    def test_one_zip_with_the_files_and_the_workbook(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        build_all(export)

        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        archive = zip_of(fake_storage, export, 1)
        assert sorted(archive.namelist()) == sorted([*PATHS, 'tasks.xlsx'])
        assert archive.read('Home/7001 - Pay rent/rent.pdf') == b'rent-' * 5
        assert archive.read('Home/7001 - Pay rent/comments/proof.png') == b'proof-' * 5
        assert archive.read('No project/7002 - Call the bank/notes.txt') == b'notes-' * 5
        assert all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist())
        assert archive.testzip() is None

    def test_the_workbook_lists_the_tasks_comments_and_where_each_file_went(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        build_all(export)

        workbook = workbook_of(fake_storage, export)
        tasks, comments = workbook['Tasks'], workbook['Comments']
        by_number = {row[0].value: row for row in tasks.iter_rows(min_row=2)}
        files_column = [cell.value for cell in tasks[1]].index('Files')
        assert by_number[7001][files_column].value == 'Home/7001 - Pay rent/rent.pdf'
        assert by_number[7002][files_column].value == 'No project/7002 - Call the bank/notes.txt'
        [comment] = list(comments.iter_rows(min_row=2))
        assert comment[4].value == 'proof attached'
        assert comment[5].value == 'Home/7001 - Pay rent/comments/proof.png'

    def test_a_task_with_no_files_is_still_a_row_of_the_workbook(
        self, funded_user, three, fake_storage
    ):
        make_task(funded_user, number=7003, title='No files here')
        export = make_export(funded_user)
        build_all(export)
        tasks = workbook_of(fake_storage, export)['Tasks']
        assert [row[0].value for row in tasks.iter_rows(min_row=2)] == [7001, 7002, 7003]
        assert 'No files here' not in ' '.join(zip_of(fake_storage, export, 1).namelist())

    def test_a_task_created_after_the_export_was_asked_for_is_not_in_the_workbook(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        later = make_task(funded_user, number=7003, title='Later')
        created(later, export.created_at + timedelta(minutes=5))
        build_all(export)
        tasks = workbook_of(fake_storage, export)['Tasks']
        assert [row[0].value for row in tasks.iter_rows(min_row=2)] == [7001, 7002]

    def test_the_workbook_is_only_of_the_range(self, funded_user, three, fake_storage):
        created(three[2].task, utc(2024, 3, 3))
        created(three[0].task, utc(2026, 11, 2))
        export = make_export(funded_user, ExportRange('custom', date(2026, 1, 1), None))
        build_all(export)
        archive = zip_of(fake_storage, export, 1)
        assert sorted(archive.namelist()) == sorted(
            [
                'Home/7001 - Pay rent/rent.pdf',
                'Home/7001 - Pay rent/comments/proof.png',
                'tasks.xlsx',
            ]
        )
        tasks = workbook_of(fake_storage, export)['Tasks']
        assert [row[0].value for row in tasks.iter_rows(min_row=2)] == [7001]

    def test_the_part_is_marked_built_with_its_size(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        build_all(export)
        part = export.parts.get()
        assert part.built_at is not None and part.file_count == 3
        assert part.size_bytes == len(fake_storage.objects[part.key]) > 0

    def test_several_parts_each_open_on_their_own_and_part_one_is_built_last(
        self, funded_user, three, fake_storage, price
    ):
        price.export_part_bytes = 40
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
            names += [n for n in archive.namelist() if n != 'tasks.xlsx']
        assert len(names) == 3 and set(names) == PATHS
        # Only part 1 has the workbook, and it lists every part's files with their part.
        assert 'tasks.xlsx' not in zip_of(fake_storage, export, 2).namelist()
        workbook = workbook_of(fake_storage, export)
        lines = []
        for sheet, index in ((workbook['Tasks'], 15), (workbook['Comments'], 5)):
            for row in sheet.iter_rows(min_row=2):
                if row[index].value:
                    lines += row[index].value.split('\n')
        assert sorted(lines) == sorted(
            f'{item.path} (part {item.part_number})' for item in export.items.all()
        )
        assert all(' (part ' in line for line in lines)

    def test_the_export_becomes_ready_with_an_expiry(self, funded_user, three, price):
        price.export_retention_days = 10
        price.save()
        export = make_export(funded_user)
        build_all(export)
        export.refresh_from_db()
        assert export.ready_at is not None and export.expires_at is not None
        assert export.expires_at - export.ready_at == timedelta(days=10)
        assert export.attempts == 0 and export.lease_until is None

    def test_a_file_that_is_gone_is_left_out_and_refunded(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        del fake_storage.objects[attachment_key(funded_user.pk, three[2].pk)]

        build_all(export)

        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        assert export.skipped_count == 1 and export.refunded_credits == PRICE
        assert get_balance(funded_user) == 100 - 9 + 3
        refund = CreditTransaction.objects.get(user=funded_user, type=CreditTransactionType.REFUND)
        assert refund.amount == 3 and refund.source == 'todo'
        assert 'To-do export: 1 file(s) no longer there' in refund.description
        archive = zip_of(fake_storage, export, 1)
        assert 'No project/7002 - Call the bank/notes.txt' not in archive.namelist()
        # ... and the workbook does not claim it.
        files = [
            row[15].value for row in workbook_of(fake_storage, export)['Tasks'].iter_rows(min_row=2)
        ]
        assert files == ['Home/7001 - Pay rent/rent.pdf', None]
        assert export.items.get(path='No project/7002 - Call the bank/notes.txt').skipped

    def test_a_skipped_file_in_a_later_part_is_missing_from_the_workbook_too(
        self, funded_user, three, fake_storage, price
    ):
        price.export_part_bytes = 40
        price.save()
        export = make_export(funded_user)
        del fake_storage.objects[attachment_key(funded_user.pk, three[2].pk)]
        build_all(export)
        text = ' '.join(
            str(cell.value)
            for row in workbook_of(fake_storage, export)['Tasks'].iter_rows()
            for cell in row
        )
        assert 'notes.txt' not in text
        export.refresh_from_db()
        assert export.skipped_count == 1

    def test_everything_gone_still_finishes_and_refunds_it_all(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        for attachment in three:
            del fake_storage.objects[attachment_key(funded_user.pk, attachment.pk)]
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
        price.export_part_bytes = 40
        price.save()
        export = make_export(funded_user)
        svc.build_part.call(str(export.pk), 1)  # part 1 is last: not yet
        assert not export.parts.filter(built_at__isnull=False).exists()

    def test_a_held_lease_stops_a_second_worker(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        TodoExport.objects.filter(pk=export.pk).update(
            lease_until=timezone.now() + timedelta(minutes=5)
        )
        svc.build_part.call(str(export.pk), 1)
        assert not export.parts.filter(built_at__isnull=False).exists()
        assert not fake_storage.uploads and not any(
            k.endswith('.zip') for k in fake_storage.objects
        )

    def test_a_late_duplicate_never_deletes_the_object_of_a_ready_export(
        self, funded_user, three, fake_storage, monkeypatch
    ):
        export = make_export(funded_user)
        real = engine.write_part

        def other_worker_finishes_meanwhile(kind, exp, part):
            result = real(kind, exp, part)
            TodoExportPart.objects.filter(pk=part.pk).update(built_at=timezone.now())
            assert svc.finish_export(exp.pk)
            return result

        monkeypatch.setattr(engine, 'write_part', other_worker_finishes_meanwhile)
        svc.build_part.call(str(export.pk), 1)

        export.refresh_from_db()
        part = export.parts.get()
        assert export.status == ExportStatus.READY
        assert part.key in fake_storage.objects and part.key not in fake_storage.deleted

    def test_the_export_is_not_part_of_the_stored_bytes(self, funded_user, three):
        before = TodoAccount.objects.get(pk=funded_user.pk).stored_bytes
        export = make_export(funded_user)
        build_all(export)
        assert TodoAccount.objects.get(pk=funded_user.pk).stored_bytes == before

    def test_a_file_bigger_than_the_writers_buffer_is_sent_in_several_s3_parts(
        self, funded_user, fake_storage
    ):
        big = b'x' * (svc.WRITER_PART_BYTES + 1000)
        task = make_task(funded_user, number=7001, title='Big')
        make_attachment(task, fake_storage, data=big, filename='big.bin')
        export = make_export(funded_user)
        build_all(export)
        archive = zip_of(fake_storage, export, 1)
        assert archive.read('No project/7001 - Big/big.bin') == big

    def test_other_users_files_never_go_in(self, funded_user, other_user, three, fake_storage):
        make_attachment(make_task(other_user), fake_storage, filename='secret.png')
        export = make_export(funded_user)
        build_all(export)
        assert not any('secret' in name for name in zip_of(fake_storage, export, 1).namelist())
        text = ' '.join(
            str(cell.value)
            for row in workbook_of(fake_storage, export)['Tasks'].iter_rows()
            for cell in row
        )
        assert 'secret' not in text


# -- Failures and refunds -----------------------------------------------------------------------


class TestFailure:
    def test_a_failing_part_is_counted_and_retried_later(self, funded_user, three, fake_storage):
        export = make_export(funded_user)
        fake_storage.failing_reads.add(attachment_key(funded_user.pk, three[0].pk))

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
        key = attachment_key(funded_user.pk, three[0].pk)
        fake_storage.failing_reads.add(key)
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
        fake_storage.failing_reads.add(attachment_key(funded_user.pk, three[0].pk))

        for _ in range(svc.MAX_ATTEMPTS):
            TodoExport.objects.filter(pk=export.pk).update(lease_until=None)
            svc.build_part.call(str(export.pk), 1)

        export.refresh_from_db()
        assert export.status == ExportStatus.FAILED
        assert export.refunded_credits == 9 and get_balance(funded_user) == 100
        assert export.last_error
        assert not export.items.exists() and not export.parts.exists()
        note = Notification.objects.get(user=funded_user, key=svc.notice_key(export.pk))
        assert note.title == 'Your to-do export could not be prepared'
        assert '9 credits' in note.body
        refund = CreditTransaction.objects.get(user=funded_user, type=CreditTransactionType.REFUND)
        assert refund.source == 'todo' and refund.description == (
            'To-do export could not be prepared'
        )

    def test_failing_twice_refunds_only_once(self, funded_user, three):
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
        del fake_storage.objects[attachment_key(funded_user.pk, three[0].pk)]
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
        fake_storage.failing_reads.add(attachment_key(funded_user.pk, three[0].pk))
        svc.build_part.call(str(export.pk), 1)
        export.refresh_from_db()
        assert export.progress_at == before

    def test_a_worker_that_keeps_dying_fails_the_export_and_refunds_it(self, funded_user, three):
        export = make_export(funded_user)
        for _ in range(svc.MAX_ATTEMPTS):
            assert engine.claim(svc.KIND, export.pk)  # a worker takes the part ... and is killed
            TodoExport.objects.filter(pk=export.pk).update(
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
        TodoExport.objects.filter(pk=export.pk).update(
            attempts=svc.MAX_ATTEMPTS, lease_until=timezone.now() - timedelta(minutes=1)
        )
        svc.build_part.call(str(export.pk), 1)
        export.refresh_from_db()
        assert export.status == ExportStatus.FAILED and get_balance(funded_user) == 100

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
        TodoExport.objects.filter(pk=export.pk).update(
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
        TodoExportPart.objects.filter(export=export).update(built_at=timezone.now())
        self.old(export)
        svc.resume_stalled()
        export.refresh_from_db()
        assert export.status == ExportStatus.READY

    def test_the_retry_job_resumes_stalled_exports_and_is_registered(
        self, funded_user, three, django_capture_on_commit_callbacks
    ):
        from apps.core.scheduler.registry import get_jobs

        job = {job.name: job for job in get_jobs()}['todo.retry_exports']
        assert job.interval == timedelta(minutes=5) and job.description
        export = make_export(funded_user)
        self.old(export)
        with django_capture_on_commit_callbacks(execute=True):
            jobs.retry_exports()
        export.refresh_from_db()
        assert export.status == ExportStatus.READY

    def test_a_failed_part_waits_out_its_backoff_before_it_is_retried(
        self, funded_user, three, fake_storage
    ):
        export = make_export(funded_user)
        fake_storage.failing_reads.add(attachment_key(funded_user.pk, three[0].pk))
        svc.build_part.call(str(export.pk), 1)
        TodoExport.objects.filter(pk=export.pk).update(
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
        assert svc.notice_key(export.pk) == f'todo_export:{export.pk}'
        assert note.title == 'Your to-do export is ready'
        assert note.url == f'/todo/exports/{export.pk}/' == svc.export_url_path(export.pk)
        assert '3 files in 1 zip file' in note.body and 'Available until' in note.body
        assert note.source == 'todo'

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
        assert mail['subject'] == 'Your to-do export is ready'
        assert f'/todo/exports/{export.pk}/' in mail['text_body']
        assert f'/todo/exports/{export.pk}/' in mail['html_body']
        assert '3 files in 1 zip file' in mail['text_body']
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

    def test_no_email_for_an_export_that_is_not_ready_or_unknown(
        self, funded_user, three, sent_emails
    ):
        export = make_export(funded_user)
        emails.send_export_ready_email.call(str(export.pk))
        emails.send_export_ready_email.call('00000000-0000-0000-0000-000000000000')
        emails.send_export_ready_email.call('not-a-uuid')
        assert sent_emails == []

    def test_the_email_says_what_was_left_out(self, funded_user, three, fake_storage, sent_emails):
        export = make_export(funded_user)
        del fake_storage.objects[attachment_key(funded_user.pk, three[0].pk)]
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

    def test_the_email_task_is_on_the_default_queue(self):
        assert emails.send_export_ready_email.queue_name == 'default'
        assert svc.build_part.queue_name == 'default'


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
        assert call['filename'] == svc.part_name(ready, 1)
        assert call['filename'] == f'task-files-{timezone.localdate(ready.created_at)}.zip'
        assert url.startswith('https://fake-s3.test/')

    def test_a_multi_part_name_says_which(self, ready):
        TodoExport.objects.filter(pk=ready.pk).update(part_count=5)
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
        TodoExport.objects.filter(pk=ready.pk).update(status=status)
        ready.refresh_from_db()
        with pytest.raises(ValidationError):
            svc.part_download_url(ready, 1)

    def test_an_unknown_part_is_a_typed_refusal(self, ready):
        from apps.core.services.export_errors import ExportPartMissingError

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

    def test_expiry_deletes_what_is_past_and_keeps_the_rest(self, ready, funded_user, fake_storage):
        TodoExport.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )
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

    def test_the_job_expires_and_is_registered(self, ready):
        from apps.core.scheduler.registry import get_jobs

        job = {job.name: job for job in get_jobs()}['todo.purge_exports']
        assert job.interval == timedelta(hours=24) and job.description
        TodoExport.objects.filter(pk=ready.pk).update(expires_at=timezone.now() - timedelta(days=1))
        jobs.purge_exports()
        ready.refresh_from_db()
        assert ready.status == ExportStatus.EXPIRED

    def test_a_failed_delete_is_finished_by_the_next_run(self, ready, fake_storage):
        key = ready.parts.get().key
        fake_storage.failing_prefix_delete = True
        TodoExport.objects.filter(pk=ready.pk).update(expires_at=timezone.now() - timedelta(days=1))

        jobs.purge_exports()  # the objects could not be deleted: the rows stay
        ready.refresh_from_db()
        assert ready.status == ExportStatus.EXPIRED and ready.parts.exists()
        assert key in fake_storage.objects

        fake_storage.failing_prefix_delete = False
        jobs.purge_exports()
        assert not ready.parts.exists() and key not in fake_storage.objects

    def test_one_bad_export_does_not_stop_the_rest(self, funded_user, ready, three, monkeypatch):
        other = make_export(funded_user)
        build_all(other)
        TodoExport.objects.update(expires_at=timezone.now() - timedelta(days=1))
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
        assert ready.parts.get().key.startswith('todo/users/')

    def test_deleting_the_account_deletes_the_zip_files(self, ready, funded_user, fake_storage):
        from ..services import lifecycle

        key = ready.parts.get().key
        lifecycle.delete_user_storage.call(str(funded_user.pk))
        assert key not in fake_storage.objects

    def test_the_attachments_are_untouched_by_ending(self, ready, three, funded_user, fake_storage):
        svc.delete_export(ready)
        assert all(attachment_key(funded_user.pk, a.pk) in fake_storage.objects for a in three)
        assert Attachment.objects.filter(user=funded_user).count() == 3
