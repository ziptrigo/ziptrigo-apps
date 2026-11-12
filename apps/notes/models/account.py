from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User


class NotesAccount(models.Model):
    """A user's Notes account, created lazily on first use through
    `apps.notes.services.accounts.get_account`.

    Its row is **the lock** every write that counts notes or free slots takes first
    (`services.accounts.lock_account`), so two creations can't both take the last free slot and one
    user's writes take turns. `renewal_warned_at` is when the renewal warning last went out (a later
    sub-issue).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    user = cast(
        'User',
        models.OneToOneField(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            primary_key=True,
            related_name='notes_account',
        ),
    )
    renewal_warned_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    def __str__(self) -> str:
        return f'Notes account of {self.user}'
