import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

from ..limits import EVENT_TITLE_MAX


class Event(models.Model):
    """A moment shared with a link: a title, a start and an end, and a **snapshot** of the
    locations of the tab it was made from (issue #269, PR 2).

    Anyone who has `/w/<code>` sees it, with no login, until the owner deletes it or it is purged
    `WorldTimeSettings.event_purge_days` after it ends. `code` is the capability (22 random
    URL-safe characters, never reused or edited); the title is plain text. `locations` is a list of
    `{"name", "detail", "timezone", "home"}` copied when the event is made: later changes to the
    tab (or deleting it) don't change what the link shows.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    user_id: uuid.UUID
    #: Set by `services.events.list_events`: the event is over.
    ended: bool = False

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='world_time_events'
        ),
    )
    code = cast(str, models.CharField(max_length=32, unique=True, editable=False))
    title = cast(str, models.CharField(max_length=EVENT_TITLE_MAX))
    starts_at = cast(datetime, models.DateTimeField())
    ends_at = cast(datetime, models.DateTimeField())
    locations = cast(list, models.JSONField(default=list, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime, models.DateTimeField(auto_now=True))

    class Meta:
        ordering = ['starts_at', 'created_at']
        indexes = [
            models.Index(fields=['ends_at'], name='wt_event_ends_at'),
            models.Index(fields=['user', 'starts_at'], name='wt_event_user_starts'),
        ]

    def __str__(self) -> str:
        return self.title
