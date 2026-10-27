import pytest
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError

from ..models import ClickEvent, ShortLink
from ..services import passwords
from .conftest import BROWSER_AGENT
from .factories import create_link, expired

pytestmark = pytest.mark.django_db

PASSWORD = 'hunter2'


@pytest.fixture
def link(user):
    return create_link(
        user,
        code='Secret1',
        title='Secret title',
        destination='https://example.com/hidden-place',
        password_hash=make_password(PASSWORD),
    )


def clicks(link) -> int:
    return ShortLink.objects.get(pk=link.pk).click_count


class TestService:
    def test_validate(self):
        assert passwords.validate_password(' a b ') == ' a b '
        for bad in ('', '   '):
            with pytest.raises(ValidationError) as exc:
                passwords.validate_password(bad)
            assert 'password' in exc.value.error_dict
        with pytest.raises(ValidationError):
            passwords.validate_password('x' * (passwords.MAX_PASSWORD_LENGTH + 1))
        assert passwords.validate_password('x' * passwords.MAX_PASSWORD_LENGTH)

    def test_hashing_and_checking(self, link):
        assert PASSWORD not in link.password_hash
        assert passwords.check_password(link, PASSWORD)
        assert not passwords.check_password(link, 'wrong')
        assert not passwords.check_password(link, '')
        assert not passwords.check_password(link, 'x' * 1000)
        assert passwords.hash_password('abc') != passwords.hash_password('abc')  # salted

    def test_a_link_without_a_password_checks_nothing(self, user):
        assert not passwords.check_password(create_link(user), 'anything')

    def test_the_session_remembers_the_current_password_only(self, link):
        session = SessionStore()
        assert not passwords.is_unlocked(session, link)
        passwords.unlock(session, link)
        assert passwords.is_unlocked(session, link)
        link.password_hash = make_password('another')
        assert not passwords.is_unlocked(session, link), 'a new password locks everyone out again'

    def test_a_link_without_a_password_is_always_unlocked(self, user):
        assert passwords.is_unlocked(SessionStore(), create_link(user))

    def test_unlocking_one_link_does_not_unlock_another(self, link, user):
        other = create_link(user, password_hash=make_password(PASSWORD))
        session = SessionStore()
        passwords.unlock(session, link)
        assert not passwords.is_unlocked(session, other)

    def test_the_session_is_bounded(self, user):
        session = SessionStore()
        links = [create_link(user, password_hash='x') for _ in range(passwords.MAX_REMEMBERED + 5)]
        for link in links:
            passwords.unlock(session, link)
        assert len(session[passwords.SESSION_KEY]) == passwords.MAX_REMEMBERED
        assert passwords.is_unlocked(session, links[-1])
        assert not passwords.is_unlocked(session, links[0])

    def test_a_damaged_session_value_is_ignored(self, link):
        session = SessionStore()
        session[passwords.SESSION_KEY] = 'garbage'
        assert not passwords.is_unlocked(session, link)
        passwords.unlock(session, link)
        assert passwords.is_unlocked(session, link)


class TestGate:
    def test_a_protected_link_sends_the_visitor_to_the_password_page(self, client, link):
        response = client.get('/s/Secret1')
        assert response.status_code == 302
        assert response['Location'] == '/s/Secret1/unlock'
        assert response['Cache-Control'] == 'no-store, private'
        assert 'hidden-place' not in response['Location']

    def test_it_is_the_same_for_a_head_and_nothing_is_counted(self, client, link):
        response = client.head('/s/secret1')
        assert response.status_code == 302 and response['Location'] == '/s/Secret1/unlock'
        client.get('/s/Secret1')
        assert clicks(link) == 0
        assert not ClickEvent.objects.exists()

    def test_the_password_page_says_nothing_about_the_destination(self, client, link):
        response = client.get('/s/Secret1/unlock/')
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Password needed' in content and 'name="password"' in content
        assert 'hidden-place' not in content and 'Secret title' not in content
        assert response['Cache-Control'] == 'no-store, private'
        assert response['X-Robots-Tag'] == 'noindex, nofollow'
        assert clicks(link) == 0

    def test_the_form_posts_to_the_unlock_url_with_csrf(self, client, link):
        content = client.get('/s/Secret1/unlock/').content.decode()
        assert 'action="/s/Secret1/unlock"' in content
        assert 'csrfmiddlewaretoken' in content

    def test_a_post_without_a_csrf_token_is_refused(self, link):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True, HTTP_USER_AGENT=BROWSER_AGENT)
        assert strict.post('/s/Secret1/unlock/', {'password': PASSWORD}).status_code == 403

    def test_a_link_that_is_not_available_is_the_neutral_404(self, client, user):
        for item in (
            create_link(user, status='disabled', password_hash='x'),
            expired(user, password_hash='x'),
            create_link(user, max_clicks=1, click_count=1, password_hash='x'),
        ):
            for method in (client.get, client.post):
                response = method(f'/s/{item.code}/unlock/')
                assert response.status_code == 404
        unknown = client.get('/s/neverissued/unlock/')
        assert unknown.status_code == 404
        assert unknown.content == client.get('/s/neverissued').content

    def test_a_link_without_a_password_skips_the_page(self, client, user):
        create_link(user, code='Plain01')
        assert client.get('/s/Plain01/unlock/')['Location'] == '/s/Plain01'
        assert client.post('/s/Plain01/unlock/', {'password': 'x'})['Location'] == '/s/Plain01'

    def test_other_methods_are_refused(self, client, link):
        assert client.put('/s/Secret1/unlock/').status_code == 405
        assert client.delete('/s/Secret1/unlock/').status_code == 405


