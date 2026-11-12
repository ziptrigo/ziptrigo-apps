"""The share-link endpoints (`/api/notes/notes/{id}/links`): the links of one of the caller's notes.
Each calls the service the website's Share panel uses and spends `NOTES_EDIT_USER`, after the
checks that need no lock. Creating and changing a link are refused on a note that is read-only
(400 in the Trash, 402 locked, 409 taken down); **revoking is always allowed**.
"""

from datetime import date, datetime
from uuid import UUID

from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from ..models import Note, ShareLink
from ..schemas import NoteLinkCreateSchema, NoteLinkSchema, NoteLinkUpdateSchema
from ..services import links as link_service
from .common import charge, mapped_errors, owned_note
from .router import router


def _data(link: ShareLink) -> dict:
    return {
        'id': link.pk,
        'url': link_service.link_url(link),
        'mode': link.mode,
        'label': link.label,
        'expires_at': link.expires_at,
        'revoked_at': link.revoked_at,
        'active': link.is_active(),
        'created_at': link.created_at,
        'last_edited_at': link.last_edited_at,
    }


def _owned_link(note: Note, link_id: UUID) -> ShareLink:
    link = get_object_or_404(ShareLink, pk=link_id, note=note)
    link.note = note  # the note (and its owner) are already loaded
    return link


@router.get('/notes/{note_id}/links', response=list[NoteLinkSchema])
def list_links(request, note_id: UUID):
    """The note's share links: the active ones first (newest first), then the revoked and expired
    ones."""
    note = owned_note(request, note_id)
    return [_data(link) for link in link_service.list_links(note)]


@router.post('/notes/{note_id}/links', response={201: NoteLinkSchema})
def create_link(request, note_id: UUID, payload: NoteLinkCreateSchema):
    """Make a share link: `read` (the note, read-only) or `edit` (anyone with the link can also
    change its content; they can never change the title or see who you are). `expires_at` is a date
    (the end of that day in your time zone) or a date-time, not in the past. At most 10 links per
    note are active at once (400)."""
    note = owned_note(request, note_id)
    with mapped_errors():
        link_service.check_create(
            note, mode=payload.mode, label=payload.label, expires_on=payload.expires_at
        )
        charge(request, 'NOTES_EDIT_USER')
        link = link_service.create_link(
            note, mode=payload.mode, label=payload.label, expires_on=payload.expires_at
        )
    return 201, _data(link)


@router.patch('/notes/{note_id}/links/{link_id}', response=NoteLinkSchema)
def update_link(request, note_id: UUID, link_id: UUID, payload: NoteLinkUpdateSchema):
    """Change a link's `mode`, `label` or `expires_at`; only the fields present are applied, and an
    explicit `null` clears `label` and `expires_at` (`mode` cannot be null: a 400). A revoked link
    cannot be changed (400); the code never changes. A body with nothing in it changes nothing and
    spends no budget."""
    note = owned_note(request, note_id)
    link = _owned_link(note, link_id)
    fields = payload.model_dump(exclude_unset=True)
    changes: dict = {}
    if 'mode' in fields:
        if fields['mode'] is None:
            raise HttpError(400, 'mode: Cannot be null.')
        changes['mode'] = fields['mode']
    if 'label' in fields:
        changes['label'] = fields['label'] or ''
    if 'expires_at' in fields:
        expires_at: date | datetime | None = fields['expires_at']
        if expires_at is None:
            changes['clear_expiry'] = True
        else:
            changes['expires_on'] = expires_at
    if changes:
        with mapped_errors():
            link_service.check_update(link, **changes)
            charge(request, 'NOTES_EDIT_USER')
            link = link_service.update_link(link, **changes)
    return _data(link)


@router.delete('/notes/{note_id}/links/{link_id}', response={204: None})
def revoke_link(request, note_id: UUID, link_id: UUID):
    """Revoke a link: it stops working for good (idempotent, and allowed whatever the note's state:
    a leaked link can always be killed). A revoked link is never active again; make another."""
    note = owned_note(request, note_id)
    link = _owned_link(note, link_id)
    charge(request, 'NOTES_EDIT_USER')
    with mapped_errors():
        link_service.revoke_link(link)
    return 204, None
