"""A user's tags: flat, coloured, any number per note. (To-do's labels, as Notes' own set.)"""

import re
from collections.abc import Sequence
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, QuerySet

from apps.accounts.models import User

from ..limits import TAG_DESCRIPTION_MAX, TAG_NAME_MAX
from ..models import DEFAULT_TAG_COLOR, NotesSettings, Tag

_COLOR = re.compile(r'^#[0-9a-f]{6}$')
DUPLICATE_MESSAGE = 'You already have a tag with this name.'
TOO_MANY_MESSAGE = 'You have too many tags.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def clean_name(name: object) -> str:
    cleaned = ' '.join(str(name or '').split())
    if not cleaned:
        raise _error('name', 'Name is required.')
    if len(cleaned) > TAG_NAME_MAX:
        raise _error('name', f'Name is too long (at most {TAG_NAME_MAX} characters).')
    return cleaned


def clean_color(color: object) -> str:
    """`#rrggbb`, lower-cased; empty means the default colour."""
    cleaned = str(color or '').strip().lower() or DEFAULT_TAG_COLOR
    if not _COLOR.match(cleaned):
        raise _error('color', 'Use a colour like #8fa89e.')
    return cleaned


def clean_description(description: object) -> str:
    cleaned = ' '.join(str(description or '').split())
    if len(cleaned) > TAG_DESCRIPTION_MAX:
        raise _error(
            'description', f'Description is too long (at most {TAG_DESCRIPTION_MAX} characters).'
        )
    return cleaned


def _check_unique(user: User, name: str, *, exclude: Tag | None = None) -> None:
    taken = Tag.objects.filter(user=user, name__iexact=name)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise _error('name', DUPLICATE_MESSAGE)


def list_tags(user: User) -> QuerySet:
    """The user's tags in their order, each with `note_count` (notes not in the Trash)."""
    # Ordered explicitly: `Meta.ordering` is dropped from a query that aggregates.
    return (
        Tag.objects.filter(user=user)
        .annotate(note_count=Count('notes', filter=Q(notes__deleted_at__isnull=True)))
        .order_by('position', 'name')
    )


def create_tag(user: User, name: str, color: str = '', description: str = '') -> Tag:
    """Create a tag at the end of the user's list. Names are unique per user, ignoring case."""
    cleaned_name = clean_name(name)
    cleaned_color = clean_color(color)
    cleaned_description = clean_description(description)
    cap = NotesSettings.load().max_tags_per_user
    with transaction.atomic():
        existing = Tag.objects.filter(user=user)
        if existing.count() >= cap:
            raise _error('__all__', TOO_MANY_MESSAGE)
        _check_unique(user, cleaned_name)
        position = (existing.aggregate(top=Max('position'))['top'] or 0) + 1
        try:
            with transaction.atomic():
                return Tag.objects.create(
                    user=user,
                    name=cleaned_name,
                    color=cleaned_color,
                    description=cleaned_description,
                    position=position,
                )
        except IntegrityError:  # a concurrent create of the same name
            raise _error('name', DUPLICATE_MESSAGE) from None


def update_tag(
    tag: Tag,
    *,
    name: str | None = None,
    color: str | None = None,
    description: str | None = None,
) -> Tag:
    """Change what is given (`None` leaves a field as it is; an empty description clears it)."""
    fields = []
    if name is not None:
        tag.name = clean_name(name)
        _check_unique(tag.user, tag.name, exclude=tag)
        fields.append('name')
    if color is not None:
        tag.color = clean_color(color)
        fields.append('color')
    if description is not None:
        tag.description = clean_description(description)
        fields.append('description')
    if fields:
        try:
            with transaction.atomic():
                tag.save(update_fields=fields)
        except IntegrityError:
            raise _error('name', DUPLICATE_MESSAGE) from None
    return tag


def delete_tag(tag: Tag) -> None:
    """Delete the tag; the notes that had it keep their other tags."""
    tag.delete()


def reorder_tags(user: User, ids: Sequence[UUID]) -> int:
    """Write `position = index` for the given ids (the user's own; others are ignored)."""
    own = {tag.pk: tag for tag in Tag.objects.filter(user=user, pk__in=ids)}
    changed = []
    for index, pk in enumerate(ids):
        tag = own.get(pk)
        if tag is not None:
            tag.position = index
            changed.append(tag)
    with transaction.atomic():
        Tag.objects.bulk_update(changed, ['position'])
    return len(changed)
