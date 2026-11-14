"""A tab's locations: the search, adding, the label, the home row, removing and reordering.

Every change answers the re-rendered grid for htmx (`#wt-grid`); a refusal is a 422 into the page's
message slot. Validated first, charged after: a refused request spends no rate-limit budget.
"""

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.htmx import is_htmx

from ..forms import LabelForm, LocationForm
from ..limits import MAX_REORDER_IDS
from ..models import Tab
from ..services import (
    add_city,
    add_zone,
    is_seeded,
    remove_location,
    reorder_locations,
    search,
    set_home,
    set_label,
)
from .common import (
    form_error_lines,
    grid_response,
    limited,
    owned_location,
    owned_tab,
    refuse,
    requested_date,
    tab_url,
    validation_lines,
)

SEARCH_SLOT = '#wt-search-msg'


@login_required
@require_GET
def location_search(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The suggestions for what is typed in "Add a location" (`?q=`, `?tab=`), an htmx partial.
    Over the rate limit it answers 204, so the last suggestions stay; opened directly it redirects
    to the app."""
    if not is_htmx(request):
        return redirect('world_time:home')
    tab = _searched_tab(request)
    query = request.GET.get('q', '')
    if not ratelimit.hit_user(request.user, 'WT_SEARCH_USER').allowed:
        return HttpResponse(status=204)
    return render(
        request,
        'world_time/partials/search_results.html',
        {
            'tab': tab,
            'query': ' '.join(query.split()),
            'results': search(query),
            'cities_seeded': is_seeded(),
            'grid_date': requested_date(request),
        },
    )


def _searched_tab(request: AuthenticatedHttpRequest) -> Tab:
    """The tab the search is for (`?tab=`): the requester's own, else a 404."""
    try:
        return owned_tab(request, UUID(request.GET.get('tab', '')))
    except ValueError:
        raise Http404('Tab not found') from None


@login_required
@require_POST
def location_add(request: AuthenticatedHttpRequest, tab_id: UUID) -> HttpResponse:
    tab = owned_tab(request, tab_id)
    fallback = tab_url(tab, requested_date(request))
    form = LocationForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=SEARCH_SLOT, fallback=fallback)
    if (response := limited(request, 'WT_EDIT_USER', retarget=SEARCH_SLOT)) is not None:
        return response
    try:
        if form.cleaned_data.get('city'):
            add_city(tab, form.cleaned_data['city'])
        else:
            add_zone(tab, form.cleaned_data['zone'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=SEARCH_SLOT, fallback=fallback)
    return grid_response(request, tab)


@login_required
@require_POST
def location_label(
    request: AuthenticatedHttpRequest, tab_id: UUID, location_id: UUID
) -> HttpResponse:
    tab = owned_tab(request, tab_id)
    location = owned_location(tab, location_id)
    fallback = tab_url(tab, requested_date(request))
    form = LabelForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), fallback=fallback)
    if (response := limited(request, 'WT_EDIT_USER')) is not None:
        return response
    try:
        set_label(location, form.cleaned_data['label'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), fallback=fallback)
    return grid_response(request, tab)


@login_required
@require_POST
def location_home(
    request: AuthenticatedHttpRequest, tab_id: UUID, location_id: UUID
) -> HttpResponse:
    tab = owned_tab(request, tab_id)
    location = owned_location(tab, location_id)
    if (response := limited(request, 'WT_EDIT_USER')) is not None:
        return response
    try:
        set_home(location)
    except ValidationError as exc:
        return refuse(
            request, validation_lines(exc), fallback=tab_url(tab, requested_date(request))
        )
    return grid_response(request, tab)


@login_required
@require_POST
def location_remove(
    request: AuthenticatedHttpRequest, tab_id: UUID, location_id: UUID
) -> HttpResponse:
    tab = owned_tab(request, tab_id)
    location = owned_location(tab, location_id)
    if (response := limited(request, 'WT_EDIT_USER')) is not None:
        return response
    remove_location(location)
    return grid_response(request, tab)


@login_required
@require_POST
def location_reorder(request: AuthenticatedHttpRequest, tab_id: UUID) -> HttpResponse:
    """Write the order the grid posts (`ids[]`). Answers 204."""
    tab = owned_tab(request, tab_id)
    raw_ids = request.POST.getlist('ids[]')
    ids: list[UUID] = []
    try:
        ids = [UUID(raw) for raw in raw_ids]
    except ValueError:
        ids = []
    if not ids or len(ids) > MAX_REORDER_IDS:
        return refuse(request, 'Could not read the new order.')
    if (response := limited(request, 'WT_EDIT_USER')) is not None:
        return response
    reorder_locations(tab, ids)
    return HttpResponse(status=204)
