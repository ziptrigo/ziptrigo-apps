"""Sign in with Google (issue #91): a server-side OpenID Connect authorization code flow, built on
Authlib's Django client.

This module holds everything about the flow that isn't an HTTP request/response: the lazily built
Authlib client, turning the ID token's claims into a `GoogleIdentity`, and deciding which `User`
that identity is -- the part with the account-takeover considerations, hence the long docstring on
`resolve_user`. `apps.accounts.views.google` is the thin layer around it (state handling and the
redirects).

The web UI signs in with a Django session (CLAUDE.md, "Auth"), so Google sign-in does too: the
callback ends in the same `django.contrib.auth.login()` the password form uses, and no JWT is
involved. The feature is *enabled* only when `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`GOOGLE_REDIRECT_URI` are all set (`is_configured`); the views and templates check that rather
than assuming it, so an environment without a Google client simply doesn't offer the button.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.db import IntegrityError, transaction

from ..models import User

logger = logging.getLogger(__name__)

PROVIDER = 'google'
DISCOVERY_URL = 'https://accounts.google.com/.well-known/openid-configuration'
SCOPE = 'openid email profile'
#: Authlib makes its discovery, JWKS and token requests with no timeout unless asked to, which
#: would let a stalled Google endpoint hold a gunicorn worker indefinitely.
REQUEST_TIMEOUT_SECONDS = 10

#: The backend `login()` records on the session. There is only one, but naming it explicitly keeps
#: `login()` working if a second backend is ever added (it refuses to guess with several).
LOGIN_BACKEND = 'apps.accounts.backends.EmailBackend'

INACTIVE_MESSAGE = 'User not active'
CONFLICT_MESSAGE = (
    "We couldn't sign you in with this Google account because the email address is already "
    'linked to a different one. Sign in with your password instead.'
)
UNVERIFIED_MESSAGE = (
    "Google hasn't verified the email address of this Google account, so we can't sign you in "
    'with it. Verify the address with Google, or sign in with your email and password.'
)


class GoogleSignInError(Exception):
    """A sign-in that can't proceed for a reason worth telling the user about.

    `user_message` is written for the user and safe to show; the exception's own text is not.
    """

    user_message = "We couldn't sign you in with Google. Please try again."

    def __init__(self, user_message: str | None = None):
        super().__init__(user_message or self.user_message)
        if user_message:
            self.user_message = user_message


class GoogleNotConfigured(GoogleSignInError):
    user_message = 'Google sign-in is not available right now.'


class InvalidClaims(GoogleSignInError):
    """The ID token lacked a usable `sub` or `email`."""


class UnverifiedEmail(GoogleSignInError):
    """Google says the account's email address isn't verified."""

    user_message = UNVERIFIED_MESSAGE


class InactiveAccount(GoogleSignInError):
    """The matched account is not active -- refused like the password login refuses it."""

    user_message = INACTIVE_MESSAGE


class AccountConflict(GoogleSignInError):
    """The email belongs to an account that can't safely be linked to this Google identity."""

    user_message = CONFLICT_MESSAGE


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """What we use of the ID token's claims."""

    sub: str
    email: str
    email_verified: bool
    name: str


def is_configured() -> bool:
    """Whether all three `GOOGLE_*` settings are set, i.e. whether Google sign-in is offered."""
    return bool(
        settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REDIRECT_URI
    )


#: At most one entry, keyed on the credentials it was built with: keeping the client around is
#: what lets Authlib cache Google's discovery document and signing keys instead of fetching them
#: on every sign-in, while keying on the settings keeps `override_settings` honest in tests.
_client_cache: dict[tuple[str, str], Any] = {}


def get_client() -> Any:
    """The Authlib client for Google (`authorize_redirect` / `authorize_access_token`).

    Built lazily, on first use rather than at import, so the settings are read when needed and a
    missing configuration is a clear `GoogleNotConfigured` instead of an import-time failure.

    Raises:
        GoogleNotConfigured: one of the `GOOGLE_*` settings is empty.
    """
    if not is_configured():
        raise GoogleNotConfigured()

    key = (settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET)
    client = _client_cache.get(key)
    if client is None:
        oauth = OAuth()
        client = oauth.register(
            name=PROVIDER,
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            server_metadata_url=DISCOVERY_URL,
            client_kwargs={'scope': SCOPE, 'default_timeout': REQUEST_TIMEOUT_SECONDS},
        )
        _client_cache.clear()
        _client_cache[key] = client
    return client


