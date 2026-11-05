"""How the site writes dates and times for a user (issue #245).

Stored on `User.date_format` (`mdy` or `dmy`) and `User.time_format` (`12h` or `24h`) rather than
in the browser, so the choice follows the account across devices. `apps.core.datetime_format`
does the formatting; `accounts.middleware.TimezoneMiddleware` activates a signed-in user's choice
for the request.
"""

from ..models import User

DATE_FORMATS = frozenset(value for value, _label in User.DATE_FORMAT_CHOICES)
TIME_FORMATS = frozenset(value for value, _label in User.TIME_FORMAT_CHOICES)


def set_display_formats(
    user: User, *, date_format: str | None = None, time_format: str | None = None
) -> bool:
    """Save `date_format` and/or `time_format` for `user`; `None` leaves a field as it is.

    Returns whether anything changed; writes (only the changed columns, plus `updated_at`) only
    then. The caller has validated the values (`apps.accounts.forms.DisplayFormatsForm`); anything
    else is a bug and raises `ValueError` rather than being stored, since a bad value would only
    be caught at render time.
    """
    if date_format is not None and date_format not in DATE_FORMATS:
        raise ValueError(f'Unknown date format: {date_format!r}')
    if time_format is not None and time_format not in TIME_FORMATS:
        raise ValueError(f'Unknown time format: {time_format!r}')
    changed = []
    if date_format is not None and date_format != user.date_format:
        user.date_format = date_format
        changed.append('date_format')
    if time_format is not None and time_format != user.time_format:
        user.time_format = time_format
        changed.append('time_format')
    if changed:
        user.save(update_fields=[*changed, 'updated_at'])
    return bool(changed)
