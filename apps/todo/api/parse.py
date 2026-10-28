"""`POST /api/todo/parse`: the AI reading of a date, time and repeat out of a title, and the same
reading for `POST /tasks` with `parse_title` (issue #183).

The web endpoint (`apps.todo.views.parse`) is the model: the same request to the model, the same
answer, the same two rate limits -- `TODO_PARSE_USER` (per minute) and `TODO_PARSE_USER_DAY` (per
day), both *peeked* first and *hit* only when the model is really called, so a title with no date
in it, an unconfigured site and a busy process spend nothing. Nothing is saved and no credits are
charged.
"""

import logging
from datetime import time

from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today, user_tz
from apps.core import ratelimit
from apps.core.ratelimit import RateLimitResult

from ..schemas import ParseSchema
from ..services import TITLE_MAX, get_account
from ..services.parsing import (
    WEEKDAY_NAMES,
    ParseRateLimited,
    ParseRequest,
    ParseUnavailable,
    PermanentAIError,
    TitleParse,
    parse_title,
)
from .router import router

logger = logging.getLogger(__name__)

#: Per minute, then per day. Both checked together: the stricter one answers.
RULES = ('TODO_PARSE_USER', 'TODO_PARSE_USER_DAY')


def parse_request(user: User, title: str) -> ParseRequest:
    """What the model is asked: the title and the user's own today, weekday, zone, default due time
    and the time now."""
    tz = user_tz(user)
    today = user_today(user)
    now = timezone.now().astimezone(tz)
    return ParseRequest(
        title=title,
        today=today,
        weekday=WEEKDAY_NAMES[today.weekday()],
        timezone=tz.key,
        default_time=get_account(user).default_due_time,
        now_local=time(now.hour, now.minute),
    )


def _refusal(results: list[RateLimitResult]) -> RateLimitResult | None:
    """The refusal to answer with (the one that makes the caller wait longest), or `None`."""
    refused = [result for result in results if not result.allowed]
    return max(refused, key=lambda result: result.retry_after) if refused else None


def read_title(user: User, title: str) -> tuple[TitleParse | None, str]:
    """The AI's reading of `title` for a task being created, with the reason when there is none
    (not configured, busy, failing, over the user's limits, or simply no date in the title): the
    task is then created as typed. Never raises."""

    def charge() -> bool:
        # A list, not a generator: every rule is counted, whichever refuses.
        return all([ratelimit.hit_user(user, rule).allowed for rule in RULES])

    try:
        result = parse_title(parse_request(user, title), charge=charge)
    except PermanentAIError:
        return None, 'The date reader is not available.'
    except ParseRateLimited:
        return None, 'Too many titles were read recently; try again later.'
    except ParseUnavailable:
        return None, 'The date reader did not answer.'
    except Exception:
        logger.exception('Reading the date of a title failed')
        return None, 'The date reader did not answer.'
    if not result.has_date:
        return None, 'No date found in the title.'
    return result, ''


@router.post('/parse', response=dict)
def parse(request, payload: ParseSchema):
    """Read a date, time and repeat out of a title, without creating anything. The answer is the
    web endpoint's:

    - `{"available": true, "has_date": true, "clean_title", "due_date", "due_time", "rrule",
      "rrule_text", "matched_text", "label"}` for a title with a date in it (`clean_title` is the
      title without those words; `label` is the interpretation in words);
    - `{"available": true, "has_date": false, ...}` for one without (`clean_title` is the title);
    - `{"available": true, "has_date": false, "retry": true}` when the call failed or the server
      is busy with another: try again in a minute;
    - `{"available": false}` when the date reader is not configured on this site.

    A 422 for an empty title or one over 500 characters; a 429 over `TODO_PARSE_USER` (per minute)
    or `TODO_PARSE_USER_DAY` (per day). Nothing is saved and no credits are charged.
    """
    title = ' '.join(payload.title.split())
    if not title:
        raise HttpError(422, 'Type a title to read.')
    if len(title) > TITLE_MAX:
        raise HttpError(422, f'The title is too long (at most {TITLE_MAX} characters).')

    user = request.auth
    refused = _refusal([ratelimit.peek(str(user.pk), rule) for rule in RULES])
    if refused is not None:
        ratelimit.enforce(refused)

    spent: list[RateLimitResult] = []

    def charge() -> bool:
        # Both are counted (a list, not `all(generator)`), so a request that finds one spent still
        # counts the other, and a concurrent one that took the last slot since the peek loses here.
        spent[:] = [ratelimit.hit_user(user, rule) for rule in RULES]
        return _refusal(spent) is None

    try:
        result = parse_title(parse_request(user, title), charge=charge)
    except ParseRateLimited:
        refused = _refusal(spent)  # never `None`: `charge` said no
        if refused is not None:
            ratelimit.enforce(refused)
        return {'available': True, 'has_date': False, 'retry': True}
    except PermanentAIError:
        return {'available': False}
    except ParseUnavailable:
        return {'available': True, 'has_date': False, 'retry': True}

    return {
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
