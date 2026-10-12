"""Migration 0004: every existing receipt gets a vendor. Called directly with the real app registry
(the suite runs with `--no-migrations`)."""

from importlib import import_module

import pytest
from django.apps import apps
from django.utils import timezone

from ..models import Receipt, ReceiptStatus, Vendor, VendorAlias
from .factories import create_receipt

migration = import_module('apps.receipts.migrations.0004_backfill_vendors')

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(vendor='', *, read=None, user=None, edited=False, **fields):
        if read is not None:
            fields['ai_result'] = {'vendor': read}
        if edited:
            fields['user_edited_fields'] = ['vendor']
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        return create_receipt(user or funded_user, fake_storage, vendor=vendor, **fields)

    return _make


def backfill() -> None:
    migration.backfill_vendors(apps, None)


def vendor_of(receipt) -> Vendor:
    receipt.refresh_from_db()
    assert receipt.vendor_ref is not None
    return receipt.vendor_ref


def unlinked(receipt) -> bool:
    receipt.refresh_from_db()
    return receipt.vendor_ref is None


def keys(vendor) -> set[str]:
    return set(VendorAlias.objects.filter(vendor=vendor).values_list('key', flat=True))


def test_receipts_are_grouped_by_their_vendor_ignoring_case_and_spaces(make):
    receipts = [make('Staples'), make('STAPLES'), make(' staples '), make('Staples')]
    other = make('Uber')

    backfill()

    vendor = vendor_of(receipts[0])
    assert {vendor_of(r) for r in receipts} == {vendor}
    assert vendor_of(other) != vendor
    assert Vendor.objects.count() == 2


def test_the_most_common_spelling_names_the_vendor_and_the_text_follows(make):
    receipts = [make('STAPLES'), make('Staples'), make('Staples'), make('staples')]

    backfill()

    vendor = vendor_of(receipts[0])
    assert vendor.name == 'Staples'
    for receipt in receipts:
        receipt.refresh_from_db()
        assert receipt.vendor == 'Staples'


def test_a_tie_between_spellings_takes_the_first_alphabetically(make):
    make('b shop')
    make('B Shop')

    backfill()

    assert Vendor.objects.get().name == 'B Shop'


def test_only_the_text_that_differs_is_touched(make):
    same = [make('Staples', notes='keep', amount='42.10'), make('Staples')]
    differs = make('STAPLES')
    before = {r.pk: Receipt.objects.get(pk=r.pk).updated_at for r in [*same, differs]}

    backfill()

    for receipt in same:
        receipt.refresh_from_db()
        assert receipt.updated_at == before[receipt.pk]
        assert receipt.status == ReceiptStatus.PROCESSED
    assert same[0].notes == 'keep' and str(same[0].amount) == '42.10'
    differs.refresh_from_db()
    assert differs.vendor == 'Staples' and differs.vendor_ref is not None


def test_a_group_is_vetted_when_the_user_typed_the_vendor_on_any_of_its_receipts(make):
    typed = [make('ACME'), make('Acme', edited=True)]
    read = make('Staples', read='Staples')

    backfill()

    assert vendor_of(typed[0]).vetted_at is not None
    assert vendor_of(read).vetted_at is None


def test_the_name_the_ai_read_is_kept_on_each_receipt(make):
    with_read = make('ACME', read='ACME Industries, LLC')
    without = make('ACME')
    cleared = make('', read='Hetzner GmbH')
    nothing = make('')

    backfill()

    with_read.refresh_from_db()
    without.refresh_from_db()
    assert with_read.vendor_extracted == 'ACME Industries, LLC' and with_read.vendor == 'ACME'
    assert without.vendor_extracted == ''
    cleared.refresh_from_db()
    nothing.refresh_from_db()
    assert unlinked(cleared) and cleared.vendor == ''
    assert (
        cleared.vendor_extracted == 'Hetzner GmbH'
    )  # what the AI read, though the user cleared it
    assert unlinked(nothing) and nothing.vendor_extracted == ''


