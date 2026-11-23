"""The "Download files" dialog, the exports pages and the actions on them (`views/exports.py`)."""

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import credits_url, get_balance, spend_credits
from apps.core.services import exports as engine

from ..models import ExportStatus, ReceiptExport, ReceiptStatus
from ..services import exports as svc
from ..services.costs import cost_section
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def receipts_block(content: str) -> str:
    """Receipts' block of the account page's App actions: up to the next app's block."""
    return content.split('data-app-actions="receipts"')[1].split('data-app-actions="')[0]


HTMX = {'HTTP_HX_REQUEST': 'true'}
NEW = 'receipts:export-new'


@pytest.fixture(autouse=True)
def price(receipts_settings):
    receipts_settings.export_price_per_file = 3
    receipts_settings.save()


@pytest.fixture
def three(make_receipt):
    return [
        make_receipt(
            data=b'one-' * 5,
            original_filename='one.pdf',
            vendor='Acme',
            receipt_date=date(2026, 3, 4),
            amount=Decimal('10.00'),
            status=ReceiptStatus.PROCESSED,
        ),
        make_receipt(
            data=b'two-' * 5,
            original_filename='two.pdf',
            vendor='Beta Co',
            receipt_date=date(2025, 1, 2),
            status=ReceiptStatus.PROCESSED,
        ),
        make_receipt(data=b'three-' * 5, original_filename='three.pdf', vendor='Acme'),
    ]


