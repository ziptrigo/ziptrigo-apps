"""Add tasks by email (issue #186): from an object SES dropped in S3 to a `Task`.

Receipts' intake (`apps.receipts.services.inbound`) reproduced for `todo/inbound/`, with the
mail-reading code shared in `apps.core.services.mail`. SES writes every message it accepts to
`todo/inbound/<message id>` (see `services.storage`). Nothing is pushed to us: a scheduler job
polls that prefix, and the rest runs on the `default` queue.

1. **`poll`** (job `todo.poll_inbound`, every minute): list up to 100 keys; each key without an
   `InboundMessage` row gets one (`pending`) and an `ingest_message` task, queued on commit. It
   parses nothing. It also re-queues `pending` rows that are due -- never picked up for 10
   minutes (a crash between the commit and the enqueue), or whose retry time has come -- deletes
   the object of a `done` message that is still listed (its delete failed), and finishes moving
   a `failed` message's object out of the prefix (see Retries).
2. **`ingest`** (task `ingest_message`): claim the row, download the object, parse it
   (`core.services.mail.mime`), and for each recipient that is an *active* inbox code of an active
   user do the work below in one transaction, then delete the object and mark the row `done`.

   Per recipient, in this order (the first that applies decides the `InboundEmail.status`):

   - SES flagged it as spam / a virus (`FAIL`): `rejected_spam` / `rejected_virus`.
   - The account has a sender allowlist and the sender is not on it, or SES did not authenticate
     the address that matched (`Verdicts.vouches_for`, exactly Receipts' rule): `rejected_sender`.
     An empty allowlist accepts anyone, with no authentication needed.
   - Nothing in it (no subject, no body, no file): `no_content`. Files that were all too large:
     `rejected_too_large`.
   - The user is already at the open-task cap: `rejected_task_limit`. (Checked here, with the
     credits below, before the AI call and the uploads; `create_task` checks again under its lock.)
   - The task would not be paid for (not enough credits, or creation blocked by an unpaid repeating
     task): `rejected_credits`, nothing stored, and the user's in-app notice (one per user, however
     many emails: see `_notify_not_created`).
   - Otherwise a task: the subject is the title (`Fwd:`/`Re:` prefixes removed), the body the
     description (`text/plain` as it is, else the HTML as text), the attachments the task's
     attachments (any type), and the AI reads a due date from the subject. The reading spends the
     user's `TODO_PARSE_USER` / `TODO_PARSE_USER_DAY` rate limits, **the same budget as the web
     quick add** (one shared AI budget per user): with an empty allowlist anyone who knows the
     address can use some of it, and past it the task is simply created undated.

   A rejected email keeps its metadata only (from, subject, time, status, note). The task is
   created with `services.tasks.create_task` (source `email`, never unpaid), so it counts toward
   the free allowance and costs what any task does.

   The attachments are stored server-side with `put_object` at `attachment_key` and their rows are
   created already `uploaded`, with their bytes added to the account's `stored_bytes`. At most
   `max_attachments_per_task` and `max_attachment_bytes` each; the ones left out are counted in the
   email's `note`. A **frozen** storage account still gets the task, but no attachments are stored.

**Retries.** A claim bumps `attempts` and sets `next_attempt_at` to a lease. A failure puts the row
back to `pending` with a backoff (1 min, then 5 min) and the poller re-queues it when due; after
`MAX_ATTEMPTS` it is `failed`. An unrecoverable message (over the size guard, too many parts,
unparseable, object gone) fails at once. A worker that dies mid-ingest just lets the lease run out.
The final writes (`done`, `failed`, the retry schedule) only apply while `attempts` is still the
value the writing worker claimed with, so a slow worker whose lease ran out can't disturb the
one that took over.

**A failed message's object is moved** to `todo/failed/<name>` (`InboundMessage.failed_key`;
`s3_key` stays the original key, which identifies the message) -- it must not stay under
`todo/inbound/`, where a pile of failures would crowd new mail out of the poller's listing
(anyone can make a message fail). Admin "retry" moves it back and resets the row.

Each recipient's work is done so that "the `InboundEmail` row exists" means "fully done" and a
retry skips recipients already handled: the task, the attachment rows and the email row are one
transaction. The email's id and each attachment's id are derived from (message, recipient[, leaf])
with `uuid5`, so the S3 keys are the same on every try: objects written by a try that died hard
(OOM, SIGKILL -- no cleanup ran) are overwritten by the next one instead of being orphaned. The
objects are written first, then the transaction creates the task and the rows; if it fails the
objects are deleted again.

Delivered mail is untrusted input: everything here is bounded (raw size, number of MIME parts,
nesting depth, per-file size, header lengths, the description, rows per user per day) and nothing
from the message reaches a subject line or a key.
"""

