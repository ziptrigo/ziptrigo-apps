"""Sign in with an emailed one-time code or link (issue #295).

Covers the request page (the same answer for every address, an email only for an eligible
account), the code page, the link (a GET signs nobody in, the POST does, once), the purpose
isolation from email confirmation, `next`, the signals and rate limits.
"""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import SESSION_KEY, signals
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import login_otp
from apps.accounts.views.login_code import INVALID_CODE_MESSAGE
from apps.core.models import CoreSettings, EmailVerification
from apps.core.services import email_verification

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
REQUEST_URL = '/account/login/code/'
VERIFY_URL = '/account/login/code/verify/'


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    messages: list[dict] = []
    monkeypatch.setattr(
        'apps.core.services.email_verification.send_email',
        lambda **kwargs: messages.append(kwargs) or (1, 0),
    )
    return messages


@pytest.fixture
def request_code(client, django_capture_on_commit_callbacks):
    def _request(email, *, test_client=None, **extra):
        with django_capture_on_commit_callbacks(execute=True):
            return (test_client or client).post(REQUEST_URL, {'email': email, **extra}, **HTMX)

    return _request


def _code(message: dict) -> str:
    match = re.search(r'^(\d{4,12})$', message['text_body'], re.MULTILINE)
    assert match is not None
    return match.group(1)


def _link_token(message: dict) -> str:
    match = re.search(r'/account/login/link/([^/\s?]+)/', message['text_body'])
    assert match is not None
    return match.group(1)


def _post_code(client, email, code, **extra):
    return client.post(VERIFY_URL, {'email': email, 'code': code, **extra}, **HTMX)


def _signed_in(client) -> bool:
    return SESSION_KEY in client.session


@pytest.fixture
def social_user(db) -> User:
    """An account with no usable password: Google or Microsoft sign-in only."""
    user = User.objects.create_user(
        email='social@example.com', password=None, name='Social', email_confirmed=True
    )
    user.set_unusable_password()
    user.google_sub = 'google-sub-1'
    user.save()
    return user


# --- requesting a code ---------------------------------------------------------------------


def test_login_page_links_to_the_code_page(client):
    html = client.get(reverse('accounts:login')).content.decode()

    assert 'Email me a sign-in code' in html
    assert f'href="{REQUEST_URL}"' in html


def test_login_page_carries_next_to_the_code_page(client):
    html = client.get(reverse('accounts:login'), {'next': '/links/'}).content.decode()

    assert f'{REQUEST_URL}?next=/links/' in html


def test_request_page_renders_the_form(client):
    response = client.get(REQUEST_URL)

    assert response.status_code == 200
    html = response.content.decode()
    assert f'hx-post="{REQUEST_URL}"' in html
    assert 'Email me a code' in html


def test_request_sends_one_email_with_a_code_and_a_link(client, user, sent, request_code):
    response = request_code(user.email)

    assert response.status_code == 200
    assert response['HX-Redirect'] == VERIFY_URL
    assert len(sent) == 1
    message = sent[0]
    assert message['to'] == user.email
    assert message['subject'] == 'Your ZipTrigo sign-in code'
    assert _code(message) not in message['subject']
    assert re.search(r'https?://\S+/account/login/link/[^/\s]+/', message['text_body'])
    assert _code(message) in message['html_body']
    assert '/account/login/link/' in message['html_body']
    assert '10 minutes' in message['text_body']
    assert 'ignore this email' in message['text_body']


