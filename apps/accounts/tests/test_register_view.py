"""The register page (issue #52): the only way to create an account, behind a Cloudflare Turnstile
captcha when one is configured (issue #117)."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import captcha
from apps.accounts.views.register import CAPTCHA_ERROR, EMAIL_TAKEN_ERROR

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _register(client, **overrides):
    payload = {
        'name': 'New User',
        'email': 'newuser@example.com',
        'password': 'password123',
        **overrides,
    }
    return client.post(reverse('accounts:register'), payload, **HTMX)


def test_get_renders_form(client):
    response = client.get(reverse('accounts:register'))

    assert response.status_code == 200
    assert f'hx-post="{reverse("accounts:register")}"' in response.content.decode()


def test_valid_submission_creates_account_and_redirects(client):
    response = _register(client)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    user = User.objects.get(email='newuser@example.com')
    assert user.name == 'New User'
    assert user.email_confirmed is False


def test_register_without_htmx_redirects(client):
    response = client.post(
        reverse('accounts:register'),
        {'name': 'New User', 'email': 'newuser@example.com', 'password': 'password123'},
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:created')


def test_duplicate_email_returns_422(client, user):
    response = _register(client, email=user.email)

    assert response.status_code == 422
    assert 'already exists' in response.content.decode()
    assert User.objects.filter(email=user.email).count() == 1


def test_duplicate_email_differing_only_by_domain_case_returns_422(client, user):
    """Issue #52 code review: `UserManager.create_user` normalizes email (lower-casing only the
    domain) before saving, so `user.email` ('testuser@example.com') is already normalized. A
    signup for the same address with an upper-case domain must be caught by `create_account`'s
    normalized comparison, rather than hitting the model's unique constraint as a 500."""
    mixed_case_domain = 'testuser@EXAMPLE.COM'
    assert mixed_case_domain.lower() == user.email

    response = _register(client, email=mixed_case_domain)

    assert response.status_code == 422
    assert 'already exists' in response.content.decode()
    assert User.objects.filter(email=user.email).count() == 1


def test_weak_password_too_short_returns_422(client):
    response = _register(client, password='pass1')

    assert response.status_code == 422
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_weak_password_no_digit_returns_422(client):
    response = _register(client, password='password')

    assert response.status_code == 422
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_blank_name_returns_422(client):
    response = _register(client, name='')

    assert response.status_code == 422
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_register_requires_csrf_token():
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    response = client.post(
        reverse('accounts:register'),
        {'name': 'New User', 'email': 'newuser@example.com', 'password': 'password123'},
    )

    assert response.status_code == 403
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_api_signup_endpoint_is_gone(client):
    """Issue #117: accounts are created only through this page, behind its captcha."""
    response = client.post(
        '/api/auth/signup',
        {'name': 'A', 'email': 'a@example.com', 'password': 'password123'},
        content_type='application/json',
    )

    assert response.status_code in (404, 405)
    assert not User.objects.filter(email='a@example.com').exists()


