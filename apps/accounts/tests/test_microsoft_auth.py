"""Sign in with Microsoft (issue #156): `GET /auth/microsoft/login` and
`GET /auth/microsoft/callback`.

Nothing here touches the network. The seams are the same as `test_google_auth`:

- the discovery document is replaced by patching `DjangoOAuth2App.load_server_metadata`, so the
  real Authlib builds the real authorization URL and stores its real `state` in the session;
- the code-for-token exchange is replaced by patching `DjangoOAuth2App.authorize_access_token`
  (returning what Authlib would: a token dict with the validated claims under `userinfo`), except
  in the tests that check Authlib's own ID token validation, which sign a real JWT and patch only
  `fetch_access_token` and `fetch_jwk_set`.
"""

from contextlib import contextmanager
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.integrations.django_client import DjangoOAuth2App, OAuthError
from django.contrib.auth import SESSION_KEY
from django.contrib.messages import get_messages
from django.core import checks
from django.urls import reverse
from joserfc import jwt
from joserfc.jwk import RSAKey

from apps.accounts import checks as accounts_checks
from apps.accounts.models import User
from apps.accounts.services import microsoft_oauth
from apps.accounts.services.microsoft_oauth import (
    PERSONAL_ACCOUNT_TENANT_ID,
    identity_from_claims,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

CLIENT_ID = '11111111-2222-3333-4444-555555555555'
REDIRECT_URI = 'https://app-staging.ziptrigo.com/auth/microsoft/callback'
WORK_TENANT_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
# What the discovery document of the `common` authority really says: a template, not a URL.
MICROSOFT_METADATA = {
    'issuer': 'https://login.microsoftonline.com/{tenantid}/v2.0',
    'authorization_endpoint': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    'token_endpoint': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    'jwks_uri': 'https://login.microsoftonline.com/common/discovery/v2.0/keys',
    'id_token_signing_alg_values_supported': ['RS256'],
}
SUB = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
OID = '00000000-0000-0000-1111-222222222222'
#: What `User.microsoft_id` stores for the default personal / work claims below.
PERSONAL_ID = f'{PERSONAL_ACCOUNT_TENANT_ID}:{OID}'
WORK_ID = f'{WORK_TENANT_ID}:{OID}'


@pytest.fixture(autouse=True)
def microsoft_settings(settings):
    """Microsoft sign-in configured, as in an environment that has an Entra app."""
    settings.MICROSOFT_CLIENT_ID = CLIENT_ID
    settings.MICROSOFT_CLIENT_SECRET = 'test-client-secret'
    settings.MICROSOFT_REDIRECT_URI = REDIRECT_URI
    settings.MICROSOFT_TENANT = 'common'


@pytest.fixture
def unconfigured(settings):
    settings.MICROSOFT_CLIENT_ID = ''
    settings.MICROSOFT_CLIENT_SECRET = ''
    settings.MICROSOFT_REDIRECT_URI = ''


@contextmanager
def _metadata():
    with mock.patch.object(
        DjangoOAuth2App, 'load_server_metadata', return_value=MICROSOFT_METADATA
    ):
        yield


def _claims(**overrides):
    """A personal account's ID token claims; `iss` follows `tid` unless given."""
    claims = {
        'sub': SUB,
        'oid': OID,
        'tid': PERSONAL_ACCOUNT_TENANT_ID,
        'email': 'new.person@outlook.com',
        'name': 'New Person',
    }
    claims.update(overrides)
    claims.setdefault('iss', f'https://login.microsoftonline.com/{claims["tid"]}/v2.0')
    return claims


def _work_claims(**overrides):
    """A work/school account's claims, with the tenant vouching for the email's domain."""
    defaults = {'tid': WORK_TENANT_ID, 'email': 'new.person@contoso.com', 'xms_edov': True}
    return _claims(**{**defaults, **overrides})


def _callback(client, claims=None, *, token=None, query='code=abc&state=xyz'):
    """Hit the callback with Authlib's token exchange returning `claims` as the validated ID
    token (or `token` as is)."""
    if token is None:
        token = {'access_token': 'at', 'userinfo': claims if claims is not None else _claims()}
    with mock.patch.object(DjangoOAuth2App, 'authorize_access_token', return_value=token):
        return client.get(f'{reverse("microsoft_auth:callback")}?{query}')


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


def _signed_in_as(client, user) -> bool:
    return client.session.get(SESSION_KEY) == str(user.pk)


def _refused(client, response, message):
    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [message]
    assert SESSION_KEY not in client.session


# -- routes -----------------------------------------------------------------------------------


def test_routes_are_exactly_the_registered_paths():
    # The callback must equal the redirect URI registered with Microsoft: no trailing slash.
    assert reverse('microsoft_auth:login') == '/auth/microsoft/login'
    assert reverse('microsoft_auth:callback') == '/auth/microsoft/callback'


@pytest.mark.parametrize('name', ['microsoft_auth:login', 'microsoft_auth:callback'])
def test_routes_are_get_only(client, name):
    assert client.post(reverse(name)).status_code == 405


# -- /auth/microsoft/login --------------------------------------------------------------------


def test_login_redirects_to_microsoft(client):
    with _metadata():
        response = client.get(reverse('microsoft_auth:login'))

    assert response.status_code == 302
    location = urlsplit(response['Location'])
    assert (location.scheme, location.netloc, location.path) == (
        'https',
        'login.microsoftonline.com',
        '/common/oauth2/v2.0/authorize',
    )
    query = parse_qs(location.query)
    assert query['client_id'] == [CLIENT_ID]
    assert query['redirect_uri'] == [REDIRECT_URI]
    assert query['scope'] == ['openid email profile']
    assert query['response_type'] == ['code']
    # Always the account picker, so a browser signed in to several accounts doesn't pick one.
    assert query['prompt'] == ['select_account']
    assert query['nonce']
    (state,) = query['state']
    assert client.session[f'_state_microsoft_{state}']['data']['redirect_uri'] == REDIRECT_URI


def test_login_never_puts_the_client_secret_in_the_redirect(client):
    with _metadata():
        response = client.get(reverse('microsoft_auth:login'))

    assert 'test-client-secret' not in response['Location']


def test_login_when_microsoft_is_unreachable_redirects_with_a_message(client, caplog):
    with mock.patch.object(
        DjangoOAuth2App, 'load_server_metadata', side_effect=ConnectionError('dns failure')
    ):
        response = client.get(reverse('microsoft_auth:login'))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [microsoft_oauth.MicrosoftSignInError.user_message]
    assert 'Could not start the Microsoft sign-in redirect' in caplog.text


# -- not configured ---------------------------------------------------------------------------


@pytest.mark.parametrize('name', ['microsoft_auth:login', 'microsoft_auth:callback'])
def test_routes_when_not_configured_redirect_to_login_with_a_message(
    client, unconfigured, caplog, name
):
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == ['Microsoft sign-in is not available right now.']
    assert 'not all set' in caplog.text
    # Anonymous traffic can trigger this whenever the feature is off, so it must not be an error.
    assert {r.levelname for r in caplog.records if 'not all set' in r.getMessage()} == {'INFO'}


@pytest.mark.parametrize('name', ['accounts:login', 'accounts:register'])
def test_button_is_hidden_when_not_configured(client, unconfigured, name):
    response = client.get(reverse(name))

    assert response.status_code == 200
    assert 'Microsoft' not in response.content.decode()


# -- the buttons ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('name', 'label'),
    [('accounts:login', 'Sign in with Microsoft'), ('accounts:register', 'Sign up with Microsoft')],
)
def test_button_is_a_plain_link_that_bypasses_htmx(client, name, label):
    content = client.get(reverse(name)).content.decode()

    assert label in content
    link = content[content.index('<a href="/auth/microsoft/login') :].split('>', 1)[0]
    assert 'hx-boost="false"' in link
    # The partial's template comment must not leak into the page as text.
    assert '{#' not in content
    assert 'issue #156' not in content


