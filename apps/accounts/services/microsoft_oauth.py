"""Sign in with Microsoft (issue #156): a server-side OpenID Connect authorization code flow, built
on Authlib's Django client, against the Microsoft identity platform (Microsoft Entra ID).

It is the twin of `google_oauth` and ends the same way: the callback finishes in Django's own
`login()`, so this is a session login, not a JWT one. What differs is the identity, because a
Microsoft ID token is not as trustworthy about the *email address* as Google's:

* There is no `email_verified` claim.
* In a work/school (Entra) tenant the `email` claim is whatever a tenant admin set; nothing proves
  the person owns that mailbox (the "nOAuth" account takeover: an attacker makes their own tenant,
  sets a victim's address on a user, and signs in with it). Microsoft's guidance is to identify a
  user by `sub` or `oid`, never by email.

So sign-in matches on the user's tenant and object id (`tid` + `oid`, see `MicrosoftIdentity.
account_id`) first, and the email is used to *link* an existing account or *create* a new one only
when Microsoft can vouch for it (`_vouches_for_email`). `resolve_user` has the rules, with the
reasons. `sub` is deliberately not the key: it is unique per user *and per app registration*, so
recreating the registration would give every linked user a new one and lock them out, whereas
`oid` is the same for every app.

The feature is *enabled* only when `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` and
`MICROSOFT_REDIRECT_URI` are all set (`is_configured`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from ..models import User

logger = logging.getLogger(__name__)

PROVIDER = 'microsoft'
AUTHORITY = 'https://login.microsoftonline.com'
SCOPE = 'openid email profile'
#: Authlib makes its discovery, JWKS and token requests with no timeout unless asked to, which
#: would let a stalled endpoint hold a gunicorn worker indefinitely.
REQUEST_TIMEOUT_SECONDS = 10

#: The tenant id every personal Microsoft account (Outlook, Hotmail, Live) signs in under: the
#: `tid` claim of its ID token.
PERSONAL_ACCOUNT_TENANT_ID = '9188040d-6c67-4c5b-b112-36a304b66dad'

#: Mailbox domains Microsoft itself hosts for personal accounts, per brand. An explicit list, not a
#: pattern: a pattern over country suffixes would also match domains Microsoft doesn't run (and
#: someone else might own), and these decide whose address a Microsoft sign-in may take over. A
#: Microsoft domain missing here **fails closed** (such a user can't create or link an account by
#: email and is pointed at their password or Google), so extend it when one turns up.
_MICROSOFT_MAIL_SUFFIXES: dict[str, str] = {
    'outlook': (
        'com at be cl co.id co.il co.th com.ar com.au com.br com.tr com.vn cz de dk es fr hu ie in '
        'it jp kr lv my ph pt sa sg sk'
    ),
    'hotmail': (
        'com at be ch co.il co.jp co.th co.uk com.ar com.au com.br com.tr de dk es fi fr gr ie it '
        'nl no pt se'
    ),
    'live': (
        'com at be ca cn co.uk co.za com.ar com.au com.mx com.pt com.sg de dk fr hk ie in it jp nl '
        'no ru se'
    ),
    'msn': 'com',
    'windowslive': 'com',
}
MICROSOFT_MAIL_DOMAINS = frozenset(
    f'{brand}.{suffix}'
    for brand, suffixes in _MICROSOFT_MAIL_SUFFIXES.items()
    for suffix in suffixes.split()
)

#: The backend `login()` records on the session (see `google_oauth.LOGIN_BACKEND`).
LOGIN_BACKEND = 'apps.accounts.backends.EmailBackend'

INACTIVE_MESSAGE = 'User not active'
CONFLICT_MESSAGE = (
    "We couldn't sign you in with this Microsoft account because the email address is already "
    'linked to a different one. Sign in with your password instead.'
)
UNCONFIRMED_ACCOUNT_MESSAGE = (
    "An account with this email already exists but hasn't been confirmed yet. Confirm it from "
    'the email we sent, or reset your password, then try again.'
)
UNVERIFIED_MESSAGE = (
    "Microsoft can't vouch for the email address of this Microsoft account, so we can't sign you "
    'in with it. Sign in with your email and password, or with Google.'
)
NO_EMAIL_MESSAGE = (
    "This Microsoft account didn't share an email address with us, so we can't create an account "
    'from it. Sign in with your email and password, or with Google.'
)


class MicrosoftSignInError(Exception):
    """A sign-in that can't proceed for a reason worth telling the user about.

    `user_message` is written for the user and safe to show; the exception's own text is not.
    """

    user_message = "We couldn't sign you in with Microsoft. Please try again."

    def __init__(self, user_message: str | None = None):
        super().__init__(user_message or self.user_message)
        if user_message:
            self.user_message = user_message


class MicrosoftNotConfigured(MicrosoftSignInError):
    user_message = 'Microsoft sign-in is not available right now.'


class InvalidClaims(MicrosoftSignInError):
    """The ID token lacked a usable `tid` or `oid`, or its issuer didn't match its tenant."""


