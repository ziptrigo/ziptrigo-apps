"""The World Time pages and endpoints."""

import json
import re
import uuid
from html import unescape

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from ..models import Location, Tab
from ..services import list_locations, list_tabs
from .factories import make_city, make_location, make_tab, make_user, write_city_file

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


def text(response) -> str:
    return unescape(response.content.decode())


def url(name: str, *args) -> str:
    return reverse(f'world_time:{name}', args=args)


@pytest.fixture
def tab(user) -> Tab:
    """The user's first tab: one row, Lisbon (the account's zone), the home."""
    return list_tabs(user)[0]


def payload(response) -> dict:
    match = re.search(r'<script type="application/json">(.*?)</script>', response.content.decode())
    assert match is not None
    return json.loads(match.group(1))


class TestPages:
    def test_login_is_required(self):
        response = Client().get(url('home'))
        assert response.status_code == 302 and '/account/login/' in response['Location']

    def test_a_first_visit_shows_the_home_row_and_the_search(self, web, user, freeze_now):
        freeze_now(2026, 11, 19, 15, 30)
        response = web.get(url('home'))
        assert response.status_code == 200
        body = text(response)
        assert '<title>My times - World Time - ZipTrigo</title>' in body
        assert 'Add a location' in body and 'Click an hour, or drag across the hours' in body
        assert 'City data from' in body and 'GeoNames' in body
        (tab,) = Tab.objects.filter(user=user)
        (home,) = list_locations(tab)
        assert f'data-sort-id="{home.pk}"' in body and '>Lisbon</p>' in body
        data = payload(response)
        assert data['hours'] == 24 and [row['title'] for row in data['rows']] == ['Lisbon']
        assert body.count('class="wt-tile ') == 24
        # No date in the links of today's page; the previous and next days are linked.
        assert f'{url("tab", tab.pk)}?date=2026-11-18' in body
        assert f'{url("tab", tab.pk)}?date=2026-11-20' in body
        assert '>Today</a>' not in body

    def test_the_time_is_in_the_accounts_format(self, web, user, freeze_now):
        freeze_now(2026, 11, 19, 15, 30)
        assert '>3:30 PM</span>' in text(web.get(url('home')))
        user.time_format, user.date_format = '24h', 'dmy'
        user.save()
        body = text(web.get(url('home')))
        assert '>15:30</span>' in body and 'Thu 19/11/2026' in body

    def test_another_day(self, web, tab, freeze_now):
        freeze_now(2026, 11, 19, 15, 30)
        body = text(web.get(url('tab', tab.pk), {'date': '2026-10-25'}))
        assert body.count('class="wt-tile ') == 25, 'the clocks go back in Lisbon that day'
        assert '>Today</a>' in body and 'name="date" value="2026-10-25"' in body
        assert 'value="2026-10-25"' in body
        assert web.get(url('tab', tab.pk), {'date': 'nonsense'}).status_code == 200

    def test_the_tabs_are_listed_and_the_current_one_marked(self, web, user, tab):
        other = make_tab(user, 'Family')
        body = text(web.get(url('tab', other.pk)))
        assert re.search(rf'href="{url("tab", other.pk)}" aria-current="page"', body)
        assert f'href="{url("tab", tab.pk)}"' in body
        assert 'No locations in this tab yet' in body
        assert 'Delete the tab Family' in body

    def test_a_lone_tab_has_no_delete_button(self, web, tab):
        assert 'data-title="Delete tab"' not in text(web.get(url('home')))

    def test_another_users_tab_is_a_404(self, web, other_user):
        theirs = make_tab(other_user)
        row = make_location(theirs)
        assert web.get(url('tab', theirs.pk)).status_code == 404
        assert web.get(url('tab', uuid.uuid4())).status_code == 404
        for name, args in (
            ('tab-rename', (theirs.pk,)),
            ('tab-delete', (theirs.pk,)),
            ('location-add', (theirs.pk,)),
            ('location-reorder', (theirs.pk,)),
            ('location-label', (theirs.pk, row.pk)),
            ('location-home', (theirs.pk, row.pk)),
            ('location-remove', (theirs.pk, row.pk)),
        ):
            assert web.post(url(name, *args), {'name': 'x', 'zone': 'UTC'}).status_code == 404, name
        assert Location.objects.filter(tab=theirs).count() == 1

    def test_a_location_of_another_tab_is_a_404(self, web, user, tab):
        row = make_location(make_tab(user, 'Other'))
        assert web.post(url('location-remove', tab.pk, row.pk)).status_code == 404

    def test_gets_are_refused_on_the_actions(self, web, tab):
        assert web.get(url('tab-create')).status_code == 405
        assert web.get(url('location-add', tab.pk)).status_code == 405

    def test_the_page_costs_few_queries_whatever_the_rows(
        self, web, tab, django_assert_max_num_queries
    ):
        for zone in ('Asia/Tokyo', 'America/New_York', 'Asia/Kolkata', 'UTC', 'Europe/Paris'):
            make_location(tab, zone.rsplit('/', 1)[-1], zone)
        with django_assert_max_num_queries(12):
            assert web.get(url('tab', tab.pk)).status_code == 200

    def test_a_hostile_name_is_escaped(self, web, tab):
        make_location(tab, 'Tokyo', 'Asia/Tokyo', label='<script>alert(1)</script>')
        body = web.get(url('tab', tab.pk)).content.decode()
        assert '<script>alert(1)</script>' not in body
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in body
        assert '\\u003Cscript\\u003E' in body, 'escaped inside the JSON too'


