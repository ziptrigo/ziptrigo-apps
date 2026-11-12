"""The activity log of a task (issue #274): every change is recorded by the services, from every
entry point, with its channel and whether it was automatic; it reads right in the viewer's date and
time formats, loads only on demand, and goes with its task."""

import base64
import logging
from datetime import date, time, timedelta
from unittest import mock

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.datetime_format import DisplayFormats, override

from ..models import (
    EventChannel,
    EventKind,
    Resolution,
    Task,
    TaskEvent,
    TaskSource,
    TaskState,
)
from ..services import (
    add_blocker,
    add_comment,
    close_task,
    complete_task,
    create_task,
    delete_forever,
    events,
    postpone_task,
    purge_trash,
    recurrence,
    remove_blocker,
    reschedule_overdue,
    restore_task,
    set_resolution,
    set_state,
    toggle_checklist,
    trash_task,
    uncomplete_task,
    update_task,
)
from ..services import attachments as attachments_service
from ..services.event_text import describe
from ..views.activity import PAGE_SIZE
from .api_support import auth, bearer, patch, post  # noqa: F401
from .factories import make_attachment, make_label
from .test_attachments import upload
from .test_blockers import HTMX, text
from .test_inbound import Attachment, deliver, inbox, mail, the_task  # noqa: F401
from .test_push_reminders import configured, due_task, phone, sent_payload, tap, webpush  # noqa: F401
from .test_push_reminders import deliver as deliver_reminders

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def signed_in_wording():
    """What a signed-in user's request has active (outside one the public wording is)."""
    with override(DisplayFormats('mdy', '12h')):
        yield


def log(task: Task) -> list[TaskEvent]:
    """The task's entries, oldest first."""
    return list(TaskEvent.objects.filter(task=task).order_by('created_at', 'id'))


def kinds(task: Task) -> list[str]:
    return [event.kind for event in log(task)]


def last(task: Task, kind: str) -> TaskEvent:
    return [event for event in log(task) if event.kind == kind][-1]


def make(user, title='T', **fields) -> Task:
    return create_task(user, title=title, **fields)


def sentence(task: Task, kind: str) -> str:
    return describe(last(task, kind))


# -- Created -------------------------------------------------------------------------------------


class TestCreated:
    def test_a_new_task_has_a_created_entry(self, funded_user):
        task = make(funded_user, 'Pay rent')

        [entry] = log(task)
        assert entry.kind == EventKind.CREATED and entry.user == funded_user
        assert entry.automatic is False
        assert entry.data == {'source': 'web'}
        assert describe(entry) == 'Created'

    def test_a_task_made_through_the_api_source_says_so(self, funded_user):
        task = make(funded_user, source=TaskSource.API)

        assert describe(last(task, 'created')) == 'Created through the API'

    def test_a_subtask_names_its_parent_and_the_parent_logs_it(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)

        assert describe(last(sub, 'created')) == f'Created, as a subtask of #{parent.number} Parent'
        added = last(parent, 'subtask_added')
        assert describe(added) == f'Subtask added: #{sub.number} Sub'

    def test_a_repeating_task_names_its_rule(self, funded_user):
        task = make(funded_user, 'Rent', rrule='FREQ=WEEKLY;BYDAY=MO')

        assert 'Repeats: Every week on Monday' in describe(last(task, 'created'))

    def test_the_next_occurrence_is_automatic_and_names_the_previous(self, funded_user):
        task = make(funded_user, 'Rent', due_date=date(2026, 11, 1), rrule='FREQ=MONTHLY')

        complete_task(task)

        nxt = Task.objects.get(recurrence=task.recurrence, completed_at__isnull=True)
        entry = last(nxt, 'created')
        assert entry.automatic is True
        assert describe(entry).startswith(f'Created as the next occurrence of #{task.number}')

    def test_the_copies_of_subtasks_of_an_occurrence_do_not_flood_the_new_parent(self, funded_user):
        task = make(funded_user, 'Rent', due_date=date(2026, 11, 1), rrule='FREQ=MONTHLY')
        make(funded_user, 'Sub', parent=task)

        complete_task(task)

        nxt = Task.objects.get(recurrence=task.recurrence, completed_at__isnull=True, parent=None)
        assert 'subtask_added' not in kinds(nxt)
        copy = Task.objects.get(parent=nxt)
        assert last(copy, 'created').automatic is True

    def test_a_task_created_with_a_blocker_logs_it_and_the_automatic_block(self, funded_user):
        blocker = make(funded_user, 'Blocker')

        task = make(funded_user, 'Task', blocked_by=[blocker.number])

        assert kinds(task) == ['created', 'blocker_added', 'blocked']
        assert last(task, 'blocked').automatic is True
        assert describe(last(task, 'blocked')) == f'Blocked by #{blocker.number} Blocker'


# -- Field changes -------------------------------------------------------------------------------


