"""A note's history (`/api/notes/notes/{id}/versions`): list, read and restore a version. Reading is
for any of the caller's notes (the Trash's too); restoring is a save, so the note must be writable.
"""

from uuid import UUID

from django.shortcuts import get_object_or_404

from ..models import NoteVersion
from ..schemas import NoteConflictSchema, NoteSchema, NoteVersionListItemSchema, NoteVersionSchema
from ..services import NoteConflictError, list_versions, require_writable, restore_version
from .common import (
    charge,
    conflict_body,
    mapped_errors,
    note_response,
    owned_note,
)
from .router import router


def _data(version: NoteVersion, *, content: bool = False) -> dict:
    editor = version.editor
    data = {
        'id': version.pk,
        'number': version.number,
        'title': version.title,
        'editor_kind': version.editor_kind,
        'editor_name': (editor.name or None) if editor is not None else None,
        'via_link': version.link_id is not None,
        'created_at': version.created_at,
        'updated_at': version.updated_at,
    }
    if content:
        data['content'] = version.content
    return data


@router.get('/notes/{note_id}/versions', response=list[NoteVersionListItemSchema])
def list_note_versions(request, note_id: UUID):
    """The note's history, newest first (at most 50 versions), without the text: read one version
    for its content. A run of saves by the same editor within ten minutes is a single version."""
    note = owned_note(request, note_id)
    return [_data(version) for version in list_versions(note)]


@router.get('/notes/{note_id}/versions/{version_id}', response=NoteVersionSchema)
def get_note_version(request, note_id: UUID, version_id: UUID):
    """One version, with the title and content the note had after that save."""
    note = owned_note(request, note_id)
    version = get_object_or_404(
        NoteVersion.objects.select_related('editor'), pk=version_id, note=note
    )
    return _data(version, content=True)


@router.post(
    '/notes/{note_id}/versions/{version_id}/restore',
    response={200: NoteSchema, 409: NoteConflictSchema},
)
def restore_note_version(request, note_id: UUID, version_id: UUID):
    """Make a version's title and content the note's again: a new save (a new version, so what it
    replaced stays in the history). Restoring what the note already holds changes nothing. Refused
    as any change is: in the Trash (400), locked (402), taken down (409)."""
    note = owned_note(request, note_id)
    version = get_object_or_404(NoteVersion, pk=version_id, note=note)
    try:
        with mapped_errors(keep=NoteConflictError):
            require_writable(note)  # a refusal spends no budget
            charge(request, 'NOTES_EDIT_USER')
            restore_version(note, version)
    except NoteConflictError as exc:  # someone saved between the read and the write
        return 409, conflict_body(exc)
    return note_response(request, note)
