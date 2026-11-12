"""`POST /api/notes/shared/{code}/duplicate`: copy a note someone shared with a link into the
caller's own notes."""

from django.http import Http404

from .. import services
from ..schemas import NoteSchema
from ..services import links
from .common import charge, mapped_errors, note_response
from .router import router


@router.post('/shared/{code}/duplicate', response={201: NoteSchema})
def duplicate_shared_note(request, code: str, expected_cost: int | None = None):
    """Copy the note a share link (`<site>/n/<code>`) points to into your own notes: its title and
    content, no tags, links or history. A normal new note: free while a free slot is open, else the
    creation price; `expected_cost` guards it (a 409 if it differs). A 404 for a code that doesn't
    work (revoked, expired, unknown, or its note is in the Trash, locked or taken down: the same
    answer for all)."""
    link = links.get_available(code)
    if link is None:
        raise Http404('Shared note not found')
    note = link.note
    with mapped_errors():
        services.check_duplicable(note)
        services.check_can_create(request.auth, title=note.title, content=note.content)
        charge(request, 'NOTES_CREATE_USER')
        copy = services.duplicate(note, request.auth, expected_cost=expected_cost, keep_title=True)
    return 201, note_response(request, copy)
