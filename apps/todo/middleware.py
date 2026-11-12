"""Tell the activity log which entry point a request came in through (issue #274)."""

import inspect

from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware

from .models import EventChannel
from .services import events

API_PREFIX = '/api/'


def _channel_for(request: HttpRequest) -> str:
    return EventChannel.API if request.path.startswith(API_PREFIX) else EventChannel.WEB


@sync_and_async_middleware
def EventChannelMiddleware(get_response):
    """`API` for a request under `/api/`, `WEB` for every other: what `services.events` records as
    the channel of any task change made while the request is handled. Work that is not a request
    (a job, the shell) records `SYSTEM`; the email intake and the phone's answers set their own."""
    if inspect.iscoroutinefunction(get_response):

        async def amiddleware(request: HttpRequest) -> HttpResponse:
            with events.channel(_channel_for(request)):
                return await get_response(request)

        return amiddleware

    def middleware(request: HttpRequest) -> HttpResponse:
        with events.channel(_channel_for(request)):
            return get_response(request)

    return middleware
