"""The anonymous sender's one-time manage link (spec section 6): `/t/<slug>/manage/<token>/`,
emailed alongside the download link once an anonymous transfer is confirmed
(`services.anon_emails.send_anonymous_sender_copy`). Offers disable and the download count --
nothing else, since there's no dashboard behind it. Stops working once the transfer ends
(`Transfer.is_ended`), same neutral-failure spirit as the public download page (spec section 3).
"""

import hmac

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .. import services
from ..models import Transfer


def _unavailable(request: HttpRequest) -> HttpResponse:
    return render(request, 'file_transfer/unavailable.html', status=404)


def _matching_transfer(slug: str, token: str) -> Transfer | None:
    """Look up by `slug` (already public, via the download link) and compare `token` in constant
    time -- so a wrong guess can't be distinguished, by timing, from a right one. No separate
    hashed column: the slug alone already makes the row unguessable to enumerate, and this token
    is never used as a lookup key, only compared once the row is already in hand."""
    transfer = Transfer.objects.filter(slug=slug, owner__isnull=True).first()
    if transfer is None:
        return None
    if not hmac.compare_digest(token, transfer.manage_token):
        return None
    return transfer


@require_GET
def manage_page(request: HttpRequest, slug: str, token: str) -> HttpResponse:
    transfer = _matching_transfer(slug, token)
    if transfer is None or transfer.is_ended:
        return _unavailable(request)
    return render(request, 'file_transfer/manage.html', {'transfer': transfer})


@require_POST
def manage_disable(request: HttpRequest, slug: str, token: str) -> HttpResponse:
    transfer = _matching_transfer(slug, token)
    if transfer is None or transfer.is_ended:
        return _unavailable(request)

    error = ''
    try:
        services.disable_transfer(transfer)
    except ValidationError as exc:
        error = exc.messages[0]
    return render(request, 'file_transfer/manage.html', {'transfer': transfer, 'error': error})
