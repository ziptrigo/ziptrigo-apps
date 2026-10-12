"""Email intake: from an object SES dropped in S3 to `Receipt` rows.

SES writes every message it accepts to `receipts/inbound/<message id>` (see `services.storage`).
Nothing here is pushed to us: a scheduler job polls that prefix, and the rest runs on the
`receipts` queue.

1. **`poll`** (job `receipts.poll_inbound`, every minute): list up to 100 keys; each key without an
   `InboundMessage` row gets one (`pending`) and an `ingest_message` task, queued on commit. It
   parses nothing. It also re-queues `pending` rows that are due -- never picked up for 10
   minutes (a crash between the commit and the enqueue), or whose retry time has come -- and
   deletes the object of a `done` message that is still listed (its delete failed).
2. **`ingest`** (task `ingest_message`): claim the row, download the object, parse it
   (`services.mime`), and for each recipient that is an *active* inbox code do the work below in
   one transaction, then delete the object and mark the row `done`.

   Per recipient, in this order (the first that applies decides the `InboundEmail.status`):

   - SES flagged it as spam / a virus (`FAIL`): `rejected_spam` / `rejected_virus`.
   - It is a Gmail forwarding confirmation (a known sender, authenticated by SES): stored as
     `forwarding_confirmation` with the code and link it contained. Free, no receipts, and it skips
     the allowlist -- the owner must be able to finish setting up the forwarding that delivers
     their receipts.
   - The account has a sender allowlist and the sender is not on it (or SES did not vouch for the
     sender with SPF or DKIM): `rejected_sender`. Empty allowlist = anyone, no authentication
     needed.
   - Otherwise the eligible attachments (see `mime.analyze`) become one receipt each; with none,
     the body (HTML preferred) becomes the receipt; with neither, `no_receipt`. An attachment over
     `max_file_bytes` with nothing else eligible: `rejected_too_large`.
   - Every file takes a slot from the account's daily cap (`accounts.take_intake_slot`, the same
     counter uploads use). None left for any file: `rejected_cap`; some: the rest are skipped and
     the email's `note` says so.

   A rejected email keeps its metadata only (from, subject, time, status). An accepted one stores
   each file at `receipts/users/<user>/files/<receipt>` and the attachment-stripped `.eml` at
   `.../emails/<email>.eml`, creates the receipts, adds exactly those bytes to the account's
   `stored_bytes`, and queues `process_receipt` per receipt on commit. A frozen account still
   gets its receipts stored (`process_receipt` parks them in `waiting_for_credits`).

**Retries.** A claim bumps `attempts` and sets `next_attempt_at` to a lease. A failure puts the row
back to `pending` with a backoff (1 min, then 5 min) and the poller re-queues it when due; after
`MAX_ATTEMPTS` it is `failed` and **its S3 object is kept** for inspection (admin "retry" resets
it). An unrecoverable message (over the size guard, unparseable, object gone) fails at once. A
worker that dies mid-ingest just lets the lease run out. Each recipient's work is one database
transaction (its `InboundEmail` row is created in it), so "the row exists" means "fully done" and a
retry skips recipients already handled; objects written for a transaction that then fails are
deleted again.

Delivered mail is untrusted input: everything here is bounded (raw size, nesting depth, per-file
size, header lengths) and nothing from the message reaches a subject line or a key.
"""

import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import Message

from botocore.exceptions import BotoCoreError, ClientError
from django.db import transaction
from django.db.models import F, Q
from django.tasks import task
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.storage import ObjectTooLargeError

from ..models import (
    InboundEmail,
    InboundEmailStatus,
    InboundMessage,
    InboundMessageStatus,
    InboxCode,
    Receipt,
    ReceiptsAccount,
    ReceiptSource,
    ReceiptsSettings,
    ReceiptStatus,
)
from . import mime
from .accounts import add_stored_bytes, take_intake_slot
from .addresses import matches_allowlist
from .errors import DailyLimitExceededError
from .processing import enqueue_processing
from .queue import enqueue_after_commit
from .storage import INBOUND_PREFIX, email_key, file_key, get_storage

logger = logging.getLogger(__name__)

