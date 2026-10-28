"""`POST /todo/parse/`: the AI reading of a date out of a title (issue #183).

JSON in and out, like the upload views of Receipts and file transfer (a script posts it, there is
no form to re-render). Answers:

- 200 `{"available": true, "has_date": ..., "clean_title": ..., "due_date", "due_time", "rrule",
  "rrule_text", "matched_text", "label"}`: `label` is the chip's text;
- 200 `{"available": false}` when the AI is not configured or the call failed: the page stops
  asking for the rest of its load;
- 422 `{"detail": ...}` for a title that is empty or over `TITLE_MAX`;
- 429 (`TODO_PARSE_USER`, charged before the AI is called).

The user's own local date, time zone and default due time go to the model; nothing is saved and
nothing is charged.
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

from ..services import TITLE_MAX, get_account
from ..services.parsing import (
    WEEKDAY_NAMES,
    ParseRequest,
    ParseUnavailable,
    PermanentAIError,
    parse_title,
)


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

    # Charged before the AI is called, and for every valid request: a site without the AI
    # answers "unavailable" once and the page stops asking, so there is no budget worth saving.
    limited = ratelimit.hit_user(user, 'TODO_PARSE_USER')
    if not limited.allowed:
        return ratelimit.json_response(limited)

    try:
        result = parse_title(ask)
    except PermanentAIError, ParseUnavailable:
        return JsonResponse({'available': False})

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