def test_request_without_htmx_redirects(client, user, sent, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(REQUEST_URL, {'email': user.email})

    assert response.status_code == 302
    assert response['Location'] == VERIFY_URL
    assert len(sent) == 1


def test_invalid_email_format_is_a_422(client, sent, request_code):
    response = request_code('not-an-email')

    assert response.status_code == 422
    assert sent == []


@pytest.mark.parametrize('case', ['unknown', 'inactive', 'deleted', 'unconfirmed'])
def test_every_address_gets_the_same_answer_and_only_eligible_ones_an_email(
    client, user, sent, request_code, case
):
    """CLAUDE.md (issue #125): nothing here may tell whether an address has an account."""
    if case == 'inactive':
        user.is_active = False
        user.save()
    elif case == 'deleted':
        user.status = User.STATUS_DELETED
        user.save()
    elif case == 'unconfirmed':
        user.email_confirmed = False
        user.save()
    email = 'nobody@example.com' if case == 'unknown' else user.email

    refused = request_code(email)
    known = request_code('someone-else@example.com')

    assert refused.status_code == known.status_code == 200
    assert refused['HX-Redirect'] == known['HX-Redirect']
    assert refused.content == known.content
    assert sent == []
    assert not EmailVerification.objects.filter(purpose=login_otp.PURPOSE).exists()


def test_eligible_and_unknown_addresses_answer_identically(client, user, sent, request_code):
    eligible = request_code(user.email, test_client=Client())
    unknown = request_code('nobody@example.com', test_client=Client())

    assert eligible.status_code == unknown.status_code
    assert eligible['HX-Redirect'] == unknown['HX-Redirect']
    assert eligible.content == unknown.content
    assert len(sent) == 1


def test_request_queues_a_task_for_every_address(client, user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False) as known:
        client.post(REQUEST_URL, {'email': user.email}, **HTMX)
    with django_capture_on_commit_callbacks(execute=False) as unknown:
        client.post(REQUEST_URL, {'email': 'nobody@example.com'}, **HTMX)

    assert len(known) == len(unknown) == 1


def test_social_only_account_gets_a_code_and_signs_in(client, social_user, sent, request_code):
    request_code(social_user.email)
    assert len(sent) == 1

    response = _post_code(client, social_user.email, _code(sent[0]))

    assert response.status_code == 200
    assert _signed_in(client)
    social_user.refresh_from_db()
    assert not social_user.has_usable_password()
    assert social_user.google_sub == 'google-sub-1'


def test_staff_accounts_get_a_code_too(client, admin_user, sent, request_code):
    admin_user.email_confirmed = True
    admin_user.save()

    request_code(admin_user.email)

    assert len(sent) == 1


def test_the_email_is_autoescaped_and_the_subject_fixed(client, user, sent, request_code):
    user.name = '<script>alert(1)</script>'
    user.save()

    request_code(user.email)

    assert '<script>' not in sent[0]['html_body']
    assert '&lt;script&gt;' in sent[0]['html_body']
    assert 'script' not in sent[0]['subject']


def test_a_failed_send_is_swallowed_and_leaves_nothing_pending(
    client, user, monkeypatch, request_code
):
    monkeypatch.setattr('apps.core.services.email_verification.send_email', lambda **kwargs: (0, 1))

    response = request_code(user.email)

    assert response.status_code == 200
    assert not EmailVerification.objects.filter(purpose=login_otp.PURPOSE).exists()


def test_the_cooldown_swallows_a_second_request_and_keeps_the_first_code(
    client, user, sent, request_code
):
    request_code(user.email)
    second = request_code(user.email)

    assert second.status_code == 200
    assert len(sent) == 1
    assert _post_code(client, user.email, _code(sent[0])).status_code == 200
    assert _signed_in(client)


def test_a_new_code_supersedes_the_old_one(client, user, sent, request_code):
    request_code(user.email)
    EmailVerification.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    request_code(user.email)
    assert len(sent) == 2

    stale = _post_code(client, user.email, _code(sent[0]))

    assert stale.status_code == 422
    assert not _signed_in(client)
    assert _post_code(client, user.email, _code(sent[1])).status_code == 200
    assert _signed_in(client)


# --- the code ------------------------------------------------------------------------------


def test_verify_page_prefills_the_address_from_the_session(client, user, sent, request_code):
    request_code(user.email)

    html = client.get(VERIFY_URL).content.decode()

    assert f'value="{user.email}"' in html
    assert 'autocomplete="one-time-code"' in html
    assert 'inputmode="numeric"' in html
    assert 'If that address has an account' in html


def test_the_right_code_signs_in_with_a_session(client, user, sent, request_code):
    request_code(user.email)

    response = _post_code(client, user.email, _code(sent[0]))

    assert response.status_code == 200
    assert response['HX-Redirect'] == '/'
    assert client.session[SESSION_KEY] == str(user.pk)
    assert client.get('/account/').status_code == 200


def test_the_code_works_with_spaces_and_a_differently_cased_domain(
    client, user, sent, request_code
):
    request_code(user.email)
    code = _code(sent[0])

    response = _post_code(client, 'testuser@EXAMPLE.com', f'{code[:3]} {code[3:]}')

    assert response.status_code == 200
    assert _signed_in(client)


def test_sign_in_by_code_does_not_touch_the_password_or_confirmation(
    client, user, sent, request_code
):
    password_hash = user.password
    request_code(user.email)

    _post_code(client, user.email, _code(sent[0]))

    user.refresh_from_db()
    assert user.password == password_hash
    assert user.email_confirmed


def test_a_code_signs_in_once(client, user, sent, request_code):
    request_code(user.email)
    code = _code(sent[0])
    assert _post_code(client, user.email, code).status_code == 200
    other = Client()

    reused = _post_code(other, user.email, code)

    assert reused.status_code == 422
    assert not _signed_in(other)


def test_user_logged_in_fires_and_last_login_is_set(client, user, sent, request_code):
    received = []

    def receiver(sender, request, user, **kwargs):
        received.append(user.pk)

    signals.user_logged_in.connect(receiver)
    try:
        request_code(user.email)
        _post_code(client, user.email, _code(sent[0]))
    finally:
        signals.user_logged_in.disconnect(receiver)

    assert received == [user.pk]
    user.refresh_from_db()
    assert user.last_login is not None


def test_sign_in_uses_the_email_backend(client, user, sent, request_code):
    request_code(user.email)
    _post_code(client, user.email, _code(sent[0]))

    assert client.session['_auth_user_backend'] == 'apps.accounts.backends.EmailBackend'


def test_next_is_honoured_when_it_is_a_same_site_path(client, user, sent, request_code):
    request_code(user.email, next='/links/')
    assert 'next=%2Flinks%2F' in sent[0]['text_body']

    response = _post_code(client, user.email, _code(sent[0]), next='/links/')

    assert response['HX-Redirect'] == '/links/'


@pytest.mark.parametrize('bad', ['https://evil.example/', '//evil.example/', 'javascript:alert(1)'])
def test_next_to_another_site_goes_home(client, user, sent, request_code, bad):
    request_code(user.email, next=bad)
    assert 'next=' not in sent[0]['text_body']

    response = _post_code(client, user.email, _code(sent[0]), next=bad)

    assert response['HX-Redirect'] == '/'


def test_the_request_page_carries_next_to_the_code_page(client, user, sent, request_code):
    response = request_code(user.email, next='/todo/')

    assert response['HX-Redirect'] == f'{VERIFY_URL}?next=%2Ftodo%2F'


def test_a_wrong_code_is_a_generic_422(client, user, sent, request_code):
    request_code(user.email)

    response = _post_code(client, user.email, '000000')

    assert response.status_code == 422
    assert INVALID_CODE_MESSAGE in response.content.decode()
    assert not _signed_in(client)


def test_the_failure_is_the_same_for_a_wrong_code_an_unknown_address_and_no_code(
    client, user, sent, request_code
):
    """Nothing about the page tells whether the address has an account or a pending code."""
    request_code(user.email)
    wrong = _post_code(Client(), user.email, '000000')
    unknown = _post_code(Client(), 'nobody@example.com', '000000')
    other = User.objects.create_user(email='other@example.com', password='x', email_confirmed=True)
    no_code = _post_code(Client(), other.email, '000000')

    assert wrong.status_code == unknown.status_code == no_code.status_code == 422
    bodies = {
        re.sub(r'value="[^"]*"|csrfmiddlewaretoken[^>]*', '', r.content.decode())
        for r in (wrong, unknown, no_code)
    }
    assert len(bodies) == 1


def test_wrong_codes_burn_the_row_even_for_the_right_code(client, user, sent, request_code):
    request_code(user.email)
    code = _code(sent[0])
    for _ in range(5):
        assert _post_code(client, user.email, '000000').status_code == 422

    response = _post_code(client, user.email, code)

    assert response.status_code == 422
    assert not _signed_in(client)


def test_an_expired_code_is_refused(client, user, sent, request_code):
    request_code(user.email)
    EmailVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    assert _post_code(client, user.email, _code(sent[0])).status_code == 422
    assert not _signed_in(client)


def test_the_validity_comes_from_the_setting(client, user, sent, request_code, settings):
    settings.LOGIN_OTP_VALIDITY_MINUTES = 3

    request_code(user.email)

    verification = EmailVerification.objects.get(purpose=login_otp.PURPOSE)
    assert verification.expires_at - verification.created_at <= timedelta(minutes=3, seconds=1)
    assert '3 minutes' in sent[0]['text_body']


def test_the_default_validity_is_ten_minutes(settings):
    assert settings.LOGIN_OTP_VALIDITY_MINUTES == 10


def test_a_code_for_an_account_that_stopped_being_eligible_is_refused(
    client, user, sent, request_code
):
    request_code(user.email)
    user.is_active = False
    user.save()

    assert _post_code(client, user.email, _code(sent[0])).status_code == 422
    assert not _signed_in(client)


def test_code_must_be_digits(client, user):
    response = _post_code(client, user.email, 'abcdef')

    assert response.status_code == 422
    assert 'digits only' in response.content.decode()


def test_non_htmx_failure_renders_the_page(client, user):
    response = client.post(VERIFY_URL, {'email': user.email, 'code': '000000'})

    assert response.status_code == 422
    assert '<html' in response.content.decode().lower()


# --- purpose isolation ---------------------------------------------------------------------


def test_an_email_confirmation_code_and_token_cannot_sign_in(client, user, monkeypatch):
    """The two flows share one table; their purposes keep them apart."""
    monkeypatch.setattr('apps.core.services.email_verification.send_email', lambda **kwargs: (1, 0))
    seen = []

    def build_email(context):
        seen.append(context)
        return 'subject', 'text', '<p>html</p>'

    email_verification.start(user.email, 'accounts.email_confirmation', build_email=build_email)
    context = seen[0]

    assert client.post(reverse('accounts:login-link', args=[context.token])).status_code == 302
    assert not _signed_in(client)
    assert login_otp.user_for_token(context.token) is None
    assert _post_code(client, user.email, context.code).status_code == 422
    assert not _signed_in(client)
    # ... and neither was consumed by the attempt: it still confirms an email.
    assert email_verification.confirm_by_token(context.token, 'accounts.email_confirmation') == (
        user.email
    )


def test_a_sign_in_token_cannot_confirm_an_email(client, user, sent, request_code):
    request_code(user.email)
    token = _link_token(sent[0])
    user.email_confirmed = False
    user.save()

    response = client.post(reverse('accounts:confirm-email', args=[token]))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:email-confirmation-expired')
    user.refresh_from_db()
    assert not user.email_confirmed


# --- the link ------------------------------------------------------------------------------


@pytest.fixture
def link(client, user, sent, request_code) -> str:
    request_code(user.email)
    return reverse('accounts:login-link', args=[_link_token(sent[0])])


def test_the_link_in_the_email_is_absolute(user, sent, request_code, settings):
    settings.BASE_URL = 'https://app.example.com'
    request_code(user.email)

    assert (
        f'https://app.example.com/account/login/link/{_link_token(sent[0])}/'
        in sent[0]['text_body']
    )


def test_get_shows_a_button_and_signs_nobody_in(client, link):
    response = client.get(link)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'csrfmiddlewaretoken' in html
    assert f'action="{link}"' in html
    assert not _signed_in(client)
    # The token was not consumed: the click still works.
    assert client.post(link).status_code == 302
    assert _signed_in(client)


def test_get_of_an_unknown_token_says_it_is_invalid_with_no_button(client):
    response = client.get(reverse('accounts:login-link', args=['not-a-real-token']))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'That sign-in link is not valid or has expired' in html
    assert 'csrfmiddlewaretoken' not in html
    assert not _signed_in(client)


def test_get_shows_the_masked_address_the_button_signs_into(client, link, user):
    html = client.get(link).content.decode()

    assert 't\u2022\u2022\u2022@example.com' in html
    assert user.email not in html


def test_get_peeks_without_consuming_or_counting(client, link, user):
    row = EmailVerification.objects.get(email=user.email)

    for _ in range(3):
        assert client.get(link).status_code == 200

    row.refresh_from_db()
    assert row.confirmed_at is None
    assert row.attempts == 0
    assert row.invalidated_at is None
    assert not _signed_in(client)
    assert client.post(link).status_code == 302
    assert _signed_in(client)


@pytest.mark.parametrize('state', ['used', 'expired', 'superseded'])
def test_get_of_a_dead_link_says_so(client, link, user, state):
    row = EmailVerification.objects.get(email=user.email)
    if state == 'used':
        row.confirmed_at = timezone.now()
    elif state == 'expired':
        row.expires_at = timezone.now() - timedelta(seconds=1)
    else:
        row.invalidated_at = timezone.now()
    row.save()

    html = client.get(link).content.decode()

    assert 'That sign-in link is not valid or has expired' in html
    assert 'csrfmiddlewaretoken' not in html


def test_get_warns_a_visitor_signed_in_as_someone_else(client, link, user):
    other = User.objects.create_user(
        email='other@example.com', password='otherpass123', email_confirmed=True
    )
    client.force_login(other)

    html = client.get(link).content.decode()

    assert 'You&#x27;re signed in as other@example.com' in html or (
        "You're signed in as other@example.com" in html
    )
    assert 'continuing signs you in as t\u2022\u2022\u2022@example.com instead' in html
    # Still nothing happened: the GET changed no session.
    assert client.session[SESSION_KEY] == str(other.pk)


def test_get_has_no_warning_for_the_same_user_or_a_signed_out_visitor(client, link, user):
    assert 'signed in as' not in client.get(link).content.decode()

    client.force_login(user)

    assert 'signed in as' not in client.get(link).content.decode()


def test_code_page_tells_a_signed_in_user_it_switches_accounts(client, user):
    assert 'signed in as' not in client.get(VERIFY_URL).content.decode()

    client.force_login(user)
    html = client.get(VERIFY_URL).content.decode()

    assert 'signed in as testuser@example.com' in html
    assert 'switches this browser' in html


def test_the_signed_in_user_is_the_row_s_account_never_the_typed_address(
    client, sent, request_code
):
    """Two legacy accounts that differ only by case: the code sent to one signs in that one,
    whichever spelling is typed with it."""
    User.objects.create_user(email='Case@example.com', password='x', email_confirmed=True)
    lower = User.objects.create_user(email='case@example.com', password='x', email_confirmed=True)
    request_code('case@example.com')

    response = _post_code(client, 'Case@example.com', _code(sent[0]))

    assert response.status_code == 200
    assert client.session[SESSION_KEY] == str(lower.pk)


def test_post_signs_in_and_goes_home(client, link, user):
    response = client.post(link)

    assert response.status_code == 302
    assert response['Location'] == '/'
    assert client.session[SESSION_KEY] == str(user.pk)


def test_post_honours_next(client, link):
    assert 'name="next"' in client.get(link, {'next': '/links/'}).content.decode()

    response = client.post(link, {'next': '/links/'})

    assert response['Location'] == '/links/'


def test_post_ignores_a_next_to_another_site(client, link):
    response = client.post(link, {'next': 'https://evil.example/'})

    assert response['Location'] == '/'


def test_the_link_works_once(client, link):
    assert client.post(link).status_code == 302
    other = Client()

    response = other.post(link)

    assert response.status_code == 302
    assert response['Location'] == REQUEST_URL
    assert not _signed_in(other)


def test_using_the_link_burns_the_code(client, user, sent, request_code):
    request_code(user.email)
    code = _code(sent[0])
    link = reverse('accounts:login-link', args=[_link_token(sent[0])])
    assert client.post(link).status_code == 302

    other = Client()
    assert _post_code(other, user.email, code).status_code == 422
    assert not _signed_in(other)


def test_using_the_code_burns_the_link(client, user, sent, request_code):
    request_code(user.email)
    link = reverse('accounts:login-link', args=[_link_token(sent[0])])
    assert _post_code(client, user.email, _code(sent[0])).status_code == 200

    other = Client()
    response = other.post(link)

    assert response['Location'] == REQUEST_URL
    assert not _signed_in(other)


def test_an_expired_link_is_refused_with_a_message(client, link, user):
    EmailVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = client.post(link)

    assert response.status_code == 302
    assert response['Location'] == REQUEST_URL
    assert not _signed_in(client)
    page = client.get(response['Location'])
    assert 'That sign-in link is not valid or has expired' in page.content.decode()


def test_a_superseded_link_is_refused(client, user, sent, request_code):
    request_code(user.email)
    old = reverse('accounts:login-link', args=[_link_token(sent[0])])
    EmailVerification.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    request_code(user.email)

    response = client.post(old)

    assert response['Location'] == REQUEST_URL
    assert not _signed_in(client)


def test_a_link_for_an_account_that_stopped_being_eligible_is_refused(client, link, user):
    user.email_confirmed = False
    user.save()

    assert client.post(link)['Location'] == REQUEST_URL
    assert not _signed_in(client)


def test_link_and_code_pages_need_csrf(user, sent, request_code):
    request_code(user.email)
    strict = Client(enforce_csrf_checks=True)

    assert strict.post(REQUEST_URL, {'email': user.email}).status_code == 403
    assert strict.post(VERIFY_URL, {'email': user.email, 'code': '1'}).status_code == 403
    link = reverse('accounts:login-link', args=[_link_token(sent[0])])
    assert strict.post(link).status_code == 403


# --- the terms gate ------------------------------------------------------------------------


@pytest.mark.terms_gate
def test_the_terms_gate_still_applies_after_a_code_sign_in(client, user, sent, request_code):
    request_code(user.email)
    _post_code(client, user.email, _code(sent[0]))

    response = client.get('/account/')

    assert response.status_code == 302
    assert response['Location'].startswith('/account/terms/')


@pytest.mark.terms_gate
def test_the_pages_are_reachable_signed_out_with_the_gate_on(client):
    for url in (REQUEST_URL, VERIFY_URL, '/account/login/link/abc/'):
        assert client.get(url).status_code == 200


# --- rate limits ---------------------------------------------------------------------------


def _enable(settings, **rules):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, **rules}


