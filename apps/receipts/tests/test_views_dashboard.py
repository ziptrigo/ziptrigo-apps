"""The dashboard, its list partial and the CSV export (`views/dashboard.py`)."""

import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from ..forms.filters import resolve_date_range
from ..models import Receipt, ReceiptCategory, ReceiptsAccount, ReceiptStatus
from ..services.accounts import get_account
from ..services.filters import CSV_COLUMNS
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(**fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        return create_receipt(funded_user, fake_storage, **fields)

    return _make


def input_tag(content: str, name: str) -> str:
    """The `<input>` tag named `name` in a page."""
    match = re.search(rf'<input [^>]*name="{name}"[^>]*>', content)
    assert match, name
    return match[0]


def titles(response) -> list[str]:
    """The receipt ids in the order the page lists them."""
    return re.findall(r'<li id="receipt-([0-9a-f-]{36})"', response.content.decode())


class TestAccess:
    @pytest.mark.parametrize('name', ['dashboard', 'list', 'export', 'upload', 'trash', 'settings'])
    def test_every_page_requires_login(self, client, name):
        response = client.get(reverse(f'receipts:{name}'))

        assert response.status_code == 302
        assert response['Location'].startswith(reverse('accounts:login'))

    def test_the_first_visit_creates_the_account_and_shows_the_address(self, web, funded_user):
        response = web.get(reverse('receipts:dashboard'))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'receipts+' in content and '@ziptrigo.com' in content
        assert 'Email receipts to' in content

    def test_only_the_owners_receipts_are_listed(self, web, make, other_user, fake_storage):
        mine = make(vendor='Mine')
        create_receipt(other_user, fake_storage, vendor='Theirs', status=ReceiptStatus.PROCESSED)

        content = web.get(reverse('receipts:dashboard')).content.decode()

        assert 'Mine' in content and 'Theirs' not in content
        assert str(mine.pk) in content

    def test_the_nav_links_to_receipts(self, web):
        content = web.get(reverse('core:home')).content.decode()

        assert f'href="{reverse("receipts:dashboard")}"' in content
        assert 'fa-receipt' in content


class TestList:
    def test_rows_show_what_the_mock_shows(self, web, make):
        staples = make(
            vendor='Staples',
            amount=Decimal('1042.10'),
            currency='USD',
            receipt_date=date(2026, 10, 9),
            category=None,
            source='email',
            document_type='receipt',
            payment_method='Visa ••4242',
        )

        content = web.get(reverse('receipts:dashboard')).content.decode()

        assert 'Staples' in content
        assert '2026-10-09' in content
        assert 'USD 1,042.10' in content
        assert 'Processed' in content
        assert 'email' in content and 'receipt' in content and 'Visa ••4242' in content
        assert f'added {staples.created_at:%b} {staples.created_at.day}' in content

    def test_rows_without_data_show_what_is_wrong(self, web, make):
        make(status=ReceiptStatus.NEEDS_REVIEW, original_filename='IMG_2041.HEIC')
        make(
            status=ReceiptStatus.FAILED,
            failure_reason='multiple_receipts',
            original_filename='scan.pdf',
        )
        make(status=ReceiptStatus.PROCESSING, original_filename='invoice-oct.pdf')
        make(status=ReceiptStatus.WAITING_FOR_CREDITS, original_filename='later.jpg')

        content = web.get(reverse('receipts:dashboard')).content.decode()

        assert 'IMG_2041.HEIC' in content and 'vendor, amount, date missing' in content
        assert (
            'more than one receipt' in content and 'Upload each receipt as its own file' in content
        )
        assert 'reading the receipt' in content
        assert 'waiting for credits' in content
        for label in ('Needs review', 'Failed', 'Processing', 'Waiting for credits'):
            assert label in content

    def test_a_possible_duplicate_is_flagged_until_dismissed(self, web, make):
        original = make(vendor='Delta')
        dup = make(vendor='Delta', possible_duplicate_of=original)

        assert 'possible duplicate' in web.get(reverse('receipts:dashboard')).content.decode()

        Receipt.objects.filter(pk=dup.pk).update(duplicate_dismissed=True)
        assert 'possible duplicate' not in web.get(reverse('receipts:dashboard')).content.decode()

    def test_a_vendorless_email_body_is_titled_by_what_it_is(self, web, make):
        make(vendor='', original_filename='', source='email', status=ReceiptStatus.NEEDS_REVIEW)

        assert 'Email body' in web.get(reverse('receipts:dashboard')).content.decode()

    def test_an_unfinished_upload_is_not_listed(self, web, make):
        make(vendor='Half way', uploaded=False)

        assert 'Half way' not in web.get(reverse('receipts:dashboard')).content.decode()

    def test_trashed_receipts_are_not_listed(self, web, make):
        make(vendor='Gone', deleted_at=timezone.now())

        assert 'Gone' not in web.get(reverse('receipts:dashboard')).content.decode()

    def test_empty_states(self, web, make):
        content = web.get(reverse('receipts:dashboard')).content.decode()
        assert 'No receipts yet.' in content

        filtered = web.get(reverse('receipts:dashboard'), {'q': 'nothing like this'})
        assert 'No receipts found.' in filtered.content.decode()

    def test_the_default_order_is_newest_receipt_date_first_with_undated_on_top(self, web, make):
        old = make(vendor='Old', receipt_date=date(2026, 1, 1))
        new = make(vendor='New', receipt_date=date(2026, 6, 1))
        undated = make(vendor='', status=ReceiptStatus.NEEDS_REVIEW)

        ids = titles(web.get(reverse('receipts:dashboard')))

        assert ids == [str(undated.pk), str(new.pk), str(old.pk)]

    def test_sorting_by_added(self, web, make):
        first = make(vendor='First')
        second = make(vendor='Second')
        Receipt.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=2))

        newest = titles(web.get(reverse('receipts:dashboard'), {'sort': '-created_at'}))
        oldest = titles(web.get(reverse('receipts:dashboard'), {'sort': 'created_at'}))

        assert newest == [str(second.pk), str(first.pk)]
        assert oldest == [str(first.pk), str(second.pk)]

    def test_the_active_sort_link_flips_direction(self, web, make):
        make()

        default = web.get(reverse('receipts:dashboard')).content.decode()
        assert 'hx-get="/receipts/list/?sort=receipt_date"' in default
        flipped = web.get(reverse('receipts:dashboard'), {'sort': 'receipt_date'}).content.decode()
        assert 'hx-get="/receipts/list/"' in flipped  # back to the default (newest first)

    def test_the_sort_links_say_the_current_order_to_screen_readers(self, web, make):
        make()

        default = web.get(reverse('receipts:dashboard')).content.decode()
        assert 'aria-sort' not in default  # only valid on column headers, which these are not
        assert 'Receipt date<span class="sr-only"> (sorted descending)</span>' in default
        assert '(sorted ascending)' not in default and 'Added<span' not in default

        ascending = web.get(reverse('receipts:dashboard'), {'sort': 'created_at'}).content.decode()
        assert 'Added<span class="sr-only"> (sorted ascending)</span>' in ascending
        assert 'Receipt date<span' not in ascending

    def test_25_per_page(self, web, make):
        for index in range(30):
            make(
                vendor=f'Vendor {index:02d}', receipt_date=date(2026, 1, 1) + timedelta(days=index)
            )

        first = web.get(reverse('receipts:dashboard'))
        second = web.get(reverse('receipts:dashboard'), {'page': 2})

        assert len(titles(first)) == 25 and len(titles(second)) == 5
        assert 'Page 1 of 2' in first.content.decode()
        assert 'Page 2 of 2' in second.content.decode()
        assert 'Previous' in second.content.decode()

    def test_a_bad_page_number_falls_back_to_a_real_page(self, web, make):
        make()

        assert web.get(reverse('receipts:dashboard'), {'page': 'abc'}).status_code == 200
        assert web.get(reverse('receipts:dashboard'), {'page': 99}).status_code == 200