import base64
import hashlib
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from email.message import Message

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.tasks import task
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today, user_tz
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core import ratelimit
from apps.core.services import notifications
from apps.core.services.mail import mime
from apps.core.services.mail.addresses import matches_allowlist
from apps.core.services.mail.images import IMAGE_TYPES, image_dimensions
from apps.core.services.mail.text import html_to_text
from apps.core.services.queue import enqueue_after_commit
from apps.core.services.sniff import SNIFF_BYTES, sniff_inline_type
from apps.core.services.storage import ObjectTooLargeError

from ..limits import INLINE_IMAGE_MIN_PX
from ..models import (
    Attachment,
    InboundEmail,
    InboundEmailStatus,
    InboundMessage,
    InboundMessageStatus,
    InboxCode,
    TaskSource,
    TodoAccount,
    TodoSettings,
)
from . import attachments as attachments_service
from . import billing, dates, parsing, tasks
from .accounts import add_stored_bytes
from .limits import DESCRIPTION_MAX, TITLE_MAX
from .storage import INBOUND_PREFIX, attachment_key, failed_key_for, get_storage

logger = logging.getLogger(__name__)

#: Keys listed per poll.
POLL_BATCH = 100
#: A `pending` message nobody has claimed for this long was probably never queued.
PENDING_STALE_AFTER = timedelta(minutes=10)

#: A `done` message that produced no `InboundEmail` (mail for an unknown or retired code, a
#: message nobody wanted) is only a bookkeeping row; it is deleted after this long, measured from
#: when it went `done` (`processed_at`). One that did produce an email stays, since the user sees
#: that email (and deleting the message would cascade).
#: **Keep this well above the expiry the `infra` repo puts on `todo/inbound/`** (14 days, like
#: Receipts'): `poll` registers any key under that prefix it has no row for, so a row may only go
#: once its object is gone. `purge_done_messages` deletes the object itself before the row; the
#: expiry is the second guard, for an object that is somehow still there.
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
#: Only this much of a `text/plain` body is read, sliced as soon as it is decoded and before any
#: copy or clean-up (the result is cut to `DESCRIPTION_MAX`): a message can carry ~30 MB of text,
#: and the cleaning copies it several times.
MAX_TEXT_CHARS = DESCRIPTION_MAX * 2
#: Only this much of an HTML body is turned into text, sliced right after decoding as well. Larger
#: than the text bound because markup is most of an HTML body (the result is cut to
#: `DESCRIPTION_MAX`).
MAX_HTML_CHARS = 1_000_000
#: What replaces a missing subject.
NO_SUBJECT = '(no subject)'
#: The line that marks a description cut at `DESCRIPTION_MAX`.
CUT_MARK = '\n…'
#: The rate limits the AI reading of the subject spends, as the web's `POST /todo/parse/` does.
PARSE_RULES = ('TODO_PARSE_USER', 'TODO_PARSE_USER_DAY')
#: The note of an email refused at the open-task cap.
TASK_LIMIT_NOTE = 'Too many open tasks.'
#: The key of the one in-app notice about emailed tasks refused for lack of credits (per user).
CREDITS_NOTICE_KEY = 'todo_email_credits'

#: Namespace of the deterministic ids (`email_id_for`, `attachment_id_for`). Random, and fixed
#: forever: changing it would change the keys a retry writes.
_ID_NAMESPACE = uuid.UUID('b3e1c7a4-52d9-4f60-8a1e-7c94d02f6b35')

_SUBJECT_PREFIX = re.compile(r'^\s*(?:(?:fwd?|re)\s*:\s*)+', re.I)
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


class PermanentIngestError(Exception):
    """A message that can never be ingested, however often it is retried (the row fails at once)."""


def email_id_for(message_id: int, recipient: str) -> uuid.UUID:
    """The id of the `InboundEmail` for one recipient of one message: the same on every try."""
    return uuid.uuid5(_ID_NAMESPACE, f'email:{message_id}:{recipient}')


