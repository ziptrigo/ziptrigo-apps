"""`services.tasks` and `services.dates`: validation, completing, postponing, the Trash, manual order
and the sort orders."""

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError

from apps.core.markdown import search_text

from ..models import Task, TodoSettings
from ..services import (
    DESCRIPTION_MAX,
    TITLE_MAX,
    complete_task,
    create_task,
    delete_forever,
    due_at,
    get_account,
    has_manual_order,
    order_tasks,
    postpone_target,
    postpone_task,
    purge_trash,
    reorder,
    reschedule_overdue,
    restore_task,
    subtasks_of,
    today_set,
    todays_tasks,
    toggle_checklist,
    trash_task,
    uncomplete_task,
    update_task,
)
from .factories import make_label, make_task, make_user, utc

pytestmark = pytest.mark.django_db


@pytest.fixture
def config(config):
    """Billing is tested elsewhere: here every task is free."""
    config.free_tasks_per_month = 1000
    config.save()
    return config


def errors(callable_, *args, **kwargs) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as caught:
        callable_(*args, **kwargs)
    return caught.value.message_dict


class TestCreateValidation:
    def test_a_task_with_only_a_title(self, user, config):
        task = create_task(user, title='  Pay rent  ')

        assert task.title == 'Pay rent'
        assert task.title_text == 'pay rent'
        assert (task.priority, task.due_date, task.due_time, task.source) == (0, None, None, 'web')
        assert task.is_open

    def test_the_title_is_required(self, user, config):
        assert errors(create_task, user, title='   ') == {'title': ['Title is required.']}

    def test_the_title_is_capped(self, user, config):
        assert 'title' in errors(create_task, user, title='x' * (TITLE_MAX + 1))
        assert create_task(user, title='x' * TITLE_MAX)

    def test_the_description_is_capped_and_normalised(self, user, config):
        assert 'description' in errors(
            create_task, user, title='T', description='x' * (DESCRIPTION_MAX + 1)
        )
        task = create_task(user, title='T', description='**Bold** text\r\nnext #1234')

        assert task.description == '**Bold** text\nnext #1234'
        assert task.description_text == search_text(task.description)
        assert 'bold' in task.description_text

    @pytest.mark.parametrize('priority', [-1, 4, 'high', None.__class__])
    def test_the_priority_is_0_to_3(self, user, config, priority):
        assert 'priority' in errors(create_task, user, title='T', priority=priority)

    def test_a_time_needs_a_date(self, user, config):
        assert errors(create_task, user, title='T', due_time=time(9, 0)) == {
            'due_time': ['A time needs a date.']
        }
        task = create_task(user, title='T', due_date=date(2026, 11, 3), due_time=time(9, 0))
        assert (task.due_date, task.due_time) == (date(2026, 11, 3), time(9, 0))

    def test_a_subtask_goes_under_an_open_top_level_task_of_the_user(
        self, user, other_user, config
    ):
        parent = create_task(user, title='Parent')
        sub = create_task(user, title='Sub', parent=parent)
        assert sub.parent == parent

        assert errors(create_task, user, title='Deep', parent=sub) == {
            'parent': ['A subtask cannot have subtasks.']
        }
        theirs = create_task(other_user, title='Theirs')
        assert 'parent' in errors(create_task, user, title='T', parent=theirs)
        trash_task(parent)
        assert 'parent' in errors(create_task, user, title='T', parent=parent)
        done = create_task(user, title='Done')
        complete_task(done)
        assert 'parent' in errors(create_task, user, title='T', parent=done)

    def test_the_model_refuses_a_deep_subtask_too(self, user, config):
        parent = make_task(user)
        sub = make_task(user, parent=parent)
        deep = Task(user=user, number=9000, title='Deep', parent=sub)

        with pytest.raises(ValidationError) as caught:
            deep.full_clean(exclude=['user'])

        assert 'A subtask cannot have subtasks.' in str(caught.value)

    def test_labels_must_be_the_users_own(self, user, other_user, config):
        mine = make_label(user, 'Mine')
        theirs = make_label(other_user, 'Theirs')

        task = create_task(user, title='T', labels=[mine])
        assert list(task.labels.all()) == [mine]
        assert errors(create_task, user, title='T', labels=[theirs]) == {
            'labels': ['Unknown label.']
        }
        assert errors(create_task, user, title='T', labels=['not-a-uuid']) == {
            'labels': ['Unknown label.']
        }
        assert create_task(user, title='By id', labels=[str(mine.pk)]).labels.count() == 1

    def test_over_the_open_task_cap_is_refused(self, user, config):
        config.max_open_tasks_per_user = 2
        config.save()
        create_task(user, title='One')
        second = create_task(user, title='Two')

        assert 'Too many open tasks' in errors(create_task, user, title='Three')['__all__'][0]
        complete_task(second)  # a completed task no longer counts
        assert create_task(user, title='Three')

    def test_a_refusal_writes_nothing(self, user, config):
        errors(create_task, user, title='')

        assert not Task.objects.exists()
        assert get_account(user).next_task_number == 1000

    def test_no_manual_order_means_position_zero_everywhere(self, user, config, wednesday):
        first = create_task(user, title='A', due_date=wednesday)
        second = create_task(user, title='B', due_date=wednesday)

        assert (first.position, first.today_position) == (0, 0)  # the default sort applies
        assert (second.position, second.today_position) == (0, 0)

    def test_a_task_joins_the_end_of_todays_manual_order_only_when_it_is_due_today(
        self, user, config, wednesday
    ):
        a = create_task(user, title='A', due_date=wednesday)
        b = create_task(user, title='B', due_date=wednesday)
        reorder(user, scope='today', ids=[b.pk, a.pk])

        today = create_task(user, title='Today', due_date=wednesday)
        tomorrow = create_task(user, title='Tomorrow', due_date=date(2026, 10, 29))
        undated = create_task(user, title='Undated')

        assert today.today_position == 2  # after the others in the list that was reordered
        assert (tomorrow.today_position, undated.today_position) == (0, 0)  # other lists
        assert tomorrow.position == undated.position == today.position == 0

    def test_a_task_created_after_a_drag_does_not_change_a_later_days_today(
        self, user, config, wednesday, freeze_now
    ):
        thursday = date(2026, 10, 29)
        a = create_task(user, title='A', due_date=wednesday)
        b = create_task(user, title='B', due_date=wednesday)
        reorder(user, scope='today', ids=[b.pk, a.pk])
        late = create_task(user, title='17:00', due_date=thursday, due_time=time(17, 0))
        early = create_task(user, title='08:00', due_date=thursday, due_time=time(8, 0))

        freeze_now(2026, 10, 29)  # the next day

        assert (late.today_position, early.today_position) == (0, 0)
        assert not has_manual_order(todays_tasks(user), 'today_position')
        listed = order_tasks(todays_tasks(user), 'date')
        assert [task.title for task in listed] == ['08:00', '17:00']  # due time, not creation

    def test_a_subtask_drag_does_not_put_today_into_manual_order(self, user, config, wednesday):
        parent = create_task(user, title='P')
        first = create_task(user, title='S1', parent=parent, due_date=wednesday)
        second = create_task(user, title='S2', parent=parent, due_date=wednesday)

        reorder(user, scope='subtasks', ids=[second.pk, first.pk], parent=parent)

        assert has_manual_order(subtasks_of(parent))
        assert not has_manual_order(todays_tasks(user), 'today_position')
        assert create_task(user, title='New', due_date=wednesday).today_position == 0

    def test_a_today_drag_does_not_put_subtasks_into_manual_order(self, user, config, wednesday):
        parent = create_task(user, title='P')
        first = create_task(user, title='S1', parent=parent, due_date=wednesday)
        second = create_task(user, title='S2', parent=parent, due_date=wednesday)

        reorder(user, scope='today', ids=[second.pk, first.pk])

        assert has_manual_order(todays_tasks(user), 'today_position')
        assert not has_manual_order(subtasks_of(parent))
        assert create_task(user, title='S3', parent=parent).position == 0

    def test_a_subtask_joins_only_its_own_parents_manual_order(self, user, config):
        p1, p2 = create_task(user, title='P1'), create_task(user, title='P2')
        s1 = create_task(user, title='S1', parent=p1)
        s2 = create_task(user, title='S2', parent=p1)
        reorder(user, scope='subtasks', ids=[s2.pk, s1.pk], parent=p1)

        assert create_task(user, title='S3', parent=p2).position == 0  # another parent's list
        assert create_task(user, title='S4', parent=p1).position == 2  # joins the end

    def test_changing_the_date_forgets_the_place_in_todays_order(self, user, config, wednesday):
        a = create_task(user, title='A', due_date=wednesday)
        b = create_task(user, title='B', due_date=wednesday)
        reorder(user, scope='today', ids=[b.pk, a.pk])
        assert a.refresh_from_db() is None and a.today_position == 1

        update_task(a, changes={'due_date': date(2026, 11, 2)})
        postpone_task(b, preset='tomorrow')

        a.refresh_from_db()
        b.refresh_from_db()
        assert (a.today_position, b.today_position) == (0, 0)


