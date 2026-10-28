"""Helpers that build users and tasks in the states the tests need."""

import base64
import hashlib
import itertools
import uuid
from datetime import UTC, datetime

from apps.accounts.models import User

from ..models import Attachment, Label, Task
from ..services import get_account
from ..services.accounts import add_stored_bytes
from ..services.storage import attachment_key

_numbers = itertools.count(5000)


def make_user(**fields) -> User:
    """A confirmed, active user without a password (so no slow password hashing)."""
    fields.setdefault('email', f'user-{uuid.uuid4().hex[:8]}@example.com')
    fields.setdefault('email_confirmed', True)
    return User.objects.create_user(**fields)


def make_task(user: User, **fields) -> Task:
    """A saved task straight in the database (no services, no numbering, no charge)."""
    get_account(user)
    fields.setdefault('number', next(_numbers))
    fields.setdefault('title', f'Task {fields["number"]}')
    labels = fields.pop('labels', ())
    task = Task.objects.create(user=user, **fields)
    if labels:
        task.labels.set(labels)
    return task


def make_label(user: User, name: str = 'Home', **fields) -> Label:
    fields.setdefault('position', 0)
    return Label.objects.create(user=user, name=name, **fields)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


#: The first bytes of a PNG, so the upload sniffs as an inline-safe image.
PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def make_attachment(task: Task, storage, *, data: bytes = PNG_BYTES, **fields) -> Attachment:
    """An uploaded attachment on `task` with `data` in the fake bucket, counted in the account's
    `stored_bytes` (what `complete_upload` leaves behind). `uploaded=False` makes a pending one
    instead: a row only, nothing stored or counted."""
    fields.setdefault('filename', 'photo.png')
    fields.setdefault('size_bytes', len(data))
    fields.setdefault('sha256', base64.b64encode(hashlib.sha256(data).digest()).decode())
    fields.setdefault('uploaded', True)
    fields.setdefault('inline_type', 'image/png' if data.startswith(b'\x89PNG') else '')
    attachment = Attachment.objects.create(task=task, user=task.user, **fields)
    if attachment.uploaded:
        storage.seed(attachment_key(task.user_id, attachment.pk), data)
        add_stored_bytes(task.user_id, attachment.size_bytes)
    return attachment