class TestFieldChanges:
    def test_a_title_change(self, funded_user):
        task = make(funded_user, 'Old')

        update_task(task, changes={'title': 'New'})

        assert describe(last(task, 'title')) == 'Title changed from "Old" to "New"'

    def test_priority_with_and_without(self, funded_user):
        task = make(funded_user, priority=2)

        update_task(task, changes={'priority': 1})
        update_task(task, changes={'priority': 0})

        assert [describe(e) for e in log(task) if e.kind == 'priority'] == [
            'Priority changed from priority 2 to priority 1',
            'Priority changed from priority 1 to no priority',
        ]

    def test_the_due_date_and_time_are_one_entry(self, funded_user):
        task = make(funded_user, due_date=date(2026, 11, 5), due_time=time(15, 0))

        update_task(task, changes={'due_date': date(2026, 11, 7), 'due_time': time(9, 0)})

        assert kinds(task).count('due') == 1
        assert describe(last(task, 'due')) == (
            'Due date changed from 11/05/2026, 3:00 PM to 11/07/2026, 9:00 AM'
        )

    def test_setting_and_clearing_a_date(self, funded_user):
        task = make(funded_user)

        update_task(task, changes={'due_date': date(2026, 11, 5)})
        update_task(task, changes={'due_date': None})

        assert [describe(e) for e in log(task) if e.kind == 'due'] == [
            'Due date set to 11/05/2026',
            'Due date cleared (it was 11/05/2026)',
        ]

    def test_only_a_time_change(self, funded_user):
        task = make(funded_user, due_date=date(2026, 11, 5))

        update_task(task, changes={'due_time': time(8, 30)})

        assert describe(last(task, 'due')) == (
            'Due date changed from 11/05/2026 to 11/05/2026, 8:30 AM'
        )

    def test_labels_added_and_removed_by_name(self, funded_user):
        home, later, work = (make_label(funded_user, n) for n in ('home', 'later', 'work'))
        task = make(funded_user, labels=[later])

        update_task(task, changes={'labels': [home, work]})

        assert last(task, 'labels').data == {'added': ['home', 'work'], 'removed': ['later']}
        assert describe(last(task, 'labels')) == 'Labels: added @home, @work; removed @later'

    def test_the_entry_survives_the_label_being_deleted(self, funded_user):
        home = make_label(funded_user, 'home')
        task = make(funded_user)
        update_task(task, changes={'labels': [home]})

        home.delete()

        assert describe(last(task, 'labels')) == 'Labels: added @home'

    def test_a_description_edit_stores_no_text(self, funded_user):
        task = make(funded_user, description='secret words')

        update_task(task, changes={'description': 'other secret words'})

        entry = last(task, 'description')
        assert entry.data == {} and describe(entry) == 'Description edited'

    def test_a_ticked_checklist_item_is_its_own_entry(self, funded_user):
        task = make(funded_user, description='Plan\n- [ ] buy milk\n- [ ] call dad')

        toggle_checklist(task, 1, True)
        toggle_checklist(task, 1, False)

        assert [describe(e) for e in log(task) if e.kind == 'checklist'] == [
            'Checklist item ticked: call dad',
            'Checklist item unticked: call dad',
        ]
        assert 'description' not in kinds(task)

    @pytest.mark.parametrize(
        'changes',
        [
            {},
            {'title': 'T'},
            {'priority': 0},
            {'description': ''},
            {'labels': []},
            {'due_date': None},
            {'rrule': ''},
        ],
    )
    def test_no_change_records_nothing(self, funded_user, changes):
        task = make(funded_user, 'T')

        update_task(task, changes=changes)

        assert kinds(task) == ['created']

    def test_several_fields_write_one_entry_each(self, funded_user):
        task = make(funded_user, 'Old')

        update_task(task, changes={'title': 'New', 'priority': 1, 'description': 'x'})

        assert sorted(kinds(task)) == ['created', 'description', 'priority', 'title']

    def test_a_refused_update_records_nothing(self, funded_user):
        task = make(funded_user, 'T')

        with pytest.raises(Exception):
            update_task(task, changes={'title': ''})

        assert kinds(task) == ['created']

    def test_postponing_is_a_due_date_change(self, funded_user, wednesday):
        task = make(funded_user, due_date=date(2026, 10, 28))

        postpone_task(task, preset='tomorrow')
        postpone_task(task, preset='date', date=date(2026, 10, 29))  # already that day

        assert [describe(e) for e in log(task) if e.kind == 'due'] == [
            'Due date changed from 10/28/2026 to 10/29/2026'
        ]

    def test_rescheduling_the_overdue_ones_logs_each(self, funded_user, wednesday):
        a = make(funded_user, due_date=date(2026, 10, 20))
        b = make(funded_user, due_date=date(2026, 10, 21), due_time=time(9, 0))
        fine = make(funded_user, due_date=date(2026, 10, 30))

        assert reschedule_overdue(funded_user) == 2

        assert describe(last(a, 'due')) == 'Due date changed from 10/20/2026 to 10/28/2026'
        assert 'to 10/28/2026, 9:00 AM' in describe(last(b, 'due'))
        assert 'due' not in kinds(fine)

    def test_a_repeat_rule_set_changed_and_stopped(self, funded_user):
        task = make(funded_user, due_date=date(2026, 10, 28))

        update_task(task, changes={'rrule': 'FREQ=DAILY'})
        update_task(task, changes={'rrule': 'FREQ=WEEKLY', 'recurrence_mode': 'from_completion'})
        update_task(task, changes={'rrule': ''})

        assert [describe(e) for e in log(task) if e.kind == 'repeat'] == [
            'Repeat set: Every day',
            'Repeat changed from Every day to Every week (from completion)',
            'Repeat stopped (it was: Every week)',
        ]

    def test_stopping_through_the_service_logs_once(self, funded_user):
        task = make(funded_user, rrule='FREQ=DAILY')

        assert recurrence.stop(task) is True
        assert recurrence.stop(task) is False

        assert kinds(task).count('repeat') == 1

    def test_a_rule_that_runs_out_is_an_automatic_entry(self, funded_user):
        task = make(funded_user, due_date=date(2026, 11, 1), rrule='FREQ=DAILY;COUNT=1')

        complete_task(task)

        entry = last(task, 'repeat')
        assert entry.automatic is True
        assert describe(entry).startswith('Repeat ended: Every day')


