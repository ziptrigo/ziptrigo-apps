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
from ..services.existing_account import handle_signup_for_existing_email
from ..services.signup import EmailAlreadyRegistered, create_account

CAPTCHA_ERROR = 'Please complete the captcha check and try again.'


@require_http_methods(['GET', 'POST'])
def register_page(request: HttpRequest) -> HttpResponse:
    """Show the registration form, and create the account with it.

    Order of checks on a POST, cheapest and least abusable first:

    1. The form (name, email format, password). An invalid submission re-renders with 422 and
       touches neither the captcha nor any rate limit (issue #52 code review: a bad password must
       not cost one of only 5 signups/hour per IP). It does *not* say whether the email is taken.
    2. The captcha, when configured (`_check_captcha`). A failure is a form error (422).
    3. `SIGNUP_IP`, so only a submission a person actually got through counts against it.
    4. `create_account`. An already-registered email is *not* reported to the visitor (issue
       #125): the response is the same redirect as for a new account, and the address's owner is
       emailed instead (`handle_signup_for_existing_email`), so the page can't be used to learn
       whether an address has an account (nor, by how long it takes: both paths hash the password
       and queue an email). It is still behind the captcha and `SIGNUP_IP`, so the notice can't be
       triggered in bulk either.

    Every 422 re-renders the form partial with a fresh captcha widget, since a token is single-use
    and the swap replaces the old widget anyway. On success, redirects to the "account created"
    page.
    """
    captcha_enabled = captcha.is_configured()

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        status = 422
        if form.is_valid():
            response = _create(request, form, captcha_enabled)
            if response is not None:
                return response
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


def _create(request: HttpRequest, form: RegisterForm, captcha_enabled: bool) -> HttpResponse | None:
    """Steps 2-4 for a valid form. Returns the response to send (a redirect or a 429), or `None`
    after adding an error to `form`, which the caller re-renders with 422."""
    if captcha_enabled:
        outcome = _check_captcha(request)
        if isinstance(outcome, HttpResponse):
            return outcome
        if not outcome:
            form.add_error(None, CAPTCHA_ERROR)
            return None

    limited = ratelimit.hit_ip(request, 'SIGNUP_IP')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#register-msg')

    already_registered: str | None = None
    try:
        create_account(
            name=form.cleaned_data['name'],
            email=form.cleaned_data['email'],
            password=form.cleaned_data['password'],
        )
    except EmailAlreadyRegistered as exc:
        already_registered = exc.email
    if already_registered is not None:
        # Same status, redirect and page as a new account -- see the docstring above. Handled
        # outside the `except` block so that anything logged there doesn't chain this exception
        # (which carries the address) into its traceback. The password is passed only so it gets
        # hashed like a new account's (timing); it is never stored.
        handle_signup_for_existing_email(already_registered, form.cleaned_data['password'])
    return hx_redirect(request, reverse('accounts:created'))


def _check_captcha(request: HttpRequest) -> bool | HttpResponse:
    """Whether the submitted Turnstile token passes, or a 429 response when this IP has made us
    call Cloudflare too often (`SIGNUP_CAPTCHA_IP`). An empty token fails without a call and
    without charging the rule."""
    token = request.POST.get(captcha.RESPONSE_FIELD, '')
    if not token:
        return False
    limited = ratelimit.hit_ip(request, 'SIGNUP_CAPTCHA_IP')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#register-msg')
    return captcha.verify(token, client_ip(request))
