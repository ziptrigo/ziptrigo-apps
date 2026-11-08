"""`POST /api/auth/google` (issue #251): a Google ID token from the Android app for our JWTs.

A real RS256 JWT is signed with a test key and verified by the real code; only the fetch of
Google's key set (`google_id_token.fetch_keys`) is replaced.
"""

import time
from datetime import UTC, datetime
from unittest import mock

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from apps.accounts.models import RefreshToken, User
from apps.accounts.services import google_id_token
from apps.accounts.tokens import CustomAccessToken, CustomRefreshToken

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

URL = '/api/auth/google'
CLIENT_ID = 'web-client.apps.googleusercontent.com'
SUB = '1098765432109876543210'
NO_ACCOUNT = 'No ZipTrigo account uses this Google account. Create one at app.ziptrigo.com first.'
INVALID = 'Invalid or expired Google token.'


@pytest.fixture
def key():
    return RSAKey.generate_key(2048, parameters={'kid': 'k1', 'use': 'sig'})


@pytest.fixture(autouse=True)
def configured(settings, key):
    settings.GOOGLE_CLIENT_ID = CLIENT_ID
    settings.GOOGLE_ANDROID_AUDIENCES = []
    google_id_token.reset_cache()
    with mock.patch.object(
        google_id_token, 'fetch_keys', return_value={'keys': [key.as_dict(private=False)]}
    ) as fetch:
        yield fetch
    google_id_token.reset_cache()


