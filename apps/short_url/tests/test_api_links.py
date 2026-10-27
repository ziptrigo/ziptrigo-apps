"""The Short URLs JWT API's link endpoints (`/api/s/links...`): reading and listing, creating and
pricing a create, editing, enabling, disabling and deleting, with the status codes of
`api/common.py`. Costs and rate limits have their own file (`test_api_costs.py`)."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User

from ..models import BlockedDomain, LinkStatus, ShortLink
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    bearer,
    delete,
    get,
    other_auth,
    patch,
    post,
)
from .conftest import balance
from .factories import create_link, expired

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

LINK_FIELDS = {
    'id',
    'code',
    'short_url',
    'is_alias',
    'destination',
    'title',
    'tags',
    'status',
    'state',
    'expires_at',
    'max_clicks',
    'password_protected',
    'filter_bots',
    'click_count',
    'bot_click_count',
    'last_clicked_at',
    'analytics_enabled',
    'takedown_reason',
    'created_at',
    'updated_at',
}
DETAIL_FIELDS = LINK_FIELDS | {'costs', 'credits_spent'}


def create(client, auth, **fields):
    payload = {'destination': 'https://example.com/page', 'title': 'A page', **fields}
    return post(client, '/links', auth, payload)


def codes(response) -> list[str]:
    return [link['code'] for link in response.json()['results']]


class TestAuthentication:
    PATHS = [
        ('get', '/links'),
        ('post', '/links'),
        ('post', '/links/quote'),
        ('get', '/links/00000000-0000-0000-0000-000000000000'),
        ('patch', '/links/00000000-0000-0000-0000-000000000000'),
        ('delete', '/links/00000000-0000-0000-0000-000000000000'),
        ('post', '/links/00000000-0000-0000-0000-000000000000/enable'),
        ('post', '/links/00000000-0000-0000-0000-000000000000/disable'),
        ('post', '/links/00000000-0000-0000-0000-000000000000/analytics'),
        ('get', '/links/00000000-0000-0000-0000-000000000000/stats'),
        ('get', '/links/00000000-0000-0000-0000-000000000000/qr.png'),
        ('get', '/links/00000000-0000-0000-0000-000000000000/preview'),
    ]

    @pytest.mark.parametrize(('method', 'path'), PATHS)
    def test_every_endpoint_needs_a_token(self, client, method, path):
        response = getattr(client, method)(f'/api/s{path}')

        assert response.status_code == 401

    def test_a_deactivated_account_is_refused(self, client, auth, funded_user):
        funded_user.deactivate()

        assert get(client, '/links', auth).status_code == 401

    def test_a_token_from_before_a_password_change_is_refused(self, client, auth, funded_user):
        funded_user.set_password('changed-Passw0rd!')
        funded_user.save()

        assert get(client, '/links', auth).status_code == 401

    def test_the_endpoints_are_in_the_openapi_schema(self):
        from config.api import api

        paths = api.get_openapi_schema()['paths']

        assert '/api/s/links' in paths
        assert '/api/s/links/{link_id}/stats' in paths
        assert '/api/s/links/{link_id}/qr.png' in paths


class TestList:
    def test_the_shape_and_the_default_order(self, client, auth, funded_user):
        old = create_link(funded_user, code='Old0001')
        new = create_link(funded_user, code='New0001', tags=['a'])
        ShortLink.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))

        response = get(client, '/links', auth)

        assert response.status_code == 200
        body = response.json()
        assert (body['count'], body['limit'], body['offset']) == (2, 20, 0)
        assert codes(response) == ['New0001', 'Old0001']
        link = body['results'][0]
        assert set(link) == LINK_FIELDS
        assert link['id'] == str(new.pk)
        assert link['short_url'] == 'https://app.example.test/s/New0001'
        assert link['tags'] == ['a']
        assert link['state'] == 'active'
        assert 'password_hash' not in link

    def test_only_the_callers_links_and_not_the_deleted(
        self, client, auth, funded_user, other_user
    ):
        create_link(funded_user, code='Mine001')
        create_link(funded_user, code='Gone001', status=LinkStatus.DELETED)
        create_link(other_user, code='Theirs1')

        assert codes(get(client, '/links', auth)) == ['Mine001']

    def test_search_matches_code_destination_title_and_tags(self, client, auth, funded_user):
        create_link(funded_user, code='sale2026', destination='https://shop.example/x')
        create_link(funded_user, code='b0000001', destination='https://news.example/launch')
        create_link(funded_user, code='c0000001', title='Quarterly report')
        create_link(funded_user, code='d0000001', tags=['marketing'])

        assert codes(get(client, '/links', auth, q='SALE')) == ['sale2026']
        assert codes(get(client, '/links', auth, q='launch')) == ['b0000001']
        assert codes(get(client, '/links', auth, q='quarterly')) == ['c0000001']
        assert codes(get(client, '/links', auth, q='market')) == ['d0000001']

    def test_punctuation_in_a_search_does_not_match_every_tagged_link(
        self, client, auth, funded_user
    ):
        create_link(funded_user, tags=['one'])

        assert get(client, '/links', auth, q='"').json()['count'] == 0

    def test_the_tag_filter(self, client, auth, funded_user):
        create_link(funded_user, code='t0000001', tags=['promo', 'spring'])
        create_link(funded_user, code='t0000002', tags=['promo'])
        create_link(funded_user, code='t0000003')

        assert sorted(codes(get(client, '/links', auth, tag='promo'))) == ['t0000001', 't0000002']
        assert codes(get(client, '/links', auth, tag='SPRING')) == ['t0000001']

    def test_every_status(self, client, auth, funded_user):
        create_link(funded_user, code='act00001')
        create_link(funded_user, code='dis00001', status=LinkStatus.DISABLED)
        expired(funded_user, code='exp00001')
        create_link(funded_user, code='cap00001', max_clicks=1, click_count=1)
        create_link(funded_user, code='tkn00001', status=LinkStatus.TAKEN_DOWN)

        def only(status):
            return codes(get(client, '/links', auth, status=status))

        assert only('active') == ['act00001']
        assert only('disabled') == ['dis00001']
        assert only('expired') == ['exp00001']
        assert only('capped') == ['cap00001']
        assert only('taken_down') == ['tkn00001']
        assert get(client, '/links', auth, status='all').json()['count'] == 5

    def test_the_state_is_the_one_the_dashboard_shows(self, client, auth, funded_user):
        expired(funded_user, code='exp00001')
        create_link(funded_user, code='cap00001', max_clicks=1, click_count=1)
        create_link(funded_user, code='hld00001', held_for_review_at=timezone.now())
        create_link(funded_user, code='blk00001', destination='https://bad.example/x')
        BlockedDomain.objects.create(value='bad.example')

        states = {
            link['code']: link['state'] for link in get(client, '/links', auth).json()['results']
        }

        assert states == {
            'exp00001': 'expired',
            'cap00001': 'capped',
            'hld00001': 'held',
            'blk00001': 'blocked',
        }

    def test_sorting(self, client, auth, funded_user):
        create_link(funded_user, code='s0000001', click_count=5)
        create_link(funded_user, code='s0000002', click_count=9)
        create_link(funded_user, code='s0000003', click_count=1)

        assert codes(get(client, '/links', auth, sort='-click_count')) == [
            's0000002',
            's0000001',
            's0000003',
        ]
        assert codes(get(client, '/links', auth, sort='click_count'))[0] == 's0000003'

    @pytest.mark.parametrize(
        'query', [{'status': 'sleeping'}, {'sort': 'code'}, {'sort': '-created_at;drop'}]
    )
    def test_an_unknown_status_or_sort_is_a_400(self, client, auth, query):
        response = get(client, '/links', auth, **query)

        assert response.status_code == 400
        assert 'must be one of' in response.json()['detail']

    def test_paging_and_the_clamped_limits(self, client, auth, funded_user):
        for n in range(5):
            create_link(funded_user, code=f'p000000{n}')

        page = get(client, '/links', auth, limit=2, offset=2).json()
        assert (page['count'], page['limit'], page['offset'], len(page['results'])) == (5, 2, 2, 2)
        clamped = get(client, '/links', auth, limit=1000, offset=-5).json()
        assert (clamped['limit'], clamped['offset']) == (100, 0)
        assert get(client, '/links', auth, limit=0).json()['limit'] == 1
        assert get(client, '/links', auth, limit='abc').status_code == 422

    def test_the_number_of_queries_does_not_grow_with_the_page(self, client, auth, funded_user):
        create_link(funded_user)
        get(client, '/links', auth)  # warm up (content types, settings row)
        with CaptureQueriesContext(connection) as one:
            get(client, '/links', auth)
        for _ in range(10):
            create_link(funded_user)
        with CaptureQueriesContext(connection) as many:
            response = get(client, '/links', auth)

        assert len(response.json()['results']) == 11
        assert len(many) == len(one)


class TestGet:
    def test_the_shape(self, client, auth, funded_user):
        link = create_link(funded_user, code='Get0001', title='T', tags=['x'])

        response = get(client, f'/links/{link.pk}', auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == DETAIL_FIELDS
        assert body['code'] == 'Get0001'
        assert body['credits_spent'] == 0
        assert body['costs'] == {'enable': None, 'analytics': 1, 'password': 1}

    def test_the_password_is_never_returned(self, client, auth, funded_user):
        link = create_link(funded_user, password_hash='pbkdf2_sha256$secret')

        body = get(client, f'/links/{link.pk}', auth).json()

        assert body['password_protected'] is True
        assert 'secret' not in str(body)

    def test_a_taken_down_link_shows_the_reason_but_never_the_note(self, client, auth, funded_user):
        link = create_link(
            funded_user,
            status=LinkStatus.TAKEN_DOWN,
            takedown_reason='Phishing page',
            takedown_internal_note='reported by Bob',
        )

        body = get(client, f'/links/{link.pk}', auth).json()

        assert body['status'] == 'taken_down'
        assert body['takedown_reason'] == 'Phishing page'
        assert 'Bob' not in str(body)

    def test_the_costs_of_the_next_paid_actions(self, client, auth, funded_user, prices):
        prices.free_active_links = 1
        prices.save()
        create_link(funded_user)
        disabled = create_link(funded_user, status=LinkStatus.DISABLED)
        paid_disabled = create_link(
            funded_user,
            status=LinkStatus.DISABLED,
            extra_link_paid_at=timezone.now(),
            password_paid_at=timezone.now(),
            analytics_paid_at=timezone.now(),
        )

        assert get(client, f'/links/{disabled.pk}', auth).json()['costs']['enable'] == 1
        assert get(client, f'/links/{paid_disabled.pk}', auth).json()['costs'] == {
            'enable': 0,
            'analytics': 0,
            'password': 0,
        }

    def test_another_users_and_a_deleted_link_are_404(self, client, auth, funded_user, other_user):
        foreign = create_link(other_user)
        gone = create_link(funded_user, status=LinkStatus.DELETED)

        for link in (foreign, gone):
            response = get(client, f'/links/{link.pk}', auth)
            assert response.status_code == 404

    def test_a_malformed_id_is_a_422(self, client, auth):
        assert get(client, '/links/not-a-uuid', auth).status_code == 422


class TestCreate:
    def test_a_plain_link_is_free(self, client, auth, funded_user):
        before = balance(funded_user)

        response = post(client, '/links', auth, {'destination': 'https://example.com/a?b=1'})

        assert response.status_code == 201
        body = response.json()
        assert set(body) == DETAIL_FIELDS
        assert len(body['code']) == 7
        assert body['is_alias'] is False
        assert body['destination'] == 'https://example.com/a?b=1'
        assert body['status'] == 'active' and body['filter_bots'] is True
        assert body['credits_spent'] == 0
        assert balance(funded_user) == before
        assert ShortLink.objects.get(pk=body['id']).owner == funded_user

    def test_every_field(self, client, auth):
        expires = (timezone.now() + timedelta(days=3)).isoformat()

        response = post(
            client,
            '/links',
            auth,
            {
                'destination': 'https://example.com/a',
                'alias': 'Spring-Sale',
                'title': 'Spring  sale',
                'tags': ['Promo', 'spring sale'],
                'expires_at': expires,
                'max_clicks': 50,
                'password': 'open sesame',
                'filter_bots': False,
                'utm_source': 'news',
                'utm_campaign': 'spring',
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body['code'] == 'Spring-Sale' and body['is_alias'] is True
        assert body['short_url'] == 'https://app.example.test/s/Spring-Sale'
        assert body['title'] == 'Spring sale'
        assert body['tags'] == ['promo', 'spring-sale']
        assert body['max_clicks'] == 50
        assert body['password_protected'] is True
        assert body['filter_bots'] is False
        assert body['destination'] == 'https://example.com/a?utm_source=news&utm_campaign=spring'
        assert body['credits_spent'] == 2  # the alias and the password

    def test_the_alias_is_case_insensitive_and_the_redirect_follows_the_destination(
        self, client, auth
    ):
        post(client, '/links', auth, {'destination': 'https://example.com/a', 'alias': 'Hello'})

        response = client.get('/s/hELLO')

        assert response.status_code == 302
        assert response['Location'] == 'https://example.com/a'

    @pytest.mark.parametrize('alias', ['Hello', 'hello', 'HELLO', 'admin', 'api', 'help'])
    def test_an_alias_that_is_taken_or_reserved_is_a_409(self, client, auth, other_user, alias):
        create_link(other_user, code='Hello')

        response = post(
            client, '/links', auth, {'destination': 'https://example.com', 'alias': alias}
        )

        assert response.status_code == 409
        assert response.json()['detail'] == 'alias: That alias is not available.'
        assert ShortLink.objects.filter(owner__email='testuser@example.com').count() == 0

    def test_the_409_does_not_say_whose_alias_it_is(self, client, auth, other_user):
        create_link(other_user, code='Taken')

        taken = post(client, '/links', auth, {'destination': 'https://x.example', 'alias': 'taken'})
        reserved = post(
            client, '/links', auth, {'destination': 'https://x.example', 'alias': 'admin'}
        )

        assert taken.json() == reserved.json()

    def test_a_deleted_links_alias_is_never_reissued(self, client, auth):
        first = post(client, '/links', auth, {'destination': 'https://x.example', 'alias': 'mine'})
        delete(client, f'/links/{first.json()["id"]}', auth)

        again = post(client, '/links', auth, {'destination': 'https://x.example', 'alias': 'mine'})

        assert again.status_code == 409

    @pytest.mark.parametrize('alias', ['ab', 'has space', 'ünï', 'x' * 33, 'a/b'])
    def test_a_badly_shaped_alias_is_a_400(self, client, auth, alias):
        response = post(
            client, '/links', auth, {'destination': 'https://x.example', 'alias': alias}
        )

        assert response.status_code == 400
        assert response.json()['detail'].startswith('alias:')

    @pytest.mark.parametrize(
        'destination',
        [
            '',
            'ftp://example.com/file',
            'javascript:alert(1)',
            'http://127.0.0.1/admin',
            'http://localhost/x',
            'https://user:pw@example.com/',
            'https://app.example.test/s/abc',
            'https://app.example.test/go/abc',
            'not a url',
        ],
    )
    def test_a_refused_destination_is_a_400_keyed_destination(self, client, auth, destination):
        response = post(client, '/links', auth, {'destination': destination})

        assert response.status_code == 400
        assert response.json()['detail'].startswith('destination:')
        assert not ShortLink.objects.exists()

    def test_a_blocked_domain_is_a_403_with_the_neutral_message(self, client, auth):
        BlockedDomain.objects.create(value='*.evil.example')

        response = post(client, '/links', auth, {'destination': 'https://www.evil.example/x'})

        assert response.status_code == 403
        assert response.json()['detail'] == 'destination: That address cannot be used.'
        assert not ShortLink.objects.exists()

    @pytest.mark.parametrize(
        ('field', 'value'),
        [
            ('expires_at', '2020-01-01T00:00:00Z'),
            ('max_clicks', 0),
            ('max_clicks', 2_000_000_001),
            ('tags', ['bad tag!']),
            ('tags', [f't{n}' for n in range(21)]),
            ('title', 'x' * 300),
        ],
    )
    def test_a_bad_field_is_a_400_keyed_by_it(self, client, auth, field, value):
        response = post(client, '/links', auth, {'destination': 'https://x.example', field: value})

        assert response.status_code == 400
        assert response.json()['detail'].startswith(f'{field}:')

    def test_the_cap_on_links_is_a_400(self, client, auth, funded_user, prices):
        for _ in range(prices.max_active_links):
            create_link(funded_user, status=LinkStatus.DISABLED)

        response = post(client, '/links', auth, {'destination': 'https://x.example'})

        assert response.status_code == 400
        assert 'at most 5 links' in response.json()['detail']

    def test_validation_errors_are_422_lists(self, client, auth):
        for payload in (
            {},  # destination is required
            {'destination': 'https://x.example', 'max_clicks': 'many'},
            {'destination': 'https://x.example', 'status': 'disabled'},  # unknown key
            {'destination': 'https://x.example', 'expected_cost': -1},
            {'destination': 'https://x.example', 'tags': 'a,b'},
        ):
            response = post(client, '/links', auth, payload)
            assert response.status_code == 422
            assert isinstance(response.json()['detail'], list)

    def test_a_link_without_a_title_gets_one_fetched_in_the_background(
        self, client, auth, monkeypatch, django_capture_on_commit_callbacks
    ):
        queued = []
        monkeypatch.setattr(
            'apps.short_url.services.links.enqueue_after_commit',
            lambda task, *args: queued.append(args),
        )

        with django_capture_on_commit_callbacks(execute=True):
            body = post(client, '/links', auth, {'destination': 'https://x.example'}).json()

        assert queued == [(body['id'],)]

    def test_a_link_with_a_title_fetches_nothing(self, client, auth, monkeypatch):
        queued = []
        monkeypatch.setattr(
            'apps.short_url.services.links.enqueue_after_commit',
            lambda task, *args: queued.append(args),
        )

        create(client, auth)

        assert queued == []


class TestQuote:
    def test_a_free_link(self, client, auth):
        response = post(client, '/links/quote', auth, {'destination': 'https://x.example'})

        assert response.status_code == 200
        assert response.json() == {'credits': 0, 'charges': [], 'balance': 10, 'affordable': True}

    def test_what_each_extra_costs(self, client, auth, prices):
        prices.custom_alias_price = 3
        prices.password_price = 2
        prices.save()

        response = post(
            client,
            '/links/quote',
            auth,
            {'destination': 'https://x.example', 'alias': 'quoted', 'password': 'pw'},
        )

        assert response.json()['credits'] == 5
        assert response.json()['charges'] == [
            {'extra': 'alias', 'label': 'Custom alias', 'credits': 3},
            {'extra': 'password', 'label': 'Password', 'credits': 2},
        ]

    def test_an_active_link_beyond_the_allowance_is_in_the_quote(self, client, auth, funded_user):
        create_link(funded_user)
        create_link(funded_user)

        response = post(client, '/links/quote', auth, {'destination': 'https://x.example'})

        assert response.json()['charges'] == [
            {'extra': 'extra_link', 'label': 'Extra active link', 'credits': 1}
        ]

    def test_it_creates_nothing_and_charges_nothing(self, client, auth, funded_user):
        post(client, '/links/quote', auth, {'destination': 'https://x.example', 'alias': 'quoted'})

        assert not ShortLink.objects.exists()
        assert balance(funded_user) == 10

    def test_it_says_when_the_balance_does_not_cover_it(self, client, other_auth):
        response = post(
            client,
            '/links/quote',
            other_auth,
            {'destination': 'https://x.example', 'alias': 'quoted'},
        )

        assert response.status_code == 200
        assert response.json()['affordable'] is False
        assert response.json()['balance'] == 0

    def test_it_refuses_what_the_create_would_refuse(self, client, auth, other_user):
        create_link(other_user, code='Taken')

        taken = post(
            client, '/links/quote', auth, {'destination': 'https://x.example', 'alias': 'taken'}
        )
        bad = post(client, '/links/quote', auth, {'destination': 'http://127.0.0.1/'})
        BlockedDomain.objects.create(value='evil.example')
        blocked = post(client, '/links/quote', auth, {'destination': 'https://evil.example/'})

        assert (taken.status_code, bad.status_code, blocked.status_code) == (409, 400, 403)

    def test_it_does_not_spend_the_create_budget(self, client, auth, settings):
        from .api_support import limit_rule

        limit_rule(settings, 'SU_CREATE_USER', limit=1)
        for _ in range(3):
            assert (
                post(client, '/links/quote', auth, {'destination': 'https://x.example'}).status_code
                == 200
            )

        assert create(client, auth).status_code == 201


class TestUpdate:
    def test_only_the_fields_present_change(self, client, auth, funded_user):
        link = create_link(funded_user, title='Old', tags=['a'], max_clicks=5)

        response = patch(client, f'/links/{link.pk}', auth, {'title': 'New'})

        assert response.status_code == 200
        body = response.json()
        assert set(body) == DETAIL_FIELDS
        assert (body['title'], body['tags'], body['max_clicks']) == ('New', ['a'], 5)

    def test_the_destination_changes_and_the_redirect_follows(self, client, auth, funded_user):
        link = create_link(funded_user, code='Move001', destination='https://example.com/old')

        response = patch(
            client,
            f'/links/{link.pk}',
            auth,
            {'destination': 'https://example.com/new', 'utm_source': 'api'},
        )

        assert response.status_code == 200
        assert response.json()['destination'] == 'https://example.com/new?utm_source=api'
        assert client.get('/s/Move001')['Location'] == 'https://example.com/new?utm_source=api'

    def test_utm_alone_merges_into_the_current_destination(self, client, auth, funded_user):
        link = create_link(funded_user, destination='https://example.com/p?x=1')

        body = patch(client, f'/links/{link.pk}', auth, {'utm_medium': 'email'}).json()

        assert body['destination'] == 'https://example.com/p?x=1&utm_medium=email'

    def test_null_clears_the_expiry_the_click_cap_and_the_password(self, client, auth, funded_user):
        link = create_link(
            funded_user,
            expires_at=timezone.now() + timedelta(days=1),
            max_clicks=9,
            password_hash='pbkdf2_sha256$x',
        )

        body = patch(
            client,
            f'/links/{link.pk}',
            auth,
            {'expires_at': None, 'max_clicks': None, 'password': None},
        ).json()

        assert body['expires_at'] is None
        assert body['max_clicks'] is None
        assert body['password_protected'] is False

    def test_setting_the_expiry_the_cap_and_bot_filtering(self, client, auth, funded_user):
        link = create_link(funded_user)
        expires = (timezone.now() + timedelta(days=2)).replace(microsecond=0)

        body = patch(
            client,
            f'/links/{link.pk}',
            auth,
            {'expires_at': expires.isoformat(), 'max_clicks': 3, 'filter_bots': False},
        ).json()

        assert body['expires_at'].startswith(expires.strftime('%Y-%m-%dT%H:%M:%S'))
        assert (body['max_clicks'], body['filter_bots']) == (3, False)

    def test_tags_are_replaced(self, client, auth, funded_user):
        link = create_link(funded_user, tags=['old'])

        body = patch(client, f'/links/{link.pk}', auth, {'tags': ['New', 'other']}).json()

        assert body['tags'] == ['new', 'other']
        assert patch(client, f'/links/{link.pk}', auth, {'tags': []}).json()['tags'] == []

    @pytest.mark.parametrize(
        'payload',
        [
            {'destination': None},
            {'title': None},
            {'tags': None},
            {'filter_bots': None},
            {'alias': 'changed'},  # the alias is fixed at creation
            {'status': 'disabled'},
            {'click_count': 0},
            {'nonsense': 1},
            {'max_clicks': 'many'},
        ],
    )
    def test_a_null_for_a_required_field_or_an_unknown_key_is_a_422(
        self, client, auth, funded_user, payload
    ):
        link = create_link(funded_user, title='Same')

        response = patch(client, f'/links/{link.pk}', auth, payload)

        assert response.status_code == 422
        assert isinstance(response.json()['detail'], list)
        link.refresh_from_db()
        assert link.title == 'Same' and link.status == LinkStatus.ACTIVE

    def test_a_refused_field_changes_nothing(self, client, auth, funded_user):
        link = create_link(funded_user, title='Same')

        response = patch(
            client, f'/links/{link.pk}', auth, {'title': 'Changed', 'destination': 'ftp://x'}
        )

        assert response.status_code == 400
        assert response.json()['detail'].startswith('destination:')
        link.refresh_from_db()
        assert link.title == 'Same'

    def test_a_blocked_destination_is_a_403(self, client, auth, funded_user):
        link = create_link(funded_user)
        BlockedDomain.objects.create(value='evil.example')

        response = patch(client, f'/links/{link.pk}', auth, {'destination': 'https://evil.example'})

        assert response.status_code == 403

    def test_a_held_link_is_frozen_with_a_409(self, client, auth, funded_user):
        link = create_link(funded_user, held_for_review_at=timezone.now())

        response = patch(client, f'/links/{link.pk}', auth, {'title': 'x'})

        assert response.status_code == 409
        assert 'under review' in response.json()['detail']

    def test_a_taken_down_link_cannot_be_changed(self, client, auth, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)

        response = patch(client, f'/links/{link.pk}', auth, {'title': 'x'})

        assert response.status_code == 400
        assert 'can no longer be changed' in response.json()['detail']

    def test_another_users_and_a_deleted_link_are_404(self, client, auth, funded_user, other_user):
        foreign = create_link(other_user, title='Theirs')
        gone = create_link(funded_user, status=LinkStatus.DELETED)

        assert patch(client, f'/links/{foreign.pk}', auth, {'title': 'x'}).status_code == 404
        assert patch(client, f'/links/{gone.pk}', auth, {'title': 'x'}).status_code == 404
        foreign.refresh_from_db()
        assert foreign.title == 'Theirs'

    def test_an_empty_body_changes_nothing(self, client, auth, funded_user):
        link = create_link(funded_user, title='Same')

        response = patch(client, f'/links/{link.pk}', auth, {})

        assert response.status_code == 200
        assert response.json()['title'] == 'Same'


class TestEnableDisableDelete:
    def test_disable_then_enable(self, client, auth, funded_user):
        link = create_link(funded_user, code='Tog0001')

        disabled = post(client, f'/links/{link.pk}/disable', auth)
        assert disabled.status_code == 200
        assert (disabled.json()['status'], disabled.json()['state']) == ('disabled', 'disabled')
        assert client.get('/s/Tog0001').status_code == 404

        enabled = post(client, f'/links/{link.pk}/enable', auth)
        assert enabled.status_code == 200
        assert enabled.json()['status'] == 'active'
        assert client.get('/s/Tog0001').status_code == 302

    def test_both_are_idempotent(self, client, auth, funded_user):
        link = create_link(funded_user)

        assert post(client, f'/links/{link.pk}/enable', auth).json()['status'] == 'active'
        post(client, f'/links/{link.pk}/disable', auth)
        assert post(client, f'/links/{link.pk}/disable', auth).json()['status'] == 'disabled'

    def test_enabling_a_link_whose_destination_became_blocked_is_a_403(
        self, client, auth, funded_user
    ):
        link = create_link(
            funded_user, status=LinkStatus.DISABLED, destination='https://evil.example/x'
        )
        BlockedDomain.objects.create(value='evil.example')

        response = post(client, f'/links/{link.pk}/enable', auth)

        assert response.status_code == 403
        assert balance(funded_user) == 10
        link.refresh_from_db()
        assert link.status == LinkStatus.DISABLED

    def test_a_held_link_can_be_disabled_but_not_enabled(self, client, auth, funded_user):
        held = create_link(funded_user, held_for_review_at=timezone.now())
        disabled_held = create_link(
            funded_user, status=LinkStatus.DISABLED, held_for_review_at=timezone.now()
        )

        assert post(client, f'/links/{held.pk}/disable', auth).status_code == 200
        assert post(client, f'/links/{disabled_held.pk}/enable', auth).status_code == 409

    def test_a_taken_down_link_can_do_neither(self, client, auth, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)

        assert post(client, f'/links/{link.pk}/enable', auth).status_code == 400
        assert post(client, f'/links/{link.pk}/disable', auth).status_code == 400

    def test_other_users_links_are_404(self, client, auth, other_user):
        foreign = create_link(other_user)

        for action in ('enable', 'disable', 'analytics'):
            assert post(client, f'/links/{foreign.pk}/{action}', auth).status_code == 404
        assert delete(client, f'/links/{foreign.pk}', auth).status_code == 404
        foreign.refresh_from_db()
        assert foreign.status == LinkStatus.ACTIVE

    def test_delete_is_a_soft_delete_and_the_link_is_gone_for_its_owner(
        self, client, auth, funded_user
    ):
        link = create_link(funded_user, code='Del0001')

        response = delete(client, f'/links/{link.pk}', auth)

        assert response.status_code == 204
        link.refresh_from_db()
        assert link.status == LinkStatus.DELETED  # the row stays: the code is never reused
        assert client.get('/s/Del0001').status_code == 404
        assert get(client, f'/links/{link.pk}', auth).status_code == 404
        assert delete(client, f'/links/{link.pk}', auth).status_code == 404
        assert get(client, '/links', auth).json()['count'] == 0

    def test_a_held_link_cannot_be_deleted(self, client, auth, funded_user):
        link = create_link(funded_user, held_for_review_at=timezone.now())

        assert delete(client, f'/links/{link.pk}', auth).status_code == 409

    def test_a_taken_down_link_cannot_be_deleted(self, client, auth, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)

        assert delete(client, f'/links/{link.pk}', auth).status_code == 400


class TestErrorMapping:
    def test_an_inactive_owner_is_a_403(self):
        from ninja.errors import HttpError

        from ..api.common import mapped_errors
        from ..services import OwnerInactiveError

        with pytest.raises(HttpError) as raised, mapped_errors():
            raise OwnerInactiveError()

        assert raised.value.status_code == 403

    def test_a_cost_that_changed_names_the_new_total(self):
        from ninja.errors import HttpError

        from ..api.common import mapped_errors
        from ..services import CostChangedError, Quote

        with pytest.raises(HttpError) as raised, mapped_errors():
            raise CostChangedError(Quote())

        assert raised.value.status_code == 409
        assert 'it is now free' in str(raised.value)
        assert 'expected_cost=0' in str(raised.value)


def test_an_account_that_is_not_active_cannot_use_the_api(client, funded_user):
    headers = bearer(funded_user)
    User.objects.filter(pk=funded_user.pk).update(status=User.STATUS_INACTIVE)

    assert get(client, '/links', headers).status_code == 401
