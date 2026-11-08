import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class RefreshToken(models.Model):
    """One refresh token the API has issued (issue #252), keyed by its `jti`: the bookkeeping
    behind rotation (`services.refresh_tokens`), not the token itself, which is a signed JWT that
    is never stored.

    Every `POST /api/auth/refresh` replaces the token it was given with a new one of the same
    `family` (all the refresh tokens descended from one login). A token that has been replaced has
    `rotated_at`; using it again within the grace window is allowed (two requests from one app),
    using it after the window is a replay -- the whole family is revoked (`revoked_at`). Rows are
    kept until the token would have expired anyway (`expires_at`; the daily
    `accounts.purge_refresh_tokens` job), since a revoked family must go on being refused.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = cast(int, models.BigAutoField(primary_key=True))
    jti = cast(str, models.CharField(max_length=64, unique=True))
    user = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='refresh_tokens'
    )
    family = cast(uuid.UUID, models.UUIDField(db_index=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    expires_at = cast(datetime, models.DateTimeField(db_index=True))
    rotated_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    revoked_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    # Successors issued from this token (capped by `refresh_tokens.MAX_SUCCESSORS`).
    successor_count = cast(int, models.PositiveSmallIntegerField(default=0, db_default=0))

    class Meta:
        verbose_name = 'Refresh token'
        verbose_name_plural = 'Refresh tokens'

    def __str__(self) -> str:
        return f'{self.jti[:8]} ({str(self.family)[:8]})'
