"""Pydantic/ninja schemas for the Notes JWT API (`/api/notes/`)."""

from .filter import (
    NoteFilterCreateSchema,
    NoteFilterOrderSchema,
    NoteFilterParamsSchema,
    NoteFilterSchema,
    NoteFilterUpdateSchema,
)
from .link import NoteLinkCreateSchema, NoteLinkSchema, NoteLinkUpdateSchema
from .note import (
    NoteChecklistSchema,
    NoteConflictSchema,
    NoteCountSchema,
    NoteCreateSchema,
    NoteListItemSchema,
    NoteListSchema,
    NoteReorderSchema,
    NoteSchema,
    NoteUpdateSchema,
)
from .settings import NoteSettingsSchema
from .tag import (
    NoteTagCreateSchema,
    NoteTagOrderSchema,
    NoteTagRefSchema,
    NoteTagSchema,
    NoteTagUpdateSchema,
)
from .version import NoteVersionListItemSchema, NoteVersionSchema

__all__ = [
    'NoteChecklistSchema',
    'NoteConflictSchema',
    'NoteCountSchema',
    'NoteFilterCreateSchema',
    'NoteFilterOrderSchema',
    'NoteFilterParamsSchema',
    'NoteFilterSchema',
    'NoteFilterUpdateSchema',
    'NoteLinkCreateSchema',
    'NoteLinkSchema',
    'NoteLinkUpdateSchema',
    'NoteCreateSchema',
    'NoteListItemSchema',
    'NoteListSchema',
    'NoteSchema',
    'NoteUpdateSchema',
    'NoteReorderSchema',
    'NoteSettingsSchema',
    'NoteTagCreateSchema',
    'NoteTagOrderSchema',
    'NoteTagRefSchema',
    'NoteTagSchema',
    'NoteTagUpdateSchema',
    'NoteVersionListItemSchema',
    'NoteVersionSchema',
]