def test_login_button_carries_next_through(client):
    content = client.get(reverse('accounts:login'), {'next': '/qr/?a=b'}).content.decode()

    assert 'href="/auth/microsoft/login?next=/qr/%3Fa%3Db"' in content


@pytest.mark.parametrize('name', ['accounts:login', 'accounts:register'])
def test_both_buttons_share_one_divider(client, settings, name):
    settings.GOOGLE_CLIENT_ID = 'id'
    settings.GOOGLE_CLIENT_SECRET = 'secret'
    settings.GOOGLE_REDIRECT_URI = 'https://example.com/auth/google/callback'

    content = client.get(reverse(name)).content.decode()

    assert 'id="google-signin"' in content
    assert 'id="microsoft-signin"' in content
    assert content.count('role="separator"') == 1


def test_only_microsoft_configured_shows_just_its_button(client, settings):
    settings.GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_SECRET = settings.GOOGLE_REDIRECT_URI = ''

    content = client.get(reverse('accounts:login')).content.decode()

    assert 'id="microsoft-signin"' in content
    assert 'Google' not in content
    assert content.count('role="separator"') == 1


# -- /auth/microsoft/callback: creating, linking and matching ---------------------------------


def test_callback_creates_a_new_user_and_signs_them_in(client):
    response = _callback(client)

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')
    user = User.objects.get(email='new.person@outlook.com')
    assert user.microsoft_id == PERSONAL_ID
    assert user.google_sub is None
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

    user = User.objects.get(email='new.person@outlook.com')
    # `login()` fired `user_logged_in` (`update_last_login`, claim-on-login, ...), no JWT involved.
    assert user.last_login is not None
    assert 'access_token' not in response.content.decode()
    assert set(response.cookies) <= {'sessionid', 'csrftoken'}


