from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

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
