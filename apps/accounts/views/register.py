"""The register page (issue #52): session view replacing the old `register.html` `fetch()` flow
to `POST /api/auth/signup`, following the "HTMX form views" convention (CLAUDE.md) `login.py`
already uses -- CSRF, a Django form, a 422 partial on validation errors.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import RegisterForm
from ..services.signup import EmailAlreadyRegistered, create_account


@require_http_methods(['GET', 'POST'])
def register_page(request: HttpRequest) -> HttpResponse:
    """Show the registration form, and create the account with it.

    Same `SIGNUP_IP` rate limit as `POST /api/auth/signup` (issue #53), checked only once the
    form has validated -- on the API side, django-ninja's own pydantic validation of `SignupRequest`
    (format, password strength) always runs before the view body's `ratelimit.enforce` call, so a
    request that was never going to succeed doesn't burn the budget; checking here right before
    `create_account` instead of up front mirrors that (issue #52 code review -- checking it before
    `form.is_valid()` meant every rejected submission, e.g. a bad password, cost one of only 5
    signups/hour per IP). Invalid submissions re-render the form with status 422 without touching
    the rate limit at all. On success, redirects to the "account created" page, matching what the
    old fetch-based flow did.
    """
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        status = 422
        if form.is_valid():
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
                # The email passed `RegisterForm.clean_email`'s own uniqueness check moments ago --
                # this only fires on a genuine race with another signup for the same address.
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
    return render(request, template, {'form': form}, status=status)
