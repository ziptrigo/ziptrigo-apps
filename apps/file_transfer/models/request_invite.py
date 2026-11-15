import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.functions import Lower

from .transfer import Transfer

if TYPE_CHECKING:
    from django.db.models import Manager


class RequestInvite(models.Model):
    """One person a file request is for (issue #306): the audience "Only these people". Their
    account email must match `email` (ignoring case) and be confirmed to use the link. Separate
    from `TransferRecipient` so a send's recipients and a request's invitees never mix.

    Deduplicated per request ignoring case (`unique_request_invite`); at most
    `FileTransferSettings.logged_in_max_recipients` per request (checked in the services).
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = cast(
        Transfer, models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='invites')
    )
    transfer_id: UUID

    email = cast(str, models.EmailField())
    last_sent_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['email']
        constraints = [
            models.UniqueConstraint(Lower('email'), 'transfer', name='unique_request_invite_email')
        ]

    def __str__(self) -> str:
        return self.email