#: Keys listed per poll.
POLL_BATCH = 100
#: Messages that failed keep their object, so they stay in the listing forever; the poll lists that
#: many extra keys (up to this cap) so a pile of failures can't hide new mail behind them.
POLL_EXTRA_FOR_FAILED_MAX = 900
#: A `pending` message nobody has claimed for this long was probably never queued.
PENDING_STALE_AFTER = timedelta(minutes=10)
#: Don't queue the same message again within this window (a slow queue would otherwise collect a
#: duplicate task per message per poll).
REQUEUE_THROTTLE = timedelta(minutes=5)
#: How long a claim holds the message before a crashed worker's claim counts as abandoned.
CLAIM_LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 3
#: Delay before try number 2 and 3.
RETRY_BACKOFF = (timedelta(minutes=1), timedelta(minutes=5))
#: SES accepts messages up to 40 MB; nothing bigger is ever read into memory.
MAX_RAW_BYTES = 40 * 1024 * 1024
MAX_ERROR_CHARS = 2000


class PermanentIngestError(Exception):
    """A message that can never be ingested, however often it is retried (the row fails at once)."""


# -- 1. Polling ---------------------------------------------------------------------------------


def poll() -> None:
    """One run of the `receipts.poll_inbound` job. Keep it light: one S3 listing, a few queries."""
    storage = get_storage()
    failed = InboundMessage.objects.filter(status=InboundMessageStatus.FAILED).count()
    limit = POLL_BATCH + min(failed, POLL_EXTRA_FOR_FAILED_MAX)
    keys = [
        key
        for key in storage.list_keys(INBOUND_PREFIX, max_keys=limit)
        if key != INBOUND_PREFIX and not key.endswith('/')
    ]
    known = dict(InboundMessage.objects.filter(s3_key__in=keys).values_list('s3_key', 'status'))
    for key in keys:
        try:
            status = known.get(key)
            if status is None:
                _register(key)
            elif status == InboundMessageStatus.DONE:
                # Done, but the delete after it failed: finish it.
                delete_inbound_object(key)
        except Exception:
            logger.exception('Could not handle inbound key %s', key)
    _requeue_due()


def _register(key: str) -> None:
    with transaction.atomic():
        message, created = InboundMessage.objects.get_or_create(
            s3_key=key, defaults={'queued_at': timezone.now()}
        )
        if created:
            enqueue_after_commit(ingest_message, message.pk)


def _requeue_due() -> None:
    """Queue `pending` messages whose time has come: never claimed and old, or a retry that is due.
    (A claimed message holds a lease in `next_attempt_at`, so it only shows up here once the lease
    has run out.)"""
    now = timezone.now()
    due = (
        InboundMessage.objects.filter(status=InboundMessageStatus.PENDING)
        .filter(
            Q(next_attempt_at__lte=now)
            | Q(next_attempt_at__isnull=True, created_at__lte=now - PENDING_STALE_AFTER)
        )
        .filter(Q(queued_at__isnull=True) | Q(queued_at__lte=now - REQUEUE_THROTTLE))
        .order_by('created_at')
        .values_list('pk', flat=True)[:POLL_BATCH]
    )
    for message_id in list(due):
        try:
            queue_message(message_id)
        except Exception:
            logger.exception('Could not queue inbound message %s', message_id)


def queue_message(message_id: int) -> None:
    """Stamp the message as just queued and queue `ingest_message` for it (now, not on commit:
    call it outside a transaction or from one that has the row committed)."""
    InboundMessage.objects.filter(pk=message_id).update(queued_at=timezone.now())
    ingest_message.enqueue(message_id)


def delete_inbound_object(key: str) -> None:
    try:
        get_storage().delete_object(key)
    except ClientError, BotoCoreError:
        logger.exception('Could not delete the inbound object %s', key)


def reset_message_for_retry(message: InboundMessage) -> bool:
    """Put a `failed` message back to `pending` with a fresh attempt count and queue it, for the
    admin's "retry" action. Returns whether anything changed. (`created_at` is left alone.)"""
    changed = InboundMessage.objects.filter(
        pk=message.pk, status=InboundMessageStatus.FAILED
    ).update(
        status=InboundMessageStatus.PENDING,
        attempts=0,
        last_error='',
        processed_at=None,
        next_attempt_at=None,
        queued_at=timezone.now(),
    )
    if changed:
        enqueue_after_commit(ingest_message, message.pk)
    return bool(changed)


