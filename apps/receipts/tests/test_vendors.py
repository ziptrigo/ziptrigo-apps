"""Vendors (`services/vendors.py`): the normalized key, matching what the AI read to a vendor, the
user's edits (move a receipt, rename, vet, merge, delete) and the lists the pages are built from."""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import Receipt, ReceiptStatus, Vendor, VendorAlias, VendorAliasSource
from ..services import vendors
from ..services.vendors import (
    MIN_NEAR_LENGTH,
    VendorNameTaken,
    assign_vendor,
    clean_name,
    clear_vendor,
    count_receipts,
    delete_vendor,
    drop_if_orphan,
    list_vendors,
    match_vendor,
    merge_vendors,
    normalize_key,
    rename_vendor,
    set_vetted,
    suggest_vendors,
    vendor_counts,
    with_alias_names,
)
from .factories import create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def alias_keys(vendor: Vendor) -> set[str]:
    return set(VendorAlias.objects.filter(vendor=vendor).values_list('key', flat=True))


class TestNormalizeKey:
    @pytest.mark.parametrize(
        'name',
        [
            'ACME Industries, LLC',
            'Acme Industries LLC',
            'ACME INDUSTRIES L.L.C.',
            'acme industries',
            'The Acme Industries Inc.',
            '  Acme   Industries ,  Ltd ',
            'ACME-Industries',
            'Acme Industries Co. Ltd',
        ],
    )
    def test_the_spellings_of_one_company_share_a_key(self, name):
        assert normalize_key(name) == 'acme industries'

    @pytest.mark.parametrize(
        ('name', 'key'),
        [
            ('Café Müller GmbH', 'cafe muller'),
            ('Hétzner Online GmbH', 'hetzner online'),
            ('AT&T Inc.', 'at and t'),
            ('Smith & Sons', 'smith and sons'),
            ("McDonald's Corp", 'mcdonalds'),
            ('McDonald’s', 'mcdonalds'),
            ('The Home Depot, Inc.', 'home depot'),
            ('Amazon.com, Inc.', 'amazoncom'),
            ('Ｓｔａｐｌｅｓ', 'staples'),
            ('Straße 7', 'strasse 7'),
            ('Uber BV', 'uber'),
            ('Café 24', 'cafe 24'),
        ],
    )
    def test_cases(self, name, key):
        assert normalize_key(name) == key

    @pytest.mark.parametrize(
        ('name', 'key'),
        [('The Co', 'co'), ('Inc', 'inc'), ('LLC', 'llc'), ('The', 'the'), ('Co. Ltd', 'co')],
    )
    def test_a_key_is_never_stripped_to_nothing(self, name, key):
        assert normalize_key(name) == key

    def test_a_name_without_letters_or_digits_keys_as_itself(self):
        assert normalize_key('---') == '---'
        assert normalize_key('  * * ') == '* *'

    def test_accents_are_only_stripped_from_ascii_letters(self):
        # Stripping a kana's dakuten would turn one name into another.
        assert normalize_key('ガス') != normalize_key('カス')

    def test_the_key_fits_the_column(self):
        assert len(normalize_key('ß' * 255)) == 255

    def test_clean_name_collapses_whitespace_and_drops_control_characters(self):
        assert clean_name('  Acme \t\n Industries\x00 ') == 'Acme Industries'
        assert clean_name(None) == ''


