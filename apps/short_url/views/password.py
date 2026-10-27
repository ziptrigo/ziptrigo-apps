"""The password page of a protected link (`/s/<code>/unlock/`).

`GET` shows the form; `POST` checks the password. A correct one is remembered in the session for
that link (`services.passwords`) and the visitor is sent through the ordinary `/s/<code>` path,
which then counts the click and redirects. Nothing here says anything about the destination.

Modelled on file transfer's download-password page (`FT_UNLOCK_IP` / `FT_UNLOCK_TRANSFER`), with
the same split: `SU_PASSWORD_IP` is strict and counts every attempt, `SU_PASSWORD_LINK` is looser,
spans every IP and counts only *wrong* passwords (checked with `peek` before the password is
tried, recorded with `hit` after it fails). So someone who merely knows a code can't lock out the
real recipients, yet guessing one password from many addresses is still throttled.
"""

from django import forms
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core import ratelimit

from ..models import ShortLink
from ..services import is_available, passwords
from .public import PublicHttpRequest, find_link, private, unavailable


class UnlockForm(forms.Form):
    password = forms.CharField(
        label='Password', max_length=passwords.MAX_PASSWORD_LENGTH, strip=False
    )


def _page(
    request: PublicHttpRequest,
    link: ShortLink,
    *,
    error: str = '',
    status: int = 200,
    retry_after: int | None = None,
) -> HttpResponse:
    response = render(
        request, 'short_url/password.html', {'link': link, 'error': error}, status=status
    )
    if retry_after is not None:
        response['Retry-After'] = str(retry_after)
    return private(response)


def _continue(link: ShortLink) -> HttpResponse:
    """On to the ordinary redirect, which counts the click."""
    response = HttpResponseRedirect(reverse('s:redirect', args=[link.code]))
    response['Cache-Control'] = 'no-store, private'
    return response


@require_http_methods(['GET', 'HEAD', 'POST'])
def unlock_link(request: PublicHttpRequest, code: str) -> HttpResponse:
    if request.method != 'POST':
        # Charged like a redirect, so made-up codes spend the same budget; never refused (this is
        # only a form).
        ratelimit.hit_ip(request, 'SU_REDIRECT_IP')

    link = find_link(code)
    if link is None or not is_available(link):
        return unavailable(request)
    if not link.password_hash or passwords.is_unlocked(request.session, link):
        return _continue(link)  # nothing to ask (its password was removed, or already entered)

    if request.method != 'POST':
        return _page(request, link)

    # Strict per IP (every attempt) and looser per link (wrong passwords only): see the module
    # docstring.
    limited = ratelimit.hit_ip(request, 'SU_PASSWORD_IP')
    if limited.allowed:
        limited = ratelimit.peek_value(link.code_key, 'SU_PASSWORD_LINK')
    if not limited.allowed:
        return _page(
            request,
            link,
            error='Too many attempts. Please wait a moment and try again.',
            status=429,
            retry_after=limited.retry_after,
        )

    form = UnlockForm(request.POST)
    if form.is_valid() and passwords.check_password(link, form.cleaned_data['password']):
        passwords.unlock(request.session, link)
        return _continue(link)

    # Counted against the per-link ceiling only now, and only for a wrong password.
    ratelimit.hit_value(link.code_key, 'SU_PASSWORD_LINK')
    return _page(request, link, error='Incorrect password.', status=422)
