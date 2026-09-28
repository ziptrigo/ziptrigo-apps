"""The public download page (spec sections 3-4): `/t/<slug>/`. No login required -- this is the
link recipients get by email. Never reveals *why* a transfer isn't available (expired, disabled,
suspended, deleted, or its limit reached all render the same neutral page).
"""

import ipaddress

from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .. import services
from ..forms import DownloadPasswordForm
from ..models import Transfer, TransferFile


class PublicHttpRequest(HttpRequest):
    """`HttpRequest` typed with `.session`, added by `SessionMiddleware` for every request --
    unlike `apps.accounts.http.AuthenticatedHttpRequest`, this doesn't require login: these are
    the public, unauthenticated download-page views."""

    session: SessionBase


def _client_ip(request: HttpRequest) -> str | None:
    """The real client IP for `DownloadEvent.ip` (phase 1 only; the per-IP anonymous limits in
    spec section 13 are phase 2).

    Reads `X-Real-IP`, which **must** be set by our own nginx from the actual TCP peer (never
    passed through from the client) -- unlike `X-Forwarded-For`, which the client fully controls
    and which nginx here does not sanitize, so it isn't trustworthy on its own. Falls back to
    `REMOTE_ADDR` for direct/local access (dev, tests, or nginx misconfigured to not set it).
    Returns `None` for anything that doesn't parse as a valid IP address, rather than letting a
    bogus value reach `GenericIPAddressField` and crash the request with a `DataError` on
    Postgres.
    """
    candidate = request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR')
    if not candidate:
        return None
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def _unavailable(request: HttpRequest) -> HttpResponse:
    return render(request, 'file_transfer/unavailable.html', status=404)


def _context(request: PublicHttpRequest, transfer: Transfer, password_form: DownloadPasswordForm):
    return {
        'transfer': transfer,
        'files': transfer.files.filter(uploaded=True).order_by('name'),
        'unlocked': not services.requires_password(transfer)
        or services.is_unlocked_in_session(request.session, transfer.id, transfer.password_hash),
        'password_form': password_form,
    }


@require_GET
def download_page(request: PublicHttpRequest, slug: str) -> HttpResponse:
    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not services.is_available(transfer):
        return _unavailable(request)

    return render(
        request, 'file_transfer/download.html', _context(request, transfer, DownloadPasswordForm())
    )


@require_POST
def unlock(request: PublicHttpRequest, slug: str) -> HttpResponse:
    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not services.is_available(transfer):
        return _unavailable(request)

    form = DownloadPasswordForm(request.POST)
    if form.is_valid() and services.check_password(transfer, form.cleaned_data['password']):
        services.unlock_in_session(request.session, transfer.id, transfer.password_hash)
        return redirect(reverse('t:download', args=[slug]))

    form.add_error('password', 'Incorrect password.')
    return render(
        request, 'file_transfer/download.html', _context(request, transfer, form), status=422
    )


@require_GET
def download_file(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not services.is_available(transfer):
        return _unavailable(request)

    if services.requires_password(transfer) and not services.is_unlocked_in_session(
        request.session, transfer.id, transfer.password_hash
    ):
        return redirect(reverse('t:download', args=[slug]))

    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer, uploaded=True)
    try:
        url = services.record_download(transfer, file, _client_ip(request))
    except ValidationError:
        return _unavailable(request)
    return redirect(url)
