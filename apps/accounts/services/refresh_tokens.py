"""Rotating refresh tokens (issue #252): a client in regular use never has to sign in again.

`POST /api/auth/refresh` replaces the refresh token it is given with a new one, so the 30 days
restart at every use while a token nobody uses still dies after 30. The bookkeeping is one
`RefreshToken` row per issued token (by `jti`), grouped in a **family** (everything descended from
one login):

- `issue(user)` -- a login: a new family, one row.
- `rotate(token, user)` -- a refresh. The first use of a token marks it rotated and issues its
  successor in the same family. **Within `settings.JWT_REFRESH_GRACE_SECONDS` (60) of that first
  rotation** the same token may be used again and gets another successor (an app and its widget
  racing on one token must not sign the user out); the siblings are all valid, but at most
  `MAX_SUCCESSORS` (3) per token: a fourth use is a replay too. **After the window,
  a rotated token is a replay**: either the client lost its newer token, or a copy was stolen, and
  we can't tell which, so the whole family is revoked (the real client signs in again, the thief
  can't use whichever copy of the family he holds) and `InvalidRefreshToken` is raised.
  Trade-off: replay detection only works after the window; inside it a stolen copy gets a
  sibling of its own, bounded by `MAX_SUCCESSORS`.
- A token that has no row is a **legacy** one, issued by a login before this existed: it is
  accepted once (a row is made for it, in a family of its own, as if just issued) and rotated like
  any other, so deploying this signs nobody out. The row's unique `jti` makes two concurrent first
  uses safe: the loser finds the winner's row.

The decision runs under a row lock (`select_for_update`; a no-op on SQLite) in one transaction, and
a revocation is committed before the error is raised. The caller checks the signature, the user's
state and the `pwd` fingerprint (`tokens.matches_password`) first: a password change still revokes
everything, rows or no rows. Access tokens are not tracked (they live as long as they live), so
revoking a family stops its refreshing, not an access token already issued. Rows are purged once
the token would have expired anyway (`jobs.purge_refresh_tokens`).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import RefreshToken, User
from ..tokens import CustomRefreshToken

logger = logging.getLogger(__name__)


# How many successors one rotated token may have. Within the grace window an app and its widget
# racing on one token each get one; past this the reuse is treated as a replay.
MAX_SUCCESSORS = 3


class InvalidRefreshToken(Exception):
    """The refresh token may not be used any more: its family is revoked, or it was replayed."""


def _expires_at(token: CustomRefreshToken) -> datetime:
    return datetime.fromtimestamp(int(token['exp']), tz=UTC)


def _record(user: User, token: CustomRefreshToken, family: uuid.UUID) -> RefreshToken:
    return RefreshToken.objects.create(
        jti=str(token['jti']), user=user, family=family, expires_at=_expires_at(token)
    )


def issue(user: User) -> CustomRefreshToken:
    """A refresh token for a new login: the first of a new family."""
    token = CustomRefreshToken.for_user(user)
    _record(user, token, uuid.uuid4())
    return token


def rotate(old: CustomRefreshToken, user: User) -> CustomRefreshToken:
    """The successor of `old` (a verified refresh token of `user`), or `InvalidRefreshToken`."""
    grace = timedelta(seconds=settings.JWT_REFRESH_GRACE_SECONDS)
    replayed_family: uuid.UUID | None = None
    refused = False
    new: CustomRefreshToken | None = None

    # Every outcome is decided as plain local variables and acted on after the block, so a refusal
    # never raises through `atomic()` (which would roll a revocation back).
    with transaction.atomic():
        record, _ = RefreshToken.objects.select_for_update().get_or_create(
            jti=str(old['jti']),
            defaults={'user': user, 'family': uuid.uuid4(), 'expires_at': _expires_at(old)},
        )
        # Read after the lock is held, so the grace decision and the stamps use the moment the
        # row was examined, not the moment the request began waiting for it.
        now = timezone.now()
        if record.user_id != user.pk or record.revoked_at is not None:
            # A foreign row can't happen with a signed token; refuse, don't guess.
            refused = True
        elif record.rotated_at is not None and (
            now - record.rotated_at > grace or record.successor_count >= MAX_SUCCESSORS
        ):
            replayed_family = record.family
            RefreshToken.objects.filter(family=record.family, revoked_at__isnull=True).update(
                revoked_at=now
            )
        else:
            RefreshToken.objects.filter(pk=record.pk).update(
                rotated_at=record.rotated_at or now,
                successor_count=record.successor_count + 1,
            )
            new = CustomRefreshToken.for_user(user)
            _record(user, new, record.family)

    if replayed_family is not None:
        logger.warning(
            'Replayed refresh token: revoked family %s of user %s', replayed_family, user.pk
        )
    if new is None or refused:
        raise InvalidRefreshToken
    return new