class TestUpdate:
    def test_a_partial_update_changes_only_what_is_given(self, user, config):
        task = create_task(user, title='Old', description='d', priority=2)

        update_task(task, changes={'title': 'New'})

        task.refresh_from_db()
        assert (task.title, task.title_text, task.description, task.priority) == (
            'New',
            'new',
            'd',
            2,
        )

    def test_every_create_rule_applies(self, user, config):
        task = create_task(user, title='T')

        assert 'title' in errors(update_task, task, changes={'title': ' '})
        assert 'description' in errors(
            update_task, task, changes={'description': 'x' * (DESCRIPTION_MAX + 1)}
        )
        assert 'priority' in errors(update_task, task, changes={'priority': 9})
        assert 'due_time' in errors(update_task, task, changes={'due_time': time(8, 0)})
        assert 'labels' in errors(update_task, task, changes={'labels': [uuid4()]})
        with pytest.raises(ValueError):
            update_task(task, changes={'status': 'done'})

    def test_a_failed_update_leaves_the_task_untouched(self, user, config):
        task = create_task(user, title='T', priority=1)

        errors(update_task, task, changes={'title': 'Changed', 'priority': 9})

        task.refresh_from_db()
        assert (task.title, task.priority) == ('T', 1)

    def test_clearing_the_date_clears_the_time(self, user, config):
        task = create_task(user, title='T', due_date=date(2026, 11, 3), due_time=time(8, 0))

        update_task(task, changes={'due_date': None})

        task.refresh_from_db()
        assert (task.due_date, task.due_time) == (None, None)

    def test_the_time_can_be_set_on_a_task_with_a_date(self, user, config):
        task = create_task(user, title='T', due_date=date(2026, 11, 3))

        update_task(task, changes={'due_time': time(14, 30)})

        task.refresh_from_db()
        assert task.due_time == time(14, 30)

    def test_labels_are_replaced(self, user, config):
        a, b = make_label(user, 'A'), make_label(user, 'B')
        task = create_task(user, title='T', labels=[a])

        update_task(task, changes={'labels': [b]})

        assert list(task.labels.all()) == [b]
        update_task(task, changes={'labels': []})
        assert task.labels.count() == 0

    def test_moving_under_a_parent(self, user, config):
        parent = create_task(user, title='Parent')
        task = create_task(user, title='Task')

        update_task(task, changes={'parent': parent})
        task.refresh_from_db()
        assert task.parent == parent

        update_task(task, changes={'parent': None})
        task.refresh_from_db()
        assert task.parent is None

    def test_a_task_with_subtasks_cannot_become_a_subtask(self, user, config):
        parent = create_task(user, title='Parent')
        create_task(user, title='Sub', parent=parent)
        other = create_task(user, title='Other')

        assert 'parent' in errors(update_task, parent, changes={'parent': other})

    def test_a_task_cannot_be_its_own_parent(self, user, config):
        task = create_task(user, title='T')

        assert 'parent' in errors(update_task, task, changes={'parent': task})

    def test_a_trashed_task_is_read_only(self, user, config):
        task = create_task(user, title='T')
        trash_task(task)

        assert errors(update_task, task, changes={'title': 'x'}) == {
            '__all__': ['Restore the task before changing it.']
        }

    def test_updated_at_moves(self, user, config):
        task = create_task(user, title='T')
        before = task.updated_at

        update_task(task, changes={'title': 'U'})

        assert task.updated_at > before

    def test_toggling_a_checklist_item_rewrites_the_description(self, user, config):
        task = create_task(user, title='T', description='- [ ] one\n- [ ] two')

        toggle_checklist(task, 1, True)

        task.refresh_from_db()
        assert task.description == '- [ ] one\n- [x] two'
        toggle_checklist(task, 1, False)
        task.refresh_from_db()
        assert task.description == '- [ ] one\n- [ ] two'
        assert errors(toggle_checklist, task, 5, True) == {'index': ['No such checklist item.']}


