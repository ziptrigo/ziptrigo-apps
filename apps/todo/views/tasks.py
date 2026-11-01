"""Task actions and the task page (`/todo/<number>/`).

Every mutation is a POST to a session view that calls `apps.todo.services` (the rules are the
services', never re-implemented here). An htmx request is answered with the re-rendered row
(`ctx=row`, the default; a row that no longer belongs to the list it was in is removed instead) or,
from the task page (`ctx=page`), the re-rendered panel; without htmx the action redirects back.
Refusals follow the 422 convention: `Label: message` lines swapped into the page's message slot.
"""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.billing.services import InsufficientCreditsError
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import ChecklistForm, PostponeForm, SubtaskForm, TaskForm
from ..forms.reminder import PRESETS as REMINDER_PRESETS
from ..forms.reminder import UNITS as REMINDER_UNITS
from ..models import Label, ReminderChannel, Task, TodoSettings
from ..services import (
    complete_task,
    create_task,
    delete_forever,
    get_account,
    has_manual_order,
    open_tasks,
    order_tasks,
    postpone_task,
    recurrence,
    referenced_by,
    reminders,
    reorder,
    reschedule_overdue,
    restore_task,
    subtasks_of,
    today_set,
    toggle_checklist,
    trash_task,
    uncomplete_task,
    update_task,
)
from .attachments import attachments_context
from .comments import comments_context
from .common import (
    form_error_lines,
    free_line,
    limited,
    owned_task,
    redirect_back,
    refuse,
    render_page,
    validation_lines,
)
from .lists import VIEWS, belongs, decorate, list_context, page_url, row_task

PANEL_MSG = '#task-msg'
CREDITS_LINE = 'You do not have enough credits to add this task.'
NEIGHBOUR_ROWS = 20
CLEAR_PAGE_MSG = (
    b'<div id="todo-msg" hx-swap-oob="true" class="mb-4 text-sm" aria-live="polite"></div>'
)


def _view_of(request: AuthenticatedHttpRequest) -> str:
    view = request.POST.get('view', '')
    return view if view in (*VIEWS, 'subtask', 'search') else 'all'


def _is_page(request: AuthenticatedHttpRequest) -> bool:
    return request.POST.get('ctx') == 'page'


def _slot(request: AuthenticatedHttpRequest) -> str:
    return PANEL_MSG if _is_page(request) else '#todo-msg'


def _task_url(task: Task) -> str:
    return reverse('todo:task', args=[task.number])


# -- Responses -----------------------------------------------------------------------------------


def panel_context(request: AuthenticatedHttpRequest, task: Task) -> dict:
    """The context of the task page's panel (also what an action from the page re-renders). The
    comments are not part of it: they sit beside the panel (`comments_context`), so a panel action
    neither re-renders them nor loses a draft typed in them."""
    user = request.user
    task = decorate(Task.objects.filter(pk=task.pk)).get()
    subtasks = []
    open_subtasks = 0
    if task.parent_id is None:
        queryset = subtasks_of(task)
        manual = has_manual_order(queryset)
        subtasks = list(decorate(order_tasks(queryset, 'manual' if manual else 'date')))
        open_subtasks = sum(1 for sub in subtasks if not sub.is_completed)
    labels = list(Label.objects.filter(user=user))
    return {
        'reminders': reminders.task_reminders(task),
        'reminder_history': reminders.task_history(task),
        'reminder_presets': REMINDER_PRESETS,
        'reminder_units': REMINDER_UNITS,
        'reminder_channels': ReminderChannel.choices,
        'default_reminder_channel': get_account(user).default_reminder_channel,
        'referenced_by': referenced_by(task),
        'task': task,
        'subtasks': subtasks,
        'open_subtasks': open_subtasks,
        'labels': labels,
        'selected_label_ids': [str(label.pk) for label in task.labels.all()],
        'today': user_today(user),
        'trash_view': task.is_trashed,
    }


def panel_response(
    request: AuthenticatedHttpRequest,
    task: Task,
    *,
    status: int = 200,
    refresh_comments: bool = False,
) -> HttpResponse:
    """The re-rendered panel. `refresh_comments` adds the comments section out of band: only for
    an action that changes what it shows (moving the task to the Trash or back makes the section
    read-only or editable, and decides whether `#1234` to the task itself is a link); any other
    action leaves the section, and a draft typed in it, alone."""
    response = render(
        request, 'todo/partials/task_panel.html', panel_context(request, task), status=status
    )
    if refresh_comments:
        context = {**comments_context(request, task), 'oob': True}
        response.content += render_to_string(
            'todo/partials/comments.html', context, request=request
        ).encode()
    return response


