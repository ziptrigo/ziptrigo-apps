"""The note endpoints (`/api/notes/notes`): list and search, create, read, change, tick a
checklist box, tags, archive, Trash, restore, duplicate and manual order. Each calls the service
the website's page uses; changes spend `NOTES_CREATE_USER` (a new note) or `NOTES_EDIT_USER`, a
search spends `NOTES_SEARCH_USER`, and a request the services refuse spends nothing where the check
needs no lock.
"""

from typing import Annotated
from uuid import UUID

from django.db import transaction
from ninja import Query
from ninja.errors import HttpError

from .. import services
from ..schemas import (
    NoteChecklistSchema,
    NoteConflictSchema,
    NoteCountSchema,
    NoteCreateSchema,
    NoteListSchema,
    NoteReorderSchema,
    NoteSchema,
    NoteUpdateSchema,
)
from ..services import (
    DIRECTIONS,
    SORTS,
    Editor,
    FilterParams,
    NoteConflictError,
    filter_notes,
    order_notes,
    trashed_notes,
)
from ..services.filters import NO_TAG, SHARED_FILTERS, UPDATED_RANGES
from ..services.search import MODES, prepare_query, search_condition
from .common import (
    DEFAULT_LIMIT,
    charge,
    choice,
    clamp_page,
    conflict_body,
    mapped_errors,
    note_page,
    note_response,
    owned_note,
    parse_date,
)
from .router import router

#: The `expected_cost` query parameter of the endpoints that can charge: credits, so never negative.
ExpectedCost = Annotated[int | None, Query(ge=0)]  # ty: ignore[call-non-callable]

VIEWS = ('active', 'archived', 'trash', 'all')
#: The sort a view uses when none is asked for (the Trash has its own order, newest deleted first).
DEFAULT_SORT = {'active': 'manual', 'archived': 'updated', 'all': 'updated'}


def _tag_filter(value: str | None) -> list[str]:
    """`tag` as ids and `none`, or a 400."""
    tokens = [part.strip().lower() for part in (value or '').split(',') if part.strip()]
    for token in tokens:
        if token != NO_TAG:
            try:
                UUID(token)
            except ValueError:
                raise HttpError(
                    400, 'tag must be a comma-separated list of tag ids and "none".'
                ) from None
    return tokens


@router.get('/notes', response=NoteListSchema)
def list_notes(
    request,
    view: str = 'active',
    q: str = '',
    mode: str = 'glob',
    tag: str | None = None,
    shared: str | None = None,
    updated: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    sort: str | None = None,
    # `dir` is a Python builtin, so the parameter is `direction`, read from `?dir=`. `ty` cannot see
    # that ninja's `Query` is callable.
    direction: Annotated[str | None, Query(alias='dir')] = None,  # ty: ignore[call-non-callable]
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
):
    """List the caller's notes (no content: an `excerpt` instead; read a note for its text).

    - `view`: `active` (not archived; the default), `archived`, `trash` or `all` (active and
      archived; the Trash is never included).
    - `q`: search the titles and contents, ignoring case, accents and punctuation: `*` and `?` are
      wildcards (`mode=glob`, the default) or a regular expression (`mode=regex`, or `/.../`). 200
      characters at most; a 400 for an empty, invalid or too slow pattern. Edit history is not
      searched.
    - `tag`: comma-separated tag ids, and `none` for notes without a tag; any of them.
    - `shared`: `yes` (the note has a share link that is active and works) or `no`.
    - `updated`: `any`, `today`, `last_7_days`, `last_30_days` or `custom` with `updated_from` /
      `updated_to` (`YYYY-MM-DD`, inclusive, either may be left out; dates alone mean `custom`).
      Relative ranges are the caller's own days.
    - `sort`: `manual` (the order set by hand; the default of `active`), `updated` (the default of
      the other views), `created` or `title`. The Trash lists the most recently deleted first.
    - `dir`: `asc` or `desc`, the direction of the sort: each sort's own default when left out
      (`manual` and `title` ascending, `updated` and `created` newest first), the exact reverse
      for the other one; a 400 for any other value. Without `sort`, `dir` reverses the view's
      default sort; for `view=trash`, whose own order is not a sort, it is a 400
      `dir: Choose a sort to reverse.`
    - `limit` (1-100, default 50) and `offset` page the result; `count` is the total.

    A search, or any of `tag`, `shared` and `updated`, spends `NOTES_SEARCH_USER`; a search text
    that is refused (a 400) spends nothing.

    With `q`, each note also carries `search_field`, `search_snippet`, `match_start` and
    `match_end` (see `NoteListItemSchema`); without it all four are `null`.
    """
    limit, offset = clamp_page(limit, offset)
    view = choice('view', view, VIEWS)
    mode = choice('mode', mode, MODES)
    if sort is not None:
        choice('sort', sort, SORTS)
    if direction is not None:
        choice('dir', direction, DIRECTIONS)
        if sort is None and view == 'trash':
            # The Trash's own order is not a sort: a bare `dir` would silently replace it.
            raise HttpError(400, 'dir: Choose a sort to reverse.')
    if shared is not None:
        choice('shared', shared, SHARED_FILTERS)
    if updated is not None:
        choice('updated', updated, UPDATED_RANGES)
    first = parse_date('updated_from', updated_from)
    last = parse_date('updated_to', updated_to)

    params = FilterParams.from_raw(
        {
            'q': [q],
            'mode': [mode],
            'scope': ['all' if view == 'trash' else view],
            'tags': _tag_filter(tag),
            'shared': [shared or ''],
            'updated': [updated or ''],
            'updated_from': [str(first or '')],
            'updated_to': [str(last or '')],
            'sort': [sort or DEFAULT_SORT.get(view, 'updated')],
            'dir': [direction or ''],
        }
    )
    user = request.auth
    with mapped_errors():
        # The pattern is validated before the budget is spent: one that can't run costs nothing
        # (only a database timeout, which needs the query, still does).
        query = prepare_query(params.q, params.mode) if params.q.strip() else None
        if params.has_criteria:
            charge(request, 'NOTES_SEARCH_USER')
        if view == 'trash':
            queryset = trashed_notes(user)
            if query is not None:
                queryset = queryset.filter(search_condition(query.db_pattern))
            queryset = params.narrow(queryset, user)
        else:
            queryset = filter_notes(user, params, ordered=False)  # validates the search text
        if view == 'trash' and sort is None:
            ordered = queryset.order_by('-deleted_at', 'pk')
        else:
            ordered = order_notes(queryset, params.sort, direction)
        return note_page(request, queryset, ordered, query=query, limit=limit, offset=offset)


