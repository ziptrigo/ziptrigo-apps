"""The browser's automatic time zone detection (issue #177)."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

from apps.core import ratelimit

from ..http import AuthenticatedHttpRequest
from ..services.timezones import set_timezone
from ..validators import UNKNOWN_TIMEZONE_MESSAGE, is_valid_timezone


@login_required
@require_POST
def timezone_detect(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Store the zone the browser reported, but only for an account that has none yet.

    `accounts/js/timezone.js` posts `timezone=<IANA name>` (form-encoded, with the CSRF token) once
    per browser session while `User.timezone` is empty. A saved zone is never changed here -- only
    on the account page -- so a script, a stale tab or a travelling laptop can't override a choice.
    Answers 204 either way (the page needs nothing back), 400 `{"detail": "Unknown time zone."}`
    for a name that isn't a zone (an empty one included), and a JSON 429 over
    `ACCOUNT_TIMEZONE_USER`, which every call counts against.
    """
    limited = ratelimit.hit_user(request.user, 'ACCOUNT_TIMEZONE_USER')
    if not limited.allowed:
        return ratelimit.json_response(limited)

    name = request.POST.get('timezone', '').strip()
    if not name or not is_valid_timezone(name):
        return JsonResponse({'detail': UNKNOWN_TIMEZONE_MESSAGE}, status=400)
    if not request.user.timezone:
        # `only_if_unset` makes the write conditional on the stored value too, not just on the
        # one this request loaded.
        set_timezone(request.user, name, only_if_unset=True)
    return HttpResponse(status=204)
