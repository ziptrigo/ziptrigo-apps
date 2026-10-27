"""In-app notifications (issue #169): the one place a product creates them and the only place the
views read and change them.

A product calls `notify(user, source='file_transfer', title=..., body=..., url=reverse(...))`;
`core` never knows a product's URL names, so the caller passes a relative path it built itself.
Everything is plain text (the templates escape it), and a `url` must be a same-site path, so
neither a caller's mistake nor a bad row can become an open redirect.

**Read, opened, dismissed.** A notification is *new* while `read_at` is null. The bell counts
what is new and not dismissed (`unread_count`). The dropdown (`dropdown_notifications`) lists the
latest `DROPDOWN_LIMIT` that are neither dismissed nor *opened*, and the view marks the ones it
lists read as it renders them, so the badge clears when the user looks. Clicking one
(`mark_opened`) sets `opened_at` too, which takes it out of the dropdown; dismissing it
(`dismiss`) hides it everywhere. "Mark all as read" (`mark_all_read`) only clears the new state: the
notifications stay listed until opened or dismissed. The full page (`page_notifications`) lists
everything not dismissed.

**Scoping.** Every function takes the requesting user and filters on it; a notification that
belongs to someone else looks exactly like one that doesn't exist (`Notification.DoesNotExist`,
which the views turn into a 404, never a 403).
"""

import re
from collections.abc import Iterable
from uuid import UUID

from django.db import transaction
from django.db.models import Model, QuerySet
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import Notification
from ..models.notification import (
    BODY_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    URL_MAX_LENGTH,
)

#: How many notifications the bell's dropdown lists.
DROPDOWN_LIMIT = 10

_CONTROL_OR_SPACE = re.compile(r'[\x00-\x20\x7f]')


def is_safe_url(url: str) -> bool:
    """Whether `url` is a relative, same-site path that is safe to redirect to: it starts with a
    single `/` (so not `//host`), has no scheme, backslash, space or control character, and passes
    Django's own host check with no host allowed. The empty string is not a URL."""
    if not url or len(url) > URL_MAX_LENGTH:
        return False
    if not url.startswith('/') or url.startswith('//') or '\\' in url:
        return False
    if _CONTROL_OR_SPACE.search(url):
        return False
    return url_has_allowed_host_and_scheme(url, allowed_hosts=None)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + '…'


def notify(
    user: Model | UUID | str,
    *,
    source: str,
    title: str,
    body: str = '',
    url: str = '',
) -> Notification:
    """Create a notification for `user` (an instance, or just its id).

    `source` is the calling app's label. `title` is one line (whitespace collapsed) and `body` is
    optional plain text; both are cut to the model's limits with an ellipsis rather than refused,
    because the text often carries something a user typed (a file name) and the event being
    reported must not fail over it. `url` is optional, relative and built by the caller with
    `reverse(...)`; anything else (an absolute URL, `//host`, a backslash...) is a programming
    error and raises `ValueError`, as does a missing `source` or `title`.

    The row is written inside a savepoint in the caller's transaction: it commits or rolls back
    with the caller's work, and a failed insert cannot poison the caller's transaction.
    """
    source = source.strip()
    if not source or len(source) > SOURCE_MAX_LENGTH:
        raise ValueError(f'source must be 1-{SOURCE_MAX_LENGTH} characters.')
    title = ' '.join(title.split())
    if not title:
        raise ValueError('title is required.')
    body = _truncate(body.replace('\r\n', '\n').strip(), BODY_MAX_LENGTH)
    if url and not is_safe_url(url):
        raise ValueError('url must be a relative path such as reverse() returns.')

    user_pk = user.pk if isinstance(user, Model) else user
    with transaction.atomic():
        return Notification.objects.create(
            user_id=user_pk,
            source=source,
            title=_truncate(title, TITLE_MAX_LENGTH),
            body=body,
            url=url,
        )


def _of(user: Model) -> QuerySet:
    return Notification.objects.filter(user_id=user.pk)


def get_notification(user: Model, notification_id: UUID | str) -> Notification:
    """The user's notification, or `Notification.DoesNotExist` (also for someone else's)."""
    return _of(user).get(pk=notification_id)


def unread_count(user: Model) -> int:
    """What the bell shows: new (never read) notifications that were not dismissed."""
    return _of(user).filter(read_at__isnull=True, dismissed_at__isnull=True).count()


def dropdown_notifications(user: Model, limit: int = DROPDOWN_LIMIT) -> list[Notification]:
    """The latest `limit` notifications for the dropdown: not dismissed, not opened."""
    return list(
        _of(user)
        .filter(dismissed_at__isnull=True, opened_at__isnull=True)
        .order_by('-created_at', '-pk')[:limit]
    )


def page_notifications(user: Model) -> QuerySet:
    """Every notification of the user that was not dismissed, newest first (for pagination)."""
    return _of(user).filter(dismissed_at__isnull=True).order_by('-created_at', '-pk')


def mark_read(user: Model, ids: Iterable[UUID | str]) -> int:
    """Mark the user's notifications with these ids read; ids that are not theirs are ignored.
    Returns how many changed."""
    ids = list(ids)
    if not ids:
        return 0
    return _of(user).filter(pk__in=ids, read_at__isnull=True).update(read_at=timezone.now())


def mark_all_read(user: Model) -> int:
    """Mark everything new read, including what the dropdown's limit left out. Returns how many."""
    return _of(user).filter(read_at__isnull=True).update(read_at=timezone.now())


def mark_opened(user: Model, notification_id: UUID | str) -> Notification:
    """The user followed the notification: set `opened_at` (and `read_at`, if still new) and
    return it, for the view to redirect to its `url`. `DoesNotExist` if it is not theirs."""
    notification = get_notification(user, notification_id)
    now = timezone.now()
    _of(user).filter(pk=notification.pk).update(
        opened_at=Coalesce('opened_at', now), read_at=Coalesce('read_at', now)
    )
    notification.opened_at = notification.opened_at or now
    notification.read_at = notification.read_at or now
    return notification


def dismiss(user: Model, notification_id: UUID | str) -> None:
    """Hide the notification (idempotent). It counts as read, so it leaves the badge too.
    `DoesNotExist` if it is not the user's."""
    notification = get_notification(user, notification_id)
    now = timezone.now()
    _of(user).filter(pk=notification.pk, dismissed_at__isnull=True).update(
        dismissed_at=now, read_at=Coalesce('read_at', now)
    )


def delete_for_url(user: Model | UUID | str, url: str, *, source: str | None = None) -> int:
    """Delete the user's notifications that point at `url` (and, when given, were created by
    `source`), for a product whose target no longer needs attention -- the paused transfer was
    resumed, the task was done. Returns how many were deleted."""
    user_pk = user.pk if isinstance(user, Model) else user
    queryset = Notification.objects.filter(user_id=user_pk, url=url)
    if source is not None:
        queryset = queryset.filter(source=source)
    return queryset.delete()[0]
