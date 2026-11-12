"""Blockers (issue #273): "Blocked by" links between a user's tasks, the Blocked state they drive,
the automatic unblocking (and the notices), a blocker coming back, the close confirmation, the task
page and quick-add controls, rows, search, the admin, and the JWT API, which must look and behave as
it always did."""

import json
import uuid
from datetime import date, time
from html import unescape
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.core.models import Notification

from ..limits import MAX_BLOCKERS_PER_TASK
from ..models import Reminder, ReminderDelivery, Resolution, Task, TaskBlocker, TaskState
from ..services import (
    FilterParams,
    add_blocker,
    close_task,
    complete_task,
    create_task,
    delete_forever,
    describe_params,
    filter_tasks,
    get_account,
    parse_numbers,
    reminders,
    remove_blocker,
    reschedule_overdue,
    restore_task,
    set_state,
    tasks_by_numbers,
    trash_task,
    uncomplete_task,
    update_task,
)
from ..services.blockers import find_blocker, suggestions
from ..services.limits import TITLE_MAX
from .api_support import auth, delete, get, patch, post  # noqa: F401 -- `auth` is a fixture
from .factories import make_task, utc
from .test_api_tasks import TASK_KEYS

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
TODAY = date(2026, 10, 28)  # `wednesday` freezes now at 12:00 UTC
FIRES = utc(2026, 10, 28, 14, 26)  # the default 5-minute reminder of a task due at 14:30


def text(response) -> str:
    return unescape(response.content.decode())


def reload(task: Task) -> Task:
    return Task.objects.get(pk=task.pk)


def errors(callable_, *args, **kwargs) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as caught:
        callable_(*args, **kwargs)
    return caught.value.message_dict


def make(user, title='T', **fields) -> Task:
    return create_task(user, title=title, **fields)


def notices(user, prefix: str):
    return Notification.objects.filter(user=user, key__startswith=prefix)


def link(blocker: Task, blocked: Task) -> None:
    """A link straight in the database (no state change), for tests that arrange a situation."""
    TaskBlocker.objects.create(blocker=blocker, blocked=blocked)


# -- Linking -------------------------------------------------------------------------------------


