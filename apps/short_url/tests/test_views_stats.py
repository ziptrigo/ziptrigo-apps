from datetime import timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import CreditTransaction

from ..models import LinkStatus, ShortLink
from ..services import billing
from .conftest import balance
from .events import add_event, at_day
from .factories import create_link

pytestmark = pytest.mark.django_db


def today():
    """The real UTC day: the views read the clock, so these tests can't use a fixed one."""
    return timezone.now().astimezone(dt_timezone.utc).date()


def stats_url(link, **query):
    url = reverse('short_url:stats', args=[link.pk])
    return f'{url}?{"&".join(f"{k}={v}" for k, v in query.items())}' if query else url


def enable_url(link):
    return reverse('short_url:enable-analytics', args=[link.pk])


def flashes(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.fixture
def link(funded_user):
    return create_link(funded_user, code='Stat001', click_count=7, bot_click_count=3)


@pytest.fixture
def paid_link(funded_user):
    return create_link(funded_user, code='Stat002', analytics_paid_at=timezone.now())


class TestAccess:
    def test_login_is_required(self, client, link):
        assert client.get(stats_url(link)).status_code == 302
        assert client.post(enable_url(link)).status_code == 302

    def test_other_users_and_deleted_links_are_404(self, web, other_user, funded_user):
        foreign = create_link(other_user)
        assert web.get(stats_url(foreign)).status_code == 404
        assert web.post(enable_url(foreign)).status_code == 404
        gone = create_link(funded_user, status=LinkStatus.DELETED)
        assert web.get(stats_url(gone)).status_code == 404

    def test_only_get_and_post_where_they_belong(self, web, link):
        assert web.post(stats_url(link)).status_code == 405
        assert web.get(enable_url(link)).status_code == 405


class TestPaywall:
    def test_the_totals_are_always_shown(self, web, link):
        add_event(link, at_day(today()), visitor='a')
        content = web.get(stats_url(link)).content.decode()
        assert 'Clicks' in content and 'Unique visitors' in content and 'Bot clicks' in content
        assert '>7<' in content.replace(' ', '').replace('\n', '')
        assert '>3<' in content.replace(' ', '').replace('\n', '')

    def test_the_detail_is_offered_with_its_price_until_paid(self, web, link):
        content = web.get(stats_url(link)).content.decode()
        assert 'Detailed analytics' in content
        assert 'Turn on for 1 credit' in content
        assert 'data-cost="1"' in content
        assert 'Clicks over time' not in content
        assert 'Top referrers' not in content
        assert '<svg' not in content

    def test_nothing_of_the_detail_leaks_into_the_page(self, web, link):
        add_event(link, at_day(today()), referrer='secret-referrer.example')
        assert 'secret-referrer.example' not in web.get(stats_url(link)).content.decode()

    def test_once_paid_the_chart_and_tables_appear(self, web, paid_link):
        referrer = 'news.example.org'
        add_event(paid_link, at_day(today()), referrer=referrer, country='DE')
        content = web.get(stats_url(paid_link)).content.decode()
        assert 'Clicks over time' in content and '<svg' in content
        for title in ('Top referrers', 'Top countries', 'Devices', 'Browsers'):
            assert title in content
        assert content.count(referrer) >= 1
        assert 'Turn on for' not in content

    def test_a_free_price_opens_it_for_everyone(self, web, link, prices):
        prices.analytics_price = 0
        prices.save()
        assert 'Clicks over time' in web.get(stats_url(link)).content.decode()

    def test_opening_free_stats_keeps_them_when_the_price_rises(self, web, link, prices):
        prices.analytics_price = 0
        prices.save()
        web.get(stats_url(link))
        link.refresh_from_db()
        assert link.analytics_paid_at is not None
        prices.analytics_price = 5
        prices.save()
        assert 'Clicks over time' in web.get(stats_url(link)).content.decode()

    def test_a_taken_down_link_cannot_buy_it(self, web, funded_user):
        taken = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        content = web.get(stats_url(taken)).content.decode()
        assert 'Turn on for' not in content
        response = web.post(enable_url(taken), {'confirm_cost': '1'})
        assert 'can no longer be changed' in ' '.join(flashes(response))
        assert balance(funded_user) == 10

    def test_a_link_that_cannot_be_changed_has_no_change_link(self, web, funded_user):
        editable = create_link(funded_user)
        assert reverse('short_url:edit', args=[editable.pk]) in (
            web.get(stats_url(editable)).content.decode()
        )
        for kwargs in ({'status': LinkStatus.TAKEN_DOWN}, {'held_for_review_at': timezone.now()}):
            link = create_link(funded_user, **kwargs)
            content = web.get(stats_url(link)).content.decode()
            assert reverse('short_url:edit', args=[link.pk]) not in content


class TestRanges:
    def test_the_selector_and_the_default(self, web, paid_link):
        content = web.get(stats_url(paid_link)).content.decode()
        for label in ('7 days', '30 days', '90 days', 'All time'):
            assert label in content
        assert 'aria-current="true"' in content
        assert 'in 30 days' in content

    @pytest.mark.parametrize(
        ('value', 'label'), [('7', 'in 7 days'), ('90', 'in 90 days'), ('all', 'in all time')]
    )
    def test_a_range_is_applied(self, web, paid_link, value, label):
        assert label in web.get(stats_url(paid_link, range=value)).content.decode()

    def test_a_bad_range_falls_back(self, web, paid_link):
        assert 'in 30 days' in web.get(stats_url(paid_link, range='forever')).content.decode()

    def test_the_numbers_follow_the_range(self, web, paid_link):
        add_event(paid_link, at_day(today() - timedelta(days=1)), visitor='a')
        add_event(paid_link, at_day(today() - timedelta(days=40)), visitor='b')
        recent = web.get(stats_url(paid_link, range=7)).context['detail']
        everything = web.get(stats_url(paid_link, range='all')).context['detail']
        assert (recent.clicks, everything.clicks) == (1, 2)

    def test_the_empty_state(self, web, paid_link):
        content = web.get(stats_url(paid_link)).content.decode()
        assert 'No clicks in this period yet.' in content
        assert 'Nothing yet.' in content

    def test_the_bot_filter_is_explained_and_followed(self, web, funded_user):
        link = create_link(funded_user, analytics_paid_at=timezone.now())
        add_event(link, at_day(today()), visitor='h')
        add_event(link, at_day(today()), visitor='b', bot=True)
        page = web.get(stats_url(link))
        assert 'People only: bots are left out.' in page.content.decode()
        assert page.context['detail'].clicks == 1
        ShortLink.objects.filter(pk=link.pk).update(filter_bots=False)
        page = web.get(stats_url(link))
        assert 'Bots included.' in page.content.decode()
        assert page.context['detail'].clicks == 2


class TestEnablingAnalytics:
    def test_it_charges_once_and_unlocks(self, web, funded_user, link):
        response = web.post(enable_url(link), {'confirm_cost': '1'})
        assert response.status_code == 302 and response['Location'] == stats_url(link)
        assert flashes(response) == ['Detailed analytics are on for this link.']
        link.refresh_from_db()
        assert link.analytics_paid_at is not None
        assert balance(funded_user) == 9
        assert 'Clicks over time' in web.get(stats_url(link)).content.decode()

    def test_the_ledger_row_names_the_extra(self, web, funded_user, link):
        web.post(enable_url(link), {'confirm_cost': '1'})
        row = CreditTransaction.objects.get(user=funded_user, source='short_url')
        assert row.amount == -1 and 'detailed analytics' in row.description.lower()

    def test_a_second_time_is_free_and_quiet(self, web, funded_user, link):
        web.post(enable_url(link), {'confirm_cost': '1'})
        web.get(stats_url(link))  # shows (and so consumes) the first message
        response = web.post(enable_url(link), {'confirm_cost': '0'})
        assert flashes(response) == []
        assert balance(funded_user) == 9

    @pytest.mark.parametrize('confirm', [None, '', '0', '2', 'one'])
    def test_without_the_right_confirmation_nothing_is_charged(
        self, web, funded_user, link, confirm
    ):
        data = {} if confirm is None else {'confirm_cost': confirm}
        response = web.post(enable_url(link), data)
        assert 'cost 1 credit.' in ' '.join(flashes(response))
        link.refresh_from_db()
        assert link.analytics_paid_at is None
        assert balance(funded_user) == 10

    def test_a_price_that_moved_after_the_quote_asks_again(
        self, web, funded_user, link, monkeypatch
    ):
        from ..views import stats as stats_module

        stale = billing.quote([billing.Extra.ANALYTICS])
        monkeypatch.setattr(stats_module, 'quote_analytics', lambda link: stale)
        row = billing.ShortUrlSettings.load()
        row.analytics_price = 4
        row.save()
        response = web.post(enable_url(link), {'confirm_cost': '1'})
        assert 'now cost 4 credits' in ' '.join(flashes(response))
        assert balance(funded_user) == 10
        link.refresh_from_db()
        assert link.analytics_paid_at is None

    def test_without_enough_credits_it_says_so(self, client, user, prices):
        client.force_login(user)
        link = create_link(user)
        response = client.post(enable_url(link), {'confirm_cost': '1'})
        assert 'have enough credits' in ' '.join(flashes(response))
        assert reverse('billing:credits-history') in ' '.join(flashes(response))
        link.refresh_from_db()
        assert link.analytics_paid_at is None

    def test_a_free_price_charges_nothing(self, web, funded_user, link, prices):
        prices.analytics_price = 0
        prices.save()
        web.post(enable_url(link), {'confirm_cost': '0'})
        assert balance(funded_user) == 10

    def test_the_page_offers_the_modal_to_confirm(self, web, link):
        content = web.get(stats_url(link)).content.decode()
        assert 'x-data="confirmModal"' in content
        assert f'data-url="{enable_url(link)}"' in content
