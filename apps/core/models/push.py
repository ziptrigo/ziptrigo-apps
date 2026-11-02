import uuid
from datetime import datetime
from typing import ClassVar, cast
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

#: Caps on what a browser may send. Real endpoints are a few hundred characters (FCM, Mozilla,
#: Apple, Windows), the keys are base64url of 65 and 16 bytes.
ENDPOINT_MAX_LENGTH = 1000
P256DH_MAX_LENGTH = 200
AUTH_MAX_LENGTH = 100
USER_AGENT_MAX_LENGTH = 500
KIND_MAX_LENGTH = 100
ACTION_MAX_LENGTH = 50


class PushSubscription(models.Model):
    """One browser's Web Push subscription for one user (issue #239).

    A browser has one subscription per site, identified by its `endpoint` (the push service URL
    the server POSTs to), so `endpoint` is unique: the same browser subscribing for another user
    (a shared device, after a sign-out) moves the row to that user rather than adding a second.
    `p256dh` and `auth` are the browser's keys that encrypt the payload, base64url as the browser
    gave them. Rows are written by `apps.core.services.push` only, and deleted when the push
    service says the subscription is gone (404/410), when the user turns push off, and when the
    device signs out.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The user is referenced by setting, not imported: `core` sits below `accounts`.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_subscriptions'
    )
    user_id: UUID
    endpoint = cast(str, models.CharField(max_length=ENDPOINT_MAX_LENGTH, unique=True))
    p256dh = cast(str, models.CharField(max_length=P256DH_MAX_LENGTH))
    auth = cast(str, models.CharField(max_length=AUTH_MAX_LENGTH))
    user_agent = cast(str, models.CharField(max_length=USER_AGENT_MAX_LENGTH, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    last_success_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        verbose_name = 'Push subscription'
        verbose_name_plural = 'Push subscriptions'
        ordering = ['-created_at']

    def __str__(self) -> str:
        # The endpoint is a capability URL (with the keys, anyone can push to the browser): its
        # push service's host only.
        return f'Push subscription on {urlsplit(self.endpoint).hostname or "?"}'


class PushAction(models.Model):
    """The answer a user gave by tapping an action button on a push notification (issue #239).

    `push_id` is the id `send_push` gave that notification (shared by every device it went to),
    so it is unique: the first answer is kept, a repeat of the same request changes nothing.
    `kind` is what the caller of `send_push` said the question is about (`'core.test'`), and
    `action` the button's id (`'yes'`). A product reacts to an answer through the
    `apps.core.signals.push_action` signal; `core` itself only records it.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_actions'
    )
    user_id: UUID
    push_id = cast(UUID, models.UUIDField(unique=True))
    kind = cast(str, models.CharField(max_length=KIND_MAX_LENGTH, blank=True))
    action = cast(str, models.CharField(max_length=ACTION_MAX_LENGTH))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        verbose_name = 'Push answer'
        verbose_name_plural = 'Push answers'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'created_at'])]

    def __str__(self) -> str:
        return f'{self.kind or "push"}: {self.action}'
