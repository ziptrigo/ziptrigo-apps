"""Dates, the sets of tasks each page shows, and the sort orders (issue #178).

A task's due date and time are the user's local calendar date and wall-clock time, not an instant.
"Today" is always `user_today(user)`. Every list sorts the same way by default: due date ascending
with undated tasks last, then date-only tasks before timed ones, then the time, then priority (1
first, none last), then oldest first.
"""

from datetime import date, datetime, timedelta

from django.db.models import (
    Case,
    Count,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.expressions import OrderBy
from django.db.models.functions import Coalesce

from apps.accounts.models import User
from apps.accounts.services.timezones import local_datetime, user_today

from ..models import MUTED_STATES, MutedOverdue, Resolution, Task, TaskState, TodoAccount
from .accounts import get_account

#: The sorts a list may ask for with `?sort=`. `manual` exists only for Today and a parent's
#: subtasks (see `order_tasks`).
SORT_DEFAULT = 'date'
SORTS = {
    'date': 'Due date',
    'priority': 'Priority',
    'created': 'Newest',
    'title': 'Title',
}
#: The direction each sort runs in when none is asked for (`?dir=asc|desc`): the due date and the
#: title ascending, priority 1 first (ascending), the creation time newest first (descending).
DIRECTIONS = ('asc', 'desc')
DEFAULT_DIRECTIONS = {'date': 'asc', 'priority': 'asc', 'created': 'desc', 'title': 'asc'}
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


def state_rank() -> Case:
    """The group a task sorts into within a day: In progress (0), To do (1), muted (2)."""
    return Case(
        When(state=TaskState.IN_PROGRESS, then=Value(0)),
        When(state__in=MUTED_STATES, then=Value(2)),
        default=Value(1),
        output_field=IntegerField(),
    )


def default_order() -> list:
    return [*_date_order(), _priority_rank().asc(), 'created_at', 'number']


def sort_direction(sort: str, requested: str | None = None) -> str:
    """The direction (`asc` or `desc`) a list sorted by `sort` runs in: `requested` when it is one
    of `DIRECTIONS`, else the sort's default."""
    default = DEFAULT_DIRECTIONS.get(sort, 'asc')
    return requested if requested in DIRECTIONS else default


def order_tasks(
    queryset: QuerySet, sort: str = SORT_DEFAULT, direction: str | None = None
) -> QuerySet:
    """`queryset` in `sort` order (see `sort_direction` for `direction`; the opposite of the sort's
    default is its exact reverse, ties included): `date` (the default, also for an unknown value), `priority`
    (then date), `created` (newest first), `title`, `manual` (a parent's subtasks: `Task.position`)
    or `today_manual` (the Today list: `Task.today_position`), the manual ones then in the default
    order for the tasks that tie."""
    ordered = _order_tasks(queryset, sort)
    if sort in DEFAULT_DIRECTIONS and sort_direction(sort, direction) != DEFAULT_DIRECTIONS[sort]:
        return ordered.reverse()
    return ordered


def _order_tasks(queryset: QuerySet, sort: str) -> QuerySet:
    if sort == 'priority':
        return queryset.order_by(_priority_rank().asc(), *_date_order(), 'created_at', 'number')
    if sort == 'created':
        return queryset.order_by('-created_at', '-number')
    if sort == 'title':
        return queryset.order_by('title_text', 'created_at', 'number')
    if sort == 'manual':
        return queryset.order_by('position', *default_order())
    if sort == 'today_manual':
        return queryset.order_by('today_position', *default_order())
    return queryset.order_by(*default_order())


def order_today(queryset: QuerySet, today: date, *, manual: bool) -> QuerySet:
    """The Today list in one query: the overdue tasks and the ones due `today`, the way the page
    shows them (the overdue first, by date, then today's). With `manual` (the tasks due today have
    been ordered by hand, `has_manual_order(todays_tasks(user), 'today_position')`) the ones due
    today follow `Task.today_position`; the overdue ones never do, since a position only means
    something on the day it was set. Without it, the default order."""
    if not manual:
        return order_tasks(queryset)
    in_todays_order = Case(
        When(due_date=today, then=F('today_position')),
        default=Value(0),
        output_field=IntegerField(),
    )
    return queryset.order_by(
        F('due_date').asc(nulls_last=True),
        in_todays_order.asc(),
        F('due_time').asc(nulls_first=True),
        _priority_rank().asc(),
        'created_at',
        'number',
    )


def order_grouped(queryset: QuerySet, today: date, *, manual: bool = False) -> QuerySet:
    """The order of the Today and Upcoming pages (issue #272): by due date, and within each day In
    progress first, then To do, then muted, then the usual order (time, priority, oldest). With
    `manual` (the tasks due today were ordered by hand) the ones due `today` follow
    `Task.today_position` inside their group; a position only means something among the tasks of
    one day and one group, which is why the group comes first. The API keeps `order_today`."""
    in_todays_order = (
        Case(
            When(due_date=today, then=F('today_position')),
            default=Value(0),
            output_field=IntegerField(),
        )
        if manual
        else Value(0)
    )
    return queryset.order_by(
        F('due_date').asc(nulls_last=True),
        state_rank().asc(),
        in_todays_order,
        F('due_time').asc(nulls_first=True),
        _priority_rank().asc(),
        'created_at',
        'number',
    )


def has_manual_order(queryset: QuerySet, field: str = 'position') -> bool:
    """Whether anything in `queryset` was ever positioned by hand in `field` (`position` for a
    parent's subtasks, `today_position` for the Today list). A reorder writes 0, 1, 2, ..., so any
    value above 0 means one happened. `queryset` must be exactly the set the list orders, and
    `next_position` / `next_today_position` (below) take their maximum over the same set."""
    return queryset.filter(**{f'{field}__gt': 0}).exists()


def with_subtask_counts(queryset: QuerySet) -> QuerySet:
    """Annotate `subtask_total`, `subtask_done` and `subtask_open` (not trashed subtasks), for the
    `2/5` badge and the "complete its subtasks too?" question."""
    live = Q(subtasks__deleted_at__isnull=True)
    return queryset.annotate(
        subtask_total=Count('subtasks', filter=live),
        subtask_done=Count('subtasks', filter=live & Q(subtasks__completed_at__isnull=False)),
        subtask_open=Count('subtasks', filter=live & Q(subtasks__completed_at__isnull=True)),
    )


def with_open_occurrences(queryset: QuerySet) -> QuerySet:
    """Annotate `open_occurrences`: how many open, non-trashed tasks share each task's repeat rule
    (0 for a task that does not repeat). `Task.offers_stop_repeating` reads it, so a list of rows
    does not query once per row."""
    siblings = (
        Task.objects.filter(
            recurrence_id=OuterRef('recurrence_id'),
            completed_at__isnull=True,
            deleted_at__isnull=True,
        )
        .order_by()
        .values('recurrence_id')
        .annotate(total=Count('pk'))
        .values('total')
    )
    return queryset.annotate(
        open_occurrences=Coalesce(Subquery(siblings, output_field=IntegerField()), Value(0))
    )


def open_tasks(user: User) -> QuerySet:
    """The user's open, non-trashed tasks (subtasks included)."""
    return Task.objects.filter(user=user, completed_at__isnull=True, deleted_at__isnull=True)


def today_set(user: User) -> QuerySet:
    """Open, non-trashed tasks due today or earlier (the overdue ones included), unordered: what
    the count beside Today counts."""
    return open_tasks(user).filter(due_date__lte=user_today(user))


def upcoming_set(user: User) -> QuerySet:
    """Open, non-trashed tasks due after today and within the next `UPCOMING_DAYS` days,
    unordered: what the Upcoming page lists."""
    today = user_today(user)
    return open_tasks(user).filter(
        due_date__gt=today, due_date__lte=today + timedelta(days=UPCOMING_DAYS)
    )


def closed_set(user: User, resolution: str = '') -> QuerySet:
    """Closed tasks (either resolution) that are not in the Trash, unordered: what the Closed page
    lists. `resolution` (`done` or `wont_do`) narrows it; a closed task with no resolution recorded
    (one closed by a container that predates the column) counts as Done."""
    queryset = Task.objects.filter(user=user, completed_at__isnull=False, deleted_at__isnull=True)
    if resolution == Resolution.WONT_DO:
        return queryset.filter(resolution=Resolution.WONT_DO)
    if resolution == Resolution.DONE:
        return queryset.exclude(resolution=Resolution.WONT_DO)
    return queryset


def completed_set(user: User) -> QuerySet:
    """Closed tasks of either resolution (the API's `view=completed`: a Won't do task is reported
    as completed, as it has `completed_at`)."""
    return closed_set(user)


def trash_set(user: User) -> QuerySet:
    """Tasks in the Trash (subtasks included), unordered: what the Trash page lists."""
    return Task.objects.filter(user=user, deleted_at__isnull=False)


def todays_tasks(user: User) -> QuerySet:
    """Open, non-trashed tasks due *today* (not the overdue ones), unordered: the list the user can
    drag into a manual order, and the set `today_position` is taken over."""
    return open_tasks(user).filter(due_date=user_today(user))


def subtasks_of(parent: Task) -> QuerySet:
    """The non-trashed subtasks of `parent` (completed ones included), unordered: the list a
    parent's `position` order is taken over."""
    return Task.objects.filter(parent=parent, deleted_at__isnull=True)


def overdue_set(user: User) -> QuerySet:
    """Open, non-trashed tasks due before today that are not muted: what is overdue, what the Overdue
    section lists and what "Reschedule overdue to today" moves."""
    return open_tasks(user).filter(due_date__lt=user_today(user)).exclude(state__in=MUTED_STATES)


def muted_overdue_set(user: User) -> QuerySet:
    """Open, non-trashed muted tasks due before today: never shown as overdue, never moved by
    "Reschedule overdue"; where they show on Today is `TodoAccount.muted_overdue`."""
    return open_tasks(user).filter(due_date__lt=user_today(user), state__in=MUTED_STATES)


def today_count(user: User, account: TodoAccount | None = None) -> int:
    """The number beside Today: the tasks due today or earlier that the page lists, which leaves out
    the muted overdue ones unless the account shows them dimmed in Overdue (hidden, or in their own
    collapsed section, they are not part of the count)."""
    queryset = today_set(user)
    if account is None:
        account = get_account(user)
    if account.muted_overdue != MutedOverdue.DIMMED:
        queryset = queryset.exclude(due_date__lt=user_today(user), state__in=MUTED_STATES)
    return queryset.count()


# -- Where a new or moved task goes in a manual order ------------------------------------------


def after_the_others(queryset, field: str) -> int:
    """`max(field) + 1` over `queryset` when it has a manual order (any value above 0), else 0 (the
    default sort applies). `queryset` is the very set the list orders (`dates.has_manual_order`)."""
    top = queryset.aggregate(top=Max(field))['top'] or 0
    return top + 1 if top > 0 else 0


def next_position(parent: Task | None) -> int:
    """`Task.position` of a task that joins `parent`'s subtasks: after the others when they have a
    manual order, else 0. Only subtasks have a manual order in `position`; a top-level task is
    ordered in Today by `today_position` (`next_today_position`) and keeps 0 here."""
    if parent is None:
        return 0
    return after_the_others(subtasks_of(parent), 'position')


def next_today_position(user: User, due_date: date | None) -> int:
    """`Task.today_position` of a new task: after the others only when it lands in the Today list
    (it is due today) and *that list* already has a manual order; else 0, so a task created for
    another day, or in a list nobody reordered, never changes how Today sorts."""
    if due_date is None or due_date != user_today(user):
        return 0
    return after_the_others(todays_tasks(user), 'today_position')
