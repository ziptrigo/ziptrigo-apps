"""Email intake: from an object SES dropped in S3 to `Receipt` rows.

SES writes every message it accepts to `receipts/inbound/<message id>` (see `services.storage`).
Nothing here is pushed to us: a scheduler job polls that prefix, and the rest runs on the
`receipts` queue.

1. **`poll`** (job `receipts.poll_inbound`, every minute): list up to 100 keys; each key without an
   `InboundMessage` row gets one (`pending`) and an `ingest_message` task, queued on commit. It
   parses nothing. It also re-queues `pending` rows that are due -- never picked up for 10
   minutes (a crash between the commit and the enqueue), or whose retry time has come -- deletes
   the object of a `done` message that is still listed (its delete failed), and finishes moving
   a `failed` message's object out of the prefix (see Retries).
2. **`ingest`** (task `ingest_message`): claim the row, download the object, parse it
   (`services.mime`), and for each recipient that is an *active* inbox code do the work below in
   one transaction, then delete the object and mark the row `done`.

   Per recipient, in this order (the first that applies decides the `InboundEmail.status`):

   - SES flagged it as spam / a virus (`FAIL`): `rejected_spam` / `rejected_virus`.
   - It is a Gmail forwarding confirmation (a known sender, authenticated by SES): stored as
     `forwarding_confirmation` with the code and link it contained. Free, no receipts, and it skips
     the allowlist -- the owner must be able to finish setting up the forwarding that delivers
     their receipts.
   - The account has a sender allowlist and the sender is not on it, or SES did not authenticate
     the address that matched (`mime.Verdicts.vouches_for`: SPF pass for the very envelope sender
     that matched, DMARC pass for the domain of a matching `From`, else an aligned DKIM pass, all
     from SES's `Authentication-Results`): `rejected_sender`. Empty allowlist =
     anyone, no authentication needed.
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
`MAX_ATTEMPTS` it is `failed`. An unrecoverable message (over the size guard, too many parts,
unparseable, object gone) fails at once. A worker that dies mid-ingest just lets the lease run out.
The final writes (`done`, `failed`, the retry schedule) only apply while `attempts` is still the
value the writing worker claimed with, so a slow worker whose lease ran out can't disturb the
one that took over.

**A failed message's object is moved** to `receipts/failed/<name>` (`InboundMessage.failed_key`;
`s3_key` stays the original key, which identifies the message) -- it must not stay under
`receipts/inbound/`, where a pile of failures would crowd new mail out of the poller's listing
(anyone can make a message fail). Admin "retry" moves it back and resets the row. The move is
copy, record, delete, and the poller finishes it if it was interrupted.

Each recipient's work is done so that "the `InboundEmail` row exists" means "fully done" and a
retry skips recipients already handled. Ids (the email's and each receipt's) are derived from
(message, recipient[, leaf]) with `uuid5`, so the S3 keys are the same on every try: objects
written by a try that died hard (OOM, SIGKILL -- no cleanup ran) are overwritten by the next one
instead of being orphaned. The objects are written first, then one short transaction takes the
cap slots and creates the rows (so the account row is not locked during uploads); if it fails the
objects are deleted again.

Delivered mail is untrusted input: everything here is bounded (raw size, number of MIME parts,
nesting depth, per-file size, the stored `.eml`, header lengths, rows per user per day) and nothing
from the message reaches a subject line or a key.
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
from .accounts import add_stored_bytes, intake_slots_remaining, take_intake_slot
from .addresses import matches_allowlist
from .errors import DailyLimitExceededError
from .processing import enqueue_processing
from .queue import enqueue_after_commit
from .storage import INBOUND_PREFIX, email_key, failed_key_for, file_key, get_storage

logger = logging.getLogger(__name__)

#: Keys listed per poll.
POLL_BATCH = 100
#: A `pending` message nobody has claimed for this long was probably never queued.
PENDING_STALE_AFTER = timedelta(minutes=10)

#: A `done` message that produced no `InboundEmail` (mail for an unknown or retired code, a
#: message nobody wanted) is only a bookkeeping row; it is deleted after this long, measured from
#: when it went `done` (`processed_at`). One that did produce an email stays, since the user sees
#: that email (and deleting the message would cascade).
#: **Keep this well above the 14-day expiry the `infra` repo puts on `receipts/inbound/`.** `poll`
#: registers any key under that prefix it has no row for, so a row may only go once its object is
#: gone. `purge_done_messages` deletes the object itself before the row; the expiry is the second
#: guard, for an object that is somehow still there.
DONE_MESSAGE_RETENTION = timedelta(days=30)
#: Rows per batch: short transactions and small WAL bursts on a table that was never pruned.
PURGE_BATCH = 1000
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
#: The window the per-user `max_inbound_rows_per_day` ceiling counts rows over.
ROWS_WINDOW = timedelta(hours=24)
#: How often `_store` re-plans when the daily cap changes under it (another upload took the slots
#: it was counting on) before giving up to the ordinary retry.
STORE_ROUNDS = 3

#: Namespace of the deterministic ids (`email_id_for`, `receipt_id_for`). Random, and fixed forever:
#: changing it would change the keys a retry writes.
_ID_NAMESPACE = uuid.UUID('4b7d9a2e-6c1f-4e58-9a3d-0f5e2c8b7a14')


class PermanentIngestError(Exception):
    """A message that can never be ingested, however often it is retried (the row fails at once)."""


class _SlotsChangedError(Exception):
    """The daily cap changed between planning how many files to store and taking the slots."""


def email_id_for(message_id: int, recipient: str) -> uuid.UUID:
    """The id of the `InboundEmail` for one recipient of one message: the same on every try."""
    return uuid.uuid5(_ID_NAMESPACE, f'email:{message_id}:{recipient}')


def receipt_id_for(message_id: int, recipient: str, leaf_index: int) -> uuid.UUID:
    """The id of the `Receipt` made from one part of one message for one recipient."""
    return uuid.uuid5(_ID_NAMESPACE, f'receipt:{message_id}:{recipient}:{leaf_index}')


# -- 1. Polling ---------------------------------------------------------------------------------


def poll() -> None:
    """One run of the `receipts.poll_inbound` job. Keep it light: one S3 listing, a few queries."""
    storage = get_storage()
    keys = [
        key
        for key in storage.list_keys(INBOUND_PREFIX, max_keys=POLL_BATCH)
        if key != INBOUND_PREFIX and not key.endswith('/')
    ]
    known = {m.s3_key: m for m in InboundMessage.objects.filter(s3_key__in=keys)}
    for key in keys:
        try:
            message = known.get(key)
            if message is None:
                _register(key)
            elif message.status == InboundMessageStatus.DONE:
                # Done, but the delete after it failed: finish it.
                delete_inbound_object(key)
            elif message.status == InboundMessageStatus.FAILED:
                # Failed, but the move out of this prefix didn't finish: finish it.
                park_failed_object(message)
        except Exception:
            logger.exception('Could not handle inbound key %s', key)
    _requeue_due()


def purge_done_messages() -> int:
    """Delete `done` messages older than `DONE_MESSAGE_RETENTION` (from `processed_at`, else
    `created_at` for a row without one) that have no `InboundEmail`, in batches. `pending` and
    `failed` ones are never touched (a failed one is parked for the admin to retry).

    A batch's S3 objects are deleted before its rows (a missing key is a no-op): a row that is gone
    while its object is not would be registered and ingested again by `poll`. A storage error
    propagates, leaving the rows for the next run, and the scheduler records it as the job's
    `last_error`. Idempotent; returns how many rows went."""
    cutoff = timezone.now() - DONE_MESSAGE_RETENTION
    total = 0
    while True:
        batch = list(
            InboundMessage.objects.filter(
                Q(processed_at__lte=cutoff) | Q(processed_at__isnull=True, created_at__lte=cutoff),
                status=InboundMessageStatus.DONE,
                emails__isnull=True,
            ).values_list('pk', 's3_key', 'failed_key')[:PURGE_BATCH]
        )
        if not batch:
            return total
        keys = [key for _, s3_key, failed_key in batch for key in (s3_key, failed_key) if key]
        get_storage().delete_objects(keys)
        # Re-check "no emails" in the delete itself: the FK cascades, and a row that gained an
        # email since the read above must not take it along.
        total += InboundMessage.objects.filter(
            pk__in=[pk for pk, _, _ in batch], emails__isnull=True
        ).delete()[0]


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


def park_failed_object(message: InboundMessage) -> None:
    """Move a failed message's object out of the polled prefix: copy it to `receipts/failed/...`,
    record where, delete the original. Idempotent and safe to interrupt (the copy is repeated, the
    record only ever points at an object that exists); a failure is logged and the poller tries
    again. Nothing to do if the object is already gone."""
    destination = message.failed_key or failed_key_for(message.s3_key)
    try:
        get_storage().copy_object(message.s3_key, destination)
    except ClientError as exc:
        if not _is_missing(exc):
            logger.exception('Could not move the failed object %s', message.s3_key)
        return
    except BotoCoreError:
        logger.exception('Could not move the failed object %s', message.s3_key)
        return
    InboundMessage.objects.filter(pk=message.pk).update(failed_key=destination)
    delete_inbound_object(message.s3_key)


def reset_message_for_retry(message: InboundMessage) -> bool:
    """Put a `failed` message back to `pending` with a fresh attempt count and queue it, for the
    admin's "retry" action: its object is moved back to where SES put it first. Returns whether
    anything changed. (`created_at` is left alone.)"""
    current = InboundMessage.objects.filter(
        pk=message.pk, status=InboundMessageStatus.FAILED
    ).first()
    if current is None:
        return False
    if current.failed_key:
        try:
            get_storage().copy_object(current.failed_key, current.s3_key)
        except ClientError as exc:
            if not _is_missing(exc):
                logger.exception('Could not move %s back for a retry', current.failed_key)
                return False
            # Not there: either it was never parked or it is already back; the ingest will say.
        except BotoCoreError:
            logger.exception('Could not move %s back for a retry', current.failed_key)
            return False
    changed = InboundMessage.objects.filter(
        pk=current.pk, status=InboundMessageStatus.FAILED
    ).update(
        status=InboundMessageStatus.PENDING,
        attempts=0,
        last_error='',
        failed_key='',
        processed_at=None,
        next_attempt_at=None,
        queued_at=timezone.now(),
    )
    if changed:
        if current.failed_key:
            delete_inbound_object(current.failed_key)
        enqueue_after_commit(ingest_message, current.pk)
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
    """Fail the message for good (only if the caller still holds the claim) and move its object out
    of the polled prefix."""
    updated = InboundMessage.objects.filter(
        pk=message.pk, status=InboundMessageStatus.PENDING, attempts=message.attempts
    ).update(
        status=InboundMessageStatus.FAILED,
        last_error=error[:MAX_ERROR_CHARS],
        next_attempt_at=None,
        processed_at=timezone.now(),
    )
    if updated:
        park_failed_object(message)


def _record_failure(message: InboundMessage, exc: Exception) -> None:
    error = f'{type(exc).__name__}: {exc}'
    if message.attempts >= MAX_ATTEMPTS:
        _mark_failed(message, error)
        return
    delay = RETRY_BACKOFF[min(message.attempts, len(RETRY_BACKOFF)) - 1]
    InboundMessage.objects.filter(
        pk=message.pk, status=InboundMessageStatus.PENDING, attempts=message.attempts
    ).update(
        last_error=error[:MAX_ERROR_CHARS],
        next_attempt_at=timezone.now() + delay,
        queued_at=None,
    )


def _mark_done(message: InboundMessage) -> bool:
    """Mark the message done, if the caller still holds the claim. Returns whether it did."""
    return bool(
        InboundMessage.objects.filter(
            pk=message.pk, status=InboundMessageStatus.PENDING, attempts=message.attempts
        ).update(
            status=InboundMessageStatus.DONE,
            processed_at=timezone.now(),
            next_attempt_at=None,
            last_error='',
        )
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
    except mime.MessageTooLargeError as exc:
        raise PermanentIngestError(str(exc)) from exc
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

    if _mark_done(message):
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
    receipt_id: uuid.UUID


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
        id=email_id_for(message.pk, recipient.address),
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
        _record_unaccepted(email, user, config, message.s3_key)
        return
    if _is_forwarding_confirmation(context):
        email.status = InboundEmailStatus.FORWARDING_CONFIRMATION
        email.forwarding_confirmation_code, email.forwarding_confirmation_url = (
            mime.extract_forwarding_confirmation(context.parsed)
        )
        _record_unaccepted(email, user, config, message.s3_key)
        return

    analysis = context.analysis(config)
    files = [
        _File(
            a.index,
            _filename(a),
            a.content_type,
            a.data,
            receipt_id_for(message.pk, recipient.address, a.index),
        )
        for a in analysis.eligible
    ]
    if not files:
        if analysis.too_large:
            email.status = InboundEmailStatus.REJECTED_TOO_LARGE
            email.note = f'A file is over the {config.max_file_bytes // (1024 * 1024)} MB limit.'
        elif analysis.body is not None and len(analysis.body.data) > config.max_file_bytes:
            email.status = InboundEmailStatus.REJECTED_TOO_LARGE
            email.note = 'The message body is too large.'
        elif analysis.body is not None:
            body = analysis.body
            files = [
                _File(
                    body.index,
                    '',
                    body.content_type,
                    body.data,
                    receipt_id_for(message.pk, recipient.address, body.index),
                )
            ]
        else:
            email.status = InboundEmailStatus.NO_RECEIPT
            email.note = 'No supported attachment and no readable text.'
        if not files:
            _record_unaccepted(email, user, config, message.s3_key)
            return

    _store(context, config, user, email, analysis, files)


def _record_unaccepted(
    email: InboundEmail, user: User, config: ReceiptsSettings, source_key: str
) -> None:
    """Save the row of an email that stored nothing (rejected, ignored, a forwarding confirmation)
    -- unless the user already has `max_inbound_rows_per_day` rows from the last 24 hours, in which
    case it is only logged: a flood of junk must not fill someone's inbound list or the database.
    (Not saving it means a retry decides again, harmlessly.)"""
    recent = InboundEmail.objects.filter(
        user=user, created_at__gte=timezone.now() - ROWS_WINDOW
    ).count()
    if recent >= config.max_inbound_rows_per_day:
        logger.info(
            'Inbound %s: %s email for user %s not recorded (%s rows in the last 24h)',
            source_key,
            email.status,
            user.pk,
            recent,
        )
        return
    email.save(force_insert=True)


def _is_listed(allowlist: Sequence[str], address: str) -> bool:
    return bool(address) and matches_allowlist(allowlist, address)


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

        def is_listed(address: str) -> bool:
            return _is_listed(allowlist, address)

        if not (is_listed(context.from_address) or is_listed(context.envelope_sender)):
            return InboundEmailStatus.REJECTED_SENDER, 'The sender is not on the accepted list.'
        if not verdicts.vouches_for(
            from_address=context.from_address,
            envelope_sender=context.envelope_sender,
            is_listed=is_listed,
        ):
            return (
                InboundEmailStatus.REJECTED_SENDER,
                'The sender could not be verified (no SPF pass for the envelope sender, or no '
                'DMARC or DKIM pass for the From domain).',
            )
    return None


def _is_forwarding_confirmation(context: _Context) -> bool:
    return context.verdicts.vouches_for(
        from_address=context.from_address,
        envelope_sender=context.envelope_sender,
        is_listed=mime.is_forwarding_confirmation_sender,
    )


def _store(
    context: _Context,
    config: ReceiptsSettings,
    user: User,
    email: InboundEmail,
    analysis: mime.Analysis,
    files: list[_File],
) -> None:
    """Store the files (as many as the daily cap allows) and create the rows.

    The objects are written first, outside any transaction; then one short transaction takes the
    cap slots (locking the account row only for its own duration), creates the rows and adds the
    bytes. If the transaction fails the objects are deleted again. The number of files is planned
    from an unlocked look at the cap; if another upload takes slots in between, the plan is made
    again."""
    limit = config.max_files_per_user_per_day
    for _ in range(STORE_ROUNDS):
        planned = files[: intake_slots_remaining(user, limit)]
        if not planned:
            email.status = InboundEmailStatus.REJECTED_CAP
            email.note = f'Daily limit of {limit} receipts reached.'
            _record_unaccepted(email, user, config, context.message.s3_key)
            return
        try:
            _store_round(context, config, user, email, analysis, files, planned)
        except _SlotsChangedError:
            continue
        return
    raise RuntimeError('The daily receipt cap kept changing while storing; will retry.')


def _store_round(
    context: _Context,
    config: ReceiptsSettings,
    user: User,
    email: InboundEmail,
    analysis: mime.Analysis,
    files: list[_File],
    planned: list[_File],
) -> None:
    limit = config.max_files_per_user_per_day
    notes = []
    if len(planned) < len(files):
        notes.append(
            f'{len(files) - len(planned)} of {len(files)} files skipped: daily limit of '
            f'{limit} receipts reached.'
        )
    if analysis.too_large:
        notes.append(f'{len(analysis.too_large)} file(s) over the size limit skipped.')
    email.status = InboundEmailStatus.ACCEPTED
    email.note = ' '.join(notes)[:255]

    eml = mime.build_stripped_eml(context.raw, context.parsed, _markers(analysis, planned))
    email.raw_key = email_key(user.pk, email.pk)
    email.size_bytes = len(eml)

    storage = get_storage()
    written: list[str] = []
    try:
        for item in planned:
            key = file_key(user.pk, item.receipt_id)
            storage.put_object(key, item.data, content_type=item.content_type)
            written.append(key)
        storage.put_object(email.raw_key, eml, content_type='message/rfc822')
        written.append(email.raw_key)

        with transaction.atomic():
            for _ in planned:
                try:
                    take_intake_slot(user, limit)
                except DailyLimitExceededError:
                    raise _SlotsChangedError from None
            email.save(force_insert=True)
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
                for item in planned
            )
            add_stored_bytes(user.pk, sum(len(item.data) for item in planned) + len(eml))
            for item in planned:
                enqueue_processing(item.receipt_id)
    except BaseException as exc:
        # Another worker may have finished this very recipient meanwhile (the keys are the same
        # for both of us): then the objects are its, and must stay.
        completed_elsewhere = (
            not isinstance(exc, _SlotsChangedError)
            and InboundEmail.objects.filter(
                message=email.message, recipient=email.recipient
            ).exists()
        )
        if not completed_elsewhere:
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
