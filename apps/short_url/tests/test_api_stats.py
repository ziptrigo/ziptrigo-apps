"""`GET /api/s/links/{id}/stats`: totals for every link, the detailed part once it is open, and
the same figures the stats page shows."""

from datetime import timedelta
from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone

from ..models import LinkStatus
from .api_support import auth, get, other_auth, post  # noqa: F401 -- fixtures used by name
from .conftest import balance
from .events import add_event, at_day
from .factories import create_link

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def today():
    return timezone.now().astimezone(dt_timezone.utc).date()


@pytest.fixture
def link(funded_user):
    return create_link(funded_user, code='Stat001', click_count=7, bot_click_count=3)


def stats(client, auth, link, **query):
    return get(client, f'/links/{link.pk}/stats', auth, **query)


class TestLockedStats:
    def test_totals_only_with_the_price_of_the_rest(self, client, auth, link):
        response = stats(client, auth, link)

        assert response.status_code == 200
        assert response.json() == {
            'range': '30',
            'totals': {'clicks': 7, 'bot_clicks': 3, 'uniques': 0, 'filter_bots': True},
            'analytics_enabled': False,
            'analytics_cost': 1,
            'detail': None,
        }

    def test_the_totals_count_unique_visitors(self, client, auth, link):
        for visitor in ('a', 'a', 'b'):
            add_event(link, at_day(today()), visitor=visitor)

        assert stats(client, auth, link).json()['totals']['uniques'] == 2

    def test_a_free_price_opens_it_without_a_charge(self, client, auth, link, prices, funded_user):
        prices.analytics_price = 0
        prices.save()

        body = stats(client, auth, link).json()

        assert body['analytics_enabled'] is True and body['detail'] is not None
        assert balance(funded_user) == 10
        link.refresh_from_db()
        assert link.analytics_paid_at is not None  # a later price rise keeps it open


class TestDetailedStats:
    @pytest.fixture
    def open_link(self, funded_user):
        link = create_link(funded_user, code='Stat002', analytics_paid_at=timezone.now())
        add_event(link, at_day(today()), visitor='a', referrer='news.example', country='US')
        add_event(link, at_day(today()), visitor='b', referrer='news.example', country='DE')
        add_event(link, at_day(today() - timedelta(days=2)), visitor='a', device='mobile')
        add_event(link, at_day(today()), visitor='c', bot=True)
        return link

    def test_the_series_and_the_top_lists(self, client, auth, open_link):
        response = stats(client, auth, open_link, range='7')

        assert response.status_code == 200
        body = response.json()
        assert body['analytics_enabled'] is True and body['analytics_cost'] == 0
        detail = body['detail']
        assert detail['end'] == today().isoformat()
        assert detail['start'] == (today() - timedelta(days=6)).isoformat()
        assert (detail['clicks'], detail['uniques'], detail['bot_clicks']) == (3, 3, 1)
        assert detail['bucket_days'] == 1
        assert [b['clicks'] for b in detail['buckets']] == [0, 0, 0, 0, 1, 0, 2]
        tables = {table['kind']: table for table in detail['tables']}
        assert set(tables) == {'referrer', 'country', 'device', 'browser'}
        assert tables['referrer']['rows'][0] == {
            'value': 'news.example',
            'label': 'news.example',
            'count': 2,
            'percent': 67,
        }
        assert {row['value'] for row in tables['country']['rows']} >= {'US', 'DE'}

    def test_it_agrees_with_the_stats_page(self, client, auth, web, open_link):
        from django.urls import reverse

        body = stats(client, auth, open_link, range='30').json()
        page = web.get(reverse('short_url:stats', args=[open_link.pk])).content.decode()

        assert str(body['detail']['clicks']) in page
        assert 'news.example' in page

    def test_including_bots_adds_them_to_the_figures(self, client, auth, open_link):
        from ..models import ShortLink

        ShortLink.objects.filter(pk=open_link.pk).update(filter_bots=False)

        detail = stats(client, auth, open_link, range='7').json()['detail']

        assert detail['clicks'] == 4

    @pytest.mark.parametrize('key', ['7', '30', '90', 'all'])
    def test_every_range(self, client, auth, open_link, key):
        body = stats(client, auth, open_link, range=key).json()

        assert body['range'] == key
        assert body['detail']['clicks'] == 3

    @pytest.mark.parametrize('key', ['1', 'week', '7;', ''])
    def test_an_unknown_range_is_a_400(self, client, auth, open_link, key):
        response = stats(client, auth, open_link, range=key)

        assert response.status_code == 400
        assert 'range must be one of' in response.json()['detail']

    def test_paying_for_the_analytics_opens_the_detail(self, client, auth, link, funded_user):
        add_event(link, at_day(today()))

        assert stats(client, auth, link).json()['detail'] is None
        post(client, f'/links/{link.pk}/analytics', auth)

        body = stats(client, auth, link).json()
        assert body['detail'] is not None and body['analytics_cost'] == 0
        assert balance(funded_user) == 9

    def test_a_taken_down_link_still_shows_its_numbers(self, client, auth, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN, click_count=4)

        assert stats(client, auth, link).json()['totals']['clicks'] == 4

    def test_another_users_and_a_deleted_link_are_404(self, client, auth, funded_user, other_user):
        foreign = create_link(other_user)
        gone = create_link(funded_user, status=LinkStatus.DELETED)

        assert stats(client, auth, foreign).status_code == 404
        assert stats(client, auth, gone).status_code == 404
