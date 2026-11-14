"""What the World Time API endpoints share: ownership lookups, the mapping from service errors to
HTTP statuses, the rate limit, and how a location is turned into its JSON shape.

Status codes (documented once, here):

- **400** a `ValidationError` from a service (`field: message`, or the message alone for one that
  belongs to no field: a full tab, the last tab, a place already in the tab), or a query parameter
  read by hand (`date`, `at`). A reference in a body to something that doesn't exist (a `city`
  that is not in the city list, an unknown `zone`) is a 400 too.
- **404** a tab, location or event that isn't the caller's (never 403, so an id can't be probed).
- **422** django-ninja's own validation, before an endpoint runs, with a *list-valued* `detail`: a
  malformed id in the path, a mistyped body field, an unknown key in a body (`extra='forbid'`).
- **429** the site-wide rate limiter (`ratelimit.enforce`), with `Retry-After`. The rules, and so
  the budgets, are the web's: `WT_EDIT_USER` for every change (deleting an event is never limited)
  and `WT_SEARCH_USER` for the search.

World Time is free: nothing here costs credits, so there is no 402.

**Text is the public wording.** The API's dates and times are values (ISO instants, and wall clocks
as `YYYY-MM-DDTHH:MM` with no offset); the one sentence it returns (`dst_note`) is written in the
public wording, whatever the account's date and time formats.
"""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

from django.core.exceptions import ValidationError
from django.http import Http404
from ninja.errors import HttpError

from apps.core import ratelimit

from ..models import Location, Tab


def validation_detail(exc: ValidationError) -> str:
    """One readable line for a `ValidationError`: `field: message` pairs when the service keyed its
    errors by field (a message that belongs to no field is plain), else the first message."""
    if hasattr(exc, 'error_dict'):
        return ' '.join(
            message if field_name == '__all__' else f'{field_name}: {message}'
            for field_name, messages in exc.message_dict.items()
            for message in messages
        )
    return exc.messages[0]


@contextmanager
def mapped_errors() -> Generator[None]:
    """Turn a service's `ValidationError` into a 400 with its message."""
    try:
        yield
    except ValidationError as exc:
        raise HttpError(400, validation_detail(exc)) from exc


def charge(request, rule: str) -> None:
    """Spend one hit of the user's `rule` budget: a 429 when it is over."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, rule))


def owned_tab(request, tab_id: UUID) -> Tab:
    """The caller's tab, or a 404."""
    tab = Tab.objects.select_related('user').filter(user=request.auth, pk=tab_id).first()
    if tab is None:
        raise Http404('Tab not found')
    return tab


def owned_location(tab: Tab, location_id: UUID) -> Location:
    """The location `location_id` of `tab`, or a 404."""
    location = Location.objects.select_related('tab__user').filter(tab=tab, pk=location_id).first()
    if location is None:
        raise Http404('Location not found')
    return location


def location_data(location: Location) -> dict:
    return {
        'id': location.pk,
        'title': location.title,
        'name': location.name,
        'label': location.label,
        'region': location.region,
        'country': location.country,
        'country_code': location.country_code,
        'timezone': location.timezone,
        'geoname_id': location.geoname_id,
        'home': location.is_home,
        'position': location.position,
    }


def wall_clock(moment) -> str:
    """A local datetime as `YYYY-MM-DDTHH:MM`, with no offset: a wall clock, not an instant."""
    return moment.replace(tzinfo=None).isoformat(timespec='minutes')
