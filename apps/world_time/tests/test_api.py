"""The World Time JWT API (`/api/time/`): tabs, locations, search, the grid, a moment, shared
events and settings, over the real URLs."""

import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import pytest
from ninja import Schema

from config.api import TAGS, api

from ..models import Event, Location, Tab
from ..services import events, list_locations, list_tabs
from .api_support import auth, bearer, delete, get, limit_rule, other_auth, patch, post  # noqa: F401
from .factories import make_city, make_event, make_location, make_tab, make_user

pytestmark = pytest.mark.django_db

START = '2026-12-10T09:00:00Z'
END = '2026-12-10T10:30:00Z'


@pytest.fixture
def now(freeze_now):
    return freeze_now(2026, 12, 1, 12, 0)


@pytest.fixture
def tab(user) -> Tab:
    """The user's first tab: one row, Lisbon (the account's zone), the home."""
    return list_tabs(user)[0]


def operations() -> list[tuple[str, str]]:
    schema = api.get_openapi_schema()
    return [
        (method.upper(), path)
        for path, methods in schema['paths'].items()
        if path.startswith('/api/time/')
        for method in methods
    ]


class TestWiring:
    def test_the_tag_is_documented(self):
        assert 'World Time' in [tag['name'] for tag in TAGS]
        for path, methods in api.get_openapi_schema()['paths'].items():
            if path.startswith('/api/time/'):
                for operation in methods.values():
                    assert operation['tags'] == ['World Time'], path

    def test_every_route_needs_a_token(self, client):
        found = operations()
        assert len(found) == 18  # a new endpoint is a deliberate change here
        for method, path in found:
            target = re.sub(r'\{[a-z_]+\}', str(uuid.uuid4()), path)
            response = client.generic(method, target, data='{}', content_type='application/json')
            assert response.status_code == 401, (method, path)

    def test_a_deactivated_account_is_a_401(self, client, user, auth):
        user.status = type(user).STATUS_INACTIVE
        user.save()
        assert get(client, '/tabs', auth).status_code == 401

    def test_the_schema_names_are_unique_across_the_apis(self):
        seen: dict[str, set[str]] = defaultdict(set)

        def walk(parent: type) -> None:
            for child in parent.__subclasses__():
                seen[child.__name__].add(child.__module__)
                walk(child)

        walk(Schema)
        ours = {name: modules for name, modules in seen.items() if name.startswith('WorldTime')}
        assert len(ours) >= 18
        assert all(len(modules) == 1 for modules in ours.values())
        clashes = {
            name
            for name, modules in seen.items()
            if any(module.startswith('apps.world_time') for module in modules) and len(modules) > 1
        }
        assert not clashes
        assert all(
            name.startswith('WorldTime')
            for name, modules in seen.items()
            if any(module.startswith('apps.world_time') for module in modules)
        )


