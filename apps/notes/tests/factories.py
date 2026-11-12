"""Helpers that build users, notes and tags in the states the tests need."""

import uuid
from datetime import UTC, datetime

from apps.accounts.models import User

from ..models import Note, NoteVersion, Tag
from ..services import get_account


def make_user(**fields) -> User:
    """A confirmed, active user without a password (so no slow password hashing)."""
    fields.setdefault('email', f'user-{uuid.uuid4().hex[:8]}@example.com')
    fields.setdefault('email_confirmed', True)
    return User.objects.create_user(**fields)


def make_note(user: User, **fields) -> Note:
    """A saved note straight in the database (no services, no billing, no version)."""
    get_account(user)
    fields.setdefault('title', f'Note {uuid.uuid4().hex[:6]}')
    fields.setdefault('is_free', True)
    tags = fields.pop('tags', ())
    note = Note.objects.create(user=user, **fields)
    if tags:
        note.tags.set(tags)
    return note


def make_tag(user: User, name: str = 'Work', **fields) -> Tag:
    fields.setdefault('position', 0)
    return Tag.objects.create(user=user, name=name, **fields)


def make_version(note: Note, number: int, **fields) -> NoteVersion:
    fields.setdefault('title', note.title)
    fields.setdefault('content', note.content)
    fields.setdefault('editor_kind', 'owner')
    return NoteVersion.objects.create(note=note, number=number, **fields)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)
