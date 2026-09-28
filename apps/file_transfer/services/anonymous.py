"""Anonymous sending (spec section 2, phase 2): the same upload-then-send flow a logged-in sender
uses, but tied to the browser session rather than a user (`services.anon_session`), subject to
separate (smaller) limits and a fixed expiry list, gated by `FileTransferSettings.anonymous_enabled`,
free of charge, and -- the one real difference in the flow -- only becoming `ACTIVE` (and only then
sending the recipient emails) once the sender's email is confirmed through
`apps.core.services.email_verification`.

Two-step send, mirroring `services.send.finalize_send` split in two:

1. `start_confirmation`: validates the options and applies them to the draft, moves it to
   `PENDING_CONFIRMATION`, and emails a code + link to the address the sender typed in.
2. `confirm_by_code` / `confirm_by_link`: once that's confirmed, checks the per-IP-per-day caps
   (spec section 13) and flips the transfer to `ACTIVE`, exactly like `finalize_send` does.
"""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.core.services.email_verification import confirm_by_code as _core_confirm_by_code
from apps.core.services.email_verification import confirm_by_token as _core_confirm_by_token
from apps.core.services.email_verification import start as start_verification

from ..models import FileTransferSettings, Transfer, TransferRecipient, TransferStatus
from . import anon_limits, limits
from .anon_emails import PURPOSE, build_confirmation_email, send_anonymous_sender_copy
from .emails import send_transfer_notification
from .expiry_choices import resolve_expiry_anonymous
from .password import hash_password


@dataclass(slots=True)
class AnonymousSendOptions:
    sender_email: str
    recipients: list[str]
    message: str = ''
    expiry_choice: str = ''
    max_downloads: int | None = None
    password: str = ''
    notify_on_download: bool = True


def get_or_create_anonymous_draft(session_key: str, ip: str | None, cookie_id: str) -> Transfer:
    """Like `services.uploads.get_or_create_draft`, but session- rather than user-owned: reuse
    this session's existing empty draft if there is one, otherwise start a new one."""
    existing = (
        Transfer.objects.filter(
            owner__isnull=True, session_key=session_key, status=TransferStatus.DRAFT
        )
        .annotate(_file_count=Count('files'))
        .filter(_file_count=0)
        .order_by('-created_at')
        .first()
    )
    if existing:
        return existing
    return Transfer.objects.create(
        owner=None,
        status=TransferStatus.DRAFT,
        session_key=session_key,
        sender_ip=ip,
        anon_cookie_id=cookie_id,
    )


def current_anonymous_transfer(session_key: str) -> Transfer | None:
    """This session's most recent still-live anonymous transfer -- draft, pending confirmation, or
    just-activated -- so the send page can resume wherever it left off across reloads."""
    return (
        Transfer.objects.filter(
            owner__isnull=True,
            session_key=session_key,
            status__in=[
                TransferStatus.DRAFT,
                TransferStatus.PENDING_CONFIRMATION,
                TransferStatus.ACTIVE,
            ],
        )
        .order_by('-created_at')
        .first()
    )


def validate_anonymous_send_options(
    options: AnonymousSendOptions, settings_row: FileTransferSettings
) -> list[str]:
    """Like `services.send.validate_send_options`, against the anonymous limits and the fixed
    expiry list instead. Returns the deduplicated recipient list."""
    limits.validate_message(options.message)
    recipients = limits.validate_recipients_anonymous(options.recipients, settings_row)
    if not recipients:
        raise ValidationError('Add at least one recipient.')
    if options.max_downloads is not None and options.max_downloads < 1:
        raise ValidationError('Max downloads must be at least 1.')
    # Raises if the choice isn't one of the currently-allowed fixed values; recomputed (and used)
    # in start_confirmation so the two calls stay in sync.
    resolve_expiry_anonymous(options.expiry_choice, settings_row)
    return recipients


