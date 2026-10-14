"""Sign in with Google (issue #91): `GET /auth/google/login` and `GET /auth/google/callback`.

Nothing here touches the network. Two seams are used:

- Google's discovery document is replaced by patching `DjangoOAuth2App.load_server_metadata`, so
  the real Authlib builds the real authorization URL and stores its real `state` in the session.
- The code-for-token exchange is replaced by patching `DjangoOAuth2App.authorize_access_token`
  (returning what Authlib would: a token dict with the validated claims under `userinfo`). The
  error-path tests that don't need a token (consent denied, a callback with no matching state)
  leave it unpatched: Authlib raises before it would make any request.
"""

import html
from contextlib import contextmanager
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.integrations.django_client import DjangoOAuth2App, OAuthError
from django.contrib.auth import SESSION_KEY
from django.contrib.messages import get_messages
from django.core import checks
from django.urls import reverse

from apps.accounts import checks as accounts_checks
from apps.accounts.models import User
from apps.accounts.services import google_oauth
from apps.accounts.services.google_oauth import identity_from_claims
from apps.accounts.tokens import CustomAccessToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

CLIENT_ID = 'test-client-id.apps.googleusercontent.com'
REDIRECT_URI = 'https://app-staging.ziptrigo.com/auth/google/callback'
GOOGLE_METADATA = {
    'issuer': 'https://accounts.google.com',
    'authorization_endpoint': 'https://accounts.google.com/o/oauth2/v2/auth',
    'token_endpoint': 'https://oauth2.googleapis.com/token',
    'jwks_uri': 'https://www.googleapis.com/oauth2/v3/certs',
}
SUB = '1098765432109876543210'


@pytest.fixture(autouse=True)
def google_settings(settings):
    """Google sign-in configured, as in an environment that has a Google client."""
    settings.GOOGLE_CLIENT_ID = CLIENT_ID
    settings.GOOGLE_CLIENT_SECRET = 'test-client-secret'
    settings.GOOGLE_REDIRECT_URI = REDIRECT_URI


@pytest.fixture
def unconfigured(settings):
    settings.GOOGLE_CLIENT_ID = ''
    settings.GOOGLE_CLIENT_SECRET = ''
    settings.GOOGLE_REDIRECT_URI = ''


@contextmanager
def _metadata():
    with mock.patch.object(DjangoOAuth2App, 'load_server_metadata', return_value=GOOGLE_METADATA):
        yield


def _claims(**overrides):
    claims = {
        'iss': 'https://accounts.google.com',
        'sub': SUB,
        'email': 'new.person@example.com',
        'email_verified': True,
        'name': 'New Person',
        'given_name': 'New',
        'family_name': 'Person',
    }
    claims.update(overrides)
    return claims


def _callback(client, claims=None, *, token=None, query='code=abc&state=xyz'):
    """Hit the callback with Authlib's token exchange returning `claims` as the validated ID
    token (or `token` as is)."""
    if token is None:
        token = {'access_token': 'at', 'userinfo': claims if claims is not None else _claims()}
    with mock.patch.object(DjangoOAuth2App, 'authorize_access_token', return_value=token):
        return client.get(f'{reverse("google_auth:callback")}?{query}')


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


def _signed_in_as(client, user) -> bool:
    return client.session.get(SESSION_KEY) == str(user.pk)


# -- routes -----------------------------------------------------------------------------------


def test_routes_are_exactly_the_registered_paths():
    # The callback must equal the redirect URI registered with Google: no trailing slash.
    assert reverse('google_auth:login') == '/auth/google/login'
    assert reverse('google_auth:callback') == '/auth/google/callback'


@pytest.mark.parametrize('name', ['google_auth:login', 'google_auth:callback'])
def test_routes_are_get_only(client, name):
    assert client.post(reverse(name)).status_code == 405


# -- /auth/google/login -----------------------------------------------------------------------