def attachment_id_for(message_id: int, recipient: str, leaf_index: int) -> uuid.UUID:
    """The id of the `Attachment` made from one part of one message for one recipient."""
    return uuid.uuid5(_ID_NAMESPACE, f'attachment:{message_id}:{recipient}:{leaf_index}')


# -- 1. Polling ---------------------------------------------------------------------------------


def poll() -> None:
    """One run of the `todo.poll_inbound` job. Keep it light: one S3 listing, a few queries."""
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
    """Move a failed message's object out of the polled prefix: copy it to `todo/failed/...`,
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


@task(queue_name='default')
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


def _inbound_address() -> str:
    return settings.TODO_INBOUND_ADDRESS


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
            mime.recipients_from_addresses(list(override), inbound_address=_inbound_address())
            if override is not None
            else mime.scan_recipients(parsed, inbound_address=_inbound_address())
        )
        context = _Context(message=message, parsed=parsed)
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
    parsed: Message
    verdicts: mime.Verdicts = field(init=False)
    from_address: str = field(init=False)
    envelope_sender: str = field(init=False)
    subject: str = field(init=False)
    received_at: datetime = field(init=False)
    _analysis: mime.Analysis | None = field(default=None, init=False)
    _description: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.verdicts = mime.read_verdicts(self.parsed)
        self.from_address = mime.from_address(self.parsed)
        self.envelope_sender = mime.envelope_sender(self.parsed)
        self.subject = mime.subject(self.parsed)
        self.received_at = mime.received_at(self.parsed)

    def analysis(self, config: TodoSettings) -> mime.Analysis:
        """Which parts are attachments. Any type is accepted (`allowed_types=None`), up to the
        per-file cap; no body is chosen (`html_to_text` is not given): the description is read by
        `description`."""
        if self._analysis is None:
            self._analysis = mime.analyze(
                self.parsed,
                allowed_types=None,
                max_file_bytes=config.max_attachment_bytes,
                min_inline_image_px=INLINE_IMAGE_MIN_PX,
                sniff=_sniff,
                image_dimensions=image_dimensions,
                image_types=IMAGE_TYPES,
            )
        return self._analysis

    def description(self) -> str:
        if self._description is None:
            self._description = body_text(self.parsed)
        return self._description


def _sniff(data: bytes) -> str | None:
    return sniff_inline_type(data[:SNIFF_BYTES])


@dataclass
class _File:
    """One attachment about to be stored."""

    attachment_id: uuid.UUID
    filename: str
    content_type: str
    data: bytes


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

    config = TodoSettings.load()
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

    email.sender_authenticated = _authenticated(context)
    rejection = _rejection(context, account)
    if rejection is not None:
        email.status, email.note = rejection
        _record_unaccepted(email, user, config, message.s3_key)
        return

    title = clean_subject(context.subject)
    description = context.description()
    analysis = context.analysis(config)
    candidates = [a for a in analysis.attachments if a.skip in ('', mime.SKIP_TOO_LARGE)]
    eligible = analysis.eligible
    if not (title or description or candidates):
        email.status = InboundEmailStatus.NO_CONTENT
        email.note = 'No subject, no text and no files.'
        _record_unaccepted(email, user, config, message.s3_key)
        return
    if not (title or description or eligible):
        email.status = InboundEmailStatus.REJECTED_TOO_LARGE
        email.note = f'Every file is over the {_megabytes(config.max_attachment_bytes)} MB limit.'
        _record_unaccepted(email, user, config, message.s3_key)
        return

    # The cheap refusals that need the database come before the AI call and the uploads.
    if _at_task_cap(user, config):
        email.status = InboundEmailStatus.REJECTED_TASK_LIMIT
        email.note = TASK_LIMIT_NOTE
        _record_unaccepted(email, user, config, message.s3_key)
        return
    if account.creation_blocked_at is not None or _cannot_afford(account, config):
        _reject_for_credits(email, user, config, message.s3_key)
        return

    frozen = account.frozen_at is not None
    stored = [] if frozen else eligible[: config.max_attachments_per_task]
    files = [
        _File(
            attachment_id_for(message.pk, recipient.address, info.index),
            attachments_service.clean_filename(mime.clean_text(info.filename, 255)),
            info.content_type or 'application/octet-stream',
            info.data,
        )
        for info in stored
    ]
    note = _files_note(
        total=len(candidates),
        stored=len(files),
        too_large=len(analysis.too_large),
        over_cap=0 if frozen else len(eligible) - len(stored),
        frozen=frozen and bool(candidates),
        max_per_task=config.max_attachments_per_task,
        max_bytes=config.max_attachment_bytes,
    )

    # The AI call is slow and costs us: outside any transaction, after the cheap refusals above
    # (the cap on open tasks and the credits).
    request = _parse_request(user, account, title or NO_SUBJECT)
    reading = _read_date(user, request)
    email.note = note
    _create(context, user, email, title or NO_SUBJECT, description, reading, files, config)


def _at_task_cap(user: User, config: TodoSettings) -> bool:
    """A cheap look before the AI call and the uploads: the user already has the most open tasks
    allowed. (`create_task` decides for good, under the account lock.)"""
    return dates.open_tasks(user).count() >= config.max_open_tasks_per_user


def _cannot_afford(account: TodoAccount, config: TodoSettings) -> bool:
    """A cheap look before the AI call and the uploads: the task is beyond the free allowance and
    the balance can't pay its price. (The real decision is `create_task`'s, under the lock.)"""
    if config.task_price <= 0:
        return False
    used, free = billing.month_usage(account, config)
    return used >= free and get_balance(account.user) < config.task_price


def _reject_for_credits(
    email: InboundEmail, user: User, config: TodoSettings, source_key: str
) -> None:
    email.status = InboundEmailStatus.REJECTED_CREDITS
    email.note = 'Not enough credits to create the task.'
    _record_unaccepted(email, user, config, source_key, notify=True)


def _megabytes(size: int) -> str:
    return attachments_service.format_megabytes(size)


def _files_note(
    *,
    total: int,
    stored: int,
    too_large: int,
    over_cap: int,
    frozen: bool,
    max_per_task: int,
    max_bytes: int,
) -> str:
    """What happened to the files that were left out: `'1 of 3 files skipped: too large'`."""
    if frozen:
        return 'files skipped: storage is paused'
    reasons = []
    if too_large:
        reasons.append(f'{too_large} too large' if over_cap else 'too large')
    if over_cap:
        reasons.append(
            f'{over_cap} over the limit of {max_per_task} per task' if too_large else 'too many'
        )
    if not reasons:
        return ''
    return f'{total - stored} of {total} files skipped: {", ".join(reasons)}'[:255]


def _parse_request(user: User, account: TodoAccount, title: str) -> parsing.ParseRequest:
    tz = user_tz(user)
    today = user_today(user)
    now = timezone.now().astimezone(tz)
    return parsing.ParseRequest(
        title=title,
        today=today,
        weekday=parsing.WEEKDAY_NAMES[today.weekday()],
        timezone=tz.key,
        default_time=account.default_due_time,
        now_local=time(now.hour, now.minute),
    )


def _read_date(user: User, request: parsing.ParseRequest) -> parsing.TitleParse | None:
    """The AI's reading of the subject (`None` for no date, or when it can't be had: not
    configured, busy, failing or over this user's AI rate limits -- the task is then undated).
    `looks_dated` runs first inside `parse_title`, so a subject with no digit or date word makes
    no call."""

    def charge() -> bool:
        results = [ratelimit.hit_user(user, rule) for rule in PARSE_RULES]
        return all(result.allowed for result in results)

    try:
        result = parsing.parse_title(request, charge=charge)
    except (
        parsing.ParseUnavailable,
        parsing.ParseRateLimited,
        parsing.PermanentAIError,
    ):
        return None
    except Exception:
        logger.exception('Reading the date of an inbound email failed')
        return None
    return result if result.has_date else None


def _create(
    context: _Context,
    user: User,
    email: InboundEmail,
    title: str,
    description: str,
    reading: parsing.TitleParse | None,
    files: list[_File],
    config: TodoSettings,
) -> None:
    """Store the files, then create the task, the attachment rows and the email row in one
    transaction (the objects are deleted again if it fails). A task that can't be paid for is
    `rejected_credits` and nothing is kept."""
    storage = get_storage()
    written: list[str] = []
    try:
        for item in files:
            key = attachment_key(user.pk, item.attachment_id)
            storage.put_object(key, item.data, content_type=item.content_type)
            written.append(key)

        with transaction.atomic():
            created = _create_task(user, email, title, description, reading)
            email.status = InboundEmailStatus.ACCEPTED
            email.task = created
            email.save(force_insert=True)
            Attachment.objects.bulk_create(
                Attachment(
                    id=item.attachment_id,
                    task=created,
                    user=user,
                    filename=item.filename,
                    size_bytes=len(item.data),
                    inline_type=sniff_inline_type(item.data[:SNIFF_BYTES]) or '',
                    sha256=base64.b64encode(hashlib.sha256(item.data).digest()).decode(),
                    uploaded=True,
                )
                for item in files
            )
            add_stored_bytes(user.pk, sum(len(item.data) for item in files))
    except BaseException as exc:
        # Another worker may have finished this very recipient meanwhile (the keys are the same
        # for both of us): then the objects are its, and must stay.
        completed_elsewhere = InboundEmail.objects.filter(
            message=email.message, recipient=email.recipient
        ).exists()
        if not completed_elsewhere:
            for key in written:
                try:
                    storage.delete_object(key)
                except ClientError, BotoCoreError:
                    logger.exception('Could not delete the orphaned object %s', key)
        if isinstance(exc, _Refused):
            email.status, email.note = exc.status, exc.note
            if exc.status == InboundEmailStatus.REJECTED_CREDITS:
                _reject_for_credits(email, user, config, context.message.s3_key)
            else:
                _record_unaccepted(email, user, config, context.message.s3_key)
            return
        raise


class _Refused(Exception):
    """`create_task` refused: the email is rejected with this status and note."""

    def __init__(self, status: str, note: str) -> None:
        super().__init__(note)
        self.status = status
        self.note = note


def _create_task(
    user: User,
    email: InboundEmail,
    title: str,
    description: str,
    reading: parsing.TitleParse | None,
):
    """`tasks.create_task` for an email, with the AI's date when there is one. A date the task
    service won't take (it should not happen: the reading was validated) is dropped rather than
    losing the task."""
    try:
        if reading is not None:
            try:
                return tasks.create_task(
                    user,
                    title=reading.clean_title or title,
                    description=description,
                    due_date=reading.due_date,
                    due_time=reading.due_time,
                    rrule=reading.rrule or '',
                    source=TaskSource.EMAIL,
                    email_from=email.from_address,
                    email_from_authenticated=email.sender_authenticated,
                    allow_unpaid=False,
                )
            except ValidationError as exc:
                if not set(exc.message_dict) & {'due_date', 'due_time', 'rrule'}:
                    raise
                logger.warning('Dropping the date read from an inbound email: %s', exc.messages)
        return tasks.create_task(
            user,
            title=title,
            description=description,
            source=TaskSource.EMAIL,
            email_from=email.from_address,
            email_from_authenticated=email.sender_authenticated,
            allow_unpaid=False,
        )
    except InsufficientCreditsError as exc:
        raise _Refused(InboundEmailStatus.REJECTED_CREDITS, 'Not enough credits.') from exc
    except ValidationError as exc:
        if tasks.BLOCKED_MESSAGE in exc.messages:
            raise _Refused(InboundEmailStatus.REJECTED_CREDITS, 'Not enough credits.') from exc
        if tasks.TOO_MANY_MESSAGE in exc.messages:
            raise _Refused(InboundEmailStatus.REJECTED_TASK_LIMIT, TASK_LIMIT_NOTE) from exc
        raise


def _record_unaccepted(
    email: InboundEmail,
    user: User,
    config: TodoSettings,
    source_key: str,
    *,
    notify: bool = False,
) -> None:
    """Save the row of an email that created nothing (rejected, empty) -- unless the user already
    has `max_inbound_rows_per_day` rows from the last 24 hours, in which case it is only logged: a
    flood of junk must not fill someone's inbound list or the database. (Not saving it means a
    retry decides again, harmlessly.) With `notify`, the user is told in-app, once per recorded
    email (so the flood cap bounds the notifications too)."""
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
    with transaction.atomic():
        email.save(force_insert=True)
        if notify:
            _notify_not_created(user, email)


def _notify_not_created(user: User, email: InboundEmail) -> None:
    """The in-app notice that emailed tasks were not created for lack of credits. **One per user**,
    under a stable key: the previous one is deleted before the new one is written, so a flood of
    emails leaves a single notice (the bell shows it new again), not one per email. The text is
    fixed -- nothing the sender wrote (the subject included) reaches it -- and it links to the
    credits page, where the user tops up. In its own savepoint, logged and never raised: a failed
    notice must not lose the email's row."""
    try:
        with transaction.atomic():
            notifications.delete_for_key(user, CREDITS_NOTICE_KEY, source='todo')
            notifications.notify(
                user,
                source='todo',
                title='Emailed tasks were not created: not enough credits',
                body="Tasks sent to your To-do address couldn't be created because you don't have "
                'enough credits. Top up, then send them again.',
                url=reverse('billing:credits-history'),
                key=CREDITS_NOTICE_KEY,
            )
    except Exception:
        logger.exception('Could not notify user %s of a rejected email', user.pk)


