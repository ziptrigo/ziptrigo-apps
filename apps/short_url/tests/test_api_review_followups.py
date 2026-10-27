"""Follow-ups from the review of the API phase: `updated_at` on write responses, the quote
budget, checks before the edit budget, `costs` of impossible actions and bare-date expiries."""

from datetime import date, time, timedelta

import pytest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import BlockedDomain, LinkStatus, ShortLink
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    get,
    limit_rule,
    patch,
    post,
)
from .factories import create_link

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def stamp(link, when):
    """Make `updated_at` old (`auto_now` would overwrite it on a save)."""
    ShortLink.objects.filter(pk=link.pk).update(updated_at=when)
    link.refresh_from_db()
    return link.updated_at


def same_moment(text, moment):
    """The API renders milliseconds only."""
    return abs(parse_datetime(text) - moment) < timedelta(milliseconds=1)


class TestUpdatedAt:
    def test_a_changing_patch_returns_the_new_updated_at(self, client, auth, funded_user):
        link = create_link(funded_user, title='Old')
        before = stamp(link, timezone.now() - timedelta(days=2))

        body = patch(client, f'/links/{link.pk}', auth, {'title': 'New'}).json()

        link.refresh_from_db()
        assert link.updated_at > before
        assert same_moment(body['updated_at'], link.updated_at)

    def test_an_empty_or_unchanged_patch_leaves_it_alone(self, client, auth, funded_user):
        link = create_link(funded_user, title='Same')
        before = stamp(link, timezone.now() - timedelta(days=2))

        empty = patch(client, f'/links/{link.pk}', auth, {}).json()
        same = patch(client, f'/links/{link.pk}', auth, {'title': 'Same'}).json()

        link.refresh_from_db()
        assert link.updated_at == before
        assert empty['updated_at'] == same['updated_at']
        assert same_moment(empty['updated_at'], before)

    def test_enable_and_disable_return_the_new_updated_at(self, client, auth, funded_user):
        link = create_link(funded_user)
        before = stamp(link, timezone.now() - timedelta(days=2))

        disabled = post(client, f'/links/{link.pk}/disable', auth).json()
        link.refresh_from_db()
        assert link.updated_at > before
        assert same_moment(disabled['updated_at'], link.updated_at)

        middle = stamp(link, timezone.now() - timedelta(days=1))
        enabled = post(client, f'/links/{link.pk}/enable', auth).json()
        link.refresh_from_db()
        assert link.updated_at > middle
        assert same_moment(enabled['updated_at'], link.updated_at)

    def test_a_no_op_enable_leaves_it_alone(self, client, auth, funded_user):
        link = create_link(funded_user)
        before = stamp(link, timezone.now() - timedelta(days=2))

        body = post(client, f'/links/{link.pk}/enable', auth).json()

        link.refresh_from_db()
        assert link.updated_at == before
        assert same_moment(body['updated_at'], before)


class TestQuoteBudget:
    def test_quote_is_limited(self, client, auth, settings):
        limit_rule(settings, 'SU_QUOTE_USER', limit=2, window=60)

        statuses = [
            post(client, '/links/quote', auth, {'destination': 'https://x.example'}).status_code
            for _ in range(3)
        ]

        assert statuses == [200, 200, 429]

    def test_a_refused_create_spends_the_quote_budget_not_the_create_budget(
        self, client, auth, settings
    ):
        limit_rule(settings, 'SU_QUOTE_USER', limit=2, window=60)
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SU_CREATE_USER': (1, 3600)}
        bad = {'destination': 'ftp://nope'}

        first = post(client, '/links', auth, bad)
        second = post(client, '/links', auth, bad)
        third = post(client, '/links', auth, bad)

        assert (first.status_code, second.status_code, third.status_code) == (400, 400, 429)

    def test_a_real_create_spends_both(self, client, auth, settings):
        limit_rule(settings, 'SU_QUOTE_USER', limit=1, window=60)
        payload = {'destination': 'https://example.com/p', 'title': 'T'}

        assert post(client, '/links', auth, payload).status_code == 201
        assert post(client, '/links', auth, payload).status_code == 429