def test_login_redirects_to_google(client):
    with _metadata():
        response = client.get(reverse('google_auth:login'))

    assert response.status_code == 302
    location = urlsplit(response['Location'])
    assert (location.scheme, location.netloc, location.path) == (
        'https',
        'accounts.google.com',
        '/o/oauth2/v2/auth',
    )
    query = parse_qs(location.query)
    assert query['client_id'] == [CLIENT_ID]
    assert query['redirect_uri'] == [REDIRECT_URI]
    assert query['scope'] == ['openid email profile']
    assert query['response_type'] == ['code']
    assert query['nonce']
    # Authlib keeps the state (and the redirect URI / nonce it pairs with) in the session.
    (state,) = query['state']
    assert state
    assert client.session[f'_state_google_{state}']['data']['redirect_uri'] == REDIRECT_URI


def test_login_never_puts_the_client_secret_in_the_redirect(client):
    with _metadata():
        response = client.get(reverse('google_auth:login'))

    assert 'test-client-secret' not in response['Location']


def test_login_when_google_is_unreachable_redirects_with_a_message(client, caplog):
    with mock.patch.object(
        DjangoOAuth2App, 'load_server_metadata', side_effect=ConnectionError('dns failure')
    ):
        response = client.get(reverse('google_auth:login'))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.GoogleSignInError.user_message]
    assert 'Could not start the Google sign-in redirect' in caplog.text


# -- not configured ---------------------------------------------------------------------------


@pytest.mark.parametrize('name', ['google_auth:login', 'google_auth:callback'])
def test_routes_when_not_configured_redirect_to_login_with_a_message(
    client, unconfigured, caplog, name
):
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == ['Google sign-in is not available right now.']
    assert 'not all set' in caplog.text
    # Anonymous traffic can trigger this whenever the feature is off, so it must not be an error.
    assert {r.levelname for r in caplog.records if 'not all set' in r.getMessage()} == {'INFO'}


def test_message_is_rendered_on_the_login_page(client, unconfigured):
    response = client.get(reverse('google_auth:login'), follow=True)

    assert response.status_code == 200
    assert 'Google sign-in is not available right now.' in response.content.decode()


@pytest.mark.parametrize('name', ['accounts:login', 'accounts:register'])
def test_button_is_hidden_when_not_configured(client, unconfigured, name):
    response = client.get(reverse(name))

    assert response.status_code == 200
    assert 'Google' not in response.content.decode()


@pytest.mark.parametrize(
    ('name', 'label'),
    [('accounts:login', 'Sign in with Google'), ('accounts:register', 'Sign up with Google')],
)
def test_button_is_a_plain_link_that_bypasses_htmx(client, name, label):
    content = client.get(reverse(name)).content.decode()

    assert label in content
    link = content[content.index('<a href="/auth/google/login') :].split('>', 1)[0]
    assert 'hx-boost="false"' in link
    # The partial's template comment must not leak into the page as text.
    assert '{#' not in content
    assert 'issue #91' not in content


def test_login_button_carries_next_through(client):
    content = client.get(reverse('accounts:login'), {'next': '/qr/?a=b'}).content.decode()

    assert 'href="/auth/google/login?next=/qr/%3Fa%3Db"' in content


# -- /auth/google/callback: creating, linking and matching ------------------------------------


def test_callback_creates_a_new_user_and_signs_them_in(client):
    response = _callback(client)

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')
    user = User.objects.get(email='new.person@example.com')
    assert user.google_sub == SUB
    assert user.name == 'New Person'
    assert user.email_confirmed
    assert user.email_confirmed_at is not None
    assert user.status == User.STATUS_ACTIVE
    # Nothing to type: only "forgot password" can ever give this account a password.
    assert not user.has_usable_password()
    assert _signed_in_as(client, user)
    assert client.get(reverse('accounts:account')).status_code == 200


def test_callback_starts_a_session_the_same_way_the_password_login_does(client):
    response = _callback(client)

    user = User.objects.get(email='new.person@example.com')
    # `login()` fired `user_logged_in` (`update_last_login`, claim-on-login, ...), no JWT involved.
    assert user.last_login is not None
    assert 'access_token' not in response.content.decode()
    # Only Django's own session and CSRF cookies; no token cookie of any kind.
    assert set(response.cookies) <= {'sessionid', 'csrftoken'}