def _is_listed(allowlist: Sequence[str], address: str) -> bool:
    return bool(address) and matches_allowlist(allowlist, address)


def _authenticated(context: _Context) -> bool:
    """Whether SES authenticated the address a page will present as the sender: the `From`
    address (DMARC for its domain, else an aligned DKIM signature, or SPF when it is also the
    envelope sender). Recorded on the email and the task, whatever the allowlist says -- an empty
    one accepts anyone, authenticated or not, and a page must not present an unproven `From` as
    who sent it. (The allowlist decision is `_rejection`'s.)"""
    from_address = context.from_address.lower()

    def is_from(address: str) -> bool:
        return bool(from_address) and address.lower() == from_address

    return context.verdicts.vouches_for(
        from_address=context.from_address,
        envelope_sender=context.envelope_sender,
        is_listed=is_from,
    )


def _rejection(context: _Context, account: TodoAccount) -> tuple[str, str] | None:
    verdicts = context.verdicts
    if verdicts.spam_failed:
        return InboundEmailStatus.REJECTED_SPAM, 'Flagged as spam.'
    if verdicts.virus_failed:
        return InboundEmailStatus.REJECTED_VIRUS, 'Flagged as a virus.'
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


# -- What the message says ----------------------------------------------------------------------


def clean_subject(subject: str) -> str:
    """The title a subject makes: a leading `Fwd:`/`Fw:`/`Re:` (any case, repeated) removed,
    whitespace collapsed, cut to `TITLE_MAX`. Empty when nothing is left."""
    stripped = _SUBJECT_PREFIX.sub('', subject)
    return mime.clean_text(' '.join(stripped.split()), TITLE_MAX)