# -- Create, read, change ------------------------------------------------------------------------


@router.post('/notes', response={201: NoteSchema})
def create_note(request, payload: NoteCreateSchema):
    """Make a note, first in the manual order. A free slot makes a free note; otherwise it costs
    the creation price (`GET /settings`) and is paid for a year. `expected_cost` guards the cost
    (a 409 if it differs; leave it out to accept any). A 402 when the balance can't pay, which
    spends no rate-limit budget."""
    with mapped_errors():
        # The fields and the cap first: a refused request spends no budget.
        services.check_can_create(
            request.auth, title=payload.title, content=payload.content, tags=payload.tags
        )
        services.check_can_afford(request.auth, services.quote_create(request.auth))
        charge(request, 'NOTES_CREATE_USER')
        note = services.create_note(
            request.auth,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            expected_cost=payload.expected_cost,
            source='api',
        )
    return 201, note_response(request, note)


# Fixed paths are registered before the `{note_id}` ones: a router tries paths in the order added.
@router.post('/notes/reorder', response=NoteCountSchema)
def reorder_notes(request, payload: NoteReorderSchema):
    """Put the notes with these ids in this manual order. Ids that aren't yours, or are in the
    Trash, are ignored; the notes not listed keep their places. `count` is how many were
    positioned."""
    charge(request, 'NOTES_EDIT_USER')
    with mapped_errors():
        return {'count': services.reorder(request.auth, payload.ids)}


@router.get('/notes/{note_id}', response=NoteSchema)
def get_note(request, note_id: UUID):
    """One of the caller's notes, also one in the Trash, with its content and the content
    rendered."""
    note = owned_note(request, note_id)
    return note_response(request, note)


@router.patch('/notes/{note_id}', response={200: NoteSchema, 409: NoteConflictSchema})
def update_note(request, note_id: UUID, payload: NoteUpdateSchema):
    """Change a note's title, content and/or tags; only the fields present are applied, and both
    kinds of change are saved together or not at all. **Send `base_version`** (the `version` you
    read) with a `title` or `content`: a save on an out-of-date one is a 409 whose body carries the
    current version, title and content (nothing is written), so the client can merge and send
    again. A change that leaves the title and content as they are is not a save. A note in the
    Trash is a 400, a locked one a 402 and one taken down a 409; tags alone are changed the same
    way. An explicit `null` clears `content` and `tags`; a `title` cannot be empty (a 400)."""
    note = owned_note(request, note_id)
    changes = payload.model_dump(exclude_unset=True)
    has_text = 'title' in changes or 'content' in changes
    has_tags = 'tags' in changes
    if not has_text and not has_tags:
        return note_response(request, note)  # nothing to change spends nothing
    title = (changes.get('title') or '') if 'title' in changes else None
    content = (changes.get('content') or '') if 'content' in changes else None
    tag_ids = changes.get('tags') or []
    try:
        with mapped_errors(keep=NoteConflictError):
            if has_text and changes.get('base_version') is None:
                raise HttpError(400, 'base_version: Send the version you edited.')
            # Validated first, charged after: a refused request spends no budget.
            services.require_writable(note)
            if title is not None:
                services.clean_title(title)
            if content is not None:
                services.clean_content(content)
            if has_tags:
                services.check_tags(note, tag_ids)
            charge(request, 'NOTES_EDIT_USER')
            with transaction.atomic():
                if has_text:
                    services.save_note(
                        note,
                        editor=Editor.for_user(note, request.auth),
                        title=title,
                        content=content,
                        base_version=changes['base_version'],
                    )
                if has_tags:
                    services.set_tags(note, tag_ids)
    except NoteConflictError as exc:
        return 409, conflict_body(exc)
    return note_response(request, note)


