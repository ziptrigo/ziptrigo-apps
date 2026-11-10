"""Middleware that makes Django's current time zone, and the site's date and time formats, the
signed-in user's (issues #177 and #245), and the one that asks a signed-in user to accept the
current terms (issue #96)."""

from collections.abc import Callable
from functools import cache
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from apps.core import datetime_format
from apps.core.htmx import is_htmx

from .services import terms
from .services.timezones import user_tz


class TimezoneMiddleware:
    """Activate the signed-in user's time zone and display formats for the request, UTC
    (`TIME_ZONE`) and the fixed public wording (`datetime_format.PUBLIC`) for anyone else.

    While the zone is active, `{{ value|date }}`, `localtime()`, `localdate()`, `make_aware()` and
    `{% now %}` use the user's zone, so every product's timestamps render in local time; while the
    formats are active, `{{ value|fdate }}` and friends write the user's date order and clock. Both
    deactivate again once the response is built, so a worker thread never carries one user's zone
    or formats into the next request. (A streamed response is read after that and sees UTC and the
    public wording.)

    The JWT API has no session, so it stays on `TIME_ZONE` and `PUBLIC`; its values are explicit
    ISO 8601 anyway. Must sit after `AuthenticationMiddleware`, which sets `request.user`.

    Reading `request.user` loads the session and the user, so a path under one of
    `settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES` (the public redirects, downloads, static files and
    the API) never touches it and stays on `TIME_ZONE` and `PUBLIC`: that is how a public page
    shows `Nov 5, 2026` to everyone, a signed-in owner included.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        prefixes = tuple(getattr(settings, 'TIMEZONE_MIDDLEWARE_SKIP_PREFIXES', ()))
        if prefixes and request.path.startswith(prefixes):
            timezone.deactivate()
            datetime_format.deactivate()
            return self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            timezone.activate(user_tz(user))
            datetime_format.activate(datetime_format.formats_for(user))
        else:
            timezone.deactivate()
            datetime_format.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
            datetime_format.deactivate()


#: Paths the terms gate never touches, besides `settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES` (the
#: public links, static files, the JWT API, the service worker): the admin (staff must never be
#: locked out of it) and the end-to-end test helpers.
TERMS_GATE_EXEMPT_PREFIXES = ('/admin/', '/__e2e__/')


@cache
def _terms_gate_open_paths() -> frozenset[str]:
    """Pages a user with acceptance pending can still open: the accept page itself, sign-out, the
    two documents, and the browser's time zone detection (a background POST on every first page of
    a session, which a redirect would break for no benefit)."""
    return frozenset(
        reverse(name)
        for name in (
            'accounts:terms-accept',
            'accounts:logout',
            'accounts:timezone',
            'core:privacy',
            'core:terms',
        )
    )


class TermsAcceptanceMiddleware:
    """Send a signed-in user who has not accepted the current Terms of Use and Privacy Policy to
    the accept page (issue #96), whatever they open, until they accept.

    That covers an account made by "Sign up with Google/Microsoft" (which never sees the sign-up
    form's checkbox), accounts older than the feature and every account after
    `apps.core.legal.TERMS_VERSION` is bumped. Web sessions only: `/api/` (JWT) is skipped, and so
    are the public pages a visitor reaches (`/go/`, `/s/`, `/t/`, the two documents).

    A page request is redirected with `next` set to where it was going (a GET only). An htmx
    request gets an empty 204 with `HX-Redirect`, so the accept page is never swapped into a list;
    when it comes *from* the accept page (the header's notification bell polling) the 204 carries
    no redirect, which would reload the page under the user. Must sit after
    `AuthenticationMiddleware`.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path
        skip = tuple(getattr(settings, 'TIMEZONE_MIDDLEWARE_SKIP_PREFIXES', ()))
        if path.startswith(skip + TERMS_GATE_EXEMPT_PREFIXES) or path in _terms_gate_open_paths():
            return self.get_response(request)
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated or not terms.needs_acceptance(user):
            return self.get_response(request)

        accept_url = reverse('accounts:terms-accept')
        if is_htmx(request):
            answer = HttpResponse(status=204)
            if urlsplit(request.headers.get('HX-Current-URL', '')).path != accept_url:
                answer['HX-Redirect'] = accept_url
            return answer
        if request.method == 'GET':
            return redirect(f'{accept_url}?{urlencode({"next": request.get_full_path()})}')
        return redirect(accept_url)
