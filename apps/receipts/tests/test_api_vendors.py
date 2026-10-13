"""The receipts JWT API's vendor endpoints (`/api/receipts/vendors...`) and the vendor parts of the
receipt endpoints: the new `ReceiptSchema` fields, `PATCH /receipts/{id}` with `vendor_id`, and the
`vendor=` filter of the list and the CSV export."""

import csv
import io
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .. import services
from ..models import Receipt, ReceiptStatus, Vendor, VendorAlias
from ..schemas import MAX_MERGE_IDS
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    delete,
    get,
    other_auth,
    patch,
    post,
)
from .factories import create_receipt, create_vendor

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

VENDOR_FIELDS = {
    'id',
    'name',
    'vetted',
    'vetted_at',
    'aliases',
    'receipt_count',
    'trashed_count',
    'last_receipt_date',
}


@pytest.fixture
def filed(funded_user, fake_storage):
    """Create a receipt filed under `vendor` (its text is the vendor's name)."""

    def _filed(vendor: Vendor, **fields) -> Receipt:
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        return create_receipt(
            funded_user, fake_storage, vendor_ref=vendor, vendor=vendor.name, **fields
        )

    return _filed


def names(response) -> list[str]:
    return [v['name'] for v in response.json()['results']]


def csv_rows(response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(b''.join(response.streaming_content).decode())))


class TestList:
    def test_the_shape_and_the_order(self, client, auth, funded_user):
        create_vendor(funded_user, 'zeta', vetted=True)
        create_vendor(funded_user, 'Alpha')

        response = get(client, '/vendors', auth)

        assert response.status_code == 200
        body = response.json()
        assert (body['count'], body['limit'], body['offset']) == (2, 20, 0)
        assert names(response) == ['Alpha', 'zeta']  # by name, ignoring case
        assert set(body['results'][0]) == VENDOR_FIELDS
        alpha, zeta = body['results']
        assert alpha['vetted'] is False and alpha['vetted_at'] is None
        assert zeta['vetted'] is True and zeta['vetted_at'] is not None
        assert alpha['receipt_count'] == 0 and alpha['last_receipt_date'] is None

    def test_only_the_callers_vendors(self, client, auth, funded_user, other_user):
        create_vendor(funded_user, 'Mine')
        create_vendor(other_user, 'Theirs')

        response = get(client, '/vendors', auth)

        assert names(response) == ['Mine'] and response.json()['count'] == 1

    def test_counts_dates_and_the_trash(self, client, auth, funded_user, filed):
        staples = create_vendor(funded_user, 'Staples', vetted=True)
        filed(staples, receipt_date=date(2026, 9, 1))
        filed(staples, receipt_date=date(2026, 10, 9))
        filed(staples, receipt_date=date(2026, 12, 1), deleted_at=timezone.now())  # trashed
        filed(staples, receipt_date=None)

        row = get(client, '/vendors', auth).json()['results'][0]

        # Live receipts only in `receipt_count` and `last_receipt_date`; the Trash apart.
        assert row['receipt_count'] == 3
        assert row['trashed_count'] == 1
        assert row['last_receipt_date'] == '2026-10-09'

    def test_aliases_are_the_other_names_only(self, client, auth, funded_user):
        create_vendor(
            funded_user, 'ACME', vetted=True, aliases=('ACME Industries, LLC', 'Acme Inds')
        )

        row = get(client, '/vendors', auth).json()['results'][0]

        assert row['aliases'] == ['ACME Industries, LLC', 'Acme Inds']
        assert all(isinstance(alias, str) for alias in row['aliases'])

    @pytest.mark.parametrize(
        ('status', 'expected'),
        [
            ('all', ['Mint', 'Staples', 'Uber']),
            ('to_vet', ['Staples']),
            ('vetted', ['Mint', 'Uber']),
        ],
    )
    def test_status(self, client, auth, funded_user, status, expected):
        create_vendor(funded_user, 'Staples')
        create_vendor(funded_user, 'Uber', vetted=True)
        create_vendor(funded_user, 'Mint', vetted=True)

        response = get(client, '/vendors', auth, status=status)

        assert names(response) == expected and response.json()['count'] == len(expected)

    def test_an_unknown_status_is_a_400(self, client, auth):
        response = get(client, '/vendors', auth, status='bogus')

        assert response.status_code == 400 and 'Unknown status' in response.json()['detail']

    def test_q_matches_names_and_aliases(self, client, auth, funded_user):
        create_vendor(funded_user, 'ACME', vetted=True, aliases=('Industrial Supply Co',))
        create_vendor(funded_user, 'Staples')

        assert names(get(client, '/vendors', auth, q='acm')) == ['ACME']
        assert names(get(client, '/vendors', auth, q='industrial')) == ['ACME']
        assert names(get(client, '/vendors', auth, q='nothing')) == []

    def test_pagination(self, client, auth, funded_user):
        for name in ('A', 'B', 'C', 'D', 'E'):
            create_vendor(funded_user, name)

        page = get(client, '/vendors', auth, limit=2, offset=2).json()

        assert page['count'] == 5 and (page['limit'], page['offset']) == (2, 2)
        assert [v['name'] for v in page['results']] == ['C', 'D']

    def test_the_page_size_is_clamped_not_rejected(self, client, auth, funded_user):
        create_vendor(funded_user, 'A')

        body = get(client, '/vendors', auth, limit=1000, offset=-5).json()

        assert (body['limit'], body['offset']) == (100, 0)

    def test_a_mistyped_parameter_is_a_422(self, client, auth):
        assert get(client, '/vendors', auth, limit='abc').status_code == 422


