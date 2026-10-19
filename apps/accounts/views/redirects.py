"""Redirect helpers shared by the views that finish a sign-in (password form, Google, Microsoft)."""

from django.http import HttpRequest
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def redirect_target(request: HttpRequest, next_url: str | None) -> str:
    """``next_url`` if it points back at this site, else the home page (the landing page, from
    which the products are one click away). Every sign-in -- password, Google, Microsoft -- ends
    here, so they all land in the same place."""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return reverse('core:home')
