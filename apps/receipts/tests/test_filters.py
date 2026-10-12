import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import Receipt, ReceiptCategory, ReceiptStatus
from ..services.accounts import get_account
from ..services.filters import (
    CSV_COLUMNS,
    ReceiptFilters,
    dashboard_counts,
    export_csv,
    filter_receipts,
    iter_csv,
)
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def library(funded_user, fake_storage):
    """A user's receipts in assorted states."""
    get_account(funded_user)
    travel = ReceiptCategory.objects.get(user=funded_user, name='Travel')

    def make(**fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        return create_receipt(funded_user, fake_storage, **fields)

    now = timezone.now()
    receipts = {
        'delta': make(
            vendor='Delta Air Lines',
            amount=Decimal('389.00'),
            currency='USD',
            receipt_date=date(2026, 10, 3),
            category=travel,
            notes='Conference trip',
            original_filename='delta.pdf',
        ),
        'staples': make(
            vendor='Staples',
            amount=Decimal('42.10'),
            currency='USD',
            receipt_date=date(2026, 10, 9),
            notes='printer toner',
        ),
        'old': make(vendor='Old shop', receipt_date=date(2026, 9, 1), amount=Decimal('5')),
        'review': make(status=ReceiptStatus.NEEDS_REVIEW, original_filename='IMG_1.heic'),
        'failed': make(status=ReceiptStatus.FAILED, original_filename='scan.pdf'),
        'trashed': make(vendor='Trashed', deleted_at=now),
    }
    for index, key in enumerate(receipts):
        Receipt.objects.filter(pk=receipts[key].pk).update(
            created_at=now - timedelta(days=len(receipts) - index)
        )
    receipts['staples'].refresh_from_db()
    return receipts


def ids(queryset):
    return [r.pk for r in queryset]


def test_defaults_to_live_receipts_newest_receipt_date_first_with_undated_on_top(
    funded_user, library
):
    result = list(filter_receipts(funded_user))

    assert len(result) == 5
    assert result[0].receipt_date is None and result[1].receipt_date is None
    assert [r.receipt_date for r in result[2:]] == [
        date(2026, 10, 9),
        date(2026, 10, 3),
        date(2026, 9, 1),
    ]


def test_receipt_date_ascending_puts_undated_last(funded_user, library):
    result = list(filter_receipts(funded_user, ReceiptFilters(sort='receipt_date')))

    assert [r.receipt_date for r in result[:3]] == [
        date(2026, 9, 1),
        date(2026, 10, 3),
        date(2026, 10, 9),
    ]
    assert result[-1].receipt_date is None


def test_sorting_by_when_it_was_added(funded_user, library):
    newest_first = filter_receipts(funded_user, ReceiptFilters(sort='-created_at'))
    oldest_first = filter_receipts(funded_user, ReceiptFilters(sort='created_at'))

    assert ids(newest_first) == list(reversed(ids(oldest_first)))
    assert newest_first[0].pk == library['failed'].pk
    assert oldest_first[0].pk == library['delta'].pk


def test_unknown_sort(funded_user):
    with pytest.raises(ValidationError):
        filter_receipts(funded_user, ReceiptFilters(sort='vendor'))


@pytest.mark.parametrize(
    ('status', 'expected'),
    [
        ('processed', {'delta', 'staples', 'old'}),
        ('needs_review', {'review'}),
        ('failed', {'failed'}),
        ('needs_review,failed', {'review', 'failed'}),
        (['needs_review', 'failed'], {'review', 'failed'}),
        (None, {'delta', 'staples', 'old', 'review', 'failed'}),
        ('', {'delta', 'staples', 'old', 'review', 'failed'}),
    ],
)
def test_status(funded_user, library, status, expected):
    result = filter_receipts(funded_user, ReceiptFilters(status=status))

    assert set(ids(result)) == {library[key].pk for key in expected}


def test_unknown_status(funded_user):
    with pytest.raises(ValidationError, match='Unknown status'):
        filter_receipts(funded_user, ReceiptFilters(status='bogus'))


def test_category(funded_user, library):
    travel = library['delta'].category

    by_id = filter_receipts(funded_user, ReceiptFilters(category=travel.pk))
    by_text_id = filter_receipts(funded_user, ReceiptFilters(category=str(travel.pk)))
    none = filter_receipts(funded_user, ReceiptFilters(category='none'))

    assert ids(by_id) == ids(by_text_id) == [library['delta'].pk]
    assert library['delta'].pk not in ids(none) and len(none) == 4


def test_a_bad_category(funded_user):
    with pytest.raises(ValidationError):
        filter_receipts(funded_user, ReceiptFilters(category='abc'))


def test_search_covers_vendor_and_notes_case_insensitively(funded_user, library):
    assert ids(filter_receipts(funded_user, ReceiptFilters(q='STAPLES'))) == [library['staples'].pk]
    assert ids(filter_receipts(funded_user, ReceiptFilters(q='toner'))) == [library['staples'].pk]
    assert ids(filter_receipts(funded_user, ReceiptFilters(q=' conference '))) == [
        library['delta'].pk
    ]
    assert ids(filter_receipts(funded_user, ReceiptFilters(q='nothing like this'))) == []


def test_date_range_is_inclusive_and_applies_to_the_receipt_date(funded_user, library):
    result = filter_receipts(
        funded_user, ReceiptFilters(date_from=date(2026, 10, 3), date_to=date(2026, 10, 9))
    )

    assert set(ids(result)) == {library['delta'].pk, library['staples'].pk}
    only_from = filter_receipts(funded_user, ReceiptFilters(date_from=date(2026, 10, 4)))
    assert ids(only_from) == [library['staples'].pk]
    only_to = filter_receipts(funded_user, ReceiptFilters(date_to=date(2026, 9, 30)))
    assert ids(only_to) == [library['old'].pk]


def test_the_date_filter_is_not_the_added_date(funded_user, library):
    Receipt.objects.filter(pk=library['old'].pk).update(created_at=timezone.now())

    result = filter_receipts(
        funded_user, ReceiptFilters(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
    )

    assert library['old'].pk not in ids(result)


def test_duplicates(funded_user, library):
    library['staples'].possible_duplicate_of = library['delta']
    library['staples'].save()
    library['old'].possible_duplicate_of = library['delta']
    library['old'].duplicate_dismissed = True
    library['old'].save()

    result = filter_receipts(funded_user, ReceiptFilters(duplicates=True))

    assert ids(result) == [library['staples'].pk]


def test_filters_combine(funded_user, library):
    result = filter_receipts(
        funded_user, ReceiptFilters(status='processed', q='s', date_from=date(2026, 10, 1))
    )

    assert set(ids(result)) == {library['delta'].pk, library['staples'].pk}


def test_trash_lists_only_trashed_receipts(funded_user, library):
    assert ids(filter_receipts(funded_user, trashed=True)) == [library['trashed'].pk]


def test_only_the_users_own_receipts(funded_user, regular_user, library, fake_storage):
    create_receipt(regular_user, fake_storage, vendor='Staples')

    assert len(filter_receipts(funded_user, ReceiptFilters(q='Staples'))) == 1


# -- CSV ----------------------------------------------------------------------------------------


def parse(text):
    return list(csv.reader(io.StringIO(text)))


def test_csv_columns_are_the_ones_in_the_spec():
    assert CSV_COLUMNS == (
        'receipt_date',
        'vendor',
        'amount',
        'currency',
        'category',
        'document_type',
        'document_number',
        'payment_method',
        'notes',
        'status',
        'source',
        'added',
        'original_filename',
    )


def test_csv_has_a_header_and_one_row_per_live_receipt(funded_user, library):
    rows = parse(export_csv(funded_user))

    assert rows[0] == list(CSV_COLUMNS)
    assert len(rows) == 1 + 5  # not the trashed one


def test_csv_row_contents(funded_user, library):
    rows = parse(export_csv(funded_user, ReceiptFilters(q='Delta')))

    [row] = rows[1:]
    record = dict(zip(CSV_COLUMNS, row, strict=True))
    assert record['receipt_date'] == '2026-10-03'
    assert record['vendor'] == 'Delta Air Lines'
    assert record['amount'] == '389.00'
    assert record['currency'] == 'USD'
    assert record['category'] == 'Travel'
    assert record['notes'] == 'Conference trip'
    assert record['status'] == 'processed'
    assert record['source'] == 'upload'
    assert record['original_filename'] == 'delta.pdf'
    library['delta'].refresh_from_db()
    assert record['added'] == library['delta'].created_at.isoformat()


def test_csv_keeps_the_current_filters_and_sort(funded_user, library):
    text = export_csv(funded_user, ReceiptFilters(status='processed', sort='receipt_date'))

    vendors = [row[1] for row in parse(text)[1:]]
    assert vendors == ['Old shop', 'Delta Air Lines', 'Staples']


def test_csv_blank_cells_for_missing_values(funded_user, library):
    rows = parse(export_csv(funded_user, ReceiptFilters(status='needs_review')))

    record = dict(zip(CSV_COLUMNS, rows[1], strict=True))
    assert record['receipt_date'] == '' and record['amount'] == '' and record['vendor'] == ''


def test_csv_quotes_commas_quotes_and_newlines(funded_user, fake_storage):
    create_receipt(funded_user, fake_storage, vendor='Smith, "Jones" & Co', notes='line1\nline2')

    rows = parse(export_csv(funded_user))

    assert rows[1][1] == 'Smith, "Jones" & Co'
    assert rows[1][8] == 'line1\nline2'


@pytest.mark.parametrize('text', ['=HYPERLINK("http://evil","x")', '+1+1', '-2+3', '@SUM(A1)'])
def test_csv_neutralizes_formulas(funded_user, fake_storage, text):
    create_receipt(funded_user, fake_storage, vendor=text, notes=text)

    rows = parse(export_csv(funded_user))

    assert rows[1][1] == "'" + text
    assert rows[1][8] == "'" + text


@pytest.mark.parametrize('text', [' =1+1', '  @SUM(A1)', '\n+1', '\u00a0-2', '\t=x', '\r=x'])
def test_csv_neutralizes_formulas_after_leading_whitespace(funded_user, fake_storage, text):
    create_receipt(funded_user, fake_storage, vendor=text)

    rows = parse(export_csv(funded_user))

    assert rows[1][1] == "'" + text


def test_every_csv_cell_goes_through_the_formula_guard(funded_user, fake_storage):
    create_receipt(
        funded_user, fake_storage, currency='=1+1', document_type='@x', document_number=' =y'
    )

    record = dict(zip(CSV_COLUMNS, parse(export_csv(funded_user))[1], strict=True))

    assert record['currency'] == "'=1+1"
    assert record['document_type'] == "'@x"
    assert record['document_number'] == "' =y"


def test_csv_streams_line_by_line(funded_user, library):
    chunks = list(iter_csv(funded_user))

    assert chunks[0].startswith('receipt_date,vendor,amount')
    assert len(chunks) == 1 + 5
    assert all(chunk.endswith('\r\n') for chunk in chunks)


def test_csv_of_an_empty_library_is_just_the_header(funded_user):
    assert parse(export_csv(funded_user)) == [list(CSV_COLUMNS)]


def test_an_unfinished_upload_is_never_listed_counted_or_exported(
    funded_user, fake_storage, library
):
    create_receipt(
        funded_user, fake_storage, uploaded=False, vendor='Half way', original_filename='x'
    )

    assert len(filter_receipts(funded_user)) == 5
    assert len(filter_receipts(funded_user, trashed=True)) == 1
    assert dashboard_counts(funded_user)['total'] == 5
    assert 'Half way' not in export_csv(funded_user)


def test_dashboard_counts(funded_user, fake_storage, library):
    other = create_receipt(
        funded_user, fake_storage, status=ReceiptStatus.WAITING_FOR_CREDITS, original_filename='w'
    )
    create_receipt(
        funded_user, fake_storage, status=ReceiptStatus.PROCESSING, original_filename='p'
    )
    flagged = create_receipt(
        funded_user, fake_storage, status=ReceiptStatus.PROCESSED, possible_duplicate_of=other
    )
    create_receipt(
        funded_user,
        fake_storage,
        status=ReceiptStatus.PROCESSED,
        possible_duplicate_of=other,
        duplicate_dismissed=True,
    )

    counts = dashboard_counts(funded_user)

    assert counts == {
        'total': 5 + 4,
        'needs_review': 1,
        'in_progress': 2,
        'failed': 1,
        'duplicates': 1,
        'trash': 1,
    }
    assert flagged.pk in ids(filter_receipts(funded_user, ReceiptFilters(duplicates=True)))
