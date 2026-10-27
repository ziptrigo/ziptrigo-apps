"""How many active links a user may have: the free allowance and the hard cap, both from
`ShortUrlSettings`. "Active" is a link whose status is `active` (an expired or click-capped link
still is: its owner can disable or delete it to make room).

The free allowance counts the active links that have **not** paid their way in. A link that paid
the extra link price keeps its place for good: disabling and re-enabling it is free, and it does
not use up one of the free slots, so a user never pays twice for the same room.
"""

from apps.accounts.models import User

from ..models import LinkStatus, ShortLink, ShortUrlSettings
from .errors import LimitReachedError


def active_link_count(owner: User) -> int:
    return ShortLink.objects.filter(owner=owner, status=LinkStatus.ACTIVE).count()


def unpaid_active_link_count(owner: User) -> int:
    """Active links that are not paid-for extras: what the free allowance is measured against."""
    return ShortLink.objects.filter(
        owner=owner, status=LinkStatus.ACTIVE, extra_link_paid_at__isnull=True
    ).count()


def is_extra_link(owner: User, *, settings: ShortUrlSettings | None = None) -> bool:
    """Whether one more active link that hasn't paid yet would go beyond the owner's free
    allowance (and so cost the extra link price)."""
    settings = settings or ShortUrlSettings.load()
    return unpaid_active_link_count(owner) >= settings.free_active_links


def check_can_activate(owner: User, *, settings: ShortUrlSettings | None = None) -> None:
    """Raise `LimitReachedError` when the owner has the most active links allowed."""
    settings = settings or ShortUrlSettings.load()
    if active_link_count(owner) >= settings.max_active_links:
        raise LimitReachedError(settings.max_active_links)
