"""What the World Time views share: ownership lookups, the grid's context and response, rate
limits and the 422 message responses.

Ownership: a tab or location that is not the requester's is a 404, never a 403, so an id can't be
probed.
"""

from collections.abc import Mapping
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.field_errors import ErrorLines, form_lines
from apps.core.field_errors import validation_lines as core_validation_lines
from apps.core.htmx import is_htmx

from ..models import Location, Tab
from ..services import build_grid, home_today, is_seeded, list_locations, parse_day

FIELD_LABELS = {
    'name': 'Name',
    'label': 'Label',
    'city': 'Place',
    'zone': 'Time zone',
    'title': 'Title',
    'starts_at': 'Start',
    'ends_at': 'End',
    'start': 'Start',
    'end': 'End',
    'date': 'Date',
    'start_time': 'Start',
    'end_time': 'End',
}
PAGE_SLOT = '#wt-msg'


def owned_tab(request: AuthenticatedHttpRequest, tab_id: UUID) -> Tab:
    """The requester's tab `tab_id`, or a 404."""
    tab = Tab.objects.select_related('user').filter(user=request.user, pk=tab_id).first()
    if tab is None:
        raise Http404('Tab not found')
    return tab


def owned_location(tab: Tab, location_id: UUID) -> Location:
    """The location `location_id` of `tab`, or a 404."""
    location = Location.objects.select_related('tab__user').filter(tab=tab, pk=location_id).first()
    if location is None:
        raise Http404('Location not found')
    return location


def requested_date(request: HttpRequest) -> str:
    """The `date` a request carries (posted by the grid's forms, or in the query string)."""
    return request.POST.get('date') or request.GET.get('date') or ''


def tab_url(tab: Tab, day: str = '') -> str:
    url = reverse('world_time:tab', args=[tab.pk])
    return f'{url}?{urlencode({"date": day})}' if day else url


def grid_context(request: AuthenticatedHttpRequest, tab: Tab) -> dict:
    """The grid of `tab` for the requested date (today on the home row's clock by default)."""
    locations = list_locations(tab)
    now = timezone.now()
    today = home_today(locations, now)
    day = parse_day(requested_date(request), today)
    grid = build_grid(locations, day, now=now)
    return {
        'tab': tab,
        'grid': grid,
        'grid_date': '' if grid.is_today else day.isoformat(),
        'cities_seeded': is_seeded(),
    }


def grid_response(request: AuthenticatedHttpRequest, tab: Tab) -> HttpResponse:
    """After a change to the tab's rows: the re-rendered grid for htmx (it replaces `#wt-grid` and
    clears the message slot), a redirect to the tab's page otherwise."""
    if not is_htmx(request):
        return redirect(tab_url(tab, requested_date(request)))
    context = grid_context(request, tab)
    context['clear_msg'] = True
    return render(request, 'world_time/partials/grid.html', context)


def validation_lines(exc: ValidationError, names: Mapping[str, str] | None = None) -> ErrorLines:
    """`Label: message` per error of a service `ValidationError`, non-field errors plain and
    first."""
    return core_validation_lines(exc, FIELD_LABELS, names)


def form_error_lines(form) -> ErrorLines:
    return form_lines(form)


def message_response(
    request: HttpRequest, lines: list[str] | str, *, retarget: str = PAGE_SLOT, status: int = 422
) -> HttpResponse:
    """A 422 whose body is the message slot's content, sent to `retarget` whatever the request's
    own target was."""
    if isinstance(lines, str):
        lines = [lines]
    response = render(
        request, 'world_time/partials/messages.html', {'messages_list': lines}, status=status
    )
    response['HX-Retarget'] = retarget
    response['HX-Reswap'] = 'innerHTML'
    return response


def refuse(
    request: AuthenticatedHttpRequest,
    lines: list[str] | str,
    *,
    retarget: str = PAGE_SLOT,
    fallback: str = '',
) -> HttpResponse:
    """A refused action: the 422 message for htmx, a flash message and a redirect otherwise."""
    if is_htmx(request):
        return message_response(request, lines, retarget=retarget)
    for line in [lines] if isinstance(lines, str) else lines:
        messages.error(request, line)
    return redirect(fallback or reverse('world_time:home'))


def limited(request: AuthenticatedHttpRequest, rule: str, *, retarget: str = PAGE_SLOT):
    """The 429 response when the user is over the `rule` rate limit, else None. Charges one hit."""
    result = ratelimit.hit_user(request.user, rule)
    if result.allowed:
        return None
    return ratelimit.web_response(request, result, retarget=retarget)
