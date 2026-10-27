import pytest
from django.urls import reverse
from django.utils import timezone

from ..models import LinkStatus, ShortLink
from .factories import create_link, expired

pytestmark = pytest.mark.django_db


def clicks(link) -> int:
    return ShortLink.objects.get(pk=link.pk).click_count


@pytest.fixture
def link(user):
    return create_link(user, code='Promo1', destination='https://example.com/landing?x=1')


class TestRedirect:
    def test_a_known_code_redirects_with_a_302(self, client, link):
        response = client.get('/s/Promo1')
        assert response.status_code == 302
        assert response['Location'] == 'https://example.com/landing?x=1'

    def test_it_is_never_cacheable(self, client, link):
        response = client.get('/s/Promo1')
        assert response['Cache-Control'] == 'no-store, private'

    def test_the_lookup_ignores_case(self, client, link):
        for path in ('/s/promo1', '/s/PROMO1', '/s/pRoMo1'):
            assert client.get(path).status_code == 302

    def test_a_trailing_slash_is_accepted(self, client, link):
        assert client.get('/s/Promo1/').status_code == 302

    def test_the_destination_can_change_without_changing_the_code(self, client, link):
        assert client.get('/s/Promo1')['Location'] == 'https://example.com/landing?x=1'
        ShortLink.objects.filter(pk=link.pk).update(destination='https://example.org/new')
        assert client.get('/s/Promo1')['Location'] == 'https://example.org/new'

    def test_no_login_is_needed(self, client, link):
        assert client.get('/s/Promo1').status_code == 302

    def test_the_url_names_resolve(self, link):
        assert reverse('s:redirect', args=['Promo1']) == '/s/Promo1'

    def test_a_get_counts_one_click(self, client, link):
        client.get('/s/Promo1')
        client.get('/s/promo1')
        link.refresh_from_db()
        assert link.click_count == 2
        assert link.last_clicked_at is not None

    def test_a_head_redirects_without_counting(self, client, link):
        response = client.head('/s/Promo1')
        assert response.status_code == 302
        assert response['Location'] == 'https://example.com/landing?x=1'
        assert clicks(link) == 0

    @pytest.mark.parametrize('method', ['post', 'put', 'delete', 'patch'])
    def test_other_methods_are_refused(self, client, link, method):
        assert getattr(client, method)('/s/Promo1').status_code == 405
        assert clicks(link) == 0

    @pytest.mark.parametrize('path', ['/s/', '/s/ab%20cd', '/s/a.b', '/s/a/b', '/s'])
    def test_paths_that_cannot_be_codes_are_plain_404s(self, client, path):
        assert client.get(path).status_code == 404


def _unavailable_links(user):
    now = timezone.now()
    return {
        'disabled': create_link(user, status=LinkStatus.DISABLED),
        'deleted': create_link(user, status=LinkStatus.DELETED),
        'taken_down': create_link(
            user,
            status=LinkStatus.TAKEN_DOWN,
            takedown_reason='Phishing',
            taken_down_at=now,
        ),
        'held': create_link(user, held_for_review_at=now),
        'expired': expired(user),
        'capped': create_link(user, max_clicks=2, click_count=2),
    }


class TestUnavailable:
    def test_every_unavailable_state_renders_the_same_page(self, client, user):
        pages = {}
        for name, link in _unavailable_links(user).items():
            response = client.get(f'/s/{link.code}')
            assert response.status_code == 404, name
            assert response['Cache-Control'] == 'no-store, private'
            pages[name] = response.content
        unknown = client.get('/s/neverissued')
        assert unknown.status_code == 404
        pages['unknown'] = unknown.content
        assert len(set(pages.values())) == 1, 'the page must not reveal why'

    def test_the_page_says_nothing_specific(self, client, user):
        link = create_link(user, status=LinkStatus.TAKEN_DOWN, takedown_reason='Phishing')
        content = client.get(f'/s/{link.code}').content.decode()
        assert "This link isn't available" in content
        assert 'phishing' not in content.lower()
        assert link.destination not in content
        assert link.code not in content

    def test_nothing_is_counted_or_redirected(self, client, user):
        for name, link in _unavailable_links(user).items():
            before = ShortLink.objects.get(pk=link.pk).click_count
            response = client.get(f'/s/{link.code}')
            assert 'Location' not in response, name
            assert clicks(link) == before, name

    def test_head_and_the_preview_of_an_unavailable_link_are_404_too(self, client, user):
        link = create_link(user, status=LinkStatus.DISABLED)
        assert client.head(f'/s/{link.code}').status_code == 404
        assert client.get(f'/s/{link.code}+').status_code == 404