class TestComplete:
    def test_complete_and_uncomplete(self, user, config):
        task = create_task(user, title='T')

        complete_task(task)
        task.refresh_from_db()
        assert task.completed_at is not None and not task.is_open

        done_at = task.completed_at
        complete_task(task)  # a no-op
        task.refresh_from_db()
        assert task.completed_at == done_at

        uncomplete_task(task)
        task.refresh_from_db()
        assert task.completed_at is None and task.is_open

    def test_completing_a_parent_completes_its_open_subtasks(self, user, config):
        parent = create_task(user, title='Parent')
        open_sub = create_task(user, title='Open', parent=parent)
        done_sub = create_task(user, title='Done', parent=parent)
        complete_task(done_sub)
        done_at = Task.objects.get(pk=done_sub.pk).completed_at
        trashed_sub = create_task(user, title='Trashed', parent=parent)
        trash_task(trashed_sub)

        complete_task(parent)

        assert Task.objects.get(pk=open_sub.pk).completed_at is not None
        assert Task.objects.get(pk=done_sub.pk).completed_at == done_at
        assert Task.objects.get(pk=trashed_sub.pk).completed_at is None

    def test_uncompleting_a_subtask_leaves_the_parent(self, user, config):
        parent = create_task(user, title='Parent')
        sub = create_task(user, title='Sub', parent=parent)
        complete_task(parent)

        uncomplete_task(Task.objects.get(pk=sub.pk))

        assert Task.objects.get(pk=sub.pk).completed_at is None
        assert Task.objects.get(pk=parent.pk).completed_at is not None

    def test_a_trashed_task_cannot_be_completed(self, user, config):
        task = create_task(user, title='T')
        trash_task(task)

        assert '__all__' in errors(complete_task, task)


