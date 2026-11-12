"""The querysets behind the Notes lists: which notes a view shows and in what order."""

from django.db.models import QuerySet

from apps.accounts.models import User

from ..models import Note

SORTS = {
    'manual': 'Manual',
    'updated': 'Updated',
    'created': 'Created',
    'title': 'Title',
}
#: The direction each sort runs in when none is asked for (`?dir=asc|desc`): manual order and the
#: title ascending, updated and created newest first.
DEFAULT_DIRECTIONS = {'manual': 'asc', 'updated': 'desc', 'created': 'desc', 'title': 'asc'}
DIRECTIONS = ('asc', 'desc')
#: The sort each view uses when none is asked for. Archived has no manual order.
VIEW_DEFAULT_SORT = {'notes': 'manual', 'archived': 'updated'}


def active_notes(user: User) -> QuerySet:
    """The user's notes outside the Trash and the archive."""
    return Note.objects.filter(user=user, deleted_at__isnull=True, archived_at__isnull=True)


def archived_notes(user: User) -> QuerySet:
    return Note.objects.filter(user=user, deleted_at__isnull=True, archived_at__isnull=False)


def trashed_notes(user: User) -> QuerySet:
    return Note.objects.filter(user=user, deleted_at__isnull=False)


def sort_for(view: str, requested: str | None) -> str:
    """The sort to use: `requested` when the view offers it, else the view's default."""
    default = VIEW_DEFAULT_SORT.get(view, 'updated')
    if requested in SORTS and not (requested == 'manual' and view != 'notes'):
        return requested
    return default


def sort_direction(sort: str, requested: str | None = None) -> str:
    """The direction a list sorted by `sort` runs in: `requested` when it is one of `DIRECTIONS`,
    else the sort's default."""
    return requested if requested in DIRECTIONS else DEFAULT_DIRECTIONS.get(sort, 'asc')


def order_notes(queryset: QuerySet, sort: str, direction: str | None = None) -> QuerySet:
    """`queryset` in `sort` order: `manual` (by position, then newest, then id), `updated`, `created` or
    `title`. The opposite of a sort's default direction is its exact reverse, ties included."""
    if sort == 'manual':
        ordered = queryset.order_by('position', '-created_at', 'pk')
    elif sort == 'created':
        ordered = queryset.order_by('-created_at', 'pk')
    elif sort == 'title':
        ordered = queryset.order_by('title_text', 'created_at', 'pk')
    else:
        ordered = queryset.order_by('-updated_at', '-created_at', 'pk')
        sort = 'updated'
    if sort_direction(sort, direction) != DEFAULT_DIRECTIONS[sort]:
        return ordered.reverse()
    return ordered