# -- States, closing, blockers, moves ------------------------------------------------------------


class TestStatesAndClosing:
    def test_a_state_change(self, funded_user):
        task = make(funded_user)

        set_state(task, TaskState.IN_PROGRESS)
        set_state(task, TaskState.IN_PROGRESS)  # nothing changed

        assert kinds(task) == ['created', 'state']
        assert describe(last(task, 'state')) == 'State changed from To do to In progress'

    def test_closing_and_reopening(self, funded_user):
        task = make(funded_user)

        close_task(task, Resolution.WONT_DO)
        set_resolution(task, Resolution.DONE)
        set_resolution(task, Resolution.DONE)  # same: nothing
        uncomplete_task(task)

        assert [describe(e) for e in log(task)[1:]] == [
            "Closed as Won't do",
            "Resolution changed from Won't do to Done",
            'Reopened',
        ]

    def test_closing_an_already_closed_task_records_nothing(self, funded_user):
        task = make(funded_user)
        close_task(task)

        close_task(task)

        assert kinds(task).count('closed') == 1

    def test_closing_a_parent_logs_each_subtask_as_automatic(self, funded_user):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)

        close_task(parent, Resolution.DONE, subtasks='wont_do')

        assert describe(last(parent, 'closed')) == (
            "Closed as Done, with 1 open subtask closed as Won't do"
        )
        entry = last(sub, 'closed')
        assert entry.automatic is True
        assert describe(entry) == f"Closed as Won't do with its parent, #{parent.number} P"
        assert 'subtask_closed' not in kinds(parent)

    def test_a_subtask_closed_on_its_own_is_on_the_parents_log_too(self, funded_user):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)

        complete_task(sub)

        assert last(sub, 'closed').automatic is False
        assert describe(last(parent, 'subtask_closed')) == (
            f'Subtask #{sub.number} S closed as Done'
        )

    def test_reopening_a_task_with_an_open_blocker_says_it_is_blocked(self, funded_user):
        blocker, task = make(funded_user, 'B'), make(funded_user, 'T')
        add_blocker(task, blocker)
        complete_task(task)

        uncomplete_task(task)

        assert describe(last(task, 'reopened')) == 'Reopened (still blocked: a blocker is open)'


class TestBlockers:
    def test_the_blocked_entry_names_the_blockers_the_database_says_are_open(self, funded_user):
        from ..services import blockers as blockers_service

        blocker = make(funded_user, 'B')
        stale = Task.objects.get(pk=blocker.pk)
        stale.completed_at = timezone.now()  # what a stale copy believes: closed
        task = make(funded_user, 'T')

        blockers_service.link_new_task(task, [stale])

        entry = last(task, 'blocked')
        assert entry.data['blockers'] == [{'number': blocker.number, 'title': 'B'}]
        assert describe(entry) == f'Blocked by #{blocker.number} B'

    def test_a_blocked_entry_with_no_names_still_reads(self):
        entry = TaskEvent(kind=EventKind.BLOCKED, data={'blockers': []})

        assert describe(entry) == 'Blocked'

    def test_adding_a_blocker_logs_it_and_the_automatic_block(self, funded_user):
        blocker, task = make(funded_user, 'B'), make(funded_user, 'T')

        add_blocker(task, blocker)
        add_blocker(task, blocker)  # already linked: nothing more

        assert kinds(task) == ['created', 'blocker_added', 'blocked']
        assert last(task, 'blocker_added').automatic is False
        assert last(task, 'blocked').automatic is True
        assert describe(last(task, 'blocker_added')) == f'Blocker added: #{blocker.number} B'

    def test_a_closed_blocker_only_links(self, funded_user):
        blocker, task = make(funded_user, 'B'), make(funded_user, 'T')
        complete_task(blocker)

        add_blocker(task, blocker)

        assert kinds(task) == ['created', 'blocker_added']

    def test_closing_the_blocker_unblocks_automatically_and_names_it(self, funded_user):
        blocker, task = make(funded_user, 'B'), make(funded_user, 'T')
        add_blocker(task, blocker)

        complete_task(blocker)

        entry = last(task, 'unblocked')
        assert entry.automatic is True
        assert describe(entry) == f'Unblocked: #{blocker.number} B was closed'

    def test_a_task_with_two_blockers_is_unblocked_by_the_last_one(self, funded_user):
        one, two, task = make(funded_user, '1'), make(funded_user, '2'), make(funded_user, 'T')
        add_blocker(task, one)
        add_blocker(task, two)

        complete_task(one)
        assert 'unblocked' not in kinds(task)
        complete_task(two)

        assert describe(last(task, 'unblocked')) == f'Unblocked: #{two.number} 2 was closed'

    def test_trashing_and_deleting_the_blocker_say_so(self, funded_user):
        b1, b2 = make(funded_user, 'B1'), make(funded_user, 'B2')
        t1, t2 = make(funded_user, 'T1'), make(funded_user, 'T2')
        add_blocker(t1, b1)
        add_blocker(t2, b2)

        trash_task(b1)
        delete_forever(b2)

        assert describe(last(t1, 'unblocked')) == (
            f'Unblocked: #{b1.number} B1 was moved to the Trash'
        )
        assert describe(last(t2, 'unblocked')) == f'Unblocked: #{b2.number} B2 was deleted'

    def test_removing_the_last_blocker_by_hand(self, funded_user):
        blocker, task = make(funded_user, 'B'), make(funded_user, 'T')
        add_blocker(task, blocker)

        assert remove_blocker(task, blocker) is True
        assert remove_blocker(task, blocker) is False

        assert kinds(task) == [
            'created',
            'blocker_added',
            'blocked',
            'blocker_removed',
            'unblocked',
        ]
        assert last(task, 'blocker_removed').automatic is False
        assert describe(last(task, 'unblocked')).endswith('was removed as a blocker')

    def test_a_task_put_on_hold_by_hand_keeps_it_and_logs_no_unblock(self, funded_user):
        blocker, task = make(funded_user, 'B'), make(funded_user, 'T')
        add_blocker(task, blocker)
        set_state(task, TaskState.ON_HOLD)

        complete_task(blocker)

        assert 'unblocked' not in kinds(task)