def test_request_is_limited_per_ip(client, user, settings, sent, request_code, ratelimit_clock):
    _enable(settings, LOGIN_OTP_REQUEST_IP=(2, 3600))

    for i in range(2):
        assert request_code(f'a{i}@example.com').status_code == 200
    response = request_code('b@example.com')

    assert response.status_code == 429
    assert 'Retry-After' in response
    assert response['HX-Retarget'] == '#login-code-msg'


def test_request_is_limited_per_address_and_ip_without_revealing_the_account(
    client, user, settings, sent, request_code, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_REQUEST_EMAIL_IP=(1, 3600))

    request_code(user.email)
    request_code('nobody@example.com')
    known = request_code(user.email)
    unknown = request_code('nobody@example.com')

    assert known.status_code == unknown.status_code == 429
    assert known.content == unknown.content


def test_request_is_limited_per_address_across_ips(
    client, user, settings, sent, request_code, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_REQUEST_EMAIL=(2, 3600))
    codes = []
    for i in range(3):
        codes.append(request_code(user.email, test_client=Client(REMOTE_ADDR=f'10.0.0.{i}')))

    assert [r.status_code for r in codes] == [200, 200, 429]


def test_a_refused_form_spends_no_request_budget(
    client, settings, sent, request_code, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_REQUEST_IP=(1, 3600))

    assert request_code('not-an-email').status_code == 422
    assert request_code('a@example.com').status_code == 200