class TestPostpone:
    """Today is Wednesday 2026-10-28."""

    @pytest.mark.parametrize(
        ('today', 'preset', 'expected'),
        [
            (date(2026, 10, 28), 'tomorrow', date(2026, 10, 29)),  # Wednesday
            (date(2026, 10, 28), 'next_week', date(2026, 11, 2)),  # the Monday after
            (date(2026, 10, 28), 'next_weekend', date(2026, 10, 31)),  # this Saturday
            (date(2026, 10, 26), 'next_week', date(2026, 11, 2)),  # a Monday: the one after
            (date(2026, 10, 26), 'next_weekend', date(2026, 10, 31)),
            (date(2026, 10, 31), 'next_week', date(2026, 11, 2)),  # a Saturday
            (date(2026, 10, 31), 'next_weekend', date(2026, 11, 7)),  # not today
            (date(2026, 11, 1), 'next_week', date(2026, 11, 2)),  # a Sunday: tomorrow
            (date(2026, 11, 1), 'next_weekend', date(2026, 11, 7)),
            (date(2026, 12, 31), 'tomorrow', date(2027, 1, 1)),  # across the year
        ],
    )
    def test_presets(self, today, preset, expected):
        assert postpone_target(today, preset) == expected

    def test_a_chosen_date_is_today_or_later(self):
        today = date(2026, 10, 28)

        assert postpone_target(today, 'date', today) == today
        assert postpone_target(today, 'date', date(2027, 1, 1)) == date(2027, 1, 1)
        assert 'date' in errors(postpone_target, today, 'date', date(2026, 10, 27))
        assert 'date' in errors(postpone_target, today, 'date', None)
        assert 'preset' in errors(postpone_target, today, 'someday')

    def test_it_keeps_the_time_and_uses_the_owners_today(self, user, config, freeze_now):
        user.timezone = 'Pacific/Auckland'
        user.save()
        freeze_now(2026, 10, 28, 23, 0)  # already Thursday 29 October there
        task = create_task(user, title='T', due_date=date(2026, 10, 20), due_time=time(7, 15))

        postpone_task(task, preset='tomorrow')

        task.refresh_from_db()
        assert (task.due_date, task.due_time) == (date(2026, 10, 30), time(7, 15))

    def test_a_task_without_a_date_gets_one(self, user, config, wednesday):
        task = create_task(user, title='T')

        postpone_task(task, preset='date', date=date(2026, 12, 1))

        task.refresh_from_db()
        assert task.due_date == date(2026, 12, 1)

    def test_only_open_tasks_are_postponed(self, user, config):
        task = create_task(user, title='T')
        complete_task(task)

        assert '__all__' in errors(postpone_task, task, preset='tomorrow')

    def test_reschedule_overdue(self, user, other_user, config, wednesday):
        late = create_task(user, title='Late', due_date=date(2026, 10, 1), due_time=time(8, 0))
        today = create_task(user, title='Today', due_date=wednesday)
        later = create_task(user, title='Later', due_date=date(2026, 11, 5))
        nodate = create_task(user, title='No date')
        done = create_task(user, title='Done', due_date=date(2026, 10, 2))
        complete_task(done)
        trashed = create_task(user, title='Trashed', due_date=date(2026, 10, 3))
        trash_task(trashed)
        theirs = create_task(other_user, title='Theirs', due_date=date(2026, 10, 1))

        assert reschedule_overdue(user) == 1

        late.refresh_from_db()
        assert (late.due_date, late.due_time) == (wednesday, time(8, 0))
        for untouched, expected in (
            (today, wednesday),
            (later, date(2026, 11, 5)),
            (nodate, None),
            (done, date(2026, 10, 2)),
            (trashed, date(2026, 10, 3)),
            (theirs, date(2026, 10, 1)),
        ):
            untouched.refresh_from_db()
            assert untouched.due_date == expected


