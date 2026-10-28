"""The task lists and the pages that hold them: Today, Upcoming, All, Completed and Trash.

Each page and `/todo/list/` (the partial every one of them fetches with htmx) are built from the
same context (`list_context`), the Receipts dashboard's pattern: a change re-fetches the partial
into `#task-list` and the response's `HX-Push-Url` puts the *page* URL in the address bar, so a
reload or a shared link renders the full page.
"""

from datetime import date, timedelta
from math import ceil

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.core.htmx import is_htmx

from ..forms import TaskForm
from ..models import Label, Task, TodoSettings
from ..services import (
    SORT_DEFAULT,
    SORTS,
    UPCOMING_DAYS,
    get_account,
    has_manual_order,
    open_tasks,
    order_tasks,
    overdue_set,
    todays_tasks,
    with_comment_counts,
    with_subtask_counts,
)
from .common import render_page

PAGE_SIZE = 50
VIEWS = ('today', 'upcoming', 'all', 'completed', 'trash')
TITLES = {
    'today': 'Today',
    'upcoming': 'Upcoming',
    'all': 'All tasks',
    'completed': 'Completed',
    'trash': 'Trash',
}


def page_url(view: str) -> str:
    return reverse(f'todo:{view}')


def belongs(task: Task, view: str, today: date) -> bool:
    """Whether `task` (as it is now) would still be listed in `view`: after an action on a row,
    one that no longer belongs is removed instead of re-rendered. `subtask` is the list on the task
    page, where a completed subtask stays."""
    if view == 'subtask':
        return not task.is_trashed
    if view == 'trash':
        return task.is_trashed
    if task.is_trashed:
        return False
    if view == 'completed':
        return task.is_completed
    if task.is_completed:
        return False
    if view == 'today':
        return task.due_date is not None and task.due_date <= today
    if view == 'upcoming':
        return task.due_date is not None and today < task.due_date <= today + timedelta(
            days=UPCOMING_DAYS
        )
    return True


def decorate(queryset: QuerySet) -> QuerySet:
    """What a row needs: the subtask and comment counts, the parent (shown on a subtask), the
    repeat rule and the labels."""
    return (
        with_comment_counts(with_subtask_counts(queryset))
        .select_related('parent', 'recurrence')
        .prefetch_related('labels')
    )


def row_task(user, pk) -> Task:
    """One task loaded the way a list loads its rows."""
    return decorate(Task.objects.filter(user=user, pk=pk)).get()


def _query(**params: str) -> str:
    return '&'.join(f'{key}={value}' for key, value in params.items() if value)


def list_context(request: AuthenticatedHttpRequest, view: str) -> dict:
    """The context of the list `view` (`today`, `upcoming`, `all`, `completed`, `trash`) for the
    requester, reading `sort` and `page` from the query string."""
    user = request.user
    today = user_today(user)
    sort = request.GET.get('sort', '')
    sort = sort if sort in SORTS else SORT_DEFAULT
    context: dict = {'view': view, 'list_title': TITLES[view], 'today': today}

    if view == 'today':
        overdue = list(decorate(order_tasks(overdue_set(user))))
        todays = todays_tasks(user)
        manual = has_manual_order(todays, 'today_position')
        context.update(
            {
                'overdue': overdue,
                'tasks': list(decorate(order_tasks(todays, 'today_manual' if manual else 'date'))),
                'manual': manual,
            }
        )
        return context

    if view == 'upcoming':
        queryset = open_tasks(user).filter(
            due_date__gt=today, due_date__lte=today + timedelta(days=UPCOMING_DAYS)
        )
        by_day: dict[date, list[Task]] = {}
        for task in decorate(order_tasks(queryset)):
            assert task.due_date is not None  # filtered above
            by_day.setdefault(task.due_date, []).append(task)
        context['days'] = [
            (day, by_day.get(day, []))
            for day in (today + timedelta(days=n) for n in range(1, UPCOMING_DAYS + 1))
        ]
        return context

    if view == 'all':
        queryset = order_tasks(decorate(open_tasks(user)), sort)
    elif view == 'completed':
        queryset = decorate(
            Task.objects.filter(user=user, completed_at__isnull=False, deleted_at__isnull=True)
        ).order_by('-completed_at', '-number')
    else:
        queryset = decorate(Task.objects.filter(user=user, deleted_at__isnull=False)).order_by(
            '-deleted_at', '-number'
        )
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    tasks = list(page_obj.object_list)
    if view == 'trash':
        now = timezone.now()
        trash_days = TodoSettings.load().trash_days
        for task in tasks:
            assert task.deleted_at is not None  # filtered above
            purge_at = task.deleted_at + timedelta(days=trash_days)
            task.days_left = max(ceil((purge_at - now).total_seconds() / 86400), 0)
        context['trash_days'] = trash_days
    base = {'sort': sort if sort != SORT_DEFAULT else ''}
    context.update(
        {
            'tasks': tasks,
            'page_obj': page_obj,
            'sort': sort,
            'sorts': SORTS,
            'page_query': _query(**base, page=str(page_obj.number) if page_obj.number > 1 else ''),
            'prev_query': _query(**base, page=str(page_obj.previous_page_number()))
            if page_obj.has_previous()
            else '',
            'next_query': _query(**base, page=str(page_obj.next_page_number()))
            if page_obj.has_next()
            else '',
        }
    )
    return context


def _page(request: AuthenticatedHttpRequest, view: str) -> HttpResponse:
    context = list_context(request, view)
    context['list_url'] = reverse('todo:list')
    context['page_url'] = page_url(view)
    if view in ('today', 'upcoming', 'all'):
        context['form'] = TaskForm(user=request.user)
        context['labels'] = Label.objects.filter(user=request.user)
        context['form_values'] = {}
        context['selected_label_ids'] = []
    return render_page(request, f'todo/{view}.html', context, nav=view)


@login_required
@require_GET
@ensure_csrf_cookie
def today_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'today')


@login_required
@require_GET
@ensure_csrf_cookie
def upcoming_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'upcoming')


@login_required
@require_GET
@ensure_csrf_cookie
def all_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'all')


@login_required
@require_GET
@ensure_csrf_cookie
def completed_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'completed')


@login_required
@require_GET
@ensure_csrf_cookie
def trash_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'trash')


@login_required
@require_GET
def task_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The list partial (`view`, `sort`, `page`). Opened directly (not by htmx) it goes to the full
    page instead."""
    view = request.GET.get('view', '')
    if view not in VIEWS:
        view = 'today'
    if not is_htmx(request):
        query = _query(sort=request.GET.get('sort', ''), page=request.GET.get('page', ''))
        return redirect(f'{page_url(view)}?{query}' if query else page_url(view))

    get_account(request.user)  # the partial is the first thing a stale tab may fetch
    context = list_context(request, view)
    context['list_url'] = reverse('todo:list')
    context['page_url'] = page_url(view)
    response = render(request, 'todo/partials/task_list.html', context)
    query = context.get('page_query', '')
    response['HX-Push-Url'] = f'{page_url(view)}?{query}' if query else page_url(view)
    return response
