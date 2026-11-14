"""`/time/`: the page of a tab (its grid), and creating, renaming and deleting tabs."""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import hx_redirect

from ..forms import TabForm
from ..models import Tab
from ..services import create_tab, delete_tab, get_account, list_tabs, rename_tab
from .common import (
    form_error_lines,
    grid_context,
    limited,
    owned_tab,
    refuse,
    requested_date,
    tab_url,
    validation_lines,
)

TAB_SLOT = '#wt-tab-msg'


def _page(request: AuthenticatedHttpRequest, tab: Tab, tabs: list[Tab]) -> HttpResponse:
    get_account(request.user)
    context = grid_context(request, tab)
    context['tabs'] = tabs
    return render(request, 'world_time/tab.html', context)


@login_required
@require_GET
@ensure_csrf_cookie
def home_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The user's first tab (made on a first visit: one row, the account's time zone)."""
    tabs = list_tabs(request.user)
    return _page(request, tabs[0], tabs)


@login_required
@require_GET
@ensure_csrf_cookie
def tab_page(request: AuthenticatedHttpRequest, tab_id: UUID) -> HttpResponse:
    tab = owned_tab(request, tab_id)
    return _page(request, tab, list_tabs(request.user))


@login_required
@require_POST
def tab_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = TabForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=TAB_SLOT)
    if (response := limited(request, 'WT_EDIT_USER', retarget=TAB_SLOT)) is not None:
        return response
    try:
        tab = create_tab(request.user, form.cleaned_data['name'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=TAB_SLOT)
    return hx_redirect(request, tab_url(tab))


@login_required
@require_POST
def tab_rename(request: AuthenticatedHttpRequest, tab_id: UUID) -> HttpResponse:
    tab = owned_tab(request, tab_id)
    form = TabForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=TAB_SLOT, fallback=tab_url(tab))
    if (response := limited(request, 'WT_EDIT_USER', retarget=TAB_SLOT)) is not None:
        return response
    try:
        rename_tab(tab, form.cleaned_data['name'])
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=TAB_SLOT, fallback=tab_url(tab))
    return hx_redirect(request, tab_url(tab, requested_date(request)))


@login_required
@require_POST
def tab_delete(request: AuthenticatedHttpRequest, tab_id: UUID) -> HttpResponse:
    """From the confirmation modal: a plain POST that ends in a redirect with a flash message."""
    tab = owned_tab(request, tab_id)
    if (response := limited(request, 'WT_EDIT_USER')) is not None:
        return response
    name = tab.name
    try:
        delete_tab(tab)
    except ValidationError as exc:
        for line in validation_lines(exc):
            messages.error(request, line)
        return hx_redirect(request, tab_url(tab))
    messages.success(request, f'Deleted the tab {name}.')
    return hx_redirect(request, reverse('world_time:home'))