def flashed(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def post_new(web, **data):
    body = {'layout': 'year_vendor', 'confirm_cost': '9', **data}
    return web.post(reverse(NEW), body, **HTMX)


def build(export: ReceiptExport) -> None:
    while (number := svc.next_part_to_build(export)) is not None:
        ReceiptExport.objects.filter(pk=export.pk).update(lease_until=None)
        svc.build_part.call(str(export.pk), number)
        export.refresh_from_db()


@pytest.fixture
def ready(funded_user, three):
    export = svc.create_export(funded_user, None, 'year_vendor')
    build(export)
    export.refresh_from_db()
    return export


# -- The account page ---------------------------------------------------------------------------


class TestAccountActions:
    def test_the_account_page_has_the_receipts_block(self, web, three):
        content = web.get(reverse('accounts:account')).content.decode()

        assert 'data-app-actions="receipts"' in content
        assert 'id="receipts-export-files"' in content and 'Download files' in content
        assert f'hx-get="{reverse(NEW)}"' in content and 'hx-include="closest form"' in content
        assert 'id="export-modal"' in content
        assert 'id="receipts-export-csv"' in content and 'Export CSV' in content
        assert f'action="{reverse("receipts:export")}"' in content
        assert 'name="date_range"' in content and 'Last 15 days' in content
        assert 'name="date_from"' in content and 'name="date_to"' in content
        assert reverse('receipts:exports') in content

    def test_the_block_sits_between_the_apps_and_the_notifications(self, web):
        content = web.get(reverse('accounts:account')).content.decode()
        assert content.index('id="apps-form"') < content.index('data-app-actions="receipts"')

    def test_the_dashboard_no_longer_has_the_buttons(self, web, three):
        content = web.get(reverse('receipts:dashboard')).content.decode()

        assert 'receipts-export-files' not in content and 'id="receipts-export-csv"' not in content
        assert 'id="export-modal"' not in content
        assert reverse('receipts:exports') in content  # the Exports link stays
        content = web.get(reverse('receipts:list'), **HTMX).content.decode()
        assert 'receipts-export-files' not in content and 'id="receipts-export-csv"' not in content

    def test_the_block_loads_its_dialog_script_once(self, web):
        content = web.get(reverse('accounts:account')).content.decode()
        assert content.count('core/js/fragment_modal.js') == 1
        assert content.index('core/js/fragment_modal.js') < content.index(
            'data-app-actions="receipts"'
        )

    def test_any_date_is_preselected_and_the_custom_dates_are_disabled(self, web):
        content = web.get(reverse('accounts:account')).content.decode()
        block = receipts_block(content)
        assert '<option value="any" selected>' in block
        assert re.search(r'name="date_from"[^>]*\sdisabled(?=[\s>])', block, re.S)

    def test_a_range_in_the_query_is_preselected(self, web):
        content = web.get(
            reverse('accounts:account'),
            {'receipts_date_from': '2026-01-01', 'receipts_date_to': '2026-02-28'},
        ).content.decode()
        block = receipts_block(content)
        assert '<option value="custom" selected>' in block
        assert 'value="2026-01-01"' in block and 'value="2026-02-28"' in block
        assert not re.search(r'name="date_from"[^>]*\sdisabled(?=[\s>])', block, re.S)

        preset = web.get(
            reverse('accounts:account'), {'receipts_date_range': 'this_month'}
        ).content.decode()
        assert '<option value="this_month" selected>' in receipts_block(preset)

    def test_unprefixed_or_bad_values_are_ignored(self, web):
        content = web.get(
            reverse('accounts:account'), {'date_range': 'this_month', 'receipts_date_range': 'nope'}
        ).content.decode()
        assert '<option value="any" selected>' in receipts_block(content)

    def test_viewing_the_account_page_creates_no_receipts_account(self, web, user):
        from ..models import ReceiptsAccount

        web.get(reverse('accounts:account'))
        assert not ReceiptsAccount.objects.filter(pk=user.pk).exists()


# -- The dialog ---------------------------------------------------------------------------------


class TestDialog:
    def test_states_the_files_parts_and_cost(self, web, three):
        response = web.get(reverse(NEW), **HTMX)
        content = response.content.decode()

        assert response.status_code == 200
        assert '3 files' in content and '1 zip file' in content
        assert 'of about 100.0\xa0MB each' in content
        assert 'This costs <strong class="font-semibold">9 credits</strong> (3 per file)' in content
        assert 'Your balance is 100' in content
        assert 'name="confirm_cost" value="9"' in content
        assert 'Download for 9 credits' in content
        assert 'receipts.csv' in content and '30 days' in content
        assert 'Receipts in the Trash are not included' in content

    def test_the_price_is_read_from_the_setting(self, web, three, receipts_settings):
        receipts_settings.export_price_per_file = 4
        receipts_settings.save()
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert '12 credits' in content and '(4 per file)' in content

    def test_the_layouts_with_examples_and_year_vendor_by_default(self, web, three):
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'Year / Vendor' in content and '2026/Acme/invoice.pdf' in content
        assert 'Vendor / Year' in content and 'Acme/2026/invoice.pdf' in content
        assert re.search(r'value="year_vendor"\s+checked', content)
        content = web.get(reverse(NEW), {'layout': 'vendor_year'}, **HTMX).content.decode()
        assert re.search(r'value="vendor_year"\s+checked', content)

    def test_it_exports_what_the_list_shows(self, web, three):
        content = web.get(reverse(NEW), {'q': 'Beta'}, **HTMX).content.decode()
        assert '1 file' in content and 'name="confirm_cost" value="3"' in content
        assert 'name="q" value="Beta"' in content
        assert 'Search: &quot;Beta&quot;' in content

    def test_a_free_export_says_so(self, web, three, receipts_settings):
        receipts_settings.export_price_per_file = 0
        receipts_settings.save()
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'This is free.' in content and 'Download files' in content

    def test_too_few_credits_offers_a_top_up(self, web, funded_user, three):
        spend_credits(funded_user, 95, description='x', source='test')
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'Your balance is 5' in content and credits_url('history') in content

    def test_nothing_to_download(self, web):
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'no receipt files to download' in content
        assert re.search(r'<button type="button" disabled', content)

    def test_a_frozen_account_cannot_start_one(self, web, three, freeze):
        content = web.get(reverse(NEW), **HTMX).content.decode()
        assert 'Storage is paused' in content and re.search(
            r'<button type="button" disabled', content
        )

    def test_opened_directly_it_goes_to_the_account_page(self, web):
        response = web.get(reverse(NEW), {'q': 'acme'})
        assert response.status_code == 302
        assert response['Location'] == f'{reverse("accounts:account")}#apps'

    def test_a_custom_date_range_quotes_only_those_receipts(self, web, three):
        content = web.get(
            reverse(NEW),
            {'date_range': 'custom', 'date_from': '2026-01-01', 'date_to': '2026-12-31'},
            **HTMX,
        ).content.decode()
        assert '1 file' in content and 'name="confirm_cost" value="3"' in content
        assert 'Only these receipts:' in content and 'Receipt date:' in content

    def test_a_preset_range_quotes_the_receipts_in_it(self, web, three, funded_user):
        today = timezone.localdate()
        three[1].receipt_date = today
        three[1].save(update_fields=['receipt_date'])
        content = web.get(reverse(NEW), {'date_range': 'last_15_days'}, **HTMX).content.decode()
        assert '1 file' in content and 'name="confirm_cost" value="3"' in content

    def test_any_date_quotes_everything_and_says_so(self, web, three):
        content = web.get(reverse(NEW), {'date_range': 'any'}, **HTMX).content.decode()
        assert '3 files' in content
        assert 'choose a date range first' in content

    def test_it_needs_a_login(self, client):
        response = client.get(reverse(NEW), **HTMX)
        assert response.status_code == 200 and 'login' in response['HX-Redirect']
        assert client.get(reverse(NEW)).status_code == 302


# -- Confirming ---------------------------------------------------------------------------------


class TestConfirm:
    def test_charges_creates_and_goes_to_the_exports_page(self, web, funded_user, three):
        response = post_new(web)

        export = ReceiptExport.objects.get()
        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('receipts:export-detail', args=[export.pk])
        assert get_balance(funded_user) == 91
        assert export.status == ExportStatus.BUILDING and export.charged_credits == 9
        assert any('preparing your zip files' in m for m in flashed(response))

    def test_without_htmx_it_redirects(self, web, three):
        response = web.post(reverse(NEW), {'layout': 'vendor_year', 'confirm_cost': '9'})
        export = ReceiptExport.objects.get()
        assert response.status_code == 302
        assert response['Location'] == reverse('receipts:export-detail', args=[export.pk])
        assert export.layout == 'vendor_year'

    def test_the_filters_in_the_form_are_what_is_exported(self, web, three):
        response = post_new(web, confirm_cost='3', q='Beta')
        assert response.status_code == 200
        export = ReceiptExport.objects.get()
        assert export.file_count == 1 and export.filters['summary'] == ['Search: "Beta"']

    def test_a_changed_cost_charges_nothing_and_asks_again(
        self, web, funded_user, three, make_receipt
    ):
        make_receipt()  # a fourth file appeared after the dialog was shown

        response = post_new(web)

        content = response.content.decode()
        assert response.status_code == 422
        assert 'The cost changed. Please confirm the new total.' in content
        assert 'name="confirm_cost" value="12"' in content and 'Download for 12 credits' in content
        assert get_balance(funded_user) == 100 and not ReceiptExport.objects.exists()
        # confirming the new total works
        assert post_new(web, confirm_cost='12').status_code == 200
        assert get_balance(funded_user) == 88

    def test_the_form_is_swapped_in_place(self, web, three):
        content = post_new(web, confirm_cost='1').content.decode()
        assert '<form' in content and 'role="dialog"' not in content
        assert '<form' in content and 'fragmentModal' not in content

    def test_not_enough_credits(self, web, funded_user, three):
        spend_credits(funded_user, 95, description='x', source='test')
        response = post_new(web)
        content = response.content.decode()
        assert response.status_code == 422
        assert (
            'You don&#x27;t have enough credits for this.' in content or 'enough credits' in content
        )
        assert credits_url('history') in content
        assert get_balance(funded_user) == 5 and not ReceiptExport.objects.exists()

    def test_a_frozen_account(self, web, funded_user, three, freeze):
        response = post_new(web)
        assert response.status_code == 422
        assert 'paused' in response.content.decode() and not ReceiptExport.objects.exists()

    def test_one_at_a_time(self, web, three):
        assert post_new(web).status_code == 200
        response = post_new(web)
        assert response.status_code == 422
        assert 'already being prepared' in response.content.decode()
        assert ReceiptExport.objects.count() == 1

    def test_nothing_to_export(self, web):
        response = post_new(web, confirm_cost='0')
        assert response.status_code == 422 and 'no files' in response.content.decode()

    @pytest.mark.parametrize('layout', ['', 'sideways'])
    def test_a_bad_layout_is_refused(self, web, funded_user, three, layout):
        response = post_new(web, layout=layout)
        assert response.status_code == 422
        assert 'Choose how to organize the files.' in response.content.decode()
        assert get_balance(funded_user) == 100

    @pytest.mark.parametrize('value', [None, '', 'abc', '-3'])
    def test_the_confirmed_cost_is_required(self, web, funded_user, three, value):
        body = {'layout': 'year_vendor'}
        if value is not None:
            body['confirm_cost'] = value
        response = web.post(reverse(NEW), body, **HTMX)
        assert response.status_code == 422 and get_balance(funded_user) == 100

    def test_a_refusal_without_htmx_is_a_flash_and_the_account_page(self, web, three):
        response = web.post(
            reverse(NEW), {'layout': 'year_vendor', 'confirm_cost': '1', 'q': 'Beta'}
        )
        assert response.status_code == 302 and response['Location'] == (
            f'{reverse("accounts:account")}#apps'
        )
        assert 'cost changed' in ' '.join(flashed(response))

    def test_a_refusal_without_htmx_keeps_the_chosen_range(self, web, three):
        response = web.post(
            reverse(NEW),
            {
                'layout': 'year_vendor',
                'confirm_cost': '99',
                'date_from': '2026-01-01',
                'date_to': '2026-12-31',
            },
        )
        assert response.status_code == 302
        assert response['Location'] == (
            f'{reverse("accounts:account")}'
            '?receipts_date_from=2026-01-01&receipts_date_to=2026-12-31#apps'
        )

        preset = web.post(
            reverse(NEW),
            {'layout': 'year_vendor', 'confirm_cost': '99', 'date_range': 'this_year'},
        )
        assert preset['Location'] == (
            f'{reverse("accounts:account")}?receipts_date_range=this_year#apps'
        )

    def test_get_and_post_only(self, web):
        assert web.put(reverse(NEW)).status_code == 405

    def test_it_needs_a_login(self, client):
        assert client.post(reverse(NEW), {}).status_code == 302

    def test_another_users_vendor_in_the_filters_is_dropped(
        self, web, other_user, three, fake_storage
    ):
        from .factories import create_vendor

        theirs = create_vendor(other_user, 'Acme')
        response = post_new(web, confirm_cost='9', vendor=str(theirs.pk))
        assert response.status_code == 200 and ReceiptExport.objects.get().file_count == 3


class TestRateLimit:
    @pytest.fixture(autouse=True)
    def limit(self, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_EXPORT_FILES_USER': (1, 3600),
            'RECEIPTS_EXPORT_DIALOG_USER': (1, 60),
        }

    def test_the_second_export_in_the_window_is_limited(self, web, three):
        first = post_new(web)
        ReceiptExport.objects.update(status=ExportStatus.READY)
        second = post_new(web)

        assert first.status_code == 200
        assert second.status_code == 429 and second['Retry-After']
        assert second['HX-Retarget'] == '#export-msg'
        assert ReceiptExport.objects.count() == 1

    def test_a_refused_request_spends_none_of_the_export_budget(
        self, web, funded_user, three, settings
    ):
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_EXPORT_DIALOG_USER': (100, 60),
        }
        for _ in range(3):
            assert post_new(web, confirm_cost='1').status_code == 422  # the cost changed
        assert post_new(web).status_code == 200

    def test_a_refusal_that_read_the_list_spends_the_dialog_budget(self, web, funded_user, three):
        assert post_new(web, confirm_cost='1').status_code == 422  # reads the list: one hit
        second = post_new(web, confirm_cost='1')

        assert second.status_code == 429 and second['HX-Retarget'] == '#export-msg'
        assert not ReceiptExport.objects.exists()

    def test_over_the_files_limit_nothing_is_read_or_spent(
        self, web, funded_user, three, settings, monkeypatch
    ):
        from apps.core import ratelimit
        from apps.receipts.views import exports as views

        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_EXPORT_DIALOG_USER': (5, 60),
        }
        assert post_new(web).status_code == 200
        ReceiptExport.objects.update(status=ExportStatus.READY)
        reads: list[int] = []
        real = views.prepare_export
        monkeypatch.setattr(
            views, 'prepare_export', lambda *a, **k: reads.append(1) or real(*a, **k)
        )
        dialog_left = ratelimit.peek_user(funded_user, 'RECEIPTS_EXPORT_DIALOG_USER').remaining

        for data in ({}, {}, {'confirm_cost': '1'}):
            limited = post_new(web, **data)
            assert limited.status_code == 429 and limited['HX-Retarget'] == '#export-msg'

        assert reads == []  # not one whole-list read
        assert (
            ratelimit.peek_user(funded_user, 'RECEIPTS_EXPORT_DIALOG_USER').remaining == dialog_left
        )
        assert ReceiptExport.objects.count() == 1

    def test_opening_the_dialog_is_limited(self, web, three):
        first = web.get(reverse(NEW), **HTMX)
        second = web.get(reverse(NEW), **HTMX)
        assert first.status_code == 200
        assert second.status_code == 429 and second['Retry-After']
        assert 'Quote' not in second.content.decode()

    def test_a_dialog_redirected_to_the_account_page_spends_none_of_it(self, web, three):
        assert web.get(reverse(NEW)).status_code == 302
        assert web.get(reverse(NEW)).status_code == 302
        assert web.get(reverse(NEW), **HTMX).status_code == 200

    def test_an_invalid_form_spends_none_of_it(self, web, three):
        assert post_new(web, layout='x').status_code == 422
        assert post_new(web).status_code == 200


