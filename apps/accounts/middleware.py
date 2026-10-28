"""Middleware that makes Django's current time zone the signed-in user's (issue #177)."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from .services.timezones import user_tz


class TimezoneMiddleware:
    """Activate the signed-in user's time zone for the request, UTC (`TIME_ZONE`) for anyone else.

    While it is active, `{{ value|date }}`, `localtime()`, `localdate()`, `make_aware()` and
    `{% now %}` use the user's zone, so every product's timestamps render in local time. It
    deactivates again once the response is built, so a worker thread never carries one user's zone
    into the next request. (A streamed response is read after that and sees UTC.)

    The JWT API has no session, so it stays on `TIME_ZONE`; its values are explicit ISO 8601
    anyway. Must sit after `AuthenticationMiddleware`, which sets `request.user`.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            timezone.activate(user_tz(user))
        else:
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
