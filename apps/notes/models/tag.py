import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower

from apps.accounts.models import User

from ..limits import TAG_DESCRIPTION_MAX, TAG_NAME_MAX

DEFAULT_TAG_COLOR = '#8fa89e'


class Tag(models.Model):
    """A coloured, per-user tag, Notes' own set (independent of To-do's labels). Flat: any number
    per note. The description is shown as the chip's tooltip."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `Note.tags`.
    notes: ClassVar['models.Manager']

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    user_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notes_tags'
        ),
    )
    name = cast(str, models.CharField(max_length=TAG_NAME_MAX))
    color = cast(
        str,
        models.CharField(
            max_length=7,
            default=DEFAULT_TAG_COLOR,
            validators=[RegexValidator(r'^#[0-9a-f]{6}$', 'Use a colour like #8fa89e.')],
        ),
    )
    description = cast(str, models.CharField(max_length=TAG_DESCRIPTION_MAX, blank=True))
    position = cast(int, models.PositiveIntegerField(default=0))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['position', 'name']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'user', name='notes_tag_name_user'),
        ]

    def __str__(self) -> str:
        return self.name
