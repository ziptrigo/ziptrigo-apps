"""Linking receipts that have a vendor text and no vendor (`services.vendors.link_unlinked_receipts`),
the `receipts.link_vendors` job and `./manage.py receipts_link_vendors`."""

import logging
from io import StringIO

import pytest
from django.core.management import call_command
from django.db.models import QuerySet
from django.utils import timezone

from .. import jobs
from ..models import Receipt, ReceiptStatus, Vendor, VendorAlias, VendorAliasSource
from ..services import vendors
from ..services.vendors import (
    link_unlinked_receipts,
    unlinked_receipts,
    users_with_unlinked_receipts,
)
from .factories import create_receipt, create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(vendor='', *, user=None, **fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        return create_receipt(user or funded_user, fake_storage, vendor=vendor, **fields)

    return _make


def reload(receipt: Receipt) -> Receipt:
    receipt.refresh_from_db()
    return receipt


class TestLinkUnlinkedReceipts:
    def test_a_receipt_is_linked_to_the_vendor_called_that_ignoring_case(self, funded_user, make):
        staples = create_vendor(funded_user, 'Staples', vetted=True)
        receipt = make('STAPLES')

        assert link_unlinked_receipts() == 1

        receipt = reload(receipt)
        assert receipt.vendor_ref == staples
        assert receipt.vendor == 'STAPLES'  # the text is never rewritten

    def test_without_a_vendor_called_that_an_unvetted_one_is_made_with_its_alias(
        self, funded_user, make
    ):
        receipts = [make('blue bottle coffee, inc.'), make('Blue Bottle Coffee, Inc.')]
        receipts.append(make('blue bottle coffee, inc.'))

        assert link_unlinked_receipts() == 3

        vendor = Vendor.objects.get(user=funded_user)
        # (Named by the commonest spelling.)
        assert vendor.name == 'blue bottle coffee, inc.' and vendor.vetted_at is None
        assert {reload(r).vendor_ref for r in receipts} == {vendor}
        [alias] = VendorAlias.objects.filter(vendor=vendor)
        assert alias.key == 'blue bottle coffee' and alias.source == VendorAliasSource.EXTRACTED

    def test_it_never_picks_a_vendor_by_key_or_near_match(self, funded_user, make):
        # `Staples Inc` shares the key of the vetted `Staples`, and `Acme Industires` is a near
        # match of `Acme Industries`: matching would file both there, which is retroactive.
        staples = create_vendor(funded_user, 'Staples', vetted=True)
        acme = create_vendor(funded_user, 'Acme Industries', vetted=True)
        inc = make('Staples Inc')
        typo = make('Acme Industires')

        link_unlinked_receipts()

        inc, typo = reload(inc), reload(typo)
        assert inc.vendor_ref not in (None, staples) and inc.vendor_ref.name == 'Staples Inc'
        assert typo.vendor_ref not in (None, acme) and typo.vendor_ref.name == 'Acme Industires'
        assert (inc.vendor, typo.vendor) == ('Staples Inc', 'Acme Industires')
        # The key is Staples', so the new vendor has no alias for it.
        assert not VendorAlias.objects.filter(vendor=inc.vendor_ref).exists()

    def test_a_vendor_without_the_alias_for_its_name_gets_it(self, funded_user, make):
        leftover = Vendor.objects.create(user=funded_user, name='Uber')
        make('uber')

        link_unlinked_receipts()

        assert VendorAlias.objects.get(vendor=leftover).key == 'uber'

    def test_live_and_trashed_receipts_are_linked(self, funded_user, make):
        live = make('Uber')
        trashed = make('Uber', deleted_at=timezone.now())

        assert link_unlinked_receipts() == 2

        assert reload(live).vendor_ref == reload(trashed).vendor_ref != None  # noqa: E711

    def test_only_receipts_with_a_text_and_no_vendor_are_touched(self, funded_user, make):
        uber = create_vendor(funded_user, 'Uber')
        linked = make('Uber', vendor_ref=uber)
        elsewhere = make('Lyft', vendor_ref=uber)  # text and link disagree: not ours to fix
        nothing = make('')
        before = [reload(r).updated_at for r in (linked, elsewhere, nothing)]

        assert link_unlinked_receipts() == 0

        assert [reload(r).updated_at for r in (linked, elsewhere, nothing)] == before
        assert reload(elsewhere).vendor_ref == uber and reload(nothing).vendor_ref is None
        assert Vendor.objects.count() == 1

    def test_it_is_not_an_edit(self, make):
        receipt = make('Uber')
        before = receipt.updated_at

        link_unlinked_receipts()

        assert reload(receipt).updated_at == before

    def test_a_second_run_does_nothing(self, funded_user, make):
        make('Uber')
        make('Lyft')

        assert link_unlinked_receipts() == 2
        assert link_unlinked_receipts() == 0
        assert Vendor.objects.count() == 2

    def test_a_text_that_is_only_whitespace_is_skipped(self, funded_user, make):
        blank = make('  \t ')
        other = make('Uber')

        assert link_unlinked_receipts() == 1

        assert reload(blank).vendor_ref is None and reload(other).vendor_ref is not None
        assert Vendor.objects.count() == 1

    def test_extra_whitespace_in_the_text_names_the_same_vendor(self, funded_user, make):
        spaced = make('  Blue   Bottle ')
        plain = make('Blue Bottle')

        assert link_unlinked_receipts() == 2

        assert reload(spaced).vendor_ref == reload(plain).vendor_ref
        assert reload(spaced).vendor == '  Blue   Bottle '

    def test_each_users_receipts_get_that_users_vendor(self, funded_user, other_user, make):
        mine = create_vendor(funded_user, 'Uber', vetted=True)
        a, b = make('Uber'), make('Uber', user=other_user)

        link_unlinked_receipts()

        assert reload(a).vendor_ref == mine
        assert reload(b).vendor_ref != mine
        assert Vendor.objects.get(name='Uber', user=other_user) == reload(b).vendor_ref

    def test_it_can_be_limited_to_one_user(self, funded_user, other_user, make):
        mine, theirs = make('Uber'), make('Uber', user=other_user)

        assert link_unlinked_receipts(funded_user.pk) == 1

        assert reload(mine).vendor_ref is not None and reload(theirs).vendor_ref is None

    def test_it_reads_in_batches(self, funded_user, make):
        receipts = [make(f'Shop {n % 3}') for n in range(7)]

        assert link_unlinked_receipts(batch_size=2) == 7

        assert all(reload(r).vendor_ref is not None for r in receipts)
        assert Vendor.objects.count() == 3

    def test_the_users_and_the_receipts_to_link(self, funded_user, other_user, make):
        make('Uber')
        make('Lyft')
        make('Uber', user=other_user)
        make('', user=other_user)

        assert set(users_with_unlinked_receipts()) == {funded_user.pk, other_user.pk}
        assert unlinked_receipts(funded_user.pk).count() == 2

    def test_losing_a_race_to_create_the_vendor_uses_the_winners(
        self, funded_user, make, monkeypatch
    ):
        winner = create_vendor(funded_user, 'Staples')
        receipt = make('Staples')
        real_first = QuerySet.first
        calls = []

        def stale_first(self):  # the first lookup runs before the winner exists
            calls.append(1)
            return None if len(calls) == 1 else real_first(self)

        monkeypatch.setattr(QuerySet, 'first', stale_first)

        assert link_unlinked_receipts() == 1

        assert reload(receipt).vendor_ref == winner
        assert Vendor.objects.count() == 1

    def test_a_receipt_filed_meanwhile_is_left_alone_and_no_orphan_is_kept(
        self, funded_user, make, monkeypatch
    ):
        receipt = make('Staples')
        other = create_vendor(funded_user, 'Office Depot', vetted=True)
        real = vendors._vendor_called

        def processed_first(user_id, name):
            result = real(user_id, name)
            # Processing links it (to the vendor it matched) before our update runs.
            Receipt.objects.filter(pk=receipt.pk).update(vendor_ref=other)
            return result

        monkeypatch.setattr(vendors, '_vendor_called', processed_first)

        assert link_unlinked_receipts() == 0

        assert reload(receipt).vendor_ref == other
        assert not Vendor.objects.filter(name='Staples').exists()  # the unvetted orphan went


class TestJob:
    def test_it_links_everybodys_receipts(self, funded_user, other_user, make):
        a, b = make('Uber'), make('Lyft', user=other_user)

        jobs.link_vendors()

        assert reload(a).vendor_ref is not None and reload(b).vendor_ref is not None

    def test_it_is_one_query_once_everything_is_linked(
        self, funded_user, make, django_assert_num_queries
    ):
        jobs.link_vendors()  # (nothing yet)
        make('Uber')
        jobs.link_vendors()

        with django_assert_num_queries(1):
            jobs.link_vendors()

    def test_one_failing_user_does_not_stop_the_rest(
        self, funded_user, other_user, make, monkeypatch, caplog
    ):
        bad, good = make('Uber'), make('Lyft', user=other_user)
        real = vendors.link_unlinked_receipts

        def flaky(user_id, **kwargs):
            if user_id == funded_user.pk:
                raise RuntimeError('boom')
            return real(user_id, **kwargs)

        monkeypatch.setattr(vendors, 'link_unlinked_receipts', flaky)

        with caplog.at_level(logging.ERROR):
            jobs.link_vendors()

        assert reload(bad).vendor_ref is None and reload(good).vendor_ref is not None
        assert 'Failed to link vendors for user' in caplog.text


class TestCommand:
    def run(self) -> str:
        out = StringIO()
        call_command('receipts_link_vendors', stdout=out)
        return out.getvalue()

    def test_it_links_and_says_how_many(self, funded_user, other_user, make):
        receipts = [make('Uber'), make('Uber'), make('Lyft', user=other_user)]

        output = self.run()

        assert 'Linked 3 receipt(s) of 2 user(s)' in output
        assert all(reload(r).vendor_ref is not None for r in receipts)

    def test_running_it_again_is_harmless(self, make):
        make('Uber')
        self.run()

        assert 'Linked 0 receipt(s) of 0 user(s)' in self.run()