class TestGet:
    def test_one_vendor(self, client, auth, funded_user, filed):
        acme = create_vendor(funded_user, 'ACME', vetted=True, aliases=('Acme Industries',))
        filed(acme, receipt_date=date(2026, 10, 1))

        response = get(client, f'/vendors/{acme.pk}', auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == VENDOR_FIELDS
        assert body['id'] == acme.pk and body['name'] == 'ACME' and body['vetted'] is True
        assert body['aliases'] == ['Acme Industries'] and body['receipt_count'] == 1
        assert body['last_receipt_date'] == '2026-10-01'

    def test_another_users_vendor_is_a_404_not_a_403(self, client, auth, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        assert get(client, f'/vendors/{theirs.pk}', auth).status_code == 404

    def test_an_unknown_vendor_is_a_404(self, client, auth):
        assert get(client, '/vendors/987654', auth).status_code == 404


class TestUpdate:
    def test_rename_changes_every_receipt_and_vets(self, client, auth, funded_user, filed):
        vendor = create_vendor(funded_user, 'ACME Industries, LLC')
        first, second = filed(vendor), filed(vendor)

        response = patch(client, f'/vendors/{vendor.pk}', auth, {'name': 'ACME, LTD'})

        assert response.status_code == 200
        body = response.json()
        assert body['name'] == 'ACME, LTD' and body['vetted'] is True
        assert body['receipt_count'] == 2
        assert 'ACME Industries, LLC' in body['aliases']  # the old name still matches
        for receipt in (first, second):
            receipt.refresh_from_db()
            assert receipt.vendor == 'ACME, LTD'

    def test_rename_can_stay_unvetted(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'Staples')

        body = patch(
            client, f'/vendors/{vendor.pk}', auth, {'name': 'Staples Inc', 'vetted': False}
        ).json()

        assert body['name'] == 'Staples Inc' and body['vetted'] is False

    def test_rename_keeps_a_vendor_vetted_that_already_was(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'Uber', vetted=True)

        body = patch(client, f'/vendors/{vendor.pk}', auth, {'name': 'Uber Eats'}).json()

        assert body['vetted'] is True

    def test_vet_and_unvet(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'Staples')

        vetted = patch(client, f'/vendors/{vendor.pk}', auth, {'vetted': True}).json()
        assert vetted['vetted'] is True and vetted['vetted_at'] is not None
        assert vetted['name'] == 'Staples'

        unvetted = patch(client, f'/vendors/{vendor.pk}', auth, {'vetted': False}).json()
        assert unvetted['vetted'] is False and unvetted['vetted_at'] is None

    def test_null_and_an_empty_body_change_nothing(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'Staples')

        for payload in ({}, {'name': None, 'vetted': None}):
            response = patch(client, f'/vendors/{vendor.pk}', auth, payload)
            assert response.status_code == 200
            assert response.json()['name'] == 'Staples' and response.json()['vetted'] is False

    def test_a_name_another_vendor_has_is_a_409_naming_it(self, client, auth, funded_user, filed):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        industries = create_vendor(funded_user, 'ACME Industries, LLC')
        receipt = filed(industries)

        response = patch(client, f'/vendors/{industries.pk}', auth, {'name': 'ACME'})

        assert response.status_code == 409
        body = response.json()
        assert body['conflicting_vendor_id'] == acme.pk
        assert body['conflicting_vendor_name'] == 'ACME'
        assert 'ACME' in body['detail']
        industries.refresh_from_db()
        receipt.refresh_from_db()
        assert industries.name == 'ACME Industries, LLC' and receipt.vendor == industries.name

    @pytest.mark.parametrize('name', ['acme', 'ACME, Inc.', 'Acme LLC', 'The Acme'])
    def test_a_clash_ignores_case_punctuation_and_legal_forms(
        self, client, auth, funded_user, name
    ):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        other = create_vendor(funded_user, 'Something else')

        response = patch(client, f'/vendors/{other.pk}', auth, {'name': name})

        assert response.status_code == 409
        assert response.json()['conflicting_vendor_id'] == acme.pk

    def test_a_clash_with_an_alias_of_another_vendor_is_a_409(self, client, auth, funded_user):
        acme = create_vendor(funded_user, 'ACME', vetted=True, aliases=('Acme Industries',))
        other = create_vendor(funded_user, 'Something else')

        response = patch(client, f'/vendors/{other.pk}', auth, {'name': 'Acme Industries'})

        assert response.status_code == 409
        assert response.json()['conflicting_vendor_id'] == acme.pk

    def test_a_409_leaves_the_vetted_flag_alone_too(self, client, auth, funded_user):
        create_vendor(funded_user, 'ACME', vetted=True)
        other = create_vendor(funded_user, 'Other')

        patch(client, f'/vendors/{other.pk}', auth, {'name': 'ACME', 'vetted': True})

        other.refresh_from_db()
        assert other.vetted_at is None and other.name == 'Other'

    def test_another_users_vendor_name_is_no_clash(self, client, auth, funded_user, other_user):
        create_vendor(other_user, 'ACME', vetted=True)
        mine = create_vendor(funded_user, 'Other')

        response = patch(client, f'/vendors/{mine.pk}', auth, {'name': 'ACME'})

        assert response.status_code == 200 and response.json()['name'] == 'ACME'

    def test_renaming_to_its_own_name_is_fine(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'ACME')

        response = patch(client, f'/vendors/{vendor.pk}', auth, {'name': 'acme'})

        assert response.status_code == 200 and response.json()['name'] == 'acme'

    @pytest.mark.parametrize(
        ('name', 'message'), [('', 'Enter a vendor name'), ('x' * 300, 'at most 255')]
    )
    def test_a_bad_name_is_a_400(self, client, auth, funded_user, name, message):
        vendor = create_vendor(funded_user, 'Staples')

        response = patch(client, f'/vendors/{vendor.pk}', auth, {'name': name})

        assert response.status_code == 400 and message in response.json()['detail']
        vendor.refresh_from_db()
        assert vendor.name == 'Staples'

    @pytest.mark.parametrize(
        'payload',
        [{'id': 5}, {'nam': 'x'}, {'name': 'x', 'receipt_count': 3}, {'aliases': []}],
    )
    def test_an_unknown_key_is_a_422_and_nothing_is_applied(
        self, client, auth, funded_user, payload
    ):
        vendor = create_vendor(funded_user, 'Staples')

        response = patch(client, f'/vendors/{vendor.pk}', auth, payload)

        assert response.status_code == 422
        detail = response.json()['detail']
        assert isinstance(detail, list) and 'extra' in detail[0]['type']
        vendor.refresh_from_db()
        assert vendor.name == 'Staples' and vendor.vetted_at is None

    def test_a_wrongly_typed_field_is_a_422(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'Staples')

        assert (
            patch(client, f'/vendors/{vendor.pk}', auth, {'vetted': 'perhaps'}).status_code == 422
        )

    def test_a_vendor_deleted_during_the_rename_is_a_404(
        self, client, auth, funded_user, monkeypatch
    ):
        # The ownership check passes, then the vendor is merged away or deleted from another tab
        # before `rename_vendor` re-reads (and locks) the row: a 404, not a 500.
        vendor = create_vendor(funded_user, 'Staples')
        rename = services.rename_vendor

        def deleted_first(target, name, **kwargs):
            Vendor.objects.filter(pk=target.pk).delete()
            return rename(target, name, **kwargs)

        monkeypatch.setattr(services, 'rename_vendor', deleted_first)

        response = patch(client, f'/vendors/{vendor.pk}', auth, {'name': 'Office Staples'})

        assert response.status_code == 404

    def test_the_ownership_check_does_not_compute_the_statistics(
        self, client, auth, funded_user, filed
    ):
        vendor = create_vendor(funded_user, 'Staples')
        filed(vendor)

        with CaptureQueriesContext(connection) as queries:
            assert patch(client, f'/vendors/{vendor.pk}', auth, {'vetted': True}).status_code == 200

        # The statistics (an aggregate over the receipts) and the alias names are built once, for
        # the response, not also for the ownership check.
        sql = [q['sql'] for q in queries.captured_queries]
        assert len([q for q in sql if 'receipts_receipt' in q]) == 1
        assert len([q for q in sql if 'receipts_vendoralias' in q]) == 1

    def test_another_users_vendor_is_a_404(self, client, auth, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        response = patch(client, f'/vendors/{theirs.pk}', auth, {'name': 'Mine now'})

        assert response.status_code == 404
        theirs.refresh_from_db()
        assert theirs.name == 'Theirs'


class TestMerge:
    def test_moves_receipts_and_names_and_vets_the_survivor(self, client, auth, funded_user, filed):
        keep = create_vendor(funded_user, 'ACME')
        misread = create_vendor(funded_user, 'ACME Industires', aliases=('Acme Inds',))
        old = create_vendor(funded_user, 'Acme Corp Supply')
        first, second = filed(keep), filed(misread)
        third = filed(old, receipt_date=date(2026, 10, 2))

        response = post(
            client, '/vendors/merge', auth, {'keep': keep.pk, 'merge': [misread.pk, old.pk]}
        )

        assert response.status_code == 200
        body = response.json()
        assert body['id'] == keep.pk and body['name'] == 'ACME' and body['vetted'] is True
        assert body['receipt_count'] == 3 and body['last_receipt_date'] == '2026-10-02'
        assert {'ACME Industires', 'Acme Inds', 'Acme Corp Supply'} <= set(body['aliases'])
        assert not Vendor.objects.filter(pk__in=[misread.pk, old.pk]).exists()
        for receipt in (first, second, third):
            receipt.refresh_from_db()
            assert receipt.vendor == 'ACME' and receipt.vendor_ref_id == keep.pk
        # The merged names now match `keep`.
        assert VendorAlias.objects.filter(vendor=keep).count() >= 4

    def test_the_list_afterwards(self, client, auth, funded_user):
        keep = create_vendor(funded_user, 'ACME')
        other = create_vendor(funded_user, 'Acme Supply')

        post(client, '/vendors/merge', auth, {'keep': keep.pk, 'merge': [other.pk]})

        assert names(get(client, '/vendors', auth)) == ['ACME']
        assert get(client, f'/vendors/{other.pk}', auth).status_code == 404

    def test_a_repeated_id_and_keep_in_the_list_are_ignored(self, client, auth, funded_user):
        keep = create_vendor(funded_user, 'ACME')
        other = create_vendor(funded_user, 'Acme Supply')

        response = post(
            client,
            '/vendors/merge',
            auth,
            {'keep': keep.pk, 'merge': [other.pk, other.pk, keep.pk]},
        )

        assert response.status_code == 200 and response.json()['id'] == keep.pk

    @pytest.mark.parametrize('merge', [[], 'keep'])
    def test_nothing_to_merge_is_a_400(self, client, auth, funded_user, merge):
        keep = create_vendor(funded_user, 'ACME')
        ids = [keep.pk] if merge == 'keep' else merge

        response = post(client, '/vendors/merge', auth, {'keep': keep.pk, 'merge': ids})

        assert response.status_code == 400 and 'at least two' in response.json()['detail']

    def test_a_foreign_id_in_merge_is_a_404_and_nothing_is_merged(
        self, client, auth, funded_user, other_user, filed
    ):
        keep = create_vendor(funded_user, 'ACME')
        mine = create_vendor(funded_user, 'Acme Supply')
        theirs = create_vendor(other_user, 'Acme Theirs')
        filed(mine)

        response = post(
            client, '/vendors/merge', auth, {'keep': keep.pk, 'merge': [mine.pk, theirs.pk]}
        )

        assert response.status_code == 404
        assert Vendor.objects.filter(pk__in=[keep.pk, mine.pk, theirs.pk]).count() == 3

    def test_a_foreign_keep_is_a_404(self, client, auth, funded_user, other_user):
        mine = create_vendor(funded_user, 'Mine')
        theirs = create_vendor(other_user, 'Theirs')

        response = post(client, '/vendors/merge', auth, {'keep': theirs.pk, 'merge': [mine.pk]})

        assert response.status_code == 404
        assert Vendor.objects.filter(pk=mine.pk).exists()

    def test_an_unknown_id_is_a_404(self, client, auth, funded_user):
        keep = create_vendor(funded_user, 'ACME')

        response = post(client, '/vendors/merge', auth, {'keep': keep.pk, 'merge': [987654]})

        assert response.status_code == 404

    def test_more_than_the_maximum_ids_is_a_422_and_nothing_is_merged(
        self, client, auth, funded_user
    ):
        keep = create_vendor(funded_user, 'ACME')
        others = [create_vendor(funded_user, f'Vendor {i}') for i in range(MAX_MERGE_IDS + 1)]

        response = post(
            client, '/vendors/merge', auth, {'keep': keep.pk, 'merge': [v.pk for v in others]}
        )

        assert response.status_code == 422
        assert Vendor.objects.filter(user=funded_user).count() == MAX_MERGE_IDS + 2

    def test_exactly_the_maximum_is_merged(self, client, auth, funded_user):
        keep = create_vendor(funded_user, 'ACME')
        others = [create_vendor(funded_user, f'Vendor {i}') for i in range(MAX_MERGE_IDS)]

        response = post(
            client, '/vendors/merge', auth, {'keep': keep.pk, 'merge': [v.pk for v in others]}
        )

        assert response.status_code == 200
        assert list(Vendor.objects.filter(user=funded_user)) == [keep]

    @pytest.mark.parametrize(
        'payload',
        [{}, {'keep': 1}, {'merge': [1]}, {'keep': 'x', 'merge': [1]}, {'keep': 1, 'merge': 2}],
    )
    def test_a_malformed_body_is_a_422(self, client, auth, payload):
        assert post(client, '/vendors/merge', auth, payload).status_code == 422


class TestDelete:
    def test_deletes_a_vendor_without_receipts(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'Unused')

        response = delete(client, f'/vendors/{vendor.pk}', auth)

        assert response.status_code == 204
        assert not Vendor.objects.filter(pk=vendor.pk).exists()
        assert not VendorAlias.objects.filter(vendor_id=vendor.pk).exists()

    def test_a_vendor_with_receipts_is_a_400_advising_a_merge(
        self, client, auth, funded_user, filed
    ):
        vendor = create_vendor(funded_user, 'Staples')
        filed(vendor)

        response = delete(client, f'/vendors/{vendor.pk}', auth)

        assert response.status_code == 400
        assert 'Merge' in response.json()['detail']
        assert Vendor.objects.filter(pk=vendor.pk).exists()

    def test_a_trashed_receipt_counts_too(self, client, auth, funded_user, filed):
        vendor = create_vendor(funded_user, 'Staples')
        filed(vendor, deleted_at=timezone.now())

        assert delete(client, f'/vendors/{vendor.pk}', auth).status_code == 400
        assert Vendor.objects.filter(pk=vendor.pk).exists()

    def test_another_users_vendor_is_a_404(self, client, auth, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        assert delete(client, f'/vendors/{theirs.pk}', auth).status_code == 404
        assert Vendor.objects.filter(pk=theirs.pk).exists()

    def test_deleting_twice_is_a_404(self, client, auth, funded_user):
        vendor = create_vendor(funded_user, 'Unused')
        delete(client, f'/vendors/{vendor.pk}', auth)

        assert delete(client, f'/vendors/{vendor.pk}', auth).status_code == 404


class TestReceiptSchema:
    def test_a_receipt_carries_its_vendor(self, client, auth, funded_user, filed):
        vendor = create_vendor(funded_user, 'ACME', vetted=True)
        receipt = filed(vendor, vendor_extracted='ACME Industries, LLC')

        body = get(client, f'/receipts/{receipt.pk}', auth).json()

        assert body['vendor'] == 'ACME'  # still the name
        assert body['vendor_id'] == vendor.pk
        assert body['vendor_vetted'] is True
        assert body['vendor_extracted'] == 'ACME Industries, LLC'

    def test_an_unvetted_vendor(self, client, auth, funded_user, filed):
        receipt = filed(create_vendor(funded_user, 'Staples'))

        body = get(client, f'/receipts/{receipt.pk}', auth).json()

        assert body['vendor_vetted'] is False and body['vendor_extracted'] == ''

    def test_a_receipt_without_a_vendor(self, client, auth, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.RECEIVED)

        body = get(client, f'/receipts/{receipt.pk}', auth).json()

        assert body['vendor_id'] is None and body['vendor_vetted'] is False
        assert body['vendor'] == '' and body['vendor_extracted'] == ''

    def test_the_list_has_the_fields_too(self, client, auth, funded_user, filed):
        vendor = create_vendor(funded_user, 'ACME', vetted=True)
        filed(vendor)

        result = get(client, '/receipts', auth).json()['results'][0]

        assert result['vendor_id'] == vendor.pk and result['vendor_vetted'] is True

    def test_the_list_does_not_query_the_vendor_per_receipt(
        self, client, auth, funded_user, filed, django_assert_max_num_queries
    ):
        for name in ('A', 'B', 'C', 'D'):
            filed(create_vendor(funded_user, name, vetted=True))

        with django_assert_max_num_queries(3):
            assert get(client, '/receipts', auth).status_code == 200


class TestPatchReceiptVendor:
    def test_vendor_id_moves_the_receipt_vets_the_vendor_and_teaches(
        self, client, auth, funded_user, filed
    ):
        old = create_vendor(funded_user, 'ACME Industries, LLC')
        new = create_vendor(funded_user, 'ACME')
        receipt = filed(old, vendor_extracted='ACME Industries, LLC')
        sibling = filed(old)

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor_id': new.pk})

        assert response.status_code == 200
        body = response.json()
        assert body['vendor'] == 'ACME' and body['vendor_id'] == new.pk
        assert body['vendor_vetted'] is True
        assert body['vendor_extracted'] == 'ACME Industries, LLC'  # what the AI read is kept
        new.refresh_from_db()
        assert new.vetted_at is not None
        sibling.refresh_from_db()
        assert sibling.vendor_ref_id == old.pk  # only this receipt moved
        # Taught: the name the AI read now points at the new vendor.
        alias = VendorAlias.objects.get(user=funded_user, key='acme industries')
        assert alias.vendor_id == new.pk

    def test_vendor_id_to_another_vendor_marks_the_field_as_edited(
        self, client, auth, funded_user, filed
    ):
        receipt = filed(create_vendor(funded_user, 'A'))
        target = create_vendor(funded_user, 'B', vetted=True)

        patch(client, f'/receipts/{receipt.pk}', auth, {'vendor_id': target.pk})

        receipt.refresh_from_db()
        assert receipt.user_edited_fields == ['vendor']

    def test_vendor_by_name_goes_through_the_same_service(self, client, auth, funded_user, filed):
        receipt = filed(create_vendor(funded_user, 'ACME Industries, LLC'))

        body = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor': 'ACME'}).json()

        assert body['vendor'] == 'ACME' and body['vendor_vetted'] is True
        assert Vendor.objects.get(pk=body['vendor_id']).name == 'ACME'

    def test_vendor_id_null_clears_the_vendor(self, client, auth, funded_user, filed):
        receipt = filed(create_vendor(funded_user, 'ACME', vetted=True))

        body = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor_id': None}).json()

        assert body['vendor'] == '' and body['vendor_id'] is None
        assert body['status'] == 'needs_review' and 'vendor' in body['missing_fields']

    @pytest.mark.parametrize('second', [{'vendor_id': 'ID'}, {'vendor_id': None}])
    def test_both_vendor_and_vendor_id_is_a_400_and_nothing_is_applied(
        self, client, auth, funded_user, filed, second
    ):
        old = create_vendor(funded_user, 'Staples', vetted=True)
        new = create_vendor(funded_user, 'ACME', vetted=True)
        receipt = filed(old, notes='keep')
        payload = {'vendor': 'Other', 'notes': 'changed'}
        payload.update({k: new.pk if v == 'ID' else v for k, v in second.items()})

        response = patch(client, f'/receipts/{receipt.pk}', auth, payload)

        assert response.status_code == 400
        assert response.json()['detail'].startswith('vendor_id:')
        assert 'not both' in response.json()['detail']
        receipt.refresh_from_db()
        assert receipt.vendor_ref_id == old.pk and receipt.notes == 'keep'

    def test_another_users_vendor_id_is_a_400_like_a_foreign_category(
        self, client, auth, funded_user, other_user, filed
    ):
        theirs = create_vendor(other_user, 'Theirs', vetted=True)
        mine = create_vendor(funded_user, 'Mine')
        receipt = filed(mine)

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor_id': theirs.pk})

        assert response.status_code == 400
        assert response.json()['detail'] == 'vendor_id: Unknown vendor.'
        receipt.refresh_from_db()
        assert receipt.vendor_ref_id == mine.pk
        theirs.refresh_from_db()
        assert theirs.vetted_at is not None

    def test_an_unknown_vendor_id_is_a_400(self, client, auth, filed, funded_user):
        receipt = filed(create_vendor(funded_user, 'Mine'))

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor_id': 987654})

        assert response.status_code == 400 and 'vendor_id' in response.json()['detail']

    def test_a_wrongly_typed_vendor_id_is_a_422(self, client, auth, filed, funded_user):
        receipt = filed(create_vendor(funded_user, 'Mine'))

        for value in ('', 'abc'):
            assert (
                patch(client, f'/receipts/{receipt.pk}', auth, {'vendor_id': value}).status_code
                == 422
            )

    @pytest.mark.parametrize('by', ['vendor_id', 'vendor'])
    def test_the_vendor_it_already_has_changes_nothing(self, client, auth, funded_user, filed, by):
        # Documented on the PATCH endpoint: this does not vet the vendor, `PATCH /vendors/{id}`
        # with `{"vetted": true}` does.
        vendor = create_vendor(funded_user, 'Staples')  # unvetted
        receipt = filed(vendor)
        sent = vendor.pk if by == 'vendor_id' else vendor.name

        body = patch(client, f'/receipts/{receipt.pk}', auth, {by: sent}).json()

        assert body['vendor_vetted'] is False  # not a choice, so no vetting
        vendor.refresh_from_db()
        assert vendor.vetted_at is None
        receipt.refresh_from_db()
        assert receipt.user_edited_fields == []

        vetted = patch(client, f'/vendors/{vendor.pk}', auth, {'vetted': True}).json()
        assert vetted['vetted'] is True


class TestVendorFilter:
    @pytest.fixture
    def two(self, funded_user, filed):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        staples = create_vendor(funded_user, 'Staples')
        return acme, staples, filed(acme), filed(acme), filed(staples)

    def test_the_list(self, client, auth, two):
        acme, staples, a1, a2, s1 = two

        def ids(**query):
            return {r['id'] for r in get(client, '/receipts', auth, **query).json()['results']}

        assert ids(vendor=acme.pk) == {str(a1.pk), str(a2.pk)}
        assert ids(vendor=staples.pk) == {str(s1.pk)}
        assert len(ids()) == 3

    def test_it_combines_with_the_other_filters(self, client, auth, two, funded_user, filed):
        acme, _, a1, a2, _ = two
        Receipt.objects.filter(pk=a2.pk).update(status=ReceiptStatus.NEEDS_REVIEW)

        body = get(client, '/receipts', auth, vendor=acme.pk, status='needs_review').json()

        assert [r['id'] for r in body['results']] == [str(a2.pk)] and body['count'] == 1

    def test_the_trash_is_filtered_too(self, client, auth, two):
        acme, _, a1, _, _ = two
        Receipt.objects.filter(pk=a1.pk).update(deleted_at=timezone.now())

        body = get(client, '/receipts', auth, vendor=acme.pk, trashed='true').json()

        assert [r['id'] for r in body['results']] == [str(a1.pk)]

    def test_another_users_vendor_matches_nothing(self, client, auth, two, other_user):
        theirs = create_vendor(other_user, 'Theirs')

        body = get(client, '/receipts', auth, vendor=theirs.pk).json()

        assert body['count'] == 0 and body['results'] == []

    def test_a_non_integer_vendor_is_a_422(self, client, auth, two):
        assert get(client, '/receipts', auth, vendor='acme').status_code == 422
        assert get(client, '/export.csv', auth, vendor='acme').status_code == 422

    def test_the_search_finds_the_name_the_ai_read(self, client, auth, funded_user, filed):
        vendor = create_vendor(funded_user, 'ACME, LTD', vetted=True)
        receipt = filed(vendor, vendor_extracted='ACME Industries, LLC')

        body = get(client, '/receipts', auth, q='Industries').json()

        assert [r['id'] for r in body['results']] == [str(receipt.pk)]

    def test_the_export(self, client, auth, two):
        acme, staples, *_ = two

        rows = csv_rows(get(client, '/export.csv', auth, vendor=acme.pk))

        assert [r['vendor'] for r in rows] == ['ACME', 'ACME']
        assert len(csv_rows(get(client, '/export.csv', auth))) == 3

    def test_the_export_of_another_users_vendor_is_just_the_header(
        self, client, auth, two, other_user
    ):
        theirs = create_vendor(other_user, 'Theirs')

        assert csv_rows(get(client, '/export.csv', auth, vendor=theirs.pk)) == []

    def test_a_bad_filter_alongside_the_vendor_is_still_a_400(self, client, auth, two):
        acme = two[0]

        assert get(client, '/export.csv', auth, vendor=acme.pk, status='bogus').status_code == 400