def _token(key, kid='k1', **overrides):
    now = int(time.time())
    claims = {
        'iss': 'https://accounts.google.com',
        'aud': CLIENT_ID,
        'sub': SUB,
        'email': 'alice@gmail.com',
        'email_verified': True,
        'name': 'Alice',
        'iat': now,
        'exp': now + 600,
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode({'alg': 'RS256', 'kid': kid}, claims, key)


def _post(client, token):
    return client.post(URL, {'id_token': token}, content_type='application/json')


def _account(email='alice@gmail.com', **fields):
    fields.setdefault('email_confirmed', True)
    fields.setdefault('email_confirmed_at', datetime.now(UTC))
    return User.objects.create_user(email=email, name='Alice', password='pw12345!x', **fields)


def test_a_linked_account_gets_tokens(client, key):
    user = _account(google_sub=SUB)

    response = _post(client, _token(key))

    assert response.status_code == 200
    data = response.json()
    assert data['token_type'] == 'Bearer'
    assert data['expires_in'] == 3600
    assert CustomAccessToken(data['access_token'])['sub'] == str(user.id)
    refresh = CustomRefreshToken(data['refresh_token'])
    assert refresh['sub'] == str(user.id)
    # Issued through `refresh_tokens.issue`, so it starts a rotation family.
    assert RefreshToken.objects.filter(jti=refresh['jti']).exists()


def test_the_tokens_work_and_refresh_rotates(client, key):
    _account(google_sub=SUB)
    data = _post(client, _token(key)).json()

    assert (
        client.get('/api/account', HTTP_AUTHORIZATION=f'Bearer {data["access_token"]}').status_code
        == 200
    )
    refreshed = client.post(
        '/api/auth/refresh',
        {'refresh_token': data['refresh_token']},
        content_type='application/json',
    )
    assert refreshed.status_code == 200
    assert refreshed.json()['refresh_token'] != data['refresh_token']


def test_an_existing_account_is_linked_by_email(client, key):
    user = _account()

    assert _post(client, _token(key)).status_code == 200

    user.refresh_from_db()
    assert user.google_sub == SUB


def test_sub_wins_over_a_different_email(client, key):
    user = _account('old@example.com', google_sub=SUB, email_confirmed=False)

    assert _post(client, _token(key)).status_code == 200
    assert User.objects.count() == 1
    assert User.objects.get().pk == user.pk


def test_the_audience_may_be_a_list(client, key):
    _account(google_sub=SUB)

    assert _post(client, _token(key, aud=['other', CLIENT_ID])).status_code == 200


def test_a_separate_android_audience(client, key, settings):
    settings.GOOGLE_ANDROID_AUDIENCES = ['android-client']
    _account(google_sub=SUB)

    assert _post(client, _token(key, aud='android-client')).status_code == 200
    response = _post(client, _token(key))  # the web client id is no longer accepted
    assert response.status_code == 400


@pytest.mark.parametrize('issuer', ['accounts.google.com', 'https://accounts.google.com'])
def test_both_google_issuers_are_accepted(client, key, issuer):
    _account(google_sub=SUB)

    assert _post(client, _token(key, iss=issuer)).status_code == 200


@pytest.mark.parametrize(
    'overrides',
    [
        {'aud': 'some-other-client'},
        {'exp': int(time.time()) - 3600},
        {'iss': 'https://evil.example.com'},
        {'iss': None},
        {'sub': None},
        {'exp': None},
    ],
    ids=['aud', 'expired', 'iss', 'no-iss', 'no-sub', 'no-exp'],
)
def test_a_bad_token_is_a_400(client, key, overrides):
    _account(google_sub=SUB)

    response = _post(client, _token(key, **overrides))

    assert response.status_code == 400
    assert response.json() == {'detail': INVALID}


def test_a_token_signed_by_another_key_is_a_400(client):
    other = RSAKey.generate_key(2048, parameters={'kid': 'k1', 'use': 'sig'})
    _account(google_sub=SUB)

    assert _post(client, _token(other)).status_code == 400


def test_garbage_is_a_400(client):
    for token in ('', 'not-a-jwt', 'a.b.c', 'x' * 20000):
        response = _post(client, token)
        assert response.status_code == 400
        assert response.json() == {'detail': INVALID}


def test_an_hs256_token_signed_with_a_public_key_is_refused(client, key):
    """The classic algorithm-confusion attack."""
    _account(google_sub=SUB)
    from joserfc.jwk import OctKey

    forged = jwt.encode(
        {'alg': 'HS256', 'kid': 'k1'},
        {
            'iss': 'https://accounts.google.com',
            'aud': CLIENT_ID,
            'sub': SUB,
            'exp': int(time.time()) + 600,
            'email': 'alice@gmail.com',
            'email_verified': True,
        },
        OctKey.import_key('w' * 40),
    )

    assert _post(client, forged).status_code == 400


def test_an_unverified_email_is_a_403(client, key):
    _account(google_sub=SUB)

    response = _post(client, _token(key, email_verified=False))

    assert response.status_code == 403
    assert response.json()['detail'].startswith("Google hasn't verified the email address")


def test_a_token_without_an_email_is_a_400(client, key):
    response = _post(client, _token(key, email=None))

    assert response.status_code == 400
    assert response.json() == {'detail': INVALID}


def test_an_unknown_identity_is_a_403_and_nothing_is_created(client, key):
    response = _post(client, _token(key))

    assert response.status_code == 403
    assert response.json() == {'detail': NO_ACCOUNT}
    assert not User.objects.exists()
    assert not RefreshToken.objects.exists()


def test_an_unconfirmed_account_is_refused(client, key):
    user = _account(email_confirmed=False, email_confirmed_at=None)

    response = _post(client, _token(key))

    assert response.status_code == 403
    assert response.json()['detail'].startswith('An account with this email already exists but')
    user.refresh_from_db()
    assert user.google_sub is None


def test_a_non_authoritative_domain_is_refused(client, key):
    user = _account('alice@example.com')

    response = _post(client, _token(key, email='alice@example.com'))

    assert response.status_code == 403
    assert response.json() == {
        'detail': 'An account with this email already exists. '
        'Sign in with your email and password instead.'
    }
    user.refresh_from_db()
    assert user.google_sub is None


def test_a_workspace_domain_may_link(client, key):
    _account('alice@example.com')

    assert (
        _post(client, _token(key, email='alice@example.com', hd='example.com')).status_code == 200
    )


def test_an_account_linked_to_another_google_identity_is_refused(client, key):
    _account(google_sub='another-sub')

    response = _post(client, _token(key))

    assert response.status_code == 403
    assert response.json()['detail'].startswith("We couldn't sign you in with this Google account")


@pytest.mark.parametrize('field', ['status', 'is_active'])
def test_an_inactive_user_is_a_403(client, key, field):
    user = _account(google_sub=SUB)
    if field == 'status':
        user.status = User.STATUS_INACTIVE
    else:
        user.is_active = False
    user.save()

    response = _post(client, _token(key))

    assert response.status_code == 403
    assert response.json() == {'detail': 'User not active'}


def test_it_does_not_fire_user_logged_in(client, key):
    from django.contrib.auth.signals import user_logged_in

    _account(google_sub=SUB)
    received = []
    user_logged_in.connect(lambda **kw: received.append(kw), weak=False, dispatch_uid='t251')
    try:
        assert _post(client, _token(key)).status_code == 200
    finally:
        user_logged_in.disconnect(dispatch_uid='t251')
    assert received == []


def test_off_while_not_configured(client, key, settings):
    settings.GOOGLE_CLIENT_ID = ''
    settings.GOOGLE_ANDROID_AUDIENCES = []
    _account(google_sub=SUB)

    response = _post(client, _token(key))

    assert response.status_code == 404


def test_needs_no_secret_or_redirect_uri(client, key, settings):
    settings.GOOGLE_CLIENT_SECRET = ''
    settings.GOOGLE_REDIRECT_URI = ''
    _account(google_sub=SUB)

    assert _post(client, _token(key)).status_code == 200


def test_the_endpoint_is_rate_limited_per_ip(client, key, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'GOOGLE_TOKEN_IP': (2, 300)}

    for _ in range(2):
        assert _post(client, 'garbage').status_code == 400
    response = _post(client, 'garbage')

    assert response.status_code == 429
    assert 'Retry-After' in response


# -- Google's keys -------------------------------------------------------------------------


def test_the_key_set_is_cached(client, key, configured):
    _account(google_sub=SUB)

    for _ in range(3):
        assert _post(client, _token(key)).status_code == 200

    assert configured.call_count == 1


def test_a_rotated_in_key_triggers_one_refetch(client, key, configured, monkeypatch):
    _account(google_sub=SUB)
    assert _post(client, _token(key)).status_code == 200
    new_key = RSAKey.generate_key(2048, parameters={'kid': 'k2', 'use': 'sig'})
    configured.return_value = {'keys': [key.as_dict(private=False), new_key.as_dict(private=False)]}
    monkeypatch.setattr(google_id_token, '_next_attempt_at', 0.0)

    assert _post(client, _token(new_key, kid='k2')).status_code == 200
    assert configured.call_count == 2


def test_an_unknown_kid_cannot_force_a_refetch_every_call(client, key, configured):
    assert _post(client, _token(key)).status_code == 403  # primes the cache
    for _ in range(5):
        assert _post(client, _token(key, kid='forged')).status_code == 400

    assert configured.call_count == 1


def test_keys_that_cannot_be_fetched_are_a_503(client, key, configured, caplog):
    configured.side_effect = ConnectionError('boom')

    response = _post(client, _token(key))

    assert response.status_code == 503
    assert response.json() == {'detail': 'Google sign-in is not available right now.'}
    # And the next call within the minute doesn't hit Google again.
    assert _post(client, _token(key)).status_code == 503
    assert configured.call_count == 1


def test_stale_keys_are_used_when_a_refresh_fails(client, key, configured, monkeypatch):
    _account(google_sub=SUB)
    assert _post(client, _token(key)).status_code == 200
    monkeypatch.setattr(google_id_token, '_expires_at', 0.0)
    monkeypatch.setattr(google_id_token, '_next_attempt_at', 0.0)
    configured.side_effect = ConnectionError('boom')

    assert _post(client, _token(key)).status_code == 200


def test_the_token_is_never_logged(client, key, caplog):
    caplog.set_level('DEBUG')
    token = _token(key, aud='nope')

    _post(client, token)

    assert token not in caplog.text
    assert token.split('.')[1] not in caplog.text
