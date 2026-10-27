"""Auto-hold a link under abuse review (issue #162; the same design as file transfer's, issue #59).

A hold is a plain flag, `ShortLink.held_for_review_at`, never a status: it is independent of
whatever the owner does to the link (disable it, edit it) and of every other mechanism that looks
at `status`. While it is set the link is unavailable (`links.is_available`: the redirect, the
preview and the password page render the neutral page) and **frozen for its owner, who can only
disable it** (`links.update_link`, `set_enabled(True)` and `delete_link` raise `LinkHeldError`).
Click recording, the rollup and the statistics are not touched: nothing is clicked while a link
is held, and what was recorded stays.

`maybe_hold_for_reports` runs once per new report (`reports.create_report`); `release_hold` when
staff dismiss the last pending report (`reports.dismiss_report`) or release it from the admin.
Taking the link down clears the hold (moot once it is terminal).
"""

from django.utils import timezone

from apps.core.ratelimit import normalize_ip_for_key

from ..models import LinkStatus, ReportStatus, ShortLink, ShortUrlSettings

_HOLDABLE = (LinkStatus.ACTIVE, LinkStatus.DISABLED)


def maybe_hold_for_reports(link: ShortLink, settings_row: ShortUrlSettings | None = None) -> bool:
    """Put `link` on hold once it has `auto_hold_report_threshold` (or more) pending reports from
    distinct IP addresses. A no-op (returns False) when the setting is off (0, the default), the
    link is already held, has been taken down or deleted, or hasn't reached the threshold.

    Counts distinct *normalized* addresses (`apps.core.ratelimit.normalize_ip_for_key`: an IPv6
    address collapsed to its /64, an IPv4-mapped one unwrapped), not report rows, so one visitor
    pressing "report" repeatedly can't hold a link alone, nor can one host just by rotating its
    address within its own /64. A report with no address never counts. **Caveat**: this only
    raises the bar. Anyone controlling N distinct real addresses (N /64s for IPv6) can still hold
    a link alone, so pick the threshold with that in mind rather than as a guarantee of N
    different people. The conditional update means only one of two racing reports holds it.
    """
    settings_row = settings_row or ShortUrlSettings.load()
    threshold = settings_row.auto_hold_report_threshold
    if not threshold or link.held_for_review_at or link.status not in _HOLDABLE:
        return False

    reporter_ips = (
        link.reports.filter(status=ReportStatus.PENDING, reporter_ip__isnull=False)
        .values_list('reporter_ip', flat=True)
        .iterator()
    )
    # Stops reading as soon as the threshold is reached: unreviewed reports can pile up.
    distinct: set[str] = set()
    for ip in reporter_ips:
        distinct.add(normalize_ip_for_key(ip))
        if len(distinct) >= threshold:
            break
    else:
        return False

    now = timezone.now()
    held = ShortLink.objects.filter(
        pk=link.pk, held_for_review_at__isnull=True, status__in=_HOLDABLE
    ).update(held_for_review_at=now, updated_at=now)
    if held:
        link.held_for_review_at = now
        link.updated_at = now
    return bool(held)


def release_hold(link: ShortLink) -> ShortLink:
    """Release `link`'s hold (staff dismissed the reports behind it, or released it from the
    admin). Idempotent."""
    now = timezone.now()
    ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=None, updated_at=now)
    link.held_for_review_at = None
    link.updated_at = now
    return link
