"""The shared event page (issue #269, PR 2): `/w/<code>` shows an event to anyone who has the link.

**The code is the capability**, so every answer is `Cache-Control: no-store, private`,
`Referrer-Policy: strict-origin` and `X-Robots-Tag: noindex, nofollow` (and the page carries a
`noindex` meta), as the shared note pages are. **Every link that doesn't work** (an unknown code, a
deleted or purged event) renders the same standalone `world_time/unavailable.html` with status 404:
it never says why. The path is in `TIMEZONE_MIDDLEWARE_SKIP_PREFIXES`, so the terms gate leaves it
alone and it renders in the public date wording: each location's time is worked out here, per
zone, and the visitor's own time by the browser (`{% local_time %}`).

`WT_VIEW_IP` is charged before the lookup (made-up codes spend it too); over it, a plain 429 page.
Read-only: the page has no form.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

from apps.core import ratelimit
from apps.core.datetime_format import PUBLIC

from ..services import events


def private(response: HttpResponse) -> HttpResponse:
    """Mark an answer about one shared event as not cacheable, not to be sent as a referrer beyond
    the site's origin, and not for search engines."""
    response['Cache-Control'] = 'no-store, private'
    response['Referrer-Policy'] = 'strict-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def unavailable(request: HttpRequest) -> HttpResponse:
    """The one answer for every link that doesn't work. Rendered from no context at all, so a
    made-up code costs no session or user query and every case is byte for byte the same."""
    return private(HttpResponse(render_to_string('world_time/unavailable.html'), status=404))


@require_GET
def event_public(request: HttpRequest, code: str) -> HttpResponse:
    result = ratelimit.hit_ip(request, 'WT_VIEW_IP')
    if not result.allowed:
        return private(ratelimit.page_response(request, result))
    event = events.get_available(code)
    if event is None:
        return unavailable(request)
    url = events.event_url(event)
    context = {
        'event': event,
        'rows': events.event_rows(event, PUBLIC),
        'duration': events.duration_text(event),
        'status': events.status_text(event),
        'event_url': url,
        'qr_data_uri': events.qr_data_uri(url),
    }
    return private(render(request, 'world_time/public/event.html', context))
