"""`POST /todo/parse/`: the AI reading of a date out of a title (issue #183).

JSON in and out, like the upload views of Receipts and file transfer (a script posts it, there is
no form to re-render). Answers:

- 200 `{"available": true, "has_date": ..., "clean_title": ..., "due_date", "due_time", "rrule",
  "rrule_text", "matched_text", "label"}`: `label` is the chip's text;
- 200 `{"available": false}` when the AI is not configured: the page stops asking for the rest of
  its load;
- 200 `{"available": true, "has_date": false, "retry": true}` when this call failed or the process
  is busy with another (`parsing.ParseBusy`): no suggestion, and the page backs off for a minute
  before asking again;
- 422 `{"detail": ...}` for a title that is empty or over `TITLE_MAX`;
- 429 over `TODO_PARSE_USER` (per minute) or `TODO_PARSE_USER_DAY` (per day). Both are *peeked*
  first and *hit* only when the model is really called, so a title with no date in it, an
  unconfigured site and a busy process spend nothing.

The user's own local date, time zone and default due time go to the model; nothing is saved and
no credits are charged.
"""

import json
from datetime import time

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today, user_tz
from apps.core import ratelimit
from apps.core.ratelimit import RateLimitResult

from ..services import TITLE_MAX, get_account
from ..services.parsing import (
    WEEKDAY_NAMES,
    ParseRateLimited,
    ParseRequest,
    ParseUnavailable,
    PermanentAIError,
    parse_title,
)

#: Per minute, then per day. Both checked together: the stricter one answers.
RULES = ('TODO_PARSE_USER', 'TODO_PARSE_USER_DAY')


def _refusal(results: list[RateLimitResult]) -> RateLimitResult | None:
    """The refusal to answer with (the one that makes the caller wait longest), or `None`."""
    refused = [result for result in results if not result.allowed]
    return max(refused, key=lambda result: result.retry_after) if refused else None


def _body(request: HttpRequest) -> dict:
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError, UnicodeDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _problem(message: str) -> JsonResponse:
    return JsonResponse({'detail': message}, status=422)


@login_required
@require_POST
def parse_view(request: AuthenticatedHttpRequest) -> HttpResponse:
    raw = _body(request).get('title')
    title = ' '.join(raw.split()) if isinstance(raw, str) else ''
    if not title:
        return _problem('Type a title to read.')
    if len(title) > TITLE_MAX:
        return _problem(f'The title is too long (at most {TITLE_MAX} characters).')

    user = request.user
    tz = user_tz(user)
    today = user_today(user)
    now = timezone.now().astimezone(tz)
    ask = ParseRequest(
        title=title,
        today=today,
        weekday=WEEKDAY_NAMES[today.weekday()],
        timezone=tz.key,
        default_time=get_account(user).default_due_time,
        now_local=time(now.hour, now.minute),
    )

    # Over a limit already: stop here. Nothing is counted yet -- the limits are spent in `charge`,
    # right before the model is called, so a title with no date in it costs nothing.
    refused = _refusal([ratelimit.peek(str(user.pk), rule) for rule in RULES])
    if refused is not None:
        return ratelimit.json_response(refused)

    spent: list[RateLimitResult] = []

    def charge() -> bool:
        # Both are counted (a list, not `all(generator)`), so a request that finds one spent still
        # counts the other, and a concurrent one that took the last slot since the peek loses here.
        spent[:] = [ratelimit.hit_user(user, rule) for rule in RULES]
        return _refusal(spent) is None

    try:
        result = parse_title(ask, charge=charge)
    except ParseRateLimited:
        refused = _refusal(spent)  # never `None`: `charge` said no
        if refused is not None:
            return ratelimit.json_response(refused)
        return JsonResponse({'available': True, 'has_date': False, 'retry': True})
    except PermanentAIError:
        return JsonResponse({'available': False})
    except ParseUnavailable:
        return JsonResponse({'available': True, 'has_date': False, 'retry': True})

    return JsonResponse(
        {
            'available': True,
            'has_date': result.has_date,
            'clean_title': result.clean_title,
            'due_date': result.due_date.isoformat() if result.due_date else None,
            'due_time': f'{result.due_time:%H:%M}' if result.due_time else None,
            'rrule': result.rrule,
            'rrule_text': result.rrule_text,
            'matched_text': result.matched_text,
            'label': result.label,
        }
    )