# -- 2. Ingesting -------------------------------------------------------------------------------


@task(queue_name='receipts')
def ingest_message(message_id: int) -> None:
    ingest(message_id)


def ingest(message_id: int, *, recipients: Sequence[str] | None = None) -> None:
    """The body of `ingest_message`; never raises (a failure is recorded on the row, which is the
    retry mechanism -- the queue backends don't retry). `recipients`, when given, replaces the
    recipients found in the message's headers (the management command's `--to`)."""
    message = _claim(message_id)
    if message is None:
        return
    try:
        _ingest_claimed(message, recipients)
    except PermanentIngestError as exc:
        logger.error('Inbound message %s cannot be ingested: %s', message.s3_key, exc)
        _mark_failed(message, str(exc))
    except Exception as exc:
        logger.exception('Ingesting inbound message %s failed', message.s3_key)
        _record_failure(message, exc)


def _claim(message_id: int) -> InboundMessage | None:
    """Win the right to ingest the message: one conditional `UPDATE` (pending and not before its
    `next_attempt_at`) that also counts the attempt and takes the lease."""
    now = timezone.now()
    claimed = InboundMessage.objects.filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
        pk=message_id,
        status=InboundMessageStatus.PENDING,
    ).update(attempts=F('attempts') + 1, next_attempt_at=now + CLAIM_LEASE)
    if not claimed:
        return None
    message = InboundMessage.objects.get(pk=message_id)
    if message.attempts > MAX_ATTEMPTS:
        # Claimed over and over without ever finishing or recording a failure: the worker keeps
        # dying on it. Stop.
        _mark_failed(message, 'Gave up: the ingest was interrupted too many times.')
        return None
    return message


def _mark_failed(message: InboundMessage, error: str) -> None:
    InboundMessage.objects.filter(pk=message.pk).update(
        status=InboundMessageStatus.FAILED,
        last_error=error[:MAX_ERROR_CHARS],
        next_attempt_at=None,
        processed_at=timezone.now(),
    )


def _record_failure(message: InboundMessage, exc: Exception) -> None:
    error = f'{type(exc).__name__}: {exc}'
    if message.attempts >= MAX_ATTEMPTS:
        _mark_failed(message, error)
        return
    delay = RETRY_BACKOFF[min(message.attempts, len(RETRY_BACKOFF)) - 1]
    InboundMessage.objects.filter(pk=message.pk, status=InboundMessageStatus.PENDING).update(
        last_error=error[:MAX_ERROR_CHARS],
        next_attempt_at=timezone.now() + delay,
        queued_at=None,
    )


def _mark_done(message: InboundMessage) -> None:
    InboundMessage.objects.filter(pk=message.pk).update(
        status=InboundMessageStatus.DONE,
        processed_at=timezone.now(),
        next_attempt_at=None,
        last_error='',
    )


def _is_missing(exc: ClientError) -> bool:
    return str(exc.response.get('Error', {}).get('Code', '')) in ('NoSuchKey', '404', 'NotFound')


def _ingest_claimed(message: InboundMessage, override: Sequence[str] | None) -> None:
    storage = get_storage()
    try:
        raw = storage.get_object_bytes(message.s3_key, max_bytes=MAX_RAW_BYTES)
    except ObjectTooLargeError as exc:
        raise PermanentIngestError(f'The message is over {MAX_RAW_BYTES} bytes.') from exc
    except ClientError as exc:
        if not _is_missing(exc):
            raise
        if message.emails.exists():
            # A previous try did the work and removed the object before it could say so.
            _mark_done(message)
            return
        raise PermanentIngestError('The inbound object is missing.') from exc

    try:
        parsed = mime.parse_message(raw)
        scan = (
            mime.recipients_from_addresses(list(override))
            if override is not None
            else mime.scan_recipients(parsed)
        )
        context = _Context(message=message, raw=raw, parsed=parsed)
    except Exception as exc:
        raise PermanentIngestError(f'The message could not be parsed: {exc}') from exc

    if not scan.matches:
        logger.info(
            'Inbound message %s has no inbox recipient (looked at %s)',
            message.s3_key,
            ', '.join(scan.seen[:5]) or 'nothing',
        )
    for recipient in scan.matches:
        _ingest_for_recipient(context, recipient)

    _mark_done(message)
    delete_inbound_object(message.s3_key)