class TestUnlock:
    def test_the_right_password_unlocks_and_the_redirect_then_counts(self, client, link):
        response = client.post('/s/Secret1/unlock/', {'password': PASSWORD})
        assert response.status_code == 302
        assert response['Location'] == '/s/Secret1'
        assert clicks(link) == 0, 'the unlock itself is not a click'

        final = client.get('/s/Secret1')
        assert final.status_code == 302
        assert final['Location'] == 'https://example.com/hidden-place'
        assert clicks(link) == 1
        assert ClickEvent.objects.count() == 1

    def test_it_stays_unlocked_for_the_session(self, client, link):
        client.post('/s/Secret1/unlock/', {'password': PASSWORD})
        for _ in range(3):
            assert client.get('/s/secret1')['Location'] == 'https://example.com/hidden-place'
        assert clicks(link) == 3

    def test_the_unlock_page_forwards_once_unlocked(self, client, link):
        client.post('/s/Secret1/unlock/', {'password': PASSWORD})
        assert client.get('/s/Secret1/unlock/')['Location'] == '/s/Secret1'

    def test_a_wrong_password_is_a_422_and_unlocks_nothing(self, client, link):
        response = client.post('/s/Secret1/unlock/', {'password': 'nope'})
        assert response.status_code == 422
        assert 'Incorrect password.' in response.content.decode()
        assert 'hidden-place' not in response.content.decode()
        assert client.get('/s/Secret1')['Location'] == '/s/Secret1/unlock'
        assert clicks(link) == 0

    @pytest.mark.parametrize('data', [{}, {'password': ''}, {'password': 'x' * 500}])
    def test_a_missing_or_absurd_password_is_just_incorrect(self, client, link, data):
        response = client.post('/s/Secret1/unlock/', data)
        assert response.status_code == 422
        assert 'Incorrect password.' in response.content.decode()

    def test_it_is_per_link(self, client, link, user):
        create_link(user, code='Other01', password_hash=make_password(PASSWORD))
        client.post('/s/Secret1/unlock/', {'password': PASSWORD})
        assert client.get('/s/Other01')['Location'] == '/s/Other01/unlock'

    def test_each_visitor_has_their_own_session(self, link):
        from django.test import Client

        one = Client(HTTP_USER_AGENT=BROWSER_AGENT)
        two = Client(HTTP_USER_AGENT=BROWSER_AGENT)
        one.post('/s/Secret1/unlock/', {'password': PASSWORD})
        assert one.get('/s/Secret1')['Location'] == 'https://example.com/hidden-place'
        assert two.get('/s/Secret1')['Location'] == '/s/Secret1/unlock'

    def test_changing_the_password_locks_out_an_unlocked_visitor(self, client, link):
        client.post('/s/Secret1/unlock/', {'password': PASSWORD})
        ShortLink.objects.filter(pk=link.pk).update(password_hash=make_password('new-one'))
        assert client.get('/s/Secret1')['Location'] == '/s/Secret1/unlock'

    def test_removing_the_password_opens_the_link_to_everyone(self, client, link):
        ShortLink.objects.filter(pk=link.pk).update(password_hash=None)
        assert client.get('/s/Secret1')['Location'] == 'https://example.com/hidden-place'

    def test_the_click_after_an_unlock_does_not_report_our_own_page_as_the_referrer(
        self, client, link, settings
    ):
        settings.ALLOWED_HOSTS = ['testserver']
        settings.BASE_URL = 'https://testserver'
        client.post('/s/Secret1/unlock/', {'password': PASSWORD})
        client.get('/s/Secret1', HTTP_REFERER='http://testserver/s/Secret1/unlock/')
        assert ClickEvent.objects.get().referrer_host == ''


