"""The shared QR code pages (issue #5): `/q/<code>` shows a QR code to anyone who has the link and
`/q/<code>/image` is its PNG (`?download=1` makes it an attachment). Read-only, no login, apart from
the "report this QR code" form (`POST /q/<code>/report`, `report_qrcode`).

**The code is the capability**, so every answer is `Cache-Control: no-store, private`,
`Referrer-Policy: strict-origin` and `X-Robots-Tag: noindex, nofollow` (and the page carries a
`noindex` meta), as the shared note pages are (`strict-origin`, not `no-referrer`: with
`no-referrer` a browser sends `Origin: null` on a same-origin form POST, which Django's CSRF check
refuses, and the report form is such a POST). **Every link that doesn't work** (an unknown
code, a revoked or expired link, a deleted QR code) renders the same standalone
`qr_code/unavailable.html` with status 404: it never says why. These paths are in
`TIMEZONE_MIDDLEWARE_SKIP_PREFIXES`, so the terms gate leaves them alone and they render in UTC and
the public date wording; the visitor may have no account at all.

`QR_SHARE_VIEW_IP` is charged before the lookup on the page, and `QR_SHARE_IMAGE_IP` (looser: a page
view loads the image, and a download is a second request) on the image and its download; made-up
codes spend them too, and over either a plain 429 page. The image is the one the owner has, so a tracked code still encodes `/go/<code>`
and a scan of a shared copy counts in `scan_count` as usual.
"""

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from apps.core import ratelimit
from apps.core.htmx import is_htmx
from apps.core.services.client_ip import client_ip

from ..forms import ReportForm
from ..models import QRCodeType, QRShareLink
from ..services import LinkUnavailableError, QRCodeGenerator, share, share_reports


def private(response: HttpResponse) -> HttpResponse:
    """Mark an answer about one shared QR code as not cacheable, not to be sent as a referrer
    beyond the site's origin, and not for search engines: the code in the address is the
    capability."""
    response['Cache-Control'] = 'no-store, private'
    response['Referrer-Policy'] = 'strict-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def unavailable(request: HttpRequest) -> HttpResponse:
    """The one answer for every link that doesn't work, whatever the reason: a neutral 404 that
    never says why. Rendered from no context at all (standalone), so a made-up code costs no
    session or user query and the bodies of every case are byte for byte the same."""
    return private(HttpResponse(render_to_string('qr_code/unavailable.html'), status=404))


def _limited(request: HttpRequest, rule: str) -> HttpResponse | None:
    """The plain 429 page when this IP is over `rule` (charged before the lookup)."""
    result = ratelimit.hit_ip(request, rule)
    if result.allowed:
        return None
    return private(ratelimit.page_response(request, result))


def _link_for(code: str) -> QRShareLink | None:
    """The link that works now (`share.get_available`)."""
    return share.get_available(code)


def _became_unavailable(request: HttpRequest) -> HttpResponse:
    """For a POST: an htmx request can't swap a full page into a box sized for a form (and htmx
    doesn't swap a 404), so it reloads the page, which then renders the neutral answer."""
    if not is_htmx(request):
        return unavailable(request)
    response = HttpResponse(status=204)
    response['HX-Refresh'] = 'true'
    return private(response)


def render_page(
    request: HttpRequest,
    link: QRShareLink,
    *,
    report_form: ReportForm | None = None,
    reported: bool = False,
    status: int = 200,
) -> HttpResponse:
    """The shared page of a link that works, with the report box in the given state."""
    qrcode = link.qrcode
    is_url = qrcode.qr_type == QRCodeType.URL
    context = {
        'qrcode': qrcode,
        'link': link,
        'is_url': is_url,
        # Where a URL code leads: the destination a tracked code redirects to, else its content.
        'shown_text': (qrcode.original_url or qrcode.content) if is_url else qrcode.content,
        'report_form': report_form or ReportForm(),
        'reported': reported,
        'report_open': reported or report_form is not None,
    }
    return private(render(request, 'qr_code/shared.html', context, status=status))


@require_GET
def shared_page(request: HttpRequest, code: str) -> HttpResponse:
    if (response := _limited(request, 'QR_SHARE_VIEW_IP')) is not None:
        return response
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    return render_page(request, link)


@require_GET
def shared_image(request: HttpRequest, code: str) -> HttpResponse:
    """The PNG, rendered on demand from the QR code's own settings. With `?download=1` an
    attachment named after the code."""
    if (response := _limited(request, 'QR_SHARE_IMAGE_IP')) is not None:
        return response
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    qrcode = link.qrcode
    response = HttpResponse(QRCodeGenerator.render_png(qrcode), content_type='image/png')
    response['X-Content-Type-Options'] = 'nosniff'
    if request.GET.get('download'):
        response['Content-Disposition'] = (
            f'attachment; filename="{slugify(qrcode.name) or "qr-code"}.png"'
        )
    return private(response)


# -- Reports -----------------------------------------------------------------------------------


def _report_answer(
    request: HttpRequest,
    link: QRShareLink,
    *,
    form: ReportForm | None = None,
    reported: bool = False,
    status: int = 200,
) -> HttpResponse:
    if not is_htmx(request):
        return render_page(request, link, report_form=form, reported=reported, status=status)
    response = render(
        request,
        'qr_code/partials/report_box.html',
        {'link': link, 'report_form': form or ReportForm(), 'reported': reported},
        status=status,
    )
    return private(response)


@require_POST
def report_qrcode(request: HttpRequest, code: str) -> HttpResponse:
    """Report a shared QR code (`POST /q/<code>/report`): no login, no password -- anyone who has
    the link can. Only for a link that works now.

    `QR_SHARE_REPORT_IP` is strict and counts every submission; `QR_SHARE_REPORT_CODE` is a looser
    per-QR-code ceiling that is *peeked* first and *hit* only once a report was actually created
    (counting it up front would let a few addresses spend a code's whole budget on attempts that
    never become a report and lock every genuine reporter out behind them). The link is looked up
    before that per-code key is touched, so a made-up code creates none. An htmx request gets the
    report box back (a 422 with the errors, or the thank-you); a plain POST gets the whole page in
    the same state, so it works without JavaScript.
    """
    limited = ratelimit.hit_ip(request, 'QR_SHARE_REPORT_IP')
    if not limited.allowed:
        return private(ratelimit.web_response(request, limited, retarget='#report-msg'))

    link = _link_for(code)
    if link is None:
        return _became_unavailable(request)

    code_key = str(link.qrcode_id)
    limited = ratelimit.peek_value(code_key, 'QR_SHARE_REPORT_CODE')
    if not limited.allowed:
        return private(ratelimit.web_response(request, limited, retarget='#report-msg'))

    form = ReportForm(request.POST)
    if form.is_valid():
        try:
            share_reports.create_report(
                link,
                reason=form.cleaned_data['reason'],
                details=form.cleaned_data['details'],
                reporter_email=form.cleaned_data['reporter_email'],
                reporter_ip=client_ip(request),
            )
        except LinkUnavailableError:
            return _became_unavailable(request)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            # Only a report that was actually created counts against the per-code ceiling.
            ratelimit.hit_value(code_key, 'QR_SHARE_REPORT_CODE')
            return _report_answer(request, link, reported=True)
    return _report_answer(request, link, form=form, status=422)