# -- Per recipient ------------------------------------------------------------------------------


@dataclass
class _Context:
    """What is the same for every recipient of one message. The (costly) analysis of the parts
    is made lazily, once."""

    message: InboundMessage
    raw: bytes
    parsed: Message
    verdicts: mime.Verdicts = field(init=False)
    from_address: str = field(init=False)
    envelope_sender: str = field(init=False)
    subject: str = field(init=False)
    received_at: datetime = field(init=False)
    _analysis: mime.Analysis | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.verdicts = mime.read_verdicts(self.parsed)
        self.from_address = mime.from_address(self.parsed)
        self.envelope_sender = mime.envelope_sender(self.parsed)
        self.subject = mime.subject(self.parsed)
        self.received_at = mime.received_at(self.parsed)

    def analysis(self, config: ReceiptsSettings) -> mime.Analysis:
        if self._analysis is None:
            self._analysis = mime.analyze(
                self.parsed,
                allowed_types=config.allowed_types_list(),
                max_file_bytes=config.max_file_bytes,
                min_inline_px=config.min_inline_image_px,
            )
        return self._analysis


@dataclass
class _File:
    """One file about to become a receipt."""

    leaf_index: int
    filename: str
    content_type: str
    data: bytes
    receipt_id: uuid.UUID = field(default_factory=uuid.uuid4)


_EXTENSIONS = {
    'application/pdf': '.pdf',
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'image/heic': '.heic',
}


def _filename(info: mime.AttachmentInfo) -> str:
    from .uploads import clean_filename

    name = mime.clean_text(info.filename, 255)
    return clean_filename(name) if name else f'attachment{_EXTENSIONS.get(info.content_type, "")}'


def _ingest_for_recipient(context: _Context, recipient: mime.InboxRecipient) -> None:
    message = context.message
    if InboundEmail.objects.filter(message=message, recipient=recipient.address).exists():
        return  # a previous try finished this recipient
    inbox = InboxCode.objects.select_related('account__user').filter(code=recipient.code).first()
    if inbox is None:
        logger.info('Inbound %s: unknown inbox code %s, dropped', message.s3_key, recipient.code)
        return
    if not inbox.active:
        logger.info('Inbound %s: retired inbox code %s, dropped', message.s3_key, recipient.code)
        return
    account = inbox.account
    user = account.user
    if user.status != User.STATUS_ACTIVE or not user.is_active:
        logger.info(
            'Inbound %s: user of code %s is not active, dropped', message.s3_key, recipient.code
        )
        return

    config = ReceiptsSettings.load()
    email = InboundEmail(
        user=user,
        message=message,
        recipient=recipient.address,
        from_address=mime.clean_text(context.from_address, 320),
        envelope_sender=mime.clean_text(context.envelope_sender, 320),
        subject=context.subject,
        received_at=context.received_at,
    )

    rejection = _rejection(context, account)
    if rejection is not None:
        email.status, email.note = rejection
        email.save()
        return
    if _is_forwarding_confirmation(context):
        email.status = InboundEmailStatus.FORWARDING_CONFIRMATION
        email.forwarding_confirmation_code, email.forwarding_confirmation_url = (
            mime.extract_forwarding_confirmation(context.parsed)
        )
        email.save()
        return

    analysis = context.analysis(config)
    files = [_File(a.index, _filename(a), a.content_type, a.data) for a in analysis.eligible]
    if not files:
        if analysis.too_large:
            email.status = InboundEmailStatus.REJECTED_TOO_LARGE
            email.note = f'A file is over the {config.max_file_bytes // (1024 * 1024)} MB limit.'
        elif analysis.body is not None and len(analysis.body.data) > config.max_file_bytes:
            email.status = InboundEmailStatus.REJECTED_TOO_LARGE
            email.note = 'The message body is too large.'
        elif analysis.body is not None:
            body = analysis.body
            files = [_File(body.index, '', body.content_type, body.data)]
        else:
            email.status = InboundEmailStatus.NO_RECEIPT
            email.note = 'No supported attachment and no readable text.'
        if not files:
            email.save()
            return

    _store(context, config, user, email, analysis, files)