class TestReadsTheListOnce:
    @pytest.fixture
    def reads(self, monkeypatch):
        calls = []
        real = svc.select_rows
        monkeypatch.setattr(svc, 'select_rows', lambda *a, **k: calls.append(1) or real(*a, **k))
        return calls

    def test_the_dialog(self, web, three, reads):
        web.get(reverse(NEW), **HTMX)
        assert len(reads) == 1

    def test_a_confirm(self, web, three, reads):
        assert post_new(web).status_code == 200
        assert len(reads) == 1

    def test_a_refusal_with_a_changed_cost(self, web, three, reads, make_receipt):
        make_receipt()
        assert post_new(web).status_code == 422
        assert len(reads) == 1

    def test_a_refusal_for_too_few_credits(self, web, funded_user, three, reads):
        spend_credits(funded_user, 95, description='x', source='test')
        assert post_new(web).status_code == 422
        assert len(reads) == 1


# -- The pages ----------------------------------------------------------------------------------


class TestPages:
    def test_the_list(self, web, ready, other_user, fake_storage):
        theirs = create_receipt(other_user, fake_storage)
        mine_deleted = svc.create_export(ready.user, None, 'year_vendor')
        ReceiptExport.objects.filter(pk=mine_deleted.pk).update(status=ExportStatus.DELETED)

        content = web.get(reverse('receipts:exports')).content.decode()

        assert reverse('receipts:export-detail', args=[ready.pk]) in content
        assert reverse('receipts:export-detail', args=[mine_deleted.pk]) not in content
        assert '3 files' in content and 'Ready' in content and 'until' in content
        assert theirs  # (theirs has no export: nothing of it is listed)

    def test_an_empty_list(self, web):
        assert 'No exports yet' in web.get(reverse('receipts:exports')).content.decode()

    def test_a_ready_export_has_a_download_button_per_part(self, web, ready, three):
        content = web.get(reverse('receipts:export-detail', args=[ready.pk])).content.decode()

        assert 'Ready' in content and 'Available to download until' in content
        assert reverse('receipts:export-part-download', args=[ready.pk, 1]) in content
        assert svc.part_name(ready, 1) in content and 'with receipts.csv' in content
        assert 'hx-get' not in content.split('id="export-panel"')[1].split('>')[0]
        assert reverse('receipts:export-delete', args=[ready.pk]) in content

    def test_a_building_export_polls_and_cannot_be_deleted(self, web, funded_user, three):
        export = svc.create_export(funded_user, None, 'year_vendor')

        content = web.get(reverse('receipts:export-detail', args=[export.pk])).content.decode()

        assert 'Preparing your zip files: 0 of 1 done' in content
        assert f'hx-get="{reverse("receipts:export-panel", args=[export.pk])}?polls=1"' in content
        assert reverse('receipts:export-delete', args=[export.pk]) not in content
        assert reverse('receipts:export-part-download', args=[export.pk, 1]) not in content

    def test_polling_stops_after_a_while(self, web, funded_user, three):
        export = svc.create_export(funded_user, None, 'year_vendor')
        response = web.get(
            reverse('receipts:export-panel', args=[export.pk]), {'polls': 40}, **HTMX
        )
        assert 'hx-get' not in response.content.decode().split('>')[1]
        assert 'Reload the page' in response.content.decode()

    def test_the_panel_swaps_to_the_buttons_when_ready(self, web, funded_user, three):
        export = svc.create_export(funded_user, None, 'year_vendor')
        build(export)
        content = web.get(
            reverse('receipts:export-panel', args=[export.pk]), **HTMX
        ).content.decode()
        assert reverse('receipts:export-part-download', args=[export.pk, 1]) in content

    def test_the_panel_opened_directly_goes_to_the_page(self, web, ready):
        response = web.get(reverse('receipts:export-panel', args=[ready.pk]))
        assert response.status_code == 302
        assert response['Location'] == reverse('receipts:export-detail', args=[ready.pk])

    def test_a_failed_export_says_the_credits_came_back(self, web, funded_user, three):
        export = svc.create_export(funded_user, None, 'year_vendor')
        svc.fail_export(export.pk, 'boom')
        content = web.get(reverse('receipts:export-detail', args=[export.pk])).content.decode()
        assert 'couldn&#x27;t prepare' in content or "couldn't prepare" in content
        assert 'The 9 credits you paid were refunded.' in content and 'Failed' in content

    def test_an_expired_export_stays_listed_with_its_state(self, web, ready):
        engine.end(svc.KIND, ready, to=ExportStatus.EXPIRED)
        content = web.get(reverse('receipts:export-detail', args=[ready.pk])).content.decode()
        assert 'Expired' in content and 'were deleted' in content
        assert reverse('receipts:export-part-download', args=[ready.pk, 1]) not in content

    def test_skipped_files_are_explained(self, web, funded_user, three, fake_storage):
        export = svc.create_export(funded_user, None, 'year_vendor')
        del fake_storage.objects[three[0].file_key]
        build(export)
        content = web.get(reverse('receipts:export-detail', args=[export.pk])).content.decode()
        assert '1 file was no longer there and left out; 3 credits refunded' in content

    def test_the_dashboard_links_to_the_exports(self, web):
        assert (
            reverse('receipts:exports') in web.get(reverse('receipts:dashboard')).content.decode()
        )

    def test_everything_needs_a_login(self, client, ready):
        for name, args in (
            ('exports', []),
            ('export-detail', [ready.pk]),
            ('export-panel', [ready.pk]),
            ('export-part-download', [ready.pk, 1]),
        ):
            assert client.get(reverse(f'receipts:{name}', args=args)).status_code == 302
        assert client.post(reverse('receipts:export-delete', args=[ready.pk])).status_code == 302


