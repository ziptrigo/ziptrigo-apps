"""A user's labels: flat, coloured, any number per task."""

import re
from collections.abc import Sequence
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, QuerySet

from apps.accounts.models import User

from ..models import DEFAULT_LABEL_COLOR, Label, TodoSettings
from .limits import LABEL_DESCRIPTION_MAX, LABEL_NAME_MAX

_COLOR = re.compile(r'^#[0-9a-f]{6}$')
DUPLICATE_MESSAGE = 'You already have a label with this name.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def clean_name(name: object) -> str:
    cleaned = ' '.join(str(name or '').split())
    if not cleaned:
        raise _error('name', 'Name is required.')
    if len(cleaned) > LABEL_NAME_MAX:
        raise _error('name', f'Name is too long (at most {LABEL_NAME_MAX} characters).')
    return cleaned


def clean_color(color: object) -> str:
    """`#rrggbb`, lower-cased; empty means the default colour."""
    cleaned = str(color or '').strip().lower() or DEFAULT_LABEL_COLOR
    if not _COLOR.match(cleaned):
        raise _error('color', 'Use a colour like #8fa89e.')
    return cleaned


def clean_description(description: object) -> str:
    cleaned = ' '.join(str(description or '').split())
    if len(cleaned) > LABEL_DESCRIPTION_MAX:
        raise _error(
            'description', f'Description is too long (at most {LABEL_DESCRIPTION_MAX} characters).'
        )
    return cleaned


def _check_unique(user: User, name: str, *, exclude: Label | None = None) -> None:
    taken = Label.objects.filter(user=user, name__iexact=name)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise _error('name', DUPLICATE_MESSAGE)


def list_labels(user: User) -> QuerySet:
    """The user's labels in their order, each with `task_count` (tasks not in the Trash)."""
    # Ordered explicitly: `Meta.ordering` is dropped from a query that aggregates.
    return (
        Label.objects.filter(user=user)
        .annotate(task_count=Count('tasks', filter=Q(tasks__deleted_at__isnull=True)))
        .order_by('position', 'name')
    )


def create_label(user: User, name: str, color: str = '', description: str = '') -> Label:
    """Create a label at the end of the user's list. Names are unique per user, ignoring case."""
    cleaned_name = clean_name(name)
    cleaned_color = clean_color(color)
    cleaned_description = clean_description(description)
    cap = TodoSettings.load().max_labels_per_user
    with transaction.atomic():
        existing = Label.objects.filter(user=user)
        if existing.count() >= cap:
            raise _error('__all__', f'You can have up to {cap} labels.')
        _check_unique(user, cleaned_name)
        position = (existing.aggregate(top=Max('position'))['top'] or 0) + 1
        try:
            with transaction.atomic():
                return Label.objects.create(
                    user=user,
                    name=cleaned_name,
                    color=cleaned_color,
                    description=cleaned_description,
                    position=position,
                )
        except IntegrityError:  # a concurrent create of the same name
            raise _error('name', DUPLICATE_MESSAGE) from None


def update_label(
    label: Label,
    *,
    name: str | None = None,
    color: str | None = None,
    description: str | None = None,
) -> Label:
    """Change what is given (`None` leaves a field as it is; an empty description clears it)."""
    fields = []
    if name is not None:
        label.name = clean_name(name)
        _check_unique(label.user, label.name, exclude=label)
        fields.append('name')
    if color is not None:
        label.color = clean_color(color)
        fields.append('color')
    if description is not None:
        label.description = clean_description(description)
        fields.append('description')
    if fields:
        try:
            with transaction.atomic():
                label.save(update_fields=fields)
        except IntegrityError:
            raise _error('name', DUPLICATE_MESSAGE) from None
    return label


def delete_label(label: Label) -> None:
    """Delete the label; the tasks that had it keep their other labels."""
    label.delete()


def reorder_labels(user: User, ids: Sequence[UUID]) -> int:
    """Write `position = index` for the given ids (the user's own; others are ignored)."""
    own = {label.pk: label for label in Label.objects.filter(user=user, pk__in=ids)}
    changed = []
    for index, pk in enumerate(ids):
        label = own.get(pk)
        if label is not None:
            label.position = index
            changed.append(label)
    with transaction.atomic():
        Label.objects.bulk_update(changed, ['position'])
    return len(changed)