class TestTabs:
    def test_create(self, web, user):
        response = web.post(url('tab-create'), {'name': 'Team'}, **HTMX)
        tab = Tab.objects.get(user=user, name='Team')
        assert response.status_code == 200 and response['HX-Redirect'] == url('tab', tab.pk)
        assert web.post(url('tab-create'), {'name': 'Family'}).status_code == 302

    def test_a_refusal_is_a_422_in_the_tab_slot(self, web, tab):
        response = web.post(url('tab-create'), {'name': ''}, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#wt-tab-msg'
        assert 'Name: Give the tab a name.' in text(response)
        response = web.post(url('tab-create'), {'name': 'my TIMES'}, **HTMX)
        assert response.status_code == 422
        assert 'Name: You already have a tab with this name.' in text(response)

    def test_the_cap(self, web, tab, config, user):
        make_tab(user, 'Two')
        make_tab(user, 'Three')
        response = web.post(url('tab-create'), {'name': 'Four'}, **HTMX)
        assert response.status_code == 422
        assert 'You have too many tabs. Delete one first.' in text(response)

    def test_rename_keeps_the_day(self, web, tab):
        response = web.post(
            url('tab-rename', tab.pk), {'name': 'Work', 'date': '2026-12-01'}, **HTMX
        )
        assert response['HX-Redirect'] == f'{url("tab", tab.pk)}?date=2026-12-01'
        tab.refresh_from_db()
        assert tab.name == 'Work'

    def test_delete(self, web, user, tab):
        other = make_tab(user, 'Family')
        response = web.post(url('tab-delete', other.pk))
        assert response.status_code == 302 and response['Location'] == url('home')
        assert [str(m) for m in get_messages(response.wsgi_request)] == ['Deleted the tab Family.']
        assert not Tab.objects.filter(pk=other.pk).exists()

    def test_the_last_tab_stays(self, web, tab):
        response = web.post(url('tab-delete', tab.pk))
        assert response.status_code == 302
        assert [str(m) for m in get_messages(response.wsgi_request)] == ['Keep at least one tab.']
        assert Tab.objects.filter(pk=tab.pk).exists()


class TestLocations:
    def test_add_a_city_answers_the_grid(self, web, tab):
        make_city(5, 'Austin', 'America/Chicago', region='Texas', country='United States')
        response = web.post(url('location-add', tab.pk), {'city': '5', 'q': 'aus'}, **HTMX)
        assert response.status_code == 200
        assert [row['title'] for row in payload(response)['rows']] == ['Lisbon', 'Austin']
        body = text(response)
        assert 'Austin, Texas, United States' in body
        assert 'id="wt-search-results" hx-swap-oob="innerHTML"' in body
        assert 'id="wt-msg" hx-swap-oob="true"' in body

    def test_add_a_zone_without_htmx_redirects_to_the_day(self, web, tab):
        response = web.post(url('location-add', tab.pk), {'zone': 'UTC', 'date': '2026-12-01'})
        assert response.status_code == 302
        assert response['Location'] == f'{url("tab", tab.pk)}?date=2026-12-01'
        assert [row.name for row in list_locations(tab)] == ['Lisbon', 'UTC']

    @pytest.mark.parametrize(
        ('data', 'expected'),
        [
            ({}, 'Choose a place from the suggestions.'),
            ({'city': '999'}, 'Place: Choose a place from the suggestions.'),
            ({'zone': 'Mars/Olympus'}, 'Time zone: Unknown time zone.'),
            ({'zone': 'Europe/Lisbon'}, 'This location is already in the tab.'),
        ],
    )
    def test_a_refusal_is_a_422_under_the_search(self, web, tab, data, expected):
        response = web.post(url('location-add', tab.pk), data, **HTMX)
        assert response.status_code == 422 and response['HX-Retarget'] == '#wt-search-msg'
        assert expected in text(response)
        assert Location.objects.filter(tab=tab).count() == 1

    def test_a_full_tab(self, web, tab, config):
        for zone in ('UTC', 'Asia/Tokyo', 'Asia/Kolkata'):
            make_location(tab, zone, zone)
        response = web.post(url('location-add', tab.pk), {'zone': 'Europe/Paris'}, **HTMX)
        assert response.status_code == 422
        assert 'This tab is full. Remove a location first.' in text(response)

    def test_label(self, web, tab):
        row = list_locations(tab)[0]
        response = web.post(url('location-label', tab.pk, row.pk), {'label': 'Head office'}, **HTMX)
        assert response.status_code == 200 and '>Head office</p>' in text(response)
        response = web.post(url('location-label', tab.pk, row.pk), {'label': 'x' * 61}, **HTMX)
        assert response.status_code == 422
        assert 'Label: Keep the label to 60 characters or fewer.' in text(response)
        web.post(url('location-label', tab.pk, row.pk), {'label': ''}, **HTMX)
        row.refresh_from_db()
        assert row.label == ''

    def test_home(self, web, tab):
        tokyo = make_location(tab, 'Tokyo', 'Asia/Tokyo')
        response = web.post(url('location-home', tab.pk, tokyo.pk), **HTMX)
        rows = payload(response)['rows']
        assert [(row['title'], row['home']) for row in rows] == [('Lisbon', False), ('Tokyo', True)]
        assert 'Make home' in text(response)

    def test_remove(self, web, tab):
        tokyo = make_location(tab, 'Tokyo', 'Asia/Tokyo')
        response = web.post(f'{url("location-remove", tab.pk, tokyo.pk)}?date=2026-12-01')
        assert response.status_code == 302
        assert response['Location'] == f'{url("tab", tab.pk)}?date=2026-12-01'
        assert [row.name for row in list_locations(tab)] == ['Lisbon']

    def test_reorder(self, web, tab):
        tokyo = make_location(tab, 'Tokyo', 'Asia/Tokyo')
        lisbon = list_locations(tab)[0]
        response = web.post(
            url('location-reorder', tab.pk), {'ids[]': [str(tokyo.pk), str(lisbon.pk)]}, **HTMX
        )
        assert response.status_code == 204
        assert [row.name for row in list_locations(tab)] == ['Tokyo', 'Lisbon']

    @pytest.mark.parametrize('ids', [[], ['nope'], [str(uuid.uuid4())] * 201])
    def test_an_unreadable_order_is_refused(self, web, tab, ids):
        response = web.post(url('location-reorder', tab.pk), {'ids[]': ids}, **HTMX)
        assert response.status_code == 422 and 'Could not read the new order.' in text(response)


class TestSearch:
    def test_the_suggestions_are_submit_buttons(self, web, tab):
        make_city(1, 'Lisbon', population=517802, other_names=['Lisboa'])
        response = web.get(url('search'), {'q': 'lisboa', 'tab': str(tab.pk)}, **HTMX)
        body = text(response)
        assert response.status_code == 200
        assert 'type="submit" name="city" value="1"' in body and '(also Lisboa)' in body
        response = web.get(url('search'), {'q': 'pst', 'tab': str(tab.pk)}, **HTMX)
        body = text(response)
        assert 'name="zone" value="America/Los_Angeles"' in body
        assert 'Pacific Time (PST)' in body

    def test_nothing_found_says_so_and_mentions_an_unseeded_list(self, web, tab, tmp_path):
        response = web.get(url('search'), {'q': 'zzzz', 'tab': str(tab.pk)}, **HTMX)
        body = text(response)
        assert 'No place matches "zzzz".' in body
        assert 'The city list has not been loaded yet' in body
        from ..services import seed

        seed(
            write_city_file(
                tmp_path / 'c.tsv.gz',
                [(1, 'Faro', 'PT', 'Portugal', '', 'Europe/Lisbon', 41000, [])],
            )
        )
        body = text(web.get(url('search'), {'q': 'zzzz', 'tab': str(tab.pk)}, **HTMX))
        assert 'No place matches "zzzz".' in body and 'has not been loaded' not in body

    def test_a_short_query_shows_nothing(self, web, tab):
        response = web.get(url('search'), {'q': 'z', 'tab': str(tab.pk)}, **HTMX)
        assert response.status_code == 200 and text(response).strip() == ''

    def test_a_query_is_escaped(self, web, tab):
        response = web.get(url('search'), {'q': '<b>zz</b>', 'tab': str(tab.pk)}, **HTMX)
        assert '<b>zz</b>' not in response.content.decode()

    def test_it_needs_the_users_own_tab(self, web, other_user):
        theirs = make_tab(other_user)
        assert (
            web.get(url('search'), {'q': 'lis', 'tab': str(theirs.pk)}, **HTMX).status_code == 404
        )
        assert web.get(url('search'), {'q': 'lis', 'tab': 'nope'}, **HTMX).status_code == 404

    def test_opened_directly_it_goes_to_the_app(self, web, tab):
        response = web.get(url('search'), {'q': 'lis', 'tab': str(tab.pk)})
        assert response.status_code == 302 and response['Location'] == url('home')

    def test_login_is_required(self, tab):
        response = Client().get(url('search'), {'q': 'lis', 'tab': str(tab.pk)}, **HTMX)
        assert '/account/login/' in response['HX-Redirect']


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _on(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'WT_EDIT_USER': (2, 60),
            'WT_SEARCH_USER': (1, 60),
        }

    def test_changes_are_limited_but_a_refused_form_spends_nothing(self, web, tab):
        for _ in range(3):
            assert web.post(url('tab-create'), {'name': ''}, **HTMX).status_code == 422
        assert web.post(url('tab-create'), {'name': 'One'}, **HTMX).status_code == 200
        assert web.post(url('location-add', tab.pk), {'zone': 'UTC'}, **HTMX).status_code == 200
        response = web.post(url('location-add', tab.pk), {'zone': 'Asia/Tokyo'}, **HTMX)
        assert response.status_code == 429
        assert Location.objects.filter(tab=tab).count() == 2

    def test_search_over_the_limit_keeps_the_last_suggestions(self, web, tab):
        assert web.get(url('search'), {'q': 'lis', 'tab': str(tab.pk)}, **HTMX).status_code == 200
        assert web.get(url('search'), {'q': 'lisb', 'tab': str(tab.pk)}, **HTMX).status_code == 204

    def test_one_users_budget_is_not_anothers(self, web, tab):
        web.get(url('search'), {'q': 'lis', 'tab': str(tab.pk)}, **HTMX)
        other = Client()
        stranger = make_user()
        other.force_login(stranger)
        theirs = list_tabs(stranger)[0]
        assert (
            other.get(url('search'), {'q': 'lis', 'tab': str(theirs.pk)}, **HTMX).status_code == 200
        )