def test_verify_is_limited_per_ip(client, user, settings, ratelimit_clock):
    _enable(settings, LOGIN_OTP_VERIFY_IP=(2, 300))

    for _ in range(2):
        assert _post_code(client, user.email, '000000').status_code == 422
    response = _post_code(client, user.email, '000000')

    assert response.status_code == 429
    assert response['HX-Retarget'] == '#login-code-verify-msg'


def test_verify_is_limited_per_address_and_ip_for_unknown_addresses_too(
    client, settings, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_VERIFY_EMAIL_IP=(2, 300))

    for _ in range(2):
        assert _post_code(client, 'nobody@example.com', '000000').status_code == 422

    assert _post_code(client, 'nobody@example.com', '000000').status_code == 429
    # Another address is unaffected.
    assert _post_code(client, 'someone@example.com', '000000').status_code == 422


def test_verify_per_address_ceiling_counts_failures_only_across_ips(
    client, user, settings, sent, request_code, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_VERIFY_EMAIL=(2, 900))
    request_code(user.email)
    for i in range(2):
        other = Client(REMOTE_ADDR=f'10.0.0.{i}')
        assert _post_code(other, user.email, '000000').status_code == 422

    blocked = _post_code(Client(REMOTE_ADDR='10.0.0.99'), user.email, _code(sent[0]))

    assert blocked.status_code == 429