class TestFilters:
    @pytest.fixture
    def library(self, make, funded_user):
        get_account(funded_user)  # seeds the default categories
        travel = ReceiptCategory.objects.get(user=funded_user, name='Travel')
        return {
            'Delta': make(
                vendor='Delta',
                category=travel,
                receipt_date=date(2026, 10, 3),
                notes='conference trip',
                possible_duplicate_of=make(vendor='Delta original'),
            ),
            'Staples': make(
                vendor='Staples', receipt_date=date(2026, 10, 9), amount=Decimal('42.10')
            ),
            'Review me': make(status=ReceiptStatus.NEEDS_REVIEW, vendor='Review me'),
            'Broken': make(status=ReceiptStatus.FAILED, vendor='Broken'),
            'Busy': make(status=ReceiptStatus.PROCESSING, vendor='Busy'),
            'Waiting': make(status=ReceiptStatus.WAITING_FOR_CREDITS, vendor='Waiting'),
        }

    @pytest.mark.parametrize(
        ('params', 'expected'),
        [
            ({'status': 'needs_review'}, {'Review me'}),
            ({'status': 'failed'}, {'Broken'}),
            ({'status': 'processing'}, {'Busy', 'Waiting'}),
            ({'status': 'duplicates'}, {'Delta'}),
            ({'q': 'conference'}, {'Delta'}),
            ({'q': 'staples'}, {'Staples'}),
            ({'q': '42.1'}, {'Staples'}),
            ({'date_from': '2026-10-05'}, {'Staples'}),
            ({'date_to': '2026-10-03'}, {'Delta'}),
        ],
    )
    def test_filters(self, web, library, params, expected):
        ids = set(titles(web.get(reverse('receipts:dashboard'), params)))

        assert ids == {str(library[name].pk) for name in expected}

    def test_by_category_and_uncategorized(self, web, library):
        travel = library['Delta'].category

        by_category = titles(web.get(reverse('receipts:dashboard'), {'category': travel.pk}))
        uncategorized = titles(web.get(reverse('receipts:dashboard'), {'category': 'none'}))

        assert by_category == [str(library['Delta'].pk)]
        assert str(library['Delta'].pk) not in uncategorized and len(uncategorized) >= 5

    @pytest.mark.parametrize(
        ('params', 'expected'),
        [
            ({'date_range': 'last_15_days'}, {'Delta', 'Staples'}),
            ({'date_range': 'last_30_days'}, {'Delta', 'Staples', 'September'}),
            ({'date_range': 'this_month'}, {'Delta', 'Staples'}),
            ({'date_range': 'this_quarter'}, {'Delta', 'Staples'}),
            ({'date_range': 'this_year'}, {'Delta', 'Staples', 'September', 'March'}),
            # A preset wins over dates that are still in the form from an earlier custom range.
            ({'date_range': 'this_month', 'date_from': '2026-10-05'}, {'Delta', 'Staples'}),
            ({'date_range': 'custom', 'date_from': '2026-10-05'}, {'Staples'}),
            ({'date_range': 'custom', 'date_to': '2026-09-30'}, {'September', 'March', 'Old'}),
            # "Any date" wins over dates left in the form by a custom range (E2E run #152).
            (
                {'date_range': 'any', 'date_from': '2026-10-05', 'date_to': '2026-10-06'},
                {'Delta', 'Staples', 'September', 'March', 'Old'},
            ),
        ],
    )
    def test_date_ranges(self, web, library, make, monkeypatch, params, expected):
        monkeypatch.setattr(timezone, 'localdate', lambda: date(2026, 10, 12))
        library['September'] = make(vendor='September', receipt_date=date(2026, 9, 20))
        library['March'] = make(vendor='March', receipt_date=date(2026, 3, 1))
        library['Old'] = make(vendor='Old', receipt_date=date(2025, 12, 31))

        dated = {name for name, r in library.items() if r.receipt_date}
        ids = set(titles(web.get(reverse('receipts:dashboard'), params))) & {
            str(library[name].pk) for name in dated
        }

        assert ids == {str(library[name].pk) for name in expected}

    @pytest.mark.parametrize(
        ('params', 'pushed'),
        [
            ({'date_range': 'this_month'}, '/receipts/?date_range=this_month'),
            (
                {'date_range': 'this_month', 'date_from': '2026-10-05'},
                '/receipts/?date_range=this_month',
            ),
            (
                {'date_range': 'custom', 'date_from': '2026-10-05'},
                '/receipts/?date_from=2026-10-05',
            ),
            ({'date_range': 'custom'}, '/receipts/'),
            ({'date_range': '', 'date_from': '2026-10-05'}, '/receipts/?date_from=2026-10-05'),
            # "Any date" picked after a custom range: the dates still arrive with it (E2E #152).
            (
                {'date_range': 'any', 'date_from': '2026-10-05', 'date_to': '2026-10-31'},
                '/receipts/',
            ),
            ({'date_range': 'bogus'}, '/receipts/'),
        ],
    )
    def test_the_date_range_in_the_pushed_url(self, web, make, params, pushed):
        make()

        assert web.get(reverse('receipts:list'), params, **HX)['HX-Push-Url'] == pushed

    def test_a_preset_shows_the_dropdown_with_the_dates_hidden(self, web, make):
        make()

        content = web.get(
            reverse('receipts:dashboard'), {'date_range': 'last_30_days'}
        ).content.decode()

        assert '<option value="last_30_days" selected>Last 30 days</option>' in content
        date_from = input_tag(content, 'date_from')
        assert ' disabled' in date_from

    def test_dates_alone_open_as_a_custom_range(self, web, make):
        make()

        content = web.get(
            reverse('receipts:dashboard'), {'date_from': '2026-10-05'}
        ).content.decode()

        assert '<option value="custom" selected>Custom</option>' in content
        date_from = input_tag(content, 'date_from')
        assert 'value="2026-10-05"' in date_from and ' disabled' not in date_from

    def test_the_search_box_turns_off_the_browsers_suggestions(self, web, make):
        make()

        content = web.get(reverse('receipts:dashboard')).content.decode()

        search = input_tag(content, 'q')
        assert 'autocomplete="off"' in search

    @pytest.mark.parametrize(
        'params',
        [
            {'status': 'bogus'},
            {'date_range': 'last_century'},
            {'sort': 'vendor'},
            {'date_from': 'yesterday'},
            {'category': 'abc'},
            {'q': 'x' * 500},
        ],
    )
    def test_bad_values_are_ignored_not_errors(self, web, library, params):
        response = web.get(reverse('receipts:dashboard'), params)

        assert response.status_code == 200

    def test_the_tabs_show_counts_and_the_active_one(self, web, library):
        response = web.get(reverse('receipts:dashboard'), {'status': 'failed'})
        content = response.content.decode()

        assert 'aria-current="page"' in content
        tabs = response.context['tabs']
        counts = {tab['key']: tab['count'] for tab in tabs}
        assert counts == {
            'all': None,
            'needs_review': 1,
            'processing': 2,
            'failed': 1,
            'duplicates': 1,
        }
        assert next(tab for tab in tabs if tab['active'])['key'] == 'failed'

    def test_the_trash_link_shows_how_many(self, web, make):
        make(deleted_at=timezone.now())
        make(deleted_at=timezone.now())

        assert 'Trash (2)' in web.get(reverse('receipts:dashboard')).content.decode()


