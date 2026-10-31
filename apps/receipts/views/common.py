"""What the Receipts views share: the page renderer that adds the frozen banner, ownership
lookups, and the 422 message responses.

Ownership: a receipt that is not the requester's (or never finished uploading) is a 404, never a
403, so an id can't be probed.
"""

import json

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.field_errors import ErrorLines, form_lines

from ..models import Receipt
from ..services import get_account


def render_page(
    request: AuthenticatedHttpRequest, template: str, context: dict | None = None, status: int = 200
) -> HttpResponse:
    """Render a full Receipts page. Makes sure the user's Receipts account exists (created on
    first visit) and adds `receipts_frozen` for the "storage is paused" banner every page shows."""
    account = get_account(request.user)
    page_context = {'receipts_frozen': account.is_frozen, 'receipts_account': account}
    page_context.update(context or {})
    return render(request, template, page_context, status=status)


def owned_receipt(
    request: AuthenticatedHttpRequest,
    receipt_id: object,
    *,
    trashed: bool | None = None,
) -> Receipt:
    """The requester's receipt, or a 404. `trashed=True` / `False` additionally requires it to be
    in / out of the Trash; `None` accepts either."""
    queryset = Receipt.objects.select_related(
        'user', 'category', 'possible_duplicate_of', 'vendor_ref'
    )
    receipt = get_object_or_404(queryset, pk=receipt_id, user=request.user, uploaded=True)
    if trashed is not None and (receipt.deleted_at is not None) != trashed:
        raise Http404('Receipt not found')
    return receipt


def message_response(
    request: HttpRequest,
    messages: list[str] | str,
    *,
    retarget: str,
    status: int = 422,
    top_up: bool = False,
) -> HttpResponse:
    """A 422 whose body is the message slot's content (one line per message), sent to `retarget`
    (a selector) whatever the request's own target was. `top_up` adds a link to the credits
    page."""
    if isinstance(messages, str):
        messages = [messages]
    response = render(
        request,
        'receipts/partials/form_errors.html',
        {'messages_list': messages, 'top_up': top_up},
        status=status,
    )
    response['HX-Retarget'] = retarget
    response['HX-Reswap'] = 'innerHTML'
    return response


def form_error_lines(form) -> ErrorLines:
    """`Label: message` per error, non-field errors first (the design system's error format).
    The lines also carry each field's messages, which the slot's partial hands to the page."""
    return form_lines(form)


def credits_url() -> str:
    """Where "Top up" goes: the credits page (there is no purchase page yet)."""
    return reverse('billing:credits-history')


def json_body(request: HttpRequest) -> dict:
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError, UnicodeDecodeError:
        return {}
    return body if isinstance(body, dict) else {}