class TestMoves:
    def test_moving_a_task_under_a_parent_and_out_again(self, funded_user):
        parent, other, task = make(funded_user, 'P'), make(funded_user, 'O'), make(funded_user, 'T')

        update_task(task, changes={'parent': parent})
        update_task(task, changes={'parent': other})
        update_task(task, changes={'parent': None})

        assert [describe(e) for e in log(task) if e.kind == 'moved'] == [
            f'Moved under #{parent.number} P',
            f'Moved under #{other.number} O',
            'Made a top-level task',
        ]
        assert [describe(e) for e in log(parent)[1:]] == [
            f'Subtask moved in: #{task.number} T',
            f'Subtask moved out: #{task.number} T',
        ]
        assert [describe(e) for e in log(other)[1:]] == [
            f'Subtask moved in: #{task.number} T',
            f'Subtask moved out: #{task.number} T',
        ]

    def test_staying_under_the_same_parent_records_nothing(self, funded_user):
        parent = make(funded_user, 'P')
        task = make(funded_user, 'T', parent=parent)

        update_task(task, changes={'parent': parent})

        assert 'moved' not in kinds(task)


class TestTrash:
    def test_trash_and_restore_cascade_to_subtasks_as_automatic(self, funded_user):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)

        trash_task(parent)
        restore_task(Task.objects.get(pk=parent.pk))

        own = log(parent)[2:]  # after `created` and the new subtask
        assert [describe(e) for e in own] == ['Moved to the Trash', 'Restored']
        assert [e.automatic for e in own] == [False, False]
        trashed, restored = log(sub)[1:]
        assert trashed.automatic and restored.automatic
        assert describe(trashed) == f'Moved to the Trash with its parent, #{parent.number} P'
        assert describe(restored) == f'Restored with its parent, #{parent.number} P'

    def test_a_subtask_trashed_on_its_own_is_not_logged_again_by_the_parent(self, funded_user):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)
        trash_task(sub)

        trash_task(parent)

        assert kinds(sub).count('trashed') == 1

    def test_the_log_is_readable_for_a_task_in_the_trash(self, web, funded_user):
        task = make(funded_user, 'T')
        trash_task(task)

        html = text(web.get(reverse('todo:task-activity', args=[task.number])))

        assert 'Moved to the Trash' in html

    def test_deleting_forever_removes_the_entries(self, funded_user):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)
        trash_task(parent)
        assert TaskEvent.objects.filter(task__in=[parent, sub]).exists()

        delete_forever(Task.objects.get(pk=parent.pk))

        assert not TaskEvent.objects.filter(task_id__in=[parent.pk, sub.pk]).exists()

    def test_the_purge_removes_the_entries(self, funded_user):
        task = make(funded_user, 'T')
        trash_task(task)
        Task.objects.filter(pk=task.pk).update(deleted_at=timezone.now() - timedelta(days=400))
        mine = make(funded_user, 'Mine')

        assert purge_trash() == 1

        assert not TaskEvent.objects.filter(task_id=task.pk).exists()
        assert TaskEvent.objects.filter(task=mine).exists()

    def test_deleting_the_user_removes_the_entries(self, funded_user, other_user):
        make(funded_user)
        theirs = make(other_user)

        funded_user.delete()

        assert TaskEvent.objects.filter(user=other_user).count() == 1
        assert TaskEvent.objects.filter(task=theirs).exists()
        assert TaskEvent.objects.count() == 1


# -- Attachments ---------------------------------------------------------------------------------