class TestTrash:
    def test_trash_takes_the_subtasks_and_restore_gives_them_back(self, user, config):
        parent = create_task(user, title='Parent')
        sub = create_task(user, title='Sub', parent=parent)
        earlier = create_task(user, title='Earlier', parent=parent)
        trash_task(earlier)
        earlier_at = Task.objects.get(pk=earlier.pk).deleted_at

        trash_task(parent)

        parent.refresh_from_db()
        sub.refresh_from_db()
        assert parent.deleted_at is not None
        assert sub.deleted_at == parent.deleted_at
        assert Task.objects.get(pk=earlier.pk).deleted_at == earlier_at

        restore_task(parent)

        assert Task.objects.get(pk=parent.pk).deleted_at is None
        assert Task.objects.get(pk=sub.pk).deleted_at is None
        assert Task.objects.get(pk=earlier.pk).deleted_at == earlier_at  # trashed on its own

    def test_a_subtask_cannot_be_restored_under_a_trashed_parent(self, user, config):
        parent = create_task(user, title='Parent')
        sub = create_task(user, title='Sub', parent=parent)
        trash_task(parent)

        assert errors(restore_task, Task.objects.get(pk=sub.pk)) == {
            '__all__': ['Restore the parent task first.']
        }

    def test_a_subtask_trashed_alone_restores_alone(self, user, config):
        parent = create_task(user, title='Parent')
        sub = create_task(user, title='Sub', parent=parent)
        trash_task(sub)

        restore_task(Task.objects.get(pk=sub.pk))

        assert Task.objects.get(pk=sub.pk).deleted_at is None

    def test_trashing_twice_changes_nothing(self, user, config):
        task = create_task(user, title='T')
        trash_task(task)
        first = Task.objects.get(pk=task.pk).deleted_at

        trash_task(Task.objects.get(pk=task.pk))

        assert Task.objects.get(pk=task.pk).deleted_at == first

    def test_nothing_is_refunded(self, funded_user, config):
        from apps.billing.services import get_balance

        config.free_tasks_per_month = 2
        config.save()

        for title in ('A', 'B', 'C'):
            task = create_task(funded_user, title=title)
        trash_task(task)
        delete_forever(task)

        assert get_balance(funded_user) == 95

    def test_delete_forever_takes_the_subtasks(self, user, config):
        parent = create_task(user, title='Parent')
        create_task(user, title='Sub', parent=parent)

        delete_forever(parent)

        assert not Task.objects.exists()

    def test_purge_deletes_only_what_was_trashed_long_enough_ago(self, user, config):
        config.trash_days = 30
        config.save()
        old = create_task(user, title='Old')
        old_sub = create_task(user, title='Old sub', parent=old)
        recent = create_task(user, title='Recent')
        live = create_task(user, title='Live')
        now = datetime.now(tz=utc(2026, 1, 1).tzinfo)
        Task.objects.filter(pk__in=[old.pk, old_sub.pk]).update(deleted_at=now - timedelta(days=31))
        Task.objects.filter(pk=recent.pk).update(deleted_at=now - timedelta(days=29))

        assert purge_trash() == 2

        assert set(Task.objects.values_list('pk', flat=True)) == {recent.pk, live.pk}
        assert purge_trash() == 0

    def test_purge_logs_and_skips_a_row_that_fails(self, user, config, monkeypatch, caplog):
        task = create_task(user, title='Old')
        Task.objects.filter(pk=task.pk).update(deleted_at=utc(2020, 1, 1))
        other = create_task(user, title='Old 2')
        Task.objects.filter(pk=other.pk).update(deleted_at=utc(2020, 1, 1))
        real = Task.objects.filter

        class Boom:
            def delete(self):
                raise RuntimeError('database trouble')

        def filtering(*args, **kwargs):
            if kwargs.get('pk') == task.pk:
                return Boom()
            return real(*args, **kwargs)

        monkeypatch.setattr(Task.objects, 'filter', filtering)

        assert purge_trash() == 1
        assert 'Could not purge task' in caplog.text

    def test_the_purge_job_calls_the_service(self, user, config):
        from .. import jobs

        task = create_task(user, title='Old')
        Task.objects.filter(pk=task.pk).update(deleted_at=utc(2020, 1, 1))

        jobs.purge_trash()

        assert not Task.objects.exists()