def _is_true(value: object) -> bool:
    """`email_verified` is a JSON boolean in an ID token, but Google has returned it as the string
    `"true"` from some endpoints; accept both, and nothing else."""
    return value is True or (isinstance(value, str) and value.lower() == 'true')


def identity_from_claims(claims: Mapping[str, Any]) -> GoogleIdentity:
    """Read the claims of a validated ID token (Authlib has already checked its signature,
    issuer, audience, expiry and nonce).

    Raises:
        InvalidClaims: `sub` or `email` is missing.
    """
    sub = claims.get('sub')
    email = claims.get('email')
    if not isinstance(sub, str) or not sub or not isinstance(email, str) or not email:
        raise InvalidClaims()

    name = claims.get('name')
    if not isinstance(name, str) or not name.strip():
        given = claims.get('given_name')
        family = claims.get('family_name')
        name = ' '.join(p.strip() for p in (given, family) if isinstance(p, str) and p.strip())
    return GoogleIdentity(
        sub=sub,
        email=email.strip(),
        email_verified=_is_true(claims.get('email_verified')),
        name=name.strip()[:255],
    )


def resolve_user(identity: GoogleIdentity) -> User:
    """The user this Google identity signs in as: found, linked, or created.

    1. Refuse unless Google verified the email address -- otherwise anyone could claim someone
       else's address on a Google account and be matched to their account below.
    2. Match by `google_sub` -- stable, unlike the address, which a Google account can change.
    3. Otherwise match an existing account by email (case-insensitive) and link it, storing the
       `sub`. **Pre-hijacking guard**: when that account's email was never confirmed, whoever
       registered it did so with their own password and without proving they own the address (the
       real owner may be signing in with Google right now). Google has just proven the owner, so
       the account becomes confirmed *and its password unusable* -- the registrant's credentials
       must not survive a takeover of the account by its rightful owner. An account whose email
       was already confirmed keeps its password: its owner has proven the address before.
    4. Otherwise create a confirmed account with an unusable password (there is nothing to type;
       "forgot password" can still set one). No confirmation email: Google already did that job.

    An account already linked to a *different* `sub`, or two accounts differing only by email
    case, are refused (`AccountConflict`) rather than guessed at. Inactive accounts are refused
    before anything is written.

    Raises:
        UnverifiedEmail, InactiveAccount, AccountConflict: see above. All are `GoogleSignInError`s
            with a `user_message`.
    """
    if not identity.email_verified:
        raise UnverifiedEmail()

    try:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(google_sub=identity.sub).first()
            if user is None:
                user = _link_or_create(identity)
            elif user.status != User.STATUS_ACTIVE or not user.is_active:
                raise InactiveAccount()
            return user
    except IntegrityError:
        # A concurrent sign-in for the same address/`sub` won the race for a unique constraint.
        # Rare, and a retry (which then finds the winner's row) is the right answer.
        logger.warning('Google sign-in lost a race creating or linking an account')
        raise GoogleSignInError() from None


def _link_or_create(identity: GoogleIdentity) -> User:
    matches = list(User.objects.select_for_update().filter(email__iexact=identity.email)[:2])
    if len(matches) > 1:
        logger.warning('Google sign-in matched more than one account by email')
        raise AccountConflict()
    if matches:
        return _link(matches[0], identity)

    now = datetime.now(UTC)
    # `create_user` with no password stores an unusable one.
    return User.objects.create_user(
        email=identity.email,
        name=identity.name,
        email_confirmed=True,
        email_confirmed_at=now,
        google_sub=identity.sub,
    )


def _link(user: User, identity: GoogleIdentity) -> User:
    if user.google_sub and user.google_sub != identity.sub:
        logger.warning(
            'Google sign-in refused: account %s is linked to another Google account', user.pk
        )
        raise AccountConflict()
    if user.status != User.STATUS_ACTIVE or not user.is_active:
        raise InactiveAccount()

    user.google_sub = identity.sub
    update_fields = ['google_sub', 'updated_at']
    if not user.email_confirmed:
        user.set_unusable_password()
        user.email_confirmed = True
        user.email_confirmed_at = datetime.now(UTC)
        update_fields += ['password', 'email_confirmed', 'email_confirmed_at']
    user.save(update_fields=update_fields)
    return user