class TestMatchVendor:
    def test_no_name_is_no_vendor(self, funded_user):
        assert match_vendor(funded_user, None) is None
        assert match_vendor(funded_user, '   ') is None
        assert Vendor.objects.count() == 0

    def test_a_new_name_creates_an_unvetted_vendor_named_as_read(self, funded_user):
        vendor = match_vendor(funded_user, '  ACME   Industries, LLC ')

        assert vendor is not None
        assert vendor.name == 'ACME Industries, LLC'
        assert vendor.vetted_at is None and vendor.user == funded_user
        [alias] = VendorAlias.objects.filter(vendor=vendor)
        assert alias.key == 'acme industries'
        assert alias.name == 'ACME Industries, LLC'
        assert alias.source == VendorAliasSource.EXTRACTED

    def test_another_spelling_finds_the_same_vendor(self, funded_user):
        first = match_vendor(funded_user, 'ACME Industries, LLC')

        assert match_vendor(funded_user, 'Acme Industries LLC') == first
        assert match_vendor(funded_user, 'acme industries') == first
        assert Vendor.objects.count() == 1

    def test_a_vetted_vendor_by_alias(self, funded_user):
        acme = create_vendor(funded_user, 'ACME', vetted=True, aliases=('ACME Industries, LLC',))

        assert match_vendor(funded_user, 'Acme Industries LLC') == acme
        assert Vendor.objects.count() == 1

    def test_an_unvetted_vendor_by_alias(self, funded_user):
        previous = create_vendor(funded_user, 'ACME Industries, LLC')

        assert match_vendor(funded_user, 'ACME Industries LLC') == previous

    def test_a_vetted_near_match(self, funded_user):
        acme = create_vendor(funded_user, 'ACME Industries', vetted=True)

        assert match_vendor(funded_user, 'ACME Industires') == acme
        assert Vendor.objects.count() == 1

    def test_a_near_match_does_not_teach_the_alias(self, funded_user):
        acme = create_vendor(funded_user, 'ACME Industries', vetted=True)

        match_vendor(funded_user, 'ACME Industires')

        assert alias_keys(acme) == {'acme industries'}

    def test_there_is_no_near_match_on_an_unvetted_vendor(self, funded_user):
        previous = create_vendor(funded_user, 'ACME Industries')

        vendor = match_vendor(funded_user, 'ACME Industires')

        assert vendor is not None and vendor != previous
        assert vendor.vetted_at is None
        assert Vendor.objects.count() == 2

    def test_a_vetted_near_match_beats_an_unvetted_exact_one(self, funded_user):
        acme = create_vendor(funded_user, 'ACME Industries', vetted=True)
        create_vendor(funded_user, 'ACME Industires')  # an earlier misreading, never vetted

        assert match_vendor(funded_user, 'ACME Industires') == acme

    def test_a_vetted_exact_match_beats_a_near_match(self, funded_user):
        create_vendor(funded_user, 'ACME Industries', vetted=True)
        typo = create_vendor(funded_user, 'ACME Industires', vetted=True)

        assert match_vendor(funded_user, 'ACME Industires') == typo

    def test_several_close_vendors_are_not_guessed_between(self, funded_user):
        create_vendor(funded_user, 'Acme Industries', vetted=True)
        create_vendor(funded_user, 'Acme Industrias', vetted=True)

        vendor = match_vendor(funded_user, 'Acme Industriys')

        assert vendor is not None and vendor.vetted_at is None
        assert Vendor.objects.count() == 3

    def test_short_names_are_never_near_matched(self, funded_user):
        assert len('aldi') < MIN_NEAR_LENGTH
        create_vendor(funded_user, 'Aldi', vetted=True)

        vendor = match_vendor(funded_user, 'Aldo')

        assert vendor is not None and vendor.name == 'Aldo'

    def test_a_name_that_is_not_close_enough_creates_a_vendor(self, funded_user):
        create_vendor(funded_user, 'ACME Industries', vetted=True)

        vendor = match_vendor(funded_user, 'ACME Industrial Supply')

        assert vendor is not None and vendor.name == 'ACME Industrial Supply'

    def test_vendors_are_per_user(self, funded_user, other_user):
        theirs = create_vendor(other_user, 'ACME', vetted=True)

        mine = match_vendor(funded_user, 'ACME')

        assert mine is not None and mine != theirs and mine.user == funded_user
        assert match_vendor(other_user, 'Acme Inc') == theirs

    def test_a_vendor_called_that_with_no_alias_is_still_found(self, funded_user):
        # A backfill leaves the alias out when another group claimed the key first.
        orphan = Vendor.objects.create(user=funded_user, name='Acme Inc')

        assert match_vendor(funded_user, 'ACME INC') == orphan
        assert alias_keys(orphan) == {'acme'}

    def test_a_long_name_is_cut_to_the_column(self, funded_user):
        vendor = match_vendor(funded_user, 'x' * 400)

        assert vendor is not None and len(vendor.name) == 255

    def test_losing_a_race_to_create_the_same_vendor_uses_the_winners(
        self, funded_user, monkeypatch
    ):
        winner = create_vendor(funded_user, 'Staples')
        real_find = vendors._find
        calls = []

        def stale_find(user, name, key):
            calls.append(name)
            return None if len(calls) == 1 else real_find(user, name, key)

        monkeypatch.setattr(vendors, '_find', stale_find)

        assert match_vendor(funded_user, 'Staples') == winner
        assert Vendor.objects.count() == 1 and VendorAlias.objects.count() == 1

    def test_losing_a_race_on_a_key_alone_uses_the_winners(self, funded_user, monkeypatch):
        winner = create_vendor(funded_user, 'Staples, Inc')
        real_find = vendors._find
        calls = []

        def stale_find(user, name, key):
            calls.append(name)
            return None if len(calls) == 1 else real_find(user, name, key)

        monkeypatch.setattr(vendors, '_find', stale_find)

        # A different name (so the unique name constraint passes), the same key.
        assert match_vendor(funded_user, 'Staples Inc') == winner
        assert Vendor.objects.count() == 1


