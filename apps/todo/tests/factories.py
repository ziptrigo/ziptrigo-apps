"""Helpers that build users and tasks in the states the tests need."""

import itertools
import uuid
from datetime import UTC, datetime

from apps.accounts.models import User

from ..models import Label, Task
from ..services import get_account

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
