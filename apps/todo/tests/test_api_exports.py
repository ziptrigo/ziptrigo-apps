"""The file export endpoints (`/api/todo/exports` and `/api/todo/export.xlsx`, issue #326), over
Django's test client: the quote, creating (every status code, the cost confirmation, the budgets a
refusal does not spend), the list and one export, a zip file's download URL, deleting, the quick
workbook, and what is another user's. The twin of `apps.receipts.tests.test_api_exports`."""

import io
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from openpyxl import load_workbook

from apps.billing.models import CreditTransaction
from apps.billing.services import add_credits, get_balance, spend_credits

from ..models import ExportStatus, Project, TodoAccount, TodoExport
from ..services import exports as svc
from ..services import get_account
from .api_support import (
    BASE,
    auth,  # noqa: F401 -- fixtures
    delete,
    get,
    limit_rule,
    other_auth,  # noqa: F401
    post,
)
from .factories import created, make_attachment, make_comment, make_task, utc

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    # Requests per fixed rate-limit window: a real clock crossing a boundary breaks them.
    pytest.mark.usefixtures('ratelimit_clock'),
]


@pytest.fixture(autouse=True)
def price(config):
    config.export_price_per_file = 3
    config.save()
    return config


@pytest.fixture
def freeze(funded_user):
    get_account(funded_user)
    TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())


@pytest.fixture
def three(funded_user, fake_storage):
    """Three files on two tasks (one on a comment), 85 bytes."""
    home = Project.objects.create(user=funded_user, name='Home')
    rent = make_task(funded_user, number=7001, title='Pay rent', project=home)
    notes = make_task(funded_user, number=7002, title='Call the bank')
    comment = make_comment(rent, 'proof attached')
    return [
        make_attachment(rent, fake_storage, data=b'rent-' * 5, filename='rent.pdf'),
        make_attachment(
            rent,
            fake_storage,
            data=b'proof-' * 5,
            filename='proof.png',
            comment=comment,
            for_comment=True,
        ),
        make_attachment(notes, fake_storage, data=b'notes-' * 5, filename='notes.txt'),
    ]


def build(export: TodoExport) -> None:
    while (number := svc.next_part_to_build(export)) is not None:
        TodoExport.objects.filter(pk=export.pk).update(lease_until=None)
        svc.build_part.call(str(export.pk), number)
        export.refresh_from_db()


@pytest.fixture
def ready(funded_user, three):
    export = svc.create_export(funded_user, None)
    build(export)
    export.refresh_from_db()
    return export


def transactions(user) -> int:
    return CreditTransaction.objects.filter(user=user).count()


