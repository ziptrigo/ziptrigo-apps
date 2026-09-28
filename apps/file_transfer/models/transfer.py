import string
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.crypto import get_random_string

from apps.accounts.models import User

if TYPE_CHECKING:
    from django.db.models import Manager

# 22-char base62 slugs for the public download link (`/t/<slug>/`): long and random enough not to
# be guessable, short enough to paste. See spec section 4.
SLUG_LENGTH = 22
_BASE62_ALPHABET = string.ascii_letters + string.digits


def generate_slug() -> str:
    """A random base62 slug for a transfer's public download link."""
    return get_random_string(SLUG_LENGTH, allowed_chars=_BASE62_ALPHABET)


def generate_manage_token() -> str:
    """A random token for the (phase 2) anonymous manage link. Generated now to avoid a schema
    change later; unused until anonymous sending ships."""
    return get_random_string(43, allowed_chars=_BASE62_ALPHABET)


class TransferStatus(models.TextChoices):
    """Status lifecycle (spec section 15):

    `DRAFT` -> (`PENDING_CONFIRMATION` for anonymous, phase 2) -> `ACTIVE` -> `DISABLED` (by the
    sender, reversible) / `SUSPENDED` (no credits, reversible within the grace period) ->
    `EXPIRED` / `DELETED` (terminal).
    """

    DRAFT = 'draft', 'Draft'
    PENDING_CONFIRMATION = 'pending_confirmation', 'Pending confirmation'
    ACTIVE = 'active', 'Active'
    DISABLED = 'disabled', 'Disabled'
    SUSPENDED = 'suspended', 'Suspended'
    EXPIRED = 'expired', 'Expired'
    DELETED = 'deleted', 'Deleted'


#: Terminal statuses: a transfer here never becomes available again.
ENDED_STATUSES = (TransferStatus.EXPIRED, TransferStatus.DELETED)

#: Statuses a dashboard action (disable, extend, etc.) may still apply to.
ACTIONABLE_STATUSES = (TransferStatus.ACTIVE, TransferStatus.DISABLED, TransferStatus.SUSPENDED)


class ZipStatus(models.TextChoices):
    """Lazy "download all" zip build status (spec section 5, phase 2). Included on the model now
    so the column doesn't need a later migration; the feature itself isn't built yet."""

    NONE = 'none', 'Not requested'
    BUILDING = 'building', 'Building'
    READY = 'ready', 'Ready'
    FAILED = 'failed', 'Failed'


