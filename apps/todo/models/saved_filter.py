import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models.functions import Lower

from apps.accounts.models import User

from ..limits import SAVED_FILTER_NAME_MAX


class SavedFilter(models.Model):
    """A named combination of search text and structured filters (issue #181).

    `params` is `services.filters.FilterParams.to_dict()`: the search text and mode, whether
    completed tasks are included, priorities, labels, a date range and a sort. It is stored in its
    canonical form and re-read leniently (`FilterParams.from_dict`), so a label deleted since is
    simply dropped. The sidebar lists the user's filters in `position` order.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    user_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_saved_filters'
        ),
    )
    name = cast(str, models.CharField(max_length=SAVED_FILTER_NAME_MAX))
    params = cast(dict, models.JSONField(default=dict))
    position = cast(int, models.PositiveIntegerField(default=0))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'user', name='todo_saved_filter_name_user'),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        from ..services.filters import FilterParams  # the services import the models

        # Parsing it back must give the same thing: a stored dict is always a canonical one.
        if not isinstance(self.params, dict):
            raise ValidationError({'params': 'Params must be an object.'})
        if FilterParams.from_dict(self.params).to_dict() != self.params:
            raise ValidationError({'params': 'These are not valid filter parameters.'})

    def save(self, *args, **kwargs) -> None:
        """Store `params` in its canonical form (what parsing it back gives)."""
        from ..services.filters import FilterParams

        self.params = FilterParams.from_dict(self.params).to_dict()
        super().save(*args, **kwargs)
