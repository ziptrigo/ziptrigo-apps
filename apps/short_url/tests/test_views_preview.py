import pytest
from django.contrib.auth.hashers import make_password

from ..models import ClickEvent, ShortLink
from .factories import create_link

pytestmark = pytest.mark.django_db

PASSWORD = 'hunter2'


@pytest.fixture
def link(user):
    return create_link(
        user,
        code='Promo1',
        title='Spring catalogue',
        destination='https://shop.example.com/catalogue?id=7',
    )


@pytest.fixture
def protected(user):
    return create_link(
        user,
        code='Locked1',
        title='Secret title',
        destination='https://hidden.example.com/place',
        password_hash=make_password(PASSWORD),
    )


class TestPreview:
    def test_it_shows_the_destination_title_host_and_age(self, client, link):
        response = client.get('/s/Promo1+')
        assert response.status_code == 200
        content = response.content.decode()
        assert 'https://shop.example.com/catalogue?id=7' in content
        assert 'Spring catalogue' in content
        assert 'Goes to <strong' in content and 'shop.example.com</strong>' in content
        assert 'Created' in content and 'ago' in content

    def test_it_says_nothing_about_the_owner(self, client, link, user):
        content = client.get('/s/Promo1+').content.decode()
        assert user.email not in content and user.name not in content

    def test_it_is_not_cached_nor_indexed_and_counts_no_click(self, client, link):
        response = client.get('/s/Promo1+')
        assert response['Cache-Control'] == 'no-store, private'
        assert response['X-Robots-Tag'] == 'noindex, nofollow'
        assert ShortLink.objects.get(pk=link.pk).click_count == 0
        assert not ClickEvent.objects.exists()

    def test_the_continue_button_goes_to_the_redirect(self, client, link):
        assert 'href="/s/Promo1"' in client.get('/s/promo1+').content.decode()

    def test_a_head_and_a_trailing_slash_work(self, client, link):
        assert client.head('/s/Promo1+').status_code == 200
        assert client.get('/s/Promo1+/').status_code == 200

    def test_it_is_escaped(self, client, user):
        link = create_link(
            user, title='<b>bold</b>', destination='https://example.com/?a=<script>x</script>'
        )
        content = client.get(f'/s/{link.code}+').content.decode()
        assert '<b>bold</b>' not in content and '<script>x</script>' not in content

    def test_an_unavailable_link_is_the_neutral_404(self, client, user):
        gone = create_link(user, status='disabled')
        unknown = client.get('/s/neverissued+')
        assert client.get(f'/s/{gone.code}+').content == unknown.content
        assert unknown.status_code == 404

    def test_there_is_no_login(self, client, link):
        assert client.get('/s/Promo1+').status_code == 200


class TestProtectedPreview:
    def test_the_destination_and_title_stay_back_until_unlocked(self, client, protected):
        response = client.get('/s/Locked1+')
        assert response.status_code == 200
        content = response.content.decode()
        assert 'hidden.example.com' not in content
        assert 'Secret title' not in content
        assert 'protected by a password' in content
        assert 'Goes to' not in content

    def test_it_still_offers_to_continue_which_asks_for_the_password(self, client, protected):
        assert 'href="/s/Locked1"' in client.get('/s/Locked1+').content.decode()
        assert client.get('/s/Locked1')['Location'] == '/s/Locked1/unlock'

    def test_once_unlocked_in_the_session_it_shows_everything(self, client, protected):
        client.post('/s/Locked1/unlock/', {'password': PASSWORD})
        content = client.get('/s/Locked1+').content.decode()
        assert 'https://hidden.example.com/place' in content
        assert 'Secret title' in content
        assert 'protected by a password' not in content

    def test_a_changed_password_hides_it_again(self, client, protected):
        client.post('/s/Locked1/unlock/', {'password': PASSWORD})
        ShortLink.objects.filter(pk=protected.pk).update(password_hash=make_password('new'))
        assert 'hidden.example.com' not in client.get('/s/Locked1+').content.decode()

    def test_the_preview_does_not_unlock_anything(self, client, protected):
        client.get('/s/Locked1+')
        assert client.get('/s/Locked1')['Location'] == '/s/Locked1/unlock'


@pytest.fixture
def limits(settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True

    def set_rules(**rules):
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, **rules}

    return set_rules


class TestPreviewRateLimit:
    def test_over_the_limit_it_is_a_429_with_retry_after(self, client, link, limits):
        limits(SU_PREVIEW_IP=(2, 60))
        assert client.get('/s/Promo1+').status_code == 200
        assert client.get('/s/Promo1+').status_code == 200
        response = client.get('/s/Promo1+')
        assert response.status_code == 429
        assert response['Retry-After']
        assert 'shop.example.com' not in response.content.decode()

    def test_made_up_codes_spend_the_same_budget(self, client, link, limits):
        limits(SU_PREVIEW_IP=(2, 60))
        client.get('/s/madeup1+')
        client.get('/s/madeup2+')
        assert client.get('/s/Promo1+').status_code == 429

    def test_the_preview_and_the_redirect_have_separate_budgets(self, client, link, limits):
        limits(SU_PREVIEW_IP=(1, 60), SU_REDIRECT_IP=(1, 60))
        assert client.get('/s/Promo1+').status_code == 200
        assert client.get('/s/Promo1+').status_code == 429
        assert client.get('/s/Promo1').status_code == 302  # its own, untouched budget
        assert ShortLink.objects.get(pk=link.pk).click_count == 1

    def test_the_redirect_never_becomes_an_error(self, client, link, limits):
        limits(SU_REDIRECT_IP=(1, 60))
        statuses = [client.get('/s/Promo1').status_code for _ in range(4)]
        assert statuses == [302] * 4