class Transfer(models.Model):
    """A WeTransfer-style file transfer: one message, a set of files, a public download link.

    Phase 1 covers logged-in senders only, so `owner` is always set and `sender_email` /
    `manage_token` / `zip_status` / `zip_key` exist only so phase 2 (anonymous senders, "download
    all") doesn't need another migration.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    # Reverse FK managers (`related_name` on `TransferFile`/`TransferRecipient`/`DownloadEvent`):
    # never declared as fields themselves, so `ty` has no way to know they exist without this
    # annotation (see the class-level comment in `apps/qr_code/models/qrcode.py`).
    files: ClassVar['Manager']
    recipients: ClassVar['Manager']
    download_events: ClassVar['Manager']

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='transfers',
            help_text='Null for an anonymous transfer (phase 2).',
        ),
    )
    # Django adds this `_id` companion attribute automatically for every `ForeignKey`; see the
    # matching comment in `apps/billing/models/credit_account.py`.
    owner_id: UUID | None
    sender_email = cast(
        str,
        models.EmailField(blank=True, default='', help_text='Set for anonymous senders.'),
    )

    # Anonymous-sender bookkeeping (phase 2). All blank/null for a logged-in transfer.
    draft_token_hash = cast(
        str,
        models.CharField(
            max_length=64,
            blank=True,
            default='',
            help_text='HMAC-SHA256 digest of a random per-draft token kept in the session that '
            'created this draft, so only that browser session may upload to or finalize it '
            "before it's confirmed. Deliberately not the session's own key (which "
            'django.contrib.auth.login() rotates, and which would otherwise leak a real, '
            'authenticated session key into this read-only admin list) -- see '
            'apps.file_transfer.services.anon_session.',
        ),
    )
    sender_ip = cast(
        'str | None',
        models.GenericIPAddressField(
            null=True,
            blank=True,
            help_text='Client IP that created the transfer (anonymous only).',
        ),
    )
    anon_cookie_id = cast(
        str,
        models.CharField(
            max_length=64,
            blank=True,
            default='',
            help_text='Opaque id from the signed anonymous-sender cookie, used alongside '
            'sender_ip for the per-day caps (spec section 13).',
        ),
    )
    email_verification_id = cast(
        'UUID | None',
        models.UUIDField(
            null=True,
            blank=True,
            help_text="The apps.core EmailVerification row backing this transfer's pending "
            'anonymous-sender confirmation.',
        ),
    )

    slug = cast(
        str,
        models.CharField(max_length=32, unique=True, default=generate_slug, editable=False),
    )
    manage_token = cast(
        str,
        models.CharField(max_length=64, blank=True, default=generate_manage_token, editable=False),
    )

    message = cast(str, models.TextField(blank=True, default='', max_length=2000))
    status = cast(
        str,
        models.CharField(
            max_length=24, choices=TransferStatus.choices, default=TransferStatus.DRAFT
        ),
    )

    expires_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    max_downloads = cast(int | None, models.PositiveIntegerField(null=True, blank=True))
    password_hash = cast(str, models.CharField(max_length=255, blank=True, default=''))
    notify_on_download = cast(bool, models.BooleanField(default=True))

    size_bytes = cast(int, models.PositiveBigIntegerField(default=0))
    completed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    last_billed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    accrued = cast(
        Decimal,
        models.DecimalField(
            max_digits=14,
            decimal_places=6,
            default=Decimal('0'),
            help_text='Fractional credits owed but not yet spent (billing.spend_credits only '
            'takes whole credits); see apps.file_transfer.services.metering.',
        ),
    )
    suspended_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    billed_days = cast(
        int,
        models.PositiveIntegerField(
            default=0,
            help_text='Number of days metered so far, for the credit ledger description '
            '(e.g. "Transfer \\"report.pdf\\", day 3").',
        ),
    )
    credits_charged = cast(
        int,
        models.PositiveIntegerField(
            default=0,
            help_text='Running total of whole credits spent for this transfer so far (dashboard '
            'display; billing.CreditTransaction has no FK back here -- billing may not import '
            'file_transfer -- so this is the only place that total lives).',
        ),
    )
    expiry_notified_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True, blank=True, help_text='When the "expires tomorrow" email was sent.'
        ),
    )

    zip_status = cast(
        str, models.CharField(max_length=16, choices=ZipStatus.choices, default=ZipStatus.NONE)
    )
    zip_key = cast(str, models.CharField(max_length=512, blank=True, default=''))
    zip_build_started_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When the current (or most recent) zip build claimed `zip_status='
            'BUILDING`. Lets a build that never finished (a worker restart mid-build) be '
            "re-claimed once it is older than `services.zip`'s lease, instead of leaving the "
            'download page polling `BUILDING` forever.',
        ),
    )

    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    deleted_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    ended_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When the transfer first became unavailable (expired, deleted, or its '
            'download limit reached). Set once, even if file deletion is deferred -- see '
            '`files_deleted_at` -- so `expire_transfers` knows when the grace window is up.',
        ),
    )
    files_deleted_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When the S3 objects were actually removed. Usually set alongside '
            '`ended_at`, except when ending the transfer would invalidate a presigned download '
            'URL still being handed out (the last download reaching `max_downloads`): then '
            'deletion is deferred until that URL has expired (see '
            '`apps.file_transfer.jobs.expire_transfers`).',
        ),
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['owner', 'status']),
            models.Index(fields=['status', 'expires_at']),
            # Looked up by services.anon_limits when checking the per-IP-per-day caps.
            models.Index(fields=['sender_ip', 'created_at']),
            models.Index(fields=['anon_cookie_id', 'created_at']),
        ]

    def __str__(self) -> str:
        return f'Transfer {self.id} ({self.status})'

    @property
    def is_ended(self) -> bool:
        return self.status in ENDED_STATUSES

    @property
    def is_actionable(self) -> bool:
        """Whether dashboard actions (disable, extend, etc.) still apply."""
        return self.status in ACTIONABLE_STATUSES

    @property
    def display_name(self) -> str:
        """The dashboard/email name for a transfer: its first file's name (spec section 2 -- there's
        no title field). A plain model property (rather than only a service function) so templates
        can use it directly.

        Deliberately `self.files.first()` rather than `self.files.order_by(...).first()`:
        `TransferFile.Meta.ordering` already sorts by `('created_at', 'id')`, and an explicit
        `.order_by()` call would clone the queryset, bypassing any `prefetch_related('files')`
        cache the caller set up (e.g. the dashboard list, `apps.file_transfer.views.dashboard`)
        and re-querying per transfer.
        """
        first = self.files.first()
        return first.name if first else 'Untitled transfer'

    @property
    def download_count(self) -> int:
        """Total files downloaded so far (spec section 4: each file download counts one).

        Uses the `download_events_count` annotation when the caller provided one (the dashboard
        list does, to avoid a `COUNT` query per row); falls back to a direct count otherwise.
        """
        annotated = getattr(self, 'download_events_count', None)
        if annotated is not None:
            return annotated
        return self.download_events.count()

    @property
    def downloads_remaining(self) -> int | None:
        """`None` means uncapped."""
        if self.max_downloads is None:
            return None
        return max(self.max_downloads - self.download_count, 0)

    @property
    def absolute_download_url(self) -> str:
        """The full public download link (spec section 4), for templates and emails alike."""
        from django.urls import reverse

        return f'{settings.BASE_URL}{reverse("t:download", args=[self.slug])}'
