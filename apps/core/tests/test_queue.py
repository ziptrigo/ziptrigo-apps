import logging

import pytest

from apps.core.services.queue import enqueue_after_commit

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


class _BrokenTask:
    name = 'tests.broken_task'

    def enqueue(self, *args):
        raise RuntimeError('queue is down')


def test_a_failure_to_enqueue_is_logged_and_swallowed(caplog, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        enqueue_after_commit(_BrokenTask(), 'x')

    assert 'tests.broken_task' in caplog.text


def test_the_failure_log_never_carries_the_arguments(caplog, django_capture_on_commit_callbacks):
    """The arguments of some tasks are an address someone typed into a form."""
    with caplog.at_level(logging.DEBUG):
        with django_capture_on_commit_callbacks(execute=True):
            enqueue_after_commit(_BrokenTask(), 'stranger@example.com')

    assert 'stranger@example.com' not in caplog.text
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]
