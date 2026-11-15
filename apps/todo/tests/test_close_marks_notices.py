"""Closing a task (Done or Won't do) marks the notifications that point at its page as opened and
read, so they leave the bell and the dropdown."""

from typing import cast

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.models import Notification
from apps.core.services import notifications

from ..models import Resolution
from ..services import close_task, complete_task, create_task

pytestmark = pytest.mark.django_db


def notice_for(user, task, *, source='todo', **kwargs) -> Notification:
    return notifications.notify(
        user,
        source=source,
        title='Reminder',
        url=reverse('todo:task', args=[task.number]),
        **kwargs,
    )


def reload(notification: Notification) -> Notification:
    return Notification.objects.get(pk=notification.pk)


@pytest.mark.parametrize('resolution', [Resolution.DONE, Resolution.WONT_DO])
def test_closing_marks_the_tasks_notices_seen(funded_user, resolution):
    task = create_task(funded_user, title='Pay rent')
    notice = notice_for(funded_user, task)

    close_task(task, resolution)

    notice = reload(notice)
    assert notice.opened_at is not None and notice.read_at is not None
    assert notifications.unread_count(funded_user) == 0
    assert notifications.dropdown_notifications(funded_user) == []
    assert notifications.page_notifications(funded_user).count() == 1  # kept as a record


def test_other_tasks_and_other_sources_are_left_alone(funded_user):
    task, other = create_task(funded_user, title='A'), create_task(funded_user, title='B')
    elsewhere = notice_for(funded_user, task, source='file_transfer')
    for_other = notice_for(funded_user, other)

    complete_task(task)

    assert reload(elsewhere).opened_at is None
    assert reload(for_other).opened_at is None


def test_closed_subtasks_notices_are_seen_too(funded_user):
    parent = create_task(funded_user, title='Parent')
    sub = create_task(funded_user, title='Sub', parent=parent)
    notice = notice_for(funded_user, sub)

    complete_task(parent)

    assert reload(notice).opened_at is not None


def test_a_subtask_left_open_keeps_its_notice(funded_user):
    parent = create_task(funded_user, title='Parent')
    sub = create_task(funded_user, title='Sub', parent=parent)
    notice = notice_for(funded_user, sub)

    close_task(parent, subtasks='leave')

    assert reload(notice).opened_at is None


def test_an_already_opened_notice_keeps_its_time(funded_user):
    task = create_task(funded_user, title='T')
    notice = notice_for(funded_user, task)
    opened = notifications.mark_opened(funded_user, notice.pk).opened_at

    complete_task(task)

    assert reload(notice).opened_at == opened


def test_a_failing_tidy_never_fails_the_close(funded_user, monkeypatch):
    task = create_task(funded_user, title='T')

    def boom(*args, **kwargs):
        raise RuntimeError('no notices today')

    monkeypatch.setattr('apps.todo.services.tasks.notifications.mark_opened_for_urls', boom)

    complete_task(task)

    task.refresh_from_db()
    assert task.is_completed


def test_the_close_endpoint_marks_them_too(funded_user, client):
    task = create_task(funded_user, title='T')
    notice = notice_for(funded_user, task)
    client.force_login(funded_user)

    client.post(reverse('todo:complete', args=[task.number]))

    assert reload(notice).opened_at is not None


class TestMarkOpenedForUrls:
    def test_scoped_to_the_user(self, user):
        someone = cast(User, UserFactory())
        mine = notifications.notify(user, source='t', title='x', url='/a/')
        theirs = notifications.notify(someone, source='t', title='x', url='/a/')

        assert notifications.mark_opened_for_urls(user, ['/a/']) == 1

        assert reload(mine).opened_at is not None and reload(theirs).opened_at is None

    def test_nothing_to_do(self, user):
        assert notifications.mark_opened_for_urls(user, []) == 0
        assert notifications.mark_opened_for_urls(user, ['']) == 0
