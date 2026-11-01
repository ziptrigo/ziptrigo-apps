"""The upload page and its JSON endpoints (`views/uploads.py`), driven end to end against the fake
storage: start -> (the browser's PUT, simulated by seeding the object) -> complete -> processed."""

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.billing.services import get_balance

from ..models import Receipt, ReceiptsSettings, ReceiptStatus
from . import samples
from .factories import create_receipt
from .fakes import b64_sha256

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    # Two requests per fixed rate-limit window: a real clock crossing a boundary breaks them.
    pytest.mark.usefixtures('ratelimit_clock'),
]


def post_json(client, url, body):
    return client.post(url, data=json.dumps(body), content_type='application/json')


def start_body(data=None, **overrides):
    data = samples.jpeg_bytes() if data is None else data
    body = {
        'name': 'IMG_2041.jpg',
        'size': len(data),
        'content_type': 'image/jpeg',
        'checksum_sha256': b64_sha256(data),
    }
    body.update(overrides)
    return body


def start(web, data=None, **overrides):
    return post_json(web, reverse('receipts:upload-start'), start_body(data, **overrides))


class TestPage:
    def test_the_drop_zone_and_the_camera_input(self, web, receipts_settings):
        response = web.get(reverse('receipts:upload'))
        content = response.content.decode()

        assert response.status_code == 200
        assert (
            'Drop files here' in content and 'Choose files' in content and 'Take photo' in content
        )
        assert 'accept="image/*" capture="environment"' in content
        assert 'receipts/js/upload.js' in content
        # `initDropZone` lives in core's shared script, which must load before the page's own.
        assert content.index('core/js/drop_zone.js') < content.index('receipts/js/upload.js')
        assert 'PDF, JPG, PNG, HEIC, WebP' in content
        assert 'up to 15 MB' in content and '1 credit each' in content
        assert f'data-start-url="{reverse("receipts:upload-start")}"' in content

    def test_the_page_offers_only_the_allowed_types(self, web, receipts_settings):
        ReceiptsSettings.objects.filter(pk=1).update(allowed_types='application/pdf,image/png')

        content = web.get(reverse('receipts:upload')).content.decode()

        assert 'PDF, PNG &middot;' in content
        assert 'accept=".pdf,.png,application/pdf,image/png"' in content
        assert 'data-extensions=".pdf,.png"' in content
        assert 'data-types="application/pdf,image/png"' in content
        assert 'data-types-text="PDF or PNG"' in content
        assert 'HEIC' not in content

    def test_it_shows_the_balance(self, web, funded_user):
        assert 'Balance:' in web.get(reverse('receipts:upload')).content.decode()
        assert '100 credits' in web.get(reverse('receipts:upload')).content.decode()

    def test_a_free_price_and_a_custom_limit(self, web, receipts_settings):
        ReceiptsSettings.objects.filter(pk=1).update(price_per_file=0, max_file_bytes=5 * 1024**2)

        content = web.get(reverse('receipts:upload')).content.decode()

        assert 'up to 5 MB' in content and 'free' in content

    def test_a_balance_too_low_to_read_a_receipt_is_explained(self, web, user, receipts_settings):
        ReceiptsSettings.objects.filter(pk=1).update(price_per_file=500)

        content = web.get(reverse('receipts:upload')).content.decode()

        assert "don't have enough credits" in content

    def test_a_frozen_account_cannot_upload(self, web, freeze):
        content = web.get(reverse('receipts:upload')).content.decode()

        assert 'Uploads are paused' in content
        assert content.count('disabled') >= 2
        assert 'data-frozen="true"' in content


