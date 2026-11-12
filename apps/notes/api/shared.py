"""`POST /api/notes/shared/{code}/duplicate`: copy a note someone shared with a link into the
caller's own notes."""

from typing import Annotated

from django.http import Http404
from ninja import Query

from .. import services
from ..schemas import NoteSchema
from ..services import LinkUnavailableError, links
from .common import charge, mapped_errors, note_response
from .router import router

NOT_FOUND = 'Shared note not found'


@router.post('/shared/{code}/duplicate', response={201: NoteSchema})
def duplicate_shared_note(
    request,
    code: str,
    expected_cost: Annotated[int | None, Query(ge=0)] = None,  # ty: ignore[call-non-callable]
):
    """Copy the note a share link (`<site>/n/<code>`) points to into your own notes: its title and
    content, no tags, links or history. A normal new note: free while a free slot is open, else the
    creation price; `expected_cost` guards it (a 409 if it differs). A 404 for a code that doesn't
    work (revoked, expired, unknown, or its note is in the Trash, locked or taken down: the same
    answer for all, also when that changes while the copy is being made)."""
    link = links.get_available(code)
    if link is None:
        raise Http404(NOT_FOUND)
    note = link.note
    with mapped_errors():
        services.check_can_create(request.auth, title=note.title, content=note.content)
        services.check_can_afford(request.auth, services.quote_create(request.auth))
        charge(request, 'NOTES_CREATE_USER')
        try:
            # Whether the link still works is decided again inside, on a fresh read.
            copy = services.duplicate_shared(link, request.auth, expected_cost=expected_cost)
        except LinkUnavailableError:
            raise Http404(NOT_FOUND) from None
    return 201, note_response(request, copy)
