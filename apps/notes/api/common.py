"""What the Notes API endpoints share: ownership lookups, the mapping from service errors to HTTP
statuses, rate limits, and how a note is turned into its JSON shape.

Status codes (documented once, here):

- **400** a `ValidationError` from a service (`field: message`, or the message alone for one that
  belongs to no field: a note in the Trash, the cap on notes, ...), or one of the few query
  parameters read by hand (`view`, `mode`, `sort`, `dir`, `tag`, `shared`, `updated`,
  `updated_from`/`updated_to`). A reference in a body to something that isn't yours (a tag) is a
  400 too, with the service's own message (`tags: Choose your own tags.`): the thing being changed
  exists, it is the value that is refused. Changing the title or content without sending the
  `base_version` is a 400.
- **402** `InsufficientCreditsError` (the balance can't cover creating, duplicating or restoring a
  note) and **any change refused because the note is locked** (its yearly renewal could not be
  paid: top up and it unlocks). Reading, copying, trashing and deleting a locked note still work.
- **404** a note, version, share link, tag or saved filter that isn't the caller's (never 403, so
  an id can't be probed), a shared note whose code doesn't work (for every reason alike), and a
  request the web answers with a 404 too (deleting forever a note that isn't in the Trash).
- **409** `NoteConflictError` (a save on an out-of-date `base_version`: `{detail, current_version,
  current_title, current_content}`, nothing was written), `CostChangedError` (the cost is not what
  the caller's `expected_cost` said; nothing was charged or changed, and the detail names the real
  total) and **any change, copy or share refused because staff took the note down** (final).
- **422** django-ninja's own validation, before an endpoint runs, with a *list-valued* `detail`: a
  malformed id in the path, a mistyped query parameter or body field, an unknown key in a body
  (`extra='forbid'`).
- **429** the site-wide rate limiter (`ratelimit.enforce`), with `Retry-After`. The rules and so
  the budgets are the web's (`NOTES_CREATE_USER`, `NOTES_EDIT_USER`, `NOTES_SEARCH_USER`).

**Costs.** The first `free_notes` notes are free, the rest cost credits (see `GET /settings`). The
web asks the person to confirm the cost first; the API has no one to ask, so it accepts whatever a
request costs *unless the client says what it expects*: `expected_cost` (the body of `POST /notes`,
a query parameter of the restore and duplicate endpoints) makes the call refuse with a 409 when the
real cost, worked out inside the charging transaction, differs.
"""

from collections.abc import Generator, Iterable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit

from ..models import Note, NotesSettings
from ..services import (
    CostChangedError,
    NoteConflictError,
    free_slot_open,
    links,
    restore_cost_for,
)
from ..services.errors import NoteLockedError, NoteTakenDownError
from ..services.lists import excerpt
from ..services.search import PreparedQuery, attach_matches, search_guard
from ..templatetags.notes_ui import render_text

#: Page size bounds of the list endpoints (clamped, not rejected).
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def validation_detail(exc: ValidationError) -> str:
    """One readable line for a `ValidationError`: the first message, or `field: message` pairs when
    the service keyed its errors by field (a message that belongs to no field is plain)."""
    if hasattr(exc, 'error_dict'):
        return ' '.join(
            message if field_name == '__all__' else f'{field_name}: {message}'
            for field_name, messages in exc.message_dict.items()
            for message in messages
        )
    return exc.messages[0]


def _cost_text(total: int) -> str:
    price = 'free' if total == 0 else f'{total} credit{"" if total == 1 else "s"}'
    return (
        f'The cost changed: it is now {price}. Send expected_cost={total} to confirm it, '
        'or leave expected_cost out to accept any cost.'
    )


@contextmanager
def mapped_errors(*, keep: type[ValidationError] | None = None) -> Generator[None]:
    """Turn the services' exceptions into the API's statuses (see the module docs). The specific
    `ValidationError` subclasses come first: each has a status of its own. `keep` is a subclass the
    caller answers itself (the 409 of a save with a stale `base_version`): it passes through."""
    try:
        yield
    except InsufficientCreditsError as exc:
        raise HttpError(402, str(exc) or 'Insufficient credits') from exc
    except NoteLockedError as exc:
        raise HttpError(402, validation_detail(exc)) from exc
    except NoteTakenDownError as exc:
        raise HttpError(409, validation_detail(exc)) from exc
    except CostChangedError as exc:
        raise HttpError(409, _cost_text(exc.quote)) from exc
    except ValidationError as exc:
        if keep is not None and isinstance(exc, keep):
            raise
        raise HttpError(400, validation_detail(exc)) from exc


def conflict_body(exc: NoteConflictError) -> dict[str, Any]:
    """The body of the 409 for a stale `base_version` (`NoteConflictSchema`)."""
    return {
        'detail': exc.messages[0],
        'current_version': exc.current_version,
        'current_title': exc.current_title,
        'current_content': exc.current_content,
    }


def clamp_page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_LIMIT)), max(0, offset)


