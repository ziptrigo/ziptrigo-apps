"""Task states and resolutions (issue #272): the open states (To do, In progress, On hold), the
resolutions (Done, Won't do), muted tasks (never overdue, reminders held), Today's grouping and
manual order, the close dialog's endpoints, the Closed page, the state and resolution filters in
search and saved filters, and the JWT API, which must look and behave as it always did."""

import importlib
import re
import uuid
from datetime import date, time, timedelta
from html import unescape
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.core.models import Notification

from ..models import (
    MUTED_STATES,
    Comment,
    MutedOverdue,
    Reminder,
    ReminderDelivery,
    Resolution,
    SavedFilter,
    Task,
    TaskState,
)
from ..services import (
    FilterParams,
    close_task,
    complete_task,
    create_task,
    dates,
    describe_params,
    filter_tasks,
    get_account,
    reminders,
    reorder,
    reschedule_overdue,
    save_filter,
    set_resolution,
    set_state,
    trash_task,
    uncomplete_task,
)
from ..services.accounts import set_muted_overdue
from .api_support import auth, get, patch, post  # noqa: F401 -- `auth` is a fixture
from .factories import make_task, utc
from .test_api_tasks import TASK_KEYS
from .test_push_reminders import configured, phone, webpush  # noqa: F401 -- fixtures

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
TODAY = date(2026, 10, 28)  # `wednesday` freezes now at 12:00 UTC
FIRES = utc(2026, 10, 28, 14, 26)  # the default 5-minute reminder of a task due at 14:30


def text(response) -> str:
    return unescape(response.content.decode())


def numbers(response) -> list[int]:
    return [task['number'] for task in response.json()['results']]


def due_task(user, **fields) -> Task:
    return create_task(user, title='Pay rent', due_date=TODAY, due_time=time(14, 30), **fields)


def reload(task: Task) -> Task:
    return Task.objects.get(pk=task.pk)


# -- The model and the migration -----------------------------------------------------------------


class TestModel:
    def test_every_new_task_is_to_do_whatever_the_source(self, funded_user, wednesday):
        for source in ('web', 'api', 'email', 'recurrence'):
            task = create_task(funded_user, title=source, source=source)
            assert task.state == TaskState.TODO and task.resolution == ''

    def test_the_next_occurrence_starts_to_do(self, funded_user, wednesday):
        task = create_task(
            funded_user, title='Rent', due_date=TODAY, rrule='FREQ=DAILY', state='in_progress'
        )

        close_task(task, Resolution.WONT_DO)

        nxt = Task.objects.get(title='Rent', completed_at__isnull=True)
        assert nxt.state == TaskState.TODO and nxt.source == 'recurrence'

    def test_a_muted_task_is_never_overdue(self, funded_user, wednesday):
        late = date(2026, 10, 1)
        todo = make_task(funded_user, due_date=late)
        held = make_task(funded_user, due_date=late, state=TaskState.ON_HOLD)

        assert todo.is_overdue(TODAY) and not held.is_overdue(TODAY)
        assert held.is_muted and not todo.is_muted
        assert MUTED_STATES == {TaskState.ON_HOLD, TaskState.BLOCKED}

    def test_the_state_group(self, funded_user):
        assert make_task(funded_user, state='todo').state_group == 'todo'
        assert make_task(funded_user, state='in_progress').state_group == 'in_progress'
        assert make_task(funded_user, state='on_hold').state_group == 'muted'

    def test_a_closed_task_with_no_resolution_reads_as_done(self, funded_user):
        old = make_task(funded_user, completed_at=utc(2026, 10, 1))

        assert not old.is_wont_do
        assert old in dates.closed_set(funded_user, 'done')
        assert old not in dates.closed_set(funded_user, 'wont_do')

    def test_the_migration_closes_the_completed_tasks_as_done(self, funded_user):
        migration = importlib.import_module('apps.todo.migrations.0011_task_states')
        done = make_task(funded_user, completed_at=utc(2026, 10, 1))
        wont = make_task(funded_user, completed_at=utc(2026, 10, 1), resolution='wont_do')
        open_ = make_task(funded_user)

        migration.mark_completed_as_done(django_apps, None)

        assert reload(done).resolution == 'done'
        assert reload(wont).resolution == 'wont_do'  # never overwritten
        assert reload(open_).resolution == '' and reload(open_).state == 'todo'

    def test_the_new_columns_have_database_defaults(self):
        """The container still serving during the deploy inserts rows without them (the #102
        lesson): each carries a `db_default` as well as a `default`."""
        from ..models import TodoAccount

        for name in ('state', 'resolution'):
            assert Task._meta.get_field(name).db_default is not None  # ty: ignore[missing-attribute]
        field = TodoAccount._meta.get_field('muted_overdue')  # ty: ignore[missing-attribute]
        assert field.db_default == MutedOverdue.DIMMED


# -- set_state -----------------------------------------------------------------------------------


