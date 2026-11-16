"""The public download page (spec sections 3-4): `/t/<slug>/`. No login required -- this is the
link recipients get by email. Never reveals *why* a transfer isn't available (expired, disabled,
suspended, deleted, or its limit reached all render the same neutral page).
"""

import base64
import logging
import re
from functools import lru_cache

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_POST

from apps.core import ratelimit
from apps.core.htmx import is_htmx
from apps.core.services.client_ip import client_ip as _client_ip
from apps.core.services.qr_image import render_png

from .. import services
from ..forms import AbuseReportForm, DownloadPasswordForm
from ..models import PreviewStatus, Transfer, TransferFile, ZipStatus
from ..services import preview, renditions
from ..services.request_access import Access, check_access, is_owner
from ..services.storage import get_storage
from ..services.zip import ensure_zip_build_started
from . import request_public
from .types import PublicHttpRequest

logger = logging.getLogger(__name__)


def _unavailable(request: HttpRequest) -> HttpResponse:
    return render(request, 'file_transfer/unavailable.html', status=404)


def _became_unavailable(request: HttpRequest) -> HttpResponse:
    """Like `_unavailable`, but for `report_transfer`'s own `hx-post` (issue #59 code review): the
    full "no longer available" page's markup is what `_unavailable` renders, and htmx only ever
    swaps that into `#report-box` on a status this app's `htmx-config` lists as swappable (see
    `core/base.html`) -- a bare 404 isn't one of them, so the form used to just silently sit there
    looking like nothing happened. `HX-Refresh` tells htmx to do a full-page reload instead, which
    lands the visitor on this same URL's own GET (`download_page`) -- the ordinary, already-neutral
    "no longer available" page -- rather than trying to render a whole page's worth of markup into
    a swap target sized for a small report form. Non-htmx callers (there shouldn't be any, since
    the report form is only ever submitted via `hx-post`, but this stays a safe fallback) still get
    the plain 404 page.
    """
    if not is_htmx(request):
        return _unavailable(request)
    response = HttpResponse(status=204)
    response['HX-Refresh'] = 'true'
    return response


def _password_gated_transfer(
    request: PublicHttpRequest, slug: str, *, replay_file_id: object = None
) -> Transfer | HttpResponse:
    """Shared prelude for every public per-file/zip download endpoint: look the transfer up,
    check availability, and check the password gate (spec section 3: gates the download buttons,
    not the file list). Returns either the `Transfer` or the response to return instead.

    `replay_file_id` (the per-file download and "View full") also lets through a transfer that is
    no longer available **when this session already counted that file** and the count may itself
    have ended the transfer (`services.is_replayable`); the password and access gates below still
    apply."""
    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not (
        services.is_available(transfer)
        or (
            replay_file_id is not None
            and services.is_replayable(request.session, transfer, replay_file_id)
        )
    ):
        return _unavailable(request)
    if transfer.is_request:
        # A file request (issue #306): the whole access check (password, then audience) gates
        # every download, and only the owner may download while it is still open -- everyone else
        # once it is done. Anything refused goes back to the page, which says what is needed.
        if check_access(transfer, request.user, request.session) != Access.OK or not (
            transfer.is_done or is_owner(transfer, request.user)
        ):
            return redirect(reverse('t:download', args=[slug]))
        return transfer
    if services.requires_password(transfer) and not services.is_unlocked_in_session(
        request.session, transfer.id, transfer.password_hash
    ):
        return redirect(reverse('t:download', args=[slug]))
    return transfer


#: Pixels per module of the page's QR code. The popup shows it much smaller, so scanners and
#: high-density screens get a sharp image.
QR_SCALE = 10

#: Quiet-zone width in modules around the code (the spec's minimum is 4; the popup's white
#: frame supplies the rest, and a narrower border keeps the code larger on screen).
QR_BORDER = 3

#: How many distinct links' rendered codes are kept in memory (a few KB each).
QR_CACHE_SIZE = 256