class TestEditBudget:
    def test_what_is_refused_up_front_spends_no_edit_budget(
        self, client, auth, funded_user, settings
    ):
        limit_rule(settings, 'SU_EDIT_USER', limit=1)
        link = create_link(funded_user, title='Same')
        held = create_link(funded_user, held_for_review_at=timezone.now())
        down = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)
        BlockedDomain.objects.create(value='evil.example')

        assert (
            patch(client, f'/links/{link.pk}', auth, {'destination': 'ftp://x'}).status_code == 400
        )
        assert (
            patch(
                client, f'/links/{link.pk}', auth, {'destination': 'https://evil.example'}
            ).status_code
            == 403
        )
        assert patch(client, f'/links/{link.pk}', auth, {'max_clicks': 0}).status_code == 400
        assert patch(client, f'/links/{link.pk}', auth, {'title': 'x' * 300}).status_code == 400
        assert patch(client, f'/links/{held.pk}', auth, {'title': 'x'}).status_code == 409
        assert patch(client, f'/links/{down.pk}', auth, {'title': 'x'}).status_code == 400
        # None of those spent the single edit this budget allows.
        assert patch(client, f'/links/{link.pk}', auth, {'title': 'Fine'}).status_code == 200
        assert patch(client, f'/links/{link.pk}', auth, {'title': 'Again'}).status_code == 429

    def test_a_foreign_link_is_a_404_before_anything(self, client, auth, other_user, settings):
        limit_rule(settings, 'SU_EDIT_USER', limit=1)
        foreign = create_link(other_user)

        for _ in range(3):
            assert patch(client, f'/links/{foreign.pk}', auth, {'title': 'x'}).status_code == 404

    def test_a_cost_mismatch_still_spends_one_edit(self, client, auth, funded_user, settings):
        limit_rule(settings, 'SU_EDIT_USER', limit=1)
        link = create_link(funded_user)

        wrong = patch(client, f'/links/{link.pk}', auth, {'password': 'pw', 'expected_cost': 0})
        again = patch(client, f'/links/{link.pk}', auth, {'password': 'pw', 'expected_cost': 1})

        assert wrong.status_code == 409
        assert again.status_code == 429


class TestImpossibleCosts:
    def costs(self, client, auth, link):
        return get(client, f'/links/{link.pk}', auth).json()['costs']

    def test_a_held_disabled_link_cannot_be_enabled_or_given_a_password(
        self, client, auth, funded_user
    ):
        link = create_link(
            funded_user, status=LinkStatus.DISABLED, held_for_review_at=timezone.now()
        )

        costs = self.costs(client, auth, link)

        assert costs['enable'] is None and costs['password'] is None
        assert costs['analytics'] == 1

    def test_a_blocked_disabled_link_cannot_be_enabled(self, client, auth, funded_user):
        link = create_link(
            funded_user, status=LinkStatus.DISABLED, destination='https://evil.example/x'
        )
        BlockedDomain.objects.create(value='evil.example')

        assert self.costs(client, auth, link)['enable'] is None

    def test_a_taken_down_link_can_do_nothing(self, client, auth, funded_user):
        link = create_link(funded_user, status=LinkStatus.TAKEN_DOWN)

        assert self.costs(client, auth, link) == {
            'enable': None,
            'analytics': None,
            'password': None,
        }

    def test_an_active_link_has_nothing_to_enable(self, client, auth, funded_user):
        link = create_link(funded_user)

        costs = self.costs(client, auth, link)

        assert costs['enable'] is None
        assert (costs['analytics'], costs['password']) == (1, 1)

    def test_an_unlocked_analytics_stays_zero(self, client, auth, funded_user):
        link = create_link(funded_user, analytics_paid_at=timezone.now())

        assert self.costs(client, auth, link)['analytics'] == 0


class TestBareDateExpiry:
    def end_of_day(self, link, day):
        local = timezone.localtime(link.expires_at)
        assert (local.date(), local.time()) == (day, time(23, 59, 59))

    def test_a_create_with_a_bare_date_ends_that_day_in_the_server_time_zone(
        self, client, auth, funded_user, settings
    ):
        settings.TIME_ZONE = 'America/New_York'
        timezone.activate('America/New_York')
        try:
            response = post(
                client,
                '/links',
                auth,
                {'destination': 'https://example.com/p', 'title': 'T', 'expires_at': '2099-12-31'},
            )
            assert response.status_code == 201
            self.end_of_day(ShortLink.objects.get(pk=response.json()['id']), date(2099, 12, 31))
        finally:
            timezone.deactivate()

    def test_a_patch_with_a_bare_date_and_a_date_time_still_work(self, client, auth, funded_user):
        link = create_link(funded_user)

        assert (
            patch(client, f'/links/{link.pk}', auth, {'expires_at': '2099-06-30'}).status_code
            == 200
        )
        link.refresh_from_db()
        self.end_of_day(link, date(2099, 6, 30))

        patch(client, f'/links/{link.pk}', auth, {'expires_at': '2099-07-01T08:30:00+00:00'})
        link.refresh_from_db()
        assert link.expires_at is not None
        assert link.expires_at.isoformat() == '2099-07-01T08:30:00+00:00'

        assert patch(client, f'/links/{link.pk}', auth, {'expires_at': None}).status_code == 200
        link.refresh_from_db()
        assert link.expires_at is None

    def test_a_bad_date_is_a_422(self, client, auth, funded_user):
        link = create_link(funded_user)

        response = patch(client, f'/links/{link.pk}', auth, {'expires_at': '2099-13-45'})

        assert response.status_code == 422