def test_callback_uses_given_and_family_name_when_name_is_missing(client):
    _callback(client, _claims(name=None))

    assert User.objects.get(email='new.person@example.com').name == 'New Person'


def test_callback_links_an_existing_user_by_verified_email(client, user):
    # Case-insensitive match: Google's casing differs from what the account was created with.
    user.email = 'testuser@gmail.com'
    user.save(update_fields=['email'])

    response = _callback(client, _claims(email='TestUser@Gmail.com'))

    assert response['Location'] == reverse('accounts:account')
    assert User.objects.count() == 1
    user.refresh_from_db()
    assert user.google_sub == SUB
    assert _signed_in_as(client, user)
    # A confirmed account keeps its password.
    assert user.check_password('testpass123')


@pytest.mark.parametrize('domain', ['gmail.com', 'googlemail.com', 'GMAIL.COM', 'GoogleMail.com'])
def test_callback_links_a_confirmed_account_on_a_google_hosted_domain(client, user, domain):
    user.email = f'someone@{domain}'
    user.save(update_fields=['email'])

    _callback(client, _claims(email=f'someone@{domain.swapcase()}'))

    user.refresh_from_db()
    assert user.google_sub == SUB
    assert _signed_in_as(client, user)


def test_callback_links_a_confirmed_account_on_a_workspace_domain_with_hd(client, user):
    response = _callback(client, _claims(email=user.email, hd='example.com'))

    assert response['Location'] == reverse('accounts:account')
    user.refresh_from_db()
    assert user.google_sub == SUB
    assert user.check_password('testpass123')
    assert _signed_in_as(client, user)


@pytest.mark.parametrize('hd', [None, '', '   ', 123])
def test_callback_refuses_to_link_a_confirmed_account_on_another_domain_without_hd(
    client, user, hd
):
    """Google's `email_verified` isn't authoritative for a domain it doesn't host."""
    claims = _claims(email=user.email)
    if hd is not None:
        claims['hd'] = hd

    response = _callback(client, claims)

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.EXISTING_ACCOUNT_MESSAGE]
    user.refresh_from_db()
    assert user.google_sub is None
    assert user.check_password('testpass123')
    assert SESSION_KEY not in client.session


def test_callback_still_creates_a_new_account_on_any_verified_domain(client):
    response = _callback(client, _claims(email='new.person@some-other-domain.org'))

    assert response['Location'] == reverse('accounts:account')
    created = User.objects.get(email='new.person@some-other-domain.org')
    assert created.google_sub == SUB
    assert created.email_confirmed
    assert _signed_in_as(client, created)


def test_callback_refuses_to_link_an_unconfirmed_account(client, user):
    """Whoever registered an unconfirmed address never proved it, and may hold credentials that
    linking would hand a Google sign-in to: the account is left exactly as it was."""
    user.email = 'testuser@gmail.com'
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save(update_fields=['email', 'email_confirmed', 'email_confirmed_at'])

    response = _callback(client, _claims(email=user.email))

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.UNCONFIRMED_ACCOUNT_MESSAGE]
    user.refresh_from_db()
    assert user.google_sub is None
    assert not user.email_confirmed
    assert user.email_confirmed_at is None
    assert user.check_password('testpass123')
    assert SESSION_KEY not in client.session


def test_callback_refuses_to_link_an_established_account_whose_email_was_changed(
    client, user, monkeypatch
):
    """The review's scenario: an attacker with a confirmed account (and JWTs) changes its email
    to the victim's address, which un-confirms it but leaves the JWTs valid. When the victim then
    signs up with Google, the account must not be linked to them."""
    monkeypatch.setattr(
        'apps.accounts.services.email_confirmation.EmailConfirmationService.send_confirmation_email',
        lambda self, user: None,
    )
    token = CustomAccessToken.for_user(user)
    update = client.put(
        '/api/account',
        {'email': 'victim@gmail.com'},
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )
    assert update.status_code == 200
    user.refresh_from_db()
    assert user.email == 'victim@gmail.com'
    assert not user.email_confirmed

    response = _callback(client, _claims(email='victim@gmail.com'))

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.UNCONFIRMED_ACCOUNT_MESSAGE]
    user.refresh_from_db()
    assert user.google_sub is None
    assert not user.email_confirmed
    assert user.check_password('testpass123')
    assert SESSION_KEY not in client.session
    # The attacker's JWT still works, which is exactly why linking must not have happened.
    assert (
        client.put(
            '/api/account',
            {'name': 'Still Me'},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        ).status_code
        == 200
    )


