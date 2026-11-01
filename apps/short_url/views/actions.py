"""Row actions: enable, disable and delete a link (plain POST forms ending in a redirect with a
flash message, like the Receipts trash page).

Enabling can cost credits (an active link beyond the free allowance, once per link). The row's
menu opens a confirmation naming the cost and posts it as `confirm_cost`; the view passes the
total to `set_enabled` as `expected_cost`, which recomputes it inside the charging transaction and
refuses (`CostChangedError`) when it differs, so nothing is charged unconfirmed.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError

from ..services import CostChangedError, delete_link, quote_enable, set_enabled
from .common import (
    credits_text,
    out_of_credits_message,
    owned_link,
    parse_cost,
    redirect_back,
)


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
    cost = quote_enable(link)
    if cost and parse_cost(request.POST.get('confirm_cost')) != cost.total:
        messages.error(
            request,
            f'Enabling this link costs {credits_text(cost.total)}. '
            'Confirm it from the menu to continue.',
        )
        return _back(request)
    try:
        changed = set_enabled(link, True, expected_cost=cost.total)
    except CostChangedError as exc:
        messages.error(
            request,
            f'Enabling this link now costs {credits_text(exc.quote.total)}. '
            'Confirm it from the menu to continue.'
            if exc.quote
            else 'The cost changed. Try again.',
        )
    except InsufficientCreditsError:
        messages.error(
            request, out_of_credits_message("You don't have enough credits to enable this link.")
        )
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