class TestTabs:
    def test_a_first_call_makes_the_first_tab(self, client, user, auth):
        body = get(client, '/tabs', auth).json()
        assert [(tab['name'], tab['location_count'], tab['position']) for tab in body] == [
            ('My times', 1, 0)
        ]
        detail = get(client, f'/tabs/{body[0]["id"]}', auth).json()
        (home,) = detail['locations']
        assert (home['title'], home['timezone'], home['home'], home['geoname_id']) == (
            'Lisbon',
            'Europe/Lisbon',
            True,
            None,
        )

    def test_create_rename_delete(self, client, user, auth, tab):
        response = post(client, '/tabs', auth, {'name': '  Team   Europe '})
        assert response.status_code == 201
        created = response.json()
        assert created['name'] == 'Team Europe' and len(created['locations']) == 1
        renamed = patch(client, f'/tabs/{created["id"]}', auth, {'name': 'Work'})
        assert renamed.status_code == 200 and renamed.json()['name'] == 'Work'
        assert delete(client, f'/tabs/{created["id"]}', auth).status_code == 204
        assert not Tab.objects.filter(pk=created['id']).exists()

    @pytest.mark.parametrize(
        ('payload', 'status', 'detail'),
        [
            ({'name': ''}, 400, 'name: Give the tab a name.'),
            ({'name': 'x' * 51}, 400, 'name: Keep the name to 50 characters or fewer.'),
            ({'name': 'my TIMES'}, 400, 'name: You already have a tab with this name.'),
            ({'name': 'x', 'colour': 'red'}, 422, None),
            ({}, 422, None),
        ],
    )
    def test_refusals(self, client, auth, tab, payload, status, detail):
        response = post(client, '/tabs', auth, payload)
        assert response.status_code == status
        if detail:
            assert response.json()['detail'] == detail

    def test_the_last_tab_stays(self, client, auth, tab):
        response = delete(client, f'/tabs/{tab.pk}', auth)
        assert response.status_code == 400
        assert response.json()['detail'] == 'Keep at least one tab.'

    def test_the_cap(self, client, auth, tab, config, user):
        make_tab(user, 'Two')
        make_tab(user, 'Three')
        response = post(client, '/tabs', auth, {'name': 'Four'})
        assert response.status_code == 400
        assert response.json()['detail'] == 'You have too many tabs. Delete one first.'

    def test_another_users_tab_is_a_404(self, client, auth, other_user):
        theirs = make_tab(other_user)
        row = make_location(theirs)
        base = f'/tabs/{theirs.pk}'
        assert get(client, base, auth).status_code == 404
        assert patch(client, base, auth, {'name': 'x'}).status_code == 404
        assert delete(client, base, auth).status_code == 404
        assert get(client, f'{base}/grid', auth).status_code == 404
        assert get(client, f'{base}/times', auth).status_code == 404
        assert post(client, f'{base}/locations', auth, {'zone': 'UTC'}).status_code == 404
        assert post(client, f'{base}/locations/reorder', auth, {'ids': []}).status_code == 404
        assert patch(client, f'{base}/locations/{row.pk}', auth, {'label': 'x'}).status_code == 404
        assert delete(client, f'{base}/locations/{row.pk}', auth).status_code == 404
        assert Location.objects.filter(tab=theirs).count() == 1

    def test_a_refused_request_spends_no_budget(self, client, auth, tab, settings, ratelimit_clock):
        limit_rule(settings, 'WT_EDIT_USER')
        for _ in range(3):
            assert post(client, '/tabs', auth, {'name': ''}).status_code == 400
        # The last tab, an unknown city or zone, an empty order: none needs a write to refuse.
        base = f'/tabs/{tab.pk}'
        assert delete(client, base, auth).status_code == 400
        assert post(client, f'{base}/locations', auth, {'city': 999}).status_code == 400
        assert post(client, f'{base}/locations', auth, {'zone': 'Mars/Olympus'}).status_code == 400
        assert post(client, f'{base}/locations/reorder', auth, {'ids': []}).status_code == 200
        assert post(client, '/tabs', auth, {'name': 'One'}).status_code == 201
        response = post(client, '/tabs', auth, {'name': 'Two'})
        assert response.status_code == 429 and 'Retry-After' in response


