"""Row actions: enable, disable and delete a link (plain POST forms ending in a redirect with a
flash message, like the Receipts trash page).

Enabling can cost credits (an active link beyond the free allowance, once per link). The row's
menu opens a confirmation naming the cost and posts it as `confirm_cost`; nothing is charged
unless that equals what the service is about to spend.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError

from ..models import LinkStatus
from ..services import check_can_activate, delete_link, quote_enable, set_enabled
from .common import owned_link, parse_cost, redirect_back


def _back(request: AuthenticatedHttpRequest) -> HttpResponse:
    return redirect_back(request, reverse('short_url:dashboard'))


@login_required
@require_POST
def link_disable(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    link = owned_link(request, link_id)
    try:
        changed = set_enabled(link, False)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Link disabled.' if changed else 'Link was already disabled.')
    return _back(request)


@login_required
@require_POST
def link_enable(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    link = owned_link(request, link_id)
    if link.status == LinkStatus.DISABLED:
        # The cap first: there is no point asking for credits for a link that can't be enabled.
        try:
            check_can_activate(request.user)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return _back(request)
    cost = quote_enable(link)
    if cost and parse_cost(request.POST.get('confirm_cost')) != cost.total:
        messages.error(
            request,
            f'Enabling this link costs {cost.total} credits. Confirm it from the menu to continue.',
        )
        return _back(request)
    try:
        changed = set_enabled(link, True)
    except InsufficientCreditsError:
        messages.error(request, "You don't have enough credits to enable this link. Top up first.")
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Link enabled.' if changed else 'Link was already enabled.')
    return _back(request)


@login_required
@require_POST
def link_delete(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    link = owned_link(request, link_id)
    try:
        delete_link(link)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return _back(request)
    messages.success(request, 'Link deleted.')
    # The link's own page is gone now, so going "back" to it would be a 404.
    return redirect_back(request, reverse('short_url:dashboard'), not_to=str(link.pk))