def start_confirmation(transfer: Transfer, options: AnonymousSendOptions) -> Transfer:
    """Validate `options`, apply them to the draft, and start email confirmation (spec section 2
    step 4): moves `transfer` from `DRAFT` to `PENDING_CONFIRMATION` and emails a code and a link
    to `options.sender_email`. Nothing is sent to recipients yet -- that only happens once
    `confirm_by_code`/`confirm_by_link` succeeds.

    Raises:
        ValidationError: an option is invalid, no files have finished uploading, or `transfer`
            isn't (still) a draft.
        core.services.email_verification.ResendTooSoon / EmailVerificationSendFailed: starting
            the verification itself failed; nothing here is changed either way (both happen
            inside the same transaction as the rest of this function).
    """
    settings_row = FileTransferSettings.load()
    recipients = validate_anonymous_send_options(options, settings_row)

    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if locked.status != TransferStatus.DRAFT:
            raise ValidationError('This transfer has already been sent.')
        limits.validate_has_files(locked)

        verification_id = start_verification(
            options.sender_email, PURPOSE, build_email=build_confirmation_email(locked)
        )

        locked.sender_email = options.sender_email
        locked.email_verification_id = verification_id
        locked.message = options.message
        locked.expires_at = resolve_expiry_anonymous(options.expiry_choice, settings_row)
        locked.max_downloads = options.max_downloads
        locked.password_hash = hash_password(options.password) if options.password else ''
        locked.notify_on_download = options.notify_on_download
        locked.status = TransferStatus.PENDING_CONFIRMATION
        locked.save()

        for email in recipients:
            TransferRecipient.objects.get_or_create(transfer=locked, email=email)

    transfer.refresh_from_db()
    return transfer


def resend_confirmation(transfer: Transfer) -> Transfer:
    """Resend the confirmation email for a still-pending transfer (spec default: a 60s cooldown,
    enforced by `core`, applies via `ResendTooSoon`)."""
    if transfer.status != TransferStatus.PENDING_CONFIRMATION:
        raise ValidationError('This transfer is not awaiting confirmation.')

    verification_id = start_verification(
        transfer.sender_email, PURPOSE, build_email=build_confirmation_email(transfer)
    )
    Transfer.objects.filter(pk=transfer.pk).update(email_verification_id=verification_id)
    transfer.email_verification_id = verification_id
    return transfer


def _activate(transfer: Transfer, confirmed_email: str, ip: str | None, cookie_id: str) -> Transfer:
    """Shared tail of `confirm_by_code`/`confirm_by_link`: check the per-IP-per-day caps and flip
    the transfer to `ACTIVE`, queuing the recipient + sender emails -- the anonymous equivalent of
    `services.send.finalize_send`'s second half."""
    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if locked.status == TransferStatus.ACTIVE:
            # Already activated by a concurrent confirm (the code and the link raced, or the link
            # was opened twice) -- idempotent success, no second round of emails.
            return locked
        if locked.status != TransferStatus.PENDING_CONFIRMATION:
            raise ValidationError('This transfer is no longer awaiting confirmation.')
        if locked.sender_email.lower() != confirmed_email.lower():
            # Shouldn't happen (the verification was started for this exact transfer's
            # sender_email), but never activate on a mismatch.
            raise ValidationError('This confirmation does not match this transfer.')

        locked.size_bytes = sum(f.size for f in locked.files.filter(uploaded=True))
        anon_limits.check_send_caps(locked, ip, cookie_id)

        now = timezone.now()
        locked.status = TransferStatus.ACTIVE
        locked.completed_at = now
        locked.save()
        locked.recipients.update(last_sent_at=now)

    transaction.on_commit(lambda: _queue_send_emails(transfer.id))
    transfer.refresh_from_db()
    return transfer


def confirm_by_code(transfer: Transfer, code: str, ip: str | None, cookie_id: str) -> Transfer:
    """Confirm by the 6-digit code the sender typed on the send page. Raises `ValidationError` if
    no confirmation is in progress, or one of `core.services.email_verification`'s exceptions
    (`IncorrectCode`, `EmailVerificationBurned`, `EmailVerificationExpired`, ...) for a bad code."""
    if not transfer.email_verification_id:
        raise ValidationError('No confirmation is in progress for this transfer.')
    email = _core_confirm_by_code(transfer.email_verification_id, code, PURPOSE)
    return _activate(transfer, email, ip, cookie_id)


def confirm_by_link(transfer: Transfer, token: str, ip: str | None, cookie_id: str) -> Transfer:
    """Confirm by clicking the link in the confirmation email."""
    email = _core_confirm_by_token(token, PURPOSE)
    return _activate(transfer, email, ip, cookie_id)


def _queue_send_emails(transfer_id: object) -> None:
    transfer = Transfer.objects.get(pk=transfer_id)
    for recipient in transfer.recipients.all():
        send_transfer_notification.enqueue(str(transfer_id), recipient.email)
    send_anonymous_sender_copy.enqueue(str(transfer_id))
