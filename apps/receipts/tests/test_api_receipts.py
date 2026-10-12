"""The receipts JWT API's receipt endpoints (`/api/receipts/receipts...`): list with filters and
pagination, read, partial edit, trash and restore, re-run, "not a duplicate" and the file link."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import BillingSettings  # noqa: F401 -- keeps the fixtures' models loaded
from apps.billing.services import add_credits, get_balance, spend_credits

from ..models import Receipt, ReceiptCategory, ReceiptsSettings, ReceiptStatus
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    bearer,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

RECEIPT_FIELDS = {
    'id',
    'status',
    'failure_reason',
    'source',
    'original_filename',
    'content_type',
    'size_bytes',
    'sha256',
    'vendor',
    'amount',
    'currency',
    'receipt_date',
    'document_type',
    'document_number',
    'payment_method',
    'category',
    'notes',
    'possible_duplicate_of',
    'duplicate_dismissed',
    'missing_fields',
    'created_at',
    'updated_at',
    'processed_at',
    'deleted_at',
}


@pytest.fixture
def make(funded_user, fake_storage):
    def _make(**fields):
        fields.setdefault('status', ReceiptStatus.PROCESSED)
        return create_receipt(funded_user, fake_storage, **fields)

    return _make


def ids(response) -> list[str]:
    return [r['id'] for r in response.json()['results']]


class TestAuth:
    @pytest.mark.parametrize(
        ('method', 'path'),
        [
            ('get', '/receipts'),
            ('get', '/receipts/00000000-0000-0000-0000-000000000001'),
            ('patch', '/receipts/00000000-0000-0000-0000-000000000001'),
            ('delete', '/receipts/00000000-0000-0000-0000-000000000001'),
            ('post', '/receipts/00000000-0000-0000-0000-000000000001/restore'),
            ('post', '/receipts/00000000-0000-0000-0000-000000000001/reprocess'),
            ('post', '/receipts/00000000-0000-0000-0000-000000000001/dismiss-duplicate'),
            ('get', '/receipts/00000000-0000-0000-0000-000000000001/file'),
            ('post', '/uploads'),
            ('post', '/uploads/00000000-0000-0000-0000-000000000001/complete'),
            ('get', '/export.csv'),
            ('get', '/categories'),
            ('post', '/categories'),
            ('post', '/categories/reorder'),
            ('patch', '/categories/1'),
            ('delete', '/categories/1'),
            ('get', '/inbox'),
            ('patch', '/inbox'),
            ('post', '/inbox/regenerate'),
        ],
    )
    def test_every_endpoint_requires_a_token(self, client, method, path):
        response = getattr(client, method)(f'/api/receipts{path}')

        assert response.status_code == 401

    def test_a_garbage_token_is_a_401_not_a_500(self, client):
        response = client.get('/api/receipts/receipts', HTTP_AUTHORIZATION='Bearer nonsense')

        assert response.status_code == 401

    def test_an_inactive_user_is_rejected(self, client, funded_user):
        funded_user.status = User.STATUS_INACTIVE
        funded_user.save()

        assert get(client, '/receipts', bearer(funded_user)).status_code == 401


class TestRead:
    def test_a_receipt_has_every_field_and_no_internals(self, client, auth, make):
        category = ReceiptCategory.objects.create(user=make().user, name='Printer stuff')
        receipt = make(
            vendor='Staples',
            amount=Decimal('42.1'),
            currency='USD',
            receipt_date=date(2026, 10, 9),
            document_type='receipt',
            document_number='R-1',
            payment_method='Visa ••4242',
            category=category,
            notes='Printer paper',
            sha256='ab' * 32,
            processed_at=timezone.now(),
        )

        response = get(client, f'/receipts/{receipt.pk}', auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == RECEIPT_FIELDS
        assert body['id'] == str(receipt.pk)
        assert body['amount'] == '42.10'  # a string with two decimals, never a float
        assert body['receipt_date'] == '2026-10-09'
        assert body['category'] == {'id': category.pk, 'name': 'Printer stuff'}
        assert body['vendor'] == 'Staples' and body['notes'] == 'Printer paper'
        assert body['status'] == 'processed' and body['source'] == 'upload'
        assert body['original_filename'] == 'receipt.jpg' and body['sha256'] == 'ab' * 32
        assert body['missing_fields'] == [] and body['deleted_at'] is None
        assert body['possible_duplicate_of'] is None and body['duplicate_dismissed'] is False
        assert 'file_key' not in body and 'ai_result' not in body and 'preview_key' not in body

    def test_missing_values_are_null_and_listed(self, client, auth, make):
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW, vendor='Staples')

        body = get(client, f'/receipts/{receipt.pk}', auth).json()

        assert body['amount'] is None and body['receipt_date'] is None and body['category'] is None
        assert body['missing_fields'] == ['amount', 'receipt_date']

    def test_a_possible_duplicate_is_reported_by_id(self, client, auth, make):
        original = make(vendor='Staples')
        duplicate = make(vendor='Staples', possible_duplicate_of=original)

        body = get(client, f'/receipts/{duplicate.pk}', auth).json()

        assert body['possible_duplicate_of'] == str(original.pk)

    def test_a_trashed_receipt_can_still_be_read(self, client, auth, make):
        receipt = make(deleted_at=timezone.now())

        body = get(client, f'/receipts/{receipt.pk}', auth).json()

        assert body['deleted_at'] is not None

    def test_another_users_receipt_is_a_404(self, client, auth, other_user, fake_storage):
        theirs = create_receipt(other_user, fake_storage, status=ReceiptStatus.PROCESSED)

        for response in (
            get(client, f'/receipts/{theirs.pk}', auth),
            patch(client, f'/receipts/{theirs.pk}', auth, {'vendor': 'x'}),
            delete(client, f'/receipts/{theirs.pk}', auth),
            post(client, f'/receipts/{theirs.pk}/restore', auth),
            post(client, f'/receipts/{theirs.pk}/reprocess', auth),
            post(client, f'/receipts/{theirs.pk}/dismiss-duplicate', auth),
            get(client, f'/receipts/{theirs.pk}/file', auth),
        ):
            assert response.status_code == 404
        theirs.refresh_from_db()
        assert theirs.deleted_at is None and theirs.vendor == ''

    def test_an_unknown_or_unfinished_upload_is_a_404(self, client, auth, make):
        unfinished = make(uploaded=False)

        assert get(client, f'/receipts/{unfinished.pk}', auth).status_code == 404
        assert (
            get(client, '/receipts/00000000-0000-0000-0000-000000000001', auth).status_code == 404
        )


class TestList:
    def test_only_the_callers_live_receipts_are_listed(
        self, client, auth, make, other_user, fake_storage
    ):
        mine = make(vendor='Mine')
        make(vendor='Trashed', deleted_at=timezone.now())
        make(vendor='Unfinished', uploaded=False)
        create_receipt(other_user, fake_storage, vendor='Theirs', status=ReceiptStatus.PROCESSED)

        response = get(client, '/receipts', auth)

        assert response.status_code == 200
        body = response.json()
        assert body['count'] == 1 and ids(response) == [str(mine.pk)]
        assert set(body) == {'count', 'limit', 'offset', 'results'}
        assert body['limit'] == 20 and body['offset'] == 0

    def test_an_empty_list(self, client, auth):
        assert get(client, '/receipts', auth).json() == {
            'count': 0,
            'limit': 20,
            'offset': 0,
            'results': [],
        }

    def test_pagination(self, client, auth, make):
        made = [make(receipt_date=date(2026, 10, day)) for day in range(1, 6)]
        newest_first = [str(r.pk) for r in reversed(made)]

        first = get(client, '/receipts', auth, limit=2)
        second = get(client, '/receipts', auth, limit=2, offset=2)
        last = get(client, '/receipts', auth, limit=2, offset=4)

        assert ids(first) == newest_first[:2] and first.json()['count'] == 5
        assert ids(second) == newest_first[2:4]
        assert ids(last) == newest_first[4:] and last.json()['offset'] == 4
        assert get(client, '/receipts', auth, offset=50).json()['results'] == []

    def test_limit_and_offset_are_clamped(self, client, auth, make):
        make()

        body = get(client, '/receipts', auth, limit=100000, offset=-5).json()
        assert body['limit'] == 100 and body['offset'] == 0
        assert get(client, '/receipts', auth, limit=0).json()['limit'] == 1

    def test_sort_by_receipt_date_and_by_added(self, client, auth, make):
        early = make(receipt_date=date(2026, 1, 1))
        late = make(receipt_date=date(2026, 12, 1))
        undated = make(status=ReceiptStatus.NEEDS_REVIEW)

        # newest first by default, the undated one (which needs a look) on top
        assert ids(get(client, '/receipts', auth)) == [str(undated.pk), str(late.pk), str(early.pk)]
        assert ids(get(client, '/receipts', auth, sort='receipt_date')) == [
            str(early.pk),
            str(late.pk),
            str(undated.pk),
        ]
        assert ids(get(client, '/receipts', auth, sort='created_at')) == [
            str(early.pk),
            str(late.pk),
            str(undated.pk),
        ]
        assert ids(get(client, '/receipts', auth, sort='-created_at')) == [
            str(undated.pk),
            str(late.pk),
            str(early.pk),
        ]

    def test_filter_by_status_one_several_and_in_progress(self, client, auth, make):
        processed = make()
        review = make(status=ReceiptStatus.NEEDS_REVIEW)
        failed = make(status=ReceiptStatus.FAILED)
        waiting = make(status=ReceiptStatus.WAITING_FOR_CREDITS)
        received = make(status=ReceiptStatus.RECEIVED)

        def listed(status):
            return set(ids(get(client, '/receipts', auth, status=status)))

        assert listed('processed') == {str(processed.pk)}
        assert listed('needs_review,failed') == {str(review.pk), str(failed.pk)}
        assert listed('in_progress') == {str(waiting.pk), str(received.pk)}

    def test_filter_by_category_and_uncategorized(self, client, auth, make, funded_user):
        meals = ReceiptCategory.objects.create(user=funded_user, name='Lunches')
        categorized = make(category=meals)
        plain = make()

        assert ids(get(client, '/receipts', auth, category=meals.pk)) == [str(categorized.pk)]
        assert ids(get(client, '/receipts', auth, category='none')) == [str(plain.pk)]

    def test_search_covers_vendor_and_notes(self, client, auth, make):
        by_vendor = make(vendor='Staples Inc')
        by_notes = make(vendor='Other', notes='bought at staples downtown')
        make(vendor='Uber')

        found = set(ids(get(client, '/receipts', auth, q='STAPLES')))

        assert found == {str(by_vendor.pk), str(by_notes.pk)}

    def test_date_range_applies_to_the_receipt_date_inclusively(self, client, auth, make):
        make(receipt_date=date(2026, 9, 30))
        first = make(receipt_date=date(2026, 10, 1))
        last = make(receipt_date=date(2026, 10, 31))
        make(receipt_date=date(2026, 11, 1))
        # added long ago but dated in range: the receipt date decides, not `created_at`
        old = make(receipt_date=date(2026, 10, 15))
        Receipt.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=400))

        response = get(client, '/receipts', auth, date_from='2026-10-01', date_to='2026-10-31')

        assert set(ids(response)) == {str(first.pk), str(last.pk), str(old.pk)}

    def test_only_possible_duplicates(self, client, auth, make):
        original = make(vendor='Staples')
        flagged = make(vendor='Staples', possible_duplicate_of=original)
        make(vendor='Staples', possible_duplicate_of=original, duplicate_dismissed=True)

        assert ids(get(client, '/receipts', auth, duplicates='true')) == [str(flagged.pk)]

    def test_the_trash_is_listed_on_request(self, client, auth, make):
        make(vendor='Live')
        gone = make(vendor='Gone', deleted_at=timezone.now())

        assert ids(get(client, '/receipts', auth, trashed='true')) == [str(gone.pk)]

    @pytest.mark.parametrize(
        'query',
        [
            {'status': 'bogus'},
            {'sort': 'vendor'},
            {'category': 'abc'},
            {'date_from': 'yesterday'},
            {'date_to': '2026-13-45'},
        ],
    )
    def test_a_bad_filter_is_a_400_with_a_detail(self, client, auth, query):
        response = get(client, '/receipts', auth, **query)

        assert response.status_code == 400
        assert response.json()['detail']


class TestUpdate:
    def test_changes_only_the_fields_present(self, client, auth, make):
        receipt = make(vendor='Staples', amount=Decimal('10.00'), notes='keep me')

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor': 'Staples Inc'})

        assert response.status_code == 200
        assert response.json()['vendor'] == 'Staples Inc'
        receipt.refresh_from_db()
        assert receipt.vendor == 'Staples Inc'
        assert receipt.amount == Decimal('10.00') and receipt.notes == 'keep me'
        assert receipt.user_edited_fields == ['vendor']

    def test_every_editable_field(self, client, auth, make, funded_user):
        category = ReceiptCategory.objects.create(user=funded_user, name='Conference travel')
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW)

        response = patch(
            client,
            f'/receipts/{receipt.pk}',
            auth,
            {
                'vendor': 'Delta',
                'amount': 199.5,
                'currency': 'eur',
                'receipt_date': '2026-10-05',
                'document_type': 'invoice',
                'document_number': 'INV-9',
                'payment_method': 'Amex 1234567890123456',
                'category': category.pk,
                'notes': 'Conference trip',
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body['amount'] == '199.50' and body['currency'] == 'EUR'
        assert body['receipt_date'] == '2026-10-05' and body['document_type'] == 'invoice'
        assert body['document_number'] == 'INV-9'
        assert body['category'] == {'id': category.pk, 'name': 'Conference travel'}
        assert body['notes'] == 'Conference trip'
        assert '1234567890123456' not in body['payment_method']  # masked
        assert body['status'] == 'processed'  # all three required fields are now set
        assert body['processed_at'] is not None

    def test_an_amount_can_be_sent_as_a_string(self, client, auth, make):
        receipt = make()

        body = patch(client, f'/receipts/{receipt.pk}', auth, {'amount': '1,234.5'}).json()

        assert body['amount'] == '1234.50'

    def test_null_clears_a_field_and_omitting_does_not(self, client, auth, make, funded_user):
        category = ReceiptCategory.objects.create(user=funded_user, name='Conference travel')
        receipt = make(category=category, notes='x', currency='USD')

        body = patch(client, f'/receipts/{receipt.pk}', auth, {'category': None}).json()

        assert body['category'] is None and body['notes'] == 'x' and body['currency'] == 'USD'

    def test_clearing_a_required_field_sends_it_back_to_review(self, client, auth, make):
        receipt = make(vendor='Staples', amount=Decimal('1.00'), receipt_date=date(2026, 10, 1))

        body = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor': ''}).json()

        assert body['status'] == 'needs_review' and body['missing_fields'] == ['vendor']

    def test_an_empty_body_changes_nothing(self, client, auth, make):
        receipt = make(vendor='Staples')

        response = patch(client, f'/receipts/{receipt.pk}', auth, {})

        assert response.status_code == 200 and response.json()['vendor'] == 'Staples'

    @pytest.mark.parametrize(
        ('payload', 'field'),
        [
            ({'amount': 'lots'}, 'amount'),
            ({'amount': -5}, 'amount'),
            ({'receipt_date': '09/10/2026'}, 'receipt_date'),
            ({'currency': 'dollars'}, 'currency'),
            ({'document_type': 'memo'}, 'document_type'),
            ({'category': 987654}, 'category'),
            ({'notes': 'x' * 6000}, 'notes'),
        ],
    )
    def test_a_bad_value_is_a_400_naming_the_field(self, client, auth, make, payload, field):
        receipt = make(vendor='Staples')

        response = patch(client, f'/receipts/{receipt.pk}', auth, payload)

        assert response.status_code == 400
        assert response.json()['detail'].startswith(f'{field}:')
        receipt.refresh_from_db()
        assert receipt.vendor == 'Staples' and receipt.user_edited_fields == []

    def test_another_users_category_is_a_400(self, client, auth, make, other_user):
        theirs = ReceiptCategory.objects.create(user=other_user, name='Theirs')
        receipt = make()

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'category': theirs.pk})

        assert response.status_code == 400

    def test_a_trashed_receipt_cannot_be_edited(self, client, auth, make):
        receipt = make(deleted_at=timezone.now())

        response = patch(client, f'/receipts/{receipt.pk}', auth, {'vendor': 'x'})

        assert response.status_code == 400 and 'Trash' in response.json()['detail']

    def test_extracted_fields_wait_for_the_pipeline_but_notes_do_not(self, client, auth, make):
        receipt = make(status=ReceiptStatus.PROCESSING)

        assert patch(client, f'/receipts/{receipt.pk}', auth, {'vendor': 'x'}).status_code == 400
        assert patch(client, f'/receipts/{receipt.pk}', auth, {'notes': 'n'}).status_code == 200


class TestTrashAndRestore:
    def test_delete_moves_to_the_trash(self, client, auth, make):
        receipt = make()

        response = delete(client, f'/receipts/{receipt.pk}', auth)

        assert response.status_code == 204
        receipt.refresh_from_db()
        assert receipt.deleted_at is not None
        assert get(client, '/receipts', auth).json()['count'] == 0
        assert get(client, '/receipts', auth, trashed='true').json()['count'] == 1

    def test_deleting_twice_is_harmless(self, client, auth, make):
        receipt = make()

        assert delete(client, f'/receipts/{receipt.pk}', auth).status_code == 204
        assert delete(client, f'/receipts/{receipt.pk}', auth).status_code == 204

    def test_restore_brings_it_back(self, client, auth, make):
        receipt = make(deleted_at=timezone.now())

        response = post(client, f'/receipts/{receipt.pk}/restore', auth)

        assert response.status_code == 200 and response.json()['deleted_at'] is None
        assert get(client, '/receipts', auth).json()['count'] == 1

    def test_restoring_a_live_receipt_is_a_no_op(self, client, auth, make):
        receipt = make()

        response = post(client, f'/receipts/{receipt.pk}/restore', auth)

        assert response.status_code == 200 and response.json()['deleted_at'] is None


class TestDismissDuplicate:
    def test_clears_the_flag_for_good(self, client, auth, make):
        original = make(vendor='Staples')
        duplicate = make(vendor='Staples', possible_duplicate_of=original)

        response = post(client, f'/receipts/{duplicate.pk}/dismiss-duplicate', auth)

        assert response.status_code == 200
        body = response.json()
        assert body['possible_duplicate_of'] is None and body['duplicate_dismissed'] is True
        assert get(client, '/receipts', auth, duplicates='true').json()['count'] == 0


class TestReprocess:
    def test_runs_the_extraction_again_and_charges(
        self, client, auth, make, funded_user, django_capture_on_commit_callbacks, fake_ai
    ):
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW)
        balance = get_balance(funded_user)

        with django_capture_on_commit_callbacks(execute=True):
            response = post(client, f'/receipts/{receipt.pk}/reprocess', auth)

        assert response.status_code == 200
        assert len(fake_ai.calls) == 1
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED and receipt.vendor == 'Staples'
        assert get_balance(funded_user) == balance - 1

    def test_not_enough_credits_is_a_402(self, client, auth, make, funded_user, fake_ai):
        spend_credits(funded_user, get_balance(funded_user), description='x', source='test')
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW)

        response = post(client, f'/receipts/{receipt.pk}/reprocess', auth)

        assert response.status_code == 402
        assert 'credits' in response.json()['detail'].lower()
        assert fake_ai.calls == []
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW

    def test_a_frozen_account_is_a_402(self, client, auth, make, freeze):
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW)

        response = post(client, f'/receipts/{receipt.pk}/reprocess', auth)

        assert response.status_code == 402 and 'paused' in response.json()['detail']

    def test_a_receipt_being_processed_is_a_400(self, client, auth, make):
        receipt = make(status=ReceiptStatus.PROCESSING)

        response = post(client, f'/receipts/{receipt.pk}/reprocess', auth)

        assert response.status_code == 400
        assert 'already being processed' in response.json()['detail']

    def test_a_file_that_can_never_be_read_is_a_400(self, client, auth, make):
        receipt = make(status=ReceiptStatus.FAILED, failure_reason='unsupported_type')

        assert post(client, f'/receipts/{receipt.pk}/reprocess', auth).status_code == 400

    def test_a_price_of_zero_needs_no_credits(
        self, client, auth, make, funded_user, receipts_settings
    ):
        ReceiptsSettings.objects.filter(pk=1).update(price_per_file=0)
        spend_credits(funded_user, get_balance(funded_user), description='x', source='test')
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW)

        assert post(client, f'/receipts/{receipt.pk}/reprocess', auth).status_code == 200

    def test_it_is_rate_limited_like_the_web(self, client, auth, make, settings):
        limit_rule(settings, 'RECEIPTS_REPROCESS_USER')
        receipt = make(status=ReceiptStatus.NEEDS_REVIEW)

        assert post(client, f'/receipts/{receipt.pk}/reprocess', auth).status_code == 200
        limited = post(client, f'/receipts/{receipt.pk}/reprocess', auth)

        assert limited.status_code == 429
        assert limited['Retry-After'] and limited.json()['detail']


class TestFile:
    def test_download_is_an_attachment_url_by_default(self, client, auth, make, fake_storage):
        receipt = make(original_filename='staples.jpg')

        response = get(client, f'/receipts/{receipt.pk}/file', auth)

        assert response.status_code == 200
        body = response.json()
        assert body['url'] == f'https://fake-s3.test/{receipt.file_key}?get&disposition=attachment'
        assert body['disposition'] == 'attachment' and body['filename'] == 'staples.jpg'
        assert body['expires_in'] > 0
        assert fake_storage.get_urls[-1]['disposition'] == 'attachment'
        assert 'file_key' not in body

    def test_inline_asks_for_the_preview(self, client, auth, make, fake_storage):
        receipt = make()

        response = get(client, f'/receipts/{receipt.pk}/file', auth, disposition='inline')

        assert response.status_code == 200
        assert response.json()['url'].endswith('disposition=inline')
        assert fake_storage.get_urls[-1]['disposition'] == 'inline'

    def test_an_unknown_disposition_is_a_400(self, client, auth, make):
        receipt = make()

        assert get(client, f'/receipts/{receipt.pk}/file', auth, disposition='x').status_code == 400

    def test_an_email_body_has_no_inline_preview(self, client, auth, make):
        receipt = make(data=b'<html><body>hi</body></html>', content_type='text/html')

        response = get(client, f'/receipts/{receipt.pk}/file', auth, disposition='inline')

        assert response.status_code == 400
        assert get(client, f'/receipts/{receipt.pk}/file', auth).status_code == 200

    def test_a_frozen_account_is_a_402(self, client, auth, make, freeze):
        receipt = make()

        for query in ({}, {'disposition': 'inline'}):
            response = get(client, f'/receipts/{receipt.pk}/file', auth, **query)
            assert response.status_code == 402

    def test_topping_up_unfreezes_nothing_by_itself_but_reading_still_works(
        self, client, auth, make, freeze
    ):
        # Listing and reading a frozen account's receipts is allowed; only the stored files
        # (and new work) are refused.
        receipt = make()
        add_credits(receipt.user, 5, description='x', source='test')

        assert get(client, '/receipts', auth).status_code == 200
        assert get(client, f'/receipts/{receipt.pk}', auth).status_code == 200
