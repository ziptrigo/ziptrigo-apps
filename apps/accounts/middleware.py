"""Middleware that makes Django's current time zone, and the site's date and time formats, the
signed-in user's (issues #177 and #245)."""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.core import datetime_format

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