@lru_cache(maxsize=QR_CACHE_SIZE)
def _qr_data_uri_for(url: str) -> str:
    """The `data:` URI of the PNG QR code of `url`. Cached per link, so the page's repeated GETs
    (and the 422/429 unlock answers) don't encode it again. Keyed on the URL string alone: a link
    never changes what it encodes, so there is nothing to invalidate."""
    png = render_png(url, scale=QR_SCALE, border=QR_BORDER)
    return f'data:image/png;base64,{base64.b64encode(png).decode("ascii")}'


def download_qr_data_uri(transfer: Transfer) -> str:
    """The QR code of the transfer's download link as a `data:` URI of a PNG (issue #267).

    The code encodes the absolute, `BASE_URL`-based link (never anything read from the request), is
    rendered in memory and stored nowhere. A password-protected transfer needs no special case: the
    link it encodes is the page that asks for the password.
    """
    return _qr_data_uri_for(transfer.absolute_download_url)


def _context(request: PublicHttpRequest, transfer: Transfer, password_form: DownloadPasswordForm):
    files = list(transfer.files.filter(uploaded=True).order_by('name'))
    unlocked = not services.requires_password(transfer) or services.is_unlocked_in_session(
        request.session, transfer.id, transfer.password_hash
    )
    return {
        'transfer': transfer,
        'files': files,
        'unlocked': unlocked,
        'password_form': password_form,
        'report_form': AbuseReportForm(),
        'qr_data_uri': download_qr_data_uri(transfer),
        **preview.page_context(transfer, files, unlocked=unlocked),
    }


