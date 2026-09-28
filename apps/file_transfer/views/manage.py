"""The anonymous sender's one-time manage link (spec section 6): `/t/<slug>/manage/<token>/`,
emailed alongside the download link once an anonymous transfer is confirmed
(`services.anon_emails.send_anonymous_sender_copy`). Offers disable and the download count --
nothing else, since there's no dashboard behind it. Stops working once the transfer ends
(`Transfer.is_ended`), same neutral-failure spirit as the public download page (spec section 3).
"""

import hmac

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .. import services
from ..models import Transfer


def _unavailable(request: HttpRequest) -> HttpResponse:
    return render(request, 'file_transfer/unavailable.html', status=404)


def _matching_transfer(slug: str, token: str) -> Transfer | None:
    """Look up by `slug` (already public, via the download link) and compare `token` in constant
    time -- so a wrong guess can't be distinguished, by timing, from a right one. No separate
    hashed column: the slug alone already makes the row unguessable to enumerate, and this token
    is never used as a lookup key, only compared once the row is already in hand.

    Compares the UTF-8 bytes of both values, not the `str`s themselves: `hmac.compare_digest`
    accepts `str` arguments only when both are ASCII-only, and raises `TypeError` otherwise -- a
    non-ASCII `token` from the URL (a stray guess, a scanner, a typo'd link) would otherwise 500
    this page instead of just failing to match.
    """
    transfer = Transfer.objects.filter(slug=slug, owner__isnull=True).first()
    if transfer is None:
        return None
    if not hmac.compare_digest(token.encode(), transfer.manage_token.encode()):
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

    try:
        services.disable_transfer(transfer)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    # Post/redirect/get: a reload (or a browser's "resend form data") after disabling must not
    # re-submit the same disable POST.
    return redirect('t:manage', slug, token)
