"""Verifying a Google ID token handed over by a native app (issue #251).

The Android app gets an ID token on the device (Credential Manager) and trades it at `POST
/api/auth/google` for our JWTs. There is no redirect, `state` or `nonce` here: what proves the
token is ours to accept is Google's signature, the issuer, the expiry and the **audience** -- the
token must have been issued for one of our OAuth client ids (`allowed_audiences`), or any app's
token for any Google user could be replayed against us. A Credential Manager token has `aud` = the
web client id (the app's `serverClientId`) and `azp` (authorized party) = the Android client id;
when `GOOGLE_ANDROID_CLIENT_IDS` is set, `azp` must be one of them, which pins which client may
present the token (an ID token issued to the website itself is refused).

Verification is `joserfc` (Authlib's own JOSE library, a dependency already) against Google's
published key set. The key set is cached in memory for `KEYS_TTL` (Google rotates keys rarely and
serves them with a long `max-age`); a token naming a `kid` we don't have triggers one refetch, at
most every `KEYS_REFRESH_MIN_INTERVAL`, so a flood of forged `kid`s can't turn into a flood of
requests to Google. The fetch has a timeout (`REQUEST_TIMEOUT_SECONDS`) because it holds a gunicorn
worker. **If the keys cannot be fetched** the token cannot be judged: `KeysUnavailable` (the API
answers 503), unless an expired cached copy exists, which is used as a fallback (a stale key
set verifies exactly as well as a fresh one until Google rotates). `fetch_keys` is the only network
call and the seam tests patch.

The token itself is never logged, nor are its claims; only the exception type is.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests
from django.conf import settings
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet

from .google_oauth import GoogleSignInError

logger = logging.getLogger(__name__)

JWKS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
ISSUERS = ('accounts.google.com', 'https://accounts.google.com')
REQUEST_TIMEOUT_SECONDS = 5
KEYS_TTL = 60 * 60
KEYS_REFRESH_MIN_INTERVAL = 60
#: Real ID tokens are about 1 KB; refuse anything absurd before parsing it.
MAX_TOKEN_CHARS = 8192
#: Clock skew allowed on `exp` / `iat` between Google's clock and ours.
LEEWAY_SECONDS = 60

INVALID_TOKEN_MESSAGE = 'Invalid or expired Google token.'
UNAVAILABLE_MESSAGE = 'Google sign-in is not available right now.'


class InvalidIdToken(GoogleSignInError):
    """The token isn't a valid, current Google ID token for one of our clients."""

    user_message = INVALID_TOKEN_MESSAGE


class KeysUnavailable(GoogleSignInError):
    """Google's signing keys could not be fetched (and none are cached). Retry after
    `KEYS_REFRESH_MIN_INTERVAL` seconds."""

    user_message = UNAVAILABLE_MESSAGE


def allowed_audiences() -> list[str]:
    """The `aud` values an ID token may carry: `GOOGLE_CLIENT_ID` (the web client, which is what
    Credential Manager's `serverClientId` is set to) plus any `GOOGLE_ANDROID_AUDIENCES`. The extras
    are added to the web client id, never a replacement for it."""
    found = [settings.GOOGLE_CLIENT_ID, *getattr(settings, 'GOOGLE_ANDROID_AUDIENCES', [])]
    return list(dict.fromkeys(a for a in found if a))


def allowed_authorized_parties() -> list[str]:
    """`GOOGLE_ANDROID_CLIENT_IDS`: the clients whose `azp` may be on the token. Empty means `azp`
    is not checked."""
    return [a for a in getattr(settings, 'GOOGLE_ANDROID_CLIENT_IDS', []) if a]


def is_configured() -> bool:
    """Whether `POST /api/auth/google` is on: at least one allowed audience. Unlike the web flow
    this needs no client secret or redirect URI (nothing is exchanged with Google)."""
    return bool(allowed_audiences())


def fetch_keys() -> dict[str, Any]:
    """Google's current JSON Web Key Set (the one network call; tests patch it)."""
    response = requests.get(JWKS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


_lock = threading.Lock()
_keys: KeySet | None = None
_expires_at = 0.0
#: No fetch is attempted before this (monotonic) time, whatever the reason (success or failure).
_next_attempt_at = 0.0


def reset_cache() -> None:
    global _keys, _expires_at, _next_attempt_at
    with _lock:
        _keys, _expires_at, _next_attempt_at = None, 0.0, 0.0


def _load_keys(*, force: bool = False) -> KeySet:
    """The cached key set, fetched when missing, older than `KEYS_TTL`, or `force` (a `kid` we
    don't know). Attempts, failed ones included, are spaced `KEYS_REFRESH_MIN_INTERVAL` apart, so a
    Google outage or forged `kid`s cost one request a minute, not one per call. The attempt slot is
    claimed under the lock, the fetch itself runs without it (other callers keep using the cached
    set meanwhile) and the result is swapped in under it."""
    global _keys, _expires_at, _next_attempt_at
    with _lock:
        now = time.monotonic()
        if _keys is not None and now < _expires_at and not force:
            return _keys
        if now < _next_attempt_at:
            if _keys is None:
                raise KeysUnavailable()
            return _keys
        _next_attempt_at = now + KEYS_REFRESH_MIN_INTERVAL
    try:
        fetched = KeySet.import_key_set(fetch_keys())  # ty: ignore[invalid-argument-type]
    except Exception as exc:
        logger.warning('Could not fetch Google signing keys: %s', type(exc).__name__)
        with _lock:
            if _keys is None:
                raise KeysUnavailable() from None
            return _keys
    with _lock:
        _keys = fetched
        _expires_at = now + KEYS_TTL
    return fetched


def _decode(token: str, keys: KeySet) -> jwt.Token:
    return jwt.decode(token, keys, algorithms=['RS256'])


def verify(id_token: str) -> dict[str, Any]:
    """The claims of a valid Google ID token for one of our client ids.

    Raises:
        InvalidIdToken: malformed, wrongly signed, expired, wrong issuer or audience.
        KeysUnavailable: Google's keys could not be fetched.
    """
    if not id_token or len(id_token) > MAX_TOKEN_CHARS:
        raise InvalidIdToken()
    audiences = allowed_audiences()
    if not audiences:
        raise InvalidIdToken()

    keys = _load_keys()
    try:
        try:
            decoded = _decode(id_token, keys)
        except InvalidKeyIdError:
            # A key Google rotated in since we cached the set.
            decoded = _decode(id_token, _load_keys(force=True))
        registry = jwt.JWTClaimsRegistry(
            now=int(time.time()),
            leeway=LEEWAY_SECONDS,
            iss={'essential': True, 'values': list(ISSUERS)},
            sub={'essential': True},
            exp={'essential': True},
            iat={'essential': False},
            nbf={'essential': False},
        )
        registry.validate(decoded.claims)
        # `aud` is a string or a list; joserfc's `values` option only handles the string case.
        aud = decoded.claims.get('aud')
        token_audiences = [aud] if isinstance(aud, str) else aud
        if not isinstance(token_audiences, list) or not set(token_audiences) & set(audiences):
            raise InvalidIdToken()
        parties = allowed_authorized_parties()
        if parties and decoded.claims.get('azp') not in parties:
            raise InvalidIdToken()
    except GoogleSignInError:
        raise
    except (JoseError, ValueError, TypeError) as exc:
        logger.info('Google ID token refused: %s', type(exc).__name__)
        raise InvalidIdToken() from None
    return dict(decoded.claims)