class TestAttachments:
    def test_a_completed_upload_and_its_removal(self, funded_user, fake_storage):
        task = make(funded_user)

        attachment = upload(task, fake_storage, filename='plan.png')
        attachments_service.delete_attachment(attachment)

        assert [describe(e) for e in log(task)[1:]] == [
            'File attached: plan.png',
            'File removed: plan.png',
        ]

    def test_an_abandoned_upload_is_not_logged(self, funded_user):
        task = make(funded_user)

        attachments_service.start_upload(
            task,
            filename='x.png',
            size=3,
            declared_type='image/png',
            checksum_sha256='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
        )

        assert kinds(task) == ['created']

    def test_a_comment_file_is_logged_when_it_is_attached_not_when_uploaded(
        self, funded_user, fake_storage
    ):
        task = make(funded_user)
        draft = make_attachment(task, fake_storage, for_comment=True, filename='note.png')
        assert kinds(task) == ['created']

        add_comment(task, funded_user, 'See file', [str(draft.pk)])

        assert describe(last(task, 'attachment_added')) == 'File attached to a comment: note.png'

    def test_discarding_an_unsaved_comment_file_logs_nothing(self, funded_user, fake_storage):
        task = make(funded_user)
        draft = make_attachment(task, fake_storage, for_comment=True)

        attachments_service.discard_draft(draft)

        assert kinds(task) == ['created']

    def test_a_removed_comment_file_says_so(self, funded_user, fake_storage):
        task = make(funded_user)
        draft = make_attachment(task, fake_storage, for_comment=True, filename='note.png')
        add_comment(task, funded_user, 'See file', [str(draft.pk)])

        attachments_service.delete_attachment(draft)

        assert describe(last(task, 'attachment_removed')) == 'File removed from a comment: note.png'


# -- Channels ------------------------------------------------------------------------------------


class TestChannels:
    def test_the_default_is_system(self, funded_user):
        task = make(funded_user)

        assert last(task, 'created').channel == EventChannel.SYSTEM

    def test_the_context_manager_sets_and_restores_it(self, funded_user):
        with events.channel(EventChannel.EMAIL):
            inside = make(funded_user)
            with events.channel(EventChannel.PUSH):
                innermost = make(funded_user)
            back = make(funded_user)
        after = make(funded_user)

        assert [last(t, 'created').channel for t in (inside, innermost, back, after)] == [
            'email',
            'push',
            'email',
            'system',
        ]

    def test_a_web_view_records_web(self, web, funded_user):
        task = make(funded_user, 'T')

        web.post(reverse('todo:set-state', args=[task.number]), {'state': 'on_hold'}, **HTMX)
        web.post(reverse('todo:create'), {'title': 'Made on the web'}, **HTMX)

        assert last(task, 'state').channel == EventChannel.WEB
        made = Task.objects.get(title='Made on the web')
        assert last(made, 'created').channel == EventChannel.WEB

    def test_the_circle_is_a_user_action_with_the_web_channel(self, web, funded_user):
        task = make(funded_user, 'T')

        web.post(reverse('todo:complete', args=[task.number]), {}, **HTMX)

        entry = last(task, 'closed')
        assert entry.channel == EventChannel.WEB and entry.automatic is False

    def test_the_api_records_api(self, funded_user, auth):
        client = Client()
        response = post(client, '/tasks', auth, {'title': 'Via API'})
        assert response.status_code == 201
        task = Task.objects.get(title='Via API')
        patch(client, f'/tasks/{task.number}', auth, {'priority': 1})
        post(client, f'/tasks/{task.number}/close', auth, {})

        assert [(e.kind, e.channel) for e in log(task)] == [
            ('created', 'api'),
            ('priority', 'api'),
            ('closed', 'api'),
        ]

    def test_an_empty_patch_over_the_api_records_nothing(self, funded_user, auth):
        task = make(funded_user)

        patch(Client(), f'/tasks/{task.number}', auth, {})

        assert kinds(task) == ['created']

    def test_the_phones_complete_button_is_the_users_own_action(
        self, funded_user, wednesday, configured, phone, webpush, client,
        django_capture_on_commit_callbacks,
    ):  # fmt: skip
        task = due_task(funded_user)
        deliver_reminders(django_capture_on_commit_callbacks)

        tap(client, sent_payload(webpush), 'complete')

        entry = last(task, 'closed')
        assert entry.channel == EventChannel.PUSH and entry.automatic is False

    def test_the_email_intake_is_automatic_and_logs_the_files(self, deliver, inbox):
        message = deliver(
            mail(
                inbox,
                subject='Buy milk',
                attachments=[Attachment('notes.txt', b'hi', 'text', 'plain')],
            )
        )

        task = the_task(message)

        entries = log(task)
        assert [(e.kind, e.automatic, e.channel) for e in entries] == [
            ('created', True, 'email'),
            ('attachment_added', True, 'email'),
        ]
        assert describe(entries[0]) == 'Created from an email'
        assert describe(entries[1]) == 'File attached: notes.txt'

    def test_work_outside_a_request_records_system(self, funded_user):
        blocker, blocked = make(funded_user, 'B'), make(funded_user, 'Blocked')
        add_blocker(blocked, blocker)

        complete_task(blocker)  # the shell, a job: no request, no channel set

        assert {e.channel for e in log(blocked)} == {EventChannel.SYSTEM}


