"""The receipts JWT API's upload flow and CSV export (`/api/receipts/uploads...`, `export.csv`),
driven against the fake storage and AI: start -> (the client's PUT, simulated by seeding the
object) -> complete -> processed."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import get_balance

from ..models import Receipt, ReceiptCategory, ReceiptsSettings, ReceiptStatus
from . import samples
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    get,
    limit_rule,
    other_auth,
    post,
)
from .factories import create_receipt
from .fakes import b64_sha256

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def start_body(data=None, **overrides):
    data = samples.jpeg_bytes() if data is None else data
    body = {
        'filename': 'IMG_2041.jpg',
        'size': len(data),
        'content_type': 'image/jpeg',
        'checksum_sha256': b64_sha256(data),
    }
    body.update(overrides)
    return body


def start(client, auth, data=None, **overrides):
    return post(client, '/uploads', auth, start_body(data, **overrides))


class TestStart:
    def test_returns_a_presigned_put_for_the_signed_size_and_checksum(
        self, client, auth, funded_user, fake_storage
    ):
        data = samples.jpeg_bytes()

        response = start(client, auth, data)

        assert response.status_code == 201
        body = response.json()
        assert set(body) == {'receipt_id', 'url', 'headers', 'expires_in'}
        receipt = Receipt.objects.get(pk=body['receipt_id'])
        assert receipt.user_id == funded_user.pk and receipt.uploaded is False
        assert receipt.source == 'api'
        assert body['url'] == f'https://fake-s3.test/{receipt.file_key}?put'
        assert body['headers'] == {'x-amz-checksum-sha256': b64_sha256(data)}
        assert body['expires_in'] > 0
        assert fake_storage.put_urls[0]['content_length'] == len(data)

    def test_the_content_type_is_optional(self, client, auth):
        data = samples.png_bytes()

        response = post(
            client,
            '/uploads',
            auth,
            {'filename': 'scan.png', 'size': len(data), 'checksum_sha256': b64_sha256(data)},
        )

        assert response.status_code == 201

    @pytest.mark.parametrize(
        ('overrides', 'message'),
        [
            ({'filename': 'notes.docx', 'content_type': 'application/msword'}, 'not supported'),
            ({'size': 0}, 'empty'),
            ({'size': 10**9}, 'too large'),
            ({'checksum_sha256': 'not-base64!'}, 'checksum'),
            ({'filename': '  '}, 'needs a name'),
        ],
    )
    def test_a_rejected_file_is_a_400_with_a_message(self, client, auth, overrides, message):
        response = start(client, auth, **overrides)

        assert response.status_code == 400
        assert message in response.json()['detail']
        assert not Receipt.objects.exists()

    def test_a_missing_field_is_ninjas_own_422(self, client, auth):
        assert post(client, '/uploads', auth, {'filename': 'a.jpg'}).status_code == 422

    def test_the_daily_limit_is_a_400(self, client, auth, receipts_settings):
        ReceiptsSettings.objects.filter(pk=1).update(max_files_per_user_per_day=1)

        assert start(client, auth).status_code == 201
        response = start(client, auth)

        assert response.status_code == 400
        assert 'up to 1 receipts per day' in response.json()['detail']

    def test_a_frozen_account_is_a_402(self, client, auth, freeze):
        response = start(client, auth)

        assert response.status_code == 402 and 'paused' in response.json()['detail']
        assert not Receipt.objects.exists()

    def test_it_is_rate_limited_per_user(self, client, auth, settings):
        limit_rule(settings, 'RECEIPTS_UPLOAD_USER', limit=2, window=60)

        assert start(client, auth).status_code == 201
        assert start(client, auth).status_code == 201
        limited = start(client, auth)

        assert limited.status_code == 429
        assert limited['Retry-After'] and limited.json()['detail']


class TestComplete:
    def upload(self, client, auth, funded_user, fake_storage, data=None):
        data = samples.jpeg_bytes() if data is None else data
        started = start(client, auth, data).json()
        fake_storage.seed(f'receipts/users/{funded_user.pk}/files/{started["receipt_id"]}', data)
        return started['receipt_id']

    def test_the_whole_flow_ends_with_a_processed_receipt(
        self,
        client,
        auth,
        funded_user,
        fake_storage,
        fake_ai,
        django_capture_on_commit_callbacks,
    ):
        receipt_id = self.upload(client, auth, funded_user, fake_storage)
        balance = get_balance(funded_user)

        with django_capture_on_commit_callbacks(execute=True):
            response = post(client, f'/uploads/{receipt_id}/complete', auth)

        assert response.status_code == 200
        body = response.json()
        assert body['id'] == receipt_id and body['source'] == 'api'
        assert body['original_filename'] == 'IMG_2041.jpg'
        receipt = Receipt.objects.get(pk=receipt_id)
        assert receipt.uploaded is True
        assert receipt.status == ReceiptStatus.PROCESSED  # the fake AI answered inline
        assert receipt.vendor == 'Staples'
        assert len(fake_ai.calls) == 1
        assert get_balance(funded_user) == balance - 1
        listed = get(client, '/receipts', auth).json()
        assert listed['count'] == 1 and listed['results'][0]['vendor'] == 'Staples'

    def test_completing_twice_is_harmless(self, client, auth, funded_user, fake_storage):
        receipt_id = self.upload(client, auth, funded_user, fake_storage)

        assert post(client, f'/uploads/{receipt_id}/complete', auth).status_code == 200
        assert post(client, f'/uploads/{receipt_id}/complete', auth).status_code == 200
        assert Receipt.objects.count() == 1

    def test_before_the_put_it_is_a_400_and_can_be_retried(
        self, client, auth, funded_user, fake_storage
    ):
        data = samples.jpeg_bytes()
        started = start(client, auth, data).json()

        early = post(client, f'/uploads/{started["receipt_id"]}/complete', auth)
        fake_storage.seed(f'receipts/users/{funded_user.pk}/files/{started["receipt_id"]}', data)
        later = post(client, f'/uploads/{started["receipt_id"]}/complete', auth)

        assert early.status_code == 400 and 'not been uploaded' in early.json()['detail']
        assert later.status_code == 200

    def test_a_file_whose_contents_are_not_what_it_says_fails_the_receipt(
        self, client, auth, funded_user, fake_storage
    ):
        data = b'MZ not an image at all' * 10
        started = post(
            client,
            '/uploads',
            auth,
            start_body(data, filename='fake.jpg', content_type='image/jpeg'),
        ).json()
        fake_storage.seed(f'receipts/users/{funded_user.pk}/files/{started["receipt_id"]}', data)

        response = post(client, f'/uploads/{started["receipt_id"]}/complete', auth)

        assert response.status_code == 400
        receipt = Receipt.objects.get(pk=started['receipt_id'])
        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.failure_reason == 'unsupported_type'

    def test_another_users_upload_is_a_404(self, client, auth, other_auth, funded_user):
        started = start(client, auth).json()

        assert (
            post(client, f'/uploads/{started["receipt_id"]}/complete', other_auth).status_code
            == 404
        )

    def test_an_unknown_upload_is_a_404(self, client, auth):
        response = post(client, '/uploads/00000000-0000-0000-0000-000000000001/complete', auth)

        assert response.status_code == 404

    def test_it_has_its_own_rate_limit(self, client, auth, funded_user, fake_storage, settings):
        receipt_id = self.upload(client, auth, funded_user, fake_storage)
        limit_rule(settings, 'RECEIPTS_UPLOAD_COMPLETE_USER')

        assert post(client, f'/uploads/{receipt_id}/complete', auth).status_code == 200
        limited = post(client, f'/uploads/{receipt_id}/complete', auth)

        assert limited.status_code == 429 and limited['Retry-After']

    def test_the_budget_is_shared_with_the_web_endpoints(
        self, client, auth, web, funded_user, settings
    ):
        limit_rule(settings, 'RECEIPTS_UPLOAD_USER')
        body = start_body()

        web_response = web.post(
            '/receipts/upload/start/',
            data={
                'name': body['filename'],
                'size': body['size'],
                'content_type': body['content_type'],
                'checksum_sha256': body['checksum_sha256'],
            },
            content_type='application/json',
        )
        api_response = start(client, auth)

        assert web_response.status_code == 201
        assert api_response.status_code == 429


def csv_rows(response) -> list[dict[str, str]]:
    text = b''.join(response.streaming_content).decode()
    return list(csv.DictReader(io.StringIO(text)))


class TestExport:
    @pytest.fixture
    def make(self, funded_user, fake_storage):
        def _make(**fields):
            fields.setdefault('status', ReceiptStatus.PROCESSED)
            return create_receipt(funded_user, fake_storage, **fields)

        return _make

    def test_the_csv_has_the_documented_columns_and_the_live_receipts(
        self, client, auth, make, funded_user
    ):
        category = ReceiptCategory.objects.create(user=funded_user, name='Printer stuff')
        make(
            vendor='Staples',
            amount=Decimal('42.1'),
            currency='USD',
            receipt_date=date(2026, 10, 9),
            category=category,
            document_type='receipt',
            document_number='R-1',
            payment_method='Visa 4242',
            notes='paper',
            source='email',
        )
        make(vendor='Trashed', deleted_at=timezone.now())
        make(vendor='Unfinished', uploaded=False)

        response = get(client, '/export.csv', auth)

        assert response.status_code == 200
        assert response['Content-Type'].startswith('text/csv')
        assert response['Content-Disposition'].startswith('attachment; filename="receipts-')
        assert response['Cache-Control'] == 'private, no-store'
        rows = csv_rows(response)
        assert len(rows) == 1
        row = rows[0]
        assert list(row) == [
            'receipt_date',
            'vendor',
            'amount',
            'currency',
            'category',
            'document_type',
            'document_number',
            'payment_method',
            'notes',
            'status',
            'source',
            'added',
            'original_filename',
            'vendor_as_read',
        ]
        assert row['vendor'] == 'Staples' and row['amount'] == '42.10'
        assert row['receipt_date'] == '2026-10-09' and row['category'] == 'Printer stuff'
        assert row['source'] == 'email' and row['status'] == 'processed'

    def test_only_the_callers_receipts(self, client, auth, make, other_user, fake_storage):
        make(vendor='Mine')
        create_receipt(other_user, fake_storage, vendor='Theirs', status=ReceiptStatus.PROCESSED)

        assert [r['vendor'] for r in csv_rows(get(client, '/export.csv', auth))] == ['Mine']

    def test_it_takes_the_same_filters_as_the_list(self, client, auth, make):
        make(vendor='Staples', receipt_date=date(2026, 10, 9))
        make(vendor='Uber', receipt_date=date(2026, 9, 1))
        make(vendor='Broken', status=ReceiptStatus.FAILED, receipt_date=date(2026, 10, 10))

        def vendors(**query):
            return {r['vendor'] for r in csv_rows(get(client, '/export.csv', auth, **query))}

        assert vendors(q='stap') == {'Staples'}
        assert vendors(date_from='2026-10-01') == {'Staples', 'Broken'}
        assert vendors(date_to='2026-09-30') == {'Uber'}
        assert vendors(status='failed') == {'Broken'}
        assert vendors(sort='receipt_date') == {'Staples', 'Uber', 'Broken'}

    def test_a_spreadsheet_formula_is_defused(self, client, auth, make):
        make(vendor='=HYPERLINK("http://evil.test")')

        assert csv_rows(get(client, '/export.csv', auth))[0]['vendor'].startswith("'=")

    def test_a_bad_filter_is_a_400_not_a_broken_download(self, client, auth, make):
        make()

        for query in ({'status': 'bogus'}, {'sort': 'x'}, {'date_from': 'nope'}):
            response = get(client, '/export.csv', auth, **query)
            assert response.status_code == 400 and response.json()['detail']

    def test_a_bad_filter_does_not_spend_the_export_budget(self, client, auth, settings):
        limit_rule(settings, 'RECEIPTS_EXPORT_USER')

        for _ in range(3):
            assert get(client, '/export.csv', auth, status='bogus').status_code == 400
        assert get(client, '/export.csv', auth, date_from='nope').status_code == 400

        assert get(client, '/export.csv', auth).status_code == 200  # the one export is intact
        assert get(client, '/export.csv', auth).status_code == 429

    def test_the_budget_is_shared_with_the_web_export(self, client, auth, web, settings):
        limit_rule(settings, 'RECEIPTS_EXPORT_USER')

        assert web.get(reverse('receipts:export')).status_code == 200
        assert get(client, '/export.csv', auth).status_code == 429

    def test_the_api_export_counts_against_the_web_export(self, client, auth, web, settings):
        limit_rule(settings, 'RECEIPTS_EXPORT_USER')

        assert get(client, '/export.csv', auth).status_code == 200
        assert web.get(reverse('receipts:export')).status_code == 429

    def test_an_empty_export_is_just_the_header(self, client, auth):
        response = get(client, '/export.csv', auth)

        assert response.status_code == 200
        assert csv_rows(response) == []

    def test_it_is_rate_limited(self, client, auth, settings):
        limit_rule(settings, 'RECEIPTS_EXPORT_USER')

        assert get(client, '/export.csv', auth).status_code == 200
        limited = get(client, '/export.csv', auth)

        assert limited.status_code == 429 and limited['Retry-After']