class TestLocations:
    def test_add_a_city_with_a_label(self, client, auth, tab):
        make_city(
            5,
            'Austin',
            'America/Chicago',
            region='Texas',
            country='United States',
            country_code='US',
        )
        response = post(client, f'/tabs/{tab.pk}/locations', auth, {'city': 5, 'label': 'Office'})
        assert response.status_code == 201
        assert response.json() == {
            'id': response.json()['id'],
            'title': 'Office',
            'name': 'Austin',
            'label': 'Office',
            'region': 'Texas',
            'country': 'United States',
            'country_code': 'US',
            'timezone': 'America/Chicago',
            'geoname_id': 5,
            'home': False,
            'position': 1,
        }

    def test_add_a_zone(self, client, auth, tab):
        response = post(client, f'/tabs/{tab.pk}/locations', auth, {'zone': 'Asia/Tokyo'})
        assert response.status_code == 201
        assert (response.json()['title'], response.json()['geoname_id']) == ('Tokyo', None)

    @pytest.mark.parametrize(
        ('payload', 'status', 'detail'),
        [
            ({}, 400, 'Send city or zone.'),
            ({'city': 5, 'zone': 'UTC'}, 400, 'Send either city or zone, not both.'),
            ({'city': 999}, 400, 'city: Choose a place from the suggestions.'),
            ({'zone': 'Mars/Olympus'}, 400, 'zone: Unknown time zone.'),
            ({'zone': 'Europe/Lisbon'}, 400, 'This location is already in the tab.'),
            (
                {'zone': 'UTC', 'label': 'x' * 61},
                400,
                'label: Keep the label to 60 characters or fewer.',
            ),
            ({'city': 'five'}, 422, None),
            ({'zone': 'UTC', 'home': True}, 422, None),
        ],
    )
    def test_refusals(self, client, auth, tab, payload, status, detail):
        response = post(client, f'/tabs/{tab.pk}/locations', auth, payload)
        assert response.status_code == status
        if detail:
            assert response.json()['detail'] == detail
        assert Location.objects.filter(tab=tab).count() == 1

    def test_a_refused_label_adds_nothing(self, client, auth, tab, monkeypatch):
        # The row and its label are one transaction: a label refused after the add rolls it back.
        from django.core.exceptions import ValidationError

        from .. import services

        def refuse(location, label):
            raise ValidationError({'label': 'No.'})

        monkeypatch.setattr(services, 'set_label', refuse)
        response = post(client, f'/tabs/{tab.pk}/locations', auth, {'zone': 'UTC', 'label': 'x'})
        assert response.status_code == 400
        assert Location.objects.filter(tab=tab).count() == 1

    def test_a_full_tab(self, client, auth, tab, config):
        for zone in ('UTC', 'Asia/Tokyo', 'Asia/Kolkata'):
            make_location(tab, zone, zone)
        response = post(client, f'/tabs/{tab.pk}/locations', auth, {'zone': 'Europe/Paris'})
        assert response.status_code == 400
        assert response.json()['detail'] == 'This tab is full. Remove a location first.'

    def test_label_and_home(self, client, auth, tab):
        tokyo = make_location(tab, 'Tokyo', 'Asia/Tokyo')
        path = f'/tabs/{tab.pk}/locations/{tokyo.pk}'
        body = patch(client, path, auth, {'label': 'Tokyo office', 'home': True}).json()
        assert (body['title'], body['label'], body['home']) == (
            'Tokyo office',
            'Tokyo office',
            True,
        )
        assert [(row.name, row.is_home) for row in list_locations(tab)] == [
            ('Lisbon', False),
            ('Tokyo', True),
        ]
        assert patch(client, path, auth, {'label': None}).json()['title'] == 'Tokyo'
        response = patch(client, path, auth, {'home': False})
        assert response.status_code == 400
        assert response.json()['detail'] == 'home: Make another location the home instead.'
        assert patch(client, path, auth, {'label': 'x' * 61}).status_code == 400
        assert patch(client, path, auth, {'position': 3}).status_code == 422

    def test_an_empty_change_spends_nothing(self, client, auth, tab, settings, ratelimit_clock):
        limit_rule(settings, 'WT_EDIT_USER')
        home = list_locations(tab)[0]
        path = f'/tabs/{tab.pk}/locations/{home.pk}'
        for payload in ({}, {'home': True}, {'home': None}):
            assert patch(client, path, auth, payload).status_code == 200
        assert patch(client, path, auth, {'label': 'HQ'}).status_code == 200
        assert patch(client, path, auth, {'label': 'HQ2'}).status_code == 429

    def test_a_location_of_another_tab_is_a_404(self, client, auth, tab, user, other_user):
        # The caller's own tab in the path, with a row of their other tab, or of someone else's.
        mine_elsewhere = make_location(make_tab(user, 'Other'), 'Paris', 'Europe/Paris')
        theirs = make_location(make_tab(other_user), 'Rome', 'Europe/Rome')
        for row in (mine_elsewhere, theirs):
            path = f'/tabs/{tab.pk}/locations/{row.pk}'
            assert patch(client, path, auth, {'label': 'x'}).status_code == 404
            assert delete(client, path, auth).status_code == 404
            row.refresh_from_db()
            assert row.label == ''

    def test_remove_and_the_home_moves(self, client, auth, tab):
        make_location(tab, 'Tokyo', 'Asia/Tokyo')
        home = list_locations(tab)[0]
        assert delete(client, f'/tabs/{tab.pk}/locations/{home.pk}', auth).status_code == 204
        assert [(row.name, row.is_home) for row in list_locations(tab)] == [('Tokyo', True)]
        assert delete(client, f'/tabs/{tab.pk}/locations/{home.pk}', auth).status_code == 404

    def test_reorder(self, client, auth, tab, user):
        tokyo = make_location(tab, 'Tokyo', 'Asia/Tokyo')
        utc = make_location(tab, 'UTC', 'UTC')
        stranger = make_location(make_tab(user, 'Other'), 'Paris', 'Europe/Paris')
        response = post(
            client,
            f'/tabs/{tab.pk}/locations/reorder',
            auth,
            {'ids': [str(utc.pk), str(stranger.pk), str(tokyo.pk)]},
        )
        assert response.status_code == 200
        assert [row['title'] for row in response.json()] == ['Lisbon', 'UTC', 'Tokyo']
        too_many = {'ids': [str(uuid.uuid4()) for _ in range(501)]}
        assert post(client, f'/tabs/{tab.pk}/locations/reorder', auth, too_many).status_code == 422


