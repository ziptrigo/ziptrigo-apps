from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User


class WorldTimeAccount(models.Model):
    """A user's World Time account, created lazily on first use through
    `apps.world_time.services.accounts.get_account`.

    Its row is **the lock** every write that counts tabs or locations takes first
    (`services.accounts.lock_account`), so two requests can't both pass a cap and one user's writes
    take turns.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    user = cast(
        'User',
        models.OneToOneField(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            primary_key=True,
            related_name='world_time_account',
        ),
    )
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    def __str__(self) -> str:
        return f'World Time account of {self.user}'