def test_right_codes_never_spend_the_failure_budget(
    client, user, settings, sent, request_code, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_VERIFY_EMAIL=(1, 900))
    request_code(user.email)

    assert _post_code(client, user.email, _code(sent[0])).status_code == 200
    EmailVerification.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    request_code(user.email)
    assert _post_code(Client(), user.email, _code(sent[1])).status_code == 200


def test_the_link_post_is_limited_per_ip_and_its_get_is_not(
    client, link, settings, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_VERIFY_IP=(1, 300))

    for _ in range(5):
        assert client.get(link).status_code == 200
    assert client.post(reverse('accounts:login-link', args=['bad'])).status_code == 302
    response = client.post(link)

    assert response.status_code == 429
    assert not _signed_in(client)


def test_verify_daily_ceiling_counts_failures_only_across_ips_and_windows(
    client, user, settings, sent, request_code, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_VERIFY_EMAIL_DAY=(2, 86400))
    request_code(user.email)
    for i in range(2):
        assert (
            _post_code(Client(REMOTE_ADDR=f'10.0.1.{i}'), user.email, '000000').status_code == 422
        )
    ratelimit_clock.advance(3600)  # well past the 15-minute window

    blocked = _post_code(Client(REMOTE_ADDR='10.0.1.99'), user.email, _code(sent[0]))

    assert blocked.status_code == 429
    assert blocked['HX-Retarget'] == '#login-code-verify-msg'
    # ... and the next day it works again.
    ratelimit_clock.advance(86400)
    assert (
        _post_code(Client(REMOTE_ADDR='10.0.1.98'), user.email, _code(sent[0])).status_code == 200
    )