def body_text(msg: Message) -> str:
    """The description an email makes: its `text/plain` body (taken as Markdown, as it is) when it
    has one, else its `text/html` body as text (links as `text (url)`), else empty. Only the
    message's own parts are read (not a message forwarded as an attachment) and a part marked as an
    attachment is not a body. Quoted history is kept. Cut at `DESCRIPTION_MAX` with a last line
    `…`."""
    leaves = [
        leaf
        for leaf in mime.iter_leaves(msg, into_messages=False)
        if leaf.get_content_type() in ('text/plain', 'text/html')
        and not mime.is_attachment_like(leaf)
    ]
    for content_type in ('text/plain', 'text/html'):
        part = next((leaf for leaf in leaves if leaf.get_content_type() == content_type), None)
        if part is None:
            continue
        # Sliced as soon as it is decoded: nothing below copies more than the bound.
        if content_type == 'text/html':
            text = html_to_text(
                mime.part_text(part)[:MAX_HTML_CHARS], max_chars=DESCRIPTION_MAX * 2
            )
        else:
            text = mime.part_text(part)[:MAX_TEXT_CHARS]
        cleaned = _clean_body(text)
        if cleaned:
            return cleaned
    return ''


def _clean_body(text: str) -> str:
    # Bytes the parser couldn't decode survive as lone surrogates, which the database refuses; a
    # NUL or another control character is refused by Postgres or only noise.
    text = text.encode('utf-8', 'replace').decode('utf-8')
    text = _CONTROL.sub('', text.replace('\r\n', '\n').replace('\r', '\n')).strip()
    if len(text) <= DESCRIPTION_MAX:
        return text
    return text[: DESCRIPTION_MAX - len(CUT_MARK)].rstrip() + CUT_MARK


__all__ = [
    'MAX_ATTEMPTS',
    'MAX_RAW_BYTES',
    'PermanentIngestError',
    'attachment_id_for',
    'body_text',
    'clean_subject',
    'email_id_for',
    'ingest',
    'ingest_message',
    'poll',
    'purge_done_messages',
    'reset_message_for_retry',
]
