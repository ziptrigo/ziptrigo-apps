"""Sign in with an emailed one-time code or link (issue #295; see
`apps.accounts.services.login_otp` for the rules and the reasoning).

Three pages, all for a visitor who is not signed in: ask for a code (`login_code_page`), type it
(`login_code_verify_page`) and the emailed link (`login_link_page`). A success is the same
`django.contrib.auth.login()` the password form ends in (so `user_logged_in` fires), landing on the
same-site `next` or the home page (`redirects.redirect_target`).
"""

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx
from apps.core.services.mask import mask_email
from apps.core.services.notifications import is_safe_url

from ..backends import LOGIN_BACKEND
from ..forms import LoginCodeForm, LoginCodeRequestForm
from ..http import MaybeAuthenticatedHttpRequest
from ..models import User
from ..services import login_otp
from ..services.email_confirmation import format_validity_minutes
from .redirects import redirect_target

#: Where the address typed on the request page waits for the code page (the visitor's own
#: session, so it is only ever shown back to them).
SESSION_EMAIL_KEY = 'login_otp_email'

#: Said for every way a code or link can fail, so it reveals nothing about the account.
INVALID_CODE_MESSAGE = (
    'That code is not valid or has expired. Check it and try again, or request a new code.'
)
INVALID_LINK_MESSAGE = (
    'That sign-in link is not valid or has expired. Request a new code to sign in.'
)


def _next_url(request: MaybeAuthenticatedHttpRequest) -> str:
    """The `next` carried between these pages: kept only when it is a same-site path."""
    value = request.POST.get('next') or request.GET.get('next') or ''
    return value if is_safe_url(value) else ''


def _signed_in_email(request: MaybeAuthenticatedHttpRequest) -> str:
    """The address of the user already signed in on this browser, else `''`."""
    user = request.user
    return user.email if isinstance(user, User) else ''


def _with_next(url: str, next_url: str) -> str:
    return f'{url}?{urlencode({"next": next_url})}' if next_url else url


@require_http_methods(['GET', 'POST'])
def login_code_page(request: MaybeAuthenticatedHttpRequest) -> HttpResponse:
    """Ask for a sign-in code: enter the address, and a code and link are emailed to it when an
    eligible account has it. The answer -- a redirect to the code page -- is the same for every
    address and on every path (nothing is looked up here: the email is queued for all)."""
    next_url = _next_url(request)
    status = 200

    if request.method == 'POST':
        form = LoginCodeRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            limited = login_otp.check_before_request(request, email.lower())
            if not limited.allowed:
                return ratelimit.web_response(request, limited, retarget='#login-code-msg')

            login_otp.request_code(email, next_url)
            request.session[SESSION_EMAIL_KEY] = email
            return hx_redirect(request, _with_next(reverse('accounts:login-code-verify'), next_url))
        status = 422
    else:
        form = LoginCodeRequestForm(initial={'email': request.session.get(SESSION_EMAIL_KEY, '')})

    template = (
        'accounts/partials/login_code_form.html'
        if request.method == 'POST' and is_htmx(request)
        else 'accounts/login_code.html'
    )
    return render(request, template, {'form': form, 'next': next_url}, status=status)


@require_http_methods(['GET', 'POST'])
def login_code_verify_page(request: MaybeAuthenticatedHttpRequest) -> HttpResponse:
    """Type the emailed code. Every failure -- no such account, no code started, wrong, expired,
    used, replaced, burned -- is one generic 422; the limits (`login_otp.check_before_verify`)
    are charged for every address the same, so none of this tells whether an account exists."""
    next_url = _next_url(request)
    status = 200

    if request.method == 'POST':
        form = LoginCodeForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            rate_limit_key = email.lower()
            limited = login_otp.check_before_verify(request, rate_limit_key)
            if not limited.allowed:
                return ratelimit.web_response(request, limited, retarget='#login-code-verify-msg')

            user = login_otp.user_for_code(email, form.cleaned_data['code'])
            if user is not None:
                auth_login(request, user, backend=LOGIN_BACKEND)
                request.session.pop(SESSION_EMAIL_KEY, None)
                return hx_redirect(request, redirect_target(request, next_url))
            login_otp.record_failed_verify(rate_limit_key)
            form.add_error(None, INVALID_CODE_MESSAGE)
        status = 422
    else:
        form = LoginCodeForm(initial={'email': request.session.get(SESSION_EMAIL_KEY, '')})

    template = (
        'accounts/partials/login_code_verify_form.html'
        if request.method == 'POST' and is_htmx(request)
        else 'accounts/login_code_verify.html'
    )
    context = {
        'form': form,
        'next': next_url,
        'validity': format_validity_minutes(settings.LOGIN_OTP_VALIDITY_MINUTES),
        # Signed in already: signing in with a code switches this browser to the code's account.
        'signed_in_email': _signed_in_email(request),
    }
    return render(request, template, context, status=status)


@require_http_methods(['GET', 'POST'])
def login_link_page(request: MaybeAuthenticatedHttpRequest, token: str) -> HttpResponse:
    """The emailed sign-in link: GET shows a CSRF-protected "Sign in" button, only POST signs in.

    Mail security filters and link previewers fetch a link before the recipient clicks it, so a
    GET changes nothing: it only *reads* the row (no write, no attempt) to show the masked address
    the button signs into, and a note when the visitor is signed in as another user. A link that is not valid, expired or already used (it works once, as the code does) is reported
    after the click, by redirecting (post/redirect/get) to the code request page with a message.
    Only the POST is rate limited (`LOGIN_OTP_VERIFY_IP`).
    """
    next_url = _next_url(request)
    if request.method == 'GET':
        # Read-only look at the row (nothing consumed, no attempt counted): shows whose account
        # the button signs into, so a link can't silently switch someone to another account.
        target = login_otp.target_email_for_token(token)
        context: dict[str, object] = {'token': token, 'next': next_url}
        if target is None:
            context['invalid_message'] = INVALID_LINK_MESSAGE
            context['request_url'] = _with_next(reverse('accounts:login-code'), next_url)
        else:
            context['masked_email'] = mask_email(target)
            signed_in = _signed_in_email(request)
            if signed_in and signed_in != target:
                context['signed_in_email'] = signed_in
        return render(request, 'accounts/login_link.html', context)

    limited = ratelimit.hit_ip(request, 'LOGIN_OTP_VERIFY_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    user = login_otp.user_for_token(token)
    if user is None:
        messages.error(request, INVALID_LINK_MESSAGE)
        return redirect(_with_next(reverse('accounts:login-code'), next_url))

    auth_login(request, user, backend=LOGIN_BACKEND)
    request.session.pop(SESSION_EMAIL_KEY, None)
    return redirect(redirect_target(request, next_url))
