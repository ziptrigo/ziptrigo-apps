"""Which entry point a piece of work came in through (issue #274), for any app that keeps a log.

A context variable holds a plain string: `WEB` for a request, `API` for one under `/api/`, `EMAIL`
and `PUSH` for work an app wraps itself (an inbound email, a phone notification button) and
`SYSTEM`, the default, for everything outside a request (a job, the shell). `core` knows no log; a
product that records changes (To-do's activity log) reads `current_channel()` when it writes one.

`RequestChannelMiddleware` sets it for the duration of a request and resets it afterwards, also
when the view raises. A `StreamingHttpResponse` body is consumed after that reset, so a change made
while a response is streamed is recorded as `SYSTEM`; nothing does that today.
"""

import inspect
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware

WEB = 'web'
API = 'api'
EMAIL = 'email'
PUSH = 'push'
SYSTEM = 'system'

API_PREFIX = '/api/'

_channel: ContextVar[str] = ContextVar('request_channel', default=SYSTEM)


@contextmanager
def channel(name: str) -> Generator[None]:
    """Run the block as work that came in through `name`."""
    token = _channel.set(name)
    try:
        yield
    finally:
        _channel.reset(token)


def current_channel() -> str:
    return _channel.get()


def _channel_for(request: HttpRequest) -> str:
    return API if request.path.startswith(API_PREFIX) else WEB


@sync_and_async_middleware
def RequestChannelMiddleware(get_response):
    """`API` for a request under `/api/`, `WEB` for every other, for the whole request."""
    if inspect.iscoroutinefunction(get_response):

        async def amiddleware(request: HttpRequest) -> HttpResponse:
            with channel(_channel_for(request)):
                return await get_response(request)

        return amiddleware

    def middleware(request: HttpRequest) -> HttpResponse:
        with channel(_channel_for(request)):
            return get_response(request)

    return middleware