# -- Recording never fails the change, and takes no locks ----------------------------------------


class TestRobustness:
    def test_a_failing_insert_does_not_fail_the_action(self, funded_user, caplog):
        task = make(funded_user, 'Old')

        with (
            mock.patch.object(TaskEvent.objects, 'bulk_create', side_effect=RuntimeError('boom')),
            caplog.at_level(logging.ERROR),
        ):
            update_task(task, changes={'title': 'New'})
            complete_task(task)

        assert Task.objects.get(pk=task.pk).title == 'New'
        assert Task.objects.get(pk=task.pk).completed_at is not None
        assert 'Could not record' in caplog.text
        assert kinds(task) == ['created']

    def test_a_failing_insert_does_not_poison_the_transaction(self, funded_user):
        task = make(funded_user)

        with mock.patch(
            'apps.todo.services.events.TaskEvent', side_effect=TypeError('bad'), create=True
        ):
            update_task(task, changes={'priority': 1})
        update_task(task, changes={'priority': 2})  # the connection is still usable

        assert Task.objects.get(pk=task.pk).priority == 2
        assert last(task, 'priority').data['to'] == 2

    def test_recording_takes_no_lock(self, funded_user, monkeypatch):
        locked: list[str] = []
        real = QuerySet.select_for_update

        def spy(self, *args, **kwargs):
            locked.append(self.model.__name__)
            return real(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, 'select_for_update', spy)
        parent, blocker, task = make(funded_user), make(funded_user), make(funded_user)

        add_blocker(task, blocker)
        update_task(task, changes={'title': 'x', 'parent': parent})
        complete_task(blocker)
        trash_task(task)
        restore_task(Task.objects.get(pk=task.pk))

        assert 'TaskEvent' not in locked
        assert TaskEvent.objects.count() > 5

    def test_the_foreign_keys_have_no_database_constraint(self):
        """On PostgreSQL an insert into a table with a foreign key takes `FOR KEY SHARE` on the
        referenced row, which conflicts with the `FOR UPDATE` the services hold (a deadlock the
        spy above can't see). The ORM still cascades (the tests of the Trash and the user)."""
        task_fk = TaskEvent._meta.get_field('task')  # ty: ignore[missing-attribute]
        user_fk = TaskEvent._meta.get_field('user')  # ty: ignore[missing-attribute]

        assert task_fk.db_constraint is False
        assert user_fk.db_constraint is False
        assert task_fk.db_index is False  # `(task, created_at)` covers it

    def test_an_entry_is_one_insert_in_the_changes_transaction(self, funded_user):
        task = make(funded_user, 'Old')

        with CaptureQueriesContext(connection) as queries:
            update_task(task, changes={'title': 'New', 'priority': 1})

        inserts = [
            q for q in queries.captured_queries if 'INSERT INTO "todo_taskevent"' in q['sql']
        ]
        assert len(inserts) == 1  # two entries, one bulk insert


# -- Rendering -----------------------------------------------------------------------------------


class TestRendering:
    def test_dates_follow_the_viewers_formats(self, funded_user):
        task = make(funded_user, due_date=date(2026, 11, 5), due_time=time(21, 5))
        update_task(task, changes={'due_date': date(2026, 11, 7), 'due_time': time(9, 0)})
        entry = last(task, 'due')

        with override(DisplayFormats('mdy', '12h')):
            assert describe(entry) == (
                'Due date changed from 11/05/2026, 9:05 PM to 11/07/2026, 9:00 AM'
            )
        with override(DisplayFormats('dmy', '24h')):
            assert describe(entry) == (
                'Due date changed from 05/11/2026, 21:05 to 07/11/2026, 09:00'
            )
        with override(DisplayFormats('public', '12h')):
            assert 'Nov 5, 2026, 9:05 PM' in describe(entry)

    def test_the_repeat_rule_is_written_when_shown_not_when_stored(self, funded_user):
        task = make(
            funded_user, due_date=date(2026, 3, 3), rrule='FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=3'
        )

        with override(DisplayFormats('dmy', '24h')):
            assert '3 March' in describe(last(task, 'created'))
        with override(DisplayFormats('mdy', '12h')):
            assert 'March 3' in describe(last(task, 'created'))

    def test_every_kind_has_a_sentence(self):
        for kind in EventKind.values:
            event = TaskEvent(kind=kind, data={})
            assert describe(event)  # an entry with no data still reads, never raises

    def test_a_hostile_title_is_escaped_in_the_page(self, web, funded_user):
        task = make(funded_user, '<script>alert(1)</script>')
        update_task(task, changes={'title': '<img src=x onerror=alert(2)>'})

        html = web.get(reverse('todo:task-activity', args=[task.number])).content.decode()

        assert '<script>alert(1)' not in html and '<img src=x' not in html
        assert '&lt;script&gt;' in html


# -- The page ------------------------------------------------------------------------------------