def test_callback_creates_a_work_account_when_the_tenant_vouches_for_the_domain(client):
    _callback(client, _work_claims())

    user = User.objects.get(email='new.person@contoso.com')
    assert user.microsoft_id == WORK_ID
    assert _signed_in_as(client, user)


def test_callback_tolerates_a_missing_name(client):
    _callback(client, _claims(name=None))

    assert User.objects.get(email='new.person@outlook.com').name == ''


@pytest.mark.parametrize(
    'domain',
    [
        'outlook.com',
        'hotmail.com',
        'live.com',
        'msn.com',
        'HOTMAIL.COM',
        'hotmail.co.uk',
        'live.com.au',
        'outlook.fr',
        'live.nl',
        'windowslive.com',
    ],
)
def test_callback_links_a_confirmed_account_on_a_microsoft_hosted_domain(client, user, domain):
    user.email = f'someone@{domain}'
    user.save(update_fields=['email'])

    # Microsoft's casing differs from what the account was created with.
    _callback(client, _claims(email=f'Someone@{domain.swapcase()}'))

    user.refresh_from_db()
    assert user.microsoft_id == PERSONAL_ID
    assert User.objects.count() == 1
    # A confirmed account keeps its password.
    assert user.check_password('testpass123')
    assert _signed_in_as(client, user)


@pytest.mark.parametrize(
    'domain',
    [
        'gmail.com',
        'example.com',
        'live.attacker.com',
        'outlook.attacker.co.uk',
        'notlive.com',
        'hotmail.com.evil.com',
        # Not Microsoft mailbox domains, though they look like the pattern: the list is explicit.
        'msn.cn',
        'hotmail.xx',
        'live.xx',
        'outlook.co.uk',
    ],
)
def test_callback_refuses_to_link_a_personal_account_on_another_domain(client, user, domain):
    """A personal account can carry a third-party address verified once, long ago."""
    user.email = f'someone@{domain}'
    user.save(update_fields=['email'])

    response = _callback(client, _claims(email=f'someone@{domain}'))

    _refused(client, response, microsoft_oauth.UnverifiedEmail.user_message)
    user.refresh_from_db()
    assert user.microsoft_id is None
    assert user.check_password('testpass123')


def test_callback_does_not_create_a_personal_account_on_another_domain(client):
    response = _callback(client, _claims(email='someone@gmail.com'))

    _refused(client, response, microsoft_oauth.UnverifiedEmail.user_message)
    assert not User.objects.exists()


@pytest.mark.parametrize('flag', [True, 'true', 'True', '1', 1])
def test_callback_links_a_work_account_when_the_tenant_vouches_for_the_domain(client, user, flag):
    response = _callback(client, _work_claims(email=user.email, xms_edov=flag))

    assert response['Location'] == reverse('accounts:account')
    user.refresh_from_db()
    assert user.microsoft_id == WORK_ID
    assert user.check_password('testpass123')
    assert _signed_in_as(client, user)


@pytest.mark.parametrize('flag', [None, False, 'false', '0', 0, '', 'yes', 2])
def test_callback_refuses_to_link_a_work_account_the_tenant_does_not_vouch_for(client, user, flag):
    """The nOAuth case: a tenant admin can put anyone's address on a user in their own tenant."""
    claims = _work_claims(email=user.email)
    if flag is None:
        del claims['xms_edov']
    else:
        claims['xms_edov'] = flag

    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.UnverifiedEmail.user_message)
    user.refresh_from_db()
    assert user.microsoft_id is None
    assert user.check_password('testpass123')


def test_callback_does_not_create_a_work_account_the_tenant_does_not_vouch_for(client):
    claims = _work_claims()
    del claims['xms_edov']

    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.UnverifiedEmail.user_message)
    assert not User.objects.exists()


def test_the_personal_tenant_id_is_what_marks_a_personal_account(client, user):
    """A work tenant can't pose as a personal account: only the real tenant id counts, and the
    mail-domain rule is for that id alone."""
    user.email = 'someone@outlook.com'
    user.save(update_fields=['email'])

    # A work account whose admin set an `outlook.com` address, without `xms_edov`.
    claims = _claims(tid=WORK_TENANT_ID, email='someone@outlook.com')
    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.UnverifiedEmail.user_message)
    user.refresh_from_db()
    assert user.microsoft_id is None


def test_callback_refuses_to_link_an_unconfirmed_account(client, user):
    user.email = 'someone@outlook.com'
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()

    response = _callback(client, _claims(email='someone@outlook.com'))

    _refused(client, response, microsoft_oauth.UnconfirmedAccountConflict.user_message)
    user.refresh_from_db()
    assert user.microsoft_id is None
    assert user.check_password('testpass123')
    assert not user.email_confirmed