class TestQuote:
    def test_counts_size_parts_cost_and_balance(self, client, auth, three):
        response = post(client, '/exports/quote', auth)

        assert response.status_code == 200
        body = response.json()
        assert body == {
            'file_count': 3,
            'total_bytes': 85,
            'part_count': 1,
            'price_per_file': 3,
            'cost': 9,
            'balance': 100,
            'affordable': True,
            'frozen': False,
            'retention_days': 30,
            'part_bytes': body['part_bytes'],
        }
        assert body['part_bytes'] > 0

    def test_it_takes_the_created_range(self, client, auth, three, freeze_now):
        freeze_now(2026, 11, 16, 12)
        created(three[2].task, utc(2024, 5, 5))
        created(three[0].task, utc(2026, 11, 10))

        def count(**body):
            return post(client, '/exports/quote', auth, body).json()['file_count']

        assert count() == 3
        assert count(date_range='any') == 3
        assert count(date_range='last_15_days') == 2
        assert count(date_range='this_year') == 2
        assert count(date_from='2026-01-01') == 2  # dates with no date_range are Custom
        assert count(date_range='custom', date_from='2026-01-01', date_to='2026-12-31') == 2
        assert count(date_to='2024-12-31') == 1
        assert count(date_range='custom', date_from='2030-01-01') == 0
        assert count(date_range='custom') == 3  # Custom with no dates is every task

    def test_a_day_is_the_users_own(self, client, auth, three, funded_user):
        """A day is the user's own: 23:30 UTC on 31 October is already 1 November in Auckland."""
        funded_user.timezone = 'Pacific/Auckland'
        funded_user.save()
        created(three[0].task, utc(2026, 10, 31, 23, 30))  # 12:30 on 1 November in Auckland

        def count(**body):
            return post(client, '/exports/quote', auth, body).json()['file_count']

        # (the task has two files)
        assert count(date_from='2026-11-01', date_to='2026-11-01') == 2
        assert count(date_from='2026-10-31', date_to='2026-10-31') == 0

    def test_an_unaffordable_one_is_still_quoted(self, client, auth, three, funded_user):
        spend_credits(funded_user, 95, description='x', source='test')
        body = post(client, '/exports/quote', auth).json()
        assert body['cost'] == 9 and body['balance'] == 5 and body['affordable'] is False

    def test_a_frozen_account_is_told_in_the_quote(self, client, auth, three, freeze):
        assert post(client, '/exports/quote', auth).json()['frozen'] is True

    def test_nothing_to_export_is_quoted_as_zero(self, client, auth):
        body = post(client, '/exports/quote', auth).json()
        assert body['file_count'] == 0 and body['cost'] == 0

    def test_a_free_export_is_quoted_as_zero(self, client, auth, three, price):
        price.export_price_per_file = 0
        price.save()
        body = post(client, '/exports/quote', auth).json()
        assert body['file_count'] == 3 and body['cost'] == 0 and body['price_per_file'] == 0

    def test_only_the_callers_files(self, client, auth, three, other_user, fake_storage):
        make_attachment(make_task(other_user), fake_storage, filename='theirs.png')
        assert post(client, '/exports/quote', auth).json()['file_count'] == 3

    @pytest.mark.parametrize(
        'body',
        [
            {'date_range': 'next_decade'},
            {'date_from': 'yesterday'},
            {'date_to': '2026-13-45'},
            {'date_range': 'this_month', 'date_from': '2026-01-01'},
            {'date_range': 'any', 'date_to': '2026-01-01'},
            {'date_from': '2026-02-01', 'date_to': '2026-01-01'},
        ],
    )
    def test_a_bad_range_is_a_400_that_spends_no_budget(
        self, client, auth, settings, funded_user, body
    ):
        limit_rule(settings, 'TODO_EXPORT_DIALOG_USER')

        assert post(client, '/exports/quote', auth, body).status_code == 400
        assert post(client, '/exports/quote', auth).status_code == 200

    def test_the_range_error_says_what_to_choose(self, client, auth):
        response = post(client, '/exports/quote', auth, {'date_range': 'x'})
        assert response.status_code == 400
        assert response.json()['detail'].startswith('date_range must be one of: any, last_15_days')

    def test_an_unknown_key_is_a_422(self, client, auth):
        assert post(client, '/exports/quote', auth, {'expected_cost': 1}).status_code == 422

    def test_it_uses_the_dialogs_budget(self, client, auth, settings):
        limit_rule(settings, 'TODO_EXPORT_DIALOG_USER', limit=2, window=60)

        assert post(client, '/exports/quote', auth).status_code == 200
        assert post(client, '/exports/quote', auth).status_code == 200
        limited = post(client, '/exports/quote', auth)
        assert limited.status_code == 429 and limited['Retry-After']

    def test_it_writes_and_charges_nothing(self, client, auth, three, funded_user):
        post(client, '/exports/quote', auth)
        assert not TodoExport.objects.exists() and get_balance(funded_user) == 100

    def test_it_needs_a_token(self, client):
        assert client.post(f'{BASE}/exports/quote', {}).status_code == 401