class TestReorder:
    def test_today_scope(self, user, config, wednesday):
        a = make_task(user, due_date=wednesday)
        b = make_task(user, due_date=wednesday)
        c = make_task(user, due_date=date(2026, 10, 1))  # overdue: not in the ordered list
        later = make_task(user, due_date=date(2026, 12, 1))  # not in it either

        assert reorder(user, scope='today', ids=[c.pk, later.pk, b.pk, a.pk]) == 2

        for task, position in ((c, 0), (b, 2), (a, 3), (later, 0)):
            task.refresh_from_db()
            assert task.today_position == position
            assert task.position == 0  # the subtasks' column is untouched

    def test_subtasks_scope(self, user, config):
        parent = make_task(user)
        a = make_task(user, parent=parent)
        b = make_task(user, parent=parent)
        stranger = make_task(user)

        assert reorder(user, scope='subtasks', ids=[b.pk, a.pk, stranger.pk], parent=parent) == 2

        a.refresh_from_db()
        b.refresh_from_db()
        stranger.refresh_from_db()
        assert (b.position, a.position, stranger.position) == (0, 1, 0)
        assert (a.today_position, b.today_position) == (0, 0)  # Today's column is untouched

    def test_ids_of_another_user_are_ignored(self, user, other_user, config, wednesday):
        mine = make_task(user, due_date=wednesday)
        theirs = make_task(other_user, due_date=wednesday)

        assert reorder(user, scope='today', ids=[theirs.pk, mine.pk]) == 1

        theirs.refresh_from_db()
        assert theirs.today_position == 0

    def test_a_parent_of_another_user_is_refused(self, user, other_user, config):
        theirs = make_task(other_user)

        assert 'parent' in errors(reorder, user, scope='subtasks', ids=[], parent=theirs)
        assert 'parent' in errors(reorder, user, scope='subtasks', ids=[])
        assert 'scope' in errors(reorder, user, scope='all', ids=[])

    def test_a_manual_order_sorts_by_position(self, user, config, wednesday):
        a = make_task(user, due_date=wednesday, title='A')
        b = make_task(user, due_date=wednesday, title='B')
        c = make_task(user, due_date=wednesday, title='C')

        reorder(user, scope='today', ids=[c.pk, a.pk, b.pk])

        ordered = order_tasks(Task.objects.filter(user=user), 'today_manual')
        assert [task.title for task in ordered] == ['C', 'A', 'B']

    def test_a_manual_order_of_subtasks_sorts_by_position(self, user, config):
        parent = make_task(user)
        a = make_task(user, parent=parent, title='A')
        b = make_task(user, parent=parent, title='B')
        reorder(user, scope='subtasks', ids=[b.pk, a.pk], parent=parent)

        ordered = order_tasks(Task.objects.filter(parent=parent), 'manual')

        assert [task.title for task in ordered] == ['B', 'A']


