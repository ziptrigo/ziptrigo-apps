"""A link held for abuse review: frozen for its owner except disabling it, unavailable to
visitors, and untouched by analytics (recorded clicks, the rollup, the stats page)."""

import pytest
from django.utils import timezone

from apps.billing.services import get_balance

from ..models import ClickEvent, DailyLinkStat, LinkStatus, ShortLink
from ..services import (
    LinkHeldError,
    delete_link,
    enable_analytics,
    hold,
    is_available,
    rollup,
    set_enabled,
    update_link,
)
from .events import NOW, add_event, at_day, days_ago
from .factories import create_link

pytestmark = pytest.mark.django_db

HELD_MESSAGE = 'This link is under review, so it cannot be changed right now.'


@pytest.fixture
def held(funded_user):
    link = create_link(funded_user, code='Held01', destination='https://example.com/held')
    ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
    link.refresh_from_db()
    return link


class TestServices:
    def test_it_is_unavailable_to_visitors(self, held):
        assert not is_available(held)

    @pytest.mark.parametrize(
        'change',
        [
            lambda link: update_link(link, title='New'),
            lambda link: update_link(link, destination='https://example.org/'),
            lambda link: update_link(link, tags='a'),
            lambda link: update_link(link, max_clicks=5),
            lambda link: update_link(link, password='secret'),
            lambda link: update_link(link, filter_bots=False),
        ],
    )
    def test_the_owner_cannot_edit_it(self, held, change):
        with pytest.raises(LinkHeldError) as caught:
            change(held)
        assert HELD_MESSAGE in caught.value.messages[0]
        held.refresh_from_db()
        assert held.title == '' and held.destination == 'https://example.com/held'

    def test_a_failed_edit_charges_nothing(self, held, funded_user):
        before = get_balance(funded_user)
        with pytest.raises(LinkHeldError):
            update_link(held, password='secret')
        assert get_balance(funded_user) == before

    def test_the_owner_cannot_enable_it(self, held):
        ShortLink.objects.filter(pk=held.pk).update(status=LinkStatus.DISABLED)
        held.refresh_from_db()
        with pytest.raises(LinkHeldError):
            set_enabled(held, True)
        held.refresh_from_db()
        assert held.status == LinkStatus.DISABLED

    def test_the_owner_cannot_delete_it(self, held):
        with pytest.raises(LinkHeldError):
            delete_link(held)
        held.refresh_from_db()
        assert held.status == LinkStatus.ACTIVE and held.deleted_at is None

    def test_a_stale_delete_cannot_get_past_a_hold(self, funded_user):
        link = create_link(funded_user)
        stale = ShortLink.objects.get(pk=link.pk)  # read before the hold
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=timezone.now())
        with pytest.raises(LinkHeldError):
            delete_link(stale)

    def test_the_owner_can_still_disable_it(self, held):
        assert set_enabled(held, False) is True
        held.refresh_from_db()
        assert held.status == LinkStatus.DISABLED
        assert held.held_for_review_at is not None  # disabling doesn't lift the hold

    def test_it_stays_frozen_once_disabled(self, held):
        set_enabled(held, False)
        with pytest.raises(LinkHeldError):
            update_link(held, title='x')
        with pytest.raises(LinkHeldError):
            set_enabled(held, True)

    def test_buying_detailed_analytics_is_still_allowed(self, held, funded_user):
        # It changes nothing about the link, only what its owner can see.
        before = get_balance(funded_user)
        assert enable_analytics(held) is True
        assert get_balance(funded_user) == before - 1

    def test_releasing_the_hold_unfreezes_it(self, held):
        hold.release_hold(held)
        update_link(held, title='Back')
        held.refresh_from_db()
        assert held.title == 'Back' and is_available(held)


class TestAnalyticsAreUntouched:
    def test_recorded_clicks_are_rolled_up_while_held(self, held):
        day = days_ago(2)
        add_event(held, at_day(day), visitor='a')
        add_event(held, at_day(day), visitor='b')
        assert rollup.rollup_clicks(now=NOW) >= 1
        assert DailyLinkStat.objects.get(link=held, date=day).clicks == 2
        assert ClickEvent.objects.filter(link=held).count() == 2

    def test_the_stats_page_still_opens(self, web, held):
        response = web.get(f'/links/{held.pk}/stats/')
        assert response.status_code == 200

    def test_buying_analytics_through_the_page_still_works(self, web, held):
        response = web.post(f'/links/{held.pk}/stats/enable/', {'confirm_cost': '1'})
        assert response.status_code == 302
        held.refresh_from_db()
        assert held.analytics_paid_at is not None

    def test_no_click_is_recorded_for_a_held_link(self, client, held):
        assert client.get('/s/Held01').status_code == 404
        held.refresh_from_db()
        assert held.click_count == 0 and not ClickEvent.objects.exists()


class TestWebForTheOwner:
    def test_the_edit_page_goes_back_with_the_message(self, web, held):
        response = web.get(f'/links/{held.pk}/edit/', follow=True)
        assert response.redirect_chain[-1][0] == f'/links/{held.pk}/'
        assert HELD_MESSAGE in response.content.decode()

    def test_a_saved_form_is_a_422_with_the_message(self, web, held):
        response = web.post(
            f'/links/{held.pk}/edit/', {'destination': held.destination, 'title': 'New'}
        )
        assert response.status_code == 422
        assert response['HX-Retarget'] == '#link-form-msg'
        assert HELD_MESSAGE in response.content.decode()
        held.refresh_from_db()
        assert held.title == ''

    def test_enable_and_delete_flash_the_message(self, web, held):
        ShortLink.objects.filter(pk=held.pk).update(status=LinkStatus.DISABLED)
        enabled = web.post(f'/links/{held.pk}/enable/', {}, follow=True)
        assert HELD_MESSAGE in enabled.content.decode()
        deleted = web.post(f'/links/{held.pk}/delete/', {}, follow=True)
        assert HELD_MESSAGE in deleted.content.decode()
        held.refresh_from_db()
        assert held.status == LinkStatus.DISABLED and held.deleted_at is None

    def test_disable_works_through_the_page(self, web, held):
        web.post(f'/links/{held.pk}/disable/', {})
        held.refresh_from_db()
        assert held.status == LinkStatus.DISABLED

    def test_the_link_page_explains_and_offers_only_disable(self, web, held):
        content = web.get(f'/links/{held.pk}/').content.decode()
        assert "This link is under review, so it doesn't work for visitors" in content
        assert 'You can still disable it.' in content
        assert f'/links/{held.pk}/disable/' in content
        assert f'/links/{held.pk}/edit/' not in content
        assert f'/links/{held.pk}/delete/' not in content
        assert 'Under review' in content  # the pill

    def test_the_dashboard_marks_it_and_offers_only_disable(self, web, held):
        content = web.get('/links/').content.decode()
        assert 'Under review' in content
        assert f'/links/{held.pk}/disable/' in content
        assert f'/links/{held.pk}/edit/' not in content
        assert f'/links/{held.pk}/delete/' not in content

    def test_a_held_link_that_is_disabled_has_no_enable(self, web, held):
        ShortLink.objects.filter(pk=held.pk).update(status=LinkStatus.DISABLED)
        content = web.get(f'/links/{held.pk}/').content.decode()
        assert f'/links/{held.pk}/enable/' not in content
        assert 'You can still disable it.' not in content

    def test_creating_other_links_is_not_affected(self, web, held):
        web.post('/links/new/', {'destination': 'https://example.org/new'})
        assert ShortLink.objects.filter(destination='https://example.org/new').exists()