def charge(request, rule: str) -> None:
    """Spend one hit of the user's `rule` budget: a 429 when it is over."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, rule))


def choice(name: str, value: str, allowed: Iterable[str]) -> str:
    """`value` if it is one of `allowed`, else a 400 naming the choices."""
    options = tuple(allowed)
    if value not in options:
        raise HttpError(400, f'{name} must be one of: {", ".join(options)}.')
    return value


def parse_date(name: str, value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HttpError(400, f'{name} must be a date like 2026-10-09.') from None


# -- Lookups -------------------------------------------------------------------------------------


def owned_note(request, note_id: UUID, *, trashed: bool | None = None) -> Note:
    """The caller's note `note_id`, or a 404. `trashed=None` (the default) accepts a note in the
    Trash too -- the service the endpoint calls says what is allowed on it --, `False` refuses
    one, `True` accepts only one."""
    note = get_object_or_404(Note.objects.select_related('user'), user=request.auth, pk=note_id)
    if trashed is not None and note.is_trashed != trashed:
        raise Http404('Note not found')
    return note


# -- Serialising a note --------------------------------------------------------------------------


@dataclass(slots=True)
class NoteContext:
    """What turning notes into JSON needs, made once per request: the owner and, for notes in the
    Trash, what a restore costs (the settings and whether a free slot is open, read once)."""

    user: User
    _restore: tuple[NotesSettings, bool] | None = field(default=None, repr=False)

    def restore_cost(self, note: Note) -> int | None:
        """What taking `note` out of the Trash would cost now; `None` for a note that isn't in it."""
        if not note.is_trashed:
            return None
        if self._restore is None:
            config = NotesSettings.load()
            self._restore = (config, free_slot_open(self.user, config))
        config, slot_open = self._restore
        note.user = self.user  # already loaded: no query per note
        return restore_cost_for(note, config, slot_open)


def context_for(request) -> NoteContext:
    context = getattr(request, '_notes_context', None)
    if context is None:
        context = NoteContext(user=request.auth)
        request._notes_context = context
    return context


def decorated(queryset: QuerySet) -> QuerySet:
    """What a note's JSON needs, loaded without a query per note: the tags (one query for the whole
    page) and the active-link flag (a subquery column). The search copy of the content is never
    sent, so it is left in the database."""
    return links.with_active_link_flag(queryset).prefetch_related('tags').defer('content_text')


def load_note(request, note: Note) -> Note:
    """`note` again, loaded the way a list loads its rows (fresh after an action changed it)."""
    return decorated(Note.objects.filter(user=request.auth, pk=note.pk)).get()


def _common(note: Note, context: NoteContext) -> dict[str, Any]:
    has_link = getattr(note, 'has_active_link', None)
    return {
        'id': note.pk,
        'title': note.title,
        'version': note.version,
        'tags': [{'id': tag.pk, 'name': tag.name, 'color': tag.color} for tag in note.tags.all()],
        'archived_at': note.archived_at,
        'deleted_at': note.deleted_at,
        'is_free': note.is_free,
        'paid_through': note.paid_through,
        'locked': note.is_locked,
        'taken_down': note.is_taken_down,
        'takedown_reason': note.takedown_reason if note.is_taken_down else None,
        'has_active_link': links.has_active_link(note) if has_link is None else bool(has_link),
        'charged_credits': note.charged_credits,
        'restore_cost': context.restore_cost(note),
        'created_at': note.created_at,
        'updated_at': note.updated_at,
    }


def note_data(note: Note, context: NoteContext) -> dict[str, Any]:
    """A note (loaded by `decorated`) as the `NoteSchema` dict."""
    return {**_common(note, context), 'content': note.content, 'content_html': _html(note)}


def _html(note: Note) -> str:
    return str(render_text(note.content)) if note.content else ''


def list_item_data(note: Note, context: NoteContext, *, searched: bool = False) -> dict[str, Any]:
    """A note as the `NoteListItemSchema` dict: no content, an excerpt, and (for a searched list)
    where the search matched."""
    data = {**_common(note, context), 'excerpt': excerpt(note)}
    if searched:
        data.update(search_fields(note))
    return data


def search_fields(note: Note) -> dict[str, Any]:
    """The snippet fields of a note of a searched list, after `services.search.attach_matches` has
    read it: where it matched and the snippet as plain text with the match's offsets; `''` and
    `None`s when there is no snippet."""
    parts = getattr(note, 'search_parts', None)
    found = getattr(note, 'search_field', '')
    if parts is None or not found:
        return {'search_field': '', 'search_snippet': None, 'match_start': None, 'match_end': None}
    before, hit, after = parts
    return {
        'search_field': found,
        'search_snippet': before + hit + after,
        'match_start': len(before),
        'match_end': len(before) + len(hit),
    }


def note_response(request, note: Note) -> dict[str, Any]:
    """`note`, reloaded, as its JSON dict (what every endpoint that changes a note answers)."""
    return note_data(load_note(request, note), context_for(request))


def note_page(
    request,
    queryset: QuerySet,
    ordered: QuerySet,
    *,
    query: PreparedQuery | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """One page of notes as `NoteListSchema`: `queryset` is what is selected (counted), `ordered`
    the same in its order. With `query` the database work runs under the search's statement timeout
    (`search_guard`; a pattern it refuses or a timeout is a 400, call inside `mapped_errors`) and
    each note carries where it matched."""
    with search_guard() if query is not None else nullcontext():
        count = queryset.count()
        rows = list(decorated(ordered)[offset : offset + limit])
    if query is not None:
        attach_matches(rows, query)
    context = context_for(request)
    return {
        'count': count,
        'limit': limit,
        'offset': offset,
        'results': [list_item_data(note, context, searched=query is not None) for note in rows],
    }
