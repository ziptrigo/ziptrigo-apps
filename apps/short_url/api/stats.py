"""`GET /api/s/links/{id}/stats`: a link's totals and, once the detailed analytics are open, the
time series and the top referrers, countries, devices and browsers. Reads the very same
`apps.short_url.services.stats` the stats page does, so the numbers agree."""

from uuid import UUID

from ninja.errors import HttpError

from ..schemas import StatsSchema
from ..services import quote_analytics, stats, unlock_free_analytics
from .common import owned_link
from .router import router


@router.get('/links/{link_id}/stats', response=StatsSchema)
def link_stats(request, link_id: UUID, range: str = stats.DEFAULT_RANGE):
    """A link's statistics. `range` (`7`, `30` (default), `90` or `all`, in days back from today,
    UTC) applies to `detail`; `totals` is always all time.

    The totals are free. `detail` (time series and top lists) is the paid "detailed analytics"
    extra, once per link: until `POST /links/{id}/analytics` opens it, `detail` is `null` and
    `analytics_cost` says what it costs. The figures follow the link's current `filter_bots` (bots
    left out, or added to the clicks, visitors and lists), from the rollups plus today's events.
    """
    if range not in stats.RANGES:
        raise HttpError(400, f'range must be one of: {", ".join(stats.RANGES)}.')
    link = owned_link(request, link_id)
    through = stats.watermark()  # once, so every figure splits at the same day
    unlocked = unlock_free_analytics(link)
    return {
        'range': range,
        'totals': stats.totals(link, through=through),
        'analytics_enabled': unlocked,
        'analytics_cost': quote_analytics(link).total,
        'detail': stats.range_stats(link, range, through=through) if unlocked else None,
    }
