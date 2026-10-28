"""The label endpoints (`/api/todo/labels`): the user's flat, coloured labels. Each one calls the
service the Labels page uses. Every change spends `TODO_EDIT_USER`."""

from uuid import UUID

from django.shortcuts import get_object_or_404

from .. import services
from ..models import Label
from ..schemas import LabelCreateSchema, LabelOrderSchema, LabelSchema, LabelUpdateSchema
from .common import charge, mapped_errors
from .router import router


def _owned(request, label_id: UUID) -> Label:
    """The caller's label, or a 404."""
    return get_object_or_404(Label.objects.select_related('user'), pk=label_id, user=request.auth)


def _counted(user, label_id: UUID) -> Label:
    """The label again, with its `task_count` (a 404 if it was deleted since, say from another
    client)."""
    return get_object_or_404(services.list_labels(user), pk=label_id)


@router.get('/labels', response=list[LabelSchema])
def list_labels(request):
    """The caller's labels in order (`task_count` leaves out tasks in the Trash)."""
    return list(services.list_labels(request.auth))


@router.post('/labels', response={201: LabelSchema})
def create_label(request, payload: LabelCreateSchema):
    """Add a label at the end of the list. Names are unique, ignoring case; `color` is `#rrggbb`."""
    charge(request, 'TODO_EDIT_USER')
    with mapped_errors():
        label = services.create_label(
            request.auth, payload.name, payload.color, payload.description
        )
    return 201, _counted(request.auth, label.pk)


# Registered before the `{label_id}` routes (a different method and a `uuid` converter, so
# `reorder` is never read as an id either way, but it reads better next to the others).
@router.post('/labels/reorder', response=list[LabelSchema])
def reorder_labels(request, payload: LabelOrderSchema):
    """Put the labels in the order given; ids that aren't yours are ignored."""
    charge(request, 'TODO_EDIT_USER')
    services.reorder_labels(request.auth, payload.ids)
    return list(services.list_labels(request.auth))


@router.patch('/labels/{label_id}', response=LabelSchema)
def update_label(request, label_id: UUID, payload: LabelUpdateSchema):
    """Rename, recolour or re-describe a label; only the fields present are applied."""
    label = _owned(request, label_id)
    changes = {
        name: '' if value is None else value
        for name, value in payload.model_dump(exclude_unset=True).items()
    }
    if changes:
        charge(request, 'TODO_EDIT_USER')
        with mapped_errors():
            services.update_label(label, **changes)
    return _counted(request.auth, label.pk)


@router.delete('/labels/{label_id}', response={204: None})
def delete_label(request, label_id: UUID):
    """Delete a label; the tasks that had it keep their other labels."""
    label = _owned(request, label_id)
    charge(request, 'TODO_EDIT_USER')
    services.delete_label(label)
    return 204, None