class TestListPartial:
    def test_an_htmx_refresh_pushes_the_dashboard_url_not_its_own(self, web, make):
        make()

        response = web.get(reverse('receipts:list'), {'status': 'failed', 'q': 'a b'}, **HX)

        assert response.status_code == 200
        assert response['HX-Push-Url'] == '/receipts/?status=failed&q=a+b'

    def test_the_default_view_pushes_the_bare_dashboard_url(self, web, make):
        make()

        assert web.get(reverse('receipts:list'), **HX)['HX-Push-Url'] == '/receipts/'

    def test_it_resends_the_tabs_and_export_link_out_of_band(self, web, make):
        make()

        content = web.get(reverse('receipts:list'), {'q': 'x'}, **HX).content.decode()

        assert 'id="receipt-tabs" hx-swap-oob="true"' in content
        assert 'id="export-link" href="/receipts/export.csv?q=x" hx-swap-oob="true"' in content
        assert '<html' not in content

    def test_the_full_page_has_no_oob_duplicates(self, web, make):
        content = web.get(reverse('receipts:dashboard')).content.decode()

        assert content.count('id="receipt-tabs"') == 1
        assert 'hx-swap-oob' not in content

    def test_opened_directly_it_redirects_to_the_full_page(self, web):
        response = web.get(reverse('receipts:list'), {'status': 'failed', 'page': 2})

        assert response.status_code == 302
        assert response['Location'] == '/receipts/?status=failed&page=2'

    def test_rows_in_progress_make_the_list_poll_itself_up_to_a_cap(self, web, make):
        make(status=ReceiptStatus.PROCESSING)

        first = web.get(reverse('receipts:list'), **HX).content.decode()
        later = web.get(reverse('receipts:list'), {'polls': 5}, **HX).content.decode()
        capped = web.get(reverse('receipts:list'), {'polls': 40}, **HX).content.decode()

        assert 'hx-trigger="every 3s"' in first and 'hx-get="/receipts/list/?polls=1"' in first
        assert 'hx-get="/receipts/list/?polls=6"' in later
        assert 'every 3s' not in capped

    def test_a_row_waiting_for_a_retry_says_so_and_does_not_poll(self, web, make):
        make(
            status=ReceiptStatus.RECEIVED,
            attempts=1,
            next_attempt_at=timezone.now() + timedelta(minutes=30),
        )

        content = web.get(reverse('receipts:list'), **HX).content.decode()

        assert "couldn't read it yet, trying again in" in content
        assert 'every 3s' not in content

    def test_a_poll_keeps_the_filters_and_the_page_and_does_not_push_the_url(self, web, make):
        for index in range(26):
            make(status=ReceiptStatus.PROCESSING, vendor=f'V{index}')

        response = web.get(
            reverse('receipts:list'), {'status': 'processing', 'page': 2, 'polls': 1}, **HX
        )

        assert 'HX-Push-Url' not in response
        assert (
            'hx-get="/receipts/list/?status=processing&amp;page=2&amp;polls=2"'
            in response.content.decode()
        )

    def test_every_request_that_swaps_the_list_shares_one_sync_target(self, web, make):
        # The tabs sit outside the filter form, so they (and the links inside it, for clarity)
        # name the form as the shared queue: the newest request wins (`replace`). The poller is
        # the exception, `drop`: it must never abort a search the user just made, and its URL
        # still carries the old filters.
        for index in range(26):
            make(status=ReceiptStatus.PROCESSING, vendor=f'V{index}')

        content = web.get(reverse('receipts:dashboard')).content.decode()

        assert 'id="receipt-filters"' in content and 'hx-sync="this:replace"' in content
        for link in re.findall(r'<a [^>]*hx-get="[^"]*"[^>]*>', content):
            assert 'hx-sync="#receipt-filters:replace"' in link, link
        poller = re.search(r'<div hx-get="[^"]*" hx-trigger="every 3s"[^>]*>', content)
        assert poller and 'hx-sync="#receipt-filters:drop"' in poller.group(0)
        # Sort links (2), pager (page 1 of 2: Next), tabs (5).
        assert content.count('hx-sync="#receipt-filters:replace"') == 2 + 1 + 5

    def test_the_list_partial_does_not_create_the_receipts_account(self, web, make, funded_user):
        # Plain `render`, not `render_page`: a poll or a keystroke is not a first visit.
        make()
        ReceiptsAccount.objects.filter(pk=funded_user.pk).delete()

        response = web.get(reverse('receipts:list'), {'q': 'x'}, **HX)

        assert response.status_code == 200
        assert not ReceiptsAccount.objects.filter(pk=funded_user.pk).exists()

    def test_the_search_box_state_is_read_from_a_data_attribute(self, web):
        empty = web.get(reverse('receipts:dashboard')).content.decode()
        filled = web.get(
            reverse('receipts:dashboard'), {'q': "x' }; alert(1); //"}
        ).content.decode()

        assert 'x-data="{ hasValue: $el.dataset.hasValue === \'true\' }"' in empty
        assert 'data-has-value="false"' in empty and 'data-has-value="true"' in filled
        assert 'hasValue: true' not in filled

    @pytest.mark.parametrize(
        'status',
        [
            ReceiptStatus.PROCESSED,
            ReceiptStatus.NEEDS_REVIEW,
            ReceiptStatus.FAILED,
            ReceiptStatus.WAITING_FOR_CREDITS,
        ],
    )
    def test_settled_rows_do_not_poll(self, web, make, status):
        make(status=status)

        assert 'every 3s' not in web.get(reverse('receipts:list'), **HX).content.decode()


