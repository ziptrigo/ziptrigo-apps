"""Dates, the sets of tasks each page shows, and the sort orders (issue #178).

A task's due date and time are the user's local calendar date and wall-clock time, not an instant.
"Today" is always `user_today(user)`. Every list sorts the same way by default: due date ascending
with undated tasks last, then date-only tasks before timed ones, then the time, then priority (1
first, none last), then oldest first.
"""

from datetime import date, datetime, timedelta

from django.db.models import Case, Count, F, IntegerField, Q, QuerySet, Value, When
from django.db.models.expressions import OrderBy

from apps.accounts.models import User
from apps.accounts.services.timezones import local_datetime, user_today

from ..models import Task, TodoAccount

#: The sorts a list may ask for with `?sort=`. `manual` exists only for Today and a parent's
#: subtasks (see `order_tasks`).
SORT_DEFAULT = 'date'
SORTS = {
    'date': 'Due date',
    'priority': 'Priority',
    'created': 'Newest',
    'title': 'Title',
}
UPCOMING_DAYS = 14


def due_at(task: Task, account: TodoAccount) -> datetime | None:
    """The UTC instant `task` is due: its date at its time, or at the account's default due time
    when it has none, on the owner's wall clock. `None` for a task without a due date."""
    if task.due_date is None:
        return None
    return local_datetime(task.user, task.due_date, task.due_time or account.default_due_time)


def _priority_rank() -> Case:
    """Priority 1 first, 2, 3, and "none" (0) last."""
    return Case(
        When(priority=0, then=Value(4)),
        default=F('priority'),
        output_field=IntegerField(),
    )


def _date_order() -> list[OrderBy]:
    return [
        F('due_date').asc(nulls_last=True),
        F('due_time').asc(nulls_first=True),
    ]


def default_order() -> list:
    return [*_date_order(), _priority_rank().asc(), 'created_at', 'number']


def order_tasks(queryset: QuerySet, sort: str = SORT_DEFAULT) -> QuerySet:
    """`queryset` in `sort` order: `date` (the default, also for an unknown value), `priority`
    (then date), `created` (newest first), `title`, or `manual` (`Task.position`, then the default
    order for the tasks that tie)."""
    if sort == 'priority':
        return queryset.order_by(_priority_rank().asc(), *_date_order(), 'created_at', 'number')
    if sort == 'created':
        return queryset.order_by('-created_at', '-number')
    if sort == 'title':
        return queryset.order_by('title_text', 'created_at', 'number')
    if sort == 'manual':
        return queryset.order_by('position', *default_order())
    return queryset.order_by(*default_order())


def has_manual_order(queryset: QuerySet) -> bool:
    """Whether anything in `queryset` was ever positioned by hand. A reorder writes 0, 1, 2, ...,
    so any position above 0 means one happened; a task created while one exists goes after the
    others (`tasks.next_position`), and one created before any reorder keeps 0."""
    return queryset.filter(position__gt=0).exists()


def with_subtask_counts(queryset: QuerySet) -> QuerySet:
    """Annotate `subtask_total`, `subtask_done` and `subtask_open` (not trashed subtasks), for the
    `2/5` badge and the "complete its subtasks too?" question."""
    live = Q(subtasks__deleted_at__isnull=True)
    return queryset.annotate(
        subtask_total=Count('subtasks', filter=live),
        subtask_done=Count('subtasks', filter=live & Q(subtasks__completed_at__isnull=False)),
        subtask_open=Count('subtasks', filter=live & Q(subtasks__completed_at__isnull=True)),
    )


def open_tasks(user: User) -> QuerySet:
    """The user's open, non-trashed tasks (subtasks included)."""
    return Task.objects.filter(user=user, completed_at__isnull=True, deleted_at__isnull=True)


def today_set(user: User, account: TodoAccount | None = None) -> QuerySet:
    """Open, non-trashed tasks due today or earlier (the overdue ones included), unordered."""
    return open_tasks(user).filter(due_date__lte=user_today(user))


def overdue_set(user: User) -> QuerySet:
    return open_tasks(user).filter(due_date__lt=user_today(user))


def upcoming_groups(
    user: User, account: TodoAccount | None = None, days: int = UPCOMING_DAYS
) -> list[tuple[date, list[Task]]]:
    """Open tasks due after today and up to `days` days from now, grouped by day, days in order,
    each day's tasks in the default order. Days with nothing are left out."""
    today = user_today(user)
    queryset = open_tasks(user).filter(due_date__gt=today, due_date__lte=today + timedelta(days))
    queryset = with_subtask_counts(queryset).prefetch_related('labels')
    groups: list[tuple[date, list[Task]]] = []
    for task in order_tasks(queryset):
        assert task.due_date is not None  # filtered above
        if groups and groups[-1][0] == task.due_date:
            groups[-1][1].append(task)
        else:
            groups.append((task.due_date, [task]))
    return groups