def after_action(
    request: AuthenticatedHttpRequest, task: Task, *, refresh_comments: bool = False
) -> HttpResponse:
    """The answer to a successful action on `task`: the panel (from the task page), the row, or an
    empty body that removes the row (it left the list it was in). Without htmx, a redirect back."""
    if not is_htmx(request):
        return redirect(_task_url(task)) if _is_page(request) else redirect_back(request)
    if _is_page(request):
        return panel_response(request, task, refresh_comments=refresh_comments)
    view = _view_of(request)
    today = user_today(request.user)
    fresh = row_task(request.user, task.pk)
    context = {
        'task': fresh,
        'view': view,
        'today': today,
        'oob_nav': True,
        'today_count': today_set(request.user).count(),
        'sortable': request.POST.get('sortable') == '1',
    }
    template = (
        'todo/partials/task_row.html'
        if belongs(fresh, view, today)
        else 'todo/partials/row_gone.html'
    )
    response = render(request, template, context)
    response.content += _refreshed_neighbours(request, fresh, view, today)
    # A success clears a refusal an earlier action left in the page's message slot.
    response.content += CLEAR_PAGE_MSG
    return response


def _refreshed_neighbours(request: AuthenticatedHttpRequest, task: Task, view: str, today) -> bytes:
    """Out-of-band updates for what an action on a row changes elsewhere on the same page, which the
    row's own answer would leave as rendered with the page: the other open occurrences of a
    repeating task (`Task.offers_stop_repeating` depends on how many are left) and, for a subtask,
    the parent's Complete button (its question names the open subtasks). A target that is not on
    the page is simply ignored by htmx."""
    parts: list[str] = []
    if task.recurrence_id:
        siblings = decorate(
            open_tasks(request.user).filter(recurrence_id=task.recurrence_id).exclude(pk=task.pk)
        )[:NEIGHBOUR_ROWS]
        for sibling in siblings:
            parts.append(
                render_to_string(
                    'todo/partials/task_row.html',
                    {'task': sibling, 'view': view, 'today': today, 'oob': True},
                    request=request,
                )
            )
    if view == 'subtask' and task.parent_id:
        parent = row_task(request.user, task.parent_id)
        open_subtasks = subtasks_of(parent).filter(completed_at__isnull=True).count()
        parts.append(
            render_to_string(
                'todo/partials/complete_button.html',
                {'task': parent, 'open_subtasks': open_subtasks, 'oob': True},
                request=request,
            )
        )
    return ''.join(parts).encode()


def _rows_removed(numbers: list[int]) -> bytes:
    """Out-of-band deletes of the rows of `numbers`: the subtasks an action on their parent took out
    of the list with it (a row that is not on the page is ignored)."""
    return ''.join(
        f'<li id="task-{number}" hx-swap-oob="delete"></li>' for number in numbers
    ).encode()


def _blocked_banner(request: AuthenticatedHttpRequest) -> bytes:
    """The "Tasks are blocked" banner, out of band, when the account is blocked now: completing a
    repeating task whose next occurrence the balance can't cover creates it owed and blocks task
    creation, which the page would otherwise show only after a reload."""
    if not get_account(request.user).is_blocked:
        return b''
    return render_to_string(
        'todo/partials/blocked_banner.html', {'todo_blocked': True, 'oob': True}, request=request
    ).encode()


def _refuse_validation(
    request: AuthenticatedHttpRequest, exc: ValidationError, task: Task | None = None
) -> HttpResponse:
    fallback = _task_url(task) if task is not None and _is_page(request) else ''
    return refuse(request, validation_lines(exc), retarget=_slot(request), fallback=fallback)


# -- Create --------------------------------------------------------------------------------------