class TestDefaultOrder:
    def test_due_date_then_date_only_before_timed_then_time_then_priority_then_created(
        self, user, config
    ):
        d1, d2 = date(2026, 11, 1), date(2026, 11, 2)
        undated_p1 = make_task(user, title='undated p1', priority=1)
        timed_late = make_task(user, title='d1 15:00', due_date=d1, due_time=time(15, 0))
        timed_early = make_task(user, title='d1 08:00', due_date=d1, due_time=time(8, 0))
        date_only_p0 = make_task(user, title='d1 date only', due_date=d1)
        date_only_p1 = make_task(user, title='d1 date only p1', due_date=d1, priority=1)
        next_day = make_task(user, title='d2', due_date=d2)
        older_p0 = make_task(user, title='undated p0')
        assert older_p0 and next_day

        titles = [task.title for task in order_tasks(Task.objects.filter(user=user))]

        assert titles == [
            date_only_p1.title,
            date_only_p0.title,
            timed_early.title,
            timed_late.title,
            next_day.title,
            undated_p1.title,
            'undated p0',
        ]

    def test_priority_sort(self, user, config):
        none_early = make_task(user, title='none', due_date=date(2026, 11, 1))
        p3 = make_task(user, title='p3', priority=3, due_date=date(2026, 12, 1))
        p1_late = make_task(user, title='p1 late', priority=1, due_date=date(2026, 12, 1))
        p1_early = make_task(user, title='p1 early', priority=1, due_date=date(2026, 11, 5))
        assert none_early and p3

        titles = [task.title for task in order_tasks(Task.objects.filter(user=user), 'priority')]

        assert titles == ['p1 early', p1_late.title, 'p3', 'none']
        assert p1_early.title == 'p1 early'

    def test_created_and_title_sorts(self, user, config):
        b = make_task(user, title='Bravo')
        a = make_task(user, title='Álvaro')
        c = make_task(user, title='Charlie')

        by_created = [t.title for t in order_tasks(Task.objects.filter(user=user), 'created')]
        by_title = [t.title for t in order_tasks(Task.objects.filter(user=user), 'title')]

        assert by_created == [c.title, a.title, b.title]
        assert by_title == [a.title, b.title, c.title]  # accents ignored: álvaro < bravo

    def test_an_unknown_sort_is_the_default(self, user, config):
        make_task(user, title='x')

        assert order_tasks(Task.objects.all(), 'bogus').count() == 1


