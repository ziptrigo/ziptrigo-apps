"""How many links a user may have: the free allowance and the hard cap, both from
`ShortUrlSettings`.

The **hard cap** (`max_active_links`) counts every non-deleted link, active *and* disabled:
otherwise disabling a link would free its slot and a user could pile up unbounded rows. Only
deleting makes room. The **free allowance** counts the *active* links that have **not** paid
their way in ("active" is a link whose status is `active`: an expired or click-capped link still
is, its owner can disable or delete it to make room). A link that paid the extra link price keeps
its place for good: disabling and re-enabling it is free, and it does not use up one of the free
slots, so a user never pays twice for the same room.
"""

from apps.accounts.models import User

from ..models import LinkStatus, ShortLink, ShortUrlSettings
from .errors import LimitReachedError

#: The statuses that count toward the hard cap.
COUNTED_STATUSES = (LinkStatus.ACTIVE, LinkStatus.DISABLED)


def link_count(owner: User) -> int:
    """The owner's non-deleted (active and disabled) links: what the hard cap is measured on."""
    return ShortLink.objects.filter(owner=owner, status__in=COUNTED_STATUSES).count()


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


def check_can_create(owner: User, *, settings: ShortUrlSettings | None = None) -> None:
    """Raise `LimitReachedError` when the owner has the most links allowed. (Enabling a disabled
    link needs no check: it already counts.)"""
    settings = settings or ShortUrlSettings.load()
    if link_count(owner) >= settings.max_active_links:
        raise LimitReachedError(settings.max_active_links)