class TestAssignVendor:
    @pytest.fixture
    def receipt(self, make_receipt, funded_user):
        old = match_vendor(funded_user, 'ACME Industries, LLC')
        assert old is not None
        return make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor=old.name,
            vendor_ref=old,
            vendor_extracted='ACME Industries, LLC',
        )

    def test_a_new_name_creates_a_vetted_vendor_and_moves_the_receipt(self, receipt):
        old = receipt.vendor_ref

        vendor = assign_vendor(receipt, name='ACME')

        assert vendor.name == 'ACME' and vendor.vetted_at is not None
        receipt.refresh_from_db()
        assert receipt.vendor_ref == vendor and receipt.vendor == 'ACME'
        assert not Vendor.objects.filter(pk=old.pk).exists()  # unvetted and now empty

    def test_the_name_the_ai_read_now_points_at_the_new_vendor(self, receipt, funded_user):
        vendor = assign_vendor(receipt, name='ACME')

        assert alias_keys(vendor) == {'acme', 'acme industries'}
        alias = VendorAlias.objects.get(user=funded_user, key='acme industries')
        assert alias.vendor == vendor and alias.source == VendorAliasSource.USER
        assert alias.name == 'ACME Industries, LLC'
        assert match_vendor(funded_user, 'ACME Industries LLC') == vendor

    def test_a_vetted_vendor_the_receipt_leaves_is_kept(self, funded_user, make_receipt):
        keep = create_vendor(funded_user, 'ACME Industries, LLC', vetted=True)
        receipt = make_receipt(vendor=keep.name, vendor_ref=keep, vendor_extracted=keep.name)

        assign_vendor(receipt, name='ACME')

        assert Vendor.objects.filter(pk=keep.pk).exists()

    def test_an_unvetted_vendor_that_still_has_receipts_is_kept(self, receipt, make_receipt):
        old = receipt.vendor_ref
        make_receipt(vendor=old.name, vendor_ref=old)

        assign_vendor(receipt, name='ACME')

        assert Vendor.objects.filter(pk=old.pk).exists()

    def test_a_chosen_vendor_is_vetted(self, receipt, funded_user):
        chosen = create_vendor(funded_user, 'Blue Bottle')

        assign_vendor(receipt, vendor=chosen)

        chosen.refresh_from_db()
        assert chosen.vetted_at is not None
        receipt.refresh_from_db()
        assert receipt.vendor_ref == chosen and receipt.vendor == 'Blue Bottle'

    def test_a_name_that_is_a_vendors_name_or_alias_picks_it(self, receipt, funded_user):
        acme = create_vendor(funded_user, 'ACME', vetted=True, aliases=('Acme Corp Oakland',))

        assert assign_vendor(receipt, name='acme') == acme
        assert assign_vendor(receipt, name='Acme Corp Oakland, Inc.') == acme
        assert Vendor.objects.filter(user=funded_user, name__iexact='acme').count() == 1

    def test_an_unvetted_vendor_typed_in_becomes_vetted_under_its_own_name(
        self, funded_user, make_receipt
    ):
        unvetted = create_vendor(funded_user, 'Blue Bottle Coffee, Inc.')
        receipt = make_receipt()

        assign_vendor(receipt, name='blue bottle coffee')

        unvetted.refresh_from_db()
        assert unvetted.vetted_at is not None and unvetted.name == 'Blue Bottle Coffee, Inc.'
        receipt.refresh_from_db()
        assert receipt.vendor == 'Blue Bottle Coffee, Inc.'

    def test_a_receipt_without_a_name_read_teaches_nothing(self, funded_user, make_receipt):
        receipt = make_receipt()

        vendor = assign_vendor(receipt, name='ACME')

        assert alias_keys(vendor) == {'acme'}

    def test_another_users_vendor_is_refused(self, make_receipt, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        with pytest.raises(ValidationError, match='Unknown vendor'):
            assign_vendor(make_receipt(), vendor=theirs)

    def test_a_blank_or_too_long_name_is_refused(self, make_receipt):
        receipt = make_receipt()

        with pytest.raises(ValidationError, match='Enter a vendor name'):
            assign_vendor(receipt, name='   ')
        with pytest.raises(ValidationError, match='at most 255'):
            assign_vendor(receipt, name='x' * 256)

    def test_it_takes_exactly_one_of_vendor_and_name(self, make_receipt, funded_user):
        receipt = make_receipt()

        with pytest.raises(ValueError):
            assign_vendor(receipt)
        with pytest.raises(ValueError):
            assign_vendor(receipt, vendor=create_vendor(funded_user, 'A'), name='B')

    def test_clearing_the_vendor_drops_an_orphan(self, receipt):
        old = receipt.vendor_ref

        clear_vendor(receipt)

        receipt.refresh_from_db()
        assert receipt.vendor_ref is None and receipt.vendor == ''
        assert not Vendor.objects.filter(pk=old.pk).exists()


class TestDropIfOrphan:
    def test_an_unvetted_vendor_without_receipts_goes_with_its_aliases(self, funded_user):
        vendor = create_vendor(funded_user, 'ACME')

        assert drop_if_orphan(vendor.pk) is True

        assert not Vendor.objects.filter(pk=vendor.pk).exists()
        assert not VendorAlias.objects.filter(user=funded_user).exists()

    def test_a_vetted_vendor_stays(self, funded_user):
        vendor = create_vendor(funded_user, 'ACME', vetted=True)

        assert drop_if_orphan(vendor.pk) is False
        assert Vendor.objects.filter(pk=vendor.pk).exists()

    def test_a_vendor_with_a_receipt_in_the_trash_stays(self, funded_user, make_receipt):
        vendor = create_vendor(funded_user, 'ACME')
        make_receipt(vendor_ref=vendor, deleted_at=timezone.now())

        assert drop_if_orphan(vendor.pk) is False

    def test_nothing_and_unknown_ids(self):
        assert drop_if_orphan(None) is False
        assert drop_if_orphan(123456) is False


class TestRenameVendor:
    def test_every_receipt_shows_the_new_name(self, funded_user, make_receipt):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        receipts = [make_receipt(vendor='ACME', vendor_ref=acme) for _ in range(2)]
        before = receipts[0].updated_at

        rename_vendor(acme, 'ACME, LTD')

        for receipt in receipts:
            receipt.refresh_from_db()
            assert receipt.vendor == 'ACME, LTD' and receipt.vendor_ref == acme
        assert receipts[0].updated_at > before
        acme.refresh_from_db()
        assert acme.name == 'ACME, LTD'

    def test_the_old_name_and_the_new_one_keep_matching_it(self, funded_user):
        acme = create_vendor(funded_user, 'ACME', vetted=True)

        rename_vendor(acme, 'Acme Industrial Supply')

        assert match_vendor(funded_user, 'ACME') == acme
        assert match_vendor(funded_user, 'ACME Industrial Supply, Inc.') == acme
        new_alias = VendorAlias.objects.get(user=funded_user, key='acme industrial supply')
        assert new_alias.source == VendorAliasSource.USER

    def test_renaming_confirms_the_vendor_unless_told_not_to(self, funded_user):
        new = create_vendor(funded_user, 'Hetzner')
        again = create_vendor(funded_user, 'Uber')

        rename_vendor(new, 'Hetzner Online GmbH')
        rename_vendor(again, 'Uber BV', vetted=False)

        new.refresh_from_db()
        again.refresh_from_db()
        assert new.vetted_at is not None and again.vetted_at is None

    def test_un_vetting_while_renaming(self, funded_user):
        vendor = create_vendor(funded_user, 'Uber', vetted=True)

        rename_vendor(vendor, 'Uber Eats', vetted=False)

        vendor.refresh_from_db()
        assert vendor.vetted_at is None

    def test_a_name_another_vendor_has_is_a_clash_carrying_that_vendor(self, funded_user):
        other = create_vendor(funded_user, 'Blue Bottle Coffee, Inc.')
        vendor = create_vendor(funded_user, 'Blue Bottle')

        with pytest.raises(VendorNameTaken) as raised:
            rename_vendor(vendor, 'blue bottle coffee, inc.')

        assert raised.value.other == other
        vendor.refresh_from_db()
        assert vendor.name == 'Blue Bottle'

    def test_a_name_with_the_same_key_is_a_clash_too(self, funded_user):
        other = create_vendor(funded_user, 'Blue Bottle Coffee, Inc.')
        vendor = create_vendor(funded_user, 'Blue Bottle')

        with pytest.raises(VendorNameTaken) as raised:
            rename_vendor(vendor, 'Blue Bottle Coffee')

        assert raised.value.other == other

    def test_a_clash_is_a_validation_error(self, funded_user):
        create_vendor(funded_user, 'A Co')
        vendor = create_vendor(funded_user, 'B Co')

        with pytest.raises(ValidationError, match='already have a vendor called A Co'):
            rename_vendor(vendor, 'A')

    def test_another_users_vendors_are_no_clash(self, funded_user, other_user):
        create_vendor(other_user, 'ACME', vetted=True)
        vendor = create_vendor(funded_user, 'Acme Corp')

        rename_vendor(vendor, 'ACME')

        vendor.refresh_from_db()
        assert vendor.name == 'ACME'

    def test_changing_only_the_case_is_allowed(self, funded_user):
        vendor = create_vendor(funded_user, 'acme')

        rename_vendor(vendor, 'ACME')

        vendor.refresh_from_db()
        assert vendor.name == 'ACME'

    def test_blank_and_too_long_names_are_refused(self, funded_user):
        vendor = create_vendor(funded_user, 'ACME')

        with pytest.raises(ValidationError, match='Enter a vendor name'):
            rename_vendor(vendor, '  ')
        with pytest.raises(ValidationError, match='at most 255'):
            rename_vendor(vendor, 'x' * 256)

    def test_receipts_of_other_vendors_are_untouched(self, funded_user, make_receipt):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        other = create_vendor(funded_user, 'Staples', vetted=True)
        theirs = make_receipt(vendor='Staples', vendor_ref=other)

        rename_vendor(acme, 'ACME, LTD')

        theirs.refresh_from_db()
        assert theirs.vendor == 'Staples'


class TestSetVetted:
    def test_vet_and_un_vet_in_bulk(self, funded_user):
        a = create_vendor(funded_user, 'A Shop')
        b = create_vendor(funded_user, 'B Shop', vetted=True)

        assert set_vetted(funded_user, [a.pk, b.pk], True) == 1
        a.refresh_from_db()
        assert a.vetted_at is not None

        assert set_vetted(funded_user, [a.pk, b.pk], False) == 2
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.vetted_at is None and b.vetted_at is None

    def test_ids_may_be_text(self, funded_user):
        a = create_vendor(funded_user, 'A Shop')

        assert set_vetted(funded_user, [str(a.pk)], True) == 1

    def test_an_unknown_or_foreign_id_changes_nothing(self, funded_user, other_user):
        mine = create_vendor(funded_user, 'Mine')
        theirs = create_vendor(other_user, 'Theirs')

        with pytest.raises(ValidationError, match='Unknown vendor'):
            set_vetted(funded_user, [mine.pk, theirs.pk], True)
        with pytest.raises(ValidationError, match='Unknown vendor'):
            set_vetted(funded_user, ['abc'], True)

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.vetted_at is None and theirs.vetted_at is None


class TestMergeVendors:
    def test_receipts_aliases_and_text_move_to_the_vendor_kept(self, funded_user, make_receipt):
        keep = create_vendor(funded_user, 'ACME', vetted=True, aliases=('ACME Industries, LLC',))
        one = create_vendor(funded_user, 'ACME Industrial Supply')
        two = create_vendor(funded_user, 'A.C.M.E. Ind.')
        moved = [
            make_receipt(vendor='ACME Industrial Supply', vendor_ref=one),
            make_receipt(vendor='A.C.M.E. Ind.', vendor_ref=two),
        ]
        stays = make_receipt(vendor='ACME', vendor_ref=keep)

        merge_vendors(keep, [one, two])

        for receipt in [*moved, stays]:
            receipt.refresh_from_db()
            assert receipt.vendor_ref == keep and receipt.vendor == 'ACME'
        assert alias_keys(keep) == {
            'acme',
            'acme industries',
            'acme industrial supply',
            'acme ind',
        }
        assert not Vendor.objects.filter(pk__in=[one.pk, two.pk]).exists()
        assert match_vendor(funded_user, 'ACME Industrial Supply LLC') == keep

    def test_the_vendor_kept_is_vetted(self, funded_user):
        keep = create_vendor(funded_user, 'ACME')
        other = create_vendor(funded_user, 'Acme Supplies')

        merge_vendors(keep, [other])

        keep.refresh_from_db()
        assert keep.vetted_at is not None

    def test_the_names_of_the_merged_vendors_become_aliases_even_if_they_had_none(
        self, funded_user
    ):
        keep = create_vendor(funded_user, 'ACME')
        orphan = Vendor.objects.create(user=funded_user, name='A-C-M-E Supply')  # no alias

        merge_vendors(keep, [orphan])

        assert 'a c m e supply' in alias_keys(keep)

    def test_text_the_vendor_kept_is_ignored_in_the_others(self, funded_user):
        keep = create_vendor(funded_user, 'ACME')
        other = create_vendor(funded_user, 'Acme Supplies')

        merge_vendors(keep, [keep, other])

        assert list(Vendor.objects.filter(user=funded_user)) == [keep]

    def test_it_needs_something_to_merge(self, funded_user):
        keep = create_vendor(funded_user, 'ACME')

        with pytest.raises(ValidationError, match='at least two'):
            merge_vendors(keep, [keep])

    def test_another_users_vendor_is_refused(self, funded_user, other_user):
        keep = create_vendor(funded_user, 'ACME')
        theirs = create_vendor(other_user, 'Theirs')

        with pytest.raises(ValidationError, match='Unknown vendor'):
            merge_vendors(keep, [theirs])

        assert Vendor.objects.filter(pk=theirs.pk).exists()

    def test_a_receipt_filed_while_merging_aborts_it_whole(
        self, funded_user, make_receipt, monkeypatch
    ):
        keep = create_vendor(funded_user, 'ACME')
        other = create_vendor(funded_user, 'Acme Supplies')
        late = make_receipt(vendor='Acme Supplies')
        real = vendors._ensure_alias

        # Between moving the receipts and deleting the vendor, another one gets filed under it.
        def file_late(*args, **kwargs):
            Receipt.objects.filter(pk=late.pk).update(vendor_ref=other)
            return real(*args, **kwargs)

        monkeypatch.setattr(vendors, '_ensure_alias', file_late)

        with pytest.raises(ValidationError, match='Try again'):
            merge_vendors(keep, [other])

        assert Vendor.objects.filter(pk=other.pk).exists()


class TestDeleteVendor:
    def test_a_vendor_without_receipts_is_deleted_with_its_aliases(self, funded_user):
        vendor = create_vendor(funded_user, 'ACME', vetted=True)

        delete_vendor(vendor)

        assert not Vendor.objects.filter(pk=vendor.pk).exists()
        assert not VendorAlias.objects.filter(user=funded_user).exists()

    def test_it_is_refused_while_it_has_receipts_and_suggests_a_merge(
        self, funded_user, make_receipt
    ):
        vendor = create_vendor(funded_user, 'ACME', vetted=True)
        make_receipt(vendor='ACME', vendor_ref=vendor)

        with pytest.raises(ValidationError, match='Merge it into another vendor'):
            delete_vendor(vendor)

        assert Vendor.objects.filter(pk=vendor.pk).exists()

    def test_receipts_in_the_trash_count(self, funded_user, make_receipt):
        vendor = create_vendor(funded_user, 'ACME', vetted=True)
        make_receipt(vendor='ACME', vendor_ref=vendor, deleted_at=timezone.now())

        with pytest.raises(ValidationError, match='still has receipts'):
            delete_vendor(vendor)


class TestLists:
    @pytest.fixture
    def library(self, funded_user, make_receipt):
        acme = create_vendor(
            funded_user, 'ACME', vetted=True, aliases=('ACME Industries, LLC', 'ACME Ind. Oakland')
        )
        blue = create_vendor(funded_user, 'Blue Bottle')
        create_vendor(funded_user, 'Zed')
        for day in (3, 9):
            make_receipt(
                vendor='ACME',
                vendor_ref=acme,
                receipt_date=f'2026-10-{day:02d}',
                status=ReceiptStatus.PROCESSED,
            )
        make_receipt(
            vendor='ACME', vendor_ref=acme, receipt_date='2026-11-30', deleted_at=timezone.now()
        )
        make_receipt(vendor='Blue Bottle', vendor_ref=blue)
        return {'acme': acme, 'blue': blue}

    def test_vendors_by_name_with_their_counts(self, funded_user, library):
        rows = list(list_vendors(funded_user))

        assert [v.name for v in rows] == ['ACME', 'Blue Bottle', 'Zed']
        acme = rows[0]
        assert acme.receipt_count == 2 and acme.trashed_count == 1
        assert str(acme.last_receipt_date) == '2026-10-09'  # the trashed one doesn't count
        assert rows[2].receipt_count == 0 and rows[2].last_receipt_date is None

    def test_status_and_counts(self, funded_user, library):
        assert [v.name for v in list_vendors(funded_user, status='to_vet')] == [
            'Blue Bottle',
            'Zed',
        ]
        assert [v.name for v in list_vendors(funded_user, status='vetted')] == ['ACME']
        assert vendor_counts(funded_user) == {'all': 3, 'to_vet': 2, 'vetted': 1}
        with pytest.raises(ValidationError, match='Unknown status'):
            list_vendors(funded_user, status='bogus')

    def test_search_covers_names_and_alias_names(self, funded_user, library):
        assert [v.name for v in list_vendors(funded_user, q='bottle')] == ['Blue Bottle']
        assert [v.name for v in list_vendors(funded_user, q='oakland')] == ['ACME']
        assert [v.name for v in list_vendors(funded_user, q='  ')] == ['ACME', 'Blue Bottle', 'Zed']

    def test_a_search_matching_several_aliases_lists_the_vendor_once(self, funded_user, library):
        rows = list(list_vendors(funded_user, q='acme'))

        assert [v.name for v in rows] == ['ACME'] and rows[0].receipt_count == 2

    def test_only_the_users_vendors(self, funded_user, other_user, library):
        create_vendor(other_user, 'Theirs')

        assert 'Theirs' not in [v.name for v in list_vendors(funded_user)]
        assert vendor_counts(other_user)['all'] == 1

    def test_suggestions_list_vetted_vendors_first_then_the_busiest(
        self, funded_user, library, make_receipt
    ):
        busy = create_vendor(funded_user, 'Busy Bee')
        for _ in range(3):
            make_receipt(vendor='Busy Bee', vendor_ref=busy)

        names = [v.name for v in suggest_vendors(funded_user)]

        assert names == ['ACME', 'Busy Bee', 'Blue Bottle', 'Zed']

    def test_suggestions_match_what_is_typed_including_aliases(self, funded_user, library):
        assert [v.name for v in suggest_vendors(funded_user, 'bl')] == ['Blue Bottle']
        assert [v.name for v in suggest_vendors(funded_user, 'ind. oak')] == ['ACME']
        assert suggest_vendors(funded_user, 'nothing like it') == []

    def test_suggestions_are_limited(self, funded_user):
        for index in range(12):
            create_vendor(funded_user, f'Vendor {index:02d}')

        assert len(suggest_vendors(funded_user)) == 8
        assert len(suggest_vendors(funded_user, limit=3)) == 3

    def test_the_other_names_a_vendor_is_read_as(self, funded_user, library):
        rows = list(list_vendors(funded_user))

        with_alias_names(rows)

        assert rows[0].also_read_as == ['ACME Industries, LLC', 'ACME Ind. Oakland']
        assert rows[1].also_read_as == []

    def test_counting_a_vendors_live_receipts(self, library):
        assert count_receipts(library['acme']) == 2
