"""Shared events: the service's rules, the owner's pages, the public page and the purge."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.datetime_format import PUBLIC, DisplayFormats
from apps.core.scheduler import get_jobs

from ..models import Event, Tab, WorldTimeSettings
from ..services import events, list_tabs
from .factories import make_location, make_tab, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
MDY_12 = DisplayFormats('mdy', '12h')
START = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
END = datetime(2026, 12, 10, 10, 30, tzinfo=UTC)


def text(response) -> str:
    return unescape(response.content.decode())


def url(name: str, *args) -> str:
    return reverse(f'world_time:{name}', args=args)


def message(excinfo) -> str:
    return '; '.join(excinfo.value.messages)


@pytest.fixture
def now(freeze_now):
    return freeze_now(2026, 12, 1, 12, 0)


@pytest.fixture
def web(user, now):
    """A client signed in as `user` *after* the clock was frozen: a session made at the real time
    would have expired by the frozen one."""
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def tab(user) -> Tab:
    tab = make_tab(user, 'Team')
    make_location(tab, 'Lisbon', 'Europe/Lisbon')
    make_location(
        tab, 'Tokyo', 'Asia/Tokyo', label='Tokyo office', geoname_id=1850147, country='Japan'
    )
    return tab


@pytest.fixture
def event(user, tab, now) -> Event:
    return events.create_event(user, tab, title='Team call', starts_at=START, ends_at=END)


class TestService:
    def test_create_snapshots_the_tab(self, user, tab, now):
        event = events.create_event(user, tab, title='  Team   call ', starts_at=START, ends_at=END)
        assert event.title == 'Team call' and (event.starts_at, event.ends_at) == (START, END)
        assert re.fullmatch(r'[A-Za-z0-9_-]{22}', event.code)
        assert event.locations == [
            {
                'name': 'Lisbon',
                'detail': 'Europe/Lisbon',
                'timezone': 'Europe/Lisbon',
                'home': True,
            },
            {
                'name': 'Tokyo office',
                'detail': 'Tokyo, Japan',
                'timezone': 'Asia/Tokyo',
                'home': False,
            },
        ]

    def test_a_later_change_to_the_tab_does_not_change_the_event(self, user, tab, event):
        tab.delete()
        event.refresh_from_db()
        assert [row['name'] for row in event.locations] == ['Lisbon', 'Tokyo office']

    def test_codes_differ(self, user, tab, now):
        codes = {
            events.create_event(user, tab, title='x', starts_at=START, ends_at=END).code
            for _ in range(5)
        }
        assert len(codes) == 5

    @pytest.mark.parametrize(
        ('fields', 'expected'),
        [
            ({'title': ''}, 'Give the event a title.'),
            ({'title': 'x' * 121}, 'Keep the title to 120 characters or fewer.'),
            ({'ends_at': START}, 'The end must be after the start.'),
            ({'ends_at': START - timedelta(hours=1)}, 'The end must be after the start.'),
            (
                {'ends_at': START + timedelta(days=7, minutes=1)},
                'An event can last at most 7 days.',
            ),
            ({'starts_at': START.replace(tzinfo=None)}, 'Choose when the event starts.'),
            ({'ends_at': None}, 'Choose when the event ends.'),
            (
                {'starts_at': START.replace(year=2040), 'ends_at': END.replace(year=2040)},
                'Choose a date within ten years of today.',
            ),
        ],
    )
    def test_refusals(self, user, tab, now, fields, expected):
        values = {'title': 'Team call', 'starts_at': START, 'ends_at': END, **fields}
        with pytest.raises(ValidationError) as excinfo:
            events.create_event(user, tab, **values)
        assert expected in message(excinfo)
        assert not Event.objects.exists()

    def test_seven_days_exactly_is_allowed(self, user, tab, now):
        events.create_event(
            user, tab, title='Week', starts_at=START, ends_at=START + timedelta(days=7)
        )

    def test_an_empty_tab_has_nothing_to_share(self, user, now):
        with pytest.raises(ValidationError) as excinfo:
            events.create_event(
                user, make_tab(user, 'Empty'), title='x', starts_at=START, ends_at=END
            )
        assert 'Add a location to the tab first.' in message(excinfo)

    def test_the_cap(self, user, tab, now):
        config = WorldTimeSettings.load()
        config.max_events_per_user = 2
        config.save()
        for title in ('One', 'Two'):
            events.create_event(user, tab, title=title, starts_at=START, ends_at=END)
        with pytest.raises(ValidationError) as excinfo:
            events.create_event(user, tab, title='Three', starts_at=START, ends_at=END)
        assert 'You have too many events. Delete one first.' in message(excinfo)

    def test_update_keeps_the_link_and_the_locations(self, event):
        code, rows = event.code, event.locations
        events.update_event(event, title='Planning')
        events.update_event(
            event, starts_at=START + timedelta(hours=2), ends_at=END + timedelta(hours=2)
        )
        event.refresh_from_db()
        assert (event.title, event.code, event.locations) == ('Planning', code, rows)
        assert event.starts_at == START + timedelta(hours=2)

    def test_update_refusals(self, event):
        with pytest.raises(ValidationError) as excinfo:
            events.update_event(event, starts_at=START)
        assert 'Send both the start and the end.' in message(excinfo)
        with pytest.raises(ValidationError):
            events.update_event(event, title='')
        Event.objects.filter(pk=event.pk).delete()
        with pytest.raises(ValidationError) as excinfo:
            events.update_event(event, title='Gone')
        assert 'This event no longer exists.' in message(excinfo)

    def test_delete_is_idempotent(self, event):
        events.delete_event(event)
        events.delete_event(event)
        assert events.get_available(event.code) is None

    def test_get_available(self, event):
        assert events.get_available(event.code) == event
        assert events.get_available(event.code.swapcase()) in (None, event)  # case-sensitive
        assert events.get_available('nope') is None and events.get_available('') is None
        assert events.get_available('x' * 500) is None

    def test_list_puts_what_is_ahead_first(self, user, tab, now):
        past = events.create_event(
            user,
            tab,
            title='Past',
            starts_at=now - timedelta(days=3),
            ends_at=now - timedelta(days=3) + timedelta(hours=1),
        )
        older = events.create_event(
            user,
            tab,
            title='Older',
            starts_at=now - timedelta(days=9),
            ends_at=now - timedelta(days=9) + timedelta(hours=1),
        )
        later = events.create_event(user, tab, title='Later', starts_at=START, ends_at=END)
        soon = events.create_event(
            user,
            tab,
            title='Soon',
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=2),
        )
        listed = events.list_events(user)
        assert [e.pk for e in listed] == [soon.pk, later.pk, past.pk, older.pk]
        assert [e.ended for e in listed] == [False, False, True, True]

    def test_the_address_comes_from_base_url(self, event, settings):
        settings.BASE_URL = 'https://app.example.com/'
        assert events.event_url(event) == f'https://app.example.com/w/{event.code}'
        assert events.qr_data_uri(events.event_url(event)).startswith('data:image/png;base64,')

    def test_rows_in_each_location(self, event):
        lisbon, tokyo = events.event_rows(event, MDY_12)
        assert (lisbon.name, lisbon.home, lisbon.text) == (
            'Lisbon',
            True,
            'Thu 12/10/2026, 9:00 AM – 10:30 AM',
        )
        assert lisbon.zone_label == 'WET · UTC+0'
        assert (tokyo.name, tokyo.detail, tokyo.text) == (
            'Tokyo office',
            'Tokyo, Japan',
            'Thu 12/10/2026, 6:00 PM – 7:30 PM',
        )
        assert events.event_rows(event, PUBLIC)[0].text == 'Thu Dec 10, 2026, 9:00 AM – 10:30 AM'
        assert events.home_range_text(event, MDY_12) == lisbon.text
        assert (events.home_name(event), events.home_zone_name(event)) == (
            'Lisbon',
            'Europe/Lisbon',
        )

    def test_a_range_across_midnight_shows_both_dates(self, event):
        events.update_event(
            event, starts_at=START + timedelta(hours=6), ends_at=END + timedelta(hours=6)
        )
        tokyo = events.event_rows(event, MDY_12)[1]
        assert tokyo.text == 'Fri 12/11/2026, 12:00 AM – 1:30 AM'
        events.update_event(
            event, starts_at=START + timedelta(hours=5), ends_at=END + timedelta(hours=5)
        )
        tokyo = events.event_rows(event, MDY_12)[1]
        assert tokyo.text == 'Thu 12/10/2026, 11:00 PM – Fri 12/11/2026, 12:30 AM'

    def test_a_damaged_snapshot_is_tolerated(self, event):
        event.locations = ['junk', {'name': 'No zone'}, {'timezone': 'Mars/Olympus'}, None]
        (row,) = events.event_rows(event, MDY_12)
        assert (row.name, row.zone_label) == ('Olympus', 'UTC · UTC+0')
        event.locations = 'junk'
        assert events.event_rows(event) == [] and events.home_zone_name(event) == 'UTC'

    def test_duration_and_status(self, event, now):
        assert events.duration_text(event) == '1 h 30 min'
        assert (
            events.status_text(event, START - timedelta(days=2, hours=3)) == 'Starts in 2 days 3 h'
        )
        assert events.status_text(event, START - timedelta(minutes=200)) == 'Starts in 3 h 20 min'
        assert events.status_text(event, START - timedelta(seconds=20)) == 'Starts in 1 min'
        assert events.status_text(event, START) == 'Happening now: ends in 1 h 30 min'
        assert events.status_text(event, END) == 'Ended'
        events.update_event(event, starts_at=START, ends_at=START + timedelta(days=2))
        assert events.duration_text(event) == '2 days'

    def test_local_range(self):
        from datetime import date, time

        starts, ends = events.local_range('Asia/Tokyo', date(2026, 12, 10), time(18), time(19, 30))
        assert (starts, ends) == (START, END)
        starts, ends = events.local_range('Asia/Tokyo', date(2026, 12, 10), time(23), time(1))
        assert ends - starts == timedelta(hours=2)


class TestPurge:
    def test_events_go_some_days_after_they_end(self, user, tab, now):
        def make(title, ended_days_ago):
            end = now - timedelta(days=ended_days_ago)
            return events.create_event(
                user, tab, title=title, starts_at=end - timedelta(hours=1), ends_at=end
            )

        old, edge, recent = make('Old', 31), make('Edge', 30), make('Recent', 29)
        ahead = events.create_event(user, tab, title='Ahead', starts_at=START, ends_at=END)
        assert events.purge_ended(now + timedelta(seconds=1)) == 2
        assert set(Event.objects.values_list('pk', flat=True)) == {recent.pk, ahead.pk}
        assert events.purge_ended(now) == 0
        assert events.get_available(old.code) is None and events.get_available(edge.code) is None

    def test_the_period_is_the_admins(self, user, tab, now):
        end = now - timedelta(days=3)
        events.create_event(user, tab, title='x', starts_at=end - timedelta(hours=1), ends_at=end)
        config = WorldTimeSettings.load()
        config.event_purge_days = 2
        config.save()
        assert events.purge_ended(now) == 1

    def test_the_job_is_registered_daily_with_a_description(self):
        from django.apps import apps as django_apps

        from .. import jobs

        django_apps.get_app_config('world_time')._register_jobs()
        spec = {job.name: job for job in get_jobs()}['world_time.purge_events']
        assert spec.interval == timedelta(hours=24) and spec.description.strip()
        jobs.purge_events()

    def test_deleting_the_user_takes_their_events(self, event):
        event.user.delete()
        assert not Event.objects.exists()


class TestOwnerPages:
    def test_login_is_required(self, event):
        for target in (url('events'), url('event', event.pk)):
            response = Client().get(target)
            assert response.status_code == 302 and '/account/login/' in response['Location']

    def test_the_grid_offers_to_share_and_links_to_the_events(self, web, tab):
        body = text(web.get(url('tab', tab.pk)))
        assert f'action="{url("event-create", tab.pk)}"' in body
        assert 'Share as an event' in body and f'href="{url("events")}"' in body

    def test_create_from_a_selection(self, web, user, tab, now):
        response = web.post(
            url('event-create', tab.pk),
            {
                'title': 'Team call',
                'start': '2026-12-10T09:00:00.000Z',
                'end': '2026-12-10T10:30:00.000Z',
            },
            **HTMX,
        )
        event = Event.objects.get(user=user)
        assert response.status_code == 200 and response['HX-Redirect'] == url('event', event.pk)
        assert (event.starts_at, event.ends_at) == (START, END)
        assert [str(m) for m in get_messages(response.wsgi_request)] == [
            'Event created. Anyone with its link can see it.'
        ]

    @pytest.mark.parametrize(
        ('data', 'expected'),
        [
            (
                {'title': '', 'start': '2026-12-10T09:00Z', 'end': '2026-12-10T10:00Z'},
                'Title: Give the event a title.',
            ),
            ({'title': 'x'}, 'Start: Select a time range first.'),
            ({'title': 'x', 'start': 'soon', 'end': '2026-12-10T10:00Z'}, 'Start: '),
            (
                {'title': 'x', 'start': '2026-12-10T10:00Z', 'end': '2026-12-10T09:00Z'},
                'End: The end must be after the start.',
            ),
        ],
    )
    def test_a_refused_share_is_a_422_under_the_form(self, web, tab, now, data, expected):
        response = web.post(url('event-create', tab.pk), data, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#wt-event-msg'
        assert expected in text(response) and not Event.objects.exists()

    def test_the_list(self, web, event, user, tab, now):
        events.create_event(
            user,
            tab,
            title='<b>Old</b>',
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=2) + timedelta(hours=1),
        )
        response = web.get(url('events'))
        body = text(response)
        assert '<title>Events - World Time - ZipTrigo</title>' in body
        assert body.index('Team call') < body.index('<b>Old</b>'), 'what is ahead comes first'
        assert '<b>Old</b>' not in response.content.decode(), 'a title is escaped'
        assert 'Thu 12/10/2026, 9:00 AM – 10:30 AM' in body and '(Lisbon time)' in body
        assert f'/w/{event.code}' in body and 'Ended' in body
        assert 'An event is deleted 30 days after it ends' in body

    def test_an_empty_list(self, web):
        assert 'No events yet' in text(web.get(url('events')))

    def test_the_event_page(self, web, event):
        body = text(web.get(url('event', event.pk)))
        assert '<title>Team call - Events - World Time - ZipTrigo</title>' in body
        assert events.event_url(event) in body and 'data:image/png;base64,' in body
        assert 'Tokyo office' in body and 'Thu 12/10/2026, 6:00 PM – 7:30 PM' in body
        assert 'Starts in 8 days 21 h' in body and '1 h 30 min' in body
        assert 'name="date" value="2026-12-10"' in body
        assert 'name="start_time" value="09:00"' in body and 'name="end_time" value="10:30"' in body
        assert "On Lisbon's clock." in body
        assert 'Anyone who has this link can see the title and the time' in body

    def test_the_times_follow_the_accounts_formats(self, web, event, user):
        user.date_format, user.time_format = 'dmy', '24h'
        user.save()
        assert 'Thu 10/12/2026, 18:00 – 19:30' in text(web.get(url('event', event.pk)))

    def test_edit_on_the_home_clock(self, web, event):
        response = web.post(
            url('event-edit', event.pk),
            {'title': 'Planning', 'date': '2026-12-11', 'start_time': '23:00', 'end_time': '00:30'},
            **HTMX,
        )
        assert response['HX-Redirect'] == url('event', event.pk)
        event.refresh_from_db()
        assert event.title == 'Planning'
        assert event.starts_at == datetime(2026, 12, 11, 23, 0, tzinfo=UTC)
        assert event.ends_at == datetime(2026, 12, 12, 0, 30, tzinfo=UTC), 'the next day'

    def test_the_home_clock_is_the_snapshots_home_zone(self, web, user, now):
        tab = make_tab(user, 'Asia')
        make_location(tab, 'Tokyo', 'Asia/Tokyo')
        event = events.create_event(user, tab, title='x', starts_at=START, ends_at=END)
        assert 'name="start_time" value="18:00"' in text(web.get(url('event', event.pk)))
        web.post(
            url('event-edit', event.pk),
            {'title': 'x', 'date': '2026-12-10', 'start_time': '19:00', 'end_time': '20:00'},
        )
        event.refresh_from_db()
        assert event.starts_at == START + timedelta(hours=1)

    @pytest.mark.parametrize(
        ('data', 'expected'),
        [
            (
                {'title': '', 'date': '2026-12-10', 'start_time': '09:00', 'end_time': '10:00'},
                'Title: Give the event a title.',
            ),
            (
                {'title': 'x', 'date': '', 'start_time': '09:00', 'end_time': '10:00'},
                'Date: Choose the date.',
            ),
            (
                {'title': 'x', 'date': '2026-12-10', 'start_time': '', 'end_time': '10:00'},
                'Start: Choose when it starts.',
            ),
            (
                {'title': 'x', 'date': '2040-12-10', 'start_time': '09:00', 'end_time': '10:00'},
                'Start: Choose a date within ten years of today.',
            ),
        ],
    )
    def test_a_refused_edit(self, web, event, data, expected):
        response = web.post(url('event-edit', event.pk), data, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#wt-event-edit-msg'
        assert expected in text(response)
        event.refresh_from_db()
        assert event.title == 'Team call' and event.starts_at == START

    def test_delete(self, web, event):
        response = web.post(url('event-delete', event.pk))
        assert response.status_code == 302 and response['Location'] == url('events')
        assert [str(m) for m in get_messages(response.wsgi_request)] == [
            'Deleted the event Team call. Its link no longer works.'
        ]
        assert not Event.objects.exists()

    def test_another_users_event_is_a_404(self, web, other_user, now):
        tab = list_tabs(other_user)[0]
        theirs = events.create_event(other_user, tab, title='x', starts_at=START, ends_at=END)
        assert web.get(url('event', theirs.pk)).status_code == 404
        assert web.post(url('event-edit', theirs.pk), {'title': 'y'}).status_code == 404
        assert web.post(url('event-delete', theirs.pk)).status_code == 404
        assert web.post(url('event-create', tab.pk), {'title': 'y'}).status_code == 404
        assert web.get(url('event', uuid.uuid4())).status_code == 404
        assert Event.objects.get(pk=theirs.pk).title == 'x'

    def test_rate_limit_after_validation(self, web, tab, now, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'WT_EDIT_USER': (1, 60)}
        good = {'title': 'x', 'start': '2026-12-10T09:00Z', 'end': '2026-12-10T10:00Z'}
        assert web.post(url('event-create', tab.pk), {'title': ''}, **HTMX).status_code == 422
        assert web.post(url('event-create', tab.pk), good, **HTMX).status_code == 200
        assert web.post(url('event-create', tab.pk), good, **HTMX).status_code == 429
        assert Event.objects.count() == 1


class TestPublicPage:
    def test_anyone_with_the_link_sees_it_in_the_public_wording(self, event):
        response = Client().get(f'/w/{event.code}')
        body = text(response)
        assert response.status_code == 200
        assert '<title>Team call - ZipTrigo</title>' in body
        assert 'Thu Dec 10, 2026, 9:00 AM – 10:30 AM' in body
        assert 'Tokyo office' in body and 'Thu Dec 10, 2026, 6:00 PM – 7:30 PM' in body
        assert 'Starts in 8 days 21 h' in body and '1 h 30 min' in body
        assert 'datetime="2026-12-10T09:00:00+00:00"' in body, (
            "the visitor's own zone, by the browser"
        )
        assert 'data-start="2026-12-10T09:00:00+00:00"' in body
        assert 'data:image/png;base64,' in body and events.event_url(event) in body
        assert '<meta name="robots" content="noindex">' in body
        assert event.user.email not in body and event.user.name not in body
        assert Client().get(f'/w/{event.code}/').status_code == 200

    def test_the_headers_keep_it_private(self, event):
        response = Client().get(f'/w/{event.code}')
        assert response['Cache-Control'] == 'no-store, private'
        assert response['Referrer-Policy'] == 'strict-origin'
        assert response['X-Robots-Tag'] == 'noindex, nofollow'

    def test_a_signed_in_owner_sees_the_public_wording_too(self, web, event, user):
        user.date_format, user.time_format = 'dmy', '24h'
        user.save()
        assert 'Thu Dec 10, 2026, 9:00 AM – 10:30 AM' in text(web.get(f'/w/{event.code}'))

    def test_a_hostile_title_is_escaped(self, user, tab, now):
        event = events.create_event(
            user, tab, title='<script>alert(1)</script>', starts_at=START, ends_at=END
        )
        body = Client().get(f'/w/{event.code}').content.decode()
        assert '<script>alert(1)</script>' not in body
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in body

    def test_every_dead_link_is_the_same_neutral_page(self, event, user, tab, now):
        deleted = events.create_event(user, tab, title='Gone', starts_at=START, ends_at=END)
        events.delete_event(deleted)
        answers = [
            Client().get(target)
            for target in (f'/w/{deleted.code}', '/w/made-up-code', f'/w/{"x" * 300}')
        ]
        assert {response.status_code for response in answers} == {404}
        assert len({response.content for response in answers}) == 1
        body = answers[0].content.decode()
        assert "This event isn't available" in body and 'Gone' not in body
        assert answers[0]['Cache-Control'] == 'no-store, private'

    def test_an_edit_shows_at_the_same_address(self, event):
        events.update_event(event, title='Planning')
        assert 'Planning' in text(Client().get(f'/w/{event.code}'))

    def test_only_get(self, event):
        assert Client().post(f'/w/{event.code}').status_code == 405

    def test_it_needs_no_session_and_few_queries(self, event, django_assert_max_num_queries):
        with django_assert_max_num_queries(2):
            assert Client().get(f'/w/{event.code}').status_code == 200
        with django_assert_max_num_queries(1):
            Client().get('/w/made-up-code')

    def test_the_terms_gate_leaves_it_alone(self, settings):
        assert '/w/' in settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES

    def test_the_rate_limit_counts_made_up_codes(self, event, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'WT_VIEW_IP': (2, 60)}
        client = Client()
        assert client.get('/w/made-up-code').status_code == 404
        assert client.get(f'/w/{event.code}').status_code == 200
        response = client.get(f'/w/{event.code}')
        assert response.status_code == 429 and response['Cache-Control'] == 'no-store, private'


class TestAdmin:
    @pytest.fixture
    def admin_client(self, now):
        admin = User.objects.create_superuser(email='admin@example.com', password=None)
        client = Client()
        client.force_login(admin)
        return client

    def test_the_code_is_never_shown_in_full_or_searchable(self, admin_client, event):
        body = admin_client.get('/admin/world_time/event/').content.decode()
        assert 'Team call' in body and event.code not in body and f'{event.code[:6]}...' in body
        body = admin_client.get(f'/admin/world_time/event/{event.pk}/change/').content.decode()
        assert event.code not in body and 'Tokyo office' in body
        response = admin_client.get(f'/admin/world_time/event/?code__startswith={event.code[:3]}')
        assert response.status_code in (302, 400)
        assert admin_client.get('/admin/world_time/event/add/').status_code == 403

    def test_the_settings_have_the_event_caps(self):
        config = WorldTimeSettings.load()
        assert (config.max_events_per_user, config.event_purge_days) == (50, 30)

    def test_a_user_with_events_can_be_deleted_from_the_admin(self, admin_client, now):
        user = make_user()
        tab = list_tabs(user)[0]
        events.create_event(user, tab, title='x', starts_at=START, ends_at=END)
        response = admin_client.post(f'/admin/accounts/user/{user.pk}/delete/', {'post': 'yes'})
        assert response.status_code == 302 and not Event.objects.exists()