def test_callback_matches_an_existing_user_by_tenant_and_object_id(client, user):
    user.microsoft_id = PERSONAL_ID
    user.save(update_fields=['microsoft_id'])

    response = _callback(client, _claims(email='different@outlook.com', name='Renamed'))

    assert response['Location'] == reverse('accounts:account')
    assert User.objects.count() == 1
    user.refresh_from_db()
    # The address and name on the token change nothing on the account.
    assert user.email == 'testuser@example.com'
    assert user.name == 'Test User'
    assert _signed_in_as(client, user)


def test_a_linked_user_signs_in_without_an_email_claim(client, user):
    """The `email` claim is optional for work accounts; the tenant and object id are the proof."""
    user.microsoft_id = WORK_ID
    user.save(update_fields=['microsoft_id'])
    claims = _work_claims()
    del claims['email']
    del claims['xms_edov']

    response = _callback(client, claims)

    assert response['Location'] == reverse('accounts:account')
    assert _signed_in_as(client, user)


def test_a_linked_user_signs_in_after_their_email_was_unconfirmed(client, user):
    user.microsoft_id = PERSONAL_ID
    user.email_confirmed = False
    user.save()

    response = _callback(client, _claims())

    assert response['Location'] == reverse('accounts:account')
    assert _signed_in_as(client, user)


def test_sub_wins_over_a_different_account_with_the_same_email(client, user):
    other = User.objects.create_user(email='new.person@outlook.com', password='x' * 10)
    user.microsoft_id = PERSONAL_ID
    user.save(update_fields=['microsoft_id'])

    _callback(client, _claims(email=other.email))

    assert _signed_in_as(client, user)
    other.refresh_from_db()
    assert other.microsoft_id is None


def test_callback_refuses_an_email_linked_to_a_different_microsoft_account(client, user):
    user.email = 'someone@outlook.com'
    user.microsoft_id = 'another-tenant:another-object'
    user.save()

    response = _callback(client, _claims(email='someone@outlook.com'))

    _refused(client, response, microsoft_oauth.AccountConflict.user_message)
    user.refresh_from_db()
    assert user.microsoft_id == 'another-tenant:another-object'


def test_callback_refuses_an_ambiguous_email_match(client):
    # Legacy rows differing only by case can exist (the unique constraint is case-sensitive).
    User.objects.create_user(email='dup@outlook.com', password='x' * 10, email_confirmed=True)
    User.objects.create_user(email='DUP@outlook.com', password='x' * 10, email_confirmed=True)

    response = _callback(client, _claims(email='dup@outlook.com'))

    _refused(client, response, microsoft_oauth.AccountConflict.user_message)
    assert User.objects.filter(microsoft_id__isnull=False).count() == 0


def test_callback_for_an_unknown_sub_without_an_email_cannot_create_an_account(client):
    claims = _work_claims()
    del claims['email']

    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.NoEmail.user_message)
    assert not User.objects.exists()


@pytest.mark.parametrize('how', ['email', 'identity'])
@pytest.mark.parametrize('field', ['status', 'is_active'])
def test_callback_rejects_an_inactive_user(client, user, how, field):
    user.email = 'someone@outlook.com'
    if field == 'status':
        user.status = User.STATUS_INACTIVE
    else:
        user.is_active = False
    if how == 'identity':
        user.microsoft_id = PERSONAL_ID
    user.save()

    response = _callback(client, _claims(email='someone@outlook.com'))

    _refused(client, response, 'User not active')
    user.refresh_from_db()
    assert user.microsoft_id == (PERSONAL_ID if how == 'identity' else None)


# -- the identity key: tenant + object id, not `sub` -----------------------------------------


def test_a_new_app_registration_keeps_linked_users_signed_in(client, user):
    """`sub` is per app registration; `oid` is not. Recreating the registration (a new client id,
    so a new `sub` for everyone) must not lock linked users out."""
    user.microsoft_id = PERSONAL_ID
    user.save(update_fields=['microsoft_id'])

    response = _callback(client, _claims(sub='a-sub-from-the-new-registration'))

    assert response['Location'] == reverse('accounts:account')
    assert _signed_in_as(client, user)
    assert User.objects.count() == 1


def test_the_same_object_id_in_another_tenant_is_another_account(client, user):
    """A guest has a different `oid` per tenant and `oid`s are only unique within one, so the
    tenant is part of the key: the same `oid` under another tenant must not match."""
    user.microsoft_id = PERSONAL_ID
    user.save(update_fields=['microsoft_id'])

    response = _callback(client, _work_claims())

    assert response['Location'] == reverse('accounts:account')
    other = User.objects.get(email='new.person@contoso.com')
    assert other.pk != user.pk
    assert other.microsoft_id == WORK_ID
    assert _signed_in_as(client, other)


