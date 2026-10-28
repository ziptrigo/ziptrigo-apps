"""Add tasks by email (issue #186): the inbox addresses, the messages SES drops in S3 and what
became of each recipient. The shape is Receipts' (`apps.receipts.models.inbox_code` and
`inbound`), adapted: an email becomes a task, not a receipt, and no copy of the message is kept."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, models, transaction
from django.utils.crypto import get_random_string

from apps.accounts.models import User

from .account import TodoAccount
from .task import Task

if TYPE_CHECKING:
    from django.db.models import Manager

CODE_LENGTH = 8

#: Lowercase letters and digits minus the look-alikes (`i`, `l`, `o`, `0`, `1`), so a code read
#: aloud or off a screenshot is hard to get wrong. 31 symbols ^ 8 is about 8.5e11 codes.
CODE_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'

_MAX_COLLISION_RETRIES = 20


def generate_code() -> str:
    """A fresh random code (not checked for uniqueness; `InboxCode.issue` does that)."""
    return get_random_string(CODE_LENGTH, allowed_chars=CODE_ALPHABET)


class InboxCode(models.Model):
    """One `todo+<code>@...` address of an account. Exactly one is active at a time; regenerating
    retires the old one immediately. Retired codes are kept forever and never reissued (`code` is
    unique across all rows), so an old rule can never deliver to someone else."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    account = models.ForeignKey(TodoAccount, on_delete=models.CASCADE, related_name='codes')
    code = cast(str, models.CharField(max_length=CODE_LENGTH, unique=True))
    active = cast(bool, models.BooleanField(default=True))
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    retired_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['account'],
                condition=models.Q(active=True),
                name='todo_one_active_inbox_code_per_account',
            )
        ]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.code} ({"active" if self.active else "retired"})'

    @classmethod
    def issue(cls, account: TodoAccount) -> 'InboxCode':
        """Create a new active code for `account` with a code no row (active or retired) has ever
        had, retrying on the (unlikely) collision."""
        for _ in range(_MAX_COLLISION_RETRIES):
            code = generate_code()
            if cls.objects.filter(code=code).exists():
                continue
            try:
                # A savepoint, so losing a race on the unique column doesn't poison a caller's
                # transaction.
                with transaction.atomic():
                    return cls.objects.create(account=account, code=code)
            except IntegrityError:
                if cls.objects.filter(account=account, active=True).exists():
                    raise
                continue
        raise RuntimeError('Could not generate a unique inbox code')  # pragma: no cover


class InboundMessageStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    DONE = 'done', 'Done'
    FAILED = 'failed', 'Failed'


class InboundMessage(models.Model):
    """One row per S3 object found under `todo/inbound/` (SES writes one object per received
    message, named after its message id). It makes polling the prefix idempotent -- a key that
    already has a row is never ingested twice -- and failures are visible in the admin and with
    SQL. The poller (`services.inbound.poll`) creates the rows; the `ingest_message` task consumes
    them.

    `next_attempt_at` is the one "not before" timestamp of the ingest: null for a fresh message,
    the lease while a worker has it claimed (a crashed worker's lease simply runs out, and the
    poller then re-queues it), and the retry time after a failure. `attempts` counts claims (and
    doubles as the claim token: the final writes of an ingest only apply while `attempts` is still
    the value that worker claimed with).

    `s3_key` is always where SES put the object, which is what identifies the message. A `failed`
    message's object is moved out of the polled prefix to `failed_key` (`todo/failed/...`), so
    failures can never crowd new mail out of the listing; the admin "retry" moves it back.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: int
    emails: ClassVar['Manager']

    s3_key = cast(str, models.CharField(max_length=512, unique=True))
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=InboundMessageStatus.choices,
            default=InboundMessageStatus.PENDING,
        ),
    )
    failed_key = cast(
        str,
        models.CharField(
            max_length=512,
            blank=True,
            default='',
            help_text='Where the object of a failed message was moved to (outside the prefix '
            'the poller lists). Empty while it is still at the S3 key.',
        ),
    )
    attempts = cast(int, models.PositiveSmallIntegerField(default=0))
    last_error = cast(str, models.TextField(blank=True, default=''))
    next_attempt_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='Not before this time: the lease while an ingest is running, or when to '
            'retry after a failure. Empty for a message nobody has claimed yet.',
        ),
    )
    queued_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When the ingest task was last queued, so the poller does not queue the '
            'same message again every minute while the queue is behind.',
        ),
    )
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    processed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        indexes = [models.Index(fields=['status', 'created_at'], name='todo_inbmsg_status_created')]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.s3_key} ({self.status})'


class InboundEmailStatus(models.TextChoices):
    ACCEPTED = 'accepted', 'Accepted'
    REJECTED_SENDER = 'rejected_sender', 'Rejected: sender not allowed'
    REJECTED_SPAM = 'rejected_spam', 'Rejected: flagged as spam'
    REJECTED_VIRUS = 'rejected_virus', 'Rejected: flagged as a virus'
    REJECTED_CREDITS = 'rejected_credits', 'Rejected: not enough credits'
    REJECTED_TOO_LARGE = 'rejected_too_large', 'Rejected: too large'
    REJECTED_TASK_LIMIT = 'rejected_task_limit', 'Rejected: too many open tasks'
    NO_CONTENT = 'no_content', 'No content'


class InboundEmail(models.Model):
    """One row per matched recipient of an `InboundMessage` (one message can reach several
    users). A rejected email keeps only this metadata, so the user can see what was dropped; an
    accepted one links to the `task` it created (`SET_NULL`: deleting the task keeps the line).

    `note` is a short, human-readable remark about what ingest did that the status alone doesn't
    say (e.g. "1 of 3 files skipped: too large").
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_inbound_emails'
        ),
    )
    user_id: UUID
    message = models.ForeignKey(InboundMessage, on_delete=models.CASCADE, related_name='emails')
    recipient = cast(str, models.CharField(max_length=320))
    from_address = cast(str, models.CharField(max_length=320, blank=True, default=''))
    envelope_sender = cast(str, models.CharField(max_length=320, blank=True, default=''))
    sender_authenticated = cast(
        bool,
        models.BooleanField(
            default=False,
            help_text='Whether SES authenticated the From address (DMARC for its domain, else an '
            'aligned DKIM signature, or SPF when it is also the envelope sender). Recorded '
            'whatever the accepted-senders list says, which an empty one does not check.',
        ),
    )
    subject = cast(str, models.CharField(max_length=500, blank=True, default=''))
    received_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    status = cast(
        str,
        models.CharField(
            max_length=32,
            choices=InboundEmailStatus.choices,
            default=InboundEmailStatus.ACCEPTED,
        ),
    )
    note = cast(str, models.CharField(max_length=255, blank=True, default=''))
    task = cast(
        'Task | None',
        models.ForeignKey(
            Task,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='inbound_emails',
        ),
    )
    task_id: UUID | None
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['message', 'recipient'], name='todo_inboundemail_message_recipient'
            )
        ]
        indexes = [models.Index(fields=['user', '-created_at'], name='todo_inbemail_user_created')]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.recipient} ({self.status})'
