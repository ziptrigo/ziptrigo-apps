"""Which addresses have ever been confirmed on an account (issue #160).

`billing` uses it to tell a genuinely new account from someone returning with an address, or a
variant of an address, that already had one: the welcome credits go to the first holder only.

**Canonical form** (`canonical_email`): lower-cased; everything from the first `+` in the local
part dropped, for every domain (`email+a@x.com`, `email+b+c@x.com` and `email@x.com` are one
address); dots dropped from the local part and `googlemail.com` read as `gmail.com`, for Gmail
only (Gmail ignores dots, other providers do not). It only ever *refuses* a grant, it never merges
accounts, so being generous here costs a user at worst the welcome credits.

**What is stored** (`KnownEmail`): `HMAC-SHA256(SECRET_KEY, canonical)`, hex, plus the id of the
first account to confirm it. Rotating `SECRET_KEY` makes every stored fingerprint stop matching,
which silently makes old addresses new again; the migration `0006_known_emails` backfilled with the
key of the day. Keep that in mind before rotating.

**When it is written**: when an address is *confirmed*, not at signup. An unconfirmed signup proves
nothing about the address, and if it claimed it, anyone could register a victim's address and have
the owner refused later. The places an address becomes confirmed are `send_email_confirmed` (the
emailed link, a password reset, `inv users confirm`) and the creation of an account that is
already confirmed ("Sign in with Google/Microsoft", `create_user --confirm`), so those call
`remember`. Claiming is idempotent and first-come: the second account to confirm an address finds
the row and is not its holder. The migration's backfill covers existing confirmed accounts (an
address an existing account has already moved away from is gone and cannot be recovered).
"""

import hashlib
import hmac
import logging

from django.conf import settings
from django.db import IntegrityError, transaction

from ..models import KnownEmail, User

#: Domains where dots in the local part are ignored, and which are the same mailbox.
GMAIL_DOMAINS = frozenset({'gmail.com', 'googlemail.com'})

logger = logging.getLogger(__name__)

_PREFIX = b'known-email:'


def canonical_email(email: str) -> str:
    """The form two addresses of one person share (see the module docstring)."""
    email = email.strip().lower()
    local, at, domain = email.rpartition('@')
    if not at or not local:
        return email
    base = local.split('+', 1)[0] or local
    if domain in GMAIL_DOMAINS:
        base = base.replace('.', '') or base
        domain = 'gmail.com'
    return f'{base}@{domain}'


def fingerprint(email: str) -> str:
    """The keyed hash stored for `email`, over its canonical form."""
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, _PREFIX + canonical_email(email).encode(), hashlib.sha256).hexdigest()


def remember(user: User) -> bool:
    """Record `user`'s current address as confirmed, if no account has it yet, and say whether
    `user` is its holder: true for the first account to confirm it (also on a repeat call), false
    when another account got there first. Safe to call any number of times, and concurrently: the
    primary key decides a race."""
    key = fingerprint(user.email)
    row = KnownEmail.objects.filter(pk=key).first()
    if row is None:
        try:
            with transaction.atomic():
                KnownEmail.objects.create(fingerprint=key, first_user_id=user.pk)
            return True
        except IntegrityError:
            row = KnownEmail.objects.get(pk=key)
    return row.first_user_id == user.pk


def remember_quietly(user: User) -> None:
    """`remember` for a caller that must not fail because of it (the confirmation paths, the
    creation of an account): in its own savepoint, since on Postgres a database error that is merely
    caught still aborts the transaction, and logged with the user's id. `billing.welcome.settle`
    asks again before it grants anything, so a miss here costs nothing for an account with a
    welcome row."""
    try:
        with transaction.atomic():
            remember(user)
    except Exception:
        logger.exception('Could not remember the confirmed address of user %s', user.pk)