@login_required
@require_POST
def task_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The quick-add form. On success the list the request came from is re-rendered (`view`) and
    the form cleared."""
    form = TaskForm(request.POST, user=request.user)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget='#quick-add-msg')
    if (response := limited(request, 'TODO_CREATE_USER', retarget='#quick-add-msg')) is not None:
        return response
    try:
        create_task(request.user, **form.changes())
    except InsufficientCreditsError:
        return refuse(request, CREDITS_LINE, retarget='#quick-add-msg', top_up=True)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget='#quick-add-msg')

    if not is_htmx(request):
        messages.success(request, 'Task added.')
        return redirect_back(request)
    view = request.POST.get('view', '')
    view = view if view in VIEWS else 'today'
    used, free = free_line(get_account(request.user), TodoSettings.load())
    context = list_context(request, view)
    context.update(
        {
            'list_url': reverse('todo:list'),
            'page_url': page_url(view),
            'form': TaskForm(user=request.user),
            'labels': Label.objects.filter(user=request.user),
            'form_values': {},
            'selected_label_ids': [],
            'oob_form': True,
            'free_used': used,
            'free_total': free,
            'oob_nav': True,
            'today_count': today_set(request.user).count(),
        }
    )
    return render(request, 'todo/partials/task_list.html', context)


@login_required
@require_POST
def subtask_add(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    parent = owned_task(request, number)
    form = SubtaskForm(request.POST)
    if not form.is_valid():
        return refuse(
            request, form_error_lines(form), retarget='#subtask-msg', fallback=_task_url(parent)
        )
    if (response := limited(request, 'TODO_CREATE_USER', retarget='#subtask-msg')) is not None:
        return response
    try:
        create_task(request.user, title=form.cleaned_data['title'], parent=parent)
    except InsufficientCreditsError:
        return refuse(
            request, CREDITS_LINE, retarget='#subtask-msg', top_up=True, fallback=_task_url(parent)
        )
    except ValidationError as exc:
        return refuse(
            request, validation_lines(exc), retarget='#subtask-msg', fallback=_task_url(parent)
        )
    if not is_htmx(request):
        return redirect(_task_url(parent))
    return panel_response(request, parent)


# -- The task page -------------------------------------------------------------------------------


@login_required
@require_GET
@ensure_csrf_cookie
def task_page(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """The task page. A trashed task opens too, read-only, with Restore."""
    task = owned_task(request, number, trashed=None)
    get_account(request.user)
    context = {**comments_context(request, task), **panel_context(request, task)}
    context['title_task'] = task
    context.update(attachments_context(request, task))
    return render_page(
        request, 'todo/task.html', context, nav='trash' if task.is_trashed else 'all'
    )


@login_required
@require_POST
def task_edit(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """Save a part of the task (`part`: `title`, `description` or `details`), or all of the task
    form when there is none."""
    task = owned_task(request, number)
    part = request.POST.get('part', '')
    form = TaskForm(request.POST, user=request.user, part=part)
    if not form.is_valid():
        return refuse(
            request, form_error_lines(form), retarget=_slot(request), fallback=_task_url(task)
        )
    if (response := limited(request, 'TODO_EDIT_USER', retarget=_slot(request))) is not None:
        return response
    try:
        update_task(task, changes=form.changes())
    except ValidationError as exc:
        return _refuse_validation(request, exc, task)
    return after_action(request, task)


@login_required
@require_POST
def task_checklist(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """Tick or untick a checklist box of the description; answers the re-rendered description."""
    task = owned_task(request, number)
    form = ChecklistForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=PANEL_MSG, fallback=_task_url(task))
    if (response := limited(request, 'TODO_EDIT_USER', retarget=PANEL_MSG)) is not None:
        return response
    try:
        task = toggle_checklist(task, form.cleaned_data['index'], form.cleaned_data['checked'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=PANEL_MSG, fallback=_task_url(task))
    if not is_htmx(request):
        return redirect(_task_url(task))
    return render(request, 'todo/partials/description.html', {'task': task})


# -- State changes -------------------------------------------------------------------------------


def _act(
    request: AuthenticatedHttpRequest,
    number: int,
    action,
    *,
    trashed: bool | None = False,
    task: Task | None = None,
    refresh_comments: bool = False,
):
    """Look up the task (unless the caller already has it), charge the edit budget, run
    `action(task)` and answer (`refresh_comments`: see `panel_response`)."""
    if task is None:
        task = owned_task(request, number, trashed=trashed)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=_slot(request))) is not None:
        return response
    try:
        action(task)
    except ValidationError as exc:
        return _refuse_validation(request, exc, task)
    return after_action(request, task, refresh_comments=refresh_comments)


@login_required
@require_POST
def task_complete(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    # Completing a parent completes its open subtasks too. On a list, their rows are on the same
    # page as the parent's, so they are removed along with it (out of band) instead of staying as
    # open rows until a reload. (The task page re-renders its whole panel anyway.)
    cascaded = list(
        Task.objects.filter(
            user=request.user,
            parent__number=number,
            completed_at__isnull=True,
            deleted_at__isnull=True,
        ).values_list('number', flat=True)
    )
    response = _act(request, number, complete_task)
    if response.status_code == 200 and is_htmx(request):
        if cascaded and not _is_page(request):
            response.content += _rows_removed(cascaded)
        response.content += _blocked_banner(request)
    return response


@login_required
@require_POST
def task_uncomplete(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    return _act(request, number, uncomplete_task)


@login_required
@require_POST
def task_postpone(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    task = owned_task(request, number)
    form = PostponeForm(request.POST)
    if not form.is_valid():  # includes a preset that is not one of the choices: a 422
        return refuse(
            request, form_error_lines(form), retarget=_slot(request), fallback=_task_url(task)
        )
    return _act(
        request,
        number,
        lambda found: postpone_task(
            found, preset=form.cleaned_data['preset'], date=form.cleaned_data['date']
        ),
        task=task,
    )


@login_required
@require_POST
def task_trash(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """Move to the Trash. A repeating task's confirmation offers "Also stop repeating"
    (`stop_recurrence=1`); without it the repeat goes on."""
    stop_repeat = request.POST.get('stop_recurrence') == '1'
    # The subtasks go to the Trash with their parent: their rows leave the list too.
    cascaded = list(
        Task.objects.filter(
            user=request.user, parent__number=number, deleted_at__isnull=True
        ).values_list('number', flat=True)
    )

    def trash(task: Task) -> None:
        trash_task(task)
        if stop_repeat:
            recurrence.stop(task)

    response = _act(request, number, trash, refresh_comments=True)
    if cascaded and response.status_code == 200 and is_htmx(request) and not _is_page(request):
        response.content += _rows_removed(cascaded)
    return response


@login_required
@require_POST
def recurrence_stop(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """Stop repeating: the repeat rule ends, the task stays."""
    return _act(request, number, recurrence.stop, trashed=None)


@login_required
@require_POST
def task_restore(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    # Restoring a parent restores the subtasks trashed with it (the same `deleted_at`): on the Trash
    # page their rows are removed with it, instead of staying as rows whose Restore answers a 404.
    cascaded = list(
        Task.objects.filter(
            user=request.user,
            parent__number=number,
            deleted_at__isnull=False,
            deleted_at=F('parent__deleted_at'),
        ).values_list('number', flat=True)
    )
    response = _act(request, number, restore_task, trashed=True, refresh_comments=True)
    if cascaded and response.status_code == 200 and is_htmx(request) and not _is_page(request):
        response.content += _rows_removed(cascaded)
    return response


@login_required
@require_POST
def task_delete(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """Delete a trashed task forever (a confirmation modal asks first)."""
    task = owned_task(request, number, trashed=True)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=_slot(request))) is not None:
        return response
    cascaded = list(
        Task.objects.filter(user=request.user, parent=task).values_list('number', flat=True)
    )
    try:
        delete_forever(task)
    except ValidationError as exc:  # unpaid: it has to be settled first
        return refuse(
            request,
            validation_lines(exc),
            retarget=_slot(request),
            top_up=True,
            fallback=_task_url(task) if _is_page(request) else page_url('trash'),
        )
    if is_htmx(request) and not _is_page(request):
        response = render(
            request,
            'todo/partials/row_gone.html',
            {'oob_nav': True, 'today_count': today_set(request.user).count(), 'task': task},
        )
        response.content += _rows_removed(cascaded) + CLEAR_PAGE_MSG
        return response
    messages.success(request, 'Task deleted.')
    return hx_redirect(request, reverse('todo:trash'))


@login_required
@require_POST
def reschedule_overdue_view(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Set the due date of every overdue task to today (a confirmation modal names the count)."""
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    count = reschedule_overdue(request.user)
    if count:
        messages.success(
            request, f'{count} overdue task{"s" if count != 1 else ""} moved to today.'
        )
    return hx_redirect(request, reverse('todo:today'))


# -- Manual order --------------------------------------------------------------------------------


@login_required
@require_POST
def reorder_view(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Write the manual order SortableJS posts: `scope` (`today` or `subtasks`), `ids[]` and, for
    subtasks, `parent` (the parent's number). Answers 204; nothing on the page changes."""
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    ids: list[UUID] = []
    for raw in request.POST.getlist('ids[]'):
        try:
            ids.append(UUID(raw))
        except ValueError:
            return refuse(request, 'Could not read the new order.')
    parent = None
    if request.POST.get('scope') == 'subtasks':
        try:
            parent = owned_task(request, int(request.POST.get('parent', '')))
        except ValueError:
            return refuse(request, 'Choose the task whose subtasks to order.')
    try:
        reorder(request.user, scope=request.POST.get('scope', ''), ids=ids, parent=parent)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc))
    return HttpResponse(status=204)