@require_GET
def download_page(request: PublicHttpRequest, slug: str) -> HttpResponse:
    limited = ratelimit.hit_ip(request, 'FT_DOWNLOAD_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not services.is_available(transfer):
        return _unavailable(request)

    if transfer.is_request:
        return request_public.render_request_page(request, transfer)
    return render(
        request, 'file_transfer/download.html', _context(request, transfer, DownloadPasswordForm())
    )


def _render_unlock(
    request: PublicHttpRequest, transfer: Transfer, form: DownloadPasswordForm, status: int
) -> HttpResponse:
    """The page again with the password form's errors: a request's own page (issue #306), else the
    transfer's download page."""
    if transfer.is_request:
        return request_public.render_request_page(
            request, transfer, password_form=form, status=status
        )
    return render(
        request, 'file_transfer/download.html', _context(request, transfer, form), status=status
    )


@require_POST
def unlock(request: PublicHttpRequest, slug: str) -> HttpResponse:
    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not services.is_available(transfer):
        return _unavailable(request)

    # Per-IP (strict, every attempt counts) and per-transfer (looser, counts only wrong
    # passwords -- issue #53 code review) so brute-forcing one transfer's password is throttled
    # even from many IPs, one IP can't brute-force many transfers unchecked, and a third party who
    # merely knows (or guesses) this transfer's slug can't lock the real recipient out of a
    # transfer whose password *they* actually have just by submitting wrong guesses.
    limited = ratelimit.hit_ip(request, 'FT_UNLOCK_IP')
    if limited.allowed:
        limited = ratelimit.peek_value(transfer.slug, 'FT_UNLOCK_TRANSFER')
    if not limited.allowed:
        # Deliberately unbound (no `data=`): `add_error` needs `cleaned_data` to exist, which an
        # unbound form only gets by priming it directly here rather than binding (and thereby
        # validating) the submitted password -- see the same pattern and reasoning in
        # `apps.accounts.views.login.login_page`. Attached to the `password` field specifically
        # (not a non-field error) because `download.html` only ever renders that field's own
        # errors here, the same slot "Incorrect password" already occupies.
        form = DownloadPasswordForm()
        form.cleaned_data = {}
        form.add_error('password', 'Too many attempts. Please wait a moment and try again.')
        response = _render_unlock(request, transfer, form, 429)
        response['Retry-After'] = str(limited.retry_after)
        return response

    form = DownloadPasswordForm(request.POST)
    if form.is_valid() and services.check_password(transfer, form.cleaned_data['password']):
        services.unlock_in_session(request.session, transfer.id, transfer.password_hash)
        return redirect(reverse('t:download', args=[slug]))

    # Record this as a failed attempt against the looser per-transfer ceiling *after* the check
    # above -- never before it, and never for a right password (see the comment above `hit_ip`).
    ratelimit.hit_value(transfer.slug, 'FT_UNLOCK_TRANSFER')
    form.add_error('password', 'Incorrect password.')
    return _render_unlock(request, transfer, form, 422)


@require_GET
def download_file(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    limited = ratelimit.hit_ip(request, 'FT_DOWNLOAD_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    transfer = _password_gated_transfer(request, slug, replay_file_id=file_id)
    if isinstance(transfer, HttpResponse):
        return transfer

    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer, uploaded=True)
    if services.consume_counted(request.session, transfer.id, file.id):
        # "View full" already counted this file in this session (the browser had no copy to save,
        # or the visitor took the link): serve it again without a second count -- once.
        return redirect(services.download_url(file))
    try:
        url = services.record_download(transfer, file, _client_ip(request))
    except ValidationError:
        return _unavailable(request)
    return redirect(url)


def _preview_headers(response: HttpResponse) -> HttpResponse:
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _preview_gate(
    request: PublicHttpRequest, slug: str, file_id: object
) -> tuple[Transfer, TransferFile] | HttpResponse:
    """The prelude of every preview endpoint: the preview rate limit, then **exactly the gates of
    the download** (`_password_gated_transfer`: the transfer is available, the password was
    entered, a file request's access rules and "owner only until it is done"), then the same
    per-file lookup (`uploaded=True`, this transfer). What differs from a download: a refused
    password / access gate answers a plain 403 instead of a redirect to the page (these responses
    are fetched by an `<img>` or `fetch()`, which would take the whole page), an unavailable
    transfer the neutral 404, and a file with no `preview_type` a 404."""
    limited = ratelimit.hit_ip(request, 'FT_PREVIEW_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    transfer = _password_gated_transfer(request, slug)
    if isinstance(transfer, HttpResponseRedirect):
        return _preview_headers(HttpResponse(status=403))
    if isinstance(transfer, HttpResponse):
        return _preview_headers(transfer)

    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer, uploaded=True)
    if preview.kind_of(file.preview_type) is None:
        return _preview_headers(HttpResponse(status=404))
    return transfer, file


@require_GET
@xframe_options_sameorigin
def preview_file(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    """The sample of a text or Markdown file (issue #311), for the dialog on the download page:
    its first `TEXT_PREVIEW_BYTES` (64 KB) as `text/plain`, or for Markdown that text and its
    sanitised rendering as JSON. **A preview is a sample, never the file**: an image or a PDF has
    no answer here (404) -- its downsized pages are `preview_status` / `preview_page`.

    Gates, and what a preview is not (it is not a download: no `DownloadEvent`, nothing counted
    against `max_downloads`, no notification to the sender), are `_preview_gate`'s. **What the file
    is comes only from `TransferFile.preview_type`**, the type read from its bytes when it was
    uploaded (`services.preview`). Text is read and re-encoded here, never served from the bucket:
    it is answered as `text/plain; charset=utf-8` with `nosniff` and a `sandbox` CSP, so a hostile
    text file (HTML, an SVG) cannot run as a page. The decoded sample is cached per file id.
    """
    gate = _preview_gate(request, slug, file_id)
    if isinstance(gate, HttpResponse):
        return gate
    _, file = gate
    kind = preview.kind_of(file.preview_type)
    if kind not in (preview.PreviewKind.TEXT, preview.PreviewKind.MARKDOWN):
        return _preview_headers(HttpResponse(status=404))

    try:
        sample = preview.text_sample(file, kind, get_storage())
    except Exception:
        logger.warning('Could not read the text preview of file %s', file.pk, exc_info=True)
        return _preview_headers(HttpResponse(status=404))
    if isinstance(sample, dict):
        # Raw text plus the sanitised rendering, as JSON (never an HTML response).
        response = JsonResponse(sample)
    else:
        response = HttpResponse(sample, content_type='text/plain; charset=utf-8')
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return _preview_headers(response)


def _rendition_gate(
    request: PublicHttpRequest, slug: str, file_id: object
) -> tuple[Transfer, TransferFile] | HttpResponse:
    """`_preview_gate` for the endpoints of downsized previews: the file must be an image or a PDF."""
    gate = _preview_gate(request, slug, file_id)
    if isinstance(gate, HttpResponse):
        return gate
    if not renditions.has_renditions(gate[1]):
        return _preview_headers(HttpResponse(status=404))
    return gate


@require_GET
def preview_status(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    """The dialog's first call for an image or a PDF, and its polls (issue #311). With `?start=1`
    (the dialog opening, and its "Try again") it first claims the lazy build and queues it
    (`renditions.ensure_build_started`: a no-op when the pages exist or a build is running); the
    polls leave it out, so a failed build is reported, not silently restarted by every poll.
    Answers JSON `{status, pages, total_pages, page_urls}`: `status` is `preparing`, `ready`,
    `failed`, `unavailable` or `none`; `page_urls` are the downsized pages (empty until `ready`).
    The same gates as the download; not a download."""
    gate = _rendition_gate(request, slug, file_id)
    if isinstance(gate, HttpResponse):
        return gate
    transfer, file = gate
    if request.GET.get('start') == '1':
        renditions.ensure_build_started(file)
    state = renditions.state_of(file)
    if state['status'] == 'ready':
        renditions.touch(file)
    state['page_urls'] = [
        reverse('t:preview-page', args=[transfer.slug, file.id, number])
        for number in range(1, state['pages'] + 1)
    ]
    return _preview_headers(JsonResponse(state))


@require_GET
def preview_page(request: PublicHttpRequest, slug: str, file_id: str, page: int) -> HttpResponse:
    """One downsized page (a WebP) of an image's or a PDF's preview: a 302 to a short-lived
    presigned **inline** URL of the page, never of the file. The same gates as the download; not a
    download. A page outside what was built, or a preview that is not `ready`, is a 404."""
    gate = _rendition_gate(request, slug, file_id)
    if isinstance(gate, HttpResponse):
        return gate
    transfer, file = gate
    if file.preview_status != PreviewStatus.READY or not 1 <= page <= file.preview_pages:
        return _preview_headers(HttpResponse(status=404))
    renditions.touch(file)
    url = get_storage().presigned_get_url(
        renditions.preview_key(transfer.pk, file.pk, page),
        'preview.webp',
        disposition='inline',
        content_type=renditions.CONTENT_TYPE,
    )
    return _preview_headers(redirect(url))


@require_GET
def preview_full(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    """ "View full image" / "View full document" (issue #311): **this counts as a download**, with
    every rule of one (the transfer must be available, the password entered, `max_downloads`
    counted and the transfer ended when it is reached, a `DownloadEvent`, the sender's
    notification: all `services.record_download`), and answers JSON `{url, content_type, name}`
    with a short-lived presigned URL of the file itself, shown inline when its type allows.

    The dialog fetches that URL into a Blob and shows it from there; its Download buttons then
    save the Blob, with no second request, so the whole thing is one download. Where the browser
    can't read the bucket's response (its CORS rules), the dialog shows the URL directly and the
    Download that follows reaches `download_file`, which does not count the file again: this
    session's "View full" is remembered (`services.mark_counted`, one free repeat within
    `COUNTED_WINDOW_SECONDS`). Asking again for a file already counted this way is also free.

    A text file is offered whole only when the sample cut it and it is at most
    `FULL_TEXT_MAX_BYTES`; any other file is a 404 here.
    """
    limited = ratelimit.hit_ip(request, 'FT_DOWNLOAD_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    transfer = _password_gated_transfer(request, slug, replay_file_id=file_id)
    if isinstance(transfer, HttpResponseRedirect):
        return _preview_headers(HttpResponse(status=403))
    if isinstance(transfer, HttpResponse):
        return _preview_headers(transfer)

    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer, uploaded=True)
    kind = preview.kind_of(file.preview_type)
    if kind is None or not preview.full_label(file, kind):
        return _preview_headers(HttpResponse(status=404))

    if services.is_counted(request.session, transfer.id, file.id):
        # Already counted in this session: the same file again is free.
        url = services.download_url(file, inline=True)
    else:
        try:
            url = services.record_download(transfer, file, _client_ip(request), inline=True)
        except ValidationError:
            return _preview_headers(_unavailable(request))
        services.mark_counted(request.session, transfer.id, file.id)
    return _preview_headers(
        JsonResponse({'url': url, 'content_type': file.preview_type, 'name': file.name})
    )


#: How many times the "preparing the zip" partial polls itself automatically before giving up and
#: asking for a manual click instead (spec section 5's polling loop, capped): a build that's still
#: not done after this many ticks is either stuck (see `services.zip`'s stale-`BUILDING` lease) or
#: unusually large, and either way an unattended tab shouldn't keep hammering this endpoint
#: forever.
_MAX_AUTO_ZIP_POLLS = 30

#: The id of the element a "download all" widget swaps itself into: `zip-area` on the download page,
#: `zip-area-<transfer id>` on a dashboard row (several can be open at once, issue #306).
_ZIP_AREA_RE = re.compile(r'zip-area(-[0-9a-f-]{36})?')


@require_GET
def zip_status(request: PublicHttpRequest, slug: str) -> HttpResponse:
    """HTMX partial (spec section 5): kick the lazy zip build off the first time it's asked for,
    then report the current status -- "preparing" (`BUILDING`/`NONE` just claimed),
    "ready" (a download link), or "failed" (with a retry). The template polls this until it stops
    being `BUILDING`, up to `_MAX_AUTO_ZIP_POLLS` times."""
    limited = ratelimit.hit_ip(request, 'FT_DOWNLOAD_IP')
    if not limited.allowed:
        return ratelimit.htmx_response(request, limited)

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

    area = request.GET.get('area', '')
    context = {
        'transfer': transfer,
        'area_id': area if _ZIP_AREA_RE.fullmatch(area) else 'zip-area',
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
    limited = ratelimit.hit_ip(request, 'FT_DOWNLOAD_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

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


@require_POST
def report_transfer(request: PublicHttpRequest, slug: str) -> HttpResponse:
    """ "Report this transfer" (issue #59): an htmx partial on the download page, no login and no
    password required -- anyone who merely has the link can report it. Only for a transfer that's
    currently *available*: an already-unavailable transfer shows a small htmx-friendly notice
    (`_became_unavailable`) rather than the full "no longer available" page every other endpoint
    here returns -- this endpoint is only ever reached via the report form's own `hx-post`, and a
    full page's markup swapped into `#report-box` would just be visual garbage, not a message.

    `FT_REPORT_TRANSFER` (issue #59 code review) is counted only *after* a report is actually
    created -- peeked first, then hit once `services.create_report` succeeds -- rather than on
    every POST: counting it up front let a handful of IPs exhaust a transfer's whole report budget
    with attempts that never even become a report (a bad reason, the dedupe window, ...), locking
    out every genuine reporter behind them. The slug is looked up before this per-transfer key is
    touched at all, so a made-up slug can't be used to create rate-limit keys for transfers that
    don't exist.
    """
    limited = ratelimit.hit_ip(request, 'FT_REPORT_IP')
    if not limited.allowed:
        return ratelimit.htmx_response(request, limited, retarget='#report-box')

    transfer = Transfer.objects.filter(slug=slug).first()
    if transfer is None or not services.is_available(transfer):
        return _became_unavailable(request)

    limited = ratelimit.peek_value(transfer.slug, 'FT_REPORT_TRANSFER')
    if not limited.allowed:
        return ratelimit.htmx_response(request, limited, retarget='#report-box')

    form = AbuseReportForm(request.POST)
    if form.is_valid():
        try:
            services.create_report(
                transfer,
                reason=form.cleaned_data['reason'],
                details=form.cleaned_data['details'],
                reporter_email=form.cleaned_data['reporter_email'],
                reporter_ip=_client_ip(request),
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            # Only a report that was actually created counts against the per-transfer ceiling --
            # see this view's own docstring.
            ratelimit.hit_value(transfer.slug, 'FT_REPORT_TRANSFER')

    if form.errors:
        context = {'transfer': transfer, 'report_form': form}
        return render(request, 'file_transfer/partials/report_box.html', context, status=422)

    context = {'transfer': transfer, 'reported': True}
    return render(request, 'file_transfer/partials/report_box.html', context)
