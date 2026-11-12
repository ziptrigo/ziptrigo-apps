"""Schema for `GET /api/notes/settings`."""

from ninja import Schema
from pydantic import Field


class NoteSettingsSchema(Schema):
    """What notes cost and how many are free, for the caller.

    `free_notes_used` of `free_notes` free slots are taken (a note in the Trash frees its slot).
    `next_note_cost` is what the next note, duplicate or shared copy would cost right now: 0 while a
    free slot is open, else `creation_price`; send it as `expected_cost`. A paid note is renewed
    every `renewal_days` days for `renewal_price` credits; one that cannot be paid for is locked.
    Notes stay in the Trash for `trash_days` days.
    """

    free_notes: int
    free_notes_used: int
    creation_price: int
    renewal_price: int
    renewal_days: int
    next_note_cost: int = Field(description='0 while a free slot is open.')
    trash_days: int
