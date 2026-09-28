"""Claim on login (spec section 6): a confirmed anonymous transfer with no owner becomes a user's
own the moment they log in with a matching, *confirmed* email address -- metered from that instant
on (`last_billed_at` = claim time). Wired to Django's `user_logged_in` signal from
`apps.file_transfer.apps`.
"""

import logging

from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import get_balance

from ..models import ENDED_STATUSES, Transfer
from .send import MIN_BALANCE_TO_SEND

logger = logging.getLogger(__name__)


def claim_transfers_for_user(user: User) -> int:
    """Claim every still-live, confirmed (`completed_at` set), unclaimed anonymous transfer whose
    `sender_email` matches `user.email`, case-insensitively. Returns how many were claimed.

    Only claims once `user.email_confirmed` -- a decision, not something the spec spells out: an
    account that hasn't itself proven it controls that address yet shouldn't be able to grab
    someone else's anonymous transfer just by signing up with their address first. Claiming is
    simply deferred: the next login after the account's own email gets confirmed picks it up (see
    `CLAUDE.md`).

    **User decision:** also never claims while `user`'s credit balance is below
    `MIN_BALANCE_TO_SEND` (the same 1-credit minimum required to *start* a logged-in transfer,
    spec section 2). Claiming turns a free, anonymous transfer into a metered one with no chance
    for the user to say no first; at a zero (or near-zero) balance, the very next metering run
    would immediately suspend it. So a transfer that would otherwise be claimed here instead stays
    anonymous and free -- still fully usable by its recipients -- until a *later* login finds the
    balance topped up to at least `MIN_BALANCE_TO_SEND`. Confirming the account's own email is
    still required regardless of balance, same as before.

    A `DISABLED` transfer is still claimable (the sender may have disabled it from the anonymous
    manage page and might want to re-enable it once they have an account); `EXPIRED`/`DELETED`
    ones (`ENDED_STATUSES`) are not -- there's nothing left to manage.
    """
    if not user.email or not user.email_confirmed:
        return 0
    if get_balance(user) < MIN_BALANCE_TO_SEND:
        return 0

    now = timezone.now()
    candidates = Transfer.objects.filter(
        owner__isnull=True, sender_email__iexact=user.email, completed_at__isnull=False
    ).exclude(status__in=ENDED_STATUSES)

    claimed = 0
    for transfer in candidates:
        # Conditional on `owner__isnull=True` again: two logins for the same user racing (or a
        # transfer somehow already claimed between the query above and this update) must not
        # double-count or clobber a `last_billed_at` a concurrent claim already set.
        updated = Transfer.objects.filter(pk=transfer.pk, owner__isnull=True).update(
            owner=user, last_billed_at=now
        )
        claimed += updated
    return claimed