@pytest.fixture
def limits(settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True

    def set_rules(**rules):
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, **rules}

    return set_rules


class TestRateLimits:
    def test_the_ip_rule_counts_every_attempt_even_a_right_one(self, client, link, limits):
        limits(SU_PASSWORD_IP=(2, 600), SU_PASSWORD_LINK=(1000, 600))
        from django.test import Client

        def visitor():  # a fresh session each time, from the same address
            return Client(HTTP_USER_AGENT=BROWSER_AGENT)

        assert visitor().post('/s/Secret1/unlock/', {'password': PASSWORD}).status_code == 302
        assert visitor().post('/s/Secret1/unlock/', {'password': 'wrong'}).status_code == 422
        response = visitor().post('/s/Secret1/unlock/', {'password': PASSWORD})
        assert response.status_code == 429
        assert response['Retry-After']
        assert 'Too many attempts' in response.content.decode()

    def test_another_ip_has_its_own_budget(self, client, link, limits):
        limits(SU_PASSWORD_IP=(1, 600), SU_PASSWORD_LINK=(1000, 600))
        client.post('/s/Secret1/unlock/', {'password': 'x'}, REMOTE_ADDR='203.0.113.5')
        blocked = client.post('/s/Secret1/unlock/', {'password': 'x'}, REMOTE_ADDR='203.0.113.5')
        assert blocked.status_code == 429
        elsewhere = client.post('/s/Secret1/unlock/', {'password': 'x'}, REMOTE_ADDR='203.0.113.6')
        assert elsewhere.status_code == 422

    def test_the_link_rule_counts_wrong_passwords_across_ips(self, client, link, limits):
        limits(SU_PASSWORD_IP=(1000, 600), SU_PASSWORD_LINK=(2, 600))
        for n in range(2):
            response = client.post(
                '/s/Secret1/unlock/', {'password': 'wrong'}, REMOTE_ADDR=f'203.0.113.{n + 1}'
            )
            assert response.status_code == 422
        # From a third address, even the right password is refused: this link is being guessed at.
        response = client.post(
            '/s/Secret1/unlock/', {'password': PASSWORD}, REMOTE_ADDR='203.0.113.9'
        )
        assert response.status_code == 429

    def test_right_passwords_never_spend_the_link_budget(self, link, limits):
        """Someone who only knows the code can't lock the real recipients out: they would have to
        get passwords wrong, and the recipients' right ones don't count."""
        from django.test import Client

        limits(SU_PASSWORD_IP=(1000, 600), SU_PASSWORD_LINK=(2, 600))
        for _ in range(6):
            visitor = Client(HTTP_USER_AGENT=BROWSER_AGENT)
            assert visitor.post('/s/Secret1/unlock/', {'password': PASSWORD}).status_code == 302
        wrong = Client(HTTP_USER_AGENT=BROWSER_AGENT)
        assert wrong.post('/s/Secret1/unlock/', {'password': 'x'}).status_code == 422

    def test_the_link_budget_is_per_link(self, client, link, user, limits):
        limits(SU_PASSWORD_IP=(1000, 600), SU_PASSWORD_LINK=(1, 600))
        create_link(user, code='Other02', password_hash=make_password(PASSWORD))
        client.post('/s/Secret1/unlock/', {'password': 'wrong'})
        assert client.post('/s/Secret1/unlock/', {'password': 'wrong'}).status_code == 429
        assert client.post('/s/Other02/unlock/', {'password': 'wrong'}).status_code == 422

    def test_a_window_later_the_budget_is_back(self, client, link, limits, ratelimit_clock):
        limits(SU_PASSWORD_IP=(1, 600), SU_PASSWORD_LINK=(1000, 600))
        client.post('/s/Secret1/unlock/', {'password': 'wrong'})
        assert client.post('/s/Secret1/unlock/', {'password': PASSWORD}).status_code == 429
        ratelimit_clock.advance(600)
        assert client.post('/s/Secret1/unlock/', {'password': PASSWORD}).status_code == 302

    def test_unknown_codes_spend_no_password_budget(self, client, link, limits):
        limits(SU_PASSWORD_IP=(1, 600), SU_PASSWORD_LINK=(1000, 600))
        for _ in range(3):
            assert client.post('/s/madeup/unlock/', {'password': 'x'}).status_code == 404
        assert client.post('/s/Secret1/unlock/', {'password': 'wrong'}).status_code == 422

    def test_over_the_redirect_limit_a_protected_link_still_asks_for_its_password(
        self, client, link, limits
    ):
        limits(SU_REDIRECT_IP=(1, 60))
        client.get('/s/Secret1')
        assert client.get('/s/Secret1').status_code == 302
        assert client.get('/s/Secret1/unlock/').status_code == 200
