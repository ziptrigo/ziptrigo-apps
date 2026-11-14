import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.accounts.models import User

from ..limits import LOCATION_LABEL_MAX, TAB_NAME_MAX


class Tab(models.Model):
    """A named set of locations (`Team`, `Family`). Per user; names are unique per user ignoring
    case."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `Location.tab`.
    locations: ClassVar['models.Manager']

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    user_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='world_time_tabs'
        ),
    )
    name = cast(str, models.CharField(max_length=TAB_NAME_MAX))
    position = cast(int, models.PositiveIntegerField(default=0))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'user', name='wt_tab_name_user'),
        ]

    def __str__(self) -> str:
        return self.name


class Location(models.Model):
    """One row of a tab's grid: a city (or a bare time zone) and how the user labelled it.

    It keeps **its own copy** of what it shows (`name`, `region`, `country`, `timezone`) and only
    remembers the city's GeoNames id, with no foreign key: re-seeding the city tables can never
    change or delete a user's rows. `geoname_id` is null for a location added as a time zone
    (`UTC`, `Europe/Lisbon`). Exactly one location of a tab that has any is its home (`is_home`):
    the row the others are offset from, whose day the grid shows midnight to midnight.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID
    tab_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tab = cast('Tab', models.ForeignKey(Tab, on_delete=models.CASCADE, related_name='locations'))
    geoname_id = cast(int | None, models.PositiveIntegerField(null=True, blank=True))
    name = cast(str, models.CharField(max_length=200))
    region = cast(str, models.CharField(max_length=100, blank=True))
    country = cast(str, models.CharField(max_length=100, blank=True))
    country_code = cast(str, models.CharField(max_length=2, blank=True))
    timezone = cast(str, models.CharField(max_length=64))
    label = cast(str, models.CharField(max_length=LOCATION_LABEL_MAX, blank=True))
    is_home = cast(bool, models.BooleanField(default=False))
    position = cast(int, models.PositiveIntegerField(default=0))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tab'], condition=Q(is_home=True), name='wt_location_one_home'
            ),
            models.UniqueConstraint(
                fields=['tab', 'geoname_id'],
                condition=Q(geoname_id__isnull=False),
                name='wt_location_city_once',
            ),
            models.UniqueConstraint(
                fields=['tab', 'timezone'],
                condition=Q(geoname_id__isnull=True),
                name='wt_location_zone_once',
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def title(self) -> str:
        """What the row is called: the user's label, else the place's name."""
        return self.label or self.name

    @property
    def detail(self) -> str:
        """`Oregon, United States` for a city; the zone's name for a bare time zone."""
        if self.geoname_id is None:
            return self.timezone.replace('_', ' ') if self.timezone != self.name else ''
        return ', '.join(part for part in (self.region, self.country) if part)
