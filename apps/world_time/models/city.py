from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class City(models.Model):
    """A city a user can add as a location: reference data, seeded from the GeoNames extract in
    `apps/world_time/data/cities.tsv.gz` by `./manage.py world_time_seed_cities` (never edited by
    hand, never referenced by a foreign key: a `Location` keeps its own copy of what it shows).

    `search_name` is the name as the search compares it (`services.cities.normalize`: no accents,
    lower case, punctuation as spaces).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: int

    geoname_id = cast(int, models.PositiveIntegerField(primary_key=True))
    name = cast(str, models.CharField(max_length=200))
    search_name = cast(str, models.CharField(max_length=200, db_index=True))
    country_code = cast(str, models.CharField(max_length=2))
    country = cast(str, models.CharField(max_length=100))
    region = cast(str, models.CharField(max_length=100, blank=True))
    timezone = cast(str, models.CharField(max_length=64))
    population = cast(int, models.PositiveIntegerField(default=0))

    class Meta:
        verbose_name_plural = 'cities'
        indexes = [models.Index(fields=['-population'], name='wt_city_population')]

    def __str__(self) -> str:
        return self.display_name

    @property
    def detail(self) -> str:
        """`Oregon, United States`: what tells this city from another of the same name."""
        return ', '.join(part for part in (self.region, self.country) if part)

    @property
    def display_name(self) -> str:
        return ', '.join(part for part in (self.name, self.detail) if part)


class CityName(models.Model):
    """Another name a city is known by (`Lisboa`, `München`), for the search only."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    city_id: int

    id = models.BigAutoField(primary_key=True)
    city = cast(
        'City', models.ForeignKey(City, on_delete=models.CASCADE, related_name='other_names')
    )
    name = cast(str, models.CharField(max_length=200))
    search_name = cast(str, models.CharField(max_length=200, db_index=True))

    def __str__(self) -> str:
        return self.name


class CityData(models.Model):
    """Which city file the tables were last seeded from: one row (`pk=1`) holding the file's
    SHA-256, so the seed command run at every deploy does nothing when the file hasn't changed."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    sha256 = cast(str, models.CharField(max_length=64))
    city_count = cast(int, models.PositiveIntegerField(default=0))
    name_count = cast(int, models.PositiveIntegerField(default=0))
    seeded_at = cast(datetime, models.DateTimeField())

    class Meta:
        verbose_name = 'city data'
        verbose_name_plural = 'city data'

    def __str__(self) -> str:
        return f'{self.city_count} cities'
