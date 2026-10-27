"""A link's statistics page (`/links/<id>/stats/`) and the action that opens the detailed part.

The totals (clicks, unique visitors, bot clicks) are always shown. The chart and the top lists are
the paid "detailed analytics" extra, once per link; until then the page offers it, with the cost
confirmed in the shared modal (`confirm_cost` -> `enable_analytics(expected_cost=...)`, like
enabling a link). Events are recorded from the first click either way, so opening it later shows
the whole history.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError

from ..services import (
    CostChangedError,
    analytics_unlocked,
    display_state,
    enable_analytics,
    quote_analytics,
    short_url,
    stats,
)
from .common import owned_link, parse_cost, render_page


@login_required
@require_GET
@ensure_csrf_cookie
def link_stats(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    link = owned_link(request, link_id)
    context = {
        'link': link,
        'short_url': short_url(link.code),
        'state': display_state(link),
        'totals': stats.totals(link),
        'unlocked': analytics_unlocked(link),
        'analytics_cost': quote_analytics(link).total,
        'range_choices': list(stats.RANGE_LABELS.items()),
    }
    if context['unlocked']:
        detail = stats.range_stats(link, stats.parse_range(request.GET.get('range')))
        context.update(
            detail=detail,
            range_label=stats.RANGE_LABELS[detail.key],
            bars=stats.chart_bars(detail.buckets),
            chart_width=stats.CHART_WIDTH,
            chart_height=stats.CHART_HEIGHT,
            first_label=detail.buckets[0].label if detail.buckets else '',
            last_label=detail.buckets[-1].label if detail.buckets else '',
            peak=max((bucket.clicks for bucket in detail.buckets), default=0),
            bucket_unit={1: 'day', 7: 'week', 0: 'month'}[detail.bucket_days],
        )
    return render_page(request, 'short_url/stats.html', context)


@login_required
@require_POST
def link_enable_analytics(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    link = owned_link(request, link_id)
    back = redirect(reverse('short_url:stats', args=[link.pk]))
    cost = quote_analytics(link)
    if cost and parse_cost(request.POST.get('confirm_cost')) != cost.total:
        messages.error(
            request,
            f'Detailed analytics cost {cost.total} credits. Confirm it from the page to continue.',
        )
        return back
    try:
        changed = enable_analytics(link, expected_cost=cost.total)
    except CostChangedError as exc:
        messages.error(
            request,
            f'Detailed analytics now cost {exc.quote.total} credits. Confirm it again to continue.'
            if exc.quote
            else 'The cost changed. Try again.',
        )
    except InsufficientCreditsError:
        messages.error(
            request, "You don't have enough credits for detailed analytics. Top up first."
        )
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        if changed:
            messages.success(request, 'Detailed analytics are on for this link.')
    return back