def test_right_codes_never_spend_the_daily_failure_budget(
    client, user, settings, sent, request_code, ratelimit_clock
):
    _enable(settings, LOGIN_OTP_VERIFY_EMAIL_DAY=(1, 86400))
    request_code(user.email)

    assert _post_code(client, user.email, _code(sent[0])).status_code == 200


# --- burning the code leaves the link ------------------------------------------------------


def test_burning_the_code_leaves_the_link_in_the_same_email_usable(
    client, user, sent, request_code
):
    request_code(user.email)
    link = reverse('accounts:login-link', args=[_link_token(sent[0])])
    max_attempts = CoreSettings.load().email_verification_max_attempts
    for _ in range(max_attempts):
        assert _post_code(Client(), user.email, '000000').status_code == 422
    # The code is burned: even the right one fails now ...
    assert _post_code(Client(), user.email, _code(sent[0])).status_code == 422

    # ... but the link, which isn't guessable and so isn't burned, still signs the owner in.
    assert client.post(link).status_code == 302
    assert _signed_in(client)


# --- pending codes die with a security change ----------------------------------------------


def _pending(user, sent, request_code):
    request_code(user.email)
    return _code(sent[0]), _link_token(sent[0])


def test_a_password_change_invalidates_pending_codes_and_links(user, sent, request_code):
    from apps.accounts.services.password_change import change_password

    code, token = _pending(user, sent, request_code)

    change_password(user, 'brandnewpass123')

    assert login_otp.user_for_token(token) is None
    assert login_otp.user_for_code(user.email, code) is None