class NoEmail(MicrosoftSignInError):
    """The account is new to us and the ID token carried no email address to create it with."""

    user_message = NO_EMAIL_MESSAGE


class UnverifiedEmail(MicrosoftSignInError):
    """Microsoft can't vouch for the account's email address (see `_vouches_for_email`)."""

    user_message = UNVERIFIED_MESSAGE


class InactiveAccount(MicrosoftSignInError):
    """The matched account is not active (`status` or `is_active`)."""

    user_message = INACTIVE_MESSAGE


class AccountConflict(MicrosoftSignInError):
    """The email belongs to an account that can't safely be linked to this Microsoft identity."""

    user_message = CONFLICT_MESSAGE


class UnconfirmedAccountConflict(AccountConflict):
    """The email belongs to an account whose address was never (or is no longer) confirmed."""

    user_message = UNCONFIRMED_ACCOUNT_MESSAGE


@dataclass(frozen=True, slots=True)
class MicrosoftIdentity:
    """What we use of the ID token's claims."""

    #: The `tid` claim: the tenant the account signed in under.
    tenant_id: str
    #: The `oid` claim: the user's object id, the same for every app, unique within the tenant.
    object_id: str
    #: The address to match or create an account by: a well-formed `email` claim ('' when the token
    #: has none or an unusable one: it is an optional claim for work accounts), or, for a personal
    #: account only, its sign-in name (see `identity_from_claims`).
    email: str
    #: The `xms_edov` optional claim: the tenant says the email's domain is verified as theirs.
    email_domain_verified: bool
    name: str

    @property
    def is_personal_account(self) -> bool:
        return self.tenant_id.lower() == PERSONAL_ACCOUNT_TENANT_ID

    @property
    def account_id(self) -> str:
        """What `User.microsoft_id` stores: `<tid>:<oid>`, lower-cased. An `oid` is unique only
        within its tenant (a guest has a different one in each), so the tenant is part of it."""
        return f'{self.tenant_id}:{self.object_id}'.lower()


def is_configured() -> bool:
    """Whether all three `MICROSOFT_*` settings are set, i.e. whether the sign-in is offered."""
    return bool(
        settings.MICROSOFT_CLIENT_ID
        and settings.MICROSOFT_CLIENT_SECRET
        and settings.MICROSOFT_REDIRECT_URI
    )


def discovery_url(tenant: str) -> str:
    return f'{AUTHORITY}/{tenant}/v2.0/.well-known/openid-configuration'


#: At most one entry, keyed on what the client was built with: keeping it around lets Authlib
#: cache the discovery document and signing keys, while keying on the settings keeps
#: `override_settings` honest in tests.
_client_cache: dict[tuple[str, str, str], Any] = {}


