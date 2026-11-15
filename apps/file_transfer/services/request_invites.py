"""The people a file request is for (issue #306): `RequestInvite` rows, the invite email each
gets, and resending it. The audience "Only these people" lets exactly these accounts use the link
(`services.request_access`); an invite is also an email "<owner> is asking you for files".

An invite is emailed at creation and whenever an address is added later; **Resend** (the same
`actions.RESEND_COOLDOWN` and the same per-user `FT_RECIPIENT_EMAIL_USER` budget a transfer
recipient's resend has) emails one again. Removing an invitee stops their access; files they
uploaded stay. Nobody is emailed when the request is done except the owner.
"""

import math
from collections.abc import Iterable
from datetime import timedelta

from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import RequestInvite, Transfer
from .actions import RESEND_COOLDOWN, ChargeEmails, _require_actionable, _require_not_held
from .blocklist import check_not_blocked
from .emails import send_request_invite


def normalize_invite_email(email: str) -> str:
    """An invitee address as stored: surrounding space dropped, the domain lower-cased (the local
    part is kept as typed); matching against accounts ignores case entirely."""
    return BaseUserManager.normalize_email(email.strip())


def normalize_invite_emails(emails: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for email in emails:
        value = normalize_invite_email(email)
        if value and value.lower() not in seen:
            seen.add(value.lower())
            normalized.append(value)
    return normalized


def queue_invite_emails(transfer_id: object, emails: Iterable[str]) -> None:
    """Queue one invite email per address, once the surrounding transaction commits."""
    ids = str(transfer_id)
    addresses = list(emails)

    def _send() -> None:
        for email in addresses:
            send_request_invite.enqueue(ids, email)

    transaction.on_commit(_send)


def resend_invite(
    invite: RequestInvite, *, ip: str | None = None, charge: ChargeEmails | None = None
) -> RequestInvite:
    """Email one invitee again. The same refusals as a transfer recipient's resend, in the same
    order, all before `charge` so a refused resend spends no budget: the request must be
    actionable and not on hold, the owner not blocked, and the invitee not emailed within
    `RESEND_COOLDOWN`."""
    transfer = invite.transfer
    _require_actionable(transfer)
    _require_not_held(transfer)
    if transfer.owner is not None:
        check_not_blocked(email=transfer.owner.email, ip=ip)
    wait = _cooldown_wait(invite)
    if wait is not None:
        raise ValidationError(_cooldown_message(wait))
    if charge is not None:
        charge(1)
    # Claimed with one conditional write, so two quick clicks that both passed the check above
    # email the invitee once: the loser re-reads and gets the cooldown message.
    now = timezone.now()
    claimed = (
        RequestInvite.objects.filter(pk=invite.pk)
        .filter(Q(last_sent_at__isnull=True) | Q(last_sent_at__lte=now - RESEND_COOLDOWN))
        .update(last_sent_at=now)
    )
    if not claimed:
        try:
            invite.refresh_from_db()
        except RequestInvite.DoesNotExist:
            raise ValidationError('This person is no longer invited.') from None
        raise ValidationError(_cooldown_message(_cooldown_wait(invite) or RESEND_COOLDOWN))
    invite.last_sent_at = now
    send_request_invite.enqueue(str(transfer.id), invite.email)
    return invite


def _cooldown_wait(invite: RequestInvite) -> timedelta | None:
    """How long until `invite` may be emailed again, or `None` when it may be now."""
    if invite.last_sent_at is None:
        return None
    wait = invite.last_sent_at + RESEND_COOLDOWN - timezone.now()
    return wait if wait > timedelta(0) else None


def _cooldown_message(wait: timedelta) -> str:
    minutes = max(1, math.ceil(wait.total_seconds() / 60))
    unit = 'minute' if minutes == 1 else 'minutes'
    return f'This person was emailed recently; you can resend in {minutes} {unit}.'


def invite_emails(transfer: Transfer) -> list[str]:
    return [invite.email for invite in transfer.invites.all()]