class TestClickLimit:
    def test_the_last_allowed_click_redirects_and_the_next_does_not(self, client, user):
        link = create_link(user, max_clicks=2)
        assert client.get(f'/s/{link.code}').status_code == 302
        assert client.get(f'/s/{link.code}').status_code == 302
        assert client.get(f'/s/{link.code}').status_code == 404
        assert clicks(link) == 2

    def test_a_head_does_not_use_up_a_click(self, client, user):
        link = create_link(user, max_clicks=1)
        assert client.head(f'/s/{link.code}').status_code == 302
        assert client.head(f'/s/{link.code}').status_code == 302
        assert client.get(f'/s/{link.code}').status_code == 302
        assert client.get(f'/s/{link.code}').status_code == 404

    def test_a_cap_reached_between_the_read_and_the_write_is_not_overshot(
        self, client, user, monkeypatch
    ):
        """The link was available when read; clicks racing this one used up the cap before its
        write. The cap is part of the same UPDATE, so this click is refused too."""
        from ..views import redirect as redirect_module

        link = create_link(user, max_clicks=1)
        real = redirect_module.is_available

        def stale_read(candidate):
            ok = real(candidate)
            ShortLink.objects.filter(pk=link.pk).update(click_count=1)  # someone else's click
            return ok

        monkeypatch.setattr(redirect_module, 'is_available', stale_read)
        response = client.get(f'/s/{link.code}')
        assert response.status_code == 404
        assert clicks(link) == 1

    def test_a_disabled_link_is_not_counted_even_if_it_was_read_as_active(
        self, client, user, monkeypatch
    ):
        from ..views import redirect as redirect_module

        link = create_link(user)
        real = redirect_module.is_available

        def stale_read(candidate):
            ok = real(candidate)
            ShortLink.objects.filter(pk=link.pk).update(status=LinkStatus.DISABLED)
            return ok

        monkeypatch.setattr(redirect_module, 'is_available', stale_read)
        assert client.get(f'/s/{link.code}').status_code == 404
        assert clicks(link) == 0


