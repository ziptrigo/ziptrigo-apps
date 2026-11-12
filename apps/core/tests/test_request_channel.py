"""The request channel (issue #274): which entry point a piece of work came in through."""

import asyncio

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core import request_channel
from apps.core.request_channel import RequestChannelMiddleware, channel, current_channel

seen: list[str] = []


def view(request):
    seen.append(current_channel())
    return HttpResponse('ok')


def failing_view(request):
    seen.append(current_channel())
    raise RuntimeError('boom')


def test_the_default_is_system_and_the_context_manager_restores_it():
    assert current_channel() == 'system'
    with channel('email'):
        assert current_channel() == 'email'
        with channel('push'):
            assert current_channel() == 'push'
        assert current_channel() == 'email'
    assert current_channel() == 'system'


@pytest.mark.parametrize(('path', 'expected'), [('/todo/', 'web'), ('/api/todo/tasks', 'api')])
def test_a_request_runs_as_web_or_api(path, expected):
    seen.clear()

    RequestChannelMiddleware(view)(RequestFactory().get(path))

    assert seen == [expected]
    assert current_channel() == 'system'


def test_the_channel_is_back_to_system_after_a_view_raises():
    seen.clear()

    with pytest.raises(RuntimeError):
        RequestChannelMiddleware(failing_view)(RequestFactory().get('/todo/'))

    assert seen == ['web']
    assert current_channel() == request_channel.SYSTEM


def test_the_async_middleware_does_the_same():
    seen.clear()

    async def aview(request):
        seen.append(current_channel())
        raise RuntimeError('boom')

    async def run():
        with pytest.raises(RuntimeError):
            await RequestChannelMiddleware(aview)(RequestFactory().get('/api/x'))

    asyncio.run(run())

    assert seen == ['api']
    assert current_channel() == 'system'
