"""The file export endpoints (`/api/receipts/exports`, issue #315), over Django's test client:
the quote, creating (every status code, the cost confirmation, the budgets a refusal does not
spend), the list and one export, a zip file's download URL, deleting, and what is another
user's."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import add_credits, get_balance, spend_credits
from apps.core import ratelimit

from ..models import ExportStatus, ReceiptExport, ReceiptStatus
from ..services import exports as svc
from .api_support import (
    BASE,
    auth,  # noqa: F401 -- fixtures
    delete,
    get,
    limit_rule,
    other_auth,  # noqa: F401
    post,
)
from .factories import create_receipt

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    # Requests per fixed rate-limit window: a real clock crossing a boundary breaks them.
    pytest.mark.usefixtures('ratelimit_clock'),
]


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


def transactions(user) -> int:
    return CreditTransaction.objects.filter(user=user).count()


class TestQuote:
    def test_counts_size_parts_cost_and_balance(self, client, auth, three):
        response = post(client, '/exports/quote', auth)

        assert response.status_code == 200
        assert response.json() == {
            'file_count': 3,
            'total_bytes': 70,
            'part_count': 1,
            'price_per_file': 3,
            'cost': 9,
            'balance': 100,
            'affordable': True,
            'frozen': False,
            'retention_days': 30,
            'part_bytes': response.json()['part_bytes'],
        }
        assert response.json()['part_bytes'] > 0

    def test_it_takes_the_lists_filters(self, client, auth, three):
        def count(**body):
            return post(client, '/exports/quote', auth, body).json()['file_count']

        assert count(q='acme') == 2
        assert count(status='processed') == 2
        assert count(date_from='2026-01-01') == 1
        assert count(date_to='2025-12-31') == 1
        assert count(vendor=999999) == 0
        assert count(duplicates=True) == 0

    def test_an_unaffordable_one_is_still_quoted(self, client, auth, three, funded_user):
        spend_credits(funded_user, 95, description='x', source='test')
        body = post(client, '/exports/quote', auth).json()
        assert body['cost'] == 9 and body['balance'] == 5 and body['affordable'] is False

    def test_a_frozen_account_is_told_in_the_quote(self, client, auth, three, freeze):
        assert post(client, '/exports/quote', auth).json()['frozen'] is True

    def test_nothing_to_export_is_quoted_as_zero(self, client, auth):
        body = post(client, '/exports/quote', auth).json()
        assert body['file_count'] == 0 and body['cost'] == 0

    def test_only_the_callers_files(self, client, auth, three, other_user, fake_storage):
        create_receipt(other_user, fake_storage, vendor='Theirs')
        assert post(client, '/exports/quote', auth).json()['file_count'] == 3

    @pytest.mark.parametrize(
        'body',
        [
            {'layout': 'sideways'},
            {'status': 'nonsense'},
            {'date_from': 'yesterday'},
            {'date_to': '2026-13-45'},
        ],
    )
    def test_bad_filters_or_layout_are_a_400_that_spends_no_budget(
        self, client, auth, settings, funded_user, body
    ):
        limit_rule(settings, 'RECEIPTS_EXPORT_DIALOG_USER')

        assert post(client, '/exports/quote', auth, body).status_code == 400
        assert post(client, '/exports/quote', auth).status_code == 200

    def test_the_layout_error_says_what_to_choose(self, client, auth):
        response = post(client, '/exports/quote', auth, {'layout': 'x'})
        assert response.status_code == 400
        assert response.json()['detail'] == 'layout: Choose how to organize the files.'

    def test_an_unknown_key_is_a_422(self, client, auth):
        assert post(client, '/exports/quote', auth, {'expected_cost': 1}).status_code == 422

    def test_it_uses_the_dialogs_budget(self, client, auth, settings):
        limit_rule(settings, 'RECEIPTS_EXPORT_DIALOG_USER', limit=2, window=60)

        assert post(client, '/exports/quote', auth).status_code == 200
        assert post(client, '/exports/quote', auth).status_code == 200
        limited = post(client, '/exports/quote', auth)
        assert limited.status_code == 429 and limited['Retry-After']

    def test_it_writes_and_charges_nothing(self, client, auth, three, funded_user):
        post(client, '/exports/quote', auth)
        assert not ReceiptExport.objects.exists() and get_balance(funded_user) == 100

    def test_it_needs_a_token(self, client):
        assert client.post(f'{BASE}/exports/quote', {}).status_code == 401


class TestCreate:
    def test_it_starts_and_charges_the_export(self, client, auth, three, funded_user):
        response = post(client, '/exports', auth, {'layout': 'vendor_year', 'q': 'acme'})

        assert response.status_code == 201
        body = response.json()
        assert body['status'] == 'building' and body['layout'] == 'vendor_year'
        assert body['file_count'] == 2 and body['part_count'] == 1
        assert body['price_per_file'] == 3 and body['charged_credits'] == 6
        assert body['refunded_credits'] == 0 and body['skipped_count'] == 0
        assert body['ready_at'] is None and body['expires_at'] is None
        assert body['filters'] == {
            'summary': ['Search: "acme"'],
            'params': {'q': 'acme'},
        }
        assert [p['number'] for p in body['parts']] == [1]
        assert body['parts'][0]['built'] is False and body['parts'][0]['file_count'] == 2
        assert get_balance(funded_user) == 94
        export = ReceiptExport.objects.get()
        assert str(export.pk) == body['id'] and export.user == funded_user

    def test_the_layout_defaults_to_year_vendor(self, client, auth, three):
        assert post(client, '/exports', auth).json()['layout'] == 'year_vendor'

    def test_the_confirmed_cost_is_accepted(self, client, auth, three, funded_user):
        assert post(client, '/exports', auth, {'expected_cost': 9}).status_code == 201
        assert get_balance(funded_user) == 91

    def test_without_expected_cost_whatever_it_costs_is_accepted(
        self, client, auth, three, receipts_settings
    ):
        receipts_settings.export_price_per_file = 5
        receipts_settings.save()
        assert post(client, '/exports', auth).json()['charged_credits'] == 15

    def test_a_free_export_charges_nothing(
        self, client, auth, three, receipts_settings, funded_user
    ):
        receipts_settings.export_price_per_file = 0
        receipts_settings.save()
        before = transactions(funded_user)

        assert post(client, '/exports', auth, {'expected_cost': 0}).status_code == 201
        assert transactions(funded_user) == before

    def test_a_changed_cost_is_a_409_naming_the_real_total(self, client, auth, three, funded_user):
        response = post(client, '/exports', auth, {'expected_cost': 6})

        assert response.status_code == 409
        detail = response.json()['detail']
        assert 'now 9 credits' in detail and 'expected_cost=9' in detail
        assert not ReceiptExport.objects.exists() and get_balance(funded_user) == 100

    def test_a_cost_that_changes_between_the_quote_and_the_create(
        self, client, auth, three, make_receipt, funded_user
    ):
        confirmed = post(client, '/exports/quote', auth).json()['cost']
        make_receipt(data=b'late')

        assert post(client, '/exports', auth, {'expected_cost': confirmed}).status_code == 409
        assert not ReceiptExport.objects.exists()

    def test_a_negative_expected_cost_is_a_422(self, client, auth, three):
        assert post(client, '/exports', auth, {'expected_cost': -1}).status_code == 422

    def test_one_at_a_time_is_a_409(self, client, auth, three, funded_user):
        assert post(client, '/exports', auth).status_code == 201
        before = get_balance(funded_user)

        response = post(client, '/exports', auth)

        assert response.status_code == 409
        assert response.json()['detail'] == (
            'An export is already being prepared. Wait for it to finish first.'
        )
        assert get_balance(funded_user) == before and ReceiptExport.objects.count() == 1

    def test_a_finished_export_does_not_block_the_next(self, client, auth, ready):
        assert post(client, '/exports', auth).status_code == 201

    def test_nothing_to_export_is_a_400(self, client, auth):
        response = post(client, '/exports', auth)
        assert response.status_code == 400
        assert response.json()['detail'] == 'There are no files to download with these filters.'

    def test_not_enough_credits_is_a_402(self, client, auth, three, funded_user):
        spend_credits(funded_user, 95, description='x', source='test')

        response = post(client, '/exports', auth)

        assert response.status_code == 402
        assert not ReceiptExport.objects.exists() and get_balance(funded_user) == 5

    def test_a_frozen_account_is_a_402(self, client, auth, three, freeze):
        response = post(client, '/exports', auth)
        assert response.status_code == 402 and not ReceiptExport.objects.exists()

    @pytest.mark.parametrize(
        'body', [{'layout': 'x'}, {'status': 'nonsense'}, {'date_from': 'soon'}]
    )
    def test_bad_filters_or_layout_are_a_400(self, client, auth, three, body):
        assert post(client, '/exports', auth, body).status_code == 400
        assert not ReceiptExport.objects.exists()

    def test_an_unknown_key_is_a_422(self, client, auth, three):
        assert post(client, '/exports', auth, {'price': 0}).status_code == 422

    def test_it_is_rate_limited_per_user(self, client, auth, three, settings):
        limit_rule(settings, 'RECEIPTS_EXPORT_FILES_USER')
        assert post(client, '/exports', auth).status_code == 201
        ReceiptExport.objects.update(status=ExportStatus.READY)

        limited = post(client, '/exports', auth)

        assert limited.status_code == 429 and limited['Retry-After']
        assert ReceiptExport.objects.count() == 1

    @pytest.mark.parametrize(
        'case',
        ['bad layout', 'bad filter', 'empty', 'frozen', 'broke', 'in progress', 'cost changed'],
    )
    def test_a_refused_request_spends_none_of_the_budget(
        self, client, auth, three, funded_user, settings, case
    ):
        limit_rule(settings, 'RECEIPTS_EXPORT_FILES_USER')
        body: dict = {}
        if case == 'bad layout':
            body = {'layout': 'x'}
        elif case == 'bad filter':
            body = {'status': 'zzz'}
        elif case == 'empty':
            body = {'q': 'no such vendor'}
        elif case == 'frozen':
            from ..models import ReceiptsAccount
            from ..services.accounts import get_account

            get_account(funded_user)
            ReceiptsAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())
        elif case == 'broke':
            spend_credits(funded_user, 95, description='x', source='test')
        elif case == 'in progress':
            svc.create_export(funded_user, None, 'year_vendor')
        elif case == 'cost changed':
            body = {'expected_cost': 1}

        refused = post(client, '/exports', auth, body)
        assert refused.status_code in (400, 402, 409)

        # The whole budget (one hit) is still there for a request that goes through.
        ReceiptExport.objects.update(status=ExportStatus.READY)
        from ..models import ReceiptsAccount

        ReceiptsAccount.objects.filter(pk=funded_user.pk).update(frozen_at=None)
        add_credits(funded_user, 100, description='top up', source='test')
        assert post(client, '/exports', auth).status_code == 201

    def test_over_the_files_limit_nothing_is_read_or_spent(
        self, client, auth, three, funded_user, settings, monkeypatch
    ):
        limit_rule(settings, 'RECEIPTS_EXPORT_FILES_USER')
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_EXPORT_DIALOG_USER': (5, 60),
        }
        assert post(client, '/exports', auth).status_code == 201
        ReceiptExport.objects.update(status=ExportStatus.READY)
        reads: list[int] = []
        real = svc.prepare_export
        monkeypatch.setattr(svc, 'prepare_export', lambda *a, **k: reads.append(1) or real(*a, **k))
        dialog_left = ratelimit.peek_user(funded_user, 'RECEIPTS_EXPORT_DIALOG_USER').remaining

        for body in ({}, {}, {'expected_cost': 1}):
            assert post(client, '/exports', auth, body).status_code == 429

        assert reads == []  # not one whole-list read
        assert (
            ratelimit.peek_user(funded_user, 'RECEIPTS_EXPORT_DIALOG_USER').remaining == dialog_left
        )
        assert ReceiptExport.objects.count() == 1

    def test_refusals_that_read_the_list_spend_the_dialog_budget(
        self, client, auth, three, funded_user, settings
    ):
        limit_rule(settings, 'RECEIPTS_EXPORT_DIALOG_USER', limit=2, window=60)

        # A wrong expected_cost is a 409 that read the whole list: it counts against the budget.
        assert post(client, '/exports', auth, {'expected_cost': 1}).status_code == 409
        assert post(client, '/exports', auth, {'expected_cost': 1}).status_code == 409
        limited = post(client, '/exports', auth, {'expected_cost': 1})

        assert limited.status_code == 429 and limited['Retry-After']
        assert not ReceiptExport.objects.exists()

    def test_a_successful_create_spends_none_of_the_dialog_budget(
        self, client, auth, three, funded_user, settings
    ):
        limit_rule(settings, 'RECEIPTS_EXPORT_DIALOG_USER', limit=1, window=60)
        assert post(client, '/exports', auth).status_code == 201
        assert post(client, '/exports/quote', auth).status_code == 200

    def test_it_needs_a_token(self, client):
        assert client.post(f'{BASE}/exports', {}).status_code == 401


class TestRead:
    def test_a_ready_export_lists_its_parts(self, client, auth, ready):
        body = get(client, f'/exports/{ready.pk}', auth).json()

        assert body['id'] == str(ready.pk) and body['status'] == 'ready'
        assert body['file_count'] == 3 and body['charged_credits'] == 9
        assert body['ready_at'] and body['expires_at'] and body['created_at']
        (part,) = body['parts']
        assert part['number'] == 1 and part['built'] is True and part['file_count'] == 3
        assert part['size_bytes'] > 0
        assert part['filename'].endswith('.zip') and part['filename'].startswith('receipts-')
        assert body['filters'] == {'summary': [], 'params': {}}

    def test_a_building_export_shows_its_progress(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None, 'year_vendor')
        body = get(client, f'/exports/{export.pk}', auth).json()
        assert body['status'] == 'building' and body['parts'][0]['built'] is False

    def test_a_failed_export_has_no_parts_and_is_refunded(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None, 'year_vendor')
        svc.fail_export(export.pk, 'boom')

        body = get(client, f'/exports/{export.pk}', auth).json()

        assert body['status'] == 'failed' and body['parts'] == []
        assert body['refunded_credits'] == 9 and get_balance(funded_user) == 100

    def test_an_expired_export_is_still_shown(self, client, auth, ready):
        ReceiptExport.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        svc.expire_exports()
        assert get(client, f'/exports/{ready.pk}', auth).json()['status'] == 'expired'

    def test_another_users_export_is_a_404(self, client, other_auth, ready):
        assert get(client, f'/exports/{ready.pk}', other_auth).status_code == 404
        assert get(client, f'/exports/{ready.pk}/parts/1/url', other_auth).status_code == 404
        assert delete(client, f'/exports/{ready.pk}', other_auth).status_code == 404

    def test_an_unknown_one_is_a_404_and_a_malformed_id_a_422(self, client, auth):
        assert get(client, f'/exports/{uuid.uuid4()}', auth).status_code == 404
        assert get(client, '/exports/not-a-uuid', auth).status_code == 422

    def test_it_needs_a_token(self, client, ready):
        assert client.get(f'{BASE}/exports/{ready.pk}').status_code == 401
        assert client.get(f'{BASE}/exports').status_code == 401


class TestList:
    def test_newest_first_and_only_the_callers(
        self, client, auth, three, funded_user, other_user, fake_storage
    ):
        first = svc.create_export(funded_user, None, 'year_vendor')
        svc.fail_export(first.pk)
        second = svc.create_export(funded_user, None, 'vendor_year')
        create_receipt(other_user, fake_storage)
        add_credits(other_user, 50, description='x', source='test')
        svc.create_export(other_user, None, 'year_vendor')

        body = get(client, '/exports', auth).json()

        assert body['count'] == 2 and body['limit'] == 20 and body['offset'] == 0
        assert [e['id'] for e in body['results']] == [str(second.pk), str(first.pk)]

    def test_a_deleted_export_is_not_listed(self, client, auth, ready):
        assert delete(client, f'/exports/{ready.pk}', auth).status_code == 204
        assert get(client, '/exports', auth).json() == {
            'count': 0,
            'limit': 20,
            'offset': 0,
            'results': [],
        }

    def test_it_pages(self, client, auth, three, funded_user):
        for _ in range(3):
            export = svc.create_export(funded_user, None, 'year_vendor')
            svc.fail_export(export.pk)

        page = get(client, '/exports', auth, limit=2, offset=2).json()

        assert page['count'] == 3 and page['limit'] == 2 and len(page['results']) == 1
        clamped = get(client, '/exports', auth, limit=500, offset=-3).json()
        assert clamped['limit'] == 100 and clamped['offset'] == 0

    def test_the_parts_are_read_in_one_query(
        self, client, auth, three, funded_user, django_assert_max_num_queries
    ):
        for _ in range(4):
            export = svc.create_export(funded_user, None, 'year_vendor')
            build(export)

        with django_assert_max_num_queries(6):
            assert get(client, '/exports', auth).status_code == 200


class TestPartUrl:
    def test_it_gives_a_presigned_url_never_the_key(self, client, auth, ready, fake_storage):
        response = get(client, f'/exports/{ready.pk}/parts/1/url', auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {'url', 'filename', 'size_bytes', 'expires_in'}
        assert body['filename'].endswith('.zip') and body['size_bytes'] > 0
        assert body['expires_in'] > 0
        part = ready.parts.get(number=1)
        assert fake_storage.get_urls[-1]['key'] == part.key

    def test_an_unknown_part_is_a_404(self, client, auth, ready):
        assert get(client, f'/exports/{ready.pk}/parts/2/url', auth).status_code == 404
        assert get(client, f'/exports/{ready.pk}/parts/0/url', auth).status_code == 404

    def test_a_building_export_is_a_400(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None, 'year_vendor')
        response = get(client, f'/exports/{export.pk}/parts/1/url', auth)
        assert response.status_code == 400
        assert response.json()['detail'] == 'This export is not available to download.'

    def test_an_expired_export_is_a_400(self, client, auth, ready):
        ReceiptExport.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        svc.expire_exports()
        assert get(client, f'/exports/{ready.pk}/parts/1/url', auth).status_code == 400

    def test_a_frozen_account_is_a_402(self, client, auth, ready, freeze):
        assert get(client, f'/exports/{ready.pk}/parts/1/url', auth).status_code == 402

    def test_it_is_rate_limited_and_a_refusal_spends_none_of_it(
        self, client, auth, ready, settings
    ):
        limit_rule(settings, 'RECEIPTS_EXPORT_DOWNLOAD_USER')
        # A part that doesn't exist and a malformed id spend nothing.
        assert get(client, f'/exports/{ready.pk}/parts/9/url', auth).status_code == 404
        assert get(client, f'/exports/{uuid.uuid4()}/parts/1/url', auth).status_code == 404

        assert get(client, f'/exports/{ready.pk}/parts/1/url', auth).status_code == 200
        limited = get(client, f'/exports/{ready.pk}/parts/1/url', auth)

        assert limited.status_code == 429 and limited['Retry-After']


class TestDelete:
    def test_it_deletes_the_files_and_the_export(
        self, client, auth, ready, fake_storage, funded_user
    ):
        part_key = ready.parts.get(number=1).key
        assert part_key in fake_storage.objects
        balance = get_balance(funded_user)

        assert delete(client, f'/exports/{ready.pk}', auth).status_code == 204

        assert part_key not in fake_storage.objects
        assert ReceiptExport.objects.get(pk=ready.pk).status == ExportStatus.DELETED
        assert get_balance(funded_user) == balance  # nothing is refunded
        assert get(client, f'/exports/{ready.pk}', auth).status_code == 404
        assert delete(client, f'/exports/{ready.pk}', auth).status_code == 404

    def test_one_being_prepared_is_a_409(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None, 'year_vendor')

        response = delete(client, f'/exports/{export.pk}', auth)

        assert response.status_code == 409
        assert response.json()['detail'] == 'This export is still being prepared.'
        export.refresh_from_db()
        assert export.status == ExportStatus.BUILDING

    def test_a_failed_one_can_be_deleted(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None, 'year_vendor')
        svc.fail_export(export.pk)
        assert delete(client, f'/exports/{export.pk}', auth).status_code == 204

    def test_it_needs_a_token(self, client, ready):
        assert client.delete(f'{BASE}/exports/{ready.pk}').status_code == 401


def test_the_export_schema_names_are_prefixed():
    from apps.receipts.schemas import export as module

    names = [n for n, v in vars(module).items() if getattr(v, '__module__', '') == module.__name__]
    assert names and all(n.startswith('ReceiptExport') for n in names)