@router.delete('/notes/{note_id}', response={204: None})
def trash_note(request, note_id: UUID):
    """Move a note to the Trash (always allowed, even locked; idempotent). Trashing a free note
    frees its slot. It is deleted for good `trash_days` days later (`GET /settings`)."""
    note = owned_note(request, note_id)
    charge(request, 'NOTES_EDIT_USER')
    with mapped_errors():
        services.trash(note)
    return 204, None


@router.post('/notes/{note_id}/checklist', response=NoteSchema)
def toggle_checklist(request, note_id: UUID, payload: NoteChecklistSchema):
    """Tick or untick a checklist box of the content (`- [ ] item`), counted from 0; the box is
    written back to the text and recorded as a version. No `base_version`: two boxes ticked at once
    both survive. A 400 when there is no such box."""
    note = owned_note(request, note_id)
    with mapped_errors():
        services.check_toggle(note, payload.index)
        charge(request, 'NOTES_EDIT_USER')
        services.toggle_checklist(
            note, payload.index, payload.checked, editor=Editor.for_user(note, request.auth)
        )
    return note_response(request, note)


def _change(request, note_id: UUID, action, *, check=None) -> dict:
    """Run `action(note)` on the caller's note after the lock-free `check(note)` (a refusal spends
    no budget) and the edit budget."""
    note = owned_note(request, note_id)
    with mapped_errors():
        if check is not None:
            check(note)
        charge(request, 'NOTES_EDIT_USER')
        action(note)
    return note_response(request, note)


@router.post('/notes/{note_id}/archive', response=NoteSchema)
def archive_note(request, note_id: UUID):
    """Hide the note from the main list (idempotent; its share links keep working). Refused for a
    note in the Trash (400), locked (402) or taken down (409)."""
    return _change(request, note_id, services.archive, check=services.require_writable)


@router.post('/notes/{note_id}/unarchive', response=NoteSchema)
def unarchive_note(request, note_id: UUID):
    """Move an archived note back to the main list (idempotent; refused as `archive` is)."""
    return _change(request, note_id, services.unarchive, check=services.require_writable)


@router.post('/notes/{note_id}/restore', response=NoteSchema)
def restore_note(request, note_id: UUID, expected_cost: ExpectedCost = None):
    """Take a note out of the Trash (a note that isn't in it is returned as it is). A free note
    with no free slot left becomes a paid note (the creation price, paid for a year); a paid note
    whose renewal fell due while it was in the Trash is renewed (the renewal price) and unlocked;
    else it is free. `restore_cost` on the note says which, beforehand. `expected_cost` guards the
    cost (a 409 if it differs). The cap on notes applies."""
    note = owned_note(request, note_id)
    if not note.is_trashed:
        return note_response(request, note)
    with mapped_errors():
        # The cap and the balance first: a refused restore spends no budget.
        services.check_restorable(note)
        cost = services.quote_restore(note)
        services.check_can_afford(request.auth, cost)
        charge(request, 'NOTES_CREATE_USER' if cost else 'NOTES_EDIT_USER')
        services.restore(note, expected_cost=expected_cost)
    return note_response(request, note)


@router.post('/notes/{note_id}/delete-forever', response={204: None})
def delete_note_forever(request, note_id: UUID):
    """Delete a note that is in the Trash for good, with its history and links. A 404 for a note
    that isn't in the Trash (as the website). Nothing is refunded."""
    note = owned_note(request, note_id, trashed=True)
    charge(request, 'NOTES_EDIT_USER')
    with mapped_errors():
        services.delete_forever(note)
    return 204, None


@router.post('/notes/{note_id}/duplicate', response={201: NoteSchema})
def duplicate_note(request, note_id: UUID, expected_cost: ExpectedCost = None):
    """Copy a note (its content, no tags, links or history) as `<title> (copy)`: a normal new note,
    free while a slot is open, else the creation price (`expected_cost` guards it: a 409 if it
    differs). A note taken down cannot be copied (409)."""
    note = owned_note(request, note_id)
    with mapped_errors():
        services.check_duplicable(note)
        services.check_can_create(request.auth, title=note.title, content=note.content)
        services.check_can_afford(request.auth, services.quote_create(request.auth))
        charge(request, 'NOTES_CREATE_USER')
        copy = services.duplicate(note, request.auth, expected_cost=expected_cost)
    return 201, note_response(request, copy)