def test_a_password_reset_invalidates_pending_codes_and_links(user, sent, request_code):
    from apps.accounts.services.password_reset import PasswordResetService
    from apps.accounts.tokens import PasswordResetToken

    code, token = _pending(user, sent, request_code)

    PasswordResetService.reset_password(
        user, 'brandnewpass123', token=str(PasswordResetToken.for_user(user))
    )

    assert login_otp.user_for_token(token) is None
    assert login_otp.user_for_code(user.email, code) is None


def test_an_email_change_invalidates_codes_pending_for_the_address_left(
    client, user, sent, request_code, monkeypatch
):
    from apps.accounts.tokens import CustomAccessToken

    monkeypatch.setattr(
        'apps.accounts.services.email_confirmation.EmailConfirmationService.send_confirmation_email',
        lambda self, user: None,
    )
    code, token = _pending(user, sent, request_code)
    old_email = user.email

    response = client.put(
        '/api/account',
        {'email': 'new@example.com', 'current_password': 'testpass123'},
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {CustomAccessToken.for_user(user)}',
    )

    assert response.status_code == 200
    assert login_otp.user_for_token(token) is None
    assert login_otp.user_for_code(old_email, code) is None


# --- the validity setting check ------------------------------------------------------------


@pytest.mark.parametrize('minutes', [1, 10, 60])
def test_validity_check_is_quiet_for_sensible_values(settings, minutes):
    from apps.accounts.checks import check_login_otp_validity

    settings.LOGIN_OTP_VALIDITY_MINUTES = minutes

    assert check_login_otp_validity() == []


@pytest.mark.parametrize('minutes', [0, -5, 61, 1440])
def test_validity_check_warns_outside_1_to_60(settings, minutes):
    from apps.accounts.checks import check_login_otp_validity

    settings.LOGIN_OTP_VALIDITY_MINUTES = minutes

    (warning,) = check_login_otp_validity()
    assert warning.id == 'accounts.W007'
    assert warning.level == 30  # a Warning, never an Error: `migrate` runs the checks
    assert str(minutes) in warning.msg