class TestExport:
    def rows(self, response) -> list[dict]:
        body = b''.join(response.streaming_content).decode()
        return list(csv.DictReader(io.StringIO(body)))

    def test_a_csv_download_with_the_documented_columns(self, web, make):
        make(
            vendor='Staples',
            amount=Decimal('42.10'),
            currency='USD',
            receipt_date=date(2026, 10, 9),
            notes='toner',
        )

        response = web.get(reverse('receipts:export'))

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv; charset=utf-8'
        assert response['Content-Disposition'].startswith('attachment; filename="receipts-')
        assert response['Content-Disposition'].endswith('.csv"')
        assert response['Cache-Control'] == 'private, no-store'
        [row] = self.rows(response)
        assert list(row) == list(CSV_COLUMNS)
        assert row['vendor'] == 'Staples' and row['amount'] == '42.10' and row['notes'] == 'toner'

    def test_it_honours_the_current_filters(self, web, make):
        make(vendor='Staples', receipt_date=date(2026, 10, 9))
        make(vendor='Delta', receipt_date=date(2026, 10, 3))
        make(vendor='Broken', status=ReceiptStatus.FAILED)

        by_text = self.rows(web.get(reverse('receipts:export'), {'q': 'delta'}))
        by_status = self.rows(web.get(reverse('receipts:export'), {'status': 'failed'}))
        by_date = self.rows(web.get(reverse('receipts:export'), {'date_from': '2026-10-05'}))

        assert [r['vendor'] for r in by_text] == ['Delta']
        assert [r['vendor'] for r in by_status] == ['Broken']
        assert [r['vendor'] for r in by_date] == ['Staples']

    def test_it_exports_only_the_owners_live_receipts(self, web, make, other_user, fake_storage):
        make(vendor='Mine')
        make(vendor='Trashed', deleted_at=timezone.now())
        create_receipt(other_user, fake_storage, vendor='Theirs')

        assert [r['vendor'] for r in self.rows(web.get(reverse('receipts:export')))] == ['Mine']

    def test_the_link_on_the_page_carries_the_filters(self, web, make):
        content = web.get(
            reverse('receipts:dashboard'), {'status': 'failed', 'q': 'a'}
        ).content.decode()

        assert 'href="/receipts/export.csv?status=failed&amp;q=a"' in content

    def test_it_is_rate_limited(self, web, make, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'RECEIPTS_EXPORT_USER': (1, 3600)}

        assert web.get(reverse('receipts:export')).status_code == 200
        limited = web.get(reverse('receipts:export'))

        assert limited.status_code == 429
        assert limited['Retry-After']