def test_the_stored_identity_is_lower_cased_so_guid_case_does_not_matter(client):
    _callback(client, _claims(oid=OID.upper(), tid=PERSONAL_ACCOUNT_TENANT_ID.upper()))
    client.logout()

    _callback(client, _claims(oid=OID.lower(), email='someone.else@outlook.com'))

    (user,) = User.objects.all()
    assert user.microsoft_id == PERSONAL_ID
    assert user.email == 'new.person@outlook.com'


# -- the email claim: well-formed, and the personal-account fallback -------------------------


@pytest.mark.parametrize(
    'email',
    ['live.com', 'outlook.com\n', '@outlook.com', 'someone@', 'a@b@outlook.com', 'a b@outlook.com'],
)
def test_a_malformed_email_claim_is_no_email_at_all(client, email):
    """`live.com` has no `@` and would have passed the mail-domain rule as the "domain"."""
    response = _callback(client, _claims(email=email))

    _refused(client, response, microsoft_oauth.NoEmail.user_message)
    assert not User.objects.exists()


def test_a_malformed_email_claim_never_links_an_account(client, user):
    user.email = 'outlook.com'
    user.save(update_fields=['email'])

    response = _callback(client, _claims(email='outlook.com'))

    _refused(client, response, microsoft_oauth.NoEmail.user_message)
    user.refresh_from_db()
    assert user.microsoft_id is None


@pytest.mark.parametrize(
    'identity_email',
    ['live.com', 'someone@live.com\n', 'someone@live.com.evil.com', '', ' @live.com'],
)
def test_vouches_for_email_demands_a_whole_address_on_a_listed_domain(identity_email):
    identity = microsoft_oauth.MicrosoftIdentity(
        tenant_id=PERSONAL_ACCOUNT_TENANT_ID,
        object_id=OID,
        email=identity_email,
        email_domain_verified=False,
        name='',
    )

    assert microsoft_oauth._vouches_for_email(identity) is False


def test_a_personal_account_without_an_email_claim_uses_its_sign_in_name(client):
    """A personal account's token can carry its address only as `preferred_username`, which
    Microsoft assigns (no tenant admin can set it); it still has to be a Microsoft-hosted
    mailbox."""
    claims = _claims(preferred_username='someone@hotmail.com')
    del claims['email']

    _callback(client, claims)

    user = User.objects.get(email='someone@hotmail.com')
    assert user.microsoft_id == PERSONAL_ID
    assert _signed_in_as(client, user)


def test_the_email_claim_wins_over_the_sign_in_name(client):
    _callback(client, _claims(email='claim@outlook.com', preferred_username='name@hotmail.com'))

    assert User.objects.filter(email='claim@outlook.com').exists()
    assert not User.objects.filter(email='name@hotmail.com').exists()


def test_a_personal_sign_in_name_on_another_domain_is_not_vouched_for(client):
    claims = _claims(preferred_username='someone@gmail.com')
    del claims['email']

    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.UnverifiedEmail.user_message)
    assert not User.objects.exists()


def test_a_work_account_never_falls_back_to_its_sign_in_name(client):
    """A tenant admin sets `preferred_username` and `upn`; they prove nothing."""
    claims = _work_claims(preferred_username='victim@contoso.com', upn='victim@contoso.com')
    del claims['email']

    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.NoEmail.user_message)
    assert not User.objects.exists()


# -- /auth/microsoft/callback: the ID token's claims ------------------------------------------


@pytest.mark.parametrize(
    'claims',
    [
        {'email': 'a@outlook.com', 'tid': PERSONAL_ACCOUNT_TENANT_ID},  # no oid
        {'sub': SUB, 'oid': OID, 'email': 'a@outlook.com'},  # no tid
        {'oid': '', 'tid': PERSONAL_ACCOUNT_TENANT_ID},
        {'oid': 5, 'tid': PERSONAL_ACCOUNT_TENANT_ID},
        {'sub': SUB, 'tid': PERSONAL_ACCOUNT_TENANT_ID},  # a sub alone is not an identity
        {},
    ],
)
def test_callback_rejects_claims_without_an_oid_or_tid(client, claims):
    claims = {**claims, 'iss': f'https://login.microsoftonline.com/{claims.get("tid")}/v2.0'}

    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.InvalidClaims.user_message)
    assert not User.objects.exists()