class TestOtherUsers:
    @pytest.fixture
    def intruder(self, client, other_user):
        client.force_login(other_user)
        return client

    def test_another_users_export_is_a_404_everywhere(self, intruder, ready):
        for name, args in (
            ('export-detail', [ready.pk]),
            ('export-panel', [ready.pk]),
            ('export-part-download', [ready.pk, 1]),
        ):
            assert intruder.get(reverse(f'receipts:{name}', args=args), **HTMX).status_code == 404
        assert intruder.post(reverse('receipts:export-delete', args=[ready.pk])).status_code == 404
        ready.refresh_from_db()
        assert ready.status == ExportStatus.READY

    def test_an_unknown_one_is_a_404(self, web):
        assert web.get(reverse('receipts:export-detail', args=[uuid.uuid4()])).status_code == 404

    def test_a_deleted_export_is_a_404(self, web, ready):
        svc.delete_export(ready)
        assert web.get(reverse('receipts:export-detail', args=[ready.pk])).status_code == 404

    def test_the_list_shows_only_my_exports(self, intruder, ready):
        assert str(ready.pk) not in intruder.get(reverse('receipts:exports')).content.decode()


# -- Downloading and deleting -------------------------------------------------------------------


class TestPartDownload:
    def url(self, export, number=1):
        return reverse('receipts:export-part-download', args=[export.pk, number])

    def test_redirects_to_a_presigned_url(self, web, ready, fake_storage):
        response = web.get(self.url(ready))

        assert response.status_code == 302
        assert response['Location'].startswith('https://fake-s3.test/')
        assert 'part-01.zip' in response['Location']
        assert response['Cache-Control'] == 'private, no-store'
        assert fake_storage.get_urls[-1]['disposition'] == 'attachment'

    def test_a_frozen_account_is_sent_back_with_a_message(self, web, ready, freeze):
        response = web.get(self.url(ready))
        assert response.status_code == 302
        assert response['Location'] == reverse('receipts:export-detail', args=[ready.pk])
        assert 'paused' in ' '.join(flashed(response))

    def test_an_export_that_is_not_ready_is_sent_back(self, web, funded_user, three):
        export = svc.create_export(funded_user, None, 'year_vendor')
        response = web.get(self.url(export))
        assert response.status_code == 302 and flashed(response)

    def test_an_unknown_part_is_sent_back(self, web, ready):
        response = web.get(self.url(ready, 7))
        assert response.status_code == 302 and 'no such file' in ' '.join(flashed(response)).lower()

    def test_it_is_rate_limited(self, web, ready, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_EXPORT_DOWNLOAD_USER': (1, 60),
        }
        assert web.get(self.url(ready)).status_code == 302
        limited = web.get(self.url(ready))
        assert limited.status_code == 429 and limited['Retry-After']

    def test_post_is_not_allowed(self, web, ready):
        assert web.post(self.url(ready)).status_code == 405


