"""Create and edit a link (`/links/new/`, `/links/<id>/edit/`).

HTMX form views following the 422 convention: a refused submission puts `Label: message` lines in
`#link-form-msg`; success flashes a message and redirects to the link's page (a plain redirect
without htmx).

**Credits are never spent without the user having seen the cost.** When a create would charge
anything (a custom alias, an extra active link), the first POST charges nothing and answers 422
with a confirmation panel in `#cost-confirm` naming the total; its button re-submits the form
with `confirm_cost=<total>`. The view passes that total to `create_link` as `expected_cost`, which
recomputes the cost inside the transaction that charges (after the owner's row is locked) and raises
`CostChangedError` when it differs, so a price or allowance changed in between (another tab, a
concurrent request) asks again instead of charging unconfirmed. Editing charges nothing in this
phase.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.timezone import localtime
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core import ratelimit
from apps.core.htmx import hx_redirect

from ..forms import LinkForm
from ..models import ShortLink
from ..services import (
    CostChangedError,
    LinkInput,
    Quote,
    check_can_create,
    create_link,
    is_extra_link,
    quote_create,
    short_url,
    update_link,
)
from ..services.billing import Extra, price
from ..services.destinations import UTM_PARAMS
from .common import (
    form_error_lines,
    message_response,
    owned_link,
    parse_cost,
    render_page,
    validation_lines,
)

_MSG = '#link-form-msg'


def _initial(link: ShortLink) -> dict:
    return {
        'destination': link.destination,
        'title': link.title,
        'tags': ', '.join(link.tags),
        'expires_at': localtime(link.expires_at).strftime('%Y-%m-%dT%H:%M')
        if link.expires_at
        else '',
        'max_clicks': link.max_clicks or '',
    }


def _page_context(request: AuthenticatedHttpRequest, link: ShortLink | None) -> dict:
    values = _initial(link) if link else {}
    form_fields = LinkForm().fields
    return {
        'link': link,
        'values': values,
        'link_base': short_url(''),
        'alias_price': price(Extra.ALIAS),
        'extra_link_price': price(Extra.EXTRA_LINK),
        'beyond_allowance': link is None and is_extra_link(request.user),
        'utm_fields': [(name, form_fields[name].label) for name in UTM_PARAMS],
    }


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def link_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    if request.method == 'GET':
        return render_page(request, 'short_url/link_form.html', _page_context(request, None))

    form = LinkForm(request.POST)
    if not form.is_valid():
        return message_response(request, form_error_lines(form), retarget=_MSG)
    data = LinkInput(
        destination=form.cleaned_data['destination'],
        alias=form.cleaned_data['alias'],
        title=form.cleaned_data['title'],
        tags=form.cleaned_data['tags'],
        expires_at=form.cleaned_data['expires_at'],
        max_clicks=form.cleaned_data['max_clicks'],
        utm=form.utm,
    )

    # The cap first: there is no point asking for credits for a link that can't be made.
    try:
        check_can_create(request.user)
    except ValidationError as exc:
        return message_response(request, validation_lines(exc), retarget=_MSG)
    cost = quote_create(request.user, data.alias)
    if cost and parse_cost(request.POST.get('confirm_cost')) != cost.total:
        return _confirmation(request, cost, verb='Create the link')

    limited = ratelimit.hit_user(request.user, 'SU_CREATE_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget=_MSG)
    try:
        link = create_link(request.user, data, expected_cost=cost.total)
    except CostChangedError as exc:
        if exc.quote:
            return _confirmation(request, exc.quote, verb='Create the link')
        return message_response(request, 'The cost changed. Submit the form again.', retarget=_MSG)
    except InsufficientCreditsError:
        return message_response(
            request,
            "You don't have enough credits for this link.",
            retarget=_MSG,
            top_up=True,
        )
    except ValidationError as exc:
        return message_response(request, validation_lines(exc), retarget=_MSG)

    messages.success(request, 'Link created.')
    return hx_redirect(request, reverse('short_url:detail', args=[link.pk]))


def _confirmation(request: AuthenticatedHttpRequest, cost: Quote, *, verb: str) -> HttpResponse:
    """The "this costs N credits" panel, as a 422 into `#cost-confirm`."""
    response = render(
        request,
        'short_url/partials/cost_confirm.html',
        {'quote': cost, 'balance': get_balance(request.user), 'verb': verb},
        status=422,
    )
    response['HX-Retarget'] = '#cost-confirm'
    response['HX-Reswap'] = 'innerHTML'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def link_edit(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    link = owned_link(request, link_id)
    if request.method == 'GET':
        return render_page(request, 'short_url/link_form.html', _page_context(request, link))

    form = LinkForm(request.POST, link=link)
    if not form.is_valid():
        return message_response(request, form_error_lines(form), retarget=_MSG)

    limited = ratelimit.hit_user(request.user, 'SU_EDIT_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget=_MSG)
    try:
        update_link(
            link,
            destination=form.cleaned_data['destination'],
            utm=form.utm,
            title=form.cleaned_data['title'],
            tags=form.cleaned_data['tags'],
            expires_at=form.cleaned_data['expires_at'],
            max_clicks=form.cleaned_data['max_clicks'],
        )
    except ValidationError as exc:
        return message_response(request, validation_lines(exc), retarget=_MSG)

    messages.success(request, 'Link saved.')
    return hx_redirect(request, reverse('short_url:detail', args=[link.pk]))