class TestSets:
    def test_today_set(self, user, other_user, config, wednesday):
        overdue = make_task(user, due_date=date(2026, 10, 20))
        today = make_task(user, due_date=wednesday)
        tomorrow = make_task(user, due_date=date(2026, 10, 29))
        nodate = make_task(user)
        done = make_task(user, due_date=wednesday, completed_at=utc(2026, 10, 27))
        trashed = make_task(user, due_date=wednesday, deleted_at=utc(2026, 10, 27))
        theirs = make_task(other_user, due_date=wednesday)

        assert set(today_set(user)) == {overdue, today}
        assert not {tomorrow, nodate, done, trashed, theirs} & set(today_set(user))

    def test_today_follows_the_users_zone(self, user, config, freeze_now):
        user.timezone = 'Pacific/Auckland'
        user.save()
        freeze_now(2026, 10, 28, 23, 0)  # 29 October in Auckland
        thursday = make_task(user, due_date=date(2026, 10, 29))

        assert thursday in today_set(user)


class TestDueAt:
    def test_a_task_without_a_date_has_no_instant(self, user, config):
        assert due_at(make_task(user), get_account(user)) is None

    def test_the_date_and_time_in_the_users_zone(self, user, config):
        user.timezone = 'America/New_York'
        user.save()
        task = make_task(user, due_date=date(2026, 12, 1), due_time=time(8, 30))

        assert due_at(task, get_account(user)) == utc(2026, 12, 1, 13, 30)

    def test_a_date_only_task_uses_the_default_due_time(self, user, config):
        account = get_account(user)
        account.default_due_time = time(7, 0)
        account.save()
        task = make_task(user, due_date=date(2026, 12, 1))

        assert due_at(task, account) == utc(2026, 12, 1, 7, 0)

    def test_a_blank_zone_is_utc(self, user, config):
        task = make_task(user, due_date=date(2026, 12, 1), due_time=time(8, 0))

        assert due_at(task, get_account(user)) == utc(2026, 12, 1, 8, 0)


def test_settings_row_defaults(db):
    row = TodoSettings.load()

    assert (
        row.free_tasks_per_month,
        row.task_price,
        row.trash_days,
        row.max_open_tasks_per_user,
        row.max_labels_per_user,
    ) == (10, 10, 30, 10_000, 200)
    assert TodoSettings.load().pk == 1


def test_users_are_isolated(db):
    one, two = make_user(), make_user()
    make_task(one, number=1000)
    make_task(two, number=1000)  # the same number for another user is fine

    assert Task.objects.count() == 2
