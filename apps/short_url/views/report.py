"""Report a link (`POST /s/<code>/report`): the form on the preview page. No login and no
password: anyone who merely has the link can report it, a protected link included (the form never
reveals its destination). Only for a link that is currently *available*.

Modelled on file transfer's `report_transfer`. `SU_REPORT_IP` is strict and counts every
submission; `SU_REPORT_LINK` is a looser per-link ceiling that is *peeked* first and *hit* only
once a report was actually created, because counting it up front would let a few addresses spend a
link's whole budget on attempts that never become a report (a missing reason, the dedupe window)
and lock every genuine reporter out behind them. The link is looked up before that per-link key
is touched, so a made-up code can't create rate-limit keys for links that don't exist.

An htmx request gets the report box back (a 422 with the errors, or the thank-you); a plain POST
gets the whole preview page in the same state, so it works without JavaScript.
"""

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.core import ratelimit
from apps.core.htmx import is_htmx
from apps.core.services.client_ip import client_ip

from ..forms import ReportForm
from ..models import ShortLink
from ..services import is_available, reports
from .preview import render_preview
from .public import PublicHttpRequest, find_link, private, unavailable


def _became_unavailable(request: PublicHttpRequest) -> HttpResponse:
    """An htmx POST can't swap the full unavailable page into a box sized for a small form (and
    htmx doesn't swap a 404), so it reloads the page, which then renders the neutral answer."""
    if not is_htmx(request):
        return unavailable(request)
    response = HttpResponse(status=204)
    response['HX-Refresh'] = 'true'
    response['Cache-Control'] = 'no-store, private'
    return response


def _answer(
    request: PublicHttpRequest,
    link: ShortLink,
    *,
    form: ReportForm | None = None,
    reported: bool = False,
    status: int = 200,
) -> HttpResponse:
    if not is_htmx(request):
        return render_preview(request, link, report_form=form, reported=reported, status=status)
    response = render(
        request,
        'short_url/partials/report_box.html',
        {'link': link, 'report_form': form or ReportForm(), 'reported': reported},
        status=status,
    )
    return private(response)


@require_POST
def report_link(request: PublicHttpRequest, code: str) -> HttpResponse:
    limited = ratelimit.hit_ip(request, 'SU_REPORT_IP')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#report-box')

    link = find_link(code)
    if link is None or not is_available(link):
        return _became_unavailable(request)

    limited = ratelimit.peek_value(link.code_key, 'SU_REPORT_LINK')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#report-box')

    form = ReportForm(request.POST)
    if form.is_valid():
        try:
            reports.create_report(
                link,
                reason=form.cleaned_data['reason'],
                details=form.cleaned_data['details'],
                reporter_email=form.cleaned_data['reporter_email'],
                reporter_ip=client_ip(request),
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            # Only a report that was actually created counts against the per-link ceiling.
            ratelimit.hit_value(link.code_key, 'SU_REPORT_LINK')
            return _answer(request, link, reported=True)
    return _answer(request, link, form=form, status=422)
