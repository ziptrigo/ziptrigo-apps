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


def finalize_send(transfer: Transfer, options: SendOptions) -> Transfer:
    """Complete a draft transfer: verify files and balance, apply the sender's options, mark it
    `ACTIVE`, and queue the recipient + sender emails.

    Raises:
        ValidationError: an option is invalid, no files have finished uploading, or the owner's
            balance is below the 1-credit minimum.
    """
    if transfer.status != TransferStatus.DRAFT:
        raise ValidationError('Transfer has already been sent.')
    if transfer.owner is None:
        raise ValidationError('Anonymous sending is not available yet.')

    settings_row = FileTransferSettings.load()
    recipients = validate_send_options(options, settings_row)
    limits.validate_has_files(transfer)

    if get_balance(transfer.owner) < MIN_BALANCE_TO_SEND:
        raise InsufficientCreditsError(
            f'You need at least {MIN_BALANCE_TO_SEND} credit to send a transfer.'
        )

    now = timezone.now()
    with transaction.atomic():
        transfer.message = options.message
        transfer.expires_at = resolve_expiry(options.expiry_choice, options.expiry_date)
        transfer.max_downloads = options.max_downloads
        transfer.password_hash = hash_password(options.password) if options.password else ''
        transfer.notify_on_download = options.notify_on_download
        transfer.size_bytes = sum(f.size for f in transfer.files.filter(uploaded=True))
        transfer.status = TransferStatus.ACTIVE
        transfer.completed_at = now
        transfer.last_billed_at = now
        transfer.save()

        for email in recipients:
            TransferRecipient.objects.update_or_create(
                transfer=transfer, email=email, defaults={'last_sent_at': now}
            )

        transaction.on_commit(lambda: _queue_send_emails(transfer, recipients))

    return transfer


def _queue_send_emails(transfer: Transfer, recipients: list[str]) -> None:
    for email in recipients:
        send_transfer_notification.enqueue(str(transfer.id), email)
    send_sender_copy.enqueue(str(transfer.id))