class TestDelete:
    def test_deletes_the_export_and_its_files(self, web, ready, fake_storage):
        key = ready.parts.get().key

        response = web.post(reverse('receipts:export-delete', args=[ready.pk]))

        assert response.status_code == 302 and response['Location'] == reverse('receipts:exports')
        ready.refresh_from_db()
        assert ready.status == ExportStatus.DELETED and key not in fake_storage.objects
        assert 'The export was deleted.' in flashed(response)

    def test_a_building_export_is_refused(self, web, funded_user, three):
        export = svc.create_export(funded_user, None, 'year_vendor')
        response = web.post(reverse('receipts:export-delete', args=[export.pk]))
        assert response.status_code == 302
        assert 'still being prepared' in ' '.join(flashed(response))
        export.refresh_from_db()
        assert export.status == ExportStatus.BUILDING

    def test_get_is_not_allowed(self, web, ready):
        assert web.get(reverse('receipts:export-delete', args=[ready.pk])).status_code == 405

    def test_no_refund(self, web, funded_user, ready):
        balance = get_balance(funded_user)
        web.post(reverse('receipts:export-delete', args=[ready.pk]))
        assert get_balance(funded_user) == balance
        assert not CreditTransaction.objects.filter(description__contains='delete').exists()

    def test_expiry_makes_the_old_export_say_so(self, web, ready):
        ReceiptExport.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        svc.expire_exports()
        content = web.get(reverse('receipts:exports')).content.decode()
        assert 'Expired' in content


# -- Costs --------------------------------------------------------------------------------------


class TestCostsPage:
    def test_the_costs_section_lists_the_download_at_the_live_price(
        self, funded_user, receipts_settings
    ):
        [line] = [
            line
            for line in cost_section(funded_user).lines
            if line.action == 'Download all files as zip'
        ]
        assert line.cost == '3 credits' and line.unit == 'per file'
        assert 'You confirm the cost first.' in line.note and '30 days' in line.note

        receipts_settings.export_price_per_file = 8
        receipts_settings.export_retention_days = 14
        receipts_settings.save()
        [line] = [
            line
            for line in cost_section(funded_user).lines
            if line.action == 'Download all files as zip'
        ]
        assert line.cost == '8 credits' and '14 days' in line.note

    def test_a_free_download_shows_as_free(self, funded_user, receipts_settings):
        receipts_settings.export_price_per_file = 0
        receipts_settings.save()
        [line] = [
            line
            for line in cost_section(funded_user).lines
            if line.action == 'Download all files as zip'
        ]
        assert line.free

    def test_the_costs_page_shows_it(self, web):
        content = web.get(reverse('billing:costs')).content.decode()
        assert 'Download all files as zip' in content