@pytest.fixture
def tight_limit(settings, ratelimit_clock):
    """Rate limiting on, with `SU_REDIRECT_IP` at 2 a minute and a frozen clock."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SU_REDIRECT_IP': (2, 60)}
    return ratelimit_clock


class TestRateLimit:
    def test_over_the_limit_a_known_link_still_redirects_without_counting(
        self, client, link, tight_limit
    ):
        statuses = [client.get('/s/Promo1').status_code for _ in range(5)]
        assert statuses == [302] * 5
        assert clicks(link) == 2

    def test_over_the_limit_an_unknown_code_is_a_404_never_a_429(self, client, link, tight_limit):
        for _ in range(3):
            client.get('/s/Promo1')
        response = client.get('/s/doesnotexist')
        assert response.status_code == 404

    def test_the_limit_is_charged_before_the_lookup(self, client, link, tight_limit):
        """Made-up codes spend the same budget as real ones."""
        client.get('/s/madeup1')
        client.get('/s/madeup2')
        assert client.get('/s/Promo1').status_code == 302
        assert clicks(link) == 0

    def test_a_new_window_counts_again(self, client, link, tight_limit):
        for _ in range(4):
            client.get('/s/Promo1')
        tight_limit.advance(60)
        client.get('/s/Promo1')
        assert clicks(link) == 3

    def test_a_link_with_a_click_cap_is_counted_even_over_the_limit(
        self, client, user, tight_limit
    ):
        """There the counter is what ends the link, so it must not be skipped."""
        link = create_link(user, max_clicks=4)
        statuses = [client.get(f'/s/{link.code}').status_code for _ in range(6)]
        assert statuses == [302, 302, 302, 302, 404, 404]
        assert clicks(link) == 4

    def test_different_ips_have_their_own_budget(self, client, link, tight_limit):
        for _ in range(3):
            client.get('/s/Promo1', REMOTE_ADDR='203.0.113.5')
        client.get('/s/Promo1', REMOTE_ADDR='203.0.113.6')
        assert clicks(link) == 3

    def test_a_head_still_spends_budget_but_is_never_counted(self, client, link, tight_limit):
        client.head('/s/Promo1')
        client.head('/s/Promo1')
        client.get('/s/Promo1')
        assert clicks(link) == 0


class TestPreview:
    def test_a_plus_shows_where_the_link_goes_without_following_it(self, client, link):
        response = client.get('/s/Promo1+')
        assert response.status_code == 200
        assert 'Location' not in response
        content = response.content.decode()
        assert 'https://example.com/landing?x=1' in content
        assert response['Cache-Control'] == 'no-store, private'
        assert clicks(link) == 0

    def test_the_continue_button_goes_to_the_redirect(self, client, link):
        assert 'href="/s/Promo1"' in client.get('/s/promo1+').content.decode()

    def test_the_title_is_shown(self, client, user):
        link = create_link(user, title='Spring catalogue')
        assert 'Spring catalogue' in client.get(f'/s/{link.code}+').content.decode()

    def test_a_trailing_slash_is_accepted(self, client, link):
        assert client.get('/s/Promo1+/').status_code == 200

    def test_the_destination_is_escaped(self, client, user):
        link = create_link(user, destination='https://example.com/?a=<script>alert(1)</script>')
        content = client.get(f'/s/{link.code}+').content.decode()
        assert '<script>alert(1)</script>' not in content


BOT_AGENT = 'Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)'


class TestAnalytics:
    def test_a_click_records_an_event(self, client, link, settings):
        from ..models import ClickEvent

        settings.ALLOWED_HOSTS = ['testserver']
        client.get(
            '/s/Promo1',
            HTTP_REFERER='https://www.reddit.com/r/something/comments/1',
            HTTP_USER_AGENT=(
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
                '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
            ),
        )
        event = ClickEvent.objects.get()
        assert event.link_id == link.pk
        assert (event.device, event.browser, event.referrer_host) == (
            'mobile',
            'Safari',
            'reddit.com',
        )
        assert not event.is_bot

    def test_a_head_records_no_event(self, client, link):
        from ..models import ClickEvent

        client.head('/s/Promo1')
        assert not ClickEvent.objects.exists()

    def test_a_link_preview_bot_is_redirected_but_not_counted_as_a_click(self, client, link):
        from ..models import ClickEvent

        response = client.get('/s/Promo1', HTTP_USER_AGENT=BOT_AGENT)
        assert response.status_code == 302
        link.refresh_from_db()
        assert (link.click_count, link.bot_click_count) == (0, 1)
        assert ClickEvent.objects.get().is_bot

    @pytest.mark.parametrize('agent', [BOT_AGENT, 'curl/8.1.2', ''], ids=['bot', 'curl', 'blank'])
    def test_a_bot_user_agent_cannot_get_around_a_click_limit(self, client, user, agent):
        link = create_link(user, max_clicks=2)
        for _ in range(2):
            assert client.get(f'/s/{link.code}', HTTP_USER_AGENT=agent).status_code == 302
        assert client.get(f'/s/{link.code}', HTTP_USER_AGENT=agent).status_code == 404
        assert client.get(f'/s/{link.code}').status_code == 404
        link.refresh_from_db()
        assert (link.click_count, link.bot_click_count) == (2, 2)

    def test_over_the_limit_no_event_is_written(self, client, link, tight_limit):
        from ..models import ClickEvent

        for _ in range(5):
            assert client.get('/s/Promo1').status_code == 302
        assert ClickEvent.objects.count() == 2
        assert clicks(link) == 2

    def test_a_capped_link_over_the_limit_counts_without_an_event(self, client, user, tight_limit):
        from ..models import ClickEvent

        link = create_link(user, max_clicks=4)
        for _ in range(4):
            client.get(f'/s/{link.code}')
        assert clicks(link) == 4
        assert ClickEvent.objects.count() == 2, 'the counter moved, the extra insert was shed'

    def test_an_unavailable_link_records_nothing(self, client, user):
        from ..models import ClickEvent

        gone = create_link(user, status=LinkStatus.DISABLED)
        client.get(f'/s/{gone.code}')
        assert not ClickEvent.objects.exists()