class TestSearch:
    def test_cities_zones_and_abbreviations(self, client, auth):
        make_city(1, 'Lisbon', population=517802, country='Portugal', other_names=['Lisboa'])
        assert get(client, '/search', auth, q='lisboa').json() == [
            {
                'kind': 'city',
                'value': '1',
                'city_id': 1,
                'name': 'Lisbon',
                'detail': 'Portugal',
                'hint': 'also Lisboa',
            }
        ]
        first = get(client, '/search', auth, q='pst').json()[0]
        assert first == {
            'kind': 'zone',
            'value': 'America/Los_Angeles',
            'city_id': None,
            'name': 'Los Angeles',
            'detail': 'Pacific Time (PST)',
            'hint': 'Time zone',
        }
        assert get(client, '/search', auth, q='z').json() == []
        assert get(client, '/search', auth).json() == []

    def test_it_is_rate_limited(self, client, auth, settings, ratelimit_clock):
        limit_rule(settings, 'WT_SEARCH_USER')
        # A search that can match nothing runs no query and spends nothing.
        for text in ('', 'z', '  ', '!'):
            assert get(client, '/search', auth, q=text).json() == []
        assert get(client, '/search', auth, q='lis').status_code == 200
        assert get(client, '/search', auth, q='lisb').status_code == 429


class TestGrid:
    @pytest.fixture
    def rows(self, tab):
        make_location(tab, 'New York', 'America/New_York')
        make_location(tab, 'Kathmandu', 'Asia/Kathmandu')

    def test_a_day(self, client, auth, tab, rows, now):
        body = get(client, f'/tabs/{tab.pk}/grid', auth, date='2026-12-10').json()
        assert (body['date'], body['today'], body['hours'], body['minutes']) == (
            '2026-12-10',
            '2026-12-01',
            24,
            1440,
        )
        assert body['start'] == '2026-12-10T00:00:00Z'
        lisbon, new_york, kathmandu = body['rows']
        assert lisbon['location']['home'] is True and lisbon['minutes_from_home'] == 0
        assert (new_york['utc_offset_minutes'], new_york['minutes_from_home']) == (-300, -300)
        assert new_york['zone_name'] == 'EST' and new_york['steps'] == [[0, -300]]
        assert len(lisbon['tiles']) == 24
        assert lisbon['tiles'][9] == {
            'local': '2026-12-10T09:00',
            'band': 'day',
            'weekend': False,
            'day_start': False,
        }
        assert new_york['tiles'][9]['local'] == '2026-12-10T04:00'
        assert new_york['tiles'][5] == {
            'local': '2026-12-10T00:00',
            'band': 'night',
            'weekend': False,
            'day_start': True,
        }
        assert kathmandu['tiles'][9]['local'] == '2026-12-10T14:45'

    def test_today_by_default_and_a_clock_change(self, client, auth, tab, rows, freeze_now):
        freeze_now(2026, 10, 22, 12, 0)
        body = get(client, f'/tabs/{tab.pk}/grid', auth).json()
        assert body['date'] == body['today'] == '2026-10-22'
        assert body['rows'][0]['dst_note'] == 'Clocks go back 1 hour on Sun Oct 25, 2026'
        assert body['rows'][2]['dst_note'] == ''
        long_day = get(client, f'/tabs/{tab.pk}/grid', auth, date='2026-10-25').json()
        assert (long_day['hours'], long_day['minutes']) == (25, 1500)
        assert long_day['rows'][0]['steps'] == [[0, 60], [120, 0]]

    def test_the_wording_is_public_whatever_the_account(self, client, auth, tab, user, freeze_now):
        freeze_now(2026, 10, 22, 12, 0)
        user.date_format, user.time_format = 'dmy', '24h'
        user.save()
        body = get(client, f'/tabs/{tab.pk}/grid', auth).json()
        assert body['rows'][0]['dst_note'] == 'Clocks go back 1 hour on Sun Oct 25, 2026'

    @pytest.mark.parametrize(
        ('value', 'detail'),
        [
            ('tomorrow', 'date must be a date like 2026-10-09.'),
            ('2099-01-01', 'date must be within ten years of today.'),
        ],
    )
    def test_a_bad_date(self, client, auth, tab, now, value, detail):
        response = get(client, f'/tabs/{tab.pk}/grid', auth, date=value)
        assert response.status_code == 400 and response.json()['detail'] == detail

    def test_an_empty_tab(self, client, auth, user, now):
        empty = make_tab(user, 'Empty')
        body = get(client, f'/tabs/{empty.pk}/grid', auth).json()
        assert body['rows'] == [] and body['start'] is None

    def test_few_queries_whatever_the_rows(
        self, client, auth, tab, rows, django_assert_max_num_queries
    ):
        with django_assert_max_num_queries(4):
            assert get(client, f'/tabs/{tab.pk}/grid', auth).status_code == 200


