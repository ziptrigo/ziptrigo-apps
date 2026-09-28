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
        # Per-IP and per-account (issue #53): throttles both scripted credential stuffing from
        # one IP and a targeted attack on one victim's email from many -- see
        # `settings.RATELIMIT_RULES['LOGIN_IP']`/`['LOGIN_ACCOUNT']` for why it's throttling
        # rather than a hard lockout (a lockout a third party could trigger just by submitting a
        # known email would be a denial-of-service against the real owner).
        limited = ratelimit.hit_ip(request, 'LOGIN_IP')
        if limited.allowed:
            submitted_email = (request.POST.get('email') or '').strip().lower()
            limited = ratelimit.hit_value(submitted_email, 'LOGIN_ACCOUNT')
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
