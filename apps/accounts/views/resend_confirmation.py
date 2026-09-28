"""The resend-confirmation action on the expired-confirmation-link page (issue #52): session view
covering what `email_confirmation_expired.html` never had a UI for -- previously the only way to
get a fresh confirmation link was `POST /api/auth/resend-confirmation` directly, with no page
wired to it (CLAUDE.md's "HTMX form views" convention).
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.core import ratelimit
from apps.core.htmx import is_htmx

from ..forms import ResendConfirmationForm
from ..services.email_confirmation import get_email_confirmation_service


@require_POST
def resend_confirmation_page(request: HttpRequest) -> HttpResponse:
    """Resend a confirmation email for the submitted address, if it belongs to an unconfirmed
    account -- never reveals whether it does (CLAUDE.md), matching
    `POST /api/auth/resend-confirmation`. Same `RESEND_CONFIRMATION_IP` rate limit as that
    endpoint; the per-email-per-day cap is enforced centrally by
    `apps.core.services.email_verification.start` regardless of which surface calls it.
    """
    limited = ratelimit.hit_ip(request, 'RESEND_CONFIRMATION_IP')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#resend-confirmation-msg')

    form = ResendConfirmationForm(request.POST)
    sent = False
    status = 200
    if form.is_valid():
        get_email_confirmation_service().resend_if_unconfirmed(form.cleaned_data['email'])
        form = ResendConfirmationForm()
        sent = True
    else:
        status = 422

    template = (
        'accounts/partials/resend_confirmation_form.html'
        if is_htmx(request)
        else 'accounts/email_confirmation_expired.html'
    )
    return render(request, template, {'form': form, 'sent': sent}, status=status)
