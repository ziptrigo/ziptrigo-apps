"""The sender block list (issue #59): emails and IPs that may not send transfers, managed in the
admin (`BlockedSender`). Checked at every point a transfer's sender identity is established or
confirmed:

- Logged-in web send page (`views.send.send_page`, on the owner's email + client IP) and the JWT
  API's `POST /transfers/` (`api.transfers.create_transfer`, same checks).
- Logged-in finalize/send (`services.send.finalize_send`, on the owner's email).
- Anonymous draft creation (`views.anonymous.send_page`, client IP only -- no email is known yet).
- Anonymous email confirmation, both when it starts (`services.anonymous.start_confirmation`, on
  the address just typed in) and when it completes (`services.anonymous._activate`, on the
  confirmed address + the confirming IP).

Deliberately never says *why* a sender is blocked, or that a block list exists at all -- just that
they can't send right now (`BLOCKED_MESSAGE`), the same "don't reveal why" spirit as
`services.downloads.is_available`.
"""

import ipaddress

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User

from ..models import ACTIONABLE_STATUSES, BlockedSender, BlockedSenderKind, Transfer

BLOCKED_MESSAGE = "Sorry, you can't send transfers at this time."


class BlockedSenderError(ValidationError):
    """Raised by `check_not_blocked` -- a `ValidationError` subclass so every existing
    `except ValidationError` call site (web views, `services.send`/`services.anonymous`) already
    handles it correctly, while API endpoints that want a distinct 403 (rather than the generic
    400 a plain `ValidationError` maps to) can catch this more specific type first."""


def _active_entries(kind: str):
    now = timezone.now()
    return BlockedSender.objects.filter(kind=kind).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


def _email_blocked(email: str) -> bool:
    email = email.strip().lower()
    if not email:
        return False
    domain = email.split('@', 1)[1] if '@' in email else ''
    query = Q(value__iexact=email)
    if domain:
        query |= Q(value__iexact=f'*@{domain}')
    return _active_entries(BlockedSenderKind.EMAIL).filter(query).exists()


def _ip_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # CIDR "contains" isn't a query either SQLite or Postgres can both do generically over a plain
    # `CharField` -- the block list is expected to stay small (a support tool, not a firewall), so
    # a Python membership check over every active IP entry is simpler than a database-specific
    # `inet` column and index.
    for entry in _active_entries(BlockedSenderKind.IP).values_list('value', flat=True):
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if addr in network:
            return True
    return False


def is_blocked(*, email: str | None = None, ip: str | None = None) -> bool:
    """Whether `email` and/or `ip` are on the block list. Either may be omitted (anonymous draft
    creation has no sender email yet)."""
    if email and _email_blocked(email):
        return True
    if ip and _ip_blocked(ip):
        return True
    return False


def check_not_blocked(*, email: str | None = None, ip: str | None = None) -> None:
    """Like `is_blocked`, raising `BlockedSenderError` instead of returning a bool -- for call
    sites already inside a "validate, then raise" service function."""
    if is_blocked(email=email, ip=ip):
        raise BlockedSenderError(BLOCKED_MESSAGE)


def _block(kind: str, value: str, *, reason: str, created_by: User) -> BlockedSender | None:
    """Add `value` to the block list under `kind`, or `None` if it's already actively blocked.

    Looks only among *active* entries (issue #59 code review): a plain `get_or_create` matched an
    expired row too (since it doesn't filter on `expires_at` at all), so re-blocking someone whose
    earlier block had lapsed silently created nothing ("Added 0 entries") instead of actually
    blocking them again -- and, separately, two identical active rows added by hand through the
    admin's own CRUD (nothing stops that; there's no uniqueness constraint, see `BlockedSender`'s
    docstring) made `get_or_create` raise `MultipleObjectsReturned` instead of just picking one.
    Reactivates (clears `expires_at` on) the most recent matching *inactive* row instead of
    creating a fresh one, so the history of who blocked this value and when isn't lost each time
    it lapses and gets re-blocked.
    """
    active = _active_entries(kind).filter(value=value).first()
    if active is not None:
        return None

    inactive = BlockedSender.objects.filter(kind=kind, value=value).order_by('-created_at').first()
    if inactive is not None:
        inactive.expires_at = None
        inactive.reason = reason
        inactive.created_by = created_by
        inactive.save(update_fields=['expires_at', 'reason', 'created_by'])
        return inactive

    return BlockedSender.objects.create(
        kind=kind, value=value, reason=reason, created_by=created_by
    )


def block_transfer_sender(
    transfer: Transfer,
    *,
    created_by: User,
    reason: str = '',
    block_email: bool = True,
    block_ip: bool = True,
) -> list[BlockedSender]:
    """Admin convenience: block the sender of `transfer` (its `sender_email`/owner's email and/or
    its `sender_ip`), from the transfer or abuse-report admin. Returns the entries created or
    reactivated (an already actively-blocked value is left as it is, not duplicated -- see
    `_block`)."""
    created: list[BlockedSender] = []
    email = transfer.sender_email or (transfer.owner.email if transfer.owner else '')
    if block_email and email:
        entry = _block(
            BlockedSenderKind.EMAIL, email.strip().lower(), reason=reason, created_by=created_by
        )
        if entry is not None:
            created.append(entry)
    if block_ip and transfer.sender_ip:
        entry = _block(
            BlockedSenderKind.IP, transfer.sender_ip, reason=reason, created_by=created_by
        )
        if entry is not None:
            created.append(entry)
    return created


def active_transfers_for_sender(transfer: Transfer):
    """Every still-actionable transfer (including `transfer` itself) from the same sender as
    `transfer` -- its owner (every transfer they own), or, for an anonymous one, its
    `sender_email`/`sender_ip`. Backs the block-sender confirmation form's optional "also take
    down this sender's active transfers" checkbox (issue #59 code review): blocking a sender stops
    *future* sending, but by default leaves whatever they already have live untouched."""
    query = Q()
    if transfer.owner is not None:
        query |= Q(owner=transfer.owner)
    elif transfer.sender_email:
        query |= Q(owner__isnull=True, sender_email__iexact=transfer.sender_email)
    if transfer.sender_ip:
        query |= Q(sender_ip=transfer.sender_ip)
    if not query:
        return Transfer.objects.none()
    return Transfer.objects.filter(query, status__in=ACTIONABLE_STATUSES)