def get_client() -> Any:
    """The Authlib client for Microsoft (`authorize_redirect` / `authorize_access_token`).

    Built lazily, so a missing configuration is a clear `MicrosoftNotConfigured` rather than an
    import-time failure.

    Raises:
        MicrosoftNotConfigured: one of the `MICROSOFT_*` settings is empty.
    """
    if not is_configured():
        raise MicrosoftNotConfigured()

    tenant = settings.MICROSOFT_TENANT or 'common'
    key = (settings.MICROSOFT_CLIENT_ID, settings.MICROSOFT_CLIENT_SECRET, tenant)
    client = _client_cache.get(key)
    if client is None:
        oauth = OAuth()
        client = oauth.register(
            name=PROVIDER,
            client_id=settings.MICROSOFT_CLIENT_ID,
            client_secret=settings.MICROSOFT_CLIENT_SECRET,
            server_metadata_url=discovery_url(tenant),
            client_kwargs={
                'scope': SCOPE,
                'default_timeout': REQUEST_TIMEOUT_SECONDS,
                # In the request body, as Microsoft documents it.
                'token_endpoint_auth_method': 'client_secret_post',
            },
        )
        _client_cache.clear()
        _client_cache[key] = client
    return client


#: Passed to `authorize_access_token`. Authlib would otherwise require the ID token's `iss` to
#: equal the discovery document's `issuer`, which for `common`, `organizations` and `consumers`
#: is a template (`https://login.microsoftonline.com/{tenantid}/v2.0`) that no token carries.
#: `identity_from_claims` checks the issuer against the token's own `tid` instead. Authlib still
#: validates the signature, audience, expiry, nonce and that `iss` is present.
CLAIMS_OPTIONS: dict[str, Any] = {'iss': {'essential': True}}


def _is_true(value: object) -> bool:
    """`xms_edov` is a JSON boolean, but Microsoft has emitted it as `"1"`/`"true"`; accept those
    and nothing else."""
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() in {'true', '1'}


