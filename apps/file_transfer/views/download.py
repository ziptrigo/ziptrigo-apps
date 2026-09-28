"""The public download page (spec sections 3-4): `/t/<slug>/`. No login required -- this is the
link recipients get by email. Never reveals *why* a transfer isn't available (expired, disabled,
suspended, deleted, or its limit reached all render the same neutral page).
"""

from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .. import services
from ..forms import DownloadPasswordForm
from ..models import Transfer, TransferFile, ZipStatus
from ..services.client_ip import client_ip as _client_ip
from ..services.zip import ensure_zip_build_started


class PublicHttpRequest(HttpRequest):
    """`HttpRequest` typed with `.session`, added by `SessionMiddleware` for every request --
    unlike `apps.accounts.http.AuthenticatedHttpRequest`, this doesn't require login: these are
    the public, unauthenticated download-page views."""

    session: SessionBase


def _unavailable(request: HttpRequest) -> HttpResponse:
    return render(request, 'file_transfer/unavailable.html', status=404)


def _password_gated_transfer(request: PublicHttpRequest, slug: str) -> Transfer | HttpResponse:
    """Shared prelude for every public per-file/zip download endpoint: look the transfer up,
    check availability, and check the password gate (spec section 3: gates the download buttons,
    not the file list). Returns either the `Transfer` or the response to return instead."""
    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not services.is_available(transfer):
        return _unavailable(request)
    if services.requires_password(transfer) and not services.is_unlocked_in_session(
        request.session, transfer.id, transfer.password_hash
    ):
        return redirect(reverse('t:download', args=[slug]))
    return transfer


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
    transfer = _password_gated_transfer(request, slug)
    if isinstance(transfer, HttpResponse):
        return transfer

    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer, uploaded=True)
    try:
        url = services.record_download(transfer, file, _client_ip(request))
    except ValidationError:
        return _unavailable(request)
    return redirect(url)


#: How many times the "preparing the zip" partial polls itself automatically before giving up and
#: asking for a manual click instead (spec section 5's polling loop, capped): a build that's still
#: not done after this many ticks is either stuck (see `services.zip`'s stale-`BUILDING` lease) or
#: unusually large, and either way an unattended tab shouldn't keep hammering this endpoint
#: forever.
_MAX_AUTO_ZIP_POLLS = 30


@require_GET
def zip_status(request: PublicHttpRequest, slug: str) -> HttpResponse:
    """HTMX partial (spec section 5): kick the lazy zip build off the first time it's asked for,
    then report the current status -- "preparing" (`BUILDING`/`NONE` just claimed),
    "ready" (a download link), or "failed" (with a retry). The template polls this until it stops
    being `BUILDING`, up to `_MAX_AUTO_ZIP_POLLS` times."""
    transfer = _password_gated_transfer(request, slug)
    if isinstance(transfer, HttpResponse):
        return transfer

    if transfer.zip_status in (ZipStatus.NONE, ZipStatus.FAILED):
        ensure_zip_build_started(transfer)
        transfer.refresh_from_db(fields=['zip_status'])

    try:
        polls = int(request.GET.get('polls', '0'))
    except ValueError:
        polls = 0

    context = {
        'transfer': transfer,
        'next_poll': polls + 1,
        'poll_cap_reached': (
            transfer.zip_status == ZipStatus.BUILDING and polls >= _MAX_AUTO_ZIP_POLLS
        ),
    }
    return render(request, 'file_transfer/partials/zip_status.html', context)


@require_GET
def download_zip(request: PublicHttpRequest, slug: str) -> HttpResponse:
    """The actual zip download, once `zip_status` is `READY` -- otherwise back to the download
    page (whose zip widget shows the "preparing"/polling state) rather than erroring."""
    transfer = _password_gated_transfer(request, slug)
    if isinstance(transfer, HttpResponse):
        return transfer

    if transfer.zip_status != ZipStatus.READY:
        return redirect(reverse('t:download', args=[slug]))

    try:
        url = services.record_download(transfer, None, _client_ip(request))
    except ValidationError:
        return _unavailable(request)
    return redirect(url)
