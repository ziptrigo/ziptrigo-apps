import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone

from ..limits import LINK_LABEL_MAX
from .note import Note

if TYPE_CHECKING:
    from apps.accounts.models import User

#: Free-text "details" field cap on a report.
MAX_DETAILS_LENGTH = 2000


class LinkMode(models.TextChoices):
    """What a share link lets a visitor do: only read the note, or also change its content."""

    READ = 'read', 'Read-only'
    EDIT = 'edit', 'Editable'


class ShareLink(models.Model):
    """A capability link to a note: `<BASE_URL>/n/<code>` shows it to anyone who has the address.

    The code (`secrets.token_urlsafe(16)`, 22 characters) is the whole secret: it is never reused,
    never editable and, in the admin, shown only by its first 6 characters. A link is *active*
    while it is not revoked and has not expired; whether it *works* also depends on the note
    (`services.links.is_available`: not in the Trash, not locked, not taken down). A revoked link is
    never re-activated. `label` is the owner's own reminder of who it is for; it is never shown
    publicly.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `NoteVersion.link`.
    versions: ClassVar['models.Manager']
    #: The reverse accessor of `NoteReport.link`.
    reports: ClassVar['models.Manager']

    pk: uuid.UUID
    note_id: uuid.UUID
    # Set by `services.links.list_links` for the owner's panel (not stored).
    url: str
    state: str
    expiry_value: str
    expires_field_id: str
    qr: str

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note = cast(
        'Note',
        models.ForeignKey(Note, on_delete=models.CASCADE, related_name='links'),
    )
    code = cast(str, models.CharField(max_length=64, unique=True, editable=False))
    mode = cast(str, models.CharField(max_length=8, choices=LinkMode, default=LinkMode.READ))
    label = cast(str, models.CharField(max_length=LINK_LABEL_MAX, blank=True))
    expires_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    revoked_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    last_edited_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        indexes = [models.Index(fields=['note', 'revoked_at'], name='notes_link_note_idx')]

    def __str__(self) -> str:
        return f'{self.code[:6]}... ({self.mode})'

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or timezone.now())

    def is_active(self, now: datetime | None = None) -> bool:
        """Not revoked and not expired (the note's own state is not looked at)."""
        return not self.is_revoked and not self.is_expired(now)

    @property
    def is_editable(self) -> bool:
        return self.mode == LinkMode.EDIT


class ReportReason(models.TextChoices):
    """Reason categories of the public "report this note" form (Short URLs' set)."""

    PHISHING = 'phishing', 'Phishing or scam'
    MALWARE = 'malware', 'Malware'
    SPAM = 'spam', 'Spam'
    ILLEGAL_CONTENT = 'illegal_content', 'Illegal content'
    COPYRIGHT = 'copyright', 'Copyright infringement'
    OTHER = 'other', 'Other'


class ReportStatus(models.TextChoices):
    """A report's review state: `PENDING` until staff act on it, then `ACTIONED` (its note was
    taken down) or `DISMISSED` (staff decided no action was needed)."""

    PENDING = 'pending', 'Pending'
    ACTIONED = 'actioned', 'Actioned'
    DISMISSED = 'dismissed', 'Dismissed'


class NoteReport(models.Model):
    """One "report this note" submission from a shared page. No login is needed to send one.
    `reporter_email` is optional; `reporter_ip` is the best-effort client address
    (`apps.core.services.client_ip`), kept for support and abuse purposes, as Short URLs keep their
    reporters'. The owner never sees who reported a note."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    note_id: uuid.UUID
    link_id: uuid.UUID | None
    reviewed_by_id: uuid.UUID | None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note = cast(
        'Note',
        models.ForeignKey(Note, on_delete=models.CASCADE, related_name='reports'),
    )
    link = cast(
        'ShareLink | None',
        models.ForeignKey(
            ShareLink,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='reports',
            help_text='The link the report was sent from.',
        ),
    )
    reason = cast(str, models.CharField(max_length=32, choices=ReportReason))
    details = cast(str, models.TextField(blank=True, default='', max_length=MAX_DETAILS_LENGTH))
    reporter_email = cast(str, models.EmailField(blank=True, default=''))
    reporter_ip = cast('str | None', models.GenericIPAddressField(null=True, blank=True))
    status = cast(
        str,
        models.CharField(max_length=16, choices=ReportStatus, default=ReportStatus.PENDING),
    )
    reviewed_by = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='+',
            help_text='The staff user who dismissed this report or took its note down.',
        ),
    )
    reviewed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    resolution_note = cast(str, models.TextField(blank=True, default=''))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['note', 'status'], name='notes_report_note_idx'),
            models.Index(fields=['status', 'created_at'], name='notes_report_status_idx'),
        ]

    def __str__(self) -> str:
        return f'Report on {self.note_id} ({self.status})'
