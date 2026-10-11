"""Redirect helpers shared by the views that finish a sign-in (password form, Google)."""

from django.http import HttpRequest
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def redirect_target(request: HttpRequest, next_url: str | None) -> str:
    """``next_url`` if it points back at this site, else the account page."""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return reverse('accounts:account')
