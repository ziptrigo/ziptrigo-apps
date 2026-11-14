"""The owner's side of shared events: making one from a selected range, the list, an event's page
(its link, QR code and times; editing; deleting).

Another user's event is a 404. A refusal is a 422 into the form's message slot; the form is
validated before `WT_EDIT_USER` is charged. Deleting is never rate limited.
"""

from datetime import timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import hx_redirect

from ..forms import EventCreateForm, EventEditForm
from ..models import Event, WorldTimeSettings
from ..services import events, get_account, zones
from .common import form_error_lines, limited, owned_tab, refuse, validation_lines

CREATE_SLOT = '#wt-event-msg'
EDIT_SLOT = '#wt-event-edit-msg'
#: A service field -> the input that holds it on the edit form.
EDIT_NAMES = {'starts_at': 'start_time', 'ends_at': 'end_time'}


def owned_event(request: AuthenticatedHttpRequest, event_id: UUID) -> Event:
    """The requester's event `event_id`, or a 404."""
    event = Event.objects.select_related('user').filter(user=request.user, pk=event_id).first()
    if event is None:
        raise Http404('Event not found')
    return event


def event_page_url(event: Event) -> str:
    return reverse('world_time:event', args=[event.pk])


def _edit_values(event: Event) -> dict:
    """The edit form's fields for the event as stored: its time on its home location's clock."""
    zone = zones.tz(events.home_zone_name(event))
    start, end = event.starts_at.astimezone(zone), event.ends_at.astimezone(zone)
    return {
        'title': event.title,
        'date': start.date().isoformat(),
        'start_time': start.time().isoformat('minutes'),
        'end_time': end.time().isoformat('minutes'),
    }


def _event_context(event: Event, values: dict | None = None) -> dict:
    url = events.event_url(event)
    now = timezone.now()
    return {
        'event': event,
        'rows': events.event_rows(event),
        'event_url': url,
        'event_path': events.event_path(event),
        'qr_data_uri': events.qr_data_uri(url),
        'duration': events.duration_text(event),
        'status': events.status_text(event, now),
        'ended': events.has_ended(event, now),
        'long': event.ends_at - event.starts_at > timedelta(days=1),
        'home_name': events.home_name(event),
        'values': values or _edit_values(event),
        'purge_days': WorldTimeSettings.load().event_purge_days,
    }


@login_required
@require_GET
@ensure_csrf_cookie
def events_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The user's events: the ones ahead first, then the ended ones."""
    get_account(request.user)
    listed = events.list_events(request.user)
    rows = [
        {
            'event': event,
            'when': events.home_range_text(event),
            'home_name': events.home_name(event),
            'url': events.event_url(event),
        }
        for event in listed
    ]
    config = WorldTimeSettings.load()
    return render(
        request,
        'world_time/events.html',
        {
            'rows': rows,
            'purge_days': config.event_purge_days,
            'max_events': config.max_events_per_user,
        },
    )


@login_required
@require_GET
@ensure_csrf_cookie
def event_page(request: AuthenticatedHttpRequest, event_id: UUID) -> HttpResponse:
    event = owned_event(request, event_id)
    return render(request, 'world_time/event.html', _event_context(event))


@login_required
@require_POST
def event_create(request: AuthenticatedHttpRequest, tab_id: UUID) -> HttpResponse:
    """Share the range selected on a tab's grid: `title`, `start` and `end` (two instants)."""
    tab = owned_tab(request, tab_id)
    fallback = reverse('world_time:tab', args=[tab.pk])
    form = EventCreateForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=CREATE_SLOT, fallback=fallback)
    if (response := limited(request, 'WT_EDIT_USER', retarget=CREATE_SLOT)) is not None:
        return response
    try:
        event = events.create_event(
            request.user,
            tab,
            title=form.cleaned_data['title'],
            starts_at=form.cleaned_data['start'],
            ends_at=form.cleaned_data['end'],
        )
    except ValidationError as exc:
        lines = validation_lines(exc, {'starts_at': 'start', 'ends_at': 'end'})
        return refuse(request, lines, retarget=CREATE_SLOT, fallback=fallback)
    messages.success(request, 'Event created. Anyone with its link can see it.')
    return hx_redirect(request, event_page_url(event))


@login_required
@require_POST
def event_edit(request: AuthenticatedHttpRequest, event_id: UUID) -> HttpResponse:
    event = owned_event(request, event_id)
    fallback = event_page_url(event)
    form = EventEditForm(request.POST)
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=EDIT_SLOT, fallback=fallback)
    if (response := limited(request, 'WT_EDIT_USER', retarget=EDIT_SLOT)) is not None:
        return response
    posted = {
        'date': form.cleaned_data['date'].isoformat(),
        'start_time': form.cleaned_data['start_time'].isoformat('minutes'),
        'end_time': form.cleaned_data['end_time'].isoformat('minutes'),
    }
    stored = _edit_values(event)
    try:
        if all(posted[key] == stored[key] for key in posted):
            # The time fields are as the page showed them: only the title is saved. Recomputing
            # the instants from them would change an event the form can't fully express (one
            # longer than a day, one not on a whole minute, one in the hour a clock repeats).
            events.update_event(event, title=form.cleaned_data['title'])
        else:
            starts_at, ends_at = events.local_range(
                events.home_zone_name(event),
                form.cleaned_data['date'],
                form.cleaned_data['start_time'],
                form.cleaned_data['end_time'],
            )
            events.update_event(
                event, title=form.cleaned_data['title'], starts_at=starts_at, ends_at=ends_at
            )
    except ValidationError as exc:
        lines = validation_lines(exc, EDIT_NAMES)
        return refuse(request, lines, retarget=EDIT_SLOT, fallback=fallback)
    messages.success(request, 'Saved.')
    return hx_redirect(request, fallback)


@login_required
@require_POST
def event_delete(request: AuthenticatedHttpRequest, event_id: UUID) -> HttpResponse:
    """From the confirmation modal: a plain POST that ends in a redirect with a flash message."""
    # Not rate limited: deleting only takes something off the web, and must always work.
    event = owned_event(request, event_id)
    title = event.title
    events.delete_event(event)
    messages.success(request, f'Deleted the event {title}. Its link no longer works.')
    return hx_redirect(request, reverse('world_time:events'))
