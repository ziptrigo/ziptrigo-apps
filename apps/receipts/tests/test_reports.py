"""The report service (`services/reports.py`) and the parsing of its filters (`forms/reports.py`):
grouping, currencies, what is counted, the filters and the query count."""

from datetime import date
from decimal import Decimal

import pytest
from django.http import QueryDict
from django.test import RequestFactory
from django.utils import timezone

from apps.core.datetime_format import DisplayFormats

from ..forms import parse_report
from ..models import ReceiptCategory, ReceiptStatus
from ..services.reports import (
    NO_CATEGORY_NAME,
    NO_VENDOR_NAME,
    ReportFilters,
    build_report,
    describe_filters,
)
from .factories import create_receipt, create_vendor

MDY = DisplayFormats('mdy', '12h')

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(**fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        fields.setdefault('currency', 'USD')
        return create_receipt(funded_user, fake_storage, **fields)

    return _make


def by_vendor(report):
    return {(row.name, row.currency): row for row in report.rows}


class TestGrouping:
    def test_a_vendor_report_has_one_row_per_vendor_and_currency(self, funded_user, make):
        acme = create_vendor(funded_user, 'Acme', vetted=True)
        make(vendor_ref=acme, vendor='Acme', amount='10.00', receipt_date=date(2026, 1, 5))
        make(vendor_ref=acme, vendor='Acme', amount='5.50', receipt_date=date(2026, 3, 1))
        make(vendor_ref=acme, vendor='Acme', amount='7.00', currency='EUR')

        report = build_report(funded_user, ReportFilters())

        rows = by_vendor(report)
        assert set(rows) == {('Acme', 'USD'), ('Acme', 'EUR')}
        usd = rows[('Acme', 'USD')]
        assert (usd.count, usd.total, usd.average) == (2, Decimal('15.50'), Decimal('7.75'))
        assert usd.last_date == date(2026, 3, 1)
        assert usd.vetted is True and usd.key == acme.pk
        assert report.group_count == 1

    def test_currencies_are_never_added_together(self, funded_user, make):
        make(amount='100.00', currency='USD')
        make(amount='80.00', currency='EUR')

        report = build_report(funded_user, ReportFilters())

        assert [(t.currency, t.count, t.total) for t in report.totals] == [
            ('EUR', 1, Decimal('80.00')),
            ('USD', 1, Decimal('100.00')),
        ]
        assert report.receipt_count == 2

    def test_the_share_is_of_the_rows_own_currency(self, funded_user, make):
        a = create_vendor(funded_user, 'A')
        b = create_vendor(funded_user, 'B')
        make(vendor_ref=a, vendor='A', amount='75.00')
        make(vendor_ref=b, vendor='B', amount='25.00')
        make(vendor_ref=a, vendor='A', amount='10.00', currency='EUR')

        rows = by_vendor(build_report(funded_user, ReportFilters()))

        assert rows[('A', 'USD')].share == Decimal('75.0')
        assert rows[('B', 'USD')].share == Decimal('25.0')
        assert rows[('A', 'EUR')].share == Decimal('100.0')

    def test_rows_are_sorted_by_currency_then_total_descending(self, funded_user, make):
        small = create_vendor(funded_user, 'Small')
        big = create_vendor(funded_user, 'Big')
        make(vendor_ref=small, vendor='Small', amount='1.00')
        make(vendor_ref=big, vendor='Big', amount='9.00')
        make(vendor_ref=small, vendor='Small', amount='500.00', currency='EUR')
        make(vendor_ref=big, vendor='Big', amount='2.00', currency='')

        report = build_report(funded_user, ReportFilters())

        assert [(r.name, r.currency) for r in report.rows] == [
            ('Small', 'EUR'),
            ('Big', 'USD'),
            ('Small', 'USD'),
            ('Big', ''),  # no currency last
        ]
        assert report.rows[-1].currency_label == 'no currency'

    def test_a_receipt_with_no_vendor_is_its_own_row(self, funded_user, make):
        make(amount='4.00', vendor='')

        row = build_report(funded_user, ReportFilters()).rows[0]

        assert (row.name, row.key, row.vetted) == (NO_VENDOR_NAME, None, None)

    def test_a_category_report_groups_by_category(self, funded_user, make):
        travel = ReceiptCategory.objects.create(user=funded_user, name='Trips')
        make(category=travel, amount='30.00')
        make(category=travel, amount='20.00')
        make(amount='1.00')

        report = build_report(funded_user, ReportFilters(group='category'))

        rows = by_vendor(report)
        assert rows[('Trips', 'USD')].count == 2
        assert rows[('Trips', 'USD')].vetted is None
        assert rows[(NO_CATEGORY_NAME, 'USD')].key is None

    def test_an_average_is_over_the_receipts_that_have_an_amount(self, funded_user, make):
        make(amount='10.00', status=ReceiptStatus.PROCESSED)
        make(amount=None, status=ReceiptStatus.NEEDS_REVIEW)

        report = build_report(funded_user, ReportFilters(processed_only=False))

        assert report.rows[0].count == 2
        assert report.rows[0].average == Decimal('10.00')
        assert report.totals[0].average == Decimal('10.00')

    def test_an_unknown_group_is_refused(self, funded_user):
        with pytest.raises(ValueError):
            build_report(funded_user, ReportFilters(group='color'))

    def test_nothing_gives_an_empty_report(self, funded_user):
        report = build_report(funded_user, ReportFilters())

        assert report.is_empty and report.totals == [] and report.receipt_count == 0


class TestWhatIsCounted:
    def test_by_default_only_processed_receipts(self, funded_user, make):
        make(amount='1.00', status=ReceiptStatus.PROCESSED)
        for status in (
            ReceiptStatus.NEEDS_REVIEW,
            ReceiptStatus.FAILED,
            ReceiptStatus.RECEIVED,
            ReceiptStatus.PROCESSING,
            ReceiptStatus.WAITING_FOR_CREDITS,
        ):
            make(amount='100.00', status=status)

        assert build_report(funded_user, ReportFilters()).receipt_count == 1

    def test_without_processed_only_needs_review_counts_but_never_failed_or_in_progress(
        self, funded_user, make
    ):
        make(amount='1.00', status=ReceiptStatus.PROCESSED)
        make(amount='2.00', status=ReceiptStatus.NEEDS_REVIEW)
        for status in (
            ReceiptStatus.FAILED,
            ReceiptStatus.RECEIVED,
            ReceiptStatus.PROCESSING,
            ReceiptStatus.WAITING_FOR_CREDITS,
        ):
            make(amount='100.00', status=status)

        report = build_report(funded_user, ReportFilters(processed_only=False))

        assert report.totals[0].total == Decimal('3.00')

    def test_the_trash_an_unfinished_upload_and_other_users_are_never_counted(
        self, funded_user, make, other_user, fake_storage
    ):
        make(amount='1.00')
        make(amount='100.00', deleted_at=timezone.now())
        make(amount='100.00', uploaded=False)
        create_receipt(other_user, fake_storage, amount='100.00', status=ReceiptStatus.PROCESSED)

        report = build_report(funded_user, ReportFilters(processed_only=False))

        assert report.receipt_count == 1
        assert report.totals[0].total == Decimal('1.00')

    def test_vetted_only_leaves_out_new_vendors_and_receipts_without_one(self, funded_user, make):
        vetted = create_vendor(funded_user, 'Vetted', vetted=True)
        new = create_vendor(funded_user, 'New')
        make(vendor_ref=vetted, vendor='Vetted', amount='1.00')
        make(vendor_ref=new, vendor='New', amount='2.00')
        make(amount='4.00', vendor='')

        report = build_report(funded_user, ReportFilters(vetted_only=True))

        assert [row.name for row in report.rows] == ['Vetted']

    def test_the_dates_bound_the_receipt_date_inclusively(self, funded_user, make):
        make(amount='1.00', receipt_date=date(2026, 1, 1))
        make(amount='2.00', receipt_date=date(2026, 1, 31))
        make(amount='4.00', receipt_date=date(2026, 2, 1))
        make(amount='8.00', receipt_date=None)

        window = ReportFilters(date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
        assert build_report(funded_user, window).totals[0].total == Decimal('3.00')
        from_only = ReportFilters(date_from=date(2026, 1, 31))
        assert build_report(funded_user, from_only).totals[0].total == Decimal('6.00')
        until_only = ReportFilters(date_to=date(2026, 1, 1))
        assert build_report(funded_user, until_only).totals[0].total == Decimal('1.00')
        # With no bound a receipt without a date is in the report.
        assert build_report(funded_user, ReportFilters()).totals[0].total == Decimal('15.00')

    def test_it_is_one_query_whatever_the_number_of_vendors(
        self, funded_user, make, django_assert_num_queries
    ):
        for index in range(12):
            vendor = create_vendor(funded_user, f'Vendor {index}', vetted=index % 2 == 0)
            make(vendor_ref=vendor, vendor=vendor.name, amount='3.00')

        with django_assert_num_queries(1):
            report = build_report(funded_user, ReportFilters())

        assert len(report.rows) == 12


class TestDescribe:
    def test_the_sentence_says_what_is_covered(self):
        filters = ReportFilters(date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))

        text = describe_filters(filters, MDY)

        assert text == (
            'Receipts dated 01/01/2026 to 12/31/2026, processed only. '
            'Receipts in the Trash are never counted.'
        )

    def test_every_variant(self):
        assert describe_filters(ReportFilters(processed_only=False, vetted_only=True)).startswith(
            'Receipts of all dates, processed and needs review, vetted vendors only.'
        )
        assert 'from 01/01/2026' in describe_filters(ReportFilters(date_from=date(2026, 1, 1)), MDY)
        assert 'until 01/01/2026' in describe_filters(ReportFilters(date_to=date(2026, 1, 1)), MDY)


def parse(query: str):
    request = RequestFactory().get('/receipts/reports/?' + query)
    return parse_report(request)


class TestParsing:
    def test_the_defaults_are_this_year_processed_only_every_vendor(self):
        parsed = parse('')
        today = timezone.localdate()

        assert parsed.date_range == 'this_year'
        assert (parsed.filters.date_from, parsed.filters.date_to) == (
            date(today.year, 1, 1),
            date(today.year, 12, 31),
        )
        assert parsed.filters.processed_only and not parsed.filters.vetted_only
        assert parsed.group == 'vendor'
        assert parsed.params == {}

    def test_all_dates_is_spelled_out(self):
        parsed = parse('date_range=any')

        assert (parsed.filters.date_from, parsed.filters.date_to) == (None, None)
        assert parsed.params == {'date_range': 'any'}

    def test_a_form_that_unticked_the_boxes_means_off(self):
        parsed = parse('report_form=1&date_range=this_month')

        assert not parsed.filters.processed_only and not parsed.filters.vetted_only
        assert parsed.params == {'date_range': 'this_month', 'processed': '0'}

    def test_a_form_with_the_boxes_ticked(self):
        parsed = parse('report_form=1&processed=1&vetted=1')

        assert parsed.filters.processed_only and parsed.filters.vetted_only
        assert parsed.params == {'vetted': '1'}

    def test_the_canonical_params_read_back_the_same(self):
        parsed = parse('report_form=1&group=category&date_range=any&vetted=1')

        again = parse(parsed.query())

        assert again.filters == parsed.filters and again.params == parsed.params

    def test_dates_without_a_range_are_custom_and_swapped_when_reversed(self):
        parsed = parse('date_from=2026-03-01&date_to=2026-01-01')

        assert parsed.date_range == 'custom'
        assert (parsed.filters.date_from, parsed.filters.date_to) == (
            date(2026, 1, 1),
            date(2026, 3, 1),
        )
        assert parsed.params == {'date_from': '2026-01-01', 'date_to': '2026-03-01'}

    def test_custom_with_no_dates_is_every_date(self):
        parsed = parse('date_range=custom')

        assert parsed.date_range == 'custom'
        assert parsed.filters.date_from is None and parsed.filters.date_to is None

    def test_a_preset_ignores_stray_dates(self):
        parsed = parse('date_range=this_month&date_from=2000-01-01')

        assert parsed.filters.date_from == timezone.localdate().replace(day=1)

    def test_bad_values_fall_back_to_the_defaults(self):
        parsed = parse('group=color&date_range=lastweek&date_from=nonsense')

        assert parsed.group == 'vendor' and parsed.date_range == 'this_year'

    def test_the_dashboard_query_carries_the_dates(self):
        assert parse('').dashboard_query(vendor='3') == 'date_range=this_year&vendor=3'
        assert parse('date_range=any').dashboard_query(category='none') == 'category=none'
        custom = parse('date_from=2026-01-01&date_to=2026-02-01').dashboard_query(vendor='3')
        assert custom == 'date_from=2026-01-01&date_to=2026-02-01&vendor=3'

    def test_a_data_argument_is_read_instead_of_the_request(self):
        request = RequestFactory().get('/x/?group=category')

        parsed = parse_report(request, QueryDict('date_range=any'))

        assert parsed.group == 'vendor' and parsed.date_range == 'any'