class TestAdding:
    def test_an_open_blocker_blocks_an_open_task(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')

        assert add_blocker(task, blocker) is True

        assert reload(task).state == TaskState.BLOCKED
        assert task.state == TaskState.BLOCKED  # the caller's instance is current too
        assert TaskBlocker.objects.filter(blocker=blocker, blocked=task).count() == 1
        assert reload(blocker).state == TaskState.TODO  # nothing happens to the blocker

    @pytest.mark.parametrize('state', ['todo', 'in_progress', 'on_hold'])
    def test_from_any_open_state(self, funded_user, state):
        task = make(funded_user, 'Task', state=state)

        add_blocker(task, make(funded_user, 'Blocker'))

        assert reload(task).state == TaskState.BLOCKED

    def test_a_closed_blocker_links_without_changing_the_state(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        complete_task(blocker)

        assert add_blocker(task, blocker) is True

        assert reload(task).state == TaskState.TODO
        assert TaskBlocker.objects.filter(blocker=blocker, blocked=task).exists()

    def test_a_closed_blocked_task_only_gets_the_link(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        complete_task(task)

        add_blocker(task, blocker)

        done = reload(task)
        assert done.state == TaskState.TODO and done.resolution == 'done'
        assert TaskBlocker.objects.filter(blocker=blocker, blocked=task).exists()

    def test_a_trashed_blocker_is_refused(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        trash_task(blocker)

        assert 'in the trash' in errors(add_blocker, task, blocker)['blocker'][0]
        assert not TaskBlocker.objects.exists()

    def test_a_trashed_task_cannot_be_given_a_blocker(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        trash_task(task)

        assert errors(add_blocker, task, blocker) == {
            '__all__': ['Restore the task before changing it.']
        }

    def test_a_task_cannot_block_itself(self, funded_user):
        task = make(funded_user)

        assert errors(add_blocker, task, task) == {'blocker': ['A task cannot block itself.']}

    def test_the_database_refuses_a_self_link_too(self, funded_user):
        from django.db import IntegrityError, transaction

        task = make(funded_user)

        with pytest.raises(IntegrityError), transaction.atomic():
            TaskBlocker.objects.create(blocker=task, blocked=task)

    def test_another_users_task_is_refused(self, funded_user, other_user):
        mine = make(funded_user)
        theirs = make_task(other_user, number=7000)

        assert errors(add_blocker, mine, theirs) == {'blocker': ['Unknown task.']}

    def test_a_duplicate_is_a_no_op(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        set_state(task, 'in_progress')

        assert add_blocker(task, blocker) is False

        assert TaskBlocker.objects.count() == 1
        assert reload(task).state == TaskState.IN_PROGRESS  # untouched

    def test_at_most_twenty_blockers(self, funded_user):
        task = make(funded_user, 'Task')
        for index in range(MAX_BLOCKERS_PER_TASK):
            add_blocker(task, make(funded_user, f'B{index}'))

        extra = make(funded_user, 'One too many')

        assert errors(add_blocker, task, extra) == {
            'blocker': [f'A task can have at most {MAX_BLOCKERS_PER_TASK} blockers.']
        }
        assert TaskBlocker.objects.filter(blocked=task).count() == MAX_BLOCKERS_PER_TASK
        # the same blocker again is still a harmless no-op, not "too many"
        first = TaskBlocker.objects.filter(blocked=task).first()
        assert first is not None and add_blocker(task, first.blocker) is False

    def test_a_subtask_can_block_and_be_blocked(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)
        other = make(funded_user, 'Other')

        add_blocker(other, sub)  # a subtask blocks a task
        add_blocker(sub, make(funded_user, 'Third'))  # and is blocked

        assert reload(other).state == TaskState.BLOCKED and reload(sub).state == TaskState.BLOCKED

    def test_a_subtask_may_be_blocked_by_its_parent_and_the_other_way_round_is_a_cycle(
        self, funded_user
    ):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)
        add_blocker(sub, parent)

        message = errors(add_blocker, parent, sub)['blocker'][0]

        assert message == (
            f'#{sub.number} is already blocked by #{parent.number}, '
            'directly or through other tasks.'
        )

    def test_a_blocked_task_due_today_goes_to_the_end_of_a_manual_today(
        self, funded_user, wednesday
    ):
        from ..services import reorder

        first = make(funded_user, 'First', due_date=TODAY)
        second = make(funded_user, 'Second', due_date=TODAY)
        reorder(funded_user, scope='today', ids=[first.pk, second.pk])

        add_blocker(first, make(funded_user, 'Blocker'))

        assert reload(first).today_position == 2


class TestCycles:
    def test_a_direct_cycle_is_refused_with_a_message_naming_it(self, funded_user):
        low, high = make(funded_user, 'Low'), make(funded_user, 'High')
        add_blocker(high, low)  # low blocks high

        message = errors(add_blocker, low, high)['blocker'][0]  # high blocks low?

        assert message == (
            f'#{high.number} is already blocked by #{low.number}, directly or through other tasks.'
        )
        assert TaskBlocker.objects.count() == 1

    def test_a_long_chain_closing_on_itself_is_refused(self, funded_user):
        chain = [make(funded_user, f'T{index}') for index in range(8)]
        for blocker, blocked in zip(chain, chain[1:]):
            add_blocker(blocked, blocker)  # T0 blocks T1 blocks T2 ... blocks T7

        assert (
            'directly or through other tasks'
            in errors(add_blocker, chain[0], chain[-1])['blocker'][0]
        )
        assert TaskBlocker.objects.count() == 7

    def test_a_diamond_is_not_a_cycle(self, funded_user):
        top, left, right, bottom = (make(funded_user, name) for name in 'TLRB')
        add_blocker(left, top)
        add_blocker(right, top)
        add_blocker(bottom, left)
        add_blocker(bottom, right)  # two paths, no loop

        assert TaskBlocker.objects.count() == 4

    def test_closed_and_trashed_tasks_count_in_a_cycle(self, funded_user):
        first, second, third = (make(funded_user, name) for name in 'ABC')
        add_blocker(second, first)
        add_blocker(third, second)
        complete_task(second)  # the links are kept, so the loop is still a loop

        assert 'directly' in errors(add_blocker, first, third)['blocker'][0]

    def test_unrelated_links_are_fine(self, funded_user):
        a, b, c, d = (make(funded_user, name) for name in 'ABCD')
        add_blocker(b, a)

        add_blocker(d, c)
        add_blocker(c, b)  # a blocks b blocks c blocks d: a chain, not a loop

        assert TaskBlocker.objects.count() == 3


class TestNumbers:
    def test_parsing_a_box(self):
        assert parse_numbers('1200, 1201') == [1200, 1201]
        assert parse_numbers('#1200 #1201;1202') == [1200, 1201, 1202]
        assert parse_numbers('1200 1200  #1200') == [1200]  # once each, in order
        assert parse_numbers('') == [] and parse_numbers('   ') == []

    @pytest.mark.parametrize('bad', ['abc', '12x', '1200, soon', '#', '1.5', '1234567890'])
    def test_anything_else_is_a_refusal(self, bad):
        assert errors(parse_numbers, bad) == {
            'blocked_by': ['Enter task numbers, like 1200, 1201.']
        }

    def test_finding_the_tasks(self, funded_user, other_user):
        one, two = make(funded_user, 'One'), make(funded_user, 'Two')

        assert tasks_by_numbers(funded_user, [two.number, one.number]) == [two, one]

    def test_an_unknown_or_foreign_number_says_the_same(self, funded_user, other_user):
        theirs = make_task(other_user, number=7001)

        assert errors(tasks_by_numbers, funded_user, [theirs.number]) == {
            'blocked_by': [f'There is no task #{theirs.number}.']
        }
        assert errors(tasks_by_numbers, funded_user, [999999]) == {
            'blocked_by': ['There is no task #999999.']
        }

    def test_a_trashed_task_is_named_as_such(self, funded_user):
        task = make(funded_user)
        trash_task(task)

        message = errors(tasks_by_numbers, funded_user, [task.number])['blocked_by'][0]

        assert message == f'#{task.number} is in the trash. Restore it first.'


class TestCreatingWithBlockers:
    def test_an_open_blocker_makes_the_new_task_blocked(self, funded_user):
        blocker = make(funded_user, 'Blocker')

        task = create_task(funded_user, title='Task', blocked_by=[blocker.number])

        assert reload(task).state == TaskState.BLOCKED
        assert TaskBlocker.objects.filter(blocker=blocker, blocked=task).exists()

    def test_it_wins_over_the_state_asked_for(self, funded_user):
        blocker = make(funded_user, 'Blocker')

        task = create_task(
            funded_user, title='Task', state='in_progress', blocked_by=[blocker.number]
        )

        assert reload(task).state == TaskState.BLOCKED

    def test_a_closed_blocker_leaves_it_to_do(self, funded_user):
        blocker = make(funded_user, 'Blocker')
        complete_task(blocker)

        task = create_task(funded_user, title='Task', blocked_by=[blocker.number])

        assert reload(task).state == TaskState.TODO
        assert TaskBlocker.objects.filter(blocked=task).count() == 1

    def test_a_refusal_creates_and_charges_nothing(self, funded_user):
        before = Task.objects.count()

        assert errors(create_task, funded_user, title='X', blocked_by=[424242]) == {
            'blocked_by': ['There is no task #424242.']
        }

        assert Task.objects.count() == before
        assert get_account(funded_user).next_task_number == before + 1000

    def test_the_cap(self, funded_user):
        numbers = [make(funded_user, f'B{i}').number for i in range(MAX_BLOCKERS_PER_TASK + 1)]

        message = errors(create_task, funded_user, title='X', blocked_by=numbers)['blocked_by']

        assert message == [f'A task can have at most {MAX_BLOCKERS_PER_TASK} blockers.']
        assert create_task(funded_user, title='X', blocked_by=numbers[:MAX_BLOCKERS_PER_TASK])

    def test_blocked_is_not_a_state_to_ask_for(self, funded_user):
        assert errors(create_task, funded_user, title='X', state='blocked') == {
            'state': ['Choose To do, In progress or On hold.']
        }

    def test_the_next_occurrence_starts_with_no_blockers(self, funded_user, wednesday):
        blocker = make(funded_user, 'Blocker')
        task = create_task(
            funded_user,
            title='Rent',
            due_date=TODAY,
            rrule='FREQ=DAILY',
            blocked_by=[blocker.number],
        )

        close_task(task)

        following = Task.objects.get(recurrence_id=task.recurrence_id, completed_at__isnull=True)
        assert following.pk != task.pk
        assert following.state == TaskState.TODO
        assert not TaskBlocker.objects.filter(blocked=following).exists()
        assert TaskBlocker.objects.filter(blocked=task).count() == 1  # the links are kept


class TestStates:
    def test_blocked_is_not_chosen(self, funded_user):
        task = make(funded_user)

        assert errors(set_state, task, 'blocked') == {
            'state': ['Choose To do, In progress or On hold.']
        }

    def test_a_blocked_task_can_be_set_to_any_other_state_keeping_the_links(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        for state in ('in_progress', 'on_hold', 'todo'):
            set_state(task, state)
            assert reload(task).state == state
            assert TaskBlocker.objects.filter(blocked=task).count() == 1

    def test_another_open_blocker_blocks_it_again(self, funded_user):
        task = make(funded_user, 'Task')
        add_blocker(task, make(funded_user, 'First'))
        set_state(task, 'todo')

        add_blocker(task, make(funded_user, 'Second'))

        assert reload(task).state == TaskState.BLOCKED

    def test_blocked_is_muted(self, funded_user, wednesday):
        from ..models import MUTED_STATES

        task = make(funded_user, 'Task', due_date=date(2026, 10, 20))
        add_blocker(task, make(funded_user, 'Blocker'))

        blocked = reload(task)

        assert TaskState.BLOCKED in MUTED_STATES
        assert blocked.is_muted and blocked.state_group == 'muted'
        assert not blocked.is_overdue(TODAY)

    def test_reschedule_overdue_leaves_a_blocked_task(self, funded_user, wednesday):
        old = date(2026, 10, 20)
        blocked = make(funded_user, 'Blocked', due_date=old)
        add_blocker(blocked, make(funded_user, 'Blocker'))
        late = make(funded_user, 'Late', due_date=old)

        assert reschedule_overdue(funded_user) == 1

        assert reload(blocked).due_date == old and reload(late).due_date == TODAY


class TestRemoving:
    def test_the_last_open_blocker_goes_back_to_to_do_quietly(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        assert remove_blocker(task, blocker) is True

        assert reload(task).state == TaskState.TODO and task.state == TaskState.TODO
        assert not TaskBlocker.objects.exists()
        assert not Notification.objects.exists()  # the user did it themselves

    def test_another_open_blocker_keeps_it_blocked(self, funded_user):
        task, first, second = (make(funded_user, name) for name in ('Task', 'A', 'B'))
        add_blocker(task, first)
        add_blocker(task, second)

        remove_blocker(task, first)

        assert reload(task).state == TaskState.BLOCKED

    def test_a_closed_blocker_left_does_not_keep_it_blocked(self, funded_user):
        task, open_one, closed = (make(funded_user, name) for name in ('Task', 'A', 'B'))
        complete_task(closed)
        add_blocker(task, open_one)
        add_blocker(task, closed)

        remove_blocker(task, open_one)

        assert reload(task).state == TaskState.TODO

    def test_a_task_set_by_hand_keeps_its_state(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        set_state(task, 'on_hold')

        remove_blocker(task, blocker)

        assert reload(task).state == TaskState.ON_HOLD

    def test_there_is_nothing_to_do_without_a_link(self, funded_user):
        task, other = make(funded_user, 'Task'), make(funded_user, 'Other')

        assert remove_blocker(task, other) is False

    def test_a_trashed_task_is_read_only(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        trash_task(task)

        assert errors(remove_blocker, task, blocker) == {
            '__all__': ['Restore the task before changing it.']
        }

    def test_a_closed_task_just_loses_the_link(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(task)

        assert remove_blocker(task, blocker) is True

        assert not TaskBlocker.objects.exists() and reload(task).resolution == 'done'

    def test_leaving_blocked_delivers_the_held_reminder(
        self, funded_user, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = create_task(funded_user, title='Rent', due_date=TODAY, due_time=time(14, 30))
        blocker = make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        freeze_now(2026, 10, 28, 15, 0)  # the reminder (14:26) came due while it was blocked
        assert reminders.deliver_due(FIRES) == 0

        with django_capture_on_commit_callbacks(execute=True):
            remove_blocker(task, blocker)

        assert ReminderDelivery.objects.filter(task=task).count() == 1
        assert Reminder.objects.get(task=task).sent_at is not None


# -- A blocker ending ----------------------------------------------------------------------------


class TestUnblocking:
    @pytest.mark.parametrize('resolution', [Resolution.DONE, Resolution.WONT_DO])
    def test_closing_the_blocker_frees_the_task_and_tells_the_user(self, funded_user, resolution):
        task, blocker = make(funded_user, 'Pay rent'), make(funded_user, 'Get the money')
        add_blocker(task, blocker)

        close_task(blocker, resolution)

        assert reload(task).state == TaskState.TODO
        [notice] = notices(funded_user, 'todo_unblocked:')
        assert notice.key == f'todo_unblocked:{task.pk}'
        assert notice.source == 'todo'
        assert notice.title == f'#{task.number} Pay rent is no longer blocked'
        assert notice.url == reverse('todo:task', args=[task.number])
        assert TaskBlocker.objects.filter(blocker=blocker, blocked=task).exists()  # links kept

    def test_the_task_is_freed_only_when_every_blocker_is_closed(self, funded_user):
        task, first, second = (make(funded_user, name) for name in ('Task', 'A', 'B'))
        add_blocker(task, first)
        add_blocker(task, second)

        complete_task(first)

        assert reload(task).state == TaskState.BLOCKED and not Notification.objects.exists()

        close_task(second, Resolution.WONT_DO)

        assert reload(task).state == TaskState.TODO
        assert notices(funded_user, 'todo_unblocked:').count() == 1

    def test_a_task_set_by_hand_keeps_its_state_and_is_still_told(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        set_state(task, 'in_progress')

        complete_task(blocker)

        assert reload(task).state == TaskState.IN_PROGRESS
        assert notices(funded_user, 'todo_unblocked:').count() == 1

    def test_an_on_hold_task_stays_on_hold(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        set_state(task, 'on_hold')

        complete_task(blocker)

        assert reload(task).state == TaskState.ON_HOLD
        assert notices(funded_user, 'todo_unblocked:').count() == 1

    def test_a_closed_task_is_not_told(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(task)

        complete_task(blocker)

        assert not Notification.objects.exists()
        assert reload(task).resolution == 'done'

    def test_a_trashed_task_is_corrected_silently(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        trash_task(task)

        complete_task(blocker)

        assert reload(task).state == TaskState.TODO and not Notification.objects.exists()

    def test_trashing_the_blocker_frees_the_task(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        trash_task(blocker)

        assert reload(task).state == TaskState.TODO
        assert notices(funded_user, 'todo_unblocked:').count() == 1

    def test_trashing_a_parent_frees_what_its_subtask_blocked(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)
        task = make(funded_user, 'Task')
        add_blocker(task, sub)

        trash_task(parent)

        assert reload(task).state == TaskState.TODO

    def test_deleting_an_open_blocker_forever_frees_the_task(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        delete_forever(blocker)

        assert reload(task).state == TaskState.TODO
        assert notices(funded_user, 'todo_unblocked:').count() == 1
        assert not TaskBlocker.objects.exists()  # the link went with the task

    def test_deleting_a_trashed_blocker_says_nothing_more(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        trash_task(blocker)
        Notification.objects.all().delete()

        delete_forever(blocker)

        assert not Notification.objects.exists()

    def test_closing_a_parent_closes_the_subtask_that_blocks(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)
        task = make(funded_user, 'Task')
        add_blocker(task, sub)

        close_task(parent)  # its open subtasks close with it

        assert reload(sub).resolution == 'done'
        assert reload(task).state == TaskState.TODO

    def test_leaving_the_subtasks_open_keeps_the_task_blocked(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)
        task = make(funded_user, 'Task')
        add_blocker(task, sub)

        close_task(parent, subtasks='leave')

        assert reload(task).state == TaskState.BLOCKED

    def test_a_blocker_and_what_it_blocks_closing_together(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub_a = make(funded_user, 'A', parent=parent)
        sub_b = make(funded_user, 'B', parent=parent)
        add_blocker(sub_b, sub_a)  # both close with the parent

        close_task(parent)

        assert reload(sub_b).state == TaskState.TODO and reload(sub_b).resolution == 'done'
        assert not Notification.objects.exists()  # a closed task is not told

    def test_a_repeating_blocker_frees_the_task_and_its_next_occurrence_blocks_nothing(
        self, funded_user, wednesday
    ):
        blocker = create_task(funded_user, title='Rent', due_date=TODAY, rrule='FREQ=DAILY')
        task = make(funded_user, 'Task')
        add_blocker(task, blocker)

        close_task(blocker)

        assert reload(task).state == TaskState.TODO
        following = Task.objects.get(recurrence_id=blocker.recurrence_id, completed_at__isnull=True)
        assert not TaskBlocker.objects.filter(blocker=following).exists()

    def test_the_held_reminders_are_released_once(
        self, funded_user, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = create_task(funded_user, title='Rent', due_date=TODAY, due_time=time(14, 30))
        reminders.add_reminder(task, minutes_before=60, channel='in_app')
        blocker = make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            complete_task(blocker)

        assert reload(task).state == TaskState.TODO
        assert ReminderDelivery.objects.filter(task=task).count() == 1
        assert all(r.sent_at is not None for r in Reminder.objects.filter(task=task))

    def test_the_notice_is_replaced_not_piled_up(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(blocker)
        uncomplete_task(blocker)
        add_blocker(task, make(funded_user, 'Again'))  # blocked once more
        close_task(Task.objects.get(title='Again'))

        assert notices(funded_user, 'todo_unblocked:').count() == 1

    def test_a_failing_notice_never_fails_the_close(self, funded_user, monkeypatch):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        def boom(*args, **kwargs):
            raise RuntimeError('no notices today')

        monkeypatch.setattr('apps.todo.services.blockers.notifications.notify', boom)

        complete_task(blocker)

        assert reload(blocker).resolution == 'done' and reload(task).state == TaskState.TODO

    def test_the_notice_names_the_task_without_overflowing(self, funded_user):
        task = make(funded_user, 'x' * TITLE_MAX)
        blocker = make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        complete_task(blocker)

        [notice] = notices(funded_user, 'todo_unblocked:')
        assert notice.title.startswith(f'#{task.number} xxx') and len(notice.title) <= 140
        assert notice.title.endswith('is no longer blocked')

    def test_it_never_reaches_another_users_task(self, funded_user, other_user):
        theirs = make_task(other_user, number=7002, state='blocked')

        complete_task(make(funded_user, 'Mine'))

        assert reload(theirs).state == TaskState.BLOCKED and not Notification.objects.exists()


class TestComingBack:
    def test_a_reopened_blocker_only_tells_what_it_blocks(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(blocker)
        Notification.objects.all().delete()

        uncomplete_task(blocker)

        assert reload(task).state == TaskState.TODO  # not blocked again
        [notice] = notices(funded_user, 'todo_blocker_reopened:')
        assert notice.key == f'todo_blocker_reopened:{task.pk}'
        assert notice.title == f'#{blocker.number} was reopened and blocks #{task.number}'
        assert notice.url == reverse('todo:task', args=[task.number])

    def test_a_closed_task_is_not_told(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(task)
        complete_task(blocker)

        uncomplete_task(blocker)

        assert not notices(funded_user, 'todo_blocker_reopened:').exists()

    def test_a_restored_blocker_tells_what_it_blocks(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        trash_task(blocker)
        Notification.objects.all().delete()

        restore_task(blocker)

        assert reload(task).state == TaskState.TODO
        assert notices(funded_user, 'todo_blocker_reopened:').count() == 1

    def test_restoring_a_closed_task_is_not_a_blocker_coming_back(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(blocker)
        trash_task(blocker)
        Notification.objects.all().delete()

        restore_task(blocker)

        assert not Notification.objects.exists()

    def test_restoring_a_parent_tells_what_its_subtasks_block(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)
        task = make(funded_user, 'Task')
        add_blocker(task, sub)
        trash_task(parent)
        Notification.objects.all().delete()

        restore_task(parent)

        assert notices(funded_user, 'todo_blocker_reopened:').count() == 1

    def test_a_restored_task_keeps_its_state(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        trash_task(task)

        restore_task(task)

        assert reload(task).state == TaskState.BLOCKED

    def test_reopening_a_task_puts_it_back_to_blocked_when_a_blocker_is_open(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        set_state(task, 'in_progress')
        complete_task(task)

        reopened = uncomplete_task(task)

        assert reopened.state == TaskState.BLOCKED and reload(task).state == TaskState.BLOCKED

    def test_otherwise_to_do(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(blocker)
        complete_task(task)

        assert uncomplete_task(task).state == TaskState.TODO

    def test_a_trashed_blocker_does_not_count_when_reopening(self, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        complete_task(task)
        trash_task(blocker)

        assert uncomplete_task(task).state == TaskState.TODO


# -- Other close paths: the phone, the API ---------------------------------------------------------


class TestOtherSurfaces:
    def test_the_push_complete_button_frees_the_task(self, funded_user, wednesday):
        from ..services import reminders as reminder_service

        blocker = create_task(funded_user, title='B', due_date=TODAY, due_time=time(14, 30))
        task = make(funded_user, 'Task')
        add_blocker(task, blocker)
        push_id = uuid.uuid4()
        ReminderDelivery.objects.create(
            reminder=Reminder.objects.get(task=blocker),
            task=blocker,
            user=funded_user,
            task_number=blocker.number,
            task_title=blocker.title,
            channel='push',
            sent_at=FIRES,
            fire_at=FIRES,
            push_id=push_id,
        )

        answer = SimpleNamespace(push_id=push_id, user_id=funded_user.pk, action='complete')
        reminder_service.answer_push(answer)  # ty: ignore[invalid-argument-type]

        assert reload(task).state == TaskState.TODO
        assert notices(funded_user, 'todo_unblocked:').count() == 1

    def test_the_push_complete_button_closes_a_task_with_open_blockers_without_asking(
        self, funded_user, wednesday
    ):
        from ..services import reminders as reminder_service

        task = create_task(funded_user, title='T', due_date=TODAY, due_time=time(14, 30))
        blocker = make(funded_user, 'Blocker')
        add_blocker(task, blocker)
        push_id = uuid.uuid4()
        ReminderDelivery.objects.create(
            reminder=Reminder.objects.get(task=task),
            task=task,
            user=funded_user,
            task_number=task.number,
            task_title=task.title,
            channel='push',
            sent_at=FIRES,
            fire_at=FIRES,
            push_id=push_id,
        )

        answer = SimpleNamespace(push_id=push_id, user_id=funded_user.pk, action='complete')
        reminder_service.answer_push(answer)  # ty: ignore[invalid-argument-type]

        assert reload(task).resolution == 'done'
        assert TaskBlocker.objects.filter(blocked=task, blocker=blocker).exists()  # links kept

    def test_the_api_complete_frees_the_task_and_closes_without_asking(
        self, client, auth, funded_user
    ):
        task, blocker, waiting = (make(funded_user, name) for name in ('Task', 'Blocker', 'W'))
        add_blocker(task, blocker)
        add_blocker(waiting, task)

        assert post(client, f'/tasks/{blocker.number}/complete', auth).status_code == 200
        assert reload(task).state == TaskState.TODO

        add_blocker(task, make(funded_user, 'Another'))  # open blocker again
        assert (
            post(client, f'/tasks/{task.number}/complete', auth).status_code == 200
        )  # no question
        assert reload(task).resolution == 'done'
        assert reload(waiting).state == TaskState.TODO

    def test_the_api_trash_restore_and_uncomplete(self, client, auth, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        assert delete(client, f'/tasks/{blocker.number}', auth).status_code == 204
        assert reload(task).state == TaskState.TODO
        post(client, f'/tasks/{blocker.number}/restore', auth)
        assert notices(funded_user, 'todo_blocker_reopened:').count() == 1

        complete_task(task)
        add_blocker(task, make(funded_user, 'Open'))  # a closed task: link only
        post(client, f'/tasks/{task.number}/uncomplete', auth)
        assert reload(task).state == TaskState.BLOCKED  # the rule applies through the API

    def test_nothing_changed_in_the_api(self, client, auth, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert set(body) == TASK_KEYS | {'attachments', 'referenced_by'}
        listed = get(client, '/tasks', auth, view='all').json()['results'][0]
        assert set(listed) == TASK_KEYS

    def test_the_api_cannot_set_a_state_or_a_blocker(self, client, auth, funded_user):
        task = make(funded_user)

        assert patch(client, f'/tasks/{task.number}', auth, {'state': 'blocked'}).status_code == 422
        assert (
            patch(client, f'/tasks/{task.number}', auth, {'blocked_by': [1000]}).status_code == 422
        )
        assert post(client, '/tasks', auth, {'title': 'x', 'blocked_by': [1000]}).status_code == 422

    def test_a_blocked_task_is_not_overdue_through_the_api_either(
        self, client, auth, funded_user, wednesday
    ):
        task = make(funded_user, 'Late', due_date=date(2026, 10, 20))
        add_blocker(task, make(funded_user, 'Blocker'))

        listed = get(client, '/tasks', auth, view='all', date_range='overdue').json()

        assert task.number not in [item['number'] for item in listed['results']]


# -- The task page: add, remove, suggestions -----------------------------------------------------


class TestTaskPage:
    def page(self, web, task) -> str:
        return text(web.get(reverse('todo:task', args=[task.number])))

    def add(self, web, task, value, **extra):
        return web.post(
            reverse('todo:blocker-add', args=[task.number]),
            {'blocker': value, 'ctx': 'page', **extra},
            **HTMX,
        )

    def test_both_lists_with_markers_and_a_remove_button(self, web, funded_user):
        task = make(funded_user, 'Main')
        open_one, done_one, wont = (make(funded_user, name) for name in ('Open', 'Done', 'Wont'))
        trashed = make(funded_user, 'Gone')
        for other in (open_one, done_one, wont, trashed):
            add_blocker(task, other)
        complete_task(done_one)
        close_task(wont, Resolution.WONT_DO)
        trash_task(trashed)
        blocked = make(funded_user, 'Waits')
        add_blocker(blocked, task)

        body = self.page(web, task)

        assert '<h2 id="blockers-title"' in body and 'Blocked by</h2>' in body
        for other in (open_one, done_one, wont, trashed):
            assert f'#{other.number} {other.title}' in body
            assert reverse('todo:blocker-remove', args=[task.number, other.number]) in body
        assert body.count('Done</span>') >= 1 and "Won't do</span>" in body
        assert 'In the trash</span>' in body
        assert 'id="blocks-title"' in body and f'data-blocks="{blocked.number}"' in body
        assert f'#{blocked.number} {blocked.title}' in body
        # the blocker is not listed under Blocks of the blocked one
        assert f'data-blocker="{blocked.number}"' not in body

    def test_the_blocks_list_is_read_only(self, web, funded_user):
        task = make(funded_user, 'Main')
        blocked = make(funded_user, 'Waits')
        add_blocker(blocked, task)

        body = self.page(web, task)

        assert reverse('todo:blocker-remove', args=[task.number, blocked.number]) not in body
        assert reverse('todo:blocker-remove', args=[blocked.number, task.number]) not in body

    def test_a_trashed_task_page_is_read_only(self, web, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')
        add_blocker(task, blocker)
        trash_task(task)

        body = self.page(web, task)

        assert f'#{blocker.number} B' in body
        assert 'id="blocker-input"' not in body
        assert reverse('todo:blocker-remove', args=[task.number, blocker.number]) not in body

    def test_the_add_box_and_its_suggestions(self, web, funded_user):
        task = make(funded_user, 'Main')

        body = self.page(web, task)

        assert 'id="blocker-input"' in body and 'list="blocker-options"' in body
        assert reverse('todo:blocker-suggest', args=[task.number]) in body

    def test_adding_by_number_blocks_the_task_and_answers_the_panel(self, web, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'The blocker')

        response = self.add(web, task, f'#{blocker.number}')

        assert response.status_code == 200
        body = text(response)
        assert 'id="task-panel"' in body and f'#{blocker.number} The blocker' in body
        assert reload(task).state == TaskState.BLOCKED
        assert 'Blocked by #' in body  # the chip's tooltip names it

    def test_a_plain_number_and_a_suggestion_value_both_work(self, web, funded_user):
        task = make(funded_user, 'Main')
        one, two = make(funded_user, 'One'), make(funded_user, 'Two')

        self.add(web, task, str(one.number))
        self.add(web, task, f'#{two.number} Two')

        assert TaskBlocker.objects.filter(blocked=task).count() == 2

    def test_adding_by_title(self, web, funded_user):
        task = make(funded_user, 'Main')
        blocker = make(funded_user, 'Get the permit')

        self.add(web, task, 'get the permit')

        assert TaskBlocker.objects.filter(blocked=task, blocker=blocker).exists()

    def test_a_part_of_a_title_that_names_one_task(self, web, funded_user):
        task = make(funded_user, 'Main')
        blocker = make(funded_user, 'Call the plumber')
        make(funded_user, 'Something else')

        self.add(web, task, 'plumber')

        assert TaskBlocker.objects.filter(blocked=task, blocker=blocker).exists()

    def test_an_ambiguous_title_is_a_refusal(self, web, funded_user):
        task = make(funded_user, 'Main')
        make(funded_user, 'Call Ann')
        make(funded_user, 'Call Bob')

        response = self.add(web, task, 'call')

        assert response.status_code == 422
        assert 'Choose a task from the suggestions, or type its number.' in text(response)
        assert response['HX-Retarget'] == '#blocker-msg'

    def test_a_title_never_finds_the_task_itself_or_a_closed_task(self, web, funded_user):
        task = make(funded_user, 'Main thing')
        done = make(funded_user, 'Main done')
        complete_task(done)

        assert self.add(web, task, 'main').status_code == 422

    @pytest.mark.parametrize(
        ('value', 'message'),
        [
            ('999999', 'There is no task #999999.'),
            ('', 'Enter the number of a task.'),
            ('!!!', 'Enter the number of a task.'),
        ],
    )
    def test_refusals(self, web, funded_user, value, message):
        task = make(funded_user, 'Main')

        response = self.add(web, task, value)

        assert response.status_code == 422 and message in text(response)
        assert response['HX-Retarget'] == '#blocker-msg'
        assert not TaskBlocker.objects.exists()

    def test_self_trash_foreign_cycle_and_cap(self, web, funded_user, other_user, config):
        config.task_price = 0
        config.save()
        task, other = make(funded_user, 'Main'), make(funded_user, 'Other')
        trashed = make(funded_user, 'Trashed')
        trash_task(trashed)
        theirs = make_task(other_user, number=7003)

        assert 'A task cannot block itself.' in text(self.add(web, task, str(task.number)))
        assert 'is in the trash' in text(self.add(web, task, str(trashed.number)))
        assert f'There is no task #{theirs.number}.' in text(
            self.add(web, task, str(theirs.number))
        )
        add_blocker(other, task)
        cycle = text(self.add(web, task, str(other.number)))
        assert f'#{other.number} is already blocked by #{task.number}' in cycle
        full = make(funded_user, 'Full')
        for index in range(MAX_BLOCKERS_PER_TASK):
            add_blocker(full, make(funded_user, f'B{index}'))
        assert 'at most 20 blockers' in text(self.add(web, full, str(other.number)))

    def test_a_refusal_spends_no_rate_limit_budget_and_a_success_one(
        self, web, funded_user, settings
    ):
        from .api_support import limit_rule

        limit_rule(settings, 'TODO_EDIT_USER', limit=2)
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')

        for _ in range(3):  # three refusals
            assert self.add(web, task, '999999').status_code == 422
        assert self.add(web, task, str(blocker.number)).status_code == 200  # 1 of 2
        assert self.remove(web, task, blocker).status_code == 200  # 2 of 2
        assert self.add(web, task, str(blocker.number)).status_code == 429

    def remove(self, web, task, blocker):
        return web.post(
            reverse('todo:blocker-remove', args=[task.number, blocker.number]),
            {'ctx': 'page'},
            **HTMX,
        )

    def test_removing(self, web, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')
        add_blocker(task, blocker)

        response = self.remove(web, task, blocker)

        assert response.status_code == 200 and 'id="task-panel"' in text(response)
        assert not TaskBlocker.objects.exists() and reload(task).state == TaskState.TODO

    def test_removing_a_closed_or_trashed_blocker(self, web, funded_user):
        task, done, trashed = (make(funded_user, name) for name in ('Main', 'D', 'T'))
        add_blocker(task, done)
        add_blocker(task, trashed)
        complete_task(done)
        trash_task(trashed)

        assert self.remove(web, task, done).status_code == 200
        assert self.remove(web, task, trashed).status_code == 200
        assert not TaskBlocker.objects.exists()

    def test_not_ones_own_is_a_404(self, web, funded_user, other_user):
        theirs = make_task(other_user, number=7004)
        mine = make_task(funded_user, number=7005)

        assert self.add(web, theirs, '7005').status_code == 404
        assert self.remove(web, theirs, mine).status_code == 404
        assert (
            web.post(
                reverse('todo:blocker-remove', args=[mine.number, theirs.number]), **HTMX
            ).status_code
            == 404
        )

    def test_a_trashed_task_is_a_404_for_a_change(self, web, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')
        trash_task(task)

        assert self.add(web, task, str(blocker.number)).status_code == 404

    def test_without_htmx_it_redirects_and_flashes(self, web, funded_user):
        task = make(funded_user, 'Main')

        response = web.post(
            reverse('todo:blocker-add', args=[task.number]),
            {'blocker': '999999'},
            HTTP_REFERER=reverse('todo:task', args=[task.number]),
        )

        assert response.status_code == 302 and response['Location'].endswith(
            reverse('todo:task', args=[task.number])
        )

    def test_it_needs_a_login(self, funded_user):
        task = make(funded_user)

        response = Client().post(reverse('todo:blocker-add', args=[task.number]), {'blocker': '1'})

        assert response.status_code == 302 and '/login' in response['Location']

    def test_the_suggestions(self, web, funded_user):
        task = make(funded_user, 'Main')
        one, two = make(funded_user, 'Pay rent'), make(funded_user, 'Call dentist')
        done = make(funded_user, 'Paid already')
        complete_task(done)
        add_blocker(task, two)  # already a blocker: not offered again

        response = web.get(
            reverse('todo:blocker-suggest', args=[task.number]), {'blocker': 'pa'}, **HTMX
        )

        body = text(response)
        assert f'value="#{one.number}"' in body and 'label="Pay rent"' in body
        assert 'Paid already' not in body  # closed
        assert 'Call dentist' not in body and f'#{task.number}"' not in body

    def test_suggestions_by_number_and_the_cap(self, web, funded_user):
        task = make(funded_user, 'Main')
        for index in range(15):
            make(funded_user, f'Task {index}')

        everything = web.get(reverse('todo:blocker-suggest', args=[task.number]), **HTMX)
        by_number = web.get(
            reverse('todo:blocker-suggest', args=[task.number]),
            {'blocker': f'#{task.number + 3}'},
            **HTMX,
        )

        assert text(everything).count('<option') == 10
        assert text(by_number).count('<option') == 1

    def test_suggestions_are_the_users_own_and_need_htmx(self, web, funded_user, other_user):
        make_task(other_user, number=7006, title='Secret plan')
        task = make(funded_user, 'Main')

        body = text(
            web.get(
                reverse('todo:blocker-suggest', args=[task.number]), {'blocker': 'secret'}, **HTMX
            )
        )
        direct = web.get(reverse('todo:blocker-suggest', args=[task.number]))

        assert 'Secret' not in body
        assert direct.status_code == 302
        theirs = make_task(other_user, number=7007)
        assert (
            web.get(reverse('todo:blocker-suggest', args=[theirs.number]), **HTMX).status_code
            == 404
        )

    def test_the_helper_finders(self, funded_user):
        task = make(funded_user, 'Main')
        other = make(funded_user, 'Other thing')

        assert find_blocker(funded_user, f'#{other.number}', task) == other
        assert find_blocker(funded_user, 'other thing', task) == other
        assert [t.pk for t in suggestions(funded_user, task, '')] == [other.pk]


# -- Closing with open blockers --------------------------------------------------------------------


class TestClosingAsks:
    def setup_pair(self, user):
        task, blocker = make(user, 'Main'), make(user, 'Get permit')
        add_blocker(task, blocker)
        return task, blocker

    def test_a_close_without_the_confirmation_is_refused_naming_the_blockers(
        self, web, funded_user
    ):
        task, blocker = self.setup_pair(funded_user)

        response = web.post(reverse('todo:complete', args=[task.number]), {'ctx': 'page'}, **HTMX)

        assert response.status_code == 422
        assert f'This task is still blocked by #{blocker.number} Get permit.' in text(response)
        assert 'Confirm to close it anyway.' in text(response)
        assert reload(task).completed_at is None

    def test_wont_do_asks_too(self, web, funded_user):
        task, _ = self.setup_pair(funded_user)

        response = web.post(reverse('todo:wont-do', args=[task.number]), {'ctx': 'page'}, **HTMX)

        assert response.status_code == 422 and reload(task).completed_at is None

    @pytest.mark.parametrize('name', ['complete', 'wont-do'])
    def test_with_the_confirmation_it_closes_and_keeps_the_links(self, web, funded_user, name):
        task, blocker = self.setup_pair(funded_user)

        response = web.post(
            reverse(f'todo:{name}', args=[task.number]),
            {'ctx': 'page', 'confirm_blockers': '1'},
            **HTMX,
        )

        assert response.status_code == 200
        closed = reload(task)
        assert closed.completed_at is not None
        assert closed.resolution == ('done' if name == 'complete' else 'wont_do')
        assert TaskBlocker.objects.filter(blocked=task, blocker=blocker).exists()

    def test_a_closed_blocker_does_not_need_asking(self, web, funded_user):
        task, blocker = self.setup_pair(funded_user)
        complete_task(blocker)

        response = web.post(reverse('todo:complete', args=[task.number]), {'ctx': 'page'}, **HTMX)

        assert response.status_code == 200 and reload(task).completed_at is not None

    def test_without_htmx_the_refusal_is_a_flash(self, web, funded_user):
        task, _ = self.setup_pair(funded_user)

        response = web.post(
            reverse('todo:complete', args=[task.number]),
            HTTP_REFERER=reverse('todo:all'),
        )

        assert response.status_code == 302 and reload(task).completed_at is None

    def test_the_row_circle_opens_the_dialog_with_the_count(self, web, funded_user):
        task, blocker = self.setup_pair(funded_user)
        plain = make(funded_user, 'Plain')

        body = text(web.get(reverse('todo:all')))

        row = body[body.index(f'id="task-{task.number}"') : body.index(f'id="task-{plain.number}"')]
        assert 'todoOpenCloseDialog($el)' in row and 'data-blockers="1"' in row
        assert reverse('todo:open-blockers', args=[task.number]) in row
        plain_row = body[body.index(f'id="task-{plain.number}"') :]
        assert 'data-complete' in plain_row  # a task with nothing open posts straight away

    def test_the_page_buttons_carry_the_count(self, web, funded_user):
        task, _ = self.setup_pair(funded_user)

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert body.count('data-blockers="1"') == 2  # Complete and Close as won't do
        assert 'name="confirm_blockers"' in body  # the dialog, in the layout

    def test_the_open_blockers_as_json(self, web, funded_user):
        task, blocker = self.setup_pair(funded_user)
        done = make(funded_user, 'Done one')
        add_blocker(task, done)
        complete_task(done)

        response = web.get(reverse('todo:open-blockers', args=[task.number]))

        assert response.json() == {
            'blockers': [{'number': blocker.number, 'title': 'Get permit'}],
            'subtasks': [],
        }
        assert 'no-store' in response['Cache-Control']

    def test_the_json_is_private(self, web, other_user):
        theirs = make_task(other_user, number=7008)

        assert web.get(reverse('todo:open-blockers', args=[theirs.number])).status_code == 404
        assert Client().get(reverse('todo:open-blockers', args=[theirs.number])).status_code == 302


# -- Rows, chips, the state menu, Today ------------------------------------------------------------


class TestRows:
    def test_the_chip_says_blocked_and_names_the_open_blockers(self, web, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'Get permit')
        add_blocker(task, blocker)

        body = text(web.get(reverse('todo:all')))

        row = body[
            body.index(f'id="task-{task.number}"') : body.index(f'id="task-{blocker.number}"')
        ]
        assert 'Blocked' in row
        assert f'title="Blocked by #{blocker.number} Get permit"' in row
        assert 'data-group="muted"' in row
        title = row[row.index('class="task-title') :].split('</a>')[0]
        assert 'text-gray-500' in title and 'line-through' not in title  # dimmed, not struck

    def test_the_menu_offers_three_states_and_never_blocked(self, web, funded_user):
        task = make(funded_user, 'Main')
        add_blocker(task, make(funded_user, 'B'))

        body = text(web.get(reverse('todo:task', args=[task.number])))

        menu = body[body.index('role="menu"') : body.index('role="menu"') + 4000]
        for label in ('To do', 'In progress', 'On hold'):
            assert label in menu
        assert '"state": "blocked"' not in body
        assert 'aria-checked="true"' not in menu  # a Blocked task has none of the three

    def test_choosing_blocked_is_a_422(self, web, funded_user):
        task = make(funded_user)

        response = web.post(
            reverse('todo:set-state', args=[task.number]), {'state': 'blocked'}, **HTMX
        )

        assert response.status_code == 422
        assert 'Choose To do, In progress or On hold.' in text(response)
        assert reload(task).state == TaskState.TODO

    def test_choosing_to_do_on_a_blocked_row_keeps_the_links(self, web, funded_user):
        task = make(funded_user, 'Main')
        add_blocker(task, make(funded_user, 'B'))

        response = web.post(
            reverse('todo:set-state', args=[task.number]),
            {'state': 'todo', 'view': 'all', 'ctx': 'row'},
            **HTMX,
        )

        assert response.status_code == 200 and reload(task).state == TaskState.TODO
        assert TaskBlocker.objects.filter(blocked=task).count() == 1

    def test_the_page_note_for_a_blocked_task(self, web, funded_user):
        task = make(funded_user, 'Main')
        add_blocker(task, make(funded_user, 'B'))

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'This task is blocked' in body and 'goes back to To do' in body

    def test_a_row_action_answers_with_the_blockers_loaded(self, web, funded_user):
        task = make(funded_user, 'Main')
        add_blocker(task, make(funded_user, 'B'))

        response = web.post(
            reverse('todo:set-state', args=[task.number]),
            {'state': 'on_hold', 'view': 'all', 'ctx': 'row'},
            **HTMX,
        )

        assert 'data-blockers="1"' in text(response)

    def test_closing_a_blocker_from_a_row_frees_the_task_and_the_bell_says_so(
        self, web, funded_user
    ):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')
        add_blocker(task, blocker)

        web.post(
            reverse('todo:complete', args=[blocker.number]),
            {'view': 'all', 'ctx': 'row'},
            **HTMX,
        )

        assert reload(task).state == TaskState.TODO
        page = text(web.get(reverse('core:notifications')))
        assert f'#{task.number} Main is no longer blocked' in page

    def test_no_query_per_row(self, web, funded_user, config):
        config.task_price = 0
        config.save()
        first = make(funded_user, 'First')
        add_blocker(first, make(funded_user, 'B0'))
        web.get(reverse('todo:all'))  # warm anything cached once per process

        with CaptureQueriesContext(connection) as few:
            web.get(reverse('todo:all'))
        for index in range(8):
            blocked = make(funded_user, f'Blocked {index}')
            add_blocker(blocked, make(funded_user, f'Blocker {index}'))
            add_blocker(blocked, make(funded_user, f'Second {index}'))
        with CaptureQueriesContext(connection) as many:
            web.get(reverse('todo:all'))

        assert len(many) == len(few)

    def test_no_query_per_row_on_today_and_the_task_page(self, web, funded_user, wednesday):
        for index in range(2):
            blocked = make(funded_user, f'Blocked {index}', due_date=TODAY)
            add_blocker(blocked, make(funded_user, f'B{index}', due_date=TODAY))
        web.get(reverse('todo:today'))
        with CaptureQueriesContext(connection) as few:
            web.get(reverse('todo:today'))
        for index in range(2, 8):
            blocked = make(funded_user, f'Blocked {index}', due_date=TODAY)
            add_blocker(blocked, make(funded_user, f'B{index}', due_date=TODAY))
        with CaptureQueriesContext(connection) as many:
            web.get(reverse('todo:today'))

        assert len(many) == len(few)

    def test_a_blocked_overdue_task_is_not_red_and_has_the_section_title(
        self, web, funded_user, wednesday
    ):
        from ..services.accounts import set_muted_overdue

        late = make(funded_user, 'Late blocked', due_date=date(2026, 10, 20))
        add_blocker(late, make(funded_user, 'B'))
        set_muted_overdue(get_account(funded_user), 'section')

        body = text(web.get(reverse('todo:today')))

        assert 'On hold & blocked (1)' in body
        assert body.index('On hold & blocked (1)') < body.index('Late blocked')
        assert '(overdue)' not in body

    def test_the_settings_page_says_blocked_too(self, web):
        body = text(web.get(reverse('todo:settings')))

        assert 'On hold and blocked tasks on Today' in body
        assert 'On hold & blocked section' in body


# -- Quick add -----------------------------------------------------------------------------------


class TestQuickAdd:
    def create(self, web, **fields):
        return web.post(
            reverse('todo:create'), {'title': 'New task', 'view': 'all', **fields}, **HTMX
        )

    def test_the_box_is_on_the_quick_add_form_only(self, web, funded_user):
        task = make(funded_user)

        quick = text(web.get(reverse('todo:all')))
        page = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'name="blocked_by"' in quick and 'placeholder="e.g. 1200, 1201"' in quick
        assert 'name="blocked_by"' not in page

    def test_numbers_make_the_new_task_blocked(self, web, funded_user):
        one, two = make(funded_user, 'One'), make(funded_user, 'Two')

        response = self.create(web, blocked_by=f'{one.number}, #{two.number}')

        assert response.status_code == 200
        new = Task.objects.get(title='New task')
        assert new.state == TaskState.BLOCKED
        assert set(new.blocked_by_links.values_list('blocker_id', flat=True)) == {one.pk, two.pk}

    def test_no_blockers_is_as_before(self, web, funded_user):
        assert self.create(web).status_code == 200
        assert Task.objects.get(title='New task').state == TaskState.TODO

    def test_the_state_select_never_offers_blocked(self, web):
        body = text(web.get(reverse('todo:all')))

        select = body[
            body.index('name="state"') : body.index('</select>', body.index('name="state"'))
        ]
        assert 'In progress' in select and 'On hold' in select and 'Blocked' not in select

    def test_blocked_posted_as_a_state_is_a_422(self, web):
        response = self.create(web, state='blocked')

        assert response.status_code == 422 and not Task.objects.exists()

    @pytest.mark.parametrize(
        ('value', 'message'),
        [
            ('abc', 'Blocked by: Enter task numbers, like 1200, 1201.'),
            ('424242', 'Blocked by: There is no task #424242.'),
        ],
    )
    def test_a_refusal_creates_nothing_and_goes_to_the_message_slot(
        self, web, funded_user, value, message
    ):
        response = self.create(web, blocked_by=value)

        assert response.status_code == 422 and message in text(response)
        assert response['HX-Retarget'] == '#quick-add-msg'
        assert not Task.objects.exists()

    def test_a_foreign_number_is_no_task(self, web, other_user):
        theirs = make_task(other_user, number=7009)

        response = self.create(web, blocked_by=str(theirs.number))

        assert f'There is no task #{theirs.number}.' in text(response)
        assert not Task.objects.filter(title='New task').exists()


# -- Search and filters ----------------------------------------------------------------------------


class TestSearchAndFilters:
    def test_the_state_filter_has_blocked(self, web, funded_user):
        body = text(web.get(reverse('todo:search')))

        assert 'name="states" value="blocked"' in body

    def test_filtering_by_blocked(self, funded_user):
        plain = make(funded_user, 'Plain')
        held = make(funded_user, 'Held', state='on_hold')
        blocked = make(funded_user, 'Blocked')
        add_blocker(blocked, make(funded_user, 'B'))

        found = set(filter_tasks(funded_user, FilterParams(states=('blocked',))))
        both = set(filter_tasks(funded_user, FilterParams(states=('blocked', 'on_hold'))))

        assert found == {blocked}
        assert both == {blocked, held} and plain not in both

    def test_describing_it(self):
        assert describe_params(FilterParams(states=('blocked',))) == 'state blocked'
        assert 'state blocked, on hold' in describe_params(
            FilterParams(states=('blocked', 'on_hold'))
        )

    def test_the_search_page_lists_blocked_tasks(self, web, funded_user):
        blocked = make(funded_user, 'Findable')
        add_blocker(blocked, make(funded_user, 'B'))
        make(funded_user, 'Findable too')

        body = text(web.get(reverse('todo:search-list'), {'states': 'blocked'}, **HTMX))

        assert 'Findable' in body and 'Findable too' not in body
        assert 'data-blockers="1"' in body  # the search rows know their blockers too

    def test_the_overdue_range_leaves_a_blocked_task_out(self, funded_user, wednesday):
        late = make(funded_user, 'Late', due_date=date(2026, 10, 20))
        add_blocker(late, make(funded_user, 'B'))

        assert late not in set(filter_tasks(funded_user, FilterParams(date_range='overdue')))


# -- Admin -----------------------------------------------------------------------------------------


class TestAdmin:
    def test_the_links_are_listed_read_only_and_on_the_task(self, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'Get permit')
        add_blocker(task, blocker)
        client = Client()
        client.force_login(UserFactory(is_staff=True, is_superuser=True))

        listing = client.get(reverse('admin:todo_taskblocker_changelist'))
        change = client.get(reverse('admin:todo_task_change', args=[task.pk]))
        blocker_page = client.get(reverse('admin:todo_task_change', args=[blocker.pk]))
        link = TaskBlocker.objects.get()
        detail = client.get(reverse('admin:todo_taskblocker_change', args=[link.pk]))

        assert listing.status_code == 200
        assert f'#{blocker.number} Get permit' in text(listing)
        assert change.status_code == 200 and 'Blocked by' in text(change)
        assert f'#{blocker.number} Get permit' in text(change)
        assert blocker_page.status_code == 200 and 'Blocks' in text(blocker_page)
        assert detail.status_code == 200
        assert client.get(reverse('admin:todo_taskblocker_add')).status_code == 403
        assert (
            client.post(reverse('admin:todo_taskblocker_delete', args=[link.pk])).status_code == 403
        )

    def test_the_search_finds_by_title_and_number(self, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'Get permit')
        add_blocker(task, blocker)
        client = Client()
        client.force_login(UserFactory(is_staff=True, is_superuser=True))

        found = client.get(reverse('admin:todo_taskblocker_changelist'), {'q': 'permit'})

        assert found.status_code == 200 and 'Get permit' in text(found)

    def test_deleting_a_user_removes_the_links(self, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')
        add_blocker(task, blocker)

        funded_user.delete()

        assert not TaskBlocker.objects.exists()

    def test_deleting_a_task_removes_its_links(self, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')
        add_blocker(task, blocker)

        Task.objects.filter(pk=blocker.pk).delete()

        assert not TaskBlocker.objects.exists() and Task.objects.filter(pk=task.pk).exists()


# -- Odds and ends ---------------------------------------------------------------------------------


class TestOthers:
    def test_the_migration_adds_the_table_and_the_state(self):
        from django.db import connection as conn

        assert 'todo_taskblocker' in conn.introspection.table_names()

    def test_update_task_does_not_touch_blockers(self, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'B')
        add_blocker(task, blocker)

        update_task(task, changes={'title': 'Renamed', 'priority': 1})

        assert reload(task).state == TaskState.BLOCKED
        assert TaskBlocker.objects.count() == 1

    def test_json_dumps_for_the_dialog_are_plain_data(self, web, funded_user):
        task = make(funded_user, '<script>alert(1)</script>')
        other = make(funded_user, 'Main')
        add_blocker(other, task)

        payload = json.loads(
            web.get(reverse('todo:open-blockers', args=[other.number])).content.decode()
        )

        assert payload['blockers'][0]['title'] == '<script>alert(1)</script>'  # data, not markup
        raw = web.get(reverse('todo:task', args=[other.number])).content.decode()
        assert '<script>alert(1)</script>' not in raw and '&lt;script&gt;alert(1)' in raw
