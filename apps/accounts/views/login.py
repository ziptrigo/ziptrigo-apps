from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import LoginForm
from ..services import login_throttle


def _redirect_target(request: HttpRequest, next_url: str | None) -> str:
    """``next_url`` if it points back at this site, else the account page."""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return reverse('accounts:account')


@require_http_methods(['GET', 'POST'])
def login_page(request: HttpRequest) -> HttpResponse:
    """Show the login form, and log in with it (a Django session; no JWTs involved).

    Goes to the `next` URL afterwards when it's on this site, otherwise to the account page.
    Invalid submissions re-render the form with status 422.
    """
    next_url = request.POST.get('next') or request.GET.get('next')

    if request.method == 'POST':
        # Per-IP, strict per-(account, IP), and a looser failed-attempts-only per-account ceiling
        # (issue #53 code review; see `apps.accounts.services.login_throttle`'s module docstring
        # for the full reasoning) -- none of these are a hard lockout: a third party who merely
        # knows a victim's email can never lock them out of logging in with their own, correct
        # password just by submitting it.
        submitted_email = (request.POST.get('email') or '').strip().lower()
        limited = ratelimit.hit_ip(request, 'LOGIN_IP')
        if limited.allowed:
            limited = login_throttle.check_before_authenticate(request, submitted_email)
        if not limited.allowed:
            # Deliberately *unbound* (`initial=`, not `data=`) -- a rate-limited request must
            # never still run `LoginForm.clean()`/`authenticate()`, only redisplay what was
            # typed. `add_error(None, ...)` needs `self.cleaned_data` to exist, which unbound
            # forms normally only get from a full `full_clean()`; primed here directly instead of
            # binding real POST data through the form (which would also invite a stray "this
            # field is required" on `password` from validating fields we don't actually care
            # about for this response).
            form = LoginForm(request, initial={'email': request.POST.get('email', '')})
            form.cleaned_data = {}
            form.add_error(None, 'Too many login attempts. Please wait a moment and try again.')
            template = (
                'accounts/partials/login_form.html' if is_htmx(request) else 'accounts/login.html'
            )
            response = render(request, template, {'form': form, 'next': next_url}, status=429)
            response['Retry-After'] = str(limited.retry_after)
            return response

        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            auth_login(request, form.get_user())
            return hx_redirect(request, _redirect_target(request, next_url))
        if form.credentials_invalid and submitted_email:
            login_throttle.record_failed_attempt(submitted_email)
        status = 422
    else:
        form = LoginForm(request)
        status = 200

    template = (
        'accounts/partials/login_form.html'
        if request.method == 'POST' and is_htmx(request)
        else 'accounts/login.html'
    )
    return render(request, template, {'form': form, 'next': next_url}, status=status)


@require_POST
def logout_page(request: HttpRequest) -> HttpResponse:
    """Log out the current user and redirect to the homepage.

    POST-only, so another site can't log users out with a link or an image.
    """
    auth_logout(request)
    return redirect('core:home')