class TestTimes:
    def test_now(self, client, auth, tab, now):
        make_location(tab, 'Tokyo', 'Asia/Tokyo', label='Tokyo office')
        body = get(client, f'/tabs/{tab.pk}/times', auth).json()
        assert body['at'] == '2026-12-01T12:00:00Z'
        lisbon, tokyo = body['rows']
        assert (lisbon['local'], lisbon['minutes_from_home']) == ('2026-12-01T12:00', 0)
        assert (tokyo['local'], tokyo['zone_name'], tokyo['utc_offset_minutes']) == (
            '2026-12-01T21:00',
            'JST',
            540,
        )
        assert tokyo['location']['title'] == 'Tokyo office' and tokyo['minutes_from_home'] == 540

    def test_at_a_moment_on_the_home_clock_or_with_an_offset(self, client, auth, tab, now):
        make_location(tab, 'Tokyo', 'Asia/Tokyo')
        set_home = Location.objects.filter(tab=tab)
        set_home.filter(name='Lisbon').update(is_home=False)
        set_home.filter(name='Tokyo').update(is_home=True)
        naive = get(client, f'/tabs/{tab.pk}/times', auth, at='2026-07-01T09:00').json()
        lisbon, tokyo = naive['rows']
        assert tokyo['local'] == '2026-07-01T09:00', "no offset: the home row's wall clock"
        assert (lisbon['local'], lisbon['minutes_from_home']) == ('2026-07-01T01:00', -480)
        aware = get(client, f'/tabs/{tab.pk}/times', auth, at='2026-07-01T09:00:00+00:00').json()
        assert aware['rows'][1]['local'] == '2026-07-01T18:00'

    def test_the_instant_is_answered_in_utc(self, client, auth, tab, now):
        body = get(client, f'/tabs/{tab.pk}/times', auth, at='2026-07-01T09:00:00+02:00').json()
        assert body['at'] == '2026-07-01T07:00:00Z'
        naive = get(client, f'/tabs/{tab.pk}/times', auth, at='2026-07-01T09:00').json()
        assert naive['at'] == '2026-07-01T08:00:00Z', 'Lisbon is on UTC+1 in July'
        assert get(client, f'/tabs/{tab.pk}/times', auth, at='2026-07-01').json()['at'] == (
            '2026-06-30T23:00:00Z'
        )

    @pytest.mark.parametrize(
        'value',
        ['0001-01-01T00:00', '9999-12-31T23:59', '0001-01-01T00:00+14:00', '2040-01-01T00:00Z'],
    )
    def test_a_moment_too_far_away_is_a_400_not_a_crash(self, client, auth, tab, now, value):
        response = get(client, f'/tabs/{tab.pk}/times', auth, at=value)
        assert response.status_code == 400
        assert response.json()['detail'] == 'at must be within ten years of now.'

    def test_a_wall_time_the_home_clock_skips_is_refused(self, client, auth, tab, now):
        # Lisbon, 29 March 2026: 1:00 becomes 2:00.
        response = get(client, f'/tabs/{tab.pk}/times', auth, at='2026-03-29T01:30')
        assert response.status_code == 400
        assert "doesn't exist on the home location's clock" in response.json()['detail']
        # The repeated hour of 25 October is its first occurrence (still summer time).
        body = get(client, f'/tabs/{tab.pk}/times', auth, at='2026-10-25T01:30').json()
        assert body['at'] == '2026-10-25T00:30:00Z'

    def test_a_bad_moment(self, client, auth, tab):
        response = get(client, f'/tabs/{tab.pk}/times', auth, at='soon')
        assert response.status_code == 400
        assert response.json()['detail'] == 'at must be a date and time like 2026-10-09T15:00.'


