"""Schemas for the note endpoints (`/api/notes/notes`)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from ..limits import CONTENT_MAX, MAX_REORDER_IDS, TITLE_MAX
from .tag import NoteTagRefSchema

#: A title or content over its limit is refused by the services with their own message (a 400);
#: these ceilings only stop an absurd body before anything runs (a 422).
Title = Annotated[str, Field(max_length=TITLE_MAX * 4)]
Content = Annotated[str, Field(max_length=CONTENT_MAX * 4)]


class NoteConflictSchema(Schema):
    """A 409. Always a `detail`; a save made on an out-of-date `base_version` also carries what
    is stored now (`current_version`, `current_title`, `current_content`), so the client can merge
    by hand and send again with `base_version` set to `current_version`."""

    detail: str
    current_version: int | None = None
    current_title: str | None = None
    current_content: str | None = None


class NoteListItemSchema(Schema):
    """One note as a list shows it: everything of `NoteSchema` except the content, which is
    replaced by `excerpt` (the first 140 characters of the text without Markdown marks, on one
    line). Read the note itself (`GET /notes/{id}`) for its `content` and `content_html`.

    With `q`, `search_field` (`title` or `content`), `search_snippet` (plain text around the first
    match, in the normalised text the search runs over: no accents or formatting) and the offsets
    of the match in it (`match_start`, `match_end`) say where it matched; they are `null` without
    `q`, and for a row the snippet pass did not reach (the rows are listed all the same).
    """

    id: UUID
    title: str
    excerpt: str
    version: int
    tags: list[NoteTagRefSchema]
    archived_at: datetime | None
    deleted_at: datetime | None
    is_free: bool = Field(description='Created in a free slot: it never renews.')
    paid_through: datetime | None = Field(
        description='The date a paid note must be renewed by; `null` for a free note.'
    )
    locked: bool = Field(
        description='Its yearly renewal could not be paid: read-only until it is (see 402).'
    )
    taken_down: bool = Field(description='Taken down by staff: final, read-only.')
    takedown_reason: str | None = Field(
        description='Why, when `taken_down` (never who reported it); `null` otherwise.'
    )
    has_active_link: bool = Field(
        description='It has a share link that is not revoked and not expired.'
    )
    charged_credits: int = Field(description='What creating the note cost.')
    restore_cost: int | None = Field(
        description='What taking the note out of the Trash would cost now, in credits (to send as '
        '`expected_cost`); `null` unless it is in the Trash.'
    )
    created_at: datetime
    updated_at: datetime
    search_field: str | None = None
    search_snippet: str | None = None
    match_start: int | None = None
    match_end: int | None = None


class NoteSchema(Schema):
    """One note, as every note endpoint returns it.

    `version` counts the saves of the title or content: send the one you read as `base_version`
    when you change them (`PATCH /notes/{id}`), and a save made on a stale one is a 409.
    `content_html` is the Markdown rendered and sanitised (tables, coloured code blocks and
    checklists included). `updated_at` moves only on a save of the title, content or a checklist
    box, not on archiving, tagging or ordering.

    A note is read-only while it is `locked`, `taken_down` or in the Trash (`deleted_at`).
    """

    id: UUID
    title: str
    content: str
    content_html: str
    version: int
    tags: list[NoteTagRefSchema]
    archived_at: datetime | None
    deleted_at: datetime | None
    is_free: bool = Field(description='Created in a free slot: it never renews.')
    paid_through: datetime | None = Field(
        description='The date a paid note must be renewed by; `null` for a free note.'
    )
    locked: bool = Field(
        description='Its yearly renewal could not be paid: read-only until it is (see 402).'
    )
    taken_down: bool = Field(description='Taken down by staff: final, read-only.')
    takedown_reason: str | None = Field(
        description='Why, when `taken_down` (never who reported it); `null` otherwise.'
    )
    has_active_link: bool = Field(
        description='It has a share link that is not revoked and not expired.'
    )
    charged_credits: int = Field(description='What creating the note cost.')
    restore_cost: int | None = Field(
        description='What taking the note out of the Trash would cost now, in credits (to send as '
        '`expected_cost`); `null` unless it is in the Trash.'
    )
    created_at: datetime
    updated_at: datetime


class NoteListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[NoteListItemSchema]


class NoteCreateSchema(Schema):
    """Body for `POST /notes`. `tags` are the ids of your own tags. `expected_cost` is the number
    of credits you expect it to cost (see `GET /settings`, `next_note_cost`): when the real cost,
    worked out when the note is made, differs, nothing is charged or created and the answer is a
    409. Leave it out to accept whatever it costs."""

    model_config = ConfigDict(extra='forbid')

    title: Title
    content: Content = ''
    tags: list[UUID] = []
    expected_cost: int | None = Field(default=None, ge=0)


class NoteUpdateSchema(Schema):
    """Body for `PATCH /notes/{id}`: only the fields present are applied, and both kinds of change
    are saved together or not at all. `base_version` (the `version` you read) is **required** when
    `title` or `content` is sent: a save on an out-of-date one is a 409 carrying what is stored.
    An explicit `null` clears `content` (to empty) and `tags` (to none); a `title` cannot be
    empty, so `null` there is a 400. Changing nothing, or only `tags`, needs no `base_version`. A
    key that is not one of these is a 422."""

    model_config = ConfigDict(extra='forbid')

    title: Title | None = None
    content: Content | None = None
    tags: list[UUID] | None = None
    base_version: int | None = Field(default=None, ge=1)


class NoteChecklistSchema(Schema):
    """Body for `POST /notes/{id}/checklist`: tick or untick the `index`th checklist box of the
    content (`- [ ] item`, counted from 0 in document order). No `base_version`: two boxes ticked
    at once both survive."""

    model_config = ConfigDict(extra='forbid')

    index: int = Field(ge=0)
    checked: bool


class NoteReorderSchema(Schema):
    """Body for `POST /notes/reorder`: note ids in the new manual order (ids that aren't yours or
    are in the Trash are ignored; the notes not listed keep their places; at most 500, a longer
    list is a 422)."""

    model_config = ConfigDict(extra='forbid')

    ids: Annotated[list[UUID], Field(max_length=MAX_REORDER_IDS)]


class NoteCountSchema(Schema):
    count: int