def _well_formed_email(value: object) -> str:
    """`value` stripped, if it is a well-formed email address, else ''. The claim is checked here
    so nothing downstream sees `live.com` (no `@`) or a trailing newline as an address."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    try:
        validate_email(value)
    except ValidationError:
        return ''
    return value


def identity_from_claims(claims: Mapping[str, Any]) -> MicrosoftIdentity:
    """Read the claims of a validated ID token (Authlib has already checked its signature,
    audience, expiry and nonce).

    The issuer is checked here: a v2.0 token's `iss` is `https://login.microsoftonline.com/<tid>/
    v2.0` for the `tid` it carries, so a token whose issuer and tenant disagree is refused.

    The address is the `email` claim when it is well-formed. A *personal* account's token may
    carry its address only as `preferred_username` (its sign-in name, which Microsoft assigns: no
    tenant admin can set it), so that is used as a fallback for the personal tenant alone. For a
    work/school account `preferred_username` and `upn` are never used: a tenant admin sets them,
    so they prove nothing (and `_vouches_for_email` would refuse them anyway).

    Raises:
        InvalidClaims: `tid` or `oid` is missing, or `iss` doesn't match `tid`.
    """
    tid = claims.get('tid')
    oid = claims.get('oid')
    if not isinstance(tid, str) or not tid or not isinstance(oid, str) or not oid:
        raise InvalidClaims()
    if claims.get('iss') != f'{AUTHORITY}/{tid}/v2.0':
        raise InvalidClaims()

    email = _well_formed_email(claims.get('email'))
    if not email and tid.lower() == PERSONAL_ACCOUNT_TENANT_ID:
        email = _well_formed_email(claims.get('preferred_username'))
    name = claims.get('name')
    return MicrosoftIdentity(
        tenant_id=tid,
        object_id=oid,
        email=email,
        email_domain_verified=_is_true(claims.get('xms_edov')),
        name=name.strip()[:255] if isinstance(name, str) else '',
    )


def _vouches_for_email(identity: MicrosoftIdentity) -> bool:
    """Whether Microsoft, not just "a tenant admin typed it", vouches for this email address.

    * A personal Microsoft account: only an address on a domain Microsoft hosts for them
      (`MICROSOFT_MAIL_DOMAINS`: `outlook.com`, `hotmail.com`, `live.com`, `msn.com` and their
      country variants). A personal account can carry a third-party address that was verified
      once, at signup, and may since have changed hands, so that isn't enough to take over an
      existing account.
    * A work/school account: only with the `xms_edov` claim (Microsoft's "email domain owner
      verified", an optional claim that has to be added to the app registration's ID token).
      Without it the `email` is just text an admin set.

    `preferred_username` and `upn` are mutable display values and never vouch for anything; the
    one use of `preferred_username` (a personal account's sign-in name, when it has no `email`) is
    in `identity_from_claims`, and still has to pass the mail-domain rule here.
    """
    # Whatever built the identity, only a whole, well-formed address can be vouched for (the
    # claim is already checked in `identity_from_claims`; this keeps the rule safe on its own).
    if _well_formed_email(identity.email) != identity.email:
        return False
    domain = identity.email.rpartition('@')[2]
    if identity.is_personal_account:
        return domain.lower() in MICROSOFT_MAIL_DOMAINS
    return identity.email_domain_verified


def resolve_user(identity: MicrosoftIdentity) -> User:
    """The user this Microsoft identity signs in as: found, linked, or created.

    1. Match by `microsoft_id` (`<tid>:<oid>`). **This needs neither an email nor
       `email_confirmed`**: the tenant and object id are the proof, so a user who is already linked keeps signing in even if the token no longer
       carries a (vouched) email, or the address was changed and un-confirmed through
       `PUT /api/account`.
    2. Otherwise the email decides, and it has to be one Microsoft vouches for
       (`_vouches_for_email`) -- else `NoEmail` (the token had none) or `UnverifiedEmail`, with
       nothing written. This covers creating an account too: a new account is confirmed and has
       no password, so an address nobody proved would be a pre-hijack of whoever owns it.
    3. Match an existing account by email (case-insensitive) and link it, storing the `microsoft_id`, only
       when its email is **confirmed**. An unconfirmed account's holder never proved the address
       and may hold a password, or JWTs that outlive an email change, that a Microsoft sign-in
       would inherit (`UnconfirmedAccountConflict`). Linking changes nothing but `microsoft_id`:
       the password stays, nothing is confirmed.
    4. Otherwise create a confirmed account with an unusable password (there is nothing to type;
       "forgot password" can still set one). No confirmation email.

    An account already linked to a *different* Microsoft identity, or two accounts differing only by email
    case, are refused (`AccountConflict`) rather than guessed at. Inactive accounts are refused
    before anything is written.

    Raises:
        NoEmail, UnverifiedEmail, InactiveAccount, AccountConflict (and its subclass): see above.
            All are `MicrosoftSignInError`s with a `user_message`.
    """
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(microsoft_id=identity.account_id).first()
            if user is None:
                user = _link_or_create(identity)
            elif user.status != User.STATUS_ACTIVE or not user.is_active:
                raise InactiveAccount()
            return user
    except IntegrityError:
        # A concurrent sign-in for the same address/identity won the race for a unique constraint.
        # Rare, and a retry (which then finds the winner's row) is the right answer.
        logger.warning('Microsoft sign-in lost a race creating or linking an account')
        raise MicrosoftSignInError() from None


def _link_or_create(identity: MicrosoftIdentity) -> User:
    if not identity.email:
        raise NoEmail()
    if not _vouches_for_email(identity):
        raise UnverifiedEmail()

    matches = list(User.objects.select_for_update().filter(email__iexact=identity.email)[:2])
    if len(matches) > 1:
        logger.warning('Microsoft sign-in matched more than one account by email')
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
        microsoft_id=identity.account_id,
    )


def _link(user: User, identity: MicrosoftIdentity) -> User:
    if user.microsoft_id and user.microsoft_id != identity.account_id:
        logger.warning(
            'Microsoft sign-in refused: account %s is linked to another Microsoft account', user.pk
        )
        raise AccountConflict()
    if user.status != User.STATUS_ACTIVE or not user.is_active:
        raise InactiveAccount()
    if not user.email_confirmed:
        logger.warning('Microsoft sign-in refused: account %s has an unconfirmed email', user.pk)
        raise UnconfirmedAccountConflict()

    user.microsoft_id = identity.account_id
    user.save(update_fields=['microsoft_id', 'updated_at'])
    return user
