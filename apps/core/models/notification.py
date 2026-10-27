import uuid
from datetime import datetime
from typing import ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

#: Caps on what a caller may store. `apps.core.services.notifications.notify` truncates to these
#: (a long file name in a title must not make the event it reports fail); the columns enforce them
#: for anything that writes a row another way.
SOURCE_MAX_LENGTH = 50
TITLE_MAX_LENGTH = 140
BODY_MAX_LENGTH = 500
URL_MAX_LENGTH = 500
KEY_MAX_LENGTH = 200


class Notification(models.Model):
    """One in-app notification for one user (issue #169), created by any app through
    `apps.core.services.notifications.notify` and shown by the bell in the site header and on the
    notifications page.

    Deliberately generic, because `core` cannot import a product: `source` is the creating app's
    label (`'file_transfer'`, `'receipts'`, ...), `title` and `body` are plain text (every template
    escapes them) and `url` is a relative path the product built with `reverse(...)`, never
    something `core` could resolve itself.

    `key` is optional and identifies *what the notification is about* (`'transfer_suspended:<id>'`,
    `'receipts_account:frozen'`), chosen by the creating app, so that app can delete the notice
    when the thing it describes is fixed (`delete_for_key`). `url` can't do that job: several
    notices of one user share the same dashboard link.

    Three timestamps, all null until they happen:

    - `read_at`: the user has seen it. This is what the bell's count and the "new" look are about.
      Opening the dropdown sets it for what the dropdown lists; so does clicking through.
    - `opened_at`: the user followed it to its `url` (`/notifications/<id>/open/`). The dropdown
      lists what hasn't been opened or dismissed, so a notification stays in it, read, until the
      user clicks it or dismisses it.
    - `dismissed_at`: the user removed it. A dismissed notification is hidden everywhere (the
      row stays until the retention job deletes it).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The user is referenced by setting, not imported: `core` sits below `accounts`.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications'
    )
    user_id: UUID
    source = cast(str, models.CharField(max_length=SOURCE_MAX_LENGTH))
    title = cast(str, models.CharField(max_length=TITLE_MAX_LENGTH))
    body = cast(str, models.CharField(max_length=BODY_MAX_LENGTH, blank=True))
    url = cast(str, models.CharField(max_length=URL_MAX_LENGTH, blank=True))
    key = cast(str, models.CharField(max_length=KEY_MAX_LENGTH, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    read_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    opened_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    dismissed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        ordering = ['-created_at']
        indexes = [
            # The bell's count (unread, per user) and the user's lists (newest first).
            models.Index(fields=['user', 'read_at']),
            models.Index(fields=['user', 'created_at']),
            # `delete_for_key`: a product clearing the notice about one object of one user.
            models.Index(fields=['user', 'key']),
            # The retention job deletes by age across every user.
            models.Index(fields=['created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.source}: {self.title}'
