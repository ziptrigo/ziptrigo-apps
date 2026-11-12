"""The tag endpoints (`/api/notes/tags`): the user's flat, coloured tags. Each one calls the service
the Tags page uses. Every change spends `NOTES_EDIT_USER`."""

from uuid import UUID

from django.shortcuts import get_object_or_404

from .. import services
from ..models import Tag
from ..schemas import NoteTagCreateSchema, NoteTagOrderSchema, NoteTagSchema, NoteTagUpdateSchema
from ..services import tags as tag_service
from .common import charge, mapped_errors
from .router import router


def _owned(request, tag_id: UUID) -> Tag:
    """The caller's tag, or a 404."""
    return get_object_or_404(Tag.objects.select_related('user'), pk=tag_id, user=request.auth)


def _counted(user, tag_id: UUID) -> Tag:
    """The tag again, with its `note_count` (a 404 if it was deleted since, say from another
    client)."""
    return get_object_or_404(services.list_tags(user), pk=tag_id)


@router.get('/tags', response=list[NoteTagSchema])
def list_tags(request):
    """The caller's tags in order (`note_count` leaves out notes in the Trash)."""
    return list(services.list_tags(request.auth))


@router.post('/tags', response={201: NoteTagSchema})
def create_tag(request, payload: NoteTagCreateSchema):
    """Add a tag at the end of the list. Names are unique, ignoring case; `color` is `#rrggbb`."""
    with mapped_errors():
        tag_service.clean_name(payload.name)  # a refusal here spends no budget
        tag_service.clean_color(payload.color)
        tag_service.clean_description(payload.description)
        charge(request, 'NOTES_EDIT_USER')
        tag = services.create_tag(request.auth, payload.name, payload.color, payload.description)
    return 201, _counted(request.auth, tag.pk)


# Registered before the `{tag_id}` routes (a different method and a `uuid` converter, so
# `reorder` is never read as an id either way, but it reads better next to the others).
@router.post('/tags/reorder', response=list[NoteTagSchema])
def reorder_tags(request, payload: NoteTagOrderSchema):
    """Put the tags in the order given; ids that aren't yours are ignored."""
    charge(request, 'NOTES_EDIT_USER')
    services.reorder_tags(request.auth, payload.ids)
    return list(services.list_tags(request.auth))


@router.patch('/tags/{tag_id}', response=NoteTagSchema)
def update_tag(request, tag_id: UUID, payload: NoteTagUpdateSchema):
    """Rename, recolour or re-describe a tag; only the fields present are applied."""
    tag = _owned(request, tag_id)
    changes = {
        name: '' if value is None else value
        for name, value in payload.model_dump(exclude_unset=True).items()
    }
    if changes:
        with mapped_errors():
            if 'name' in changes:  # a refusal here spends no budget
                tag_service.clean_name(changes['name'])
            if 'color' in changes:
                tag_service.clean_color(changes['color'])
            if 'description' in changes:
                tag_service.clean_description(changes['description'])
            charge(request, 'NOTES_EDIT_USER')
            services.update_tag(tag, **changes)
    return _counted(request.auth, tag.pk)


@router.delete('/tags/{tag_id}', response={204: None})
def delete_tag(request, tag_id: UUID):
    """Delete a tag; the notes that had it keep their other tags."""
    tag = _owned(request, tag_id)
    charge(request, 'NOTES_EDIT_USER')
    services.delete_tag(tag)
    return 204, None
