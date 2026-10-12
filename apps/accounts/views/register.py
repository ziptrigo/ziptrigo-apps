"""The register page (issue #52): a session view following the "HTMX form views" convention
(CLAUDE.md) `login.py` already uses -- CSRF, a Django form, a 422 partial on validation errors.

This is the only way to create an account (issue #117 removed `POST /api/auth/signup`), and it is
behind a Cloudflare Turnstile captcha when one is configured (`apps.accounts.services.captcha`).
"""

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx
from apps.core.services.client_ip import client_ip

from ..forms import RegisterForm
from ..services import captcha, google_oauth
from ..services.signup import EmailAlreadyRegistered, create_account

CAPTCHA_ERROR = 'Please complete the captcha check and try again.'


@require_http_methods(['GET', 'POST'])
def register_page(request: HttpRequest) -> HttpResponse:
    """Show the registration form, and create the account with it.

    Order of checks on a POST, cheapest and least abusable first:

    1. The form (name, email, password). An invalid submission re-renders with 422 and touches
       neither the captcha nor any rate limit (issue #52 code review: a bad password must not cost
       one of only 5 signups/hour per IP).
    2. The captcha, when configured: `SIGNUP_CAPTCHA_IP` bounds how many times one IP can make us
       call Cloudflare, then `captcha.verify`. A failure is a form error (422). An empty token is
       rejected without a call and without charging the rule.
    3. `SIGNUP_IP`, so only a submission a person actually got through counts against it.
    4. `create_account`.

    Every 422 re-renders the form partial with a fresh captcha widget, since a token is single-use
    and the swap replaces the old widget anyway. On success, redirects to the "account created"
    page.
    """
    captcha_enabled = captcha.is_configured()

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        status = 422
        if form.is_valid():
            if captcha_enabled:
                token = request.POST.get(captcha.RESPONSE_FIELD, '')
                if token:
                    limited = ratelimit.hit_ip(request, 'SIGNUP_CAPTCHA_IP')
                    if not limited.allowed:
                        return ratelimit.web_response(request, limited, retarget='#register-msg')
                passed = bool(token) and captcha.verify(token, client_ip(request))
            else:
                passed = True

            if not passed:
                form.add_error(None, CAPTCHA_ERROR)
            else:
                limited = ratelimit.hit_ip(request, 'SIGNUP_IP')
                if not limited.allowed:
                    return ratelimit.web_response(request, limited, retarget='#register-msg')

                try:
                    create_account(
                        name=form.cleaned_data['name'],
                        email=form.cleaned_data['email'],
                        password=form.cleaned_data['password'],
                    )
                except EmailAlreadyRegistered:
                    # The email passed `RegisterForm.clean_email`'s own uniqueness check moments
                    # ago -- this only fires on a genuine race with another signup for the same
                    # address.
                    form.add_error('email', 'An account with this email already exists.')
                else:
                    return hx_redirect(request, reverse('accounts:created'))
    else:
        form = RegisterForm()
        status = 200

    template = (
        'accounts/partials/register_form.html'
        if request.method == 'POST' and is_htmx(request)
        else 'accounts/register.html'
    )
    context = {
        'form': form,
        'google_enabled': google_oauth.is_configured(),
        'turnstile_site_key': settings.TURNSTILE_SITE_KEY if captcha_enabled else '',
        'turnstile_action': captcha.ACTION,
    }
    return render(request, template, context, status=status)
