"""A user's time zone (issue #177): what "today" and a wall-clock time mean for them.

`User.timezone` is an IANA name (`Europe/Lisbon`) or `''` for "not set yet", which everything here
treats as UTC. The `TimezoneMiddleware` activates the zone for the request, so `{{ value|date }}`
and `localtime()` render in it; the functions below are for code that has to compute with it
(a due date with a time, "today", a reminder instant). The site-wide browser-side helpers
(`core/js/local_time.js`, the `{% local_time %}` tag) are separate and unaffected: they show the
*browser's* zone and always emit UTC.
"""

import logging
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import User
from ..signals import timezone_changed
from ..validators import UNKNOWN_TIMEZONE_MESSAGE, is_valid_timezone, known_timezone_names

logger = logging.getLogger(__name__)

#: Prefixes of legacy or duplicate names in the tz database, left out of the account page's select.
_LEGACY_PREFIXES = (
    'Etc/',
    'SystemV/',
    'US/',
    'posix/',
    'right/',
    # Backward-compatibility links to a zone that has a proper `Area/City` name (`Canada/Eastern`
    # is `America/Toronto`).
    'Brazil/',
    'Canada/',
    'Chile/',
    'Mexico/',
)

#: Zone names already warned about, so a stale value is logged once per process and not per request.
_warned: set[str] = set()


def user_tz(user: User) -> ZoneInfo:
    """The zone `user` lives in: `ZoneInfo(user.timezone)` when set and loadable, else UTC.

    Never raises. A value that no longer loads (a zone removed from the tz database since it was
    saved) falls back to UTC and is logged as a warning, once per process per value.
    """
    name = getattr(user, 'timezone', '')
    if not name:
        return ZoneInfo('UTC')
    try:
        return ZoneInfo(name)
    except ValueError, OSError, LookupError:  # a missing zone is a KeyError, a LookupError
        if name not in _warned:
            _warned.add(name)
            logger.warning('Unknown time zone %r on a user; using UTC.', name)
        return ZoneInfo('UTC')


def user_today(user: User) -> date:
    """Today's date where `user` is."""
    return timezone.now().astimezone(user_tz(user)).date()


def local_datetime(user: User, d: date, t: time | None) -> datetime:
    """The instant of `d` at `t` (midnight when `t` is `None`) on `user`'s wall clock, in UTC.

    A local time can be missing or doubled where the clocks change, and `ZoneInfo`'s default
    `fold=0` (PEP 495) picks, in both cases, the offset in force *before* the change:

    - a time that does not exist (the hour skipped when the clocks go forward) is read with the
      old offset, which lands the same distance after the gap: 01:30 in Lisbon on 2026-03-29,
      which was skipped, is the instant of 02:30 there;
    - a time that happens twice (the hour repeated when the clocks go back) is its first
      occurrence.
    """
    local = datetime.combine(d, t or time(0, 0), tzinfo=user_tz(user))
    return local.astimezone(UTC)


def timezone_choices() -> list[tuple[str, str]]:
    """`(name, label)` pairs for the account page's select: `UTC` first, then every `Area/City`
    zone sorted, minus the legacy names (`Etc/`, `SystemV/`, `US/`, `posix/`, `right/`, the other
    backward-compatibility links, and every bare alias such as `GMT`, `CET`, `EST` or `Japan`).
    The label is the name with `_` shown as a space."""
    names = sorted(
        name
        for name in known_timezone_names()
        if '/' in name and not name.startswith(_LEGACY_PREFIXES)
    )
    return [('UTC', 'UTC')] + [(name, name.replace('_', ' ')) for name in names]


def set_timezone(user: User, name: str, *, only_if_unset: bool = False) -> bool:
    """Save `name` as `user`'s time zone and return whether it changed.

    `name` must be `''` or a known zone, else `ValidationError('Unknown time zone.')`. The stored
    value is read under a row lock, so the answer and the signal's `old` are the database's, not a
    stale instance's, and two racing calls send one signal. When it changed,
    `signals.timezone_changed` is sent once the transaction commits. `user` is updated in place.

    With `only_if_unset` the zone is written only while the stored one is empty, so a value saved
    meanwhile (on the account page) is never overridden: the browser's detection uses it.
    """
    if not is_valid_timezone(name):
        raise ValidationError(UNKNOWN_TIMEZONE_MESSAGE, code='invalid')
    with transaction.atomic():
        old = User.objects.select_for_update().values_list('timezone', flat=True).get(pk=user.pk)
        if name == old or (only_if_unset and old):
            user.timezone = old
            return False
        user.timezone = name
        user.save(update_fields=['timezone', 'updated_at'])
        transaction.on_commit(lambda: _send_changed(user, old, name))
    return True


def _send_changed(user: User, old: str, new: str) -> None:
    """Send `timezone_changed`. The zone is saved by now, so a receiver that fails is logged and
    must not turn the request into an error page or stop the other receivers."""
    for receiver, result in timezone_changed.send_robust(sender=User, user=user, old=old, new=new):
        if isinstance(result, Exception):
            logger.error('timezone_changed receiver %r failed', receiver, exc_info=result)
