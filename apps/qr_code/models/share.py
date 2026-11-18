import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone

from ..limits import LINK_LABEL_MAX
from .qrcode import QRCode

if TYPE_CHECKING:
    from django.db.models import Manager


class QRShareLink(models.Model):
    """A capability link to a QR code: `<BASE_URL>/q/<code>` shows it to anyone who has the address.

    The code (`secrets.token_urlsafe(16)`, 22 characters) is the whole secret: it is never reused,
    never editable and, in the admin, shown only by its first 6 characters. A link is *active*
    while it is not revoked and has not expired; whether it *works* also depends on the QR code
    (`services.share.is_available`: not deleted). A revoked link is never re-activated. The link
    shows the QR code as it is now, not a snapshot. `label` is the owner's own reminder of who it
    is for; it is never shown publicly.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID
    qrcode_id: uuid.UUID
    # Set by `services.share.list_links` for the owner's panel (not stored).
    url: str
    state: str
    expiry_value: str
    expires_field_id: str

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = cast(
        'QRCode',
        models.ForeignKey(QRCode, on_delete=models.CASCADE, related_name='share_links'),
    )
    code = cast(str, models.CharField(max_length=64, unique=True, editable=False))
    label = cast(str, models.CharField(max_length=LINK_LABEL_MAX, blank=True))
    expires_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    revoked_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        indexes = [models.Index(fields=['qrcode', 'revoked_at'], name='qr_share_link_qr_idx')]
        verbose_name = 'QR share link'
        verbose_name_plural = 'QR share links'

    def __str__(self) -> str:
        return f'{self.code[:6]}...'

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or timezone.now())

    def is_active(self, now: datetime | None = None) -> bool:
        """Not revoked and not expired (the QR code's own state is not looked at)."""
        return not self.is_revoked and not self.is_expired(now)