class TestStart:
    def test_returns_a_presigned_put_for_the_signed_size_and_checksum(
        self, web, funded_user, fake_storage
    ):
        data = samples.jpeg_bytes()

        response = start(web, data)

        assert response.status_code == 201
        body = response.json()
        receipt = Receipt.objects.get(pk=body['receipt_id'])
        assert receipt.user_id == funded_user.pk and receipt.uploaded is False
        assert body['url'] == f'https://fake-s3.test/{receipt.file_key}?put'
        assert body['headers'] == {'x-amz-checksum-sha256': b64_sha256(data)}
        assert body['expires_in'] > 0
        assert fake_storage.put_urls[0]['content_length'] == len(data)

    @pytest.mark.parametrize(
        ('overrides', 'message'),
        [
            ({'name': 'notes.docx', 'content_type': 'application/msword'}, 'not supported'),
            ({'size': 0}, 'empty'),
            ({'size': 10**9}, 'too large'),
            ({'checksum_sha256': 'not-base64!'}, 'checksum'),
            ({'size': 'big'}, 'file size'),
            ({'name': '  '}, 'needs a name'),
        ],
    )
    def test_a_rejected_file_is_a_422_with_a_message(self, web, overrides, message):
        response = start(web, **overrides)

        assert response.status_code == 422
        assert message in response.json()['error']
        assert not Receipt.objects.exists()

    @pytest.mark.parametrize(
        'raw',
        [
            '1e400',  # infinity: int() raises OverflowError
            '-1e400',
            '1.5',
            '-5',
            'true',
            'null',
            '[1]',
            '"1.5"',
            '"abc"',
            '{"a": 1}',
        ],
    )
    def test_a_size_that_is_not_a_whole_number_of_bytes_is_a_422(self, web, raw):
        body = json.dumps(start_body()).replace(
            f'"size": {len(samples.jpeg_bytes())}', '"size": ' + raw
        )
        assert '"size": ' + raw in body

        response = web.post(
            reverse('receipts:upload-start'), data=body, content_type='application/json'
        )

        assert response.status_code == 422
        assert 'file size' in response.json()['error']
        assert not Receipt.objects.exists()

    def test_a_huge_size_is_refused_as_too_large_not_crashed_on(self, web):
        response = start(web, size=10**400)

        assert response.status_code == 422
        assert 'too large' in response.json()['error']

    def test_the_daily_limit_is_a_422(self, web, funded_user):
        ReceiptsSettings.load()
        ReceiptsSettings.objects.filter(pk=1).update(max_files_per_user_per_day=1)

        assert start(web).status_code == 201
        response = start(web)

        assert response.status_code == 422
        assert 'up to 1 receipts per day' in response.json()['error']

    def test_a_frozen_account_gets_a_402_and_a_way_to_top_up(self, web, freeze):
        response = start(web)

        assert response.status_code == 402
        body = response.json()
        assert body['frozen'] is True
        assert body['top_up_url'] == reverse('billing:credits-history')
        assert 'paused' in body['error']
        assert not Receipt.objects.exists()

    def test_garbage_json_is_a_422_not_a_500(self, web):
        response = web.post(
            reverse('receipts:upload-start'), data='{nope', content_type='application/json'
        )

        assert response.status_code == 422

    def test_get_is_not_allowed(self, web):
        assert web.get(reverse('receipts:upload-start')).status_code == 405

    def test_it_requires_login(self, client):
        response = client.post(
            reverse('receipts:upload-start'), data='{}', content_type='application/json'
        )

        assert response.status_code == 302

    def test_it_is_csrf_protected(self, funded_user):
        client = Client(enforce_csrf_checks=True)
        client.force_login(funded_user)

        response = post_json(client, reverse('receipts:upload-start'), start_body())

        assert response.status_code == 403

    def test_it_is_rate_limited_per_user(self, web, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'RECEIPTS_UPLOAD_USER': (2, 60)}

        assert start(web).status_code == 201
        assert start(web).status_code == 201
        limited = start(web)

        assert limited.status_code == 429
        assert limited['Retry-After']
        assert 'error' in limited.json()


