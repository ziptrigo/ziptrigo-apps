"""Helpers that build links in the states the tests need."""

import itertools
import uuid
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User

from ..models import LinkStatus, ShortLink
from ..services import normalize_key

_counter = itertools.count(1)


def make_user(**fields) -> User:
    """A confirmed, active user without a password (so no slow password hashing)."""
    fields.setdefault('email', f'user-{uuid.uuid4().hex[:8]}@example.com')
    fields.setdefault('email_confirmed', True)
    return User.objects.create_user(**fields)


def create_link(owner: User, **fields) -> ShortLink:
    """A saved link straight in the database (no services, no charges, no title fetch)."""
    n = next(_counter)
    code = fields.pop('code', f'tst{n:04d}')
    fields.setdefault('destination', f'https://example.com/page/{n}')
    fields.setdefault('status', LinkStatus.ACTIVE)
    return ShortLink.objects.create(owner=owner, code=code, code_key=normalize_key(code), **fields)


def expired(owner: User, **fields) -> ShortLink:
    return create_link(owner, expires_at=timezone.now() - timedelta(hours=1), **fields)