def test_callback_matches_an_existing_user_by_sub(client, user):
    user.google_sub = SUB
    user.save(update_fields=['google_sub'])

    # The Google account's address has changed since it was linked.
    response = _callback(client, _claims(email='changed.address@example.com'))

    assert response['Location'] == reverse('accounts:account')
    assert User.objects.count() == 1
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'
    assert _signed_in_as(client, user)


def test_sub_wins_over_a_different_account_with_the_same_email(client, user):
    other = User.objects.create_user(
        email='holder@example.com', password='x1y2z3w4', email_confirmed=True, google_sub=SUB
    )

    _callback(client, _claims(email=user.email))

    assert _signed_in_as(client, other)
    user.refresh_from_db()
    assert user.google_sub is None


def test_callback_refuses_an_email_linked_to_a_different_google_account(client, user):
    user.google_sub = 'someone-elses-sub'
    user.save(update_fields=['google_sub'])

    response = _callback(client, _claims(email=user.email))

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.CONFLICT_MESSAGE]
    user.refresh_from_db()
    assert user.google_sub == 'someone-elses-sub'
    assert SESSION_KEY not in client.session


def test_callback_refuses_an_ambiguous_email_match(client, user):
    User.objects.create_user(email='TESTUSER@example.com', password='x1y2z3w4')

    response = _callback(client, _claims(email=user.email))

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.CONFLICT_MESSAGE]
    assert SESSION_KEY not in client.session
    assert not User.objects.filter(google_sub=SUB).exists()


# -- /auth/google/callback: refusals ----------------------------------------------------------


@pytest.mark.parametrize('verified', [False, 'false', None, 'yes', 1])
def test_callback_rejects_an_unverified_email_with_403(client, verified):
    response = _callback(client, _claims(email_verified=verified))

    assert response.status_code == 403
    assert google_oauth.UNVERIFIED_MESSAGE in html.unescape(response.content.decode())
    assert SESSION_KEY not in client.session
    assert not User.objects.filter(email='new.person@example.com').exists()


def test_callback_rejects_a_missing_email_verified_claim(client):
    claims = _claims()
    del claims['email_verified']

    assert _callback(client, claims).status_code == 403


def test_callback_does_not_link_an_account_by_an_unverified_email(client, user):
    _callback(client, _claims(email=user.email, email_verified=False))

    user.refresh_from_db()
    assert user.google_sub is None


def test_callback_accepts_email_verified_as_the_string_true(client):
    response = _callback(client, _claims(email_verified='true'))

    assert response.status_code == 302
    assert User.objects.filter(email='new.person@example.com').exists()


@pytest.mark.parametrize('how', ['email', 'sub'])
@pytest.mark.parametrize('field', ['status', 'is_active'])
def test_callback_rejects_an_inactive_user(client, user, how, field):
    if field == 'status':
        user.status = User.STATUS_INACTIVE
    else:
        user.is_active = False
    if how == 'sub':
        user.google_sub = SUB
    user.save()

    response = _callback(client, _claims(email=user.email))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    # `status` is refused with the password login's words; for `is_active=False` the password
    # login now says "Invalid credentials", but Google has verified the address, so telling the
    # person why is no leak.
    assert _messages(response) == ['User not active']
    assert SESSION_KEY not in client.session
    user.refresh_from_db()
    assert user.google_sub == (SUB if how == 'sub' else None)