@pytest.mark.parametrize(
    'iss',
    [
        None,
        '',
        f'https://login.microsoftonline.com/{WORK_TENANT_ID}/v2.0',  # another tenant's issuer
        f'https://login.microsoftonline.com/{PERSONAL_ACCOUNT_TENANT_ID}/',  # v1 issuer
        f'https://sts.windows.net/{PERSONAL_ACCOUNT_TENANT_ID}/',
        f'http://login.microsoftonline.com/{PERSONAL_ACCOUNT_TENANT_ID}/v2.0',
        f'https://login.microsoftonline.com.evil.test/{PERSONAL_ACCOUNT_TENANT_ID}/v2.0',
    ],
)
def test_callback_rejects_an_issuer_that_does_not_match_the_tenant(client, iss):
    claims = _claims()
    claims['iss'] = iss

    response = _callback(client, claims)

    _refused(client, response, microsoft_oauth.InvalidClaims.user_message)
    assert not User.objects.exists()


def test_callback_rejects_a_token_response_without_claims(client):
    response = _callback(client, token={'access_token': 'at'})

    assert response['Location'] == reverse('accounts:login')
    assert not User.objects.exists()


# -- /auth/microsoft/callback: errors never become a 500 --------------------------------------


def test_callback_when_consent_is_denied(client, caplog):
    response = client.get(
        reverse('microsoft_auth:callback'), {'error': 'access_denied', 'state': 'whatever'}
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')
    assert _messages(response) == [
        'Microsoft sign-in was cancelled. You can try again, or sign in with your email and '
        'password.'
    ]
    assert 'access_denied' in caplog.text


def test_callback_with_an_unknown_state_is_refused(client, caplog):
    with _metadata():
        response = client.get(
            reverse('microsoft_auth:callback'), {'code': 'SECRETCODE', 'state': 'x'}
        )

    _refused(client, response, microsoft_oauth.MicrosoftSignInError.user_message)
    assert 'MismatchingStateError' in caplog.text
    assert 'SECRETCODE' not in caplog.text


def test_callback_with_no_parameters_at_all(client):
    response = client.get(reverse('microsoft_auth:callback'))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')


def test_callback_when_the_token_exchange_is_rejected(client, caplog):
    error = OAuthError(error='invalid_grant', description='Bad Request')
    with mock.patch.object(DjangoOAuth2App, 'authorize_access_token', side_effect=error):
        response = client.get(
            reverse('microsoft_auth:callback'), {'code': 'SECRETCODE', 'state': 'x'}
        )

    _refused(client, response, microsoft_oauth.MicrosoftSignInError.user_message)
    assert 'invalid_grant' in caplog.text
    assert 'SECRETCODE' not in caplog.text


def test_callback_when_anything_else_goes_wrong(client, caplog):
    boom = RuntimeError('connection reset while talking to Microsoft')
    with mock.patch.object(DjangoOAuth2App, 'authorize_access_token', side_effect=boom):
        response = client.get(
            reverse('microsoft_auth:callback'), {'code': 'SECRETCODE', 'state': 'x'}
        )

    _refused(client, response, microsoft_oauth.MicrosoftSignInError.user_message)
    # The user sees the friendly text, never the exception's.
    assert 'connection reset' not in client.get(reverse('accounts:login')).content.decode()
    assert 'Microsoft sign-in failed unexpectedly' in caplog.text
    assert 'SECRETCODE' not in caplog.text


# -- next -------------------------------------------------------------------------------------


def test_next_survives_the_round_trip(client):
    with _metadata():
        client.get(reverse('microsoft_auth:login'), {'next': '/qr/'})

    assert _callback(client)['Location'] == '/qr/'
    assert 'microsoft_oauth_next' not in client.session


def test_an_offsite_next_is_ignored(client):
    with _metadata():
        client.get(reverse('microsoft_auth:login'), {'next': 'https://evil.example/steal'})

    assert 'microsoft_oauth_next' not in client.session
    assert _callback(client)['Location'] == reverse('accounts:account')


def test_next_is_dropped_when_the_attempt_fails(client):
    with _metadata():
        client.get(reverse('microsoft_auth:login'), {'next': '/qr/'})

    client.get(reverse('microsoft_auth:callback'), {'error': 'access_denied'})

    assert 'microsoft_oauth_next' not in client.session


# -- the real round trip, with Authlib validating a signed ID token ---------------------------


@pytest.fixture
def signing_key():
    return RSAKey.generate_key(2048, parameters={'kid': 'test-key', 'use': 'sig'})


def _id_token(key, claims):
    return jwt.encode({'alg': 'RS256', 'kid': 'test-key'}, claims, key)


def _round_trip(client, key, **overrides):
    """Login, then a callback whose ID token is really signed and really validated by Authlib: only
    the network is faked (discovery, the key set and the code exchange)."""
    with _metadata():
        location = client.get(reverse('microsoft_auth:login'))['Location']
    query = parse_qs(urlsplit(location).query)
    (state,) = query['state']
    (nonce,) = query['nonce']
    claims = {
        **_claims(),
        'aud': CLIENT_ID,
        'exp': 4_000_000_000,
        'iat': 1_700_000_000,
        'nonce': nonce,
        **overrides,
    }
    token = {'access_token': 'at', 'id_token': _id_token(key, claims)}

    with (
        _metadata(),
        mock.patch.object(DjangoOAuth2App, 'fetch_access_token', return_value=token) as fetch,
        mock.patch.object(
            DjangoOAuth2App, 'fetch_jwk_set', return_value={'keys': [key.as_dict(private=False)]}
        ),
    ):
        response = client.get(reverse('microsoft_auth:callback'), {'code': 'abc', 'state': state})
    return response, fetch, state


def test_a_real_id_token_from_the_common_authority_is_accepted(client, signing_key):
    """The `common` authority's discovery document names a *templated* issuer that no token
    carries; Authlib would reject every sign-in unless the issuer check is ours."""
    response, fetch, state = _round_trip(client, signing_key)

    assert response['Location'] == reverse('accounts:account')
    assert User.objects.filter(microsoft_id=PERSONAL_ID).exists()
    assert fetch.call_args.kwargs['redirect_uri'] == REDIRECT_URI
    # State is single-use: replaying the callback is refused.
    assert f'_state_microsoft_{state}' not in client.session
    replay = client.get(reverse('microsoft_auth:callback'), {'code': 'abc', 'state': state})
    assert replay['Location'] == reverse('accounts:login')


def test_a_real_id_token_for_another_client_is_refused(client, signing_key):
    response, *_ = _round_trip(client, signing_key, aud='some-other-client-id')

    assert response['Location'] == reverse('accounts:login')
    assert not User.objects.exists()


def test_a_real_id_token_with_the_wrong_nonce_is_refused(client, signing_key):
    response, *_ = _round_trip(client, signing_key, nonce='not-the-one-we-sent')

    assert response['Location'] == reverse('accounts:login')
    assert not User.objects.exists()


def test_a_real_id_token_signed_with_another_key_is_refused(client):
    other_key = RSAKey.generate_key(2048, parameters={'kid': 'test-key', 'use': 'sig'})
    with _metadata():
        location = client.get(reverse('microsoft_auth:login'))['Location']
    query = parse_qs(urlsplit(location).query)
    claims = {
        **_claims(),
        'aud': CLIENT_ID,
        'exp': 4_000_000_000,
        'iat': 1_700_000_000,
        'nonce': query['nonce'][0],
    }
    token = {'access_token': 'at', 'id_token': _id_token(other_key, claims)}
    trusted = RSAKey.generate_key(2048, parameters={'kid': 'test-key', 'use': 'sig'})

    with (
        _metadata(),
        mock.patch.object(DjangoOAuth2App, 'fetch_access_token', return_value=token),
        mock.patch.object(
            DjangoOAuth2App,
            'fetch_jwk_set',
            return_value={'keys': [trusted.as_dict(private=False)]},
        ),
    ):
        response = client.get(
            reverse('microsoft_auth:callback'), {'code': 'abc', 'state': query['state'][0]}
        )

    assert response['Location'] == reverse('accounts:login')
    assert not User.objects.exists()


def test_a_real_id_token_whose_issuer_disagrees_with_its_tenant_is_refused(client, signing_key):
    response, *_ = _round_trip(
        client, signing_key, iss=f'https://login.microsoftonline.com/{WORK_TENANT_ID}/v2.0'
    )

    _refused(client, response, microsoft_oauth.InvalidClaims.user_message)
    assert not User.objects.exists()


# -- rate limiting ----------------------------------------------------------------------------


def test_callback_is_rate_limited_per_ip(client, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'MICROSOFT_LOGIN_IP': (2, 300)}

    for _ in range(2):
        assert client.get(reverse('microsoft_auth:callback'), {'error': 'x'}).status_code == 302
    response = client.get(reverse('microsoft_auth:callback'), {'error': 'x'})

    assert response.status_code == 429
    assert 'Retry-After' in response


# -- the system check and the admin -----------------------------------------------------------


@pytest.mark.parametrize(
    'values',
    [
        ('id', '', ''),
        ('', 'secret', ''),
        ('', '', 'https://example.com/auth/microsoft/callback'),
        ('id', 'secret', ''),
    ],
)
def test_check_warns_about_a_partial_configuration(settings, values):
    (
        settings.MICROSOFT_CLIENT_ID,
        settings.MICROSOFT_CLIENT_SECRET,
        settings.MICROSOFT_REDIRECT_URI,
    ) = values

    (warning,) = accounts_checks.check_microsoft_oauth()

    assert isinstance(warning, checks.Warning)
    assert warning.id == 'accounts.W004'
    for name, value in zip(
        ('MICROSOFT_CLIENT_ID', 'MICROSOFT_CLIENT_SECRET', 'MICROSOFT_REDIRECT_URI'),
        values,
        strict=True,
    ):
        assert (name in warning.msg) == (value == '')


def test_check_is_quiet_when_fully_configured_or_not_at_all(settings, unconfigured):
    assert accounts_checks.check_microsoft_oauth() == []

    settings.MICROSOFT_CLIENT_ID = 'id'
    settings.MICROSOFT_CLIENT_SECRET = 'secret'
    settings.MICROSOFT_REDIRECT_URI = REDIRECT_URI
    assert accounts_checks.check_microsoft_oauth() == []


def test_check_is_registered_with_django(settings):
    settings.MICROSOFT_CLIENT_ID = 'id'
    settings.MICROSOFT_CLIENT_SECRET = ''
    settings.MICROSOFT_REDIRECT_URI = ''

    assert 'accounts.W004' in {m.id for m in checks.run_checks()}


def test_admin_shows_microsoft_id_read_only(client, admin_user, user):
    user.microsoft_id = PERSONAL_ID
    user.save(update_fields=['microsoft_id'])
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:accounts_user_change', args=[user.pk]))

    content = response.content.decode()
    assert PERSONAL_ID in content
    assert 'name="microsoft_id"' not in content


# -- the pieces -------------------------------------------------------------------------------


def test_get_client_requires_all_three_settings(settings):
    settings.MICROSOFT_REDIRECT_URI = ''

    with pytest.raises(microsoft_oauth.MicrosoftNotConfigured):
        microsoft_oauth.get_client()


def test_get_client_registers_microsoft_with_discovery_and_scope():
    client = microsoft_oauth.get_client()

    assert client.client_id == CLIENT_ID
    assert client.client_secret == 'test-client-secret'
    assert client._server_metadata_url == (
        'https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration'
    )
    assert client.client_kwargs['scope'] == 'openid email profile'
    assert client.client_kwargs['token_endpoint_auth_method'] == 'client_secret_post'
    # Built once and reused, so the discovery document is cached.
    assert microsoft_oauth.get_client() is client


@pytest.mark.parametrize('tenant', ['consumers', 'organizations', WORK_TENANT_ID])
def test_get_client_uses_the_configured_tenant(settings, tenant):
    settings.MICROSOFT_TENANT = tenant

    client = microsoft_oauth.get_client()

    assert client._server_metadata_url == (
        f'https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration'
    )


def test_get_client_falls_back_to_common_when_the_tenant_is_empty(settings):
    settings.MICROSOFT_TENANT = ''

    assert microsoft_oauth.get_client()._server_metadata_url.startswith(
        'https://login.microsoftonline.com/common/'
    )


def test_identity_from_claims_trims_and_caps_the_name():
    identity = identity_from_claims(_claims(name='  ' + 'x' * 300 + '  '))

    assert identity.name == 'x' * 255


def test_identity_from_claims_reads_the_tenant_and_the_domain_flag():
    identity = identity_from_claims(_work_claims())

    assert identity.tenant_id == WORK_TENANT_ID
    assert identity.email_domain_verified is True
    assert not identity.is_personal_account
    personal = identity_from_claims(_claims())
    assert personal.is_personal_account
    assert personal.email_domain_verified is False
    # The tenant id compares case-insensitively.
    upper = identity_from_claims(_claims(tid=PERSONAL_ACCOUNT_TENANT_ID.upper()))
    assert upper.is_personal_account


def test_identity_from_claims_uses_preferred_username_for_a_personal_account_only():
    personal = _claims(preferred_username='someone@outlook.com')
    del personal['email']
    assert identity_from_claims(personal).email == 'someone@outlook.com'

    work = _work_claims(preferred_username='victim@contoso.com', upn='victim@contoso.com')
    del work['email']
    assert identity_from_claims(work).email == ''


def test_account_id_is_the_tenant_and_object_id_lower_cased():
    identity = identity_from_claims(_claims(oid='ABCDEF00-0000-0000-0000-000000000001'))

    assert (
        identity.account_id == f'{PERSONAL_ACCOUNT_TENANT_ID}:abcdef00-0000-0000-0000-000000000001'
    )


def test_the_mail_domain_table_is_well_formed():
    brands = {'outlook', 'hotmail', 'live', 'msn', 'windowslive'}
    for domain in microsoft_oauth.MICROSOFT_MAIL_DOMAINS:
        assert domain == domain.lower()
        assert domain.split('.')[0] in brands
        assert '.' in domain
    # Deliberately not a pattern: a country suffix that is not listed does not match.
    assert 'msn.cn' not in microsoft_oauth.MICROSOFT_MAIL_DOMAINS
    assert 'hotmail.xx' not in microsoft_oauth.MICROSOFT_MAIL_DOMAINS
