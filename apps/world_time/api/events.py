"""The shared event endpoints (`/api/time/events`): a time range with a public page. Each calls the
service the web pages use. Creating and changing spend `WT_EDIT_USER`, after the checks that need no
write; deleting is never rate limited (it only takes something off the web)."""

from datetime import timedelta
from uuid import UUID

from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils import timezone

from ..models import Event
from ..schemas import (
    WorldTimeEventCreateSchema,
    WorldTimeEventSchema,
    WorldTimeEventUpdateSchema,
)
from ..services import events, zones
from .common import charge, mapped_errors, owned_tab, wall_clock
from .router import router


def _owned(request, event_id: UUID) -> Event:
    event = Event.objects.select_related('user').filter(user=request.auth, pk=event_id).first()
    if event is None:
        raise Http404('Event not found')
    return event


def event_data(event: Event, now=None) -> dict:
    rows = []
    for row in events.snapshot_rows(event):
        zone = zones.tz(str(row['timezone']))
        start, end = event.starts_at.astimezone(zone), event.ends_at.astimezone(zone)
        rows.append(
            {
                'name': str(row.get('name') or zones.place_name(str(row['timezone']))),
                'detail': str(row.get('detail') or ''),
                'timezone': str(row['timezone']),
                'home': bool(row.get('home')),
                'starts_local': wall_clock(start),
                'ends_local': wall_clock(end),
                'utc_offset_minutes': int(
                    (start.utcoffset() or timedelta(0)).total_seconds() // 60
                ),
            }
        )
    return {
        'id': event.pk,
        'title': event.title,
        'starts_at': event.starts_at,
        'ends_at': event.ends_at,
        'url': events.event_url(event),
        'ended': events.has_ended(event, now),
        'locations': rows,
        'created_at': event.created_at,
        'updated_at': event.updated_at,
    }


@router.get('/events', response=list[WorldTimeEventSchema])
def list_events(request):
    """The caller's events: those that have not ended first, soonest first, then the ended ones,
    latest first. At most `max_events` (`GET /settings`), so the list is not paged."""
    now = timezone.now()
    return [event_data(event, now) for event in events.list_events(request.auth, now=now)]


@router.post('/events', response={201: WorldTimeEventSchema})
def create_event(request, payload: WorldTimeEventCreateSchema):
    """Share a time range: the event keeps a copy of the locations of `tab` (under the names the
    caller gave them), and anyone who has its `url` can see the title, the time and those places.
    The end must be after the start, at most 7 days later and not already past."""
    tab = owned_tab(request, payload.tab)
    with mapped_errors():
        events.clean_title(payload.title)  # a refusal here spends no budget
        events.clean_times(payload.starts_at, payload.ends_at)
        charge(request, 'WT_EDIT_USER')
        event = events.create_event(
            request.auth,
            tab,
            title=payload.title,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
        )
    return 201, event_data(event)


@router.get('/events/{event_id}', response=WorldTimeEventSchema)
def get_event(request, event_id: UUID):
    """One of the caller's events."""
    return event_data(_owned(request, event_id))


@router.patch('/events/{event_id}', response=WorldTimeEventSchema)
def update_event(request, event_id: UUID, payload: WorldTimeEventUpdateSchema):
    """Change the title and/or the time (send both `starts_at` and `ends_at` for a new time). The
    link and the places stay. A body with nothing in it changes nothing and spends nothing; an
    explicit `null` is read as "not sent"."""
    event = _owned(request, event_id)
    changes = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not changes:
        return event_data(event)
    with mapped_errors():
        if 'title' in changes:
            events.clean_title(changes['title'])
        if ('starts_at' in changes) != ('ends_at' in changes):
            raise ValidationError({'ends_at': 'Send both the start and the end.'})
        if 'starts_at' in changes:
            events.clean_times(changes['starts_at'], changes['ends_at'])
        charge(request, 'WT_EDIT_USER')
        events.update_event(event, **changes)
    return event_data(event)


@router.delete('/events/{event_id}', response={204: None})
def delete_event(request, event_id: UUID):
    """Delete an event: its link stops working. Never rate limited."""
    events.delete_event(_owned(request, event_id))
    return 204, None
