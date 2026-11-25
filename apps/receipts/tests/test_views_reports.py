"""The Reports page, its htmx partial and the three downloads (`views/reports.py`,
`services/report_files.py`)."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from ..models import ReceiptStatus
from .factories import create_receipt, create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HX = {'HTTP_HX_REQUEST': 'true'}
THIS_YEAR = date(timezone.localdate().year, 3, 15)


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(**fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        fields.setdefault('currency', 'USD')
        fields.setdefault('receipt_date', THIS_YEAR)
        return create_receipt(funded_user, fake_storage, **fields)

    return _make


@pytest.fixture
def seeded(funded_user, make):
    acme = create_vendor(funded_user, 'Acme', vetted=True)
    newco = create_vendor(funded_user, 'NewCo')
    make(vendor_ref=acme, vendor='Acme', amount='1200.50')
    make(vendor_ref=acme, vendor='Acme', amount='99.50')
    make(vendor_ref=acme, vendor='Acme', amount='40.00', currency='EUR')
    make(vendor_ref=newco, vendor='NewCo', amount='10.00')
    return acme, newco


class TestPage:
    def test_it_needs_a_login(self, client):
        response = client.get(reverse('receipts:reports'))

        assert response.status_code == 302 and '/login' in response['Location']

    def test_it_shows_the_vendor_report_with_a_total_per_currency(self, web, seeded):
        response = web.get(reverse('receipts:reports'))
        content = response.content.decode()

        assert response.status_code == 200
        assert 'By vendor' in content and 'By category' in content
        assert 'Acme' in content and 'NewCo' in content
        assert '1,300.00' in content and '40.00' in content
        assert 'Vetted' in content and 'New vendor' in content
        assert 'amounts in different currencies are not converted' in content
        # One footer line per currency, never a mixed total.
        assert (
            'Total <span class="font-normal text-gray-500 dark:text-gray-400">USD</span>' in content
        )
        assert (
            'Total <span class="font-normal text-gray-500 dark:text-gray-400">EUR</span>' in content
        )

    def test_a_name_opens_the_dashboard_for_that_vendor_and_the_dates(self, web, seeded):
        acme, _ = seeded

        content = web.get(reverse('receipts:reports')).content.decode()

        assert f'href="/receipts/?date_range=this_year&amp;vendor={acme.pk}"' in content

    def test_the_category_report_links_to_the_category_filter(self, web, make, funded_user):
        from ..models import ReceiptCategory

        category = ReceiptCategory.objects.filter(user=funded_user).first()
        if category is None:
            category = ReceiptCategory.objects.create(user=funded_user, name='Misc')
        make(category=category, amount='5.00')
        make(amount='6.00')

        content = web.get(reverse('receipts:reports'), {'group': 'category'}).content.decode()

        assert category.name in content and 'Uncategorized' in content
        assert f'category={category.pk}' in content and 'category=none' in content
        assert 'name="group" value="category"' in content

    def test_the_downloads_carry_the_filters(self, web, seeded):
        content = web.get(
            reverse('receipts:reports'), {'report_form': '1', 'date_range': 'any', 'vetted': '1'}
        ).content.decode()

        for extension in ('xlsx', 'csv', 'pdf'):
            assert (
                f'href="/receipts/reports/export.{extension}'
                '?group=vendor&amp;date_range=any&amp;processed=0&amp;vetted=1"'
            ) in content

    def test_the_filters_show_what_was_chosen(self, web, seeded):
        content = web.get(
            reverse('receipts:reports'), {'report_form': '1', 'processed': '1'}
        ).content.decode()

        assert 'name="processed" value="1" id="report-processed" checked' in content
        assert 'id="report-vetted"' in content and 'id="report-vetted" checked' not in content
        assert 'Receipts dated' in content and 'processed only' in content

    def test_nothing_in_the_period_is_an_empty_state(self, web):
        content = web.get(reverse('receipts:reports')).content.decode()

        assert 'No receipts in this period' in content
        assert '<table' not in content

    def test_the_last_receipt_follows_the_account_date_format(self, web, funded_user, seeded):
        funded_user.date_format = 'dmy'
        funded_user.save()

        content = web.get(reverse('receipts:reports')).content.decode()

        assert f'15/03/{THIS_YEAR.year}' in content

    def test_another_users_receipts_are_not_in_it(self, web, other_user, fake_storage):
        create_receipt(
            other_user,
            fake_storage,
            vendor='Secret Co',
            amount='999.00',
            currency='USD',
            receipt_date=THIS_YEAR,
            status=ReceiptStatus.PROCESSED,
        )

        content = web.get(reverse('receipts:reports')).content.decode()

        assert 'Secret Co' not in content and '999' not in content

    def test_the_dashboard_links_to_it(self, web):
        content = web.get(reverse('receipts:dashboard')).content.decode()

        assert 'href="/receipts/reports/"' in content
        assert content.index('Reports') < content.index('Vendors')

    def test_the_page_is_not_rate_limited(self, web, settings, seeded):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'RECEIPTS_REPORT_USER': (1, 3600)}

        assert all(web.get(reverse('receipts:reports')).status_code == 200 for _ in range(3))


class TestPartial:
    def test_opened_directly_it_redirects_to_the_page(self, web):
        response = web.get(reverse('receipts:report-list'), {'group': 'category'})

        assert response.status_code == 302
        assert response['Location'] == '/receipts/reports/?group=category'

    def test_htmx_gets_the_content_and_the_page_url_is_pushed(self, web, seeded):
        response = web.get(
            reverse('receipts:report-list'),
            {'report_form': '1', 'date_range': 'this_year', 'vetted': '1'},
            **HX,
        )
        content = response.content.decode()

        assert response.status_code == 200
        assert response['HX-Push-Url'] == '/receipts/reports/?processed=0&vetted=1'
        assert 'Acme' in content and 'NewCo' not in content  # vetted only
        # The tabs and the download links are sent again, out of band.
        assert 'id="report-tabs" hx-swap-oob="true"' in content
        assert 'id="report-downloads" hx-swap-oob="true"' in content
        assert '<html' not in content

    def test_switching_the_tab_keeps_the_filters(self, web, seeded):
        response = web.get(
            reverse('receipts:report-list'), {'group': 'category', 'date_range': 'any'}, **HX
        )

        assert response['HX-Push-Url'] == '/receipts/reports/?group=category&date_range=any'
        assert 'name="group" value="category"' in response.content.decode()


def csv_rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.content.decode())))


class TestCsv:
    def test_it_lists_the_rows_and_a_total_per_currency(self, web, seeded):
        response = web.get(reverse('receipts:report-csv'))
        rows = csv_rows(response)

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv; charset=utf-8'
        assert response['Cache-Control'] == 'private, no-store'
        today = timezone.localdate().isoformat()
        assert response['Content-Disposition'] == (
            f'attachment; filename="receipts-by-vendor-{today}.csv"'
        )
        assert rows[0] == [
            'Vendor', 'Vetted', 'Currency', 'Receipts', 'Total', 'Share (%)', 'Average',
            'Last receipt',
        ]  # fmt: skip
        assert rows[1] == [
            'Acme',
            'Yes',
            'EUR',
            '1',
            '40.00',
            '100.0',
            '40.00',
            THIS_YEAR.isoformat(),
        ]
        assert rows[2][:5] == ['Acme', 'Yes', 'USD', '2', '1300.00']
        assert rows[3][:4] == ['NewCo', 'No', 'USD', '1']
        assert rows[-2][:5] == ['Total', '', 'EUR', '1', '40.00']
        assert rows[-1][:5] == ['Total', '', 'USD', '3', '1310.00']

    def test_a_category_report_has_no_vetted_column(self, web, seeded):
        rows = csv_rows(web.get(reverse('receipts:report-csv'), {'group': 'category'}))

        assert rows[0][0] == 'Category' and 'Vetted' not in rows[0]
        assert rows[1][0] == 'Uncategorized'

    def test_the_filters_apply(self, web, seeded):
        rows = csv_rows(
            web.get(reverse('receipts:report-csv'), {'date_range': 'any', 'vetted': '1'})
        )

        assert {row[0] for row in rows[1:]} == {'Acme', 'Total'}

    def test_a_formula_in_a_name_is_neutralised(self, web, funded_user, make):
        evil = create_vendor(funded_user, '=HYPERLINK("http://evil","x")', vetted=True)
        make(vendor_ref=evil, vendor=evil.name, amount='1.00')

        rows = csv_rows(web.get(reverse('receipts:report-csv')))

        assert rows[1][0] == '\'=HYPERLINK("http://evil","x")'


class TestXlsx:
    def workbook(self, response):
        return load_workbook(io.BytesIO(b''.join(response.streaming_content)))

    def test_it_is_a_workbook_with_real_numbers_and_dates(self, web, seeded):
        response = web.get(reverse('receipts:report-xlsx'))
        book = self.workbook(response)

        assert response.status_code == 200
        assert response['Content-Type'] == (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        assert response['Cache-Control'] == 'private, no-store'
        assert 'receipts-by-vendor-' in response['Content-Disposition']
        assert response['Content-Disposition'].endswith('.xlsx"')
        assert book.sheetnames == ['By vendor', 'Filters']
        rows = list(book['By vendor'].iter_rows(values_only=True))
        assert rows[0] == (
            'Vendor', 'Vetted', 'Currency', 'Receipts', 'Total', 'Share (%)', 'Average',
            'Last receipt',
        )  # fmt: skip
        eur = rows[1]
        assert eur[0:4] == ('Acme', 'Yes', 'EUR', 1)
        assert eur[4] == 40 and isinstance(eur[4], int | float | Decimal)
        assert eur[5] == 1  # 100%, a real fraction
        assert eur[7].date() == THIS_YEAR
        totals = [row for row in rows if row[0] == 'Total']
        assert [(t[2], t[3], t[4]) for t in totals] == [('EUR', 1, 40), ('USD', 3, 1310)]
        about = [row[0] for row in book['Filters'].iter_rows(values_only=True)]
        assert about[0] == 'Receipts by vendor' and 'processed only' in about[1]

    def test_a_text_that_looks_like_a_formula_is_stored_as_text(self, web, funded_user, make):
        evil = create_vendor(funded_user, '=1+1', vetted=True)
        make(vendor_ref=evil, vendor=evil.name, amount='1.00')

        book = self.workbook(web.get(reverse('receipts:report-xlsx')))
        cell = book['By vendor']['A2']

        assert cell.value == '=1+1' and cell.data_type == 's'
        assert cell.quotePrefix is True

    def test_a_category_report_has_its_own_sheet(self, web, seeded):
        book = self.workbook(web.get(reverse('receipts:report-xlsx'), {'group': 'category'}))

        assert book.sheetnames[0] == 'By category'
        assert [c.value for c in book['By category'][1]][:3] == ['Category', 'Currency', 'Receipts']


class TestPdf:
    def test_it_is_a_pdf(self, web, seeded):
        response = web.get(reverse('receipts:report-pdf'))
        body = b''.join(response.streaming_content)

        assert response.status_code == 200
        assert response['Content-Type'] == 'application/pdf'
        assert response['Cache-Control'] == 'private, no-store'
        assert 'receipts-by-vendor-' in response['Content-Disposition']
        assert body.startswith(b'%PDF') and b'%%EOF' in body[-32:]

    def test_markup_and_odd_characters_in_names_do_not_break_it(self, web, funded_user, make):
        for name in ('<b>Bold & Co</b>', '</para><para>', 'Café 北京 \U0001f600'):
            vendor = create_vendor(funded_user, name, vetted=True)
            make(vendor_ref=vendor, vendor=name, amount='1.00')

        response = web.get(reverse('receipts:report-pdf'))

        assert response.status_code == 200
        assert b''.join(response.streaming_content).startswith(b'%PDF')

    def test_an_empty_report_is_still_a_valid_pdf(self, web):
        response = web.get(reverse('receipts:report-pdf'))

        assert b''.join(response.streaming_content).startswith(b'%PDF')

    def test_a_category_pdf(self, web, seeded):
        response = web.get(reverse('receipts:report-pdf'), {'group': 'category'})

        assert 'by-category' in response['Content-Disposition']


class TestDownloadsInGeneral:
    @pytest.mark.parametrize('name', ['report-csv', 'report-xlsx', 'report-pdf'])
    def test_they_need_a_login(self, client, name):
        assert client.get(reverse(f'receipts:{name}')).status_code == 302

    @pytest.mark.parametrize('name', ['report-csv', 'report-xlsx', 'report-pdf'])
    def test_they_share_one_rate_limit(self, web, settings, name):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'RECEIPTS_REPORT_USER': (2, 3600)}
        assert web.get(reverse('receipts:report-csv')).status_code == 200
        assert web.get(reverse('receipts:report-xlsx')).status_code == 200

        limited = web.get(reverse(f'receipts:{name}'))

        assert limited.status_code == 429 and limited['Retry-After']

    def test_the_file_name_is_dated_in_the_users_time_zone(self, web, funded_user, monkeypatch):
        monkeypatch.setattr(
            'apps.receipts.views.reports.user_today', lambda user: date(2026, 11, 25)
        )

        response = web.get(reverse('receipts:report-csv'))

        assert 'receipts-by-vendor-2026-11-25.csv' in response['Content-Disposition']