def test_a_vendor_gets_its_own_name_and_the_names_read_for_its_receipts_as_aliases(make):
    make('ACME', read='ACME Industries, LLC')
    make('ACME', read='Acme Ind. Oakland')

    backfill()

    vendor = Vendor.objects.get()
    assert keys(vendor) == {'acme', 'acme industries', 'acme ind oakland'}
    sources = dict(VendorAlias.objects.filter(vendor=vendor).values_list('key', 'source'))
    assert sources['acme'] == 'extracted' and sources['acme industries'] == 'extracted'


def test_a_vetted_vendors_own_name_is_a_user_alias(make):
    make('ACME', edited=True)

    backfill()

    assert VendorAlias.objects.get().source == 'user'


def test_aliases_are_claimed_by_vetted_groups_first_then_by_receipt_count(make):
    # "Acme Co" and "ACME" share the key `acme`; three other groups fight over `shared`.
    big = [make('Acme Co') for _ in range(3)]
    vetted = make('ACME', edited=True)
    first = [make('Shared Name', read='Shared'), make('Shared Name', read='Shared')]
    second = make('Shared', read='Shared')

    backfill()

    # The vetted group wins `acme` although it has the fewest receipts.
    assert 'acme' in keys(vendor_of(vetted))
    assert 'acme' not in keys(vendor_of(big[0]))
    # A group whose own key was taken stays a separate vendor.
    assert vendor_of(big[0]) != vendor_of(vetted)
    # `shared` goes to the group with more receipts (its own key `shared name` comes first, and
    # its read name `shared` is free); the other group's own key is then taken.
    assert 'shared' in keys(vendor_of(first[0]))
    assert 'shared' not in keys(vendor_of(second))


def test_an_unvetted_group_with_more_receipts_claims_before_a_smaller_one(make):
    big = [make('Acme Inc') for _ in range(3)]
    small = make('ACME')

    backfill()

    assert 'acme' in keys(vendor_of(big[0]))
    assert keys(vendor_of(small)) == set()
    assert vendor_of(small).name == 'ACME'


def test_every_alias_key_belongs_to_one_vendor_per_user(make, other_user):
    make('ACME', read='Acme Corp')
    make('Acme Corp', read='ACME')
    make('ACME', user=other_user)

    backfill()

    assert VendorAlias.objects.count() == len(set(VendorAlias.objects.values_list('user', 'key')))
    assert Vendor.objects.filter(user=other_user).count() == 1


def test_users_do_not_share_vendors(make, funded_user, other_user):
    mine = make('Staples')
    theirs = make('Staples', user=other_user)

    backfill()

    assert vendor_of(mine) != vendor_of(theirs)
    assert vendor_of(mine).user == funded_user and vendor_of(theirs).user == other_user


def test_trashed_receipts_are_included(make):
    trashed = make('Staples', deleted_at=timezone.now())

    backfill()

    assert not unlinked(trashed)


def test_many_receipts_are_updated_in_batches(make, monkeypatch):
    monkeypatch.setattr(migration, 'RECEIPT_BATCH', 2)
    monkeypatch.setattr(migration, 'USER_BATCH', 1)
    receipts = [make(f'Shop {n % 3}') for n in range(7)]

    backfill()

    assert not any(unlinked(r) for r in receipts)
    assert Vendor.objects.count() == 3


def test_a_database_without_vendors_is_left_alone():
    backfill()

    assert Vendor.objects.count() == 0


def test_reversing_clears_the_links_and_the_vendors(make):
    receipt = make('ACME', read='ACME Industries, LLC')
    backfill()
    assert vendor_of(receipt) is not None

    migration.clear_vendors(apps, None)

    receipt.refresh_from_db()
    assert receipt.vendor_ref is None and receipt.vendor_extracted == ''
    assert receipt.vendor == 'ACME'
    assert Vendor.objects.count() == 0 and VendorAlias.objects.count() == 0
