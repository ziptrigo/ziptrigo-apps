"""The blocker endpoints (`/api/todo/tasks/{number}/blockers`): "blocked by" links between the
caller's own tasks. Each calls the service the task page uses (`services.blockers`), so the same
rules apply: no task blocks itself, no cycle of any length, at most 20 blockers per task, neither
task in the Trash.

A link is kept when either task closes or is trashed. An *open* blocker (open and not in the Trash)
makes an open task `blocked`; the task goes back to `todo` when its last open blocker closes, is
trashed or is removed. Both endpoints spend `TODO_EDIT_USER`, after every check (a refused request
spends nothing; so does adding a link that already exists, and removing one that does not).
"""

from django.core.exceptions import ValidationError

from ..models import Task, TaskBlocker
from ..schemas import BlockerSchema, TaskSchema
from ..services import blockers
from .common import charge, mapped_errors, owned_task, task_response
from .router import router


@router.post('/tasks/{number}/blockers', response=TaskSchema)
def add_blocker(request, number: int, payload: BlockerSchema):
    """Make the task numbered `number` (in the body) block this one, and answer this task.

    Both tasks are the caller's own and out of the Trash (a 400 `number: There is no task #N.` for
    a number that doesn't exist **or isn't the caller's**; `number: #N is in the trash. Restore it
    first.`). A 400 too when a task is its own blocker, when the link would close a cycle (`#1200
    is already blocked by #1234, directly or through other tasks.`) and when this task already has
    20 blockers. Adding a link that already exists changes nothing. An open task given an open
    blocker becomes `blocked`, from any open state; a closed blocker, or a closed task, only links.
    """
    task = owned_task(request, number)
    with mapped_errors():
        (other,) = blockers.tasks_by_numbers(request.auth, [payload.number], 'number')
        if not blockers.check_add(task, other):  # already linked: nothing to do, nothing to spend
            return task_response(request, task)
        charge(request, 'TODO_EDIT_USER')
        blockers.add_blocker(task, other)
    return task_response(request, task)


@router.delete('/tasks/{number}/blockers/{blocker_number}', response={204: None})
def remove_blocker(request, number: int, blocker_number: int):
    """Take the task numbered `blocker_number` off this task's blockers. Idempotent: 204 whether or
    not the link was there (also for a number that doesn't exist or isn't the caller's, so a number
    can't be probed), spending nothing when there was nothing to remove. A task left with no open
    blocker that was `blocked` goes back to `todo`. A 400 for a task in the Trash."""
    task = owned_task(request, number)
    other = Task.objects.filter(user=request.auth, number=blocker_number).first()
    if other is None or not TaskBlocker.objects.filter(blocked=task, blocker=other).exists():
        return 204, None
    with mapped_errors():
        if task.is_trashed:
            raise ValidationError('Restore the task before changing it.')
        charge(request, 'TODO_EDIT_USER')
        blockers.remove_blocker(task, other)
    return 204, None
