"""Counting a click on a link.

This phase only bumps the link's counters. The analytics phase replaces the body with the
recording of a click event (device, referrer, country, bot flag) next to the same atomic bump;
the redirect view's one call site stays as it is.
"""

from django.db.models import F, Q
from django.http import HttpRequest
from django.utils import timezone

from ..models import LinkStatus, ShortLink


def record_click(link: ShortLink, request: HttpRequest) -> bool:
    """Count one click on `link`, atomically. Returns False when the link stopped being
    available between the caller's read and now: it was disabled, or the click cap was reached by
    clicks racing this one (the cap is part of the same `UPDATE`, so it can't be overshot)."""
    under_cap = Q(max_clicks__isnull=True) | Q(click_count__lt=F('max_clicks'))
    updated = ShortLink.objects.filter(under_cap, pk=link.pk, status=LinkStatus.ACTIVE).update(
        click_count=F('click_count') + 1, last_clicked_at=timezone.now()
    )
    return bool(updated)
