"""Filtering a user's links: the dashboard's tabs and search, and the API's list filters.

One definition serves both (`views.dashboard` and `api.links`), so "the Expired tab" and
`?status=expired` mean the same set of rows. The states match `links.display_state` (an expired or
click-capped link is still `active` in the database, so these are not plain status filters).
"""

import re
from datetime import datetime

from django.db.models import F, Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from apps.accounts.models import User

from ..models import LinkStatus, ShortLink

#: The states a list can be filtered by, besides "all", in the dashboard's tab order.
STATES = ('active', 'disabled', 'expired', 'capped', 'taken_down')
SORTS = ('-created_at', 'created_at', '-click_count', 'click_count')
DEFAULT_SORT = '-created_at'

#: What can be part of a tag (`links.normalize_tags`), case-insensitively for a search.
_TAG_QUERY = re.compile(r'[a-zA-Z0-9_-]+')


def state_filter(state: str, now: datetime) -> Q:
    """The rows in a display state (`STATES`), matching `links.display_state` (so the tabs and the
    pills agree): a link that is `active` in the database may still be expired or past its cap."""
    if state in ('disabled', 'taken_down'):
        return Q(status=state)
    live = Q(status=LinkStatus.ACTIVE, held_for_review_at__isnull=True)
    not_expired = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    capped = Q(max_clicks__isnull=False, click_count__gte=F('max_clicks'))
    match state:
        case 'expired':
            return live & Q(expires_at__lte=now)
        case 'capped':
            return live & not_expired & capped
        case 'active':
            return live & not_expired & ~capped
    raise ValueError(f'Unknown state: {state}')


def visible_links(owner: User) -> QuerySet:
    """The owner's links that haven't been deleted (a deleted link is gone for its owner)."""
    return ShortLink.objects.filter(owner=owner).exclude(status=LinkStatus.DELETED)


def filter_links(
    owner: User,
    *,
    tab: str = 'all',
    q: str = '',
    tag: str = '',
    sort: str = DEFAULT_SORT,
    now: datetime | None = None,
) -> QuerySet:
    """The owner's visible links in a state (`tab`: `all` or one of `STATES`), matching a tag and
    a search over code, destination, title and tags, ordered by `sort` (then newest first)."""
    now = now or timezone.now()
    queryset = visible_links(owner).annotate(tags_text=Cast('tags', TextField()))
    if tab != 'all':
        queryset = queryset.filter(state_filter(tab, now))
    if tag:
        # Tags are ASCII `[a-z0-9_-]` (`links.normalize_tags`), so the JSON text of the list
        # holds each one as `"tag"` on both PostgreSQL and SQLite (a `contains` lookup on the
        # JSON field itself is not available on SQLite).
        queryset = queryset.filter(tags_text__contains=f'"{tag}"')
    if q:
        match = Q(code_key__icontains=q) | Q(destination__icontains=q) | Q(title__icontains=q)
        # The tags are searched as the JSON text of the list, which also holds `["`, `",` and `]`:
        # only a query that could be (part of) a tag may look there, or `"` would match every
        # tagged link. Tags are stored lower-cased.
        if _TAG_QUERY.fullmatch(q):
            match |= Q(tags_text__icontains=q.lower())
        queryset = queryset.filter(match)
    return queryset.order_by(sort, '-created_at')