class TestCreate:
    def test_it_starts_and_charges_the_export(self, client, auth, three, funded_user):
        created(three[2].task, utc(2024, 5, 5))
        response = post(
            client,
            '/exports',
            auth,
            {'date_range': 'custom', 'date_from': '2026-01-01', 'date_to': '2030-12-31'},
        )

        assert response.status_code == 201
        body = response.json()
        assert body['status'] == 'building'
        assert body['file_count'] == 2 and body['part_count'] == 1
        assert body['price_per_file'] == 3 and body['charged_credits'] == 6
        assert body['refunded_credits'] == 0 and body['skipped_count'] == 0
        assert body['ready_at'] is None and body['expires_at'] is None
        assert body['filters'] == {
            'summary': ['Task created: Jan 1, 2026 to Dec 31, 2030'],
            'params': {'date_from': '2026-01-01', 'date_to': '2030-12-31'},
        }
        assert 'layout' not in body
        assert [p['number'] for p in body['parts']] == [1]
        assert body['parts'][0]['built'] is False and body['parts'][0]['file_count'] == 2
        assert get_balance(funded_user) == 94
        export = TodoExport.objects.get()
        assert str(export.pk) == body['id'] and export.user == funded_user

    def test_a_preset_is_kept_by_name(self, client, auth, three):
        body = post(client, '/exports', auth, {'date_range': 'this_year'}).json()
        assert body['filters']['params'] == {'date_range': 'this_year'}

    def test_no_body_is_every_task(self, client, auth, three):
        body = post(client, '/exports', auth).json()
        assert body['file_count'] == 3 and body['filters'] == {'summary': [], 'params': {}}

    def test_the_confirmed_cost_is_accepted(self, client, auth, three, funded_user):
        assert post(client, '/exports', auth, {'expected_cost': 9}).status_code == 201
        assert get_balance(funded_user) == 91

    def test_without_expected_cost_whatever_it_costs_is_accepted(self, client, auth, three, price):
        price.export_price_per_file = 5
        price.save()
        assert post(client, '/exports', auth).json()['charged_credits'] == 15

    def test_a_free_export_charges_nothing(self, client, auth, three, price, funded_user):
        price.export_price_per_file = 0
        price.save()
        before = transactions(funded_user)

        assert post(client, '/exports', auth, {'expected_cost': 0}).status_code == 201
        assert transactions(funded_user) == before

    def test_a_changed_cost_is_a_409_naming_the_real_total(self, client, auth, three, funded_user):
        response = post(client, '/exports', auth, {'expected_cost': 6})

        assert response.status_code == 409
        detail = response.json()['detail']
        assert 'now 9 credits' in detail and 'expected_cost=9' in detail
        assert not TodoExport.objects.exists() and get_balance(funded_user) == 100

    def test_a_cost_that_changes_between_the_quote_and_the_create(
        self, client, auth, three, fake_storage, funded_user
    ):
        confirmed = post(client, '/exports/quote', auth).json()['cost']
        make_attachment(three[0].task, fake_storage, filename='late.png')

        assert post(client, '/exports', auth, {'expected_cost': confirmed}).status_code == 409
        assert not TodoExport.objects.exists() and get_balance(funded_user) == 100

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
        assert get_balance(funded_user) == before and TodoExport.objects.count() == 1

    def test_a_finished_export_does_not_block_the_next(self, client, auth, ready):
        assert post(client, '/exports', auth).status_code == 201

    def test_nothing_to_export_is_a_400(self, client, auth, funded_user):
        make_task(funded_user)  # a task with no file is nothing to download
        response = post(client, '/exports', auth)
        assert response.status_code == 400
        assert response.json()['detail'] == svc.NOTHING_TO_EXPORT
        assert not TodoExport.objects.exists()

    def test_a_range_with_no_files_is_a_400(self, client, auth, three):
        response = post(client, '/exports', auth, {'date_from': '2030-01-01'})
        assert response.status_code == 400 and not TodoExport.objects.exists()

    def test_not_enough_credits_is_a_402(self, client, auth, three, funded_user):
        spend_credits(funded_user, 95, description='x', source='test')

        response = post(client, '/exports', auth)

        assert response.status_code == 402
        assert not TodoExport.objects.exists() and get_balance(funded_user) == 5

    def test_a_frozen_account_is_a_402(self, client, auth, three, freeze):
        response = post(client, '/exports', auth)
        assert response.status_code == 402 and not TodoExport.objects.exists()

    @pytest.mark.parametrize(
        'body',
        [
            {'date_range': 'x'},
            {'date_from': 'soon'},
            {'date_range': 'this_month', 'date_to': '2026-1-1'},
        ],
    )
    def test_a_bad_range_is_a_400(self, client, auth, three, body):
        assert post(client, '/exports', auth, body).status_code == 400
        assert not TodoExport.objects.exists()

    def test_an_unknown_key_is_a_422(self, client, auth, three):
        assert post(client, '/exports', auth, {'price': 0}).status_code == 422

    def test_it_is_rate_limited_per_user(self, client, auth, three, settings):
        limit_rule(settings, 'TODO_EXPORT_FILES_USER')
        assert post(client, '/exports', auth).status_code == 201
        TodoExport.objects.update(status=ExportStatus.READY)

        limited = post(client, '/exports', auth)

        assert limited.status_code == 429 and limited['Retry-After']
        assert TodoExport.objects.count() == 1

    @pytest.mark.parametrize(
        'case', ['bad range', 'empty', 'frozen', 'broke', 'in progress', 'cost changed']
    )
    def test_a_refused_request_spends_none_of_the_budget(
        self, client, auth, three, funded_user, settings, case
    ):
        limit_rule(settings, 'TODO_EXPORT_FILES_USER')
        body: dict = {}
        if case == 'bad range':
            body = {'date_range': 'x'}
        elif case == 'empty':
            body = {'date_from': '2030-01-01'}
        elif case == 'frozen':
            TodoAccount.objects.update_or_create(
                pk=funded_user.pk, defaults={'frozen_at': timezone.now()}
            )
        elif case == 'broke':
            spend_credits(funded_user, 95, description='x', source='test')
        elif case == 'in progress':
            svc.create_export(funded_user, None)
        elif case == 'cost changed':
            body = {'expected_cost': 1}

        refused = post(client, '/exports', auth, body)
        assert refused.status_code in (400, 402, 409)

        # The whole budget (one hit) is still there for a request that goes through.
        TodoExport.objects.update(status=ExportStatus.READY)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=None)
        add_credits(funded_user, 100, description='top up', source='test')
        assert post(client, '/exports', auth).status_code == 201

    def test_refusals_that_read_the_files_spend_the_dialog_budget(
        self, client, auth, three, funded_user, settings
    ):
        limit_rule(settings, 'TODO_EXPORT_DIALOG_USER', limit=2, window=60)

        # A wrong expected_cost is a 409 that read every file: it counts against the budget.
        assert post(client, '/exports', auth, {'expected_cost': 1}).status_code == 409
        assert post(client, '/exports', auth, {'expected_cost': 1}).status_code == 409
        limited = post(client, '/exports', auth, {'expected_cost': 1})

        assert limited.status_code == 429 and limited['Retry-After']
        assert not TodoExport.objects.exists()

    def test_a_bad_range_spends_none_of_the_dialog_budget(self, client, auth, three, settings):
        limit_rule(settings, 'TODO_EXPORT_DIALOG_USER', limit=1, window=60)
        for _ in range(3):
            assert post(client, '/exports', auth, {'date_range': 'x'}).status_code == 400
        assert post(client, '/exports', auth).status_code == 201

    def test_a_successful_create_spends_none_of_the_dialog_budget(
        self, client, auth, three, funded_user, settings
    ):
        limit_rule(settings, 'TODO_EXPORT_DIALOG_USER', limit=1, window=60)
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
        assert part['filename'].endswith('.zip') and part['filename'].startswith('task-files-')
        assert body['filters'] == {'summary': [], 'params': {}}

    def test_a_building_export_shows_its_progress(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None)
        body = get(client, f'/exports/{export.pk}', auth).json()
        assert body['status'] == 'building' and body['parts'][0]['built'] is False

    def test_a_failed_export_has_no_parts_and_is_refunded(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None)
        svc.fail_export(export.pk, 'boom')

        body = get(client, f'/exports/{export.pk}', auth).json()

        assert body['status'] == 'failed' and body['parts'] == []
        assert body['refunded_credits'] == 9 and get_balance(funded_user) == 100

    def test_an_expired_export_is_still_shown(self, client, auth, ready):
        TodoExport.objects.filter(pk=ready.pk).update(expires_at=timezone.now() - timedelta(days=1))
        svc.expire_exports()
        assert get(client, f'/exports/{ready.pk}', auth).json()['status'] == 'expired'

    def test_another_users_export_is_a_404(self, client, other_auth, ready):
        assert get(client, f'/exports/{ready.pk}', other_auth).status_code == 404
        assert get(client, f'/exports/{ready.pk}/parts/1/url', other_auth).status_code == 404
        assert delete(client, f'/exports/{ready.pk}', other_auth).status_code == 404
        ready.refresh_from_db()
        assert ready.status == ExportStatus.READY

    def test_an_unknown_one_is_a_404_and_a_malformed_id_a_422(self, client, auth):
        assert get(client, f'/exports/{uuid.uuid4()}', auth).status_code == 404
        assert get(client, '/exports/not-a-uuid', auth).status_code == 422

    def test_it_needs_a_token(self, client, ready):
        assert client.get(f'{BASE}/exports/{ready.pk}').status_code == 401
        assert client.get(f'{BASE}/exports').status_code == 401
        assert client.get(f'{BASE}/exports/{ready.pk}/parts/1/url').status_code == 401


