"""Dashboard actions on a transfer that hasn't ended (spec section 6): copy link (a pure template
concern, no service function needed), disable, re-enable, extend/shorten expiry, change max
downloads, change/remove password, resend a recipient's email, add recipients, delete now.
"""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.services import InsufficientCreditsError, get_balance

from ..models import FileTransferSettings, Transfer, TransferRecipient, TransferStatus
from . import limits
from .blocklist import check_not_blocked
from .emails import send_transfer_notification
from .expiry_choices import resolve_expiry
from .lifecycle import end_transfer
from .metering import MIN_BALANCE_TO_REENABLE, reenable_transfer
from .password import hash_password


class TransferHeldError(ValidationError):
    """Raised instead of a plain `ValidationError` when an action is refused because `transfer`
    is on hold pending abuse review (issue #59 code review) -- a `ValidationError` subclass, so
    every existing `except ValidationError` call site (web dashboard views) already handles it
    correctly (422 + the row's error message), while the JWT API can catch this more specific
    type first and map it to 409 instead of the generic 400, same pattern as
    `blocklist.BlockedSenderError`/403."""


def _require_actionable(transfer: Transfer) -> None:
    if not transfer.is_actionable:
        raise ValidationError('This transfer has already ended.')


def _require_not_held(transfer: Transfer) -> None:
    """Refuse most dashboard/API actions while `transfer` is on hold pending abuse review (issue
    #59 code review's "hold freezes the transfer" decision): a hold means someone with the link is
    being asked not to touch it while staff look into it, so the owner shouldn't be able to keep
    emailing new recipients, change its settings, or delete the evidence out from under a pending
    review. Disabling it is deliberately *not* gated by this -- taking the link down entirely is
    always allowed, hold or not."""
    if transfer.held_for_review_at:
        raise TransferHeldError(
            'This transfer is on hold pending review and cannot be changed right now.'
        )


def disable_transfer(transfer: Transfer) -> Transfer:
    """Pause an active transfer: the link stops working, and it stops being metered. Reversible
    with `reenable_transfer_action`."""
    if transfer.status != TransferStatus.ACTIVE:
        raise ValidationError('Only an active transfer can be disabled.')
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DISABLED)
    transfer.status = TransferStatus.DISABLED
    return transfer


def reenable_transfer_action(transfer: Transfer, *, ip: str | None = None) -> Transfer:
    """Resume a disabled or suspended transfer. Either way needs the same 1-credit minimum as
    starting a transfer (spec section 7: "Minimum balance to start a transfer or to re-enable
    one: 1 credit") -- a disabled transfer resumes being metered the moment it's re-enabled, so
    an owner with zero credits shouldn't be able to un-pause one just to have it immediately
    suspended again on the next metering tick. The billing clock resets to now either way.

    Blocked (issue #59 code review) the same as re-adding recipients or resending: re-enabling is
    itself a send path (the link starts working, and delivering to it again, again), and refused
    entirely while the transfer is on hold pending abuse review (`_require_not_held`)."""
    if transfer.status not in (TransferStatus.DISABLED, TransferStatus.SUSPENDED):
        raise ValidationError('Only a disabled or suspended transfer can be re-enabled.')
    _require_not_held(transfer)
    if transfer.owner is not None:
        check_not_blocked(email=transfer.owner.email, ip=ip)
    if transfer.owner is not None and get_balance(transfer.owner) < MIN_BALANCE_TO_REENABLE:
        raise InsufficientCreditsError(
            f'You need at least {MIN_BALANCE_TO_REENABLE} credit to re-enable this transfer.'
        )
    reenable_transfer(transfer)
    return transfer


def delete_transfer_now(transfer: Transfer) -> Transfer:
    """Dashboard "delete now": soft delete, files removed immediately, no grace period."""
    _require_actionable(transfer)
    _require_not_held(transfer)
    return end_transfer(transfer, TransferStatus.DELETED, delete_files=True, notify=False)


