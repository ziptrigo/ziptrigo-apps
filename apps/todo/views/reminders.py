"""Reminders (issue #182): adding, editing and removing a task's reminders from the task page, and
the history of everything that was delivered.

The reminders block sits in the task page's panel (`partials/reminders.html`), so each action
answers the re-rendered panel (the fire times shown depend on the date, which the panel also
shows). The rules are `services.reminders`'; a refusal is a 422 with `Label: message` lines swapped
into `#reminder-msg`. Another user's task, a reminder of another task and a trashed task are a 404.
"""

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx

from ..forms import ReminderForm
from ..models import Reminder, ReminderDelivery, Task
from ..services import get_account, reminders
from .common import form_error_lines, limited, owned_task, refuse, render_page, validation_lines
from .tasks import panel_response

SLOT = '#reminder-msg'
HISTORY_PAGE_SIZE = 50


def _task_url(task: Task) -> str:
    return reverse('todo:task', args=[task.number])


def _done(request: AuthenticatedHttpRequest, task: Task) -> HttpResponse:
    """The answer to a successful action: the panel (htmx), else back to the task page."""
    if is_htmx(request):
        return panel_response(request, task)
    return redirect(_task_url(task) + '#task-reminders')


def _owned_reminder(task: Task, reminder_id: UUID) -> Reminder:
    """The reminder, if it is one of `task`'s; any other is a 404."""
    return get_object_or_404(
        Reminder.objects.select_related('task', 'task__user'), pk=reminder_id, task=task
    )


def _refuse(request: AuthenticatedHttpRequest, task: Task, lines: list[str]) -> HttpResponse:
    return refuse(request, lines, retarget=SLOT, fallback=_task_url(task))


@login_required
@require_POST
def reminder_add(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    task = owned_task(request, number)
    form = ReminderForm(request.POST)
    if not form.is_valid():
        return _refuse(request, task, form_error_lines(form))
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    assert form.minutes_before is not None  # a valid form always has the time
    channel = form.cleaned_data['channel'] or get_account(request.user).default_reminder_channel
    try:
        reminders.add_reminder(task, minutes_before=form.minutes_before, channel=channel)
    except ValidationError as exc:
        return _refuse(request, task, validation_lines(exc))
    return _done(request, task)


@login_required
@require_POST
def reminder_edit(
    request: AuthenticatedHttpRequest, number: int, reminder_id: UUID
) -> HttpResponse:
    task = owned_task(request, number)
    reminder = _owned_reminder(task, reminder_id)
    form = ReminderForm(request.POST)
    if not form.is_valid():
        return _refuse(request, task, form_error_lines(form))
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    assert form.minutes_before is not None  # a valid form always has the time
    try:
        reminders.update_reminder(
            reminder,
            minutes_before=form.minutes_before,
            channel=form.cleaned_data['channel'] or None,
        )
    except ValidationError as exc:
        return _refuse(request, task, validation_lines(exc))
    return _done(request, task)


@login_required
@require_POST
def reminder_delete(
    request: AuthenticatedHttpRequest, number: int, reminder_id: UUID
) -> HttpResponse:
    task = owned_task(request, number)
    reminder = _owned_reminder(task, reminder_id)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    try:
        reminders.delete_reminder(reminder)
    except ValidationError as exc:
        return _refuse(request, task, validation_lines(exc))
    return _done(request, task)


@login_required
@require_GET
def reminder_history(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Every reminder delivered to the user, newest first, 50 to a page. A row links to its task
    while it exists."""
    queryset = ReminderDelivery.objects.filter(user=request.user).select_related('task')
    page_obj = Paginator(queryset, HISTORY_PAGE_SIZE).get_page(request.GET.get('page'))
    return render_page(
        request,
        'todo/reminder_history.html',
        {'page_obj': page_obj, 'deliveries': list(page_obj.object_list)},
        nav='settings',
    )
