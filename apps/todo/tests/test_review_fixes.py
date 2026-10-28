"""Fixes from the review of the foundation PR (#192): the checklist lost update, the form's
description cap, `settle` under the account lock, the unpaid-task lifecycle, the re-parent race,
completing a parent from a list, and the query savings."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import add_credits

from ..forms import TaskForm
from ..models import Task, TodoAccount
from ..services import (
    DESCRIPTION_MAX,
    complete_task,
    create_task,
    delete_forever,
    get_account,
    purge_trash,
    toggle_checklist,
    trash_task,
    update_task,
)
from ..services.billing import settle
from ..services.tasks import UNPAID_MESSAGE
from .test_views import HTMX, text

pytestmark = pytest.mark.django_db


@pytest.fixture
def locks(monkeypatch) -> list[str]:
    """The models `select_for_update` is asked for, in order."""
    locked: list[str] = []
    real = QuerySet.select_for_update

    def recording(self, *args, **kwargs):
        locked.append(self.model.__name__)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, 'select_for_update', recording)
    return locked


def owed_task(user, title='Owed'):
    """A user at the end of the free allowance (2) with one unpaid task, and creation blocked."""
    for number in range(2):
        create_task(user, title=f'Free {number}')
    return create_task(user, title=title, allow_unpaid=True, source='recurrence')


class TestChecklistLostUpdate:
    def test_two_toggles_on_stale_instances_both_survive(self, user, config):
        task = create_task(user, title='T', description='- [ ] a\n- [ ] b')
        first, second = Task.objects.get(pk=task.pk), Task.objects.get(pk=task.pk)  # both stale

        toggle_checklist(first, 0, True)
        toggle_checklist(second, 1, True)

        task.refresh_from_db()
        assert task.description == '- [x] a\n- [x] b'

    def test_it_toggles_the_fresh_description_and_returns_it(self, user, config):
        task = create_task(user, title='T', description='- [ ] a')
        Task.objects.filter(pk=task.pk).update(description='- [ ] a\n- [ ] new')

        result = toggle_checklist(task, 1, True)  # `task` still has the old text

        assert result.description == '- [ ] a\n- [x] new'

    def test_the_row_is_locked(self, user, config, locks):
        task = create_task(user, title='T', description='- [ ] a')
        locks.clear()

        toggle_checklist(task, 0, True)

        assert locks == ['Task']

    def test_a_task_deleted_meanwhile_is_a_refusal(self, user, config):
        task = create_task(user, title='T', description='- [ ] a')
        Task.objects.filter(pk=task.pk).delete()

        with pytest.raises(ValidationError):
            toggle_checklist(task, 0, True)


class TestFormDescriptionCap:
    def test_crlf_line_endings_count_once(self, user):
        line = 'x' * 99 + '\r\n'
        description = line * (
            (DESCRIPTION_MAX - 1) // 100
        )  # 100 chars a line with \n, 101 with \r\n
        assert len(description) > DESCRIPTION_MAX > len(description.replace('\r\n', '\n'))
        form = TaskForm({'title': 'T', 'description': description}, user=user)

        assert form.is_valid(), form.errors
        assert '\r' not in form.cleaned_data['description']

    def test_a_description_over_the_cap_is_still_refused(self, user):
        form = TaskForm({'title': 'T', 'description': 'x' * (DESCRIPTION_MAX + 1)}, user=user)

        assert not form.is_valid()
        assert 'too long' in form.errors['description'][0]

    def test_the_view_accepts_a_browser_submission_at_the_limit(self, web, funded_user):
        description = ('y' * 99 + '\r\n') * ((DESCRIPTION_MAX - 1) // 100)

        response = web.post(reverse('todo:create'), {'title': 'T', 'description': description})

        assert response.status_code == 302
        assert len(Task.objects.get(title='T').description) <= DESCRIPTION_MAX


class TestSettleLock:
    def test_the_final_check_and_the_unblock_run_under_the_account_lock(self, user, config, locks):
        owed_task(user)
        add_credits(user, 50, description='Top-up', source='test')
        locks.clear()

        assert settle(str(user.pk)) == 1

        assert locks[-1] == 'TodoAccount'  # after the task's own lock, for the final check
        assert TodoAccount.objects.get(user=user).creation_blocked_at is None

    def test_the_block_stays_when_an_unpaid_task_appeared_before_the_check(
        self, user, config, monkeypatch
    ):
        """What the lock guarantees: the check sees an unpaid task a concurrent creation committed
        first, so the block is not cleared over it. (Simulated: the account row is read, then a
        new unpaid task lands before the existence check, which is what the lock serialises.)"""
        owed_task(user)
        add_credits(user, 50, description='Top-up', source='test')
        real = QuerySet.select_for_update
        done = []

        def recording(self, *args, **kwargs):
            result = real(self, *args, **kwargs)
            if self.model is TodoAccount and not done:
                done.append(True)
                Task.objects.create(
                    user=user,
                    number=9999,
                    title='Another',
                    charged_credits=5,
                    unpaid_at=timezone.now(),
                )
            return result

        monkeypatch.setattr(QuerySet, 'select_for_update', recording)

        settle(str(user.pk))

        assert Task.objects.filter(user=user, unpaid_at__isnull=False).exists()
        assert TodoAccount.objects.get(user=user).creation_blocked_at is not None


class TestUnpaidTasksAreNotDeletedForever:
    def test_delete_forever_is_refused_until_settled(self, user, config):
        task = owed_task(user)
        trash_task(task)

        with pytest.raises(ValidationError) as caught:
            delete_forever(task)

        assert caught.value.messages == [UNPAID_MESSAGE]
        assert Task.objects.filter(pk=task.pk).exists()

        add_credits(user, 50, description='Top-up', source='test')
        settle(str(user.pk))
        delete_forever(Task.objects.get(pk=task.pk))
        assert not Task.objects.filter(pk=task.pk).exists()

    def test_a_parent_with_an_unpaid_subtask_is_refused_too(self, user, config):
        parent = create_task(user, title='P')
        sub = create_task(user, title='S', parent=parent)
        Task.objects.filter(pk=sub.pk).update(unpaid_at=timezone.now())
        trash_task(parent)

        with pytest.raises(ValidationError):
            delete_forever(parent)

    def test_trashing_and_restoring_stay_allowed(self, user, config):
        task = owed_task(user)

        trash_task(task)
        restore_after = Task.objects.get(pk=task.pk)
        from ..services import restore_task

        restore_task(restore_after)

        assert Task.objects.get(pk=task.pk).deleted_at is None

    def test_the_purge_skips_unpaid_tasks_until_they_are_settled(self, user, config):
        task = owed_task(user)
        paid = create_task(user, title='Paid', allow_unpaid=True)  # a second one, also owed
        Task.objects.filter(pk=paid.pk).update(unpaid_at=None)
        parent = create_task(user, title='Parent', allow_unpaid=True)
        sub = create_task(user, title='Sub', parent=parent, allow_unpaid=True)
        Task.objects.filter(pk=parent.pk).update(unpaid_at=None)
        old = timezone.now() - timedelta(days=400)
        Task.objects.filter(pk__in=[task.pk, paid.pk, parent.pk, sub.pk]).update(deleted_at=old)

        assert purge_trash() == 1  # only `paid`: `task` and `sub` owe, and `parent` holds `sub`

        assert set(Task.objects.values_list('pk', flat=True)) >= {task.pk, parent.pk, sub.pk}
        assert not Task.objects.filter(pk=paid.pk).exists()

        add_credits(user, 100, description='Top-up', source='test')
        settle(str(user.pk))

        assert purge_trash() == 3
        assert not Task.objects.filter(user=user, deleted_at=old).exists()

    def test_the_web_refuses_with_a_top_up_link(self, web, funded_user, config):
        # `funded_user` has 100 credits: spend them so the task stays owed
        config.free_tasks_per_month = 0
        config.task_price = 500
        config.save()
        task = create_task(funded_user, title='Owed', allow_unpaid=True)
        trash_task(task)

        htmx = web.post(reverse('todo:delete', args=[task.number]), **HTMX)

        assert htmx.status_code == 422 and htmx['HX-Retarget'] == '#todo-msg'
        body = text(htmx)
        assert 'not been paid for' in body and reverse('billing:credits-history') in body
        plain = web.post(reverse('todo:delete', args=[task.number]), follow=True)
        assert 'not been paid for' in text(plain)
        assert Task.objects.filter(pk=task.pk).exists()

    def test_the_page_context_retargets_to_the_panel_message(self, web, funded_user, config):
        config.free_tasks_per_month = 0
        config.task_price = 500
        config.save()
        task = create_task(funded_user, title='Owed', allow_unpaid=True)
        trash_task(task)

        response = web.post(reverse('todo:delete', args=[task.number]), {'ctx': 'page'}, **HTMX)

        assert response.status_code == 422 and response['HX-Retarget'] == '#task-msg'


class TestReparentRace:
    @pytest.fixture(autouse=True)
    def _free_tasks(self, config):
        config.free_tasks_per_month = 1000
        config.save()

    def test_both_rows_are_locked_before_the_checks(self, user, config, locks):
        task = create_task(user, title='T')
        parent = create_task(user, title='P')
        locks.clear()

        update_task(task, changes={'parent': parent})

        assert locks == ['Task']  # one locking query for both rows, in pk order
        assert Task.objects.get(pk=task.pk).parent_id == parent.pk

    def test_a_subtask_added_meanwhile_stops_the_move(self, user, config, monkeypatch):
        """The check reads the locked rows: a subtask that landed after the caller last looked is
        seen, so no second level of nesting results."""
        task = create_task(user, title='T')
        parent = create_task(user, title='P')
        real = QuerySet.select_for_update
        done: list[bool] = []

        def recording(self, *args, **kwargs):
            result = real(self, *args, **kwargs)
            if self.model is Task and not done:
                done.append(True)
                Task.objects.create(user=user, number=9999, title='Sneaky', parent=task)
            return result

        monkeypatch.setattr(QuerySet, 'select_for_update', recording)

        with pytest.raises(ValidationError) as caught:
            update_task(task, changes={'parent': parent})

        assert 'subtasks cannot become a subtask' in caught.value.messages[0]
        assert Task.objects.get(pk=task.pk).parent_id is None

    def test_the_parent_is_checked_again_under_the_lock(self, user, config):
        task = create_task(user, title='T')
        parent = create_task(user, title='P')
        grand = create_task(user, title='G')
        Task.objects.filter(pk=parent.pk).update(parent=grand)  # moved under another meanwhile

        with pytest.raises(ValidationError) as caught:
            update_task(task, changes={'parent': parent})

        assert caught.value.message_dict == {'parent': ['A subtask cannot have subtasks.']}

    def test_a_subtask_is_created_under_a_locked_parent(self, user, config, locks):
        parent = create_task(user, title='P')
        locks.clear()

        create_task(user, title='S', parent=parent)

        assert locks == ['TodoAccount', 'Task']

    def test_a_parent_that_became_a_subtask_meanwhile_is_refused(self, user, config):
        parent = create_task(user, title='P')
        grand = create_task(user, title='G')
        Task.objects.filter(pk=parent.pk).update(parent=grand)

        with pytest.raises(ValidationError) as caught:
            create_task(user, title='S', parent=parent)  # `parent` is a stale instance

        assert caught.value.message_dict == {'parent': ['A subtask cannot have subtasks.']}
        assert not Task.objects.filter(title='S').exists()


class TestCompletingAParentFromAList:
    def test_the_subtask_rows_are_removed_with_the_parent(self, web, funded_user, wednesday):
        parent = create_task(funded_user, title='P', due_date=wednesday)
        one = create_task(funded_user, title='S1', parent=parent, due_date=wednesday)
        two = create_task(funded_user, title='S2', parent=parent, due_date=wednesday)
        done = create_task(funded_user, title='S3', parent=parent, due_date=wednesday)
        complete_task(done)
        Task.objects.filter(pk=done.pk).update(completed_at=None)
        Task.objects.filter(pk=done.pk).update(completed_at=timezone.now())

        response = web.post(
            reverse('todo:complete', args=[parent.number]), {'view': 'today', 'ctx': 'row'}, **HTMX
        )

        body = response.content.decode()
        assert response.status_code == 200
        assert Task.objects.get(pk=one.pk).completed_at is not None
        for sub in (one, two):
            assert f'<li id="task-{sub.number}" hx-swap-oob="delete"></li>' in body
        assert f'id="task-{done.number}"' not in body  # it was already done
        assert 'hx-swap-oob="true"' in body  # the sidebar count is still sent

    def test_a_task_without_open_subtasks_adds_nothing(self, web, funded_user):
        task = create_task(funded_user, title='T')

        response = web.post(
            reverse('todo:complete', args=[task.number]), {'view': 'all', 'ctx': 'row'}, **HTMX
        )

        assert 'hx-swap-oob="delete"' not in response.content.decode()


class TestQuerySavings:
    def test_get_account_hands_back_the_user_it_was_given(self, user, django_assert_num_queries):
        get_account(user)  # created

        with django_assert_num_queries(1):
            account = get_account(user)
            assert account.user is user

    def test_postpone_looks_the_task_up_once(self, web, funded_user, wednesday):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        task = create_task(funded_user, title='T', due_date=wednesday)

        with CaptureQueriesContext(connection) as queries:
            web.post(reverse('todo:postpone', args=[task.number]), {'preset': 'tomorrow'}, **HTMX)

        lookups = [q for q in queries if 'WHERE ("todo_task"."number" =' in q['sql']]
        assert len(lookups) == 1
