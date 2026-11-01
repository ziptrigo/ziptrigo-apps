"""Saved filters (issue #181): the filters page, a filter's own page and the endpoints that create,
rename, update, delete and reorder them.

`/todo/filters/` lists them (name, a summary of the params, the count of open tasks, inline rename,
delete through the shared confirmation modal, drag to reorder) and takes the POST that saves a
search as a filter. The counts are not part of any page render: the sidebar (and the filters list)
fetch them with one `GET /todo/filters/counts/`, which answers all of them out of band in a single
query and spends `TODO_SEARCH_USER` once. `/todo/filters/<id>/` is the search page with the filter's params applied
(`views.search`). Another user's filter is a 404, never a 403. Mutations spend `TODO_EDIT_USER`;
the page of a filter and the counts spend `TODO_SEARCH_USER` like any search. The rules are
`services.saved_filters`; a refusal is a 422 `Label: message` line in the slot of the control used.
"""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..models import SavedFilter
from ..services import (
    delete_filter,
    list_filters,
    parse_filter_params,
    rename_filter,
    reorder_filters,
    save_filter,
    update_filter_params,
)
from ..services.limits import MAX_SAVED_FILTERS
from ..services.saved_filters import SIDEBAR_LIMIT, filter_counts, filter_rows
from .common import limited, refuse, render_page, validation_lines
from .search import render_search_page, requested_params

SAVE_SLOT = '#filter-save-msg'
LIST_SLOT = '#filter-msg'


def _filters_url() -> str:
    return reverse('todo:filters')


def _owned(request: AuthenticatedHttpRequest, filter_id: UUID) -> SavedFilter:
    return get_object_or_404(
        SavedFilter.objects.select_related('user'), pk=filter_id, user=request.user
    )


def _list_response(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The refreshed list for htmx; a redirect to the page otherwise."""
    if not is_htmx(request):
        return redirect(_filters_url())
    rows = filter_rows(request.user, list(list_filters(request.user)))
    return render(request, 'todo/partials/filter_list.html', {'rows': rows, 'clear_msg': True})


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def filters_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the page. POST: save the posted search as a filter."""
    if request.method == 'POST':
        return _create(request)
    rows = filter_rows(request.user, list(list_filters(request.user)))
    return render_page(request, 'todo/filters.html', {'rows': rows}, nav='filters')


def _create(request: AuthenticatedHttpRequest) -> HttpResponse:
    fallback = reverse('todo:search')
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SAVE_SLOT)) is not None:
        return response
    params = parse_filter_params(request.POST, request.user)
    try:
        saved = save_filter(request.user, request.POST.get('name', ''), params)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=SAVE_SLOT, fallback=fallback)
    messages.success(request, f'Saved the filter {saved.name}.')
    return hx_redirect(request, reverse('todo:filter', args=[saved.pk]))


@login_required
@require_GET
def filter_counts_view(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The open-task counts of the saved filters, out of band: one span per filter for the
    sidebar's first `SIDEBAR_LIMIT`, and with `?scope=all` (the filters page) one per filter for
    the page's list too. One query for all of them (`services.saved_filters.filter_counts`); it
    spends `TODO_SEARCH_USER` once, and over the limit (or on a timeout) every count is "-"."""
    filters = list(list_filters(request.user))
    if not filters:
        return HttpResponse(status=204)
    everything = request.GET.get('scope') == 'all'
    counted = filters if everything else filters[:SIDEBAR_LIMIT]
    if ratelimit.hit_user(request.user, 'TODO_SEARCH_USER').allowed:
        counts = filter_counts(request.user, counted)
    else:
        counts = dict.fromkeys((saved.pk for saved in counted))
    sidebar = [(saved.pk, counts[saved.pk]) for saved in filters[:SIDEBAR_LIMIT]]
    page = [(saved.pk, counts[saved.pk]) for saved in counted] if everything else []
    return render(request, 'todo/partials/filter_counts.html', {'sidebar': sidebar, 'page': page})


@login_required
@require_GET
@ensure_csrf_cookie
def filter_page(request: AuthenticatedHttpRequest, filter_id: UUID) -> HttpResponse:
    """A saved filter run: the search page with its params (or the edited ones in the query
    string), headed by its name."""
    saved = _owned(request, filter_id)
    return render_search_page(request, requested_params(request, saved), saved)


@login_required
@require_POST
def filter_rename(request: AuthenticatedHttpRequest, filter_id: UUID) -> HttpResponse:
    saved = _owned(request, filter_id)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=LIST_SLOT)) is not None:
        return response
    try:
        rename_filter(saved, request.POST.get('name', ''))
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=LIST_SLOT, fallback=_filters_url())
    return _list_response(request)


@login_required
@require_POST
def filter_update(request: AuthenticatedHttpRequest, filter_id: UUID) -> HttpResponse:
    """ "Update filter": replace the filter's params with the posted search."""
    saved = _owned(request, filter_id)
    fallback = reverse('todo:filter', args=[saved.pk])
    if (response := limited(request, 'TODO_EDIT_USER', retarget='#search-msg')) is not None:
        return response
    params = parse_filter_params(request.POST, request.user)
    try:
        update_filter_params(saved, params)
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget='#search-msg', fallback=fallback)
    messages.success(request, f'Updated the filter {saved.name}.')
    return hx_redirect(request, fallback)


@login_required
@require_POST
def filter_delete(request: AuthenticatedHttpRequest, filter_id: UUID) -> HttpResponse:
    saved = _owned(request, filter_id)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=LIST_SLOT)) is not None:
        return response
    name = saved.name
    delete_filter(saved)
    messages.success(request, f'Deleted the filter {name}.')
    return hx_redirect(request, _filters_url())


@login_required
@require_POST
def filter_reorder(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Write the order SortableJS posts (`ids[]`). Answers 204."""
    if (response := limited(request, 'TODO_EDIT_USER', retarget=LIST_SLOT)) is not None:
        return response
    raw_ids = request.POST.getlist('ids[]')
    if len(raw_ids) > MAX_SAVED_FILTERS:  # more than a user can have: not an order SortableJS sent
        return refuse(request, 'Could not read the new order.', retarget=LIST_SLOT)
    ids: list[UUID] = []
    for raw in raw_ids:
        try:
            ids.append(UUID(raw))
        except ValueError:
            return refuse(request, 'Could not read the new order.', retarget=LIST_SLOT)
    reorder_filters(request.user, ids)
    return HttpResponse(status=204)