class TestList:
    def test_newest_first_and_only_the_callers(
        self, client, auth, three, funded_user, other_user, fake_storage
    ):
        first = svc.create_export(funded_user, None)
        svc.fail_export(first.pk)
        second = svc.create_export(funded_user, None)
        make_attachment(make_task(other_user), fake_storage)
        add_credits(other_user, 50, description='x', source='test')
        svc.create_export(other_user, None)

        body = get(client, '/exports', auth).json()

        assert body['count'] == 2 and body['limit'] == 50 and body['offset'] == 0
        assert [e['id'] for e in body['results']] == [str(second.pk), str(first.pk)]

    def test_a_deleted_export_is_not_listed(self, client, auth, ready):
        assert delete(client, f'/exports/{ready.pk}', auth).status_code == 204
        assert get(client, '/exports', auth).json() == {
            'count': 0,
            'limit': 50,
            'offset': 0,
            'results': [],
        }

    def test_it_pages(self, client, auth, three, funded_user):
        for _ in range(3):
            export = svc.create_export(funded_user, None)
            svc.fail_export(export.pk)

        page = get(client, '/exports', auth, limit=2, offset=2).json()

        assert page['count'] == 3 and page['limit'] == 2 and len(page['results']) == 1
        clamped = get(client, '/exports', auth, limit=500, offset=-3).json()
        assert clamped['limit'] == 100 and clamped['offset'] == 0

    def test_the_parts_are_read_in_one_query(
        self, client, auth, three, funded_user, django_assert_max_num_queries
    ):
        for _ in range(4):
            export = svc.create_export(funded_user, None)
            build(export)

        with django_assert_max_num_queries(6):
            response = get(client, '/exports', auth)
        assert response.status_code == 200
        assert [len(e['parts']) for e in response.json()['results']] == [1, 1, 1, 1]


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
        assert body['url'].startswith('https://fake-s3.test/')

    def test_an_unknown_part_is_a_404(self, client, auth, ready):
        assert get(client, f'/exports/{ready.pk}/parts/2/url', auth).status_code == 404
        assert get(client, f'/exports/{ready.pk}/parts/0/url', auth).status_code == 404

    def test_a_building_export_is_a_400(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None)
        response = get(client, f'/exports/{export.pk}/parts/1/url', auth)
        assert response.status_code == 400
        assert response.json()['detail'] == 'This export is not available to download.'

    def test_an_expired_export_is_a_400(self, client, auth, ready):
        TodoExport.objects.filter(pk=ready.pk).update(expires_at=timezone.now() - timedelta(days=1))
        svc.expire_exports()
        assert get(client, f'/exports/{ready.pk}/parts/1/url', auth).status_code == 400

    def test_a_frozen_account_is_a_402(self, client, auth, ready, freeze):
        assert get(client, f'/exports/{ready.pk}/parts/1/url', auth).status_code == 402

    def test_it_is_rate_limited_and_a_refusal_spends_none_of_it(
        self, client, auth, ready, settings
    ):
        limit_rule(settings, 'TODO_EXPORT_DOWNLOAD_USER')
        # A part that doesn't exist and an export that doesn't exist spend nothing.
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
        assert TodoExport.objects.get(pk=ready.pk).status == ExportStatus.DELETED
        assert get_balance(funded_user) == balance  # nothing is refunded
        assert get(client, f'/exports/{ready.pk}', auth).status_code == 404
        assert delete(client, f'/exports/{ready.pk}', auth).status_code == 404

    def test_one_being_prepared_is_a_409(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None)

        response = delete(client, f'/exports/{export.pk}', auth)

        assert response.status_code == 409
        assert response.json()['detail'] == 'This export is still being prepared.'
        export.refresh_from_db()
        assert export.status == ExportStatus.BUILDING

    def test_a_failed_one_can_be_deleted(self, client, auth, three, funded_user):
        export = svc.create_export(funded_user, None)
        svc.fail_export(export.pk)
        assert delete(client, f'/exports/{export.pk}', auth).status_code == 204

    def test_it_needs_a_token(self, client, ready):
        assert client.delete(f'{BASE}/exports/{ready.pk}').status_code == 401


