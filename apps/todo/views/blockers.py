"""Blockers on the task page (issue #273): add and remove a "Blocked by" link, the suggestions the
add box offers and the open blockers the close dialog names.

The rules are `services.blockers`'; a refusal follows the 422 convention into `#blocker-msg`. The
answer to an add or a remove is the re-rendered panel (`ctx=page`), which shows both lists.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx

from ..forms import BlockerForm
from ..models import Task
from ..services import blockers
from .common import form_error_lines, owned_task, refuse, validation_lines
from .tasks import _act, _task_url

BLOCKER_MSG = '#blocker-msg'


@login_required
@require_POST
def blocker_add(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """Add a blocker to the task: a number, or a title the suggestions offered. Everything that can
    be told without writing is checked first, so a refusal spends no rate-limit budget."""
    task = owned_task(request, number)
    form = BlockerForm(request.POST)
    if not form.is_valid():
        return refuse(
            request, form_error_lines(form), retarget=BLOCKER_MSG, fallback=_task_url(task)
        )
    try:
        blocker = blockers.find_blocker(request.user, form.cleaned_data['blocker'], task)
        blockers.check_add(task, blocker)
    except ValidationError as exc:
        return refuse(
            request, validation_lines(exc), retarget=BLOCKER_MSG, fallback=_task_url(task)
        )
    return _act(
        request,
        number,
        lambda found: blockers.add_blocker(found, blocker),
        task=task,
        retarget=BLOCKER_MSG,
    )


@login_required
@require_POST
def blocker_remove(
    request: AuthenticatedHttpRequest, number: int, blocker_number: int
) -> HttpResponse:
    """Take a blocker off the task. A blocker that is closed or in the Trash can be removed too."""
    task = owned_task(request, number)
    blocker = get_object_or_404(Task, user=request.user, number=blocker_number)
    return _act(
        request,
        number,
        lambda found: blockers.remove_blocker(found, blocker),
        task=task,
        retarget=BLOCKER_MSG,
    )


@login_required
@require_GET
def blocker_suggest(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """The `<option>`s of the add box's list for what has been typed (`blocker`): the user's open
    tasks, not the task itself nor one that already blocks it. Opened directly it goes back to the
    task."""
    task = owned_task(request, number, trashed=None)
    if not is_htmx(request):
        return redirect(_task_url(task))
    found = blockers.suggestions(request.user, task, request.GET.get('blocker', ''))
    return render(request, 'todo/partials/blocker_options.html', {'suggestions': found})


@login_required
@require_GET
def open_blockers(request: AuthenticatedHttpRequest, number: int) -> JsonResponse:
    """The task's open, non-trashed blockers as JSON (`{"blockers": [{"number", "title"}]}`): what
    the close dialog names before it asks to confirm."""
    task = owned_task(request, number, trashed=None)
    response = JsonResponse(
        {
            'blockers': [
                {'number': blocker.number, 'title': blocker.title}
                for blocker in blockers.open_blockers(task)
            ]
        }
    )
    response['Cache-Control'] = 'no-store'
    return response