def test_invalid_submissions_do_not_consume_signup_ip_rate_limit(client, settings):
    """Issue #52 code review: rejecting a submission (bad password, blank name, ...) must not burn
    the `SIGNUP_IP` budget -- only a submission that actually reaches `create_account` does."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_IP': (1, 60 * 60)}

    for _ in range(3):
        response = _register(client, password='pass1')
        assert response.status_code == 422

    response = _register(client)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert User.objects.filter(email='newuser@example.com').exists()


# -- captcha (issue #117) --

SITE_KEY = '1x00000000000000000000AA'
SECRET_KEY = 'secret-key'


@pytest.fixture
def captcha_on(settings):
    settings.TURNSTILE_SITE_KEY = SITE_KEY
    settings.TURNSTILE_SECRET_KEY = SECRET_KEY


@pytest.fixture
def verify_calls(monkeypatch):
    """Replace `captcha.verify` with a recorder; set `.result` to choose the verdict."""

    class Recorder(list):
        result = True

    calls = Recorder()

    def fake_verify(token, remote_ip=None):
        calls.append((token, remote_ip))
        return calls.result

    monkeypatch.setattr(captcha, 'verify', fake_verify)
    return calls


def test_no_widget_when_captcha_is_off(client):
    content = client.get(reverse('accounts:register')).content.decode()

    assert 'cf-turnstile' not in content
    assert 'challenges.cloudflare.com' not in content


@pytest.mark.usefixtures('captcha_on')
def test_widget_and_script_when_captcha_is_on(client):
    content = client.get(reverse('accounts:register')).content.decode()

    assert f'data-sitekey="{SITE_KEY}"' in content
    assert f'data-action="{captcha.ACTION}"' in content
    assert 'challenges.cloudflare.com/turnstile/v0/api.js?render=explicit' in content
    assert SECRET_KEY not in content


@pytest.mark.usefixtures('captcha_on')
def test_valid_captcha_creates_account(client, verify_calls):
    response = _register(client, **{captcha.RESPONSE_FIELD: 'token-1'})

    assert response['HX-Redirect'] == reverse('accounts:created')
    assert User.objects.filter(email='newuser@example.com').exists()
    assert verify_calls == [('token-1', '127.0.0.1')]


@pytest.mark.usefixtures('captcha_on')
def test_missing_token_returns_422_without_calling_cloudflare(client, verify_calls):
    response = _register(client)

    assert response.status_code == 422
    assert CAPTCHA_ERROR in response.content.decode()
    assert not User.objects.filter(email='newuser@example.com').exists()
    assert verify_calls == []


@pytest.mark.usefixtures('captcha_on')
def test_rejected_token_returns_422_with_a_fresh_widget(client, verify_calls):
    verify_calls.result = False

    response = _register(client, **{captcha.RESPONSE_FIELD: 'bad'})

    content = response.content.decode()
    assert response.status_code == 422
    assert CAPTCHA_ERROR in content
    assert f'data-sitekey="{SITE_KEY}"' in content
    assert 'value="New User"' in content
    assert not User.objects.filter(email='newuser@example.com').exists()


@pytest.mark.usefixtures('captcha_on')
def test_invalid_form_does_not_spend_the_token(client, verify_calls):
    response = _register(client, password='pass1', **{captcha.RESPONSE_FIELD: 'token-1'})

    assert response.status_code == 422
    assert verify_calls == []


@pytest.mark.usefixtures('captcha_on')
def test_failed_captcha_does_not_consume_signup_ip(client, settings, verify_calls):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_IP': (1, 60 * 60)}
    verify_calls.result = False
    for _ in range(3):
        assert _register(client, **{captcha.RESPONSE_FIELD: 'bad'}).status_code == 422

    verify_calls.result = True
    response = _register(client, **{captcha.RESPONSE_FIELD: 'good'})

    assert response['HX-Redirect'] == reverse('accounts:created')


@pytest.mark.usefixtures('captcha_on')
def test_signup_captcha_ip_limits_calls_to_cloudflare(client, settings, verify_calls):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_CAPTCHA_IP': (2, 60 * 60)}
    verify_calls.result = False
    for _ in range(2):
        assert _register(client, **{captcha.RESPONSE_FIELD: 'bad'}).status_code == 422

    response = _register(client, **{captcha.RESPONSE_FIELD: 'bad'})

    assert response.status_code == 429
    assert 'Retry-After' in response
    assert len(verify_calls) == 2


# -- account enumeration (issue #117 review) --


def test_duplicate_email_counts_against_signup_ip(client, settings, user):
    """Whether an address is registered is only answered once a submission reaches
    `create_account`, behind the captcha and `SIGNUP_IP`, so probing it costs budget."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_IP': (1, 60 * 60)}

    assert EMAIL_TAKEN_ERROR in _register(client, email=user.email).content.decode()

    response = _register(client, email=user.email)

    assert response.status_code == 429
    assert EMAIL_TAKEN_ERROR not in response.content.decode()


@pytest.mark.usefixtures('captcha_on')
def test_duplicate_email_is_not_revealed_without_the_captcha(client, user, verify_calls):
    response = _register(client, email=user.email)

    content = response.content.decode()
    assert response.status_code == 422
    assert CAPTCHA_ERROR in content
    assert EMAIL_TAKEN_ERROR not in content


@pytest.mark.usefixtures('captcha_on')
def test_duplicate_email_is_reported_after_a_passing_captcha(client, user, verify_calls):
    response = _register(client, email=user.email, **{captcha.RESPONSE_FIELD: 'good'})

    assert response.status_code == 422
    assert EMAIL_TAKEN_ERROR in response.content.decode()
    assert User.objects.filter(email=user.email).count() == 1


@pytest.mark.usefixtures('captcha_on')
def test_signup_ip_429_after_a_passing_captcha_is_retargeted(client, settings, verify_calls):
    """The form isn't swapped on a 429 (the message goes to `#register-msg`); `turnstile.js`
    resets the widget then, since the token was already spent on Cloudflare."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_IP': (1, 60 * 60)}
    _register(client, email='first@example.com', **{captcha.RESPONSE_FIELD: 'token-1'})

    response = _register(client, **{captcha.RESPONSE_FIELD: 'token-2'})

    assert response.status_code == 429
    assert 'Retry-After' in response
    assert response['HX-Retarget'] == '#register-msg'
    assert len(verify_calls) == 2
    assert not User.objects.filter(email='newuser@example.com').exists()