class TestSetState:
    @pytest.mark.parametrize('state', ['todo', 'in_progress', 'on_hold'])
    def test_each_state(self, funded_user, state):
        task = create_task(funded_user, title='T')

        set_state(task, state)

        assert reload(task).state == state and task.state == state

    def test_the_same_state_changes_nothing(self, funded_user):
        task = create_task(funded_user, title='T')
        before = reload(task).updated_at

        set_state(task, 'todo')

        assert reload(task).updated_at == before

    @pytest.mark.parametrize('bad', ['blocked', 'done', '', None, 'x'])
    def test_an_unknown_state_is_refused(self, funded_user, bad):
        task = create_task(funded_user, title='T')

        if bad in ('', None):  # nothing said means To do, as for a new task
            assert set_state(task, bad).state == 'todo'
            return
        with pytest.raises(ValidationError, match='Choose To do, In progress or On hold'):
            set_state(task, bad)

    def test_a_closed_task_has_no_state_to_change(self, funded_user):
        task = create_task(funded_user, title='T')
        complete_task(task)

        with pytest.raises(ValidationError, match='Reopen the task'):
            set_state(task, 'on_hold')

    def test_a_trashed_task_is_refused(self, funded_user):
        task = create_task(funded_user, title='T')
        trash_task(task)

        with pytest.raises(ValidationError, match='Restore the task'):
            set_state(task, 'on_hold')

    def test_a_parent_and_its_subtasks_are_independent(self, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        set_state(parent, 'on_hold')
        assert reload(sub).state == 'todo'

        set_state(sub, 'in_progress')
        assert reload(parent).state == 'on_hold' and reload(sub).state == 'in_progress'

    def test_closing_resets_the_state_and_reopening_is_to_do(self, funded_user):
        task = create_task(funded_user, title='T', state='on_hold')

        close_task(task, Resolution.DONE)
        assert reload(task).state == 'todo'

        Task.objects.filter(pk=task.pk).update(state='in_progress')  # whatever it was
        uncomplete_task(task)
        assert reload(task).state == 'todo' and reload(task).resolution == ''

    def test_a_task_due_today_goes_to_the_end_of_its_group(self, funded_user, wednesday):
        first = create_task(funded_user, title='A', due_date=TODAY)
        second = create_task(funded_user, title='B', due_date=TODAY)
        reorder(funded_user, scope='today', ids=[first.pk, second.pk])  # Today is now manual

        set_state(first, 'in_progress')

        assert reload(first).today_position > reload(second).today_position

    def test_create_with_a_state(self, funded_user):
        assert create_task(funded_user, title='T', state='on_hold').state == 'on_hold'
        with pytest.raises(ValidationError):
            create_task(funded_user, title='T', state='blocked')


# -- Closing, Won't do, resolutions --------------------------------------------------------------


class TestClose:
    def test_done_is_what_complete_does(self, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        complete_task(parent)

        assert reload(parent).resolution == 'done' and reload(parent).completed_at
        assert reload(sub).resolution == 'done' and reload(sub).completed_at

    def test_wont_do(self, funded_user):
        task = create_task(funded_user, title='T')

        close_task(task, Resolution.WONT_DO)

        task = reload(task)
        assert task.completed_at and task.resolution == 'wont_do' and task.is_wont_do
        assert not task.is_open

    @pytest.mark.parametrize(
        ('action', 'resolution', 'closed'),
        [('done', 'done', True), ('wont_do', 'wont_do', True), ('leave', '', False)],
    )
    def test_what_happens_to_the_open_subtasks(self, funded_user, action, resolution, closed):
        parent = create_task(funded_user, title='P')
        open_sub = create_task(funded_user, title='open', parent=parent)
        done_sub = create_task(funded_user, title='done', parent=parent)
        complete_task(done_sub)
        trashed_sub = create_task(funded_user, title='trashed', parent=parent)
        trash_task(trashed_sub)

        close_task(parent, Resolution.DONE, subtasks=action)

        assert (reload(open_sub).completed_at is not None) is closed
        assert reload(open_sub).resolution == resolution
        assert reload(done_sub).resolution == 'done'  # already closed: untouched
        assert reload(trashed_sub).completed_at is None

    def test_a_parent_closed_as_wont_do_can_close_its_subtasks_as_done(self, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        close_task(parent, Resolution.WONT_DO, subtasks='done')

        assert reload(parent).resolution == 'wont_do' and reload(sub).resolution == 'done'

    def test_an_unknown_resolution_or_subtask_action_is_refused(self, funded_user):
        task = create_task(funded_user, title='T')

        with pytest.raises(ValidationError, match="Choose Done or Won't do"):
            close_task(task, 'later')
        with pytest.raises(ValidationError, match='Choose whether to close the subtasks'):
            close_task(task, Resolution.DONE, subtasks='x')
        assert reload(task).completed_at is None

    def test_a_note_becomes_a_comment(self, funded_user):
        task = create_task(funded_user, title='T')

        close_task(task, Resolution.WONT_DO, note='  Not worth it\r\nanymore  ')

        comment = Comment.objects.get(task=task)
        assert comment.body == 'Not worth it\nanymore' and comment.user == funded_user

    def test_no_note_no_comment(self, funded_user):
        task = create_task(funded_user, title='T')

        close_task(task, Resolution.WONT_DO, note='   ')

        assert not Comment.objects.exists()

    def test_a_note_is_kept_when_the_task_was_closed_meanwhile(self, funded_user):
        task = create_task(funded_user, title='T')
        stale = reload(task)
        complete_task(task)  # another tab got there first

        closed = close_task(stale, Resolution.WONT_DO, note='Because')

        assert Comment.objects.get(task=task).body == 'Because'
        assert closed.already_closed
        assert reload(task).resolution == 'done'  # the winner's resolution stands

    def test_a_lost_race_to_the_trash_still_refuses(self, funded_user):
        task = create_task(funded_user, title='T')
        stale = reload(task)
        trash_task(task)

        with pytest.raises(ValidationError, match='Restore the task'):
            close_task(stale, Resolution.WONT_DO, note='Because')
        assert not Comment.objects.exists()

    def test_set_resolution_refuses_a_task_trashed_meanwhile(self, funded_user):
        task = create_task(funded_user, title='T')
        complete_task(task)
        stale = reload(task)
        trash_task(task)

        with pytest.raises(ValidationError, match='Restore the task'):
            set_resolution(stale, Resolution.WONT_DO)
        assert reload(task).resolution == 'done'

    def test_a_too_long_note_is_refused_before_anything_is_written(self, funded_user):
        task = create_task(funded_user, title='T')

        with pytest.raises(ValidationError, match='Note is too long'):
            close_task(task, Resolution.WONT_DO, note='x' * 10_001)

        assert reload(task).completed_at is None and not Comment.objects.exists()

    def test_closing_a_closed_task_changes_nothing_but_keeps_the_note(self, funded_user):
        task = create_task(funded_user, title='T')
        close_task(task, Resolution.DONE)
        closed_at = reload(task).completed_at

        close_task(task, Resolution.WONT_DO, note='late')

        task = reload(task)
        assert task.resolution == 'done' and task.completed_at == closed_at
        assert Comment.objects.get(task=task).body == 'late'  # the typed note is not lost

    def test_a_trashed_task_cannot_be_closed(self, funded_user):
        task = create_task(funded_user, title='T')
        trash_task(task)

        with pytest.raises(ValidationError, match='Restore the task'):
            close_task(task, Resolution.WONT_DO)

    def test_a_wont_do_repeating_task_creates_and_bills_the_next_one(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=TODAY, rrule='FREQ=DAILY')
        charged = Task.objects.filter(user=funded_user).count()

        close_task(task, Resolution.WONT_DO)

        nxt = Task.objects.get(title='Rent', completed_at__isnull=True)
        assert nxt.due_date == TODAY + timedelta(days=1) and nxt.recurrence == task.recurrence
        assert Task.objects.filter(user=funded_user).count() == charged + 1
        assert nxt.charged_credits == task.charged_credits  # billed exactly as on Done

    def test_an_unpaid_next_occurrence_is_created_owed_on_wont_do_too(self, user, config):
        task = create_task(user, title='Rent', due_date=TODAY, rrule='FREQ=DAILY')
        # `user` has no credits: the first two are free, the next occurrences are owed

        close_task(task, Resolution.WONT_DO)

        assert Task.objects.filter(user=user, completed_at__isnull=True).count() == 1

    def test_set_resolution_changes_nothing_else(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=TODAY, rrule='FREQ=DAILY')
        sub = create_task(funded_user, title='S', parent=task)
        close_task(task, Resolution.DONE, subtasks='leave')
        closed_at = reload(task).completed_at
        tasks_before = Task.objects.count()

        set_resolution(task, Resolution.WONT_DO)

        assert reload(task).resolution == 'wont_do' and reload(task).completed_at == closed_at
        assert Task.objects.count() == tasks_before  # no occurrence
        assert reload(sub).completed_at is None  # no subtask touched

        set_resolution(task, Resolution.DONE)
        assert reload(task).resolution == 'done'

    def test_set_resolution_refuses_an_open_task(self, funded_user):
        task = create_task(funded_user, title='T')

        with pytest.raises(ValidationError, match='Only a closed task'):
            set_resolution(task, Resolution.DONE)
        with pytest.raises(ValidationError, match="Choose Done or Won't do"):
            set_resolution(task, 'x')

    def test_reopening_clears_the_resolution(self, funded_user):
        task = create_task(funded_user, title='T')
        close_task(task, Resolution.WONT_DO)

        uncomplete_task(task)

        task = reload(task)
        assert task.completed_at is None and task.resolution == '' and task.is_open


# -- Muted tasks: overdue, rescheduling, Today ---------------------------------------------------


class TestMutedOverdue:
    def make(self, user, wednesday):
        late = date(2026, 10, 20)
        return (
            make_task(user, title='Late todo', due_date=late),
            make_task(user, title='Late hold', due_date=late, state='on_hold'),
            make_task(user, title='Today todo', due_date=wednesday),
        )

    def test_the_overdue_set_leaves_muted_tasks_out(self, funded_user, wednesday):
        todo, held, _ = self.make(funded_user, wednesday)

        assert set(dates.overdue_set(funded_user)) == {todo}
        assert set(dates.muted_overdue_set(funded_user)) == {held}

    def test_reschedule_overdue_never_moves_a_muted_task(self, funded_user, wednesday):
        todo, held, _ = self.make(funded_user, wednesday)

        assert reschedule_overdue(funded_user) == 1

        assert reload(todo).due_date == wednesday and reload(held).due_date == date(2026, 10, 20)

    def test_the_default_shows_them_dimmed_at_the_bottom_of_overdue(
        self, web, funded_user, wednesday
    ):
        self.make(funded_user, wednesday)

        body = text(web.get(reverse('todo:today')))

        assert body.index('Late todo') < body.index('Late hold') < body.index('Today todo')
        assert 'On hold & blocked (' not in body
        assert 'id="overdue-title"' in body

    def test_the_badge_of_a_muted_task_is_not_red_or_overdue(self, web, funded_user, wednesday):
        make_task(funded_user, title='Late hold', due_date=date(2026, 10, 20), state='on_hold')

        body = text(web.get(reverse('todo:all')))

        assert '(overdue)' not in body and 'bg-red-50' not in body

    def test_the_reschedule_button_counts_only_what_it_moves(self, web, funded_user, wednesday):
        self.make(funded_user, wednesday)

        body = text(web.get(reverse('todo:today')))

        assert 'Move the date of 1 overdue task to today?' in body
        assert 'Tasks that are on hold stay where they are.' in body

    def test_only_muted_overdue_tasks_show_no_reschedule_button(self, web, funded_user, wednesday):
        make_task(funded_user, title='Late hold', due_date=date(2026, 10, 20), state='on_hold')

        body = text(web.get(reverse('todo:today')))

        assert 'Late hold' in body and 'Reschedule overdue to today' not in body

    def test_hidden_from_today(self, web, funded_user, wednesday):
        self.make(funded_user, wednesday)
        set_muted_overdue(get_account(funded_user), MutedOverdue.HIDDEN)

        body = text(web.get(reverse('todo:today')))

        assert 'Late hold' not in body and 'Late todo' in body
        assert 'On hold & blocked (' not in body

    def test_own_section(self, web, funded_user, wednesday):
        self.make(funded_user, wednesday)
        set_muted_overdue(get_account(funded_user), MutedOverdue.SECTION)

        body = text(web.get(reverse('todo:today')))

        assert 'On hold & blocked (1)' in body
        assert '<details' in body and '<details open' not in body  # collapsed
        assert (
            body.index('Today todo') < body.index('On hold & blocked (1)') < body.index('Late hold')
        )

    def test_a_muted_task_due_today_stays_in_the_today_list_in_every_setting(
        self, web, funded_user, wednesday
    ):
        make_task(funded_user, title='Hold today', due_date=wednesday, state='on_hold')
        for choice in MutedOverdue.values:
            set_muted_overdue(get_account(funded_user), choice)
            assert 'Hold today' in text(web.get(reverse('todo:today')))

    def test_the_count_beside_today(self, funded_user, wednesday):
        self.make(funded_user, wednesday)
        account = get_account(funded_user)

        assert dates.today_count(funded_user, account) == 3  # dimmed: all three are on the page
        for choice in (MutedOverdue.HIDDEN, MutedOverdue.SECTION):
            set_muted_overdue(account, choice)
            assert dates.today_count(funded_user, account) == 2

    def test_the_overdue_search_range_leaves_muted_tasks_out(self, funded_user, wednesday):
        todo, held, _ = self.make(funded_user, wednesday)

        found = filter_tasks(funded_user, FilterParams(date_range='overdue'))

        assert set(found) == {todo}

    def test_the_setting_is_validated(self, funded_user):
        with pytest.raises(ValidationError):
            set_muted_overdue(get_account(funded_user), 'nowhere')


class TestGroupsAndManualOrder:
    def make(self, user, wednesday):
        return {
            'held': make_task(user, title='Held', due_date=wednesday, state='on_hold'),
            'todo_a': make_task(user, title='TodoA', due_date=wednesday),
            'todo_b': make_task(user, title='TodoB', due_date=wednesday),
            'doing': make_task(user, title='Doing', due_date=wednesday, state='in_progress'),
        }

    def titles(self, qs) -> list[str]:
        return [task.title for task in qs]

    def test_in_progress_then_to_do_then_muted_then_the_usual_order(self, funded_user, wednesday):
        self.make(funded_user, wednesday)

        listed = dates.order_grouped(dates.todays_tasks(funded_user), wednesday)

        assert self.titles(listed) == ['Doing', 'TodoA', 'TodoB', 'Held']

    def test_the_group_applies_within_each_day_not_across_days(self, funded_user, wednesday):
        tomorrow = wednesday + timedelta(days=1)
        make_task(funded_user, title='Hold today', due_date=wednesday, state='on_hold')
        make_task(funded_user, title='Doing tomorrow', due_date=tomorrow, state='in_progress')
        make_task(funded_user, title='Todo today', due_date=wednesday)

        listed = dates.order_grouped(dates.open_tasks(funded_user), wednesday)

        assert self.titles(listed) == ['Todo today', 'Hold today', 'Doing tomorrow']

    def test_the_manual_order_works_inside_a_group(self, funded_user, wednesday):
        tasks = self.make(funded_user, wednesday)
        reorder(
            funded_user,
            scope='today',
            ids=[
                tasks['held'].pk,
                tasks['todo_b'].pk,
                tasks['doing'].pk,
                tasks['todo_a'].pk,
            ],
        )

        listed = dates.order_grouped(dates.todays_tasks(funded_user), wednesday, manual=True)

        # The dragged order only decides inside each group: B before A, and the groups stay.
        assert self.titles(listed) == ['Doing', 'TodoB', 'TodoA', 'Held']

    def test_the_today_page_orders_by_group_and_marks_the_rows(self, web, funded_user, wednesday):
        self.make(funded_user, wednesday)

        body = text(web.get(reverse('todo:today')))

        assert body.index('Doing') < body.index('TodoA') < body.index('TodoB') < body.index('Held')
        assert 'data-group="in_progress"' in body and 'data-group="muted"' in body
        assert 'data-group="todo"' in body

    def test_upcoming_uses_the_same_groups(self, web, funded_user, wednesday):
        tomorrow = wednesday + timedelta(days=1)
        make_task(funded_user, title='Held', due_date=tomorrow, state='on_hold')
        make_task(funded_user, title='Plain', due_date=tomorrow)
        make_task(funded_user, title='Doing', due_date=tomorrow, state='in_progress')

        body = text(web.get(reverse('todo:upcoming')))

        assert body.index('Doing') < body.index('Plain') < body.index('Held')

    def test_all_is_unchanged(self, web, funded_user, wednesday):
        make_task(funded_user, title='Aa', due_date=wednesday, state='on_hold')
        make_task(funded_user, title='Bb', due_date=wednesday, state='in_progress')

        body = text(web.get(reverse('todo:all')))

        assert body.index('Aa') < body.index('Bb')  # by date, then the usual order: oldest first


# -- Held reminders ------------------------------------------------------------------------------


class TestHeldReminders:
    @pytest.fixture
    def sent(self, monkeypatch):
        mails = []

        def fake(**kwargs):
            mails.append(kwargs)
            return 1, 0

        monkeypatch.setattr(reminders, 'send_email', fake)
        return mails

    def hold(self, user) -> Task:
        task = due_task(user)
        set_state(task, 'on_hold')
        return task

    def test_a_muted_task_does_not_fire(self, funded_user, wednesday):
        task = self.hold(funded_user)

        assert reminders.deliver_due(FIRES) == 0

        assert not Notification.objects.exists()
        assert Reminder.objects.get(task=task).sent_at is None

    def test_and_is_never_swept_as_stale(self, funded_user, wednesday):
        task = self.hold(funded_user)

        reminders.deliver_due(utc(2026, 10, 28, 20, 0))  # hours after it was due

        reminder = Reminder.objects.get(task=task)
        assert reminder.sent_at is None and not reminder.skipped

    def test_the_task_page_promises_a_reminder_only_when_one_is_held(
        self, web, funded_user, wednesday, freeze_now
    ):
        task = self.hold(funded_user)  # its reminder (14:26) is still ahead of 12:00
        url = reverse('todo:task', args=[task.number])

        early = text(web.get(url))

        assert 'id="muted-note"' in early and 'its reminders wait.' in early
        assert 'one reminder for what you missed' not in early

        freeze_now(2026, 10, 28, 15, 0)  # the reminder's time has passed while it is on hold

        late = text(web.get(url))

        assert 'one reminder for what you missed' in late

    def test_leaving_the_state_sends_one_reminder(
        self, funded_user, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = self.hold(funded_user)
        reminders.add_reminder(task, minutes_before=60, channel='in_app')
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            set_state(task, 'todo')

        assert Notification.objects.filter(user=funded_user).count() == 1  # one, not two
        assert ReminderDelivery.objects.filter(task=task).count() == 1
        held = Reminder.objects.filter(task=task)
        assert held.count() == 2
        assert all(r.sent_at is not None for r in held)
        # the earliest stands for the rest: it was delivered, the other was folded into it
        assert [r.skipped for r in held.order_by('fire_at')] == [False, True]

    def test_the_released_reminder_shows_sent_on_the_task_page(
        self, web, funded_user, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = self.hold(funded_user)
        freeze_now(2026, 10, 28, 15, 0)
        with django_capture_on_commit_callbacks(execute=True):
            set_state(task, 'todo')

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'Sent' in body and 'Skipped' not in body

    def test_the_union_of_the_channels_each_once(
        self, funded_user, wednesday, freeze_now, sent, django_capture_on_commit_callbacks
    ):
        task = self.hold(funded_user)  # the default one is in-app
        reminders.add_reminder(task, minutes_before=60, channel='email')
        reminders.add_reminder(task, minutes_before=90, channel='both')
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            set_state(task, 'in_progress')

        assert Notification.objects.filter(user=funded_user).count() == 1
        assert len(sent) == 1 and sent[0]['to'] == funded_user.email
        channels = sorted(
            ReminderDelivery.objects.filter(task=task).values_list('channel', flat=True)
        )
        assert channels == ['email', 'in_app']

    def test_email_only(
        self, funded_user, wednesday, freeze_now, sent, django_capture_on_commit_callbacks
    ):
        task = self.hold(funded_user)
        Reminder.objects.filter(task=task).update(channel='email')
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            set_state(task, 'todo')

        assert len(sent) == 1 and not Notification.objects.exists()

    def test_an_in_app_reminder_is_pushed_once(
        self,
        funded_user,
        wednesday,
        freeze_now,
        configured,  # noqa: F811
        phone,  # noqa: F811
        webpush,  # noqa: F811
        django_capture_on_commit_callbacks,
    ):
        task = self.hold(funded_user)
        reminders.add_reminder(task, minutes_before=60, channel='both')
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            set_state(task, 'todo')

        assert webpush.call_count == 1
        assert ReminderDelivery.objects.filter(task=task, channel='push').count() == 1

    def test_reminders_still_ahead_stay_armed(
        self, funded_user, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = self.hold(funded_user)
        reminders.add_reminder(task, minutes_before=0, channel='in_app')  # 14:30, still ahead
        freeze_now(2026, 10, 28, 14, 28)

        with django_capture_on_commit_callbacks(execute=True):
            set_state(task, 'todo')

        ahead = Reminder.objects.get(task=task, minutes_before=0)
        assert ahead.sent_at is None and not ahead.skipped
        released = Reminder.objects.get(task=task, minutes_before=5)
        assert released.sent_at is not None and not released.skipped

    def test_nothing_held_nothing_sent(self, funded_user, wednesday, freeze_now):
        task = self.hold(funded_user)  # 14:26 is still ahead of 12:00

        set_state(task, 'todo')

        assert not Notification.objects.exists() and not ReminderDelivery.objects.exists()
        assert Reminder.objects.get(task=task).sent_at is None

    def test_to_do_and_in_progress_do_not_release_anything(
        self, funded_user, wednesday, freeze_now
    ):
        task = due_task(funded_user)
        freeze_now(2026, 10, 28, 15, 0)
        Reminder.objects.filter(task=task).update(fire_at=FIRES)  # due and unsent

        set_state(task, 'in_progress')  # not leaving a muted state
        assert not Notification.objects.exists()

        set_state(task, 'on_hold')
        assert not Notification.objects.exists()

    def test_closing_a_muted_task_does_not_release_and_the_old_rules_apply(
        self, funded_user, wednesday
    ):
        task = self.hold(funded_user)

        close_task(task, Resolution.DONE)

        assert not Notification.objects.exists()
        assert reminders.deliver_due(utc(2026, 10, 28, 15, 0)) == 0  # a closed task is skipped
        reminders.deliver_due(utc(2026, 10, 28, 20, 0))  # ... and the sweep ends the old one
        assert Reminder.objects.get(task=task).skipped

    def test_a_trashed_muted_task_follows_the_ordinary_rules(self, funded_user, wednesday):
        task = self.hold(funded_user)

        trash_task(task)
        reminders.deliver_due(utc(2026, 10, 28, 20, 0))

        assert Reminder.objects.get(task=task).skipped and not Notification.objects.exists()

    def test_a_delivery_that_fails_keeps_the_state_change(
        self, funded_user, wednesday, freeze_now, monkeypatch
    ):
        task = self.hold(funded_user)
        freeze_now(2026, 10, 28, 15, 0)

        def boom(*args, **kwargs):
            raise RuntimeError('push service down')

        monkeypatch.setattr(reminders, '_deliver', boom)

        set_state(task, 'todo')

        assert reload(task).state == 'todo'
        assert Reminder.objects.get(task=task).sent_at is None  # for the job to deal with

    def test_the_job_delivers_it_afterwards_as_it_would_any_other(self, funded_user, wednesday):
        task = self.hold(funded_user)
        Task.objects.filter(pk=task.pk).update(state='todo')  # unmuted without a release

        assert reminders.deliver_due(FIRES) == 1

    def test_released_through_the_view_too(
        self, web, funded_user, wednesday, freeze_now, django_capture_on_commit_callbacks
    ):
        task = self.hold(funded_user)
        freeze_now(2026, 10, 28, 15, 0)

        with django_capture_on_commit_callbacks(execute=True):
            response = web.post(
                reverse('todo:set-state', args=[task.number]),
                {'state': 'todo', 'ctx': 'page'},
                **HTMX,
            )

        assert response.status_code == 200
        assert Notification.objects.filter(user=funded_user).count() == 1


# -- The pages and endpoints ---------------------------------------------------------------------


class TestStateEndpoint:
    def post(self, client, task, **data):
        return client.post(reverse('todo:set-state', args=[task.number]), data, **HTMX)

    def test_a_row_answers_the_row_with_the_new_chip(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = self.post(web, task, state='in_progress', view='all', ctx='row')

        body = text(response)
        assert response.status_code == 200 and f'id="task-{task.number}"' in body
        assert 'In progress' in body and reload(task).state == 'in_progress'

    @pytest.mark.parametrize('view', ['today', 'upcoming'])
    def test_on_today_and_upcoming_the_whole_list_comes_back(
        self, web, funded_user, wednesday, view
    ):
        due = wednesday if view == 'today' else wednesday + timedelta(days=1)
        first = create_task(funded_user, title='First', due_date=due)
        second = create_task(funded_user, title='Second', due_date=due)

        response = self.post(web, second, state='in_progress', view=view, ctx='row')

        body = text(response)
        assert response['HX-Retarget'] == '#task-list' and response['HX-Reswap'] == 'innerHTML'
        assert body.index('Second') < body.index('First')  # it moved to its group
        assert f'id="task-{first.number}"' in body

    def test_a_row_that_leaves_today_goes_when_the_account_hides_muted_overdue(
        self, web, funded_user, wednesday
    ):
        task = create_task(funded_user, title='Late', due_date=wednesday - timedelta(days=3))
        set_muted_overdue(get_account(funded_user), MutedOverdue.HIDDEN)

        body = text(self.post(web, task, state='on_hold', view='today', ctx='row'))

        assert 'Late' not in body

    def test_from_the_task_page_the_panel_comes_back(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = self.post(web, task, state='on_hold', ctx='page')

        body = text(response)
        assert 'id="task-panel"' in body and 'This task is on hold' in body

    def test_without_htmx_it_redirects(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = web.post(reverse('todo:set-state', args=[task.number]), {'state': 'on_hold'})

        assert response.status_code == 302 and reload(task).state == 'on_hold'

    @pytest.mark.parametrize('state', ['blocked', '', 'x'])
    def test_an_unknown_state_is_a_422(self, web, funded_user, state):
        task = create_task(funded_user, title='T')

        response = self.post(web, task, state=state, ctx='page')

        assert response.status_code == 422 and response['HX-Retarget'] == '#task-msg'
        assert 'Choose To do, In progress or On hold.' in text(response)

    def test_a_closed_task_is_a_422(self, web, funded_user):
        task = create_task(funded_user, title='T')
        complete_task(task)

        response = self.post(web, task, state='on_hold', ctx='page')

        assert response.status_code == 422 and 'Reopen the task' in text(response)

    def test_another_users_task_is_a_404(self, web, other_user):
        theirs = create_task_for(other_user)

        assert self.post(web, theirs, state='on_hold').status_code == 404
        assert reload(theirs).state == 'todo'

    def test_the_edit_budget_is_charged_after_validation(
        self, web, funded_user, settings, ratelimit_clock
    ):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (2, 60)}
        task = create_task(funded_user, title='T')
        for _ in range(5):  # a refused form spends nothing
            assert self.post(web, task, state='x', ctx='page').status_code == 422

        assert self.post(web, task, state='on_hold', ctx='page').status_code == 200
        assert self.post(web, task, state='todo', ctx='page').status_code == 200
        assert self.post(web, task, state='on_hold', ctx='page').status_code == 429

    def test_the_chip_is_on_open_rows_only(self, web, funded_user):
        open_task = create_task(funded_user, title='Open one')
        done = create_task(funded_user, title='Done one')
        complete_task(done)

        body = text(web.get(reverse('todo:all')))
        closed = text(web.get(reverse('todo:closed')))

        assert reverse('todo:set-state', args=[open_task.number]) in body
        assert reverse('todo:set-state', args=[done.number]) not in closed

    def test_quick_add_takes_a_state(self, web, funded_user):
        response = web.post(
            reverse('todo:create'), {'title': 'Held', 'state': 'on_hold', 'view': 'all'}, **HTMX
        )

        assert response.status_code == 200
        assert Task.objects.get(title='Held').state == 'on_hold'

    def test_quick_add_without_one_is_to_do(self, web):
        web.post(reverse('todo:create'), {'title': 'Plain', 'view': 'all'}, **HTMX)

        assert Task.objects.get(title='Plain').state == 'todo'

    def test_quick_add_refuses_an_unknown_state(self, web):
        response = web.post(
            reverse('todo:create'), {'title': 'X', 'state': 'blocked', 'view': 'all'}, **HTMX
        )

        assert response.status_code == 422 and not Task.objects.filter(title='X').exists()


def create_task_for(user) -> Task:
    return make_task(user, title='Theirs')


class TestCloseEndpoints:
    def post(self, client, name, task, **data):
        return client.post(reverse(name, args=[task.number]), data, **HTMX)

    def test_complete_with_the_subtask_choice(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        self.post(web, 'todo:complete', parent, subtasks='wont_do', ctx='page')

        assert reload(parent).resolution == 'done' and reload(sub).resolution == 'wont_do'

    def test_complete_leaving_the_subtasks_open(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        response = self.post(web, 'todo:complete', parent, subtasks='leave', view='all', ctx='row')

        assert reload(sub).completed_at is None
        assert f'id="task-{sub.number}" hx-swap-oob="delete"' not in text(response)

    def test_complete_without_a_choice_closes_them_as_done(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        response = self.post(web, 'todo:complete', parent, view='all', ctx='row')

        assert reload(sub).resolution == 'done'
        assert f'id="task-{sub.number}" hx-swap-oob="delete"' in text(response)

    def test_a_bad_subtask_choice_is_a_422(self, web, funded_user):
        parent = create_task(funded_user, title='P')

        response = self.post(web, 'todo:complete', parent, subtasks='x', ctx='page')

        assert response.status_code == 422 and reload(parent).completed_at is None
        assert 'Choose whether to close the subtasks' in text(response)

    def test_wont_do_with_a_note(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = self.post(web, 'todo:wont-do', task, note='Not needed', ctx='page')

        body = text(response)
        assert reload(task).resolution == 'wont_do'
        assert Comment.objects.get(task=task).body == 'Not needed'
        assert 'Not needed' in body  # the comments section is refreshed with the panel
        assert "Won't do" in body

    def test_wont_do_from_the_dialog_is_a_plain_post_that_redirects_to_the_page(
        self, web, funded_user
    ):
        task = create_task(funded_user, title='T')

        response = web.post(
            reverse('todo:wont-do', args=[task.number]), {'ctx': 'page', 'note': 'x'}
        )

        assert response.status_code == 302 and response['Location'] == reverse(
            'todo:task', args=[task.number]
        )
        assert reload(task).is_wont_do

    def test_wont_do_closes_the_subtasks_as_chosen(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        self.post(web, 'todo:wont-do', parent, subtasks='leave', ctx='page')

        assert reload(parent).is_wont_do and reload(sub).completed_at is None

    def test_a_too_long_note_is_a_422_and_closes_nothing(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = self.post(web, 'todo:wont-do', task, note='x' * 10_001, ctx='page')

        assert response.status_code == 422 and 'Note is too long' in text(response)
        assert reload(task).completed_at is None

    def test_wont_do_has_a_dialog_on_the_page_and_no_row_or_push_button(self, web, funded_user):
        task = create_task(funded_user, title='T')

        page = text(web.get(reverse('todo:task', args=[task.number])))
        rows = text(web.get(reverse('todo:all')))

        assert reverse('todo:wont-do', args=[task.number]) in page and 'Close as won' in page
        assert 'id="close-note"' in page  # the dialog's optional note
        assert reverse('todo:wont-do', args=[task.number]) not in rows

    def test_another_users_task_is_a_404(self, web, other_user):
        theirs = create_task_for(other_user)

        for name in ('todo:wont-do', 'todo:resolution', 'todo:complete'):
            assert self.post(web, name, theirs, resolution='done').status_code == 404
        assert reload(theirs).completed_at is None

    def test_the_resolution_of_a_closed_task_can_be_changed(self, web, funded_user):
        task = create_task(funded_user, title='T')
        complete_task(task)

        response = self.post(web, 'todo:resolution', task, resolution='wont_do', ctx='page')

        assert response.status_code == 200 and reload(task).resolution == 'wont_do'
        assert 'Mark as done' in text(response)

    def test_changing_the_resolution_of_an_open_task_is_a_422(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = self.post(web, 'todo:resolution', task, resolution='done', ctx='page')

        assert response.status_code == 422 and 'Only a closed task' in text(response)

    @pytest.mark.parametrize('resolution', ['', 'maybe'])
    def test_a_bad_resolution_is_a_422(self, web, funded_user, resolution):
        task = create_task(funded_user, title='T')
        complete_task(task)

        response = self.post(web, 'todo:resolution', task, resolution=resolution, ctx='page')

        assert response.status_code == 422 and "Choose Done or Won't do" in text(response)

    def test_the_edit_budget_is_shared(self, web, funded_user, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        first = create_task(funded_user, title='A')
        second = create_task(funded_user, title='B')

        assert self.post(web, 'todo:wont-do', first, ctx='page').status_code == 200
        assert self.post(web, 'todo:wont-do', second, ctx='page').status_code == 429

    def test_a_note_also_spends_the_comment_budget(
        self, web, funded_user, settings, ratelimit_clock
    ):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_EDIT_USER': (2, 60),
            'TODO_COMMENT_USER': (1, 60),
        }
        first = create_task(funded_user, title='A')
        second = create_task(funded_user, title='B')

        assert self.post(web, 'todo:wont-do', first, note='why', ctx='page').status_code == 200
        refused = self.post(web, 'todo:wont-do', second, note='why', ctx='page')

        assert refused.status_code == 429 and reload(second).completed_at is None
        # the refusal spent nothing: without a note the second close still has an edit left
        assert self.post(web, 'todo:wont-do', second, ctx='page').status_code == 200

    def test_losing_a_race_says_so_and_keeps_the_note(self, web, funded_user, monkeypatch):
        task = create_task(funded_user, title='T')
        stale = reload(task)
        complete_task(task)
        monkeypatch.setattr('apps.todo.views.tasks.owned_task', lambda *a, **k: stale)

        response = web.post(
            reverse('todo:wont-do', args=[task.number]), {'ctx': 'page', 'note': 'Because'}
        )

        assert response.status_code == 302
        assert Comment.objects.get(task=task).body == 'Because'
        page = text(web.get(response['Location']))
        assert 'This task was already closed.' in page

    def test_the_task_page_of_a_closed_task(self, web, funded_user):
        done = create_task(funded_user, title='Done one')
        complete_task(done)
        wont = create_task(funded_user, title='Wont one')
        close_task(wont, Resolution.WONT_DO)

        done_page = text(web.get(reverse('todo:task', args=[done.number])))
        wont_page = text(web.get(reverse('todo:task', args=[wont.number])))

        assert 'Done 10/' in done_page or 'Done ' in done_page
        assert 'Mark as won' in done_page and 'Reopen' in done_page
        assert "Won't do " in wont_page and 'Mark as done' in wont_page

    def test_reopening_from_the_page_makes_it_to_do(self, web, funded_user):
        task = create_task(funded_user, title='T')
        close_task(task, Resolution.WONT_DO)

        self.post(web, 'todo:uncomplete', task, ctx='page')

        task = reload(task)
        assert task.is_open and task.state == 'todo' and task.resolution == ''

    def test_the_wont_do_circle_reopens(self, web, funded_user):
        task = create_task(funded_user, title='T')
        close_task(task, Resolution.WONT_DO)

        body = text(web.get(reverse('todo:closed')))

        assert 'is-wont-do' in body and reverse('todo:uncomplete', args=[task.number]) in body
        assert 'fa-circle-xmark' not in body.split('is-wont-do')[0][-0:]  # the pill has its own

    def test_the_close_dialog_asks_for_a_parent_with_open_subtasks(self, web, funded_user):
        parent = create_task(funded_user, title='P')
        create_task(funded_user, title='S', parent=parent)

        page = text(web.get(reverse('todo:task', args=[parent.number])))

        assert 'data-count="1"' in page and 'name="subtasks"' in page
        assert 'Leave' in page


class TestClosedPage:
    @pytest.fixture
    def closed(self, funded_user):
        done = create_task(funded_user, title='Finished')
        close_task(done, Resolution.DONE)
        wont = create_task(funded_user, title='Skipped')
        close_task(wont, Resolution.WONT_DO)
        create_task(funded_user, title='Still open')
        return done, wont

    def test_both_resolutions_and_each_row_says_which(self, web, closed):
        body = text(web.get(reverse('todo:closed')))

        assert 'Finished' in body and 'Skipped' in body and 'Still open' not in body
        assert '<h1' in body and '>Closed</h1>' in body.replace('\n', '')
        assert 'Closed - To-do' in body
        assert 'title="Closed as done"' in body and 'title="Closed as won\'t do"' in body

    def test_the_resolution_filter(self, web, closed):
        done_page = text(web.get(reverse('todo:closed'), {'resolution': 'done'}))
        wont_page = text(web.get(reverse('todo:closed'), {'resolution': 'wont_do'}))
        junk = text(web.get(reverse('todo:closed'), {'resolution': 'junk'}))

        assert 'Finished' in done_page and 'Skipped' not in done_page
        assert 'Skipped' in wont_page and 'Finished' not in wont_page
        assert 'Finished' in junk and 'Skipped' in junk  # an unknown one is "All"

    def test_the_tabs(self, web, closed):
        body = text(web.get(reverse('todo:closed'), {'resolution': 'wont_do'}))

        assert 'aria-label="Closed tasks"' in body
        assert 'aria-current="page"' in body
        for label in ('All', 'Done', "Won't do"):
            assert f'>{label}</a>' in body

    def test_the_list_partial_and_its_push_url(self, web, closed):
        response = web.get(
            reverse('todo:list'), {'view': 'closed', 'resolution': 'wont_do'}, **HTMX
        )

        assert response['HX-Push-Url'] == f'{reverse("todo:closed")}?resolution=wont_do'
        assert 'Skipped' in text(response) and 'Finished' not in text(response)

    def test_the_old_partial_view_name_still_works(self, web, closed):
        response = web.get(reverse('todo:list'), {'view': 'completed'}, **HTMX)

        assert response['HX-Push-Url'] == reverse('todo:closed')

    def test_the_old_url_redirects_with_its_query(self, web):
        response = web.get('/todo/completed/')
        with_query = web.get('/todo/completed/?resolution=done')

        assert response.status_code == 302 and response['Location'] == '/todo/closed/'
        assert with_query['Location'] == '/todo/closed/?resolution=done'

    def test_the_old_url_asks_anonymous_visitors_to_sign_in(self, client, user):
        response = client.get('/todo/completed/')

        assert response.status_code == 302 and '/account/login' in response['Location']

    def test_the_sidebar_says_closed(self, web):
        body = text(web.get(reverse('todo:today')))

        assert '<span>Closed</span>' in body and '<span>Completed</span>' not in body

    def test_an_old_page_posting_the_old_view_still_gets_an_answer(self, web, funded_user):
        task = create_task(funded_user, title='T')
        complete_task(task)

        response = web.post(
            reverse('todo:uncomplete', args=[task.number]),
            {'view': 'completed', 'ctx': 'row'},
            **HTMX,
        )

        assert response.status_code == 200 and '<li' not in response.content.decode()

    def test_empty_states(self, web):
        assert 'No closed tasks yet.' in text(web.get(reverse('todo:closed')))
        assert "No tasks closed as won't do yet." in text(
            web.get(reverse('todo:closed'), {'resolution': 'wont_do'})
        )


class TestSettingsPage:
    def test_the_choice_is_saved(self, web, funded_user):
        response = web.post(
            reverse('todo:settings'),
            {'default_due_time': '09:00', 'muted_overdue': 'section'},
            **HTMX,
        )

        assert response.status_code == 200
        assert get_account(funded_user).muted_overdue == 'section'

    def test_a_post_without_it_leaves_it_alone(self, web, funded_user):
        set_muted_overdue(get_account(funded_user), 'hidden')

        web.post(reverse('todo:settings'), {'default_due_time': '10:00'}, **HTMX)

        assert get_account(funded_user).muted_overdue == 'hidden'

    def test_an_unknown_choice_is_a_422(self, web, funded_user):
        response = web.post(
            reverse('todo:settings'), {'default_due_time': '09:00', 'muted_overdue': 'x'}, **HTMX
        )

        assert response.status_code == 422
        assert 'Choose Today (dimmed), Hidden from Today or On hold & blocked section.' in text(
            response
        )
        assert get_account(funded_user).muted_overdue == 'dimmed'

    def test_the_page_lists_the_three_choices(self, web):
        body = text(web.get(reverse('todo:settings')))

        for label in ('Today, dimmed', 'Hidden from Today', 'On hold & blocked section'):
            assert label in body


# -- Search and saved filters --------------------------------------------------------------------


class TestFilterParams:
    def test_states_and_resolution_round_trip(self):
        params = FilterParams(
            include_completed=True, states=('in_progress', 'on_hold'), resolution='wont_do'
        )

        assert FilterParams.from_dict(params.to_dict()) == params
        assert params.to_dict()['states'] == ['in_progress', 'on_hold']
        assert params.to_dict()['resolution'] == 'wont_do'
        assert (
            'states=in_progress' in params.to_query() and 'resolution=wont_do' in params.to_query()
        )

    def test_the_stored_form_is_unchanged_until_they_are_used(self):
        assert 'states' not in FilterParams().to_dict()
        assert 'resolution' not in FilterParams().to_dict()
        assert FilterParams(states=('todo',)).to_dict()['states'] == ['todo']

    def test_reading_is_lenient(self):
        params = FilterParams.from_raw(
            {
                'states': ['todo,on_hold', 'blocked', 'bogus'],
                'resolution': ['wont_do'],
                'include_completed': ['1'],
            }
        )

        assert params.states == ('blocked', 'on_hold', 'todo')
        assert params.resolution == 'wont_do'

    def test_a_resolution_needs_include_closed(self):
        assert FilterParams.from_raw({'resolution': ['done']}).resolution == ''
        assert (
            FilterParams.from_raw({'resolution': ['maybe'], 'include_completed': ['1']}).resolution
            == ''
        )

    def test_states_are_criteria_the_resolution_alone_is_not(self):
        assert FilterParams(states=('todo',)).has_criteria
        assert not FilterParams(include_completed=True, resolution='done').has_criteria

    def test_the_summary(self, funded_user):
        params = FilterParams(
            states=('in_progress', 'on_hold'), include_completed=True, resolution='wont_do'
        )

        assert describe_params(params) == "state in progress, on hold · including won't do"
        assert describe_params(FilterParams(include_completed=True)) == 'including closed'


class TestFilteringTasks:
    @pytest.fixture
    def zoo(self, funded_user):
        return {
            'todo': create_task(funded_user, title='Todo'),
            'doing': create_task(funded_user, title='Doing', state='in_progress'),
            'held': create_task(funded_user, title='Held', state='on_hold'),
            'done': close(funded_user, 'Done', Resolution.DONE),
            'wont': close(funded_user, 'Wont', Resolution.WONT_DO),
        }

    def titles(self, user, **params) -> set[str]:
        return {task.title for task in filter_tasks(user, FilterParams(**params))}

    def test_the_state_filter_is_multi_select_over_open_tasks(self, funded_user, zoo):
        assert self.titles(funded_user, states=('in_progress',)) == {'Doing'}
        assert self.titles(funded_user, states=('in_progress', 'on_hold')) == {'Doing', 'Held'}
        assert self.titles(funded_user, states=('todo',)) == {'Todo'}

    def test_closed_tasks_are_left_out_unless_included(self, funded_user, zoo):
        assert self.titles(funded_user) == {'Todo', 'Doing', 'Held'}
        assert self.titles(funded_user, include_completed=True) == {
            'Todo',
            'Doing',
            'Held',
            'Done',
            'Wont',
        }

    def test_the_resolution_filter(self, funded_user, zoo):
        assert self.titles(funded_user, include_completed=True, resolution='done') == {
            'Todo',
            'Doing',
            'Held',
            'Done',
        }
        assert self.titles(funded_user, include_completed=True, resolution='wont_do') == {
            'Todo',
            'Doing',
            'Held',
            'Wont',
        }

    def test_closed_tasks_are_not_narrowed_by_a_state(self, funded_user, zoo):
        found = self.titles(
            funded_user, include_completed=True, resolution='wont_do', states=('on_hold',)
        )

        assert found == {'Held', 'Wont'}

    def test_with_search_text(self, funded_user, zoo):
        found = self.titles(
            funded_user, q='*o*', include_completed=True, resolution='wont_do', states=('todo',)
        )

        assert found == {'Todo', 'Wont'}

    def test_a_saved_filter_keeps_them(self, funded_user, zoo):
        saved = save_filter(
            funded_user,
            'Paused',
            FilterParams(states=('on_hold',), include_completed=True, resolution='done'),
        )

        reread = SavedFilter.objects.get(pk=saved.pk)
        assert reread.params['states'] == ['on_hold'] and reread.params['resolution'] == 'done'
        from ..services.saved_filters import stored_params

        assert stored_params(reread, funded_user).states == ('on_hold',)

    def test_the_search_page(self, web, zoo):
        body = text(web.get(reverse('todo:search'), {'states': 'on_hold'}))

        assert 'Held' in body and 'Doing' not in body and 'Todo' not in body
        assert 'Include closed' in body and 'name="states"' in body
        assert 'id="search-resolution"' in body

    def test_the_search_page_with_the_resolution(self, web, zoo):
        body = text(
            web.get(
                reverse('todo:search'),
                {'q': '*', 'include_completed': '1', 'resolution': 'wont_do'},
            )
        )

        assert f'id="task-{zoo["wont"].number}"' in body
        assert f'id="task-{zoo["done"].number}"' not in body

    def test_the_nothing_matches_hint_says_closed(self, web):
        body = text(web.get(reverse('todo:search'), {'q': 'zzzz'}))

        assert 'Closed tasks are left out unless you tick Include closed.' in body

    def test_saving_a_filter_from_the_page_keeps_the_new_criteria(self, web, funded_user, zoo):
        response = web.post(
            reverse('todo:filters'),
            {
                'name': 'Paused',
                'states': ['on_hold', 'in_progress'],
                'include_completed': '1',
                'resolution': 'wont_do',
            },
            **HTMX,
        )

        assert response.status_code in (200, 204)
        saved = SavedFilter.objects.get(user=funded_user, name='Paused')
        assert saved.params['states'] == ['in_progress', 'on_hold']
        assert saved.params['resolution'] == 'wont_do'

    def test_the_sidebar_counts_use_the_state_filter(self, funded_user, zoo):
        from ..services.saved_filters import open_task_count

        assert open_task_count(funded_user, FilterParams(states=('on_hold',))) == 1
        assert open_task_count(funded_user, FilterParams(states=('todo', 'in_progress'))) == 2


def close(user, title, resolution) -> Task:
    task = create_task(user, title=title)
    return close_task(task, resolution)


# -- Everything that must stay as it was: the JWT API, the push button, the admin -----------------


class TestTheApi:
    """What the API (version 2, issue #275) says about states and resolutions; the endpoints
    themselves are in `test_api_states.py`."""

    def test_a_task_carries_its_state_and_resolution(self, client, auth, funded_user):
        create_task(funded_user, title='T', state='on_hold')

        listed = get(client, '/tasks', auth).json()['results'][0]

        assert set(listed) == TASK_KEYS
        assert listed['state'] == 'on_hold' and listed['resolution'] is None
        assert 'completed_at' not in listed

    def test_both_resolutions_are_closed_tasks(self, client, auth, funded_user):
        done = create_task(funded_user, title='Done')
        complete_task(done)
        wont = create_task(funded_user, title='Wont')
        close_task(wont, Resolution.WONT_DO)
        open_ = create_task(funded_user, title='Open')

        closed = get(client, '/tasks', auth, view='closed')
        everything = get(client, '/tasks', auth, view='all', include_closed='true')

        assert set(numbers(closed)) == {done.number, wont.number}
        assert all(row['closed_at'] for row in closed.json()['results'])
        assert numbers(everything)[:1] == [open_.number]  # the open ones first
        assert set(numbers(everything)) == {done.number, wont.number, open_.number}
        body = get(client, f'/tasks/{wont.number}', auth).json()
        assert body['closed_at'] is not None and body['resolution'] == 'wont_do'

    def test_the_old_names_are_gone(self, client, auth, funded_user):
        task = create_task(funded_user, title='T')

        assert get(client, '/tasks', auth, view='completed').status_code == 400
        assert post(client, f'/tasks/{task.number}/complete', auth).status_code in (404, 405)
        assert post(client, f'/tasks/{task.number}/uncomplete', auth).status_code in (404, 405)

    def test_close_means_done_and_closes_the_open_subtasks_as_done(self, client, auth, funded_user):
        parent = create_task(funded_user, title='P')
        sub = create_task(funded_user, title='S', parent=parent)

        body = post(client, f'/tasks/{parent.number}/close', auth, {}).json()

        assert body['closed_at'] is not None and body['resolution'] == 'done'
        assert reload(parent).resolution == 'done' and reload(sub).resolution == 'done'

    def test_reopen_reopens_a_wont_do_task_as_to_do(self, client, auth, funded_user):
        task = create_task(funded_user, title='T')
        close_task(task, Resolution.WONT_DO)

        body = post(client, f'/tasks/{task.number}/reopen', auth).json()

        assert body['closed_at'] is None and body['resolution'] is None
        task = reload(task)
        assert task.state == 'todo' and task.resolution == '' and task.is_open

    def test_the_today_view_keeps_listing_a_muted_task(self, client, auth, funded_user, wednesday):
        held = make_task(
            funded_user, title='Held', due_date=wednesday - timedelta(days=2), state='on_hold'
        )

        assert numbers(get(client, '/tasks', auth, view='today')) == [held.number]

    def test_a_saved_filter_made_on_the_site_shows_the_new_keys(self, client, auth, funded_user):
        saved = save_filter(
            funded_user,
            'Paused',
            FilterParams(q='x', states=('on_hold',), include_completed=True, resolution='wont_do'),
        )

        body = get(client, '/filters', auth).json()[0]

        assert body['id'] == str(saved.pk)
        assert body['params']['states'] == ['on_hold']
        assert body['params']['resolution'] == 'wont_do'
        assert body['params']['include_closed'] is True
        assert body['summary'] == 'state on hold · "x" · including won\'t do'

    def test_patching_params_replaces_them_as_the_site_does(self, client, auth, funded_user):
        saved = save_filter(
            funded_user,
            'Paused',
            FilterParams(q='x', states=('on_hold',), include_completed=True, resolution='wont_do'),
        )

        response = patch(
            client,
            f'/filters/{saved.pk}',
            auth,
            {'params': {'q': 'y', 'include_closed': True}},
        )

        assert response.status_code == 200
        assert response.json()['params']['q'] == 'y'
        stored = FilterParams.from_dict(SavedFilter.objects.get(pk=saved.pk).params)
        assert stored.q == 'y' and stored.include_completed
        assert stored.states == () and stored.resolution == ''  # not carried over

    def test_renaming_through_the_api_keeps_the_states_and_resolution(
        self, client, auth, funded_user
    ):
        saved = save_filter(
            funded_user,
            'Paused',
            FilterParams(states=('on_hold',), include_completed=True, resolution='wont_do'),
        )

        response = patch(client, f'/filters/{saved.pk}', auth, {'name': 'Renamed'})

        assert response.status_code == 200
        stored = FilterParams.from_dict(SavedFilter.objects.get(pk=saved.pk).params)
        assert stored.states == ('on_hold',) and stored.resolution == 'wont_do'

    def test_the_summary_says_closed(self, client, auth, funded_user):
        save_filter(funded_user, 'Everything', FilterParams(q='x', include_completed=True))

        body = get(client, '/filters', auth).json()[0]

        assert body['summary'] == '"x" · including closed'

    def test_running_a_saved_filter_honours_its_states(self, client, auth, funded_user):
        create_task(funded_user, title='Plain')
        held = create_task(funded_user, title='Held', state='on_hold')
        saved = save_filter(funded_user, 'Paused', FilterParams(states=('on_hold',)))

        body = get(client, f'/filters/{saved.pk}/tasks', auth)

        assert numbers(body) == [held.number]

    def test_the_api_can_save_states_and_a_resolution(self, client, auth, funded_user):
        response = post(
            client,
            '/filters',
            auth,
            {
                'name': 'X',
                'params': {
                    'states': ['on_hold', 'blocked'],
                    'include_closed': True,
                    'resolution': 'done',
                },
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body['params']['states'] == ['blocked', 'on_hold']  # stored sorted
        assert body['params']['resolution'] == 'done'
        stored = FilterParams.from_dict(SavedFilter.objects.get(name='X').params)
        assert stored.states == ('blocked', 'on_hold') and stored.resolution == 'done'

    @pytest.mark.parametrize(
        'params',
        [
            {'states': ['sleeping']},
            {'resolution': 'maybe', 'include_closed': True},
            {'resolution': 'done'},  # a resolution needs include_closed
            {'include_completed': True},  # the old name is gone
        ],
    )
    def test_a_bad_saved_filter_is_a_422(self, client, auth, params):
        response = post(client, '/filters', auth, {'name': 'X', 'params': params})

        assert response.status_code == 422


class TestOtherSurfaces:
    def test_the_push_complete_button_closes_the_open_subtasks_as_done(
        self, funded_user, wednesday
    ):
        from ..services import reminders as reminder_service

        task = due_task(funded_user)
        sub = create_task(funded_user, title='S', parent=task)
        push_id = uuid.uuid4()
        delivery = ReminderDelivery.objects.create(
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
        message = reminder_service.answer_push(answer)  # ty: ignore[invalid-argument-type]

        assert message == 'Completed, with 1 open subtask.' and delivery.pk
        assert reload(task).resolution == 'done' and reload(sub).resolution == 'done'

    def test_the_admin_lists_and_filters_by_state_and_resolution(self, user):
        make_task(user, number=1234, title='Findable', state='on_hold')
        client = Client()
        client.force_login(UserFactory(is_staff=True, is_superuser=True))
        url = reverse('admin:todo_task_changelist')

        for query in ({}, {'state': 'on_hold'}, {'resolution': 'wont_do'}, {'completed': 'yes'}):
            response = client.get(url, query)
            assert response.status_code == 200
        assert 'Findable' in client.get(url, {'state': 'on_hold'}).content.decode()
        assert 'Findable' not in client.get(url, {'state': 'in_progress'}).content.decode()

    def test_the_row_of_a_muted_task_is_dimmed_not_struck(self, web, funded_user):
        make_task(funded_user, title='Paused', state='on_hold')

        body = text(web.get(reverse('todo:all')))

        title = re.search(r'<a [^>]*class="task-title[^"]*"[^>]*>Paused</a>', body)
        assert title is not None
        assert 'text-gray-500' in title.group(0) and 'line-through' not in title.group(0)


def test_muted_states_is_what_the_pages_use():
    from ..models import Task as TaskModel

    assert TaskModel(state='on_hold').state in MUTED_STATES
