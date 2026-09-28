"""Finishing a send: validating the transfer's options, checking the sender's balance, and
flipping a draft to `ACTIVE` (spec section 2, logged-in senders only in phase 1).
"""

from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.billing.services import InsufficientCreditsError, get_balance

from ..models import FileTransferSettings, Transfer, TransferRecipient, TransferStatus
from . import limits
from .blocklist import check_not_blocked
from .emails import send_sender_copy, send_transfer_notification
from .expiry_choices import resolve_expiry
from .password import hash_password

#: Minimum balance to start a transfer (spec section 2 and 7).
MIN_BALANCE_TO_SEND = 1


@dataclass(slots=True)
class SendOptions:
    recipients: list[str]
    message: str = ''
    expiry_choice: str = 'none'
    expiry_date: datetime | None = None
    max_downloads: int | None = None
    password: str = ''
    notify_on_download: bool = True


def validate_send_options(options: SendOptions, settings_row: FileTransferSettings) -> list[str]:
    """Validate everything about `options` except the sender's balance and the files themselves
    (those are checked separately, since the view surfaces them differently). Returns the
    deduplicated recipient list."""
    limits.validate_message(options.message)
    recipients = limits.validate_recipients(options.recipients, settings_row)
    if not recipients:
        raise ValidationError('Add at least one recipient.')
    if options.max_downloads is not None and options.max_downloads < 1:
        raise ValidationError('Max downloads must be at least 1.')
    # Raises if the expiry choice/date themselves are invalid; the resolved value is recomputed
    # (and used) in finalize_send so the two calls stay in sync down to the second.
    resolve_expiry(options.expiry_choice, options.expiry_date)
    return recipients


def finalize_send(transfer: Transfer, options: SendOptions, *, ip: str | None = None) -> Transfer:
    """Complete a draft transfer: verify files and balance, apply the sender's options, mark it
    `ACTIVE`, and queue the recipient + sender emails.

    Raises:
        ValidationError: an option is invalid, no files have finished uploading, or the owner's
            balance is below the 1-credit minimum.
        blocklist.BlockedSenderError: the owner's email (or, if the caller passes `ip`, the
            client IP) is on the block list (issue #59) -- a belt-and-suspenders re-check
            alongside `views.send.send_page`'s own check at draft creation, in case the owner was
            blocked in the meantime. `ip` is optional (and checked here, not just at draft
            creation) for consistency with every other send-path checkpoint
            (`services.blocklist`'s own docstring): the caller (`views.send.send_submit`,
            `api.transfers.finalize_transfer`) passes the request's client IP when it has one.
    """
    settings_row = FileTransferSettings.load()
    recipients = validate_send_options(options, settings_row)
    if transfer.owner is not None:
        check_not_blocked(email=transfer.owner.email, ip=ip)

    now = timezone.now()
    with transaction.atomic():
        # Lock the row for the whole check-and-flip: a double submit of the same draft (two
        # `send_submit` POSTs racing each other) must not both pass the "still a draft" check
        # before either flips the status to `ACTIVE` -- the loser's lock wait only ends once the
        # winner has committed, and by then this re-read sees `ACTIVE` and correctly rejects it,
        # rather than both sending duplicate recipient emails.
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if locked.status != TransferStatus.DRAFT:
            raise ValidationError('Transfer has already been sent.')
        if locked.owner is None:
            raise ValidationError('Anonymous sending is not available yet.')

        limits.validate_has_files(locked)
        if get_balance(locked.owner) < MIN_BALANCE_TO_SEND:
            raise InsufficientCreditsError(
                f'You need at least {MIN_BALANCE_TO_SEND} credit to send a transfer.'
            )

        locked.message = options.message
        locked.expires_at = resolve_expiry(options.expiry_choice, options.expiry_date)
        locked.max_downloads = options.max_downloads
        locked.password_hash = hash_password(options.password) if options.password else ''
        locked.notify_on_download = options.notify_on_download
        locked.size_bytes = sum(f.size for f in locked.files.filter(uploaded=True))
        locked.status = TransferStatus.ACTIVE
        locked.completed_at = now
        locked.last_billed_at = now
        locked.save()

        for email in recipients:
            TransferRecipient.objects.update_or_create(
                transfer=locked, email=email, defaults={'last_sent_at': now}
            )

        transaction.on_commit(lambda: _queue_send_emails(locked, recipients))

    transfer.refresh_from_db()
    return transfer


def _queue_send_emails(transfer: Transfer, recipients: list[str]) -> None:
    for email in recipients:
        send_transfer_notification.enqueue(str(transfer.id), email)
    send_sender_copy.enqueue(str(transfer.id))