class TestChecklistItemText:
    def entry_text(self, funded_user, description, index):
        task = make(funded_user, description=description)
        toggle_checklist(task, index, True)
        return last(task, 'checklist').data['text']

    def test_a_block_quoted_item_is_named_without_the_quote_marker(self, funded_user):
        assert self.entry_text(funded_user, '> - [ ] buy *milk*\n> - [ ] call dad', 0) == 'buy milk'

    def test_a_nested_item(self, funded_user):
        description = '- [ ] outer\n  - [ ] inner `code`\n- [ ] last'

        assert self.entry_text(funded_user, description, 1) == 'inner code'

    def test_lone_carriage_return_line_endings(self, funded_user):
        description = 'Plan\r- [ ] one\r- [ ] two\r- [ ] three'

        assert self.entry_text(funded_user, description, 2) == 'three'

    def test_a_long_item_is_cut(self, funded_user):
        text = self.entry_text(funded_user, '- [ ] ' + 'word ' * 40, 0)

        assert len(text) == 80 and text.endswith('…')


class TestHistoryRefresh:
    """The task page's open history refreshes on `HX-Trigger: taskChanged`."""

    def trigger(self, response):
        return response.headers.get('HX-Trigger')

    def test_task_changing_views_send_it(self, web, funded_user):
        task = make(funded_user, 'T', description='- [ ] a')
        blocker = make(funded_user, 'B')
        n = task.number

        def go(name, args, data, **extra):
            return web.post(reverse(f'todo:{name}', args=args), data, **HTMX, **extra)

        responses = {
            'edit': go('edit', [n], {'part': 'title', 'title': 'New'}, HTTP_HX_CURRENT_URL=''),
            'checklist': go('checklist', [n], {'index': 0, 'checked': 'true'}),
            'subtask': go('subtask-add', [n], {'title': 'Sub'}),
            'state': go('set-state', [n], {'state': 'on_hold'}, HTTP_HX_CURRENT_URL=''),
            'blocker': go('blocker-add', [n], {'blocker': str(blocker.number)}),
            'comment': go('comment-add', [n], {'body': 'hello'}),
        }

        other = make(funded_user, 'Other')
        responses['complete'] = go('complete', [other.number], {})

        for name, response in responses.items():
            assert response.status_code == 200, name
            assert self.trigger(response) == 'taskChanged', name

    def test_a_refusal_and_a_plain_request_send_nothing(self, web, funded_user):
        task = make(funded_user, 'T')
        url = reverse('todo:edit', args=[task.number])

        refused = web.post(url, {'part': 'title', 'title': ''}, **HTMX)
        plain = web.post(url, {'part': 'title', 'title': 'New'})

        assert refused.status_code == 422 and self.trigger(refused) is None
        assert plain.status_code == 302 and self.trigger(plain) is None

    def test_the_attachment_list_refresh_sends_it(self, web, funded_user):
        task = make(funded_user, 'T')

        response = web.get(reverse('todo:attachment-list', args=[task.number]), **HTMX)

        assert self.trigger(response) == 'taskChanged'


class TestQueries:
    def close_sub(self, funded_user, cached):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)
        fresh = Task.objects.select_related('parent').get(pk=sub.pk)
        if not cached:
            fresh = Task.objects.get(pk=sub.pk)
        with CaptureQueriesContext(connection) as queries:
            complete_task(fresh)
        assert kinds(parent)[-1] == 'subtask_closed'
        return len(queries)

    def test_a_loaded_parent_is_not_fetched_again(self, funded_user):
        cold = self.close_sub(funded_user, cached=False)
        warm = self.close_sub(funded_user, cached=True)

        assert warm == cold - 1

    def test_closing_the_subtasks_reads_them_once(self, funded_user):
        parent = make(funded_user, 'P')
        for n in range(3):
            make(funded_user, f'S{n}', parent=parent)

        with CaptureQueriesContext(connection) as queries:
            complete_task(Task.objects.get(pk=parent.pk))

        by_parent = [
            q
            for q in queries.captured_queries
            if q['sql'].startswith('SELECT')
            and 'WHERE ("todo_task"."completed_at" IS NULL' in q['sql']
        ]
        assert len(by_parent) == 1  # the ids; the locked rows are the ones recorded
        assert TaskEvent.objects.filter(kind='closed').count() == 4