class TestEvents:
    def test_create_read_update_delete(self, client, auth, user, tab, now):
        make_location(
            tab, 'Tokyo', 'Asia/Tokyo', label='Tokyo office', geoname_id=1850147, country='Japan'
        )
        response = post(
            client,
            '/events',
            auth,
            {'tab': str(tab.pk), 'title': 'Team call', 'starts_at': START, 'ends_at': END},
        )
        assert response.status_code == 201
        body = response.json()
        event = Event.objects.get(pk=body['id'])
        assert body['title'] == 'Team call' and body['ended'] is False
        assert body['url'] == events.event_url(event) and event.code not in ('', None)
        assert (body['starts_at'], body['ends_at']) == (START, END)
        assert body['locations'] == [
            {
                'name': 'Lisbon',
                'detail': 'Europe/Lisbon',
                'timezone': 'Europe/Lisbon',
                'home': True,
                'starts_local': '2026-12-10T09:00',
                'ends_local': '2026-12-10T10:30',
                'utc_offset_minutes': 0,
                'ends_utc_offset_minutes': 0,
            },
            {
                'name': 'Tokyo office',
                'detail': 'Tokyo, Japan',
                'timezone': 'Asia/Tokyo',
                'home': False,
                'starts_local': '2026-12-10T18:00',
                'ends_local': '2026-12-10T19:30',
                'utc_offset_minutes': 540,
                'ends_utc_offset_minutes': 540,
            },
        ]
        assert get(client, f'/events/{event.pk}', auth).json()['id'] == body['id']
        assert [row['id'] for row in get(client, '/events', auth).json()] == [body['id']]

        renamed = patch(client, f'/events/{event.pk}', auth, {'title': 'Planning'})
        assert renamed.status_code == 200 and renamed.json()['title'] == 'Planning'
        moved = patch(
            client,
            f'/events/{event.pk}',
            auth,
            {'starts_at': '2026-12-11T09:00:00Z', 'ends_at': '2026-12-14T09:00:00Z'},
        ).json()
        assert moved['ends_at'] == '2026-12-14T09:00:00Z' and moved['url'] == body['url']
        assert delete(client, f'/events/{event.pk}', auth).status_code == 204
        assert not Event.objects.exists()
        assert delete(client, f'/events/{event.pk}', auth).status_code == 404

    @pytest.mark.parametrize(
        ('changes', 'status', 'detail'),
        [
            ({'title': ''}, 400, 'title: Give the event a title.'),
            ({'title': 'x' * 121}, 400, 'title: Keep the title to 120 characters or fewer.'),
            ({'ends_at': START}, 400, 'ends_at: The end must be after the start.'),
            (
                {'ends_at': '2026-12-18T09:00:00Z'},
                400,
                'ends_at: An event can last at most 7 days.',
            ),
            (
                {'starts_at': '2025-01-10T09:00:00Z', 'ends_at': '2025-01-10T10:00:00Z'},
                400,
                'ends_at: That time has already passed. Choose one that has not ended yet.',
            ),
            ({'starts_at': '2026-12-10T09:00:00'}, 400, 'starts_at: Choose when the event starts.'),
            ({'starts_at': 'soon'}, 422, None),
            ({'colour': 'red'}, 422, None),
        ],
    )
    def test_refusals(self, client, auth, tab, now, changes, status, detail):
        payload = {'tab': str(tab.pk), 'title': 'x', 'starts_at': START, 'ends_at': END, **changes}
        response = post(client, '/events', auth, payload)
        assert response.status_code == status
        if detail:
            assert response.json()['detail'] == detail
        assert not Event.objects.exists()

    def test_update_refusals_and_an_empty_change(
        self, client, auth, user, tab, now, settings, ratelimit_clock
    ):
        event = events.create_event(
            user,
            tab,
            title='x',
            starts_at=datetime(2026, 12, 10, 9, tzinfo=UTC),
            ends_at=datetime(2026, 12, 10, 10, tzinfo=UTC),
        )
        path = f'/events/{event.pk}'
        limit_rule(settings, 'WT_EDIT_USER')
        response = patch(client, path, auth, {'starts_at': START})
        assert response.status_code == 400
        assert response.json()['detail'] == 'ends_at: Send both the start and the end.'
        assert patch(client, path, auth, {'title': ''}).status_code == 400
        for payload in ({}, {'title': None}):
            assert patch(client, path, auth, payload).status_code == 200
        assert patch(client, path, auth, {'title': 'y'}).status_code == 200
        assert patch(client, path, auth, {'title': 'z'}).status_code == 429
        # Deleting is never rate limited.
        assert delete(client, path, auth).status_code == 204

    def test_the_list_puts_what_is_ahead_first(self, client, auth, user, tab, now):
        past = make_event(user, 'Past', now - timedelta(days=3), now - timedelta(days=3, hours=-1))
        ahead = events.create_event(
            user,
            tab,
            title='Ahead',
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=1),
        )
        body = get(client, '/events', auth).json()
        assert [(row['id'], row['ended']) for row in body] == [
            (str(ahead.pk), False),
            (str(past.pk), True),
        ]

    def test_another_users_events_and_tabs(self, client, auth, other_user, now):
        theirs_tab = list_tabs(other_user)[0]
        theirs = events.create_event(
            other_user,
            theirs_tab,
            title='x',
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=1),
        )
        assert get(client, f'/events/{theirs.pk}', auth).status_code == 404
        assert patch(client, f'/events/{theirs.pk}', auth, {'title': 'y'}).status_code == 404
        assert delete(client, f'/events/{theirs.pk}', auth).status_code == 404
        payload = {'tab': str(theirs_tab.pk), 'title': 'x', 'starts_at': START, 'ends_at': END}
        response = post(client, '/events', auth, payload)
        assert response.status_code == 400
        assert response.json()['detail'] == 'tab: Choose one of your tabs.'
        assert Event.objects.count() == 1
        assert get(client, '/events', auth).json() == []
        assert Event.objects.get(pk=theirs.pk).title == 'x'

    def test_the_cap(self, client, auth, user, tab, now):
        from ..models import WorldTimeSettings

        config = WorldTimeSettings.load()
        config.max_events_per_user = 1
        config.save()
        payload = {'tab': str(tab.pk), 'title': 'x', 'starts_at': START, 'ends_at': END}
        assert post(client, '/events', auth, payload).status_code == 201
        response = post(client, '/events', auth, payload)
        assert response.status_code == 400
        assert response.json()['detail'] == 'You have too many events. Delete one first.'


class TestSettings:
    def test_the_caps_and_what_is_used(self, client, auth, user, tab, now):
        make_tab(user, 'Two')
        make_event(user, 'x', now + timedelta(days=1), now + timedelta(days=1, hours=1))
        assert get(client, '/settings', auth).json() == {
            'max_tabs': 10,
            'tabs': 2,
            'max_locations_per_tab': 20,
            'max_events': 50,
            'events': 1,
            'event_purge_days': 30,
            'cities_loaded': False,
        }

    def test_another_users_things_are_not_counted(self, client, auth, tab):
        other = make_user()
        list_tabs(other)
        make_tab(other, 'Theirs')
        assert get(client, '/settings', bearer(other)).json()['tabs'] == 2
        assert get(client, '/settings', auth).json()['tabs'] == 1