def _rejection(context: _Context, account: ReceiptsAccount) -> tuple[str, str] | None:
    verdicts = context.verdicts
    if verdicts.spam_failed:
        return InboundEmailStatus.REJECTED_SPAM, 'Flagged as spam.'
    if verdicts.virus_failed:
        return InboundEmailStatus.REJECTED_VIRUS, 'Flagged as a virus.'
    if _is_forwarding_confirmation(context):
        return None
    allowlist = account.sender_allowlist
    if allowlist:
        listed = matches_allowlist(allowlist, context.from_address, context.envelope_sender)
        if not listed:
            return InboundEmailStatus.REJECTED_SENDER, 'The sender is not on the accepted list.'
        if not verdicts.authenticated:
            return (
                InboundEmailStatus.REJECTED_SENDER,
                'The sender could not be verified (SPF or DKIM did not pass).',
            )
    return None


def _is_forwarding_confirmation(context: _Context) -> bool:
    return context.verdicts.authenticated and mime.is_forwarding_confirmation_sender(
        context.from_address, context.envelope_sender
    )


def _store(
    context: _Context,
    config: ReceiptsSettings,
    user: User,
    email: InboundEmail,
    analysis: mime.Analysis,
    files: list[_File],
) -> None:
    """Take daily-cap slots, write the objects, create the rows -- all of one recipient's work in
    one transaction. Objects already written when something fails are deleted again."""
    storage = get_storage()
    written: list[str] = []
    try:
        with transaction.atomic():
            taken: list[_File] = []
            for item in files:
                try:
                    take_intake_slot(user, config.max_files_per_user_per_day)
                except DailyLimitExceededError:
                    break
                taken.append(item)
            skipped = len(files) - len(taken)

            if not taken:
                email.status = InboundEmailStatus.REJECTED_CAP
                email.note = f'Daily limit of {config.max_files_per_user_per_day} receipts reached.'
                email.save()
                return

            email.status = InboundEmailStatus.ACCEPTED
            notes = []
            if skipped:
                notes.append(
                    f'{skipped} of {len(files)} files skipped: daily limit of '
                    f'{config.max_files_per_user_per_day} receipts reached.'
                )
            if analysis.too_large:
                notes.append(f'{len(analysis.too_large)} file(s) over the size limit skipped.')
            email.note = ' '.join(notes)[:255]

            added = 0
            for item in taken:
                key = file_key(user.pk, item.receipt_id)
                storage.put_object(key, item.data, content_type=item.content_type)
                written.append(key)
                added += len(item.data)

            markers = _markers(analysis, taken)
            eml = mime.build_stripped_eml(context.raw, markers)
            email.raw_key = email_key(user.pk, email.pk)
            email.size_bytes = len(eml)
            storage.put_object(email.raw_key, eml, content_type='message/rfc822')
            written.append(email.raw_key)
            added += len(eml)

            email.save()
            Receipt.objects.bulk_create(
                Receipt(
                    id=item.receipt_id,
                    user=user,
                    inbound_email=email,
                    source=ReceiptSource.EMAIL,
                    original_filename=item.filename,
                    content_type=item.content_type,
                    file_key=file_key(user.pk, item.receipt_id),
                    size_bytes=len(item.data),
                    sha256=hashlib.sha256(item.data).hexdigest(),
                    uploaded=True,
                    status=ReceiptStatus.RECEIVED,
                )
                for item in taken
            )
            add_stored_bytes(user.pk, added)
            for item in taken:
                enqueue_processing(item.receipt_id)
    except BaseException:
        for key in written:
            try:
                storage.delete_object(key)
            except ClientError, BotoCoreError:
                logger.exception('Could not delete the orphaned object %s', key)
        raise


def _markers(analysis: mime.Analysis, taken: list[_File]) -> dict[int, tuple[str, str]]:
    """The header every attachment-like part (and a body that became a receipt) gets in the
    stripped copy: the receipt it became, or why it did not become one."""
    markers: dict[int, tuple[str, str]] = {}
    for info in analysis.attachments:
        markers[info.index] = (mime.SKIPPED_HEADER, info.skip or mime.SKIP_DAILY_LIMIT)
    for item in taken:
        markers[item.leaf_index] = (mime.RECEIPT_ID_HEADER, str(item.receipt_id))
    return markers