@pytest.mark.parametrize(
    'claims', [{'email': 'a@example.com', 'email_verified': True}, {'sub': SUB}, {}]
)
def test_callback_rejects_claims_without_a_sub_or_email(client, claims):
    response = _callback(client, claims)

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.InvalidClaims.user_message]
    assert not User.objects.exists()


def test_callback_rejects_a_token_response_without_claims(client):
    response = _callback(client, token={'access_token': 'at'})

    assert response['Location'] == reverse('accounts:login')
    assert not User.objects.exists()


# -- /auth/google/callback: errors never become a 500 -----------------------------------------


def test_callback_when_consent_is_denied(client, caplog):
    response = client.get(
        reverse('google_auth:callback'), {'error': 'access_denied', 'state': 'whatever'}
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [
        'Google sign-in was cancelled. You can try again, or sign in with your email and password.'
    ]
    assert 'access_denied' in caplog.text


def test_callback_with_an_unknown_state_is_refused(client, caplog):
    with _metadata():
        response = client.get(reverse('google_auth:callback'), {'code': 'SECRETCODE', 'state': 'x'})

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.GoogleSignInError.user_message]
    assert 'MismatchingStateError' in caplog.text
    assert 'SECRETCODE' not in caplog.text
    assert SESSION_KEY not in client.session


def test_callback_with_no_parameters_at_all(client):
    response = client.get(reverse('google_auth:callback'))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')


def test_callback_when_the_token_exchange_is_rejected(client, caplog):
    error = OAuthError(error='invalid_grant', description='Bad Request')
    with mock.patch.object(DjangoOAuth2App, 'authorize_access_token', side_effect=error):
        response = client.get(reverse('google_auth:callback'), {'code': 'SECRETCODE', 'state': 'x'})

    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [google_oauth.GoogleSignInError.user_message]
    assert 'invalid_grant' in caplog.text
    assert 'SECRETCODE' not in caplog.text


def test_callback_when_anything_else_goes_wrong(client, caplog):
    boom = RuntimeError('connection reset while talking to Google')
    with mock.patch.object(DjangoOAuth2App, 'authorize_access_token', side_effect=boom):
        response = client.get(reverse('google_auth:callback'), {'code': 'SECRETCODE', 'state': 'x'})

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    # The user sees the friendly text, never the exception's.
    assert _messages(response) == [google_oauth.GoogleSignInError.user_message]
    follow = client.get(reverse('accounts:login'))
    assert 'connection reset' not in follow.content.decode()
    assert 'Google sign-in failed unexpectedly' in caplog.text
    assert 'SECRETCODE' not in caplog.text


# -- next, state, and the whole round trip ----------------------------------------------------


def test_next_survives_the_round_trip(client):
    with _metadata():
        client.get(reverse('google_auth:login'), {'next': '/qr/'})

    response = _callback(client)

    assert response['Location'] == '/qr/'
    # One attempt only: the next sign-in does not inherit it.
    assert 'google_oauth_next' not in client.session


def test_an_offsite_next_is_ignored(client):
    with _metadata():
        client.get(reverse('google_auth:login'), {'next': 'https://evil.example/steal'})

    assert 'google_oauth_next' not in client.session
    assert _callback(client)['Location'] == reverse('accounts:account')


def test_next_is_dropped_when_the_attempt_fails(client):
    with _metadata():
        client.get(reverse('google_auth:login'), {'next': '/qr/'})

    client.get(reverse('google_auth:callback'), {'error': 'access_denied'})

    assert 'google_oauth_next' not in client.session