class TestEndpoint:
    def url(self, task, **query):
        base = reverse('todo:task-activity', args=[task.number])
        return f'{base}?{"&".join(f"{k}={v}" for k, v in query.items())}' if query else base

    def test_the_first_page_has_the_heading_and_newest_first(self, web, funded_user):
        task = make(funded_user, 'Old')
        update_task(task, changes={'title': 'New'})

        html = text(web.get(self.url(task)))

        assert 'History' in html and 'id="task-activity-list"' in html
        assert html.index('Title changed') < html.index('Created')
        assert 'Show more' not in html

    def test_an_automatic_entry_is_marked(self, web, funded_user):
        blocker, task = make(funded_user, 'B'), make(funded_user, 'T')
        add_blocker(task, blocker)

        html = text(web.get(self.url(task)))

        assert html.count('Automatic') == 1

    def test_the_task_page_does_not_render_or_query_the_log(self, web, funded_user):
        task = make(funded_user, 'T')
        update_task(task, changes={'title': 'Distinctive new title'})
        trash_task(task)
        restore_task(Task.objects.get(pk=task.pk))

        with CaptureQueriesContext(connection) as queries:
            response = web.get(reverse('todo:task', args=[task.number]))

        html = response.content.decode()
        assert 'id="task-activity-toggle"' in html and 'aria-expanded="false"' in html
        assert 'aria-controls="task-activity-body"' in html
        assert 'Title changed from' not in html and 'Moved to the Trash' not in html
        assert not any('todo_taskevent' in q['sql'] for q in queries.captured_queries)

    def test_paging_is_20_at_a_time_with_a_cursor(self, web, funded_user):
        task = make(funded_user, 'T')
        for n in range(30):  # 31 entries with the created one
            update_task(task, changes={'title': f'Title {n}'})
        total = TaskEvent.objects.filter(task=task).count()
        assert total > PAGE_SIZE

        first = text(web.get(self.url(task)))
        assert first.count('<li') == PAGE_SIZE and 'Show more' in first
        cursor = first.split('?before=')[1].split('"')[0]
        second = text(web.get(self.url(task, before=cursor)))

        assert second.count('<li') == total - PAGE_SIZE
        assert 'data-activity-new' in second and 'hx-swap-oob' in second
        assert 'Created' in second and 'Show more' not in second

    def test_entries_written_meanwhile_do_not_repeat_or_skip_one(self, web, funded_user):
        task = make(funded_user, 'T')
        for n in range(25):
            update_task(task, changes={'title': f'Title {n}'})
        first = text(web.get(self.url(task)))
        cursor = first.split('?before=')[1].split('"')[0]

        update_task(task, changes={'title': 'Brand new'})  # lands on top of the log
        second = text(web.get(self.url(task, before=cursor)))

        assert 'Brand new' not in second
        assert second.count('Title changed') + first.count('Title changed') == 25
        assert 'Title changed from "Title 0" to "Title 1"' in second

    @pytest.mark.parametrize(
        'cursor',
        [
            'x',
            '2026-01-01T00:00:00_notauuid',
            '_',
            '2026-11-11T10:00:00+00:00_00000000-0000-0000-0000-000000000000',  # the old format
            'MjAyNi0xMS0xMVQxMDowMDowMCswMDowMF9ub3QtYS11dWlk',  # base64url, not a cursor
        ],
    )
    def test_a_bad_cursor_is_a_404(self, web, funded_user, cursor):
        task = make(funded_user)

        assert web.get(self.url(task, before=cursor)).status_code == 404

    def test_a_cursor_without_a_time_zone_is_a_404(self, web, funded_user):
        task = make(funded_user)
        raw = '2026-11-11T10:00:00_00000000-0000-0000-0000-000000000000'
        cursor = base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')

        assert web.get(self.url(task, before=cursor)).status_code == 404

    def test_somebody_elses_task_is_a_404(self, web, other_user):
        theirs = make(other_user)

        assert web.get(self.url(theirs)).status_code == 404

    def test_it_needs_a_login(self, funded_user):
        task = make(funded_user)

        response = Client().get(self.url(task))

        assert response.status_code == 302 and 'login' in response['Location']

    def test_it_only_reads(self, web, funded_user):
        task = make(funded_user)

        assert web.post(self.url(task)).status_code == 405

    def test_a_page_costs_a_fixed_number_of_queries(self, web, funded_user):
        task = make(funded_user, 'T')
        for n in range(PAGE_SIZE):
            update_task(task, changes={'title': f'T{n}'})
        web.get(self.url(task))  # warm anything lazy

        with CaptureQueriesContext(connection) as small:
            web.get(
                self.url(
                    task, before='2000-01-01T00:00:00+00:00_00000000-0000-0000-0000-000000000000'
                )
            )
        with CaptureQueriesContext(connection) as full:
            web.get(self.url(task))

        events_queries = [q for q in full.captured_queries if 'todo_taskevent' in q['sql']]
        assert len(events_queries) == 1  # one query for 20 rows: nothing per entry
        assert len(full.captured_queries) <= len(small.captured_queries) + 1

    def test_a_signed_in_users_formats_apply(self, web, funded_user):
        funded_user.date_format = 'dmy'
        funded_user.time_format = '24h'
        funded_user.save()
        task = make(funded_user, due_date=date(2026, 11, 5), due_time=time(21, 5))
        update_task(task, changes={'due_date': date(2026, 11, 7), 'due_time': time(9, 0)})

        html = text(web.get(self.url(task)))

        assert '05/11/2026, 21:05' in html and '07/11/2026, 09:00' in html


class TestAdmin:
    def test_the_changelist_and_filters_render(self, funded_user, client):
        from apps.accounts.models import User

        staff = User.objects.create_superuser(email='root@example.com', password='x-Y9z!long-pw')
        staff.email_confirmed = True
        staff.save()
        client.force_login(staff)
        task = make(funded_user, 'T')
        complete_task(task)

        listing = client.get(reverse('admin:todo_taskevent_changelist'))
        filtered = client.get(
            reverse('admin:todo_taskevent_changelist') + '?automatic__exact=0&kind=closed'
        )

        assert listing.status_code == 200 and 'Closed as Done' in listing.content.decode()
        assert filtered.status_code == 200
        assert client.get(reverse('admin:todo_taskevent_add')).status_code == 403
