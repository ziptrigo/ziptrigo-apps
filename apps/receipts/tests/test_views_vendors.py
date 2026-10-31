"""The Vendors page and what hangs off it (`views/vendors.py`), the vendor field on a receipt's
page, and the dashboard's Vendors link and vendor filter."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from ..models import Receipt, ReceiptStatus, Vendor, VendorAlias
from .factories import create_receipt, create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HX = {'HTTP_HX_REQUEST': 'true'}


def url(name, *args):
    return reverse(f'receipts:{name}', args=args)


def flashed(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def hx_from(path: str) -> dict:
    """The headers of an htmx request made from the page at `path`."""
    return {**HX, 'HTTP_HX_CURRENT_URL': f'http://testserver{path}'}


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(**fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        return create_receipt(funded_user, fake_storage, **fields)

    return _make


@pytest.fixture
def acme(funded_user, make):
    vendor = create_vendor(
        funded_user, 'ACME', vetted=True, aliases=('ACME Industries, LLC', 'Acme Ind. Oakland')
    )
    for day in (3, 9):
        make(vendor='ACME', vendor_ref=vendor, receipt_date=date(2026, 10, day))
    return vendor


class TestAccess:
    @pytest.mark.parametrize(
        ('method', 'name', 'args'),
        [
            ('get', 'vendors', ()),
            ('get', 'vendor-list', ()),
            ('get', 'vendor-suggest', ()),
            ('post', 'vendor-vet', ()),
            ('get', 'vendor-merge', ()),
            ('post', 'vendor-merge', ()),
            ('get', 'vendor-rename', (1,)),
            ('post', 'vendor-rename', (1,)),
            ('post', 'vendor-delete', (1,)),
        ],
    )
    def test_anonymous_users_are_sent_to_login(self, client, method, name, args):
        response = getattr(client, method)(url(name, *args))

        assert response.status_code == 302
        assert response['Location'].startswith(reverse('accounts:login'))

    @pytest.mark.parametrize(
        ('method', 'name'),
        [('get', 'vendor-rename'), ('post', 'vendor-rename'), ('post', 'vendor-delete')],
    )
    def test_another_users_vendor_is_a_404_never_a_403(self, web, other_user, method, name):
        theirs = create_vendor(other_user, 'Theirs', vetted=True)

        response = getattr(web, method)(url(name, theirs.pk), **HX)

        assert response.status_code == 404
        assert Vendor.objects.filter(pk=theirs.pk, name='Theirs').exists()


class TestPage:
    def test_it_lists_the_users_vendors_with_their_chip_names_and_counts(
        self, web, acme, funded_user, other_user
    ):
        create_vendor(funded_user, 'Blue Bottle')
        create_vendor(other_user, 'Theirs')

        response = web.get(url('vendors'))
        content = response.content.decode()

        assert response.status_code == 200
        assert '<title>Vendors - ZipTrigo</title>' in content
        assert 'ACME' in content and 'Blue Bottle' in content and 'Theirs' not in content
        assert 'Vetted' in content and '>New<' in content
        assert (
            'Also read as &ldquo;ACME Industries, LLC&rdquo;, &ldquo;Acme Ind. Oakland&rdquo;'
            in (content)
        )
        assert f'{url("dashboard")}?vendor={acme.pk}' in content
        assert '2026-10-09' in content

    def test_the_tabs_count_all_to_vet_and_vetted(self, web, acme, funded_user):
        create_vendor(funded_user, 'Blue Bottle')
        create_vendor(funded_user, 'Zed')

        content = web.get(url('vendors')).content.decode()

        assert 'aria-label="Vendor status"' in content
        tabs = content.split('aria-label="Vendor status"')[1].split('</nav>')[0]
        assert '>3</span>' in tabs and '>2</span>' in tabs and '>1</span>' in tabs
        assert 'bg-amber-50' in tabs  # the "To vet" count

    def test_a_tab_filters_and_the_list_says_when_nothing_matches(self, web, acme, funded_user):
        create_vendor(funded_user, 'Blue Bottle')

        to_vet = web.get(url('vendors'), {'status': 'to_vet'}).content.decode()
        vetted = web.get(url('vendors'), {'status': 'vetted'}).content.decode()
        none = web.get(url('vendors'), {'q': 'nothing like it'}).content.decode()

        assert 'Blue Bottle' in to_vet and 'Select ACME' not in to_vet
        assert 'Select ACME' in vetted and 'Blue Bottle' not in vetted
        assert 'No vendors found.' in none

    def test_a_bad_status_falls_back_to_all(self, web, acme):
        content = web.get(url('vendors'), {'status': 'bogus'}).content.decode()

        assert 'Select ACME' in content

    def test_an_empty_list_says_how_vendors_appear(self, web):
        content = web.get(url('vendors')).content.decode()

        assert 'No vendors yet.' in content

    def test_search_covers_the_names_vendors_were_read_as(self, web, acme, funded_user):
        create_vendor(funded_user, 'Blue Bottle')

        content = web.get(url('vendors'), {'q': 'oakland'}).content.decode()

        assert 'Select ACME' in content and 'Blue Bottle' not in content

    def test_it_pages_fifty_at_a_time(self, web, funded_user):
        for index in range(55):
            create_vendor(funded_user, f'Vendor {index:02d}')

        first = web.get(url('vendors')).content.decode()
        second = web.get(url('vendors'), {'page': 2}).content.decode()

        assert 'Page 1 of 2' in first and 'Vendor 49' in first and 'Vendor 50' not in first
        assert 'Vendor 50' in second and 'Vendor 54' in second

    def test_the_row_menu_offers_every_action_and_only_deletes_an_empty_vendor(
        self, web, acme, funded_user
    ):
        empty = create_vendor(funded_user, 'Empty Shop')

        content = web.get(url('vendors')).content.decode()

        assert url('vendor-rename', acme.pk) in content
        assert f'{url("vendor-merge")}?from={acme.pk}' in content
        assert 'Mark as not vetted' in content and 'Mark as vetted' in content
        assert f'data-url="{url("vendor-delete", empty.pk)}' in content
        assert f'data-url="{url("vendor-delete", acme.pk)}' not in content
        assert 'Only a vendor with no receipts can be deleted' in content

    def test_receipts_in_the_trash_are_counted_apart(self, web, acme, make):
        make(vendor='ACME', vendor_ref=acme, deleted_at=timezone.now())

        content = web.get(url('vendors')).content.decode()

        assert '+1 in Trash' in content

    def test_the_page_has_a_host_for_the_dialogs_and_loads_its_script(self, web):
        content = web.get(url('vendors')).content.decode()

        assert 'id="vendor-modal"' in content and 'receipts/js/vendors.js' in content
        assert 'id="vendor-bulk"' in content

    def test_the_list_partial_is_for_htmx_and_pushes_the_pages_url(self, web, acme):
        response = web.get(url('vendor-list'), {'status': 'vetted', 'q': 'ac'}, **HX)

        assert response.status_code == 200
        assert response['HX-Push-Url'] == f'{url("vendors")}?status=vetted&q=ac'
        content = response.content.decode()
        assert 'id="vendor-msg"' in content and 'hx-swap-oob="true"' in content  # the tabs

    def test_opened_directly_the_list_partial_goes_to_the_page(self, web):
        response = web.get(url('vendor-list'), {'status': 'to_vet', 'page': 2})

        assert response.status_code == 302
        assert response['Location'] == f'{url("vendors")}?status=to_vet&page=2'

    def test_the_dashboard_links_to_it_with_the_number_to_vet(self, web, acme, funded_user):
        create_vendor(funded_user, 'Blue Bottle')
        create_vendor(funded_user, 'Zed')

        content = web.get(url('dashboard')).content.decode()

        assert url('vendors') in content and '2 to vet' in content

    def test_the_dashboard_link_has_no_count_when_nothing_is_waiting(self, web, acme):
        content = web.get(url('dashboard')).content.decode()

        assert url('vendors') in content and 'to vet' not in content


class TestVet:
    def test_marking_vendors_as_vetted_refreshes_the_list(self, web, funded_user):
        a = create_vendor(funded_user, 'A Shop')
        b = create_vendor(funded_user, 'B Shop')

        response = web.post(
            url('vendor-vet'), {'ids': [a.pk, b.pk]}, **hx_from(f'{url("vendors")}?status=to_vet')
        )

        assert response.status_code == 200
        assert 'Marked 2 vendors as vetted.' in response.content.decode()
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.vetted_at is not None and b.vetted_at is not None
        # The list is the one the user was looking at: the To vet tab, now empty.
        assert 'No vendors found.' in response.content.decode()

    def test_one_vendor_can_be_marked_not_vetted(self, web, acme):
        response = web.post(url('vendor-vet'), {'ids': [acme.pk], 'vetted': '0'}, **HX)

        assert 'Marked 1 vendor as not vetted.' in response.content.decode()
        acme.refresh_from_db()
        assert acme.vetted_at is None

    def test_nothing_selected_is_a_message(self, web):
        response = web.post(url('vendor-vet'), {}, **HX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#vendor-msg'
        assert 'Select at least one vendor.' in response.content.decode()

    def test_another_users_vendor_is_refused_and_nothing_changes(
        self, web, funded_user, other_user
    ):
        mine = create_vendor(funded_user, 'Mine')
        theirs = create_vendor(other_user, 'Theirs')

        response = web.post(url('vendor-vet'), {'ids': [mine.pk, theirs.pk]}, **HX)

        assert response.status_code == 422 and 'Unknown vendor' in response.content.decode()
        mine.refresh_from_db()
        assert mine.vetted_at is None

    def test_without_htmx_it_redirects_with_a_flash(self, web, funded_user):
        vendor = create_vendor(funded_user, 'A Shop')

        response = web.post(url('vendor-vet'), {'ids': [vendor.pk]})

        assert response.status_code == 302 and response['Location'] == url('vendors')
        assert flashed(response) == ['Marked 1 vendor as vetted.']

    def test_from_a_receipt_it_answers_with_that_receipts_vendor_field(
        self, web, funded_user, make
    ):
        vendor = create_vendor(funded_user, 'ACME Industries, LLC')
        receipt = make(vendor=vendor.name, vendor_ref=vendor)

        response = web.post(url('vendor-vet'), {'ids': [vendor.pk], 'receipt': receipt.pk}, **HX)

        content = response.content.decode()
        assert response.status_code == 200
        assert (
            content.lstrip().startswith('<div id="vendor-field"') or 'id="vendor-field"' in content
        )
        assert 'Vetted' in content and 'Mark as vetted' not in content
        vendor.refresh_from_db()
        assert vendor.vetted_at is not None

    def test_a_receipt_id_that_is_not_one_is_a_404(self, web, funded_user):
        vendor = create_vendor(funded_user, 'ACME')

        response = web.post(url('vendor-vet'), {'ids': [vendor.pk], 'receipt': 'nope'}, **HX)

        assert response.status_code == 404
        vendor.refresh_from_db()
        assert vendor.vetted_at is None

    def test_a_receipt_that_does_not_exist_is_a_404_and_vets_nothing(self, web, funded_user):
        vendor = create_vendor(funded_user, 'ACME')

        response = web.post(
            url('vendor-vet'),
            {'ids': [vendor.pk], 'receipt': '6f1e0f9e-0000-4000-8000-000000000000'},
            **HX,
        )

        assert response.status_code == 404
        vendor.refresh_from_db()
        assert vendor.vetted_at is None

    def test_the_receipt_has_to_be_the_users(self, web, funded_user, other_user, fake_storage):
        vendor = create_vendor(funded_user, 'ACME')
        theirs = create_receipt(other_user, fake_storage, status=ReceiptStatus.PROCESSED)

        response = web.post(url('vendor-vet'), {'ids': [vendor.pk], 'receipt': theirs.pk}, **HX)

        assert response.status_code == 404
        vendor.refresh_from_db()
        assert vendor.vetted_at is None  # a 404 means nothing happened


class TestSuggest:
    def test_vetted_first_then_unvetted_with_the_match_in_bold(self, web, funded_user):
        create_vendor(funded_user, 'ACME', vetted=True)
        create_vendor(funded_user, 'Account Fees Ltd', vetted=True)
        create_vendor(funded_user, 'Acme Coffee Roasters')

        content = web.get(url('vendor-suggest'), {'q': 'ac'}, **HX).content.decode()

        assert content.index('Vetted') < content.index('Not vetted yet')
        assert '<strong>AC</strong>ME' in content and '<strong>Ac</strong>count Fees' in content
        assert '<strong>Ac</strong>me Coffee Roasters' in content
        assert 'data-vendor-name="ACME"' in content
        assert 'Use &ldquo;ac&rdquo; as a new vendor' in content

    def test_the_field_sends_its_text_as_vendor(self, web, funded_user):
        create_vendor(funded_user, 'Blue Bottle', vetted=True)

        content = web.get(url('vendor-suggest'), {'vendor': 'blue'}).content.decode()

        assert 'Blue Bottle' in content

    def test_no_new_vendor_entry_when_one_has_that_name(self, web, funded_user):
        create_vendor(funded_user, 'Acme', vetted=True)

        content = web.get(url('vendor-suggest'), {'q': 'acme'}).content.decode()

        assert 'as a new vendor' not in content

    def test_an_empty_box_lists_the_users_vendors_and_offers_nothing_new(self, web, funded_user):
        create_vendor(funded_user, 'Blue Bottle', vetted=True)

        content = web.get(url('vendor-suggest')).content.decode()

        assert 'Blue Bottle' in content and 'as a new vendor' not in content

    def test_it_matches_the_names_a_vendor_is_read_as(self, web, acme):
        content = web.get(url('vendor-suggest'), {'q': 'oakland'}).content.decode()

        assert 'data-vendor-name="ACME"' in content

    def test_only_the_users_own_vendors(self, web, other_user):
        create_vendor(other_user, 'Theirs Inc', vetted=True)

        content = web.get(url('vendor-suggest'), {'q': 'theirs'}).content.decode()

        assert 'data-vendor-name="Theirs Inc"' not in content
        assert 'Use &ldquo;theirs&rdquo; as a new vendor' in content

    def test_text_is_escaped(self, web, funded_user):
        create_vendor(funded_user, '<b>x</b>', vetted=True)

        content = web.get(url('vendor-suggest'), {'q': '<b>'}).content.decode()

        assert '<b>x' not in content and '&lt;b&gt;' in content


class TestRename:
    def test_the_dialog_is_a_fragment_for_htmx(self, web, acme):
        response = web.get(url('vendor-rename', acme.pk), {'next': url('vendors')}, **HX)
        content = response.content.decode()

        assert response.status_code == 200
        assert 'x-data="fragmentModal"' in content and 'role="dialog"' in content
        assert 'name="name" type="text" value="ACME"' in content
        assert 'All 2 receipts from ACME will show the new name.' in content
        assert (
            '&ldquo;ACME&rdquo;, &ldquo;ACME Industries, LLC&rdquo; or &ldquo;Acme Ind. Oakland&rdquo;'
            in (content)
        )
        assert f'name="next" value="{url("vendors")}"' in content
        assert 'name="vetted" value="1" checked' in content

    def test_opened_directly_it_goes_to_the_page(self, web, acme):
        response = web.get(url('vendor-rename', acme.pk))

        assert response.status_code == 302 and response['Location'] == url('vendors')

    def test_renaming_updates_every_receipt_and_goes_back_with_a_flash(self, web, acme):
        response = web.post(
            url('vendor-rename', acme.pk),
            {'name': 'ACME, LTD', 'vetted': '1', 'next': '/receipts/vendors/?status=vetted'},
            **HX,
        )

        assert response.status_code == 200
        assert response['HX-Redirect'] == '/receipts/vendors/?status=vetted'
        assert flashed(response) == ['Renamed to ACME, LTD.']
        acme.refresh_from_db()
        assert acme.name == 'ACME, LTD'
        assert set(Receipt.objects.filter(vendor_ref=acme).values_list('vendor', flat=True)) == {
            'ACME, LTD'
        }

    def test_without_htmx_it_is_a_redirect(self, web, acme):
        response = web.post(url('vendor-rename', acme.pk), {'name': 'ACME, LTD', 'vetted': '1'})

        assert response.status_code == 302 and response['Location'] == url('vendors')

    def test_unchecking_vetted_un_vets_it(self, web, acme):
        web.post(url('vendor-rename', acme.pk), {'name': 'ACME, LTD'}, **HX)

        acme.refresh_from_db()
        assert acme.vetted_at is None

    def test_next_can_only_be_a_page_of_this_site(self, web, acme):
        response = web.post(
            url('vendor-rename', acme.pk),
            {'name': 'ACME, LTD', 'vetted': '1', 'next': 'https://evil.example/phish'},
            **HX,
        )

        assert response['HX-Redirect'] == url('vendors')

    def test_a_blank_name_comes_back_in_the_form_with_a_422(self, web, acme):
        response = web.post(url('vendor-rename', acme.pk), {'name': '  '}, **HX)

        content = response.content.decode()
        assert response.status_code == 422
        assert 'Enter a vendor name' in content and 'role="alert"' in content
        assert content.lstrip().startswith('<form')
        acme.refresh_from_db()
        assert acme.name == 'ACME'

    def test_a_name_that_is_too_long_is_refused(self, web, acme):
        response = web.post(url('vendor-rename', acme.pk), {'name': 'x' * 300}, **HX)

        assert response.status_code == 422
        assert '255' in response.content.decode()

    def test_a_name_another_vendor_has_offers_the_merge(self, web, acme, funded_user, make):
        other = create_vendor(funded_user, 'Blue Bottle Coffee, Inc.')
        mine = create_vendor(funded_user, 'Blue Bottle')
        make(vendor='Blue Bottle Coffee, Inc.', vendor_ref=other)
        make(vendor='Blue Bottle', vendor_ref=mine)

        response = web.post(
            url('vendor-rename', mine.pk),
            {'name': 'Blue Bottle Coffee', 'vetted': '1', 'next': url('vendors')},
            **HX,
        )

        content = response.content.decode()
        assert response.status_code == 422
        assert (
            'You already have a vendor called <strong>Blue Bottle Coffee, Inc.</strong> (1 receipt)'
            in content
        )
        assert 'Merge <strong>Blue Bottle</strong> into it? Its 1 receipt will show' in content
        assert f'action="{url("vendor-merge")}"' in content
        assert f'name="keep" value="{other.pk}"' in content
        assert (
            f'name="ids" value="{mine.pk}"' in content
            and f'name="ids" value="{other.pk}"' in content
        )
        assert 'Merge into Blue Bottle Coffee, Inc.' in content
        mine.refresh_from_db()
        assert mine.name == 'Blue Bottle'

    def test_use_a_different_name_does_not_inherit_the_merge_forms_disabled_elt(
        self, web, funded_user
    ):
        """The "Use a different name" button sits inside the merge form, whose
        `hx-disabled-elt="find button[type='submit']"` it would inherit; with no such button inside
        itself htmx 2.0 throws and the click does nothing (E2E run #152, RCPT-18)."""
        create_vendor(funded_user, 'Blue Bottle Coffee, Inc.')
        mine = create_vendor(funded_user, 'Blue Bottle')

        response = web.post(
            url('vendor-rename', mine.pk),
            {'name': 'Blue Bottle Coffee', 'next': url('vendors')},
            **HX,
        )

        content = response.content.decode()
        button = content[content.index(f'hx-get="{url("vendor-rename", mine.pk)}') :]
        button = button[: button.index('>')]
        assert 'hx-disabled-elt="unset"' in button

    def test_without_htmx_a_clash_is_a_flash_and_a_redirect(self, web, funded_user):
        create_vendor(funded_user, 'Blue Bottle Coffee, Inc.')
        mine = create_vendor(funded_user, 'Blue Bottle')

        response = web.post(url('vendor-rename', mine.pk), {'name': 'Blue Bottle Coffee'})

        assert response.status_code == 302
        assert flashed(response) == ['You already have a vendor called Blue Bottle Coffee, Inc..']


class TestMerge:
    @pytest.fixture
    def trio(self, funded_user, make, acme):
        one = create_vendor(funded_user, 'ACME Industrial Supply')
        two = create_vendor(funded_user, 'A.C.M.E. Ind.')
        make(vendor=one.name, vendor_ref=one)
        make(vendor=one.name, vendor_ref=one)
        make(vendor=two.name, vendor_ref=two)
        return [acme, one, two]

    def test_the_dialog_asks_which_name_to_keep(self, web, trio):
        acme, one, two = trio

        response = web.get(
            url('vendor-merge'),
            {'ids': [one.pk, two.pk, acme.pk], 'next': url('vendors')},
            **HX,
        )
        content = response.content.decode()

        assert response.status_code == 200
        assert 'Merge 3 vendors' in content and 'Which name should the 5 receipts show?' in content
        # The vetted one with the most receipts is the default.
        assert f'name="keep" value="{acme.pk}" checked' in content
        assert f'name="keep" value="{one.pk}" checked' not in content
        assert 'Merge into <span x-text="keep">' in content
        assert 'data-keep="ACME"' in content

    def test_one_vendor_can_be_merged_into_another(self, web, trio):
        acme, one, _ = trio

        content = web.get(url('vendor-merge'), {'from': one.pk}, **HX).content.decode()

        assert 'Merge ACME Industrial Supply into another vendor' in content
        assert '<select id="vendor-merge-keep" name="keep"' in content
        assert (
            'ACME (vetted) &middot; 2 receipts' in content
            or 'ACME (vetted) · 2 receipts' in content
        )
        assert f'<option value="{one.pk}"' not in content  # not into itself
        assert 'name="mode" value="into"' in content

    @pytest.mark.parametrize('query', ['ids=1', 'from=999999', 'from=abc', ''])
    def test_a_selection_that_cannot_be_merged_is_a_404(self, web, query):
        assert web.get(f'{url("vendor-merge")}?{query}', **HX).status_code == 404

    def test_other_users_vendors_cannot_be_opened(self, web, acme, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        response = web.get(url('vendor-merge'), {'ids': [acme.pk, theirs.pk]}, **HX)

        assert response.status_code == 404

    def test_merging_moves_the_receipts_and_goes_back_with_a_flash(self, web, trio):
        acme, one, two = trio

        response = web.post(
            url('vendor-merge'),
            {'keep': acme.pk, 'ids': [acme.pk, one.pk, two.pk], 'next': url('vendors')},
            **HX,
        )

        assert response.status_code == 200 and response['HX-Redirect'] == url('vendors')
        assert flashed(response) == ['Merged 2 vendors into ACME. 5 receipts now show ACME.']
        assert set(Receipt.objects.filter(vendor_ref=acme).values_list('vendor', flat=True)) == {
            'ACME'
        }
        assert Receipt.objects.filter(vendor_ref=acme).count() == 5
        assert not Vendor.objects.filter(pk__in=[one.pk, two.pk]).exists()

    def test_merging_one_vendor_into_another(self, web, trio):
        acme, one, _ = trio

        response = web.post(
            url('vendor-merge'),
            {'mode': 'into', 'ids': [one.pk], 'keep': acme.pk},
            **HX,
        )

        assert response.status_code == 200
        assert not Vendor.objects.filter(pk=one.pk).exists()
        assert Receipt.objects.filter(vendor_ref=acme).count() == 4

    def test_the_rename_clash_offer_posts_the_same_thing(self, web, funded_user, make):
        other = create_vendor(funded_user, 'Blue Bottle Coffee, Inc.')
        mine = create_vendor(funded_user, 'Blue Bottle')
        receipt = make(vendor='Blue Bottle', vendor_ref=mine)

        response = web.post(
            url('vendor-merge'), {'keep': other.pk, 'ids': [mine.pk, other.pk]}, **HX
        )

        assert response.status_code == 200
        receipt.refresh_from_db()
        assert receipt.vendor_ref == other and receipt.vendor == 'Blue Bottle Coffee, Inc.'

    def test_a_refused_merge_comes_back_in_the_form_with_a_422(self, web, trio):
        acme, one, two = trio

        response = web.post(url('vendor-merge'), {'ids': [acme.pk, one.pk, two.pk]}, **HX)

        content = response.content.decode()
        assert response.status_code == 422
        assert 'Choose which vendor to keep.' in content and 'Merge 3 vendors' in content
        assert Vendor.objects.filter(pk__in=[acme.pk, one.pk, two.pk]).count() == 3

    def test_another_users_vendor_is_refused(self, web, acme, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        response = web.post(
            url('vendor-merge'), {'keep': acme.pk, 'ids': [acme.pk, theirs.pk]}, **HX
        )

        assert response.status_code == 422
        assert Vendor.objects.filter(pk=theirs.pk).exists()

    def test_merging_nothing_is_refused(self, web, acme):
        response = web.post(url('vendor-merge'), {'keep': acme.pk, 'ids': [acme.pk]}, **HX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#vendor-msg'

    def test_without_htmx_it_is_a_redirect_with_a_flash(self, web, trio):
        acme, one, _ = trio

        response = web.post(url('vendor-merge'), {'keep': acme.pk, 'ids': [acme.pk, one.pk]})

        assert response.status_code == 302
        assert 'Merged 1 vendor into ACME.' in flashed(response)[0]


class TestDelete:
    def test_an_empty_vendor_is_deleted(self, web, funded_user):
        vendor = create_vendor(funded_user, 'Empty Shop')

        response = web.post(url('vendor-delete', vendor.pk), {'next': url('vendors')})

        assert response.status_code == 302 and response['Location'] == url('vendors')
        assert flashed(response) == ['Deleted Empty Shop.']
        assert not Vendor.objects.filter(pk=vendor.pk).exists()
        assert not VendorAlias.objects.filter(user=funded_user).exists()

    def test_a_vendor_with_receipts_is_kept_and_the_reason_flashed(self, web, acme):
        response = web.post(url('vendor-delete', acme.pk))

        assert response.status_code == 302
        assert 'Merge it into another vendor' in flashed(response)[0]
        assert Vendor.objects.filter(pk=acme.pk).exists()

    def test_a_vendor_with_a_receipt_in_the_trash_is_kept(self, web, funded_user, make):
        vendor = create_vendor(funded_user, 'ACME')
        make(vendor='ACME', vendor_ref=vendor, deleted_at=timezone.now())

        web.post(url('vendor-delete', vendor.pk))

        assert Vendor.objects.filter(pk=vendor.pk).exists()

    def test_it_is_post_only(self, web, funded_user):
        vendor = create_vendor(funded_user, 'Empty Shop')

        assert web.get(url('vendor-delete', vendor.pk)).status_code == 405


class TestDashboardFilter:
    def test_the_vendor_chip_and_the_filtered_list(self, web, acme, funded_user, make):
        make(vendor='Staples', vendor_ref=create_vendor(funded_user, 'Staples'))

        response = web.get(url('dashboard'), {'vendor': acme.pk})
        content = response.content.decode()

        assert 'Vendor: <strong class="font-semibold">ACME</strong>' in content
        assert 'aria-label="Remove the vendor filter"' in content
        assert f'<input type="hidden" name="vendor" value="{acme.pk}">' in content
        assert content.count('<li id="receipt-') == 2
        assert 'Staples' not in content.split('id="receipt-list"')[1]

    def test_the_chip_removes_only_the_vendor(self, web, acme):
        content = web.get(url('dashboard'), {'vendor': acme.pk, 'q': 'x'}).content.decode()

        assert f'hx-get="{url("list")}?q=x"' in content

    def test_it_travels_with_the_tabs_the_export_and_the_pager(self, web, acme):
        content = web.get(url('dashboard'), {'vendor': acme.pk}).content.decode()

        assert f'status=needs_review&amp;vendor={acme.pk}' in content or (
            f'vendor={acme.pk}&amp;status=needs_review' in content
        )
        assert f'{url("export")}?vendor={acme.pk}' in content

    def test_the_list_partial_keeps_it(self, web, acme):
        response = web.get(url('list'), {'vendor': acme.pk}, **HX)

        assert response.status_code == 200
        assert response['HX-Push-Url'] == f'{url("dashboard")}?vendor={acme.pk}'
        assert 'Vendor: <strong' in response.content.decode()

    def test_a_vendor_that_is_not_the_users_is_dropped(self, web, acme, other_user, make):
        theirs = create_vendor(other_user, 'Theirs')

        response = web.get(url('dashboard'), {'vendor': theirs.pk})
        content = response.content.decode()

        assert 'Vendor: <strong' not in content
        assert content.count('<li id="receipt-') == 2
        assert 'vendor=' not in content.split('id="export-link"')[1].split('>')[0]

    def test_a_vendor_that_does_not_exist_is_dropped(self, web, acme):
        content = web.get(url('dashboard'), {'vendor': '999999', 'q': 'x'}).content.decode()

        assert 'Vendor: <strong' not in content

    @pytest.mark.parametrize('value', ['abc', '-1', '1.5', ''])
    def test_a_junk_vendor_is_ignored(self, web, acme, value):
        response = web.get(url('dashboard'), {'vendor': value})

        assert response.status_code == 200
        assert 'Vendor: <strong' not in response.content.decode()

    def test_the_export_takes_the_filter(self, web, acme, funded_user, make):
        make(vendor='Staples', vendor_ref=create_vendor(funded_user, 'Staples'))

        response = web.get(url('export'), {'vendor': acme.pk})

        rows = list(csv.DictReader(io.StringIO(b''.join(response.streaming_content).decode())))
        assert {row['vendor'] for row in rows} == {'ACME'} and len(rows) == 2


class TestReceiptPage:
    def field(self, web, receipt) -> str:
        content = web.get(url('detail', receipt.pk)).content.decode()
        return content.split('id="vendor-field"')[1].split('id="id_receipt_date"')[0]

    def test_a_vetted_vendor(self, web, acme, make):
        receipt = make(
            vendor='ACME',
            vendor_ref=acme,
            vendor_extracted='ACME Industries, LLC',
            amount=Decimal('275.50'),
        )

        field = self.field(web, receipt)

        assert 'value="ACME"' in field and f'name="vendor_id" value="{acme.pk}"' in field
        assert 'Vetted' in field
        assert 'role="combobox"' in field and url('vendor-suggest') in field
        assert 'Rename vendor' in field and url('vendor-rename', acme.pk) in field
        assert f'next={url("detail", receipt.pk).replace("/", "%2F")}' in field
        assert 'Read on the receipt as' in field and 'ACME Industries, LLC' in field
        assert 'Filed under your vendor ACME (3 receipts)' in field
        assert 'Mark as vetted' not in field
        assert 'data-extracted="ACME Industries, LLC"' in field

    def test_the_hint_promises_teaching_the_name_the_ai_read(self, web, funded_user, make):
        vendor = create_vendor(funded_user, 'Hetzner Online GmbH')
        receipt = make(vendor=vendor.name, vendor_ref=vendor, vendor_extracted='Hetzner')

        field = self.field(web, receipt)

        assert 'On save, future receipts read as' in field and 'x-show="showHint"' in field

    def test_there_is_no_hint_when_a_vetted_vendor_keeps_the_name_the_ai_read(
        self, web, funded_user, make
    ):
        # Staples keeps "Staples", whichever vendor this receipt is moved to: nothing to promise.
        staples = create_vendor(funded_user, 'Staples', vetted=True)
        receipt = make(vendor='Staples', vendor_ref=staples, vendor_extracted='STAPLES INC')

        field = self.field(web, receipt)

        assert 'future receipts read as' not in field and 'showHint' not in field

    def test_a_new_vendor_offers_to_mark_it_vetted(self, web, funded_user, make):
        vendor = create_vendor(funded_user, 'ACME Industries, LLC')
        receipt = make(
            vendor=vendor.name, vendor_ref=vendor, vendor_extracted='ACME Industries, LLC'
        )

        field = self.field(web, receipt)

        assert 'New vendor' in field and 'First receipt from this vendor.' in field
        assert 'Mark as vetted' in field and url('vendor-vet') in field
        assert f'"ids": "{vendor.pk}"' in field and f'"receipt": "{receipt.pk}"' in field
        assert 'Read on the receipt as' not in field  # it is the vendor's own name

    def test_a_new_vendor_with_several_receipts_says_how_many(self, web, funded_user, make):
        vendor = create_vendor(funded_user, 'Hetzner')
        make(vendor='Hetzner', vendor_ref=vendor)
        receipt = make(vendor='Hetzner', vendor_ref=vendor)

        field = self.field(web, receipt)

        assert '2 receipts from this vendor so far.' in field

    def test_the_name_read_is_not_repeated_when_it_only_differs_in_case(self, web, acme, make):
        receipt = make(vendor='ACME', vendor_ref=acme, vendor_extracted='acme')

        assert 'Read on the receipt as' not in self.field(web, receipt)

    def test_a_receipt_without_a_vendor_has_no_chip_or_rename(self, web, make):
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW)

        field = self.field(web, receipt)

        assert "couldn't read the vendor" in field and 'border-amber-600' in field
        assert 'Rename vendor' not in field and 'Mark as vetted' not in field
        assert 'name="vendor_id" value=""' in field

    def test_a_trashed_receipt_cannot_rename_or_vet(self, web, funded_user, make):
        vendor = create_vendor(funded_user, 'Hetzner')
        receipt = make(vendor='Hetzner', vendor_ref=vendor, deleted_at=timezone.now())

        field = self.field(web, receipt)

        assert 'Rename vendor' not in field and 'Mark as vetted' not in field

    def test_the_page_loads_the_combobox_and_has_a_host_for_the_dialog(self, web, acme, make):
        receipt = make(vendor='ACME', vendor_ref=acme)

        content = web.get(url('detail', receipt.pk)).content.decode()

        assert 'receipts/js/vendor_combobox.js' in content and 'id="vendor-modal"' in content

    def test_a_name_with_html_in_it_is_escaped_in_the_field(self, web, funded_user, make):
        vendor = create_vendor(funded_user, '"><script>alert(1)</script>')
        receipt = make(vendor=vendor.name, vendor_ref=vendor, vendor_extracted='<i>x</i>')

        content = web.get(url('detail', receipt.pk)).content.decode()

        assert '<script>alert(1)' not in content and '<i>x</i>' not in content


class TestSavingTheVendorField:
    def post(self, web, receipt, **fields):
        data = {
            'vendor': receipt.vendor,
            'vendor_id': receipt.vendor_ref_id or '',
            'receipt_date': receipt.receipt_date.isoformat() if receipt.receipt_date else '',
            'amount': '' if receipt.amount is None else str(receipt.amount),
            'currency': receipt.currency,
        }
        data.update(fields)
        return web.post(url('detail', receipt.pk), data)

    @pytest.fixture
    def receipt(self, funded_user, make):
        vendor = create_vendor(funded_user, 'ACME Industries, LLC')
        return make(
            vendor=vendor.name,
            vendor_ref=vendor,
            vendor_extracted='ACME Industries, LLC',
            amount=Decimal('1'),
            receipt_date=date(2026, 10, 9),
        )

    def test_saving_without_touching_the_vendor_does_not_vet_it(self, web, receipt):
        response = self.post(web, receipt, amount='2.00')

        assert response.status_code == 302
        receipt.refresh_from_db()
        assert receipt.vendor_ref.vetted_at is None
        assert receipt.user_edited_fields == ['amount']

    def test_a_picked_vendor_is_sent_by_id(self, web, receipt, funded_user):
        chosen = create_vendor(funded_user, 'ACME', vetted=True)

        self.post(web, receipt, vendor='ACME', vendor_id=str(chosen.pk))

        receipt.refresh_from_db()
        assert receipt.vendor_ref == chosen and receipt.vendor == 'ACME'

    def test_a_typed_name_wins_over_a_stale_id(self, web, receipt, funded_user):
        chosen = create_vendor(funded_user, 'ACME', vetted=True)

        self.post(web, receipt, vendor='Totally Different', vendor_id=str(chosen.pk))

        receipt.refresh_from_db()
        assert receipt.vendor == 'Totally Different' and receipt.vendor_ref != chosen
        assert receipt.vendor_ref.vetted_at is not None

    def test_another_users_id_falls_back_to_the_typed_name(self, web, receipt, other_user):
        theirs = create_vendor(other_user, 'Theirs', vetted=True)

        self.post(web, receipt, vendor='Theirs', vendor_id=str(theirs.pk))

        receipt.refresh_from_db()
        assert receipt.vendor == 'Theirs' and receipt.vendor_ref != theirs
        assert receipt.vendor_ref.user_id == receipt.user_id

    def test_a_junk_id_is_ignored(self, web, receipt):
        response = self.post(web, receipt, vendor='ACME', vendor_id='abc')

        assert response.status_code == 302
        receipt.refresh_from_db()
        assert receipt.vendor == 'ACME'

    def test_clearing_the_field_clears_the_vendor(self, web, receipt):
        self.post(web, receipt, vendor='', vendor_id='')

        receipt.refresh_from_db()
        assert receipt.vendor == '' and receipt.vendor_ref is None
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW

    def test_a_typed_name_teaches_the_name_the_ai_read(self, web, receipt, make, fake_ai):
        from ..services import processing
        from .fakes import sample_extraction

        self.post(web, receipt, vendor='ACME', vendor_id='')
        fake_ai.default = sample_extraction(vendor='ACME Industries, LLC')
        again = make(status=ReceiptStatus.RECEIVED)
        processing.process(str(again.pk))

        again.refresh_from_db()
        receipt.refresh_from_db()
        assert again.vendor_ref == receipt.vendor_ref and again.vendor == 'ACME'
