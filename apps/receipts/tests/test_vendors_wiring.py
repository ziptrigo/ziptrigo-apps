"""Vendors in the rest of Receipts: processing matches the vendor, editing a receipt moves it to
another (and teaches the matcher), purging and duplicate detection know about vendors, the user's
deletion still cascades, and the issue's example end to end."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User

from ..models import Receipt, ReceiptStatus, Vendor, VendorAlias
from ..services import actions, lifecycle, processing
from ..services.duplicates import find_duplicate
from ..services.vendors import rename_vendor
from .api_support import auth, patch  # noqa: F401 -- the fixture is used by name
from .factories import create_receipt, create_vendor
from .fakes import sample_extraction

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def run(receipt) -> Receipt:
    processing.process(str(receipt.pk))
    receipt.refresh_from_db()
    return receipt


def read_as(fake_ai, vendor, **overrides):
    fake_ai.default = sample_extraction(vendor=vendor, **overrides)


class TestProcessing:
    def test_the_first_receipt_from_a_vendor_creates_an_unvetted_one(self, make_receipt, fake_ai):
        read_as(fake_ai, 'ACME Industries, LLC')

        receipt = run(make_receipt())

        vendor = receipt.vendor_ref
        assert vendor is not None
        assert vendor.name == 'ACME Industries, LLC' and vendor.vetted_at is None
        assert receipt.vendor == 'ACME Industries, LLC'
        assert receipt.vendor_extracted == 'ACME Industries, LLC'

    def test_a_new_vendor_does_not_change_the_status(self, make_receipt, fake_ai):
        read_as(fake_ai, 'Brand New Vendor')

        receipt = run(make_receipt())

        assert receipt.status == ReceiptStatus.PROCESSED

    def test_the_name_as_read_is_kept_when_it_is_filed_under_another_name(
        self, make_receipt, fake_ai, funded_user
    ):
        acme = create_vendor(funded_user, 'ACME', vetted=True, aliases=('ACME Industries, LLC',))
        read_as(fake_ai, 'ACME Industries, LLC')

        receipt = run(make_receipt())

        assert receipt.vendor_ref == acme
        assert receipt.vendor == 'ACME'
        assert receipt.vendor_extracted == 'ACME Industries, LLC'
        assert Vendor.objects.count() == 1

    def test_spellings_of_one_vendor_in_the_same_file_share_it(self, make_receipt, fake_ai):
        read_as(fake_ai, 'Staples, Inc.')
        first = run(make_receipt())
        read_as(fake_ai, 'STAPLES')

        second = run(make_receipt())

        assert second.vendor_ref == first.vendor_ref
        assert second.vendor == 'Staples, Inc.' and second.vendor_extracted == 'STAPLES'

    def test_two_receipts_of_one_email_reading_the_same_new_vendor_share_it(
        self, make_receipt, fake_ai
    ):
        read_as(fake_ai, 'Hetzner Online GmbH')
        receipts = [run(make_receipt()), run(make_receipt())]

        assert receipts[0].vendor_ref == receipts[1].vendor_ref
        assert Vendor.objects.count() == 1 and VendorAlias.objects.count() == 1

    def test_a_vendor_the_user_set_is_untouched_by_a_rerun_but_the_read_name_is_updated(
        self, make_receipt, fake_ai, funded_user
    ):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='My Shop',
            user_edited_fields=['vendor'],
            vendor_ref=create_vendor(funded_user, 'My Shop', vetted=True),
            vendor_extracted='Old reading',
        )
        mine = receipt.vendor_ref
        read_as(fake_ai, 'Staples')
        actions.reprocess(receipt)

        run(receipt)

        assert receipt.vendor == 'My Shop' and receipt.vendor_ref == mine
        assert receipt.vendor_extracted == 'Staples'
        assert not Vendor.objects.filter(name='Staples').exists()

    def test_a_rerun_that_reads_another_vendor_drops_the_unvetted_one_it_leaves(
        self, make_receipt, fake_ai
    ):
        read_as(fake_ai, 'Staples')
        receipt = run(make_receipt())
        old = receipt.vendor_ref
        assert old is not None
        actions.reprocess(receipt)
        read_as(fake_ai, 'Office Depot')

        run(receipt)

        assert receipt.vendor == 'Office Depot'
        assert not Vendor.objects.filter(pk=old.pk).exists()

    def test_a_rerun_keeps_a_vetted_vendor_it_leaves(self, make_receipt, fake_ai, funded_user):
        read_as(fake_ai, 'Staples')
        receipt = run(make_receipt())
        leaving = receipt.vendor_ref
        assert leaving is not None
        Vendor.objects.filter(pk=leaving.pk).update(vetted_at=timezone.now())
        actions.reprocess(receipt)
        read_as(fake_ai, 'Office Depot')

        run(receipt)

        assert Vendor.objects.filter(pk=leaving.pk).exists()

    def test_a_rerun_that_reads_the_same_vendor_keeps_it(self, make_receipt, fake_ai):
        read_as(fake_ai, 'Staples')
        receipt = run(make_receipt())
        vendor = receipt.vendor_ref
        actions.reprocess(receipt)

        run(receipt)

        assert receipt.vendor_ref == vendor and Vendor.objects.count() == 1

    def test_no_vendor_read_means_no_vendor_and_a_receipt_to_review(self, make_receipt, fake_ai):
        read_as(fake_ai, None)

        receipt = run(make_receipt())

        assert receipt.vendor == '' and receipt.vendor_ref is None
        assert receipt.vendor_extracted == ''
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW
        assert Vendor.objects.count() == 0

    def test_a_failed_outcome_creates_no_vendor(self, make_receipt, fake_ai):
        fake_ai.default = sample_extraction(vendor='Staples', is_receipt=False)

        receipt = run(make_receipt())

        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.vendor_ref is None and receipt.vendor_extracted == ''
        assert Vendor.objects.count() == 0

    def test_another_users_vendors_are_not_matched(self, make_receipt, fake_ai, other_user):
        theirs = create_vendor(other_user, 'Staples', vetted=True)
        read_as(fake_ai, 'Staples')

        receipt = run(make_receipt())

        assert receipt.vendor_ref != theirs
        assert receipt.vendor_ref is not None
        assert receipt.vendor_ref.user_id == receipt.user_id


class TestUpdateReceiptVendor:
    @pytest.fixture
    def receipt(self, make_receipt, fake_ai):
        read_as(fake_ai, 'ACME Industries, LLC')
        return run(make_receipt())

    def test_a_typed_name_moves_the_receipt_and_teaches_the_matcher(
        self, receipt, fake_ai, make_receipt
    ):
        old = receipt.vendor_ref

        actions.update_receipt(receipt, {'vendor': 'ACME'})

        receipt.refresh_from_db()
        assert receipt.vendor == 'ACME' and receipt.vendor_ref != old
        assert receipt.vendor_ref.vetted_at is not None
        assert 'vendor' in receipt.user_edited_fields
        again = run(make_receipt())  # the AI reads the same long name again
        assert again.vendor_ref == receipt.vendor_ref and again.vendor == 'ACME'

    def test_an_orphaned_unvetted_vendor_is_dropped_and_a_vetted_one_kept(
        self, receipt, funded_user, make_receipt
    ):
        old = receipt.vendor_ref
        actions.update_receipt(receipt, {'vendor': 'ACME'})
        assert not Vendor.objects.filter(pk=old.pk).exists()

        kept = create_vendor(funded_user, 'Blue Bottle', vetted=True)
        other = make_receipt(vendor='Blue Bottle', vendor_ref=kept, status=ReceiptStatus.PROCESSED)
        actions.update_receipt(other, {'vendor': 'Staples'})
        assert Vendor.objects.filter(pk=kept.pk).exists()

    def test_by_vendor_id(self, receipt, funded_user):
        chosen = create_vendor(funded_user, 'Blue Bottle')

        actions.update_receipt(receipt, {'vendor_id': str(chosen.pk)})

        receipt.refresh_from_db()
        assert receipt.vendor_ref == chosen and receipt.vendor == 'Blue Bottle'
        chosen.refresh_from_db()
        assert chosen.vetted_at is not None
        assert 'vendor' in receipt.user_edited_fields

    def test_another_users_vendor_id_is_refused(self, receipt, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        with pytest.raises(ValidationError) as raised:
            actions.update_receipt(receipt, {'vendor_id': theirs.pk})

        assert raised.value.message_dict == {'vendor_id': ['Unknown vendor.']}

    @pytest.mark.parametrize('bad', ['abc', 'x1', object()])
    def test_a_vendor_id_that_is_not_an_id_is_refused(self, receipt, bad):
        with pytest.raises(ValidationError) as raised:
            actions.update_receipt(receipt, {'vendor_id': bad})

        assert 'vendor_id' in raised.value.message_dict

    def test_a_name_and_an_id_together_are_an_error(self, receipt, funded_user):
        chosen = create_vendor(funded_user, 'Blue Bottle')

        with pytest.raises(ValidationError) as raised:
            actions.update_receipt(receipt, {'vendor': 'ACME', 'vendor_id': chosen.pk})

        assert raised.value.message_dict == {
            'vendor_id': ['Send either vendor or vendor_id, not both.']
        }

    def test_the_vendor_it_already_has_is_a_no_op_by_name_or_id(self, receipt):
        vendor = receipt.vendor_ref
        before = receipt.updated_at

        actions.update_receipt(receipt, {'vendor': receipt.vendor})
        actions.update_receipt(receipt, {'vendor_id': vendor.pk, 'amount': '42.10'})

        receipt.refresh_from_db()
        vendor.refresh_from_db()
        assert vendor.vetted_at is None  # saving a form does not vet its vendor
        assert receipt.user_edited_fields == []
        assert receipt.updated_at == before

    def test_clearing_the_vendor_makes_it_a_receipt_to_review(self, receipt):
        old = receipt.vendor_ref

        actions.update_receipt(receipt, {'vendor': ''})

        receipt.refresh_from_db()
        assert receipt.vendor == '' and receipt.vendor_ref is None
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW
        assert 'vendor' in receipt.user_edited_fields
        assert not Vendor.objects.filter(pk=old.pk).exists()

    def test_an_empty_vendor_id_clears_it_too(self, receipt):
        actions.update_receipt(receipt, {'vendor_id': ''})

        receipt.refresh_from_db()
        assert receipt.vendor_ref is None and receipt.status == ReceiptStatus.NEEDS_REVIEW

    def test_filling_in_a_vendor_on_a_receipt_to_review_processes_it(self, make_receipt, fake_ai):
        read_as(fake_ai, None)
        receipt = run(make_receipt())
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW

        actions.update_receipt(receipt, {'vendor': 'Staples'})

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED
        assert receipt.vendor_ref is not None and receipt.vendor_ref.vetted_at is not None

    def test_it_is_refused_while_the_receipt_is_being_processed(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSING)

        with pytest.raises(ValidationError, match='still being processed'):
            actions.update_receipt(receipt, {'vendor': 'ACME'})

    def test_the_vendor_is_not_taught_without_a_name_read(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSED)

        actions.update_receipt(receipt, {'vendor': 'Hetzner'})

        vendor = Vendor.objects.get(name='Hetzner')
        assert list(VendorAlias.objects.filter(vendor=vendor).values_list('key', flat=True)) == [
            'hetzner'
        ]

    def test_a_vendor_typed_over_a_case_variant_picks_the_existing_one(self, receipt, funded_user):
        existing = create_vendor(funded_user, 'Staples', vetted=True)

        actions.update_receipt(receipt, {'vendor': 'staples'})

        receipt.refresh_from_db()
        assert receipt.vendor_ref == existing and receipt.vendor == 'Staples'
        assert Vendor.objects.filter(name__iexact='staples').count() == 1


class TestTheIssuesExample:
    def test_receipt_one_is_renamed_receipt_two_is_matched_and_a_later_rename_shows_everywhere(
        self, make_receipt, fake_ai
    ):
        read_as(fake_ai, 'ACME Industries, LLC')

        # Receipt 1: a new vendor, saved with the name that was read.
        first = run(make_receipt())
        assert first.vendor == 'ACME Industries, LLC'
        assert first.vendor_ref is not None and first.vendor_ref.vetted_at is None

        # The user calls that vendor "ACME".
        actions.update_receipt(first, {'vendor': 'ACME'})
        first.refresh_from_db()
        acme = first.vendor_ref
        assert first.vendor == 'ACME' and acme.vetted_at is not None

        # Receipt 2: the same reading is filed under ACME, and what was read is kept.
        second = run(make_receipt())
        assert second.vendor_ref == acme and second.vendor == 'ACME'
        assert second.vendor_extracted == 'ACME Industries, LLC'
        assert Vendor.objects.count() == 1

        # Nothing went back to update earlier receipts...
        first.refresh_from_db()
        assert first.vendor == 'ACME'

        # ... but renaming the vendor shows the new name on all of them.
        rename_vendor(acme, 'ACME, LTD')
        for receipt in (first, second):
            receipt.refresh_from_db()
            assert receipt.vendor == 'ACME, LTD'
        assert second.vendor_extracted == 'ACME Industries, LLC'
        assert first.vendor_extracted == 'ACME Industries, LLC'

    def test_the_same_through_a_rename_of_the_first_vendor(self, make_receipt, fake_ai):
        read_as(fake_ai, 'ACME Industries, LLC')
        first = run(make_receipt())
        assert first.vendor_ref is not None

        rename_vendor(first.vendor_ref, 'ACME')
        second = run(make_receipt())

        assert second.vendor == 'ACME' and second.vendor_ref == first.vendor_ref
        first.refresh_from_db()
        assert first.vendor == 'ACME'


class TestPurge:
    def test_purging_the_last_receipt_of_an_unvetted_vendor_deletes_it(
        self, funded_user, make_receipt
    ):
        vendor = create_vendor(funded_user, 'Staples')
        receipt = make_receipt(vendor='Staples', vendor_ref=vendor, deleted_at=timezone.now())

        lifecycle.purge_receipt(receipt)

        assert not Vendor.objects.filter(pk=vendor.pk).exists()

    def test_a_vendor_with_other_receipts_or_vetted_stays(self, funded_user, make_receipt):
        shared = create_vendor(funded_user, 'Shared')
        vetted = create_vendor(funded_user, 'Vetted', vetted=True)
        make_receipt(vendor='Shared', vendor_ref=shared)
        gone = [
            make_receipt(vendor='Shared', vendor_ref=shared, deleted_at=timezone.now()),
            make_receipt(vendor='Vetted', vendor_ref=vetted, deleted_at=timezone.now()),
        ]

        for receipt in gone:
            lifecycle.purge_receipt(receipt)

        assert Vendor.objects.filter(pk__in=[shared.pk, vetted.pk]).count() == 2

    def test_delete_forever_does_it_too(self, funded_user, make_receipt):
        vendor = create_vendor(funded_user, 'Staples')
        receipt = make_receipt(vendor='Staples', vendor_ref=vendor, deleted_at=timezone.now())

        actions.delete_forever(receipt)

        assert not Vendor.objects.filter(pk=vendor.pk).exists()


class TestDuplicates:
    def make(self, make_receipt, vendor, **fields):
        return make_receipt(
            status=ReceiptStatus.PROCESSED,
            amount=Decimal('275.50'),
            receipt_date=date(2026, 10, 8),
            vendor=vendor.name,
            vendor_ref=vendor,
            **fields,
        )

    def test_two_receipts_filed_under_one_vendor_are_duplicates(self, funded_user, make_receipt):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        original = self.make(make_receipt, acme, vendor_extracted='ACME')
        copy = self.make(make_receipt, acme, vendor_extracted='ACME Industries, LLC')

        assert find_duplicate(copy) == original

    def test_the_vendor_link_decides_when_the_text_differs(self, funded_user, make_receipt):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        original = self.make(make_receipt, acme)
        Receipt.objects.filter(pk=original.pk).update(vendor='ACME (old text)')
        copy = self.make(make_receipt, acme)

        assert find_duplicate(copy) == original

    def test_receipts_without_a_link_still_match_on_the_text(self, funded_user, make_receipt):
        original = make_receipt(
            vendor='Staples', amount=Decimal('1'), receipt_date=date(2026, 1, 1)
        )
        copy = make_receipt(vendor='STAPLES', amount=Decimal('1'), receipt_date=date(2026, 1, 1))

        assert find_duplicate(copy) == original

    def test_a_different_vendor_is_not_a_duplicate(self, funded_user, make_receipt):
        original = self.make(make_receipt, create_vendor(funded_user, 'ACME', vetted=True))
        other = self.make(make_receipt, create_vendor(funded_user, 'Staples', vetted=True))

        assert find_duplicate(other) is None
        assert find_duplicate(original) is None


class TestUserDeletion:
    def test_deleting_a_user_deletes_their_vendors_and_receipts(
        self,
        funded_user,
        make_receipt,
        other_user,
        fake_storage,
        django_capture_on_commit_callbacks,
    ):
        vendor = create_vendor(funded_user, 'Staples', vetted=True)
        make_receipt(vendor='Staples', vendor_ref=vendor)
        kept = create_vendor(other_user, 'Theirs')
        create_receipt(other_user, fake_storage, vendor='Theirs', vendor_ref=kept)

        with django_capture_on_commit_callbacks(execute=True):
            funded_user.delete()

        assert not User.objects.filter(pk=funded_user.pk).exists()
        assert not Vendor.objects.filter(pk=vendor.pk).exists()
        assert not VendorAlias.objects.filter(vendor=vendor).exists()
        assert Vendor.objects.filter(pk=kept.pk).exists()
        assert Receipt.objects.filter(vendor_ref=kept).count() == 1


class TestApiPatch:
    def test_patching_the_vendor_goes_through_the_same_matching_and_teaching(
        self,
        client,
        auth,
        make_receipt,
        fake_ai,  # noqa: F811
    ):
        read_as(fake_ai, 'ACME Industries, LLC')
        receipt = run(make_receipt())

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor': 'ACME'})

        assert response.status_code == 200
        assert response.json()['vendor'] == 'ACME'
        receipt.refresh_from_db()
        assert receipt.vendor_ref is not None and receipt.vendor_ref.vetted_at is not None
        second = run(make_receipt())
        assert second.vendor_ref == receipt.vendor_ref

    def test_clearing_the_vendor_through_the_api(self, client, auth, make_receipt, fake_ai):  # noqa: F811
        read_as(fake_ai, 'Staples')
        receipt = run(make_receipt())

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor': None})

        assert response.status_code == 200
        receipt.refresh_from_db()
        assert receipt.vendor == '' and receipt.vendor_ref is None