class TestXlsx:
    def open(self, response):
        return load_workbook(io.BytesIO(b''.join(response.streaming_content)))

    def rows(self, response, sheet='Tasks'):
        return list(self.open(response)[sheet].iter_rows(min_row=2))

    def test_downloads_the_workbook(self, client, auth, three, freeze_now):
        freeze_now(2026, 11, 16, 12)
        response = get(client, '/export.xlsx', auth)

        assert response.status_code == 200
        assert response['Content-Type'] == (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        assert response['Content-Disposition'] == 'attachment; filename="tasks-2026-11-16.xlsx"'
        assert response['Cache-Control'] == 'private, no-store'
        workbook = self.open(response)
        assert workbook.sheetnames == ['Tasks', 'Comments']
        assert [row[0].value for row in workbook['Tasks'].iter_rows(min_row=2)] == [7001, 7002]

    def test_the_files_columns_hold_file_names_not_zip_paths(self, client, auth, three):
        workbook = self.open(get(client, '/export.xlsx', auth))
        by_number = {row[0].value: row[15].value for row in workbook['Tasks'].iter_rows(min_row=2)}
        assert by_number == {7001: 'rent.pdf', 7002: 'notes.txt'}

    def test_it_is_free_and_stores_nothing(self, client, auth, funded_user, three, fake_storage):
        before = dict(fake_storage.objects)
        assert get(client, '/export.xlsx', auth).status_code == 200
        assert get_balance(funded_user) == 100 and not TodoExport.objects.exists()
        assert fake_storage.objects == before

    def test_it_works_with_no_files_and_when_storage_is_paused(
        self, client, auth, funded_user, freeze
    ):
        make_task(funded_user, number=7001)
        response = get(client, '/export.xlsx', auth)
        assert response.status_code == 200 and [r[0].value for r in self.rows(response)] == [7001]

    def test_the_range_narrows_it(self, client, auth, three):
        created(three[2].task, utc(2024, 5, 5))
        response = get(
            client,
            '/export.xlsx',
            auth,
            date_range='custom',
            date_from='2026-01-01',
            date_to='2030-12-31',
        )
        assert [row[0].value for row in self.rows(response)] == [7001]
        only_old = get(client, '/export.xlsx', auth, date_to='2024-12-31')
        assert [row[0].value for row in self.rows(only_old)] == [7002]

    def test_another_users_tasks_are_not_in_it(self, client, auth, other_user):
        make_task(other_user, number=7009, title='Theirs')
        assert self.rows(get(client, '/export.xlsx', auth)) == []

    @pytest.mark.parametrize(
        'query',
        [
            {'date_range': 'nope'},
            {'date_from': 'x'},
            {'date_range': 'this_year', 'date_from': '2026-01-01'},
            {'date_from': '2026-02-01', 'date_to': '2026-01-01'},
        ],
    )
    def test_a_bad_range_is_a_400_that_spends_no_budget(self, client, auth, three, settings, query):
        limit_rule(settings, 'TODO_EXPORT_XLSX_USER', window=60)

        assert get(client, '/export.xlsx', auth, **query).status_code == 400
        assert get(client, '/export.xlsx', auth).status_code == 200

    def test_it_has_its_own_limit_shared_with_the_web(self, client, auth, three, settings):
        limit_rule(settings, 'TODO_EXPORT_XLSX_USER', window=60)
        assert get(client, '/export.xlsx', auth).status_code == 200
        limited = get(client, '/export.xlsx', auth)
        assert limited.status_code == 429 and limited['Retry-After']

    def test_it_does_not_spend_the_zip_budgets(self, client, auth, three, settings):
        limit_rule(settings, 'TODO_EXPORT_DIALOG_USER')
        limit_rule(settings, 'TODO_EXPORT_FILES_USER')
        assert get(client, '/export.xlsx', auth).status_code == 200
        assert post(client, '/exports/quote', auth).status_code == 200

    def test_the_temporary_file_is_closed_when_the_build_fails(self, client, auth, monkeypatch):
        from ..api import exports as api

        targets = []

        def boom(user, chosen, target):
            targets.append(target)
            raise RuntimeError('boom')

        monkeypatch.setattr(api.services, 'build_xlsx', boom)
        with pytest.raises(RuntimeError):
            get(client, '/export.xlsx', auth)
        [target] = targets
        assert target.closed

    def test_it_needs_a_token(self, client):
        assert client.get(f'{BASE}/export.xlsx').status_code == 401


def test_the_export_schema_names_are_prefixed():
    from apps.todo.schemas import export as module

    names = [n for n, v in vars(module).items() if getattr(v, '__module__', '') == module.__name__]
    assert names and all(n.startswith('TodoExport') for n in names)