class TestComplete:
    def test_the_whole_flow_ends_with_a_processed_receipt(
        self, web, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        data = samples.jpeg_bytes()
        started = start(web, data).json()
        fake_storage.seed(f'receipts/users/{funded_user.pk}/files/{started["receipt_id"]}', data)
        balance = get_balance(funded_user)

        with django_capture_on_commit_callbacks(execute=True):
            response = post_json(
                web, reverse('receipts:upload-complete', args=[started['receipt_id']]), {}
            )

        assert response.status_code == 200
        body = response.json()
        assert body['ok'] is True
        assert body['url'] == reverse('receipts:detail', args=[started['receipt_id']])
        receipt = Receipt.objects.get(pk=started['receipt_id'])
        assert receipt.uploaded is True
        assert receipt.status == ReceiptStatus.PROCESSED  # the fake AI answered inline
        assert receipt.vendor == 'Staples'
        assert len(fake_ai.calls) == 1
        assert get_balance(funded_user) == balance - 1

    def test_completing_twice_is_harmless(self, web, funded_user, fake_storage):
        data = samples.jpeg_bytes()
        started = start(web, data).json()
        fake_storage.seed(f'receipts/users/{funded_user.pk}/files/{started["receipt_id"]}', data)
        url = reverse('receipts:upload-complete', args=[started['receipt_id']])

        assert post_json(web, url, {}).status_code == 200
        assert post_json(web, url, {}).status_code == 200

    def test_a_file_that_never_arrived_is_a_422(self, web):
        started = start(web).json()

        response = post_json(
            web, reverse('receipts:upload-complete', args=[started['receipt_id']]), {}
        )

        assert response.status_code == 422
        assert 'not been uploaded' in response.json()['error']

    def test_a_file_that_is_not_what_it_claimed_is_rejected_and_kept_as_failed(
        self, web, funded_user, fake_storage
    ):
        bad = b'MZ this is not an image'
        started = start(web, bad).json()
        fake_storage.seed(f'receipts/users/{funded_user.pk}/files/{started["receipt_id"]}', bad)

        response = post_json(
            web, reverse('receipts:upload-complete', args=[started['receipt_id']]), {}
        )

        assert response.status_code == 422
        receipt = Receipt.objects.get(pk=started['receipt_id'])
        assert (
            receipt.status == ReceiptStatus.FAILED and receipt.failure_reason == 'unsupported_type'
        )

    def test_another_users_upload_is_a_404(self, web, other_user, fake_storage):
        theirs = create_receipt(other_user, fake_storage, uploaded=False)

        response = post_json(web, reverse('receipts:upload-complete', args=[theirs.pk]), {})

        assert response.status_code == 404

    def test_it_requires_login(self, client, funded_user, fake_storage):
        receipt = create_receipt(funded_user, fake_storage, uploaded=False)

        response = client.post(reverse('receipts:upload-complete', args=[receipt.pk]))

        assert response.status_code == 302

    def test_it_is_csrf_protected(self, funded_user, fake_storage):
        receipt = create_receipt(funded_user, fake_storage, uploaded=False)
        client = Client(enforce_csrf_checks=True)
        client.force_login(funded_user)

        response = post_json(client, reverse('receipts:upload-complete', args=[receipt.pk]), {})

        assert response.status_code == 403

    def test_it_is_rate_limited_per_user_even_when_nothing_changes(self, web, settings):
        # A receipt whose object never arrived stays `uploaded=False`: without its own cap, one
        # started upload could be completed in a loop, one S3 HEAD per call.
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_UPLOAD_COMPLETE_USER': (2, 60),
        }
        started = start(web).json()
        url = reverse('receipts:upload-complete', args=[started['receipt_id']])

        assert post_json(web, url, {}).status_code == 422
        assert post_json(web, url, {}).status_code == 422
        limited = post_json(web, url, {})

        assert limited.status_code == 429
        assert limited['Retry-After']
        assert 'error' in limited.json()

    def test_start_and_complete_have_separate_budgets(self, web, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_UPLOAD_USER': (1, 60),
            'RECEIPTS_UPLOAD_COMPLETE_USER': (5, 60),
        }
        started = start(web).json()
        assert start(web).status_code == 429  # start's budget is spent...

        response = post_json(
            web, reverse('receipts:upload-complete', args=[started['receipt_id']]), {}
        )

        assert response.status_code == 422  # ...complete still answers (the file never arrived)