def test_full_round_trip_validates_state_and_nonce(client):
    """Login, then a callback carrying the state Google was given: only the network is faked
    (the code exchange and the ID token's signature check)."""
    with _metadata():
        location = client.get(reverse('google_auth:login'))['Location']
    (state,) = parse_qs(urlsplit(location).query)['state']
    (nonce,) = parse_qs(urlsplit(location).query)['nonce']
    token = {'access_token': 'at', 'id_token': 'jwt'}

    with (
        _metadata(),
        mock.patch.object(DjangoOAuth2App, 'fetch_access_token', return_value=token) as fetch,
        mock.patch.object(
            DjangoOAuth2App, 'parse_id_token', return_value=_claims()
        ) as parse_id_token,
    ):
        response = client.get(reverse('google_auth:callback'), {'code': 'abc', 'state': state})

    assert response['Location'] == reverse('accounts:account')
    assert User.objects.filter(google_sub=SUB).exists()
    # The nonce Google was given is the one the ID token is checked against, and the code is
    # exchanged against the same redirect URI.
    assert parse_id_token.call_args.kwargs['nonce'] == nonce
    assert fetch.call_args.kwargs['redirect_uri'] == REDIRECT_URI
    # State is single-use: replaying the callback is refused.
    assert f'_state_google_{state}' not in client.session
    replay = client.get(reverse('google_auth:callback'), {'code': 'abc', 'state': state})
    assert replay['Location'] == reverse('accounts:login')


# -- rate limiting ----------------------------------------------------------------------------


def test_callback_is_rate_limited_per_ip(client, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'GOOGLE_LOGIN_IP': (2, 300)}

    for _ in range(2):
        assert client.get(reverse('google_auth:callback'), {'error': 'x'}).status_code == 302
    response = client.get(reverse('google_auth:callback'), {'error': 'x'})

    assert response.status_code == 429
    assert 'Retry-After' in response


# -- the system check and the admin -----------------------------------------------------------


@pytest.mark.parametrize(
    'values',
    [
        ('id', '', ''),
        ('', 'secret', ''),
        ('', '', 'https://example.com/auth/google/callback'),
        ('id', 'secret', ''),
    ],
)
def test_check_warns_about_a_partial_configuration(settings, values):
    settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET, settings.GOOGLE_REDIRECT_URI = values

    (warning,) = accounts_checks.check_google_oauth()

    assert isinstance(warning, checks.Warning)
    assert warning.id == 'accounts.W001'
    for name, value in zip(
        ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI'), values, strict=True
    ):
        assert (name in warning.msg) == (value == '')


def test_check_is_quiet_when_fully_configured_or_not_at_all(settings, unconfigured):
    assert accounts_checks.check_google_oauth() == []

    settings.GOOGLE_CLIENT_ID = 'id'
    settings.GOOGLE_CLIENT_SECRET = 'secret'
    settings.GOOGLE_REDIRECT_URI = REDIRECT_URI
    assert accounts_checks.check_google_oauth() == []


def test_check_is_registered_with_django(settings):
    settings.GOOGLE_CLIENT_ID = 'id'
    settings.GOOGLE_CLIENT_SECRET = ''
    settings.GOOGLE_REDIRECT_URI = ''

    assert 'accounts.W001' in {m.id for m in checks.run_checks()}


def test_admin_shows_google_sub_read_only(client, admin_user, user):
    user.google_sub = SUB
    user.save(update_fields=['google_sub'])
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:accounts_user_change', args=[user.pk]))

    content = response.content.decode()
    assert SUB in content
    assert 'name="google_sub"' not in content


# -- the pieces -------------------------------------------------------------------------------


def test_get_client_requires_all_three_settings(settings):
    settings.GOOGLE_REDIRECT_URI = ''

    with pytest.raises(google_oauth.GoogleNotConfigured):
        google_oauth.get_client()


def test_get_client_registers_google_with_discovery_and_scope():
    client = google_oauth.get_client()

    assert client.client_id == CLIENT_ID
    assert client.client_secret == 'test-client-secret'
    assert client._server_metadata_url == (
        'https://accounts.google.com/.well-known/openid-configuration'
    )
    assert client.client_kwargs['scope'] == 'openid email profile'
    # Built once and reused, so Google's discovery document is cached.
    assert google_oauth.get_client() is client


def test_identity_from_claims_trims_and_caps_the_name():
    identity = identity_from_claims(_claims(name='  ' + 'x' * 300 + '  '))

    assert identity.name == 'x' * 255


def test_identity_from_claims_reads_the_hosted_domain():
    assert identity_from_claims(_claims(hd='example.com')).hosted_domain == 'example.com'
    assert identity_from_claims(_claims()).hosted_domain == ''