def set_expiry(
    transfer: Transfer, expiry_choice: str, expiry_date: datetime | None = None
) -> Transfer:
    """Extend or shorten a transfer's expiry (spec section 6)."""
    _require_actionable(transfer)
    _require_not_held(transfer)
    expires_at = resolve_expiry(expiry_choice, expiry_date)
    Transfer.objects.filter(pk=transfer.pk).update(expires_at=expires_at)
    transfer.expires_at = expires_at
    return transfer


def set_max_downloads(transfer: Transfer, max_downloads: int | None) -> Transfer:
    _require_actionable(transfer)
    _require_not_held(transfer)
    if max_downloads is not None and max_downloads < 1:
        raise ValidationError('Max downloads must be at least 1.')
    Transfer.objects.filter(pk=transfer.pk).update(max_downloads=max_downloads)
    transfer.max_downloads = max_downloads
    return transfer


def set_password(transfer: Transfer, raw_password: str) -> Transfer:
    """Set (or, with an empty string, remove) the transfer's download password."""
    _require_actionable(transfer)
    _require_not_held(transfer)
    password_hash = hash_password(raw_password) if raw_password else ''
    Transfer.objects.filter(pk=transfer.pk).update(password_hash=password_hash)
    transfer.password_hash = password_hash
    return transfer


def remove_password(transfer: Transfer) -> Transfer:
    return set_password(transfer, '')


def set_notify_on_download(transfer: Transfer, notify_on_download: bool) -> Transfer:
    """Toggle the per-transfer "email me when a file is downloaded" setting (spec section 8) --
    set once at send time (`SendOptionsForm`), and changeable afterwards through the JWT API's
    update endpoint (spec section 14); not currently exposed as its own dashboard control."""
    _require_actionable(transfer)
    Transfer.objects.filter(pk=transfer.pk).update(notify_on_download=notify_on_download)
    transfer.notify_on_download = notify_on_download
    return transfer


def add_recipients(
    transfer: Transfer, emails: list[str], *, ip: str | None = None
) -> list[TransferRecipient]:
    """Add recipients to an existing transfer, subject to the same per-transfer cap as sending
    (spec section 6). Emails already on the transfer are left alone (not re-added, not re-sent
    here -- use `resend_recipient_email` for that).

    Adding recipients is itself a send path (issue #59 code review): a blocked sender could
    otherwise keep emailing new addresses from any transfer they already had running, so this is
    checked against the block list (the owner's email, plus `ip` when the caller has it) exactly
    like starting a new one -- and refused outright while the transfer is on hold pending abuse
    review.
    """
    _require_actionable(transfer)
    _require_not_held(transfer)
    if transfer.owner is not None:
        check_not_blocked(email=transfer.owner.email, ip=ip)
    settings_row = FileTransferSettings.load()
    existing = {r.email.lower() for r in transfer.recipients.all()}
    new_emails = [
        e for e in limits.validate_recipients(emails, settings_row) if e.lower() not in existing
    ]

    combined_count = len(existing) + len(new_emails)
    if combined_count > settings_row.logged_in_max_recipients:
        raise ValidationError(
            f'A transfer can have at most {settings_row.logged_in_max_recipients} recipients.'
        )

    now = timezone.now()
    created = [
        TransferRecipient.objects.create(transfer=transfer, email=email, last_sent_at=now)
        for email in new_emails
    ]
    for recipient in created:
        send_transfer_notification.enqueue(str(transfer.id), recipient.email)
    return created


def resend_recipient_email(
    recipient: TransferRecipient, *, ip: str | None = None
) -> TransferRecipient:
    """Resend a transfer notification to one existing recipient. Same send-path block/hold checks
    as `add_recipients` (issue #59 code review): resending is another way a blocked sender could
    otherwise keep delivering mail through a transfer they already had running."""
    transfer = recipient.transfer
    _require_actionable(transfer)
    _require_not_held(transfer)
    if transfer.owner is not None:
        check_not_blocked(email=transfer.owner.email, ip=ip)
    recipient.last_sent_at = timezone.now()
    recipient.save(update_fields=['last_sent_at'])
    send_transfer_notification.enqueue(str(recipient.transfer_id), recipient.email)
    return recipient
