"""The forgot-password page (issue #52): session view replacing the old `forgot_password.html`
`fetch()` flow to `POST /api/auth/forgot-password` (CLAUDE.md's "HTMX form views" convention).
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from apps.core import ratelimit
from apps.core.htmx import is_htmx

from ..forms import ForgotPasswordForm
from ..services.password_reset import get_password_reset_service


@require_http_methods(['GET', 'POST'])
def forgot_password_page(request: HttpRequest) -> HttpResponse:
    """Show the forgot-password form, and start the reset flow with it.

    Same three rate-limit rules as `POST /api/auth/forgot-password` (issue #53) -- `FORGOT_PASSWORD_IP`
    first, then, once the email itself is at least well-formed, the strict per-(email, IP)
    `FORGOT_PASSWORD_EMAIL_IP` and the looser, cross-IP `FORGOT_PASSWORD_EMAIL`. The response is
    identical whether or not the account exists, on every path including a 429 -- CLAUDE.md: this
    page must never leak account existence.
    """
    sent = False
    status = 200

    if request.method == 'POST':
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            # Rate-limit keys are case-insensitively normalised (like `routers.auth.forgot_password`
            # does), but the service itself is called with the address exactly as submitted, since
            # `User.email` lookups are case-sensitive.
            rate_limit_key = email.lower()
            limited = ratelimit.hit_ip(request, 'FORGOT_PASSWORD_IP')
            if limited.allowed:
                limited = ratelimit.hit_ip_and_value(
                    request, rate_limit_key, 'FORGOT_PASSWORD_EMAIL_IP'
                )
            if limited.allowed:
                limited = ratelimit.hit_value(rate_limit_key, 'FORGOT_PASSWORD_EMAIL')
            if not limited.allowed:
                return ratelimit.web_response(request, limited, retarget='#forgot-password-msg')

            get_password_reset_service().request_reset(email=email)
            form = ForgotPasswordForm()
            sent = True
        else:
            status = 422
    else:
        form = ForgotPasswordForm()

    template = (
        'accounts/partials/forgot_password_form.html'
        if request.method == 'POST' and is_htmx(request)
        else 'accounts/forgot_password.html'
    )
    return render(request, template, {'form': form, 'sent': sent}, status=status)