class TestFrozenAccount:
    @pytest.mark.parametrize('name', ['dashboard', 'upload', 'trash', 'settings'])
    def test_every_page_shows_the_banner_with_a_top_up_link(self, web, freeze, name):
        content = web.get(reverse(f'receipts:{name}')).content.decode()

        assert 'Storage is paused' in content
        assert f'href="{reverse("billing:credits-history")}"' in content
        assert 'Top up' in content

    def test_a_working_account_has_no_banner(self, web):
        assert 'Storage is paused' not in web.get(reverse('receipts:dashboard')).content.decode()

    def test_the_list_still_works(self, web, freeze, make):
        make(vendor='Still here')

        assert 'Still here' in web.get(reverse('receipts:dashboard')).content.decode()


@pytest.mark.parametrize(
    ('key', 'today', 'expected'),
    [
        ('last_15_days', date(2026, 10, 12), (date(2026, 9, 28), date(2026, 10, 12))),
        ('last_30_days', date(2026, 10, 12), (date(2026, 9, 13), date(2026, 10, 12))),
        ('this_month', date(2026, 2, 14), (date(2026, 2, 1), date(2026, 2, 28))),
        ('this_month', date(2028, 2, 1), (date(2028, 2, 1), date(2028, 2, 29))),
        ('this_quarter', date(2026, 1, 1), (date(2026, 1, 1), date(2026, 3, 31))),
        ('this_quarter', date(2026, 5, 20), (date(2026, 4, 1), date(2026, 6, 30))),
        ('this_quarter', date(2026, 9, 30), (date(2026, 7, 1), date(2026, 9, 30))),
        ('this_quarter', date(2026, 12, 31), (date(2026, 10, 1), date(2026, 12, 31))),
        ('this_year', date(2026, 10, 12), (date(2026, 1, 1), date(2026, 12, 31))),
    ],
)
def test_resolve_date_range(key, today, expected):
    assert resolve_date_range(key, today) == expected
