"""The locking and the follow-ups of the review of #278 (issue #273): every write that touches the
blocker graph takes the user's `TodoAccount` row first and then the task acted on, its subtasks and
its dependents; the races that order closes (a blocked task left Blocked, a reopened task stuck);
the stale-page refusal; closing subtasks that have blockers of their own; many dependents; the
suggestions and their rate limit.

SQLite has no row locks and drops `FOR UPDATE`, so a deadlock can't be reproduced here (the same as
Receipts' `TestLockOrder`): these record the order the locks are asked for instead.
"""

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from ..models import Task, TaskBlocker, TaskState
from ..services import (
    add_blocker,
    close_task,
    complete_task,
    delete_forever,
    remove_blocker,
    reorder,
    reschedule_overdue,
    restore_task,
    set_state,
    toggle_checklist,
    trash_task,
    uncomplete_task,
    update_task,
)
from ..services import billing as billing_service
from ..services import blockers as blockers_service
from ..services import tasks as tasks_service
from ..services.blockers import dependents_closure, suggestions
from .test_blockers import HTMX, link, make, reload, text

pytestmark = pytest.mark.django_db

ACCOUNT = 'account'


@pytest.fixture
def order(monkeypatch) -> list:
    """The locks asked for, in order: `'account'` for the user's `TodoAccount` row, and
    `frozenset(task ids)` for each call of `lock_tasks`."""
    recorded: list = []
    real = QuerySet.select_for_update

    def recording(self, *args, **kwargs):
        if self.model.__name__ == 'TodoAccount':
            recorded.append(ACCOUNT)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, 'select_for_update', recording)
    for module in (tasks_service, blockers_service):
        real_lock = module.lock_tasks

        def wrapper(*pks, _real=real_lock):
            recorded.append(frozenset(pks))
            return _real(*pks)

        monkeypatch.setattr(module, 'lock_tasks', wrapper)
    return recorded


def ids(*tasks: Task) -> frozenset:
    return frozenset(task.pk for task in tasks)


@pytest.fixture
def graph(funded_user):
    """A parent with two subtasks and two tasks each of them blocks: `dep_p` is blocked by the
    parent, `dep_s` by the first subtask."""
    parent = make(funded_user, 'Parent')
    sub1 = make(funded_user, 'Sub 1', parent=parent)
    sub2 = make(funded_user, 'Sub 2', parent=parent)
    dep_p, dep_s = make(funded_user, 'Dep P'), make(funded_user, 'Dep S')
    add_blocker(dep_p, parent)
    add_blocker(dep_s, sub1)
    return parent, sub1, sub2, dep_p, dep_s


class TestLockOrder:
    def test_closing_locks_the_account_then_the_task_its_subtasks_and_its_dependents(
        self, graph, order
    ):
        parent, sub1, sub2, dep_p, dep_s = graph
        order.clear()

        close_task(parent)

        assert order == [ACCOUNT, ids(parent), ids(sub1, sub2), ids(dep_p, dep_s)]

    def test_leaving_the_subtasks_open_locks_no_subtasks(self, graph, order):
        parent, _, _, dep_p, _ = graph
        order.clear()

        close_task(parent, subtasks='leave')

        assert order == [ACCOUNT, ids(parent), ids(dep_p)]

    def test_trashing_locks_in_the_same_order(self, graph, order):
        parent, sub1, sub2, dep_p, dep_s = graph
        order.clear()

        trash_task(parent)

        assert order == [ACCOUNT, ids(parent), ids(sub1, sub2), ids(dep_p, dep_s)]

    def test_restoring_locks_the_account_the_task_and_its_subtasks(self, graph, order):
        parent, sub1, sub2, _, _ = graph
        trash_task(parent)
        order.clear()

        restore_task(reload(parent))

        assert order == [ACCOUNT, ids(parent), ids(sub1, sub2)]

    def test_deleting_forever_locks_the_account_the_task_its_subtasks_then_its_dependents(
        self, graph, order
    ):
        parent, sub1, sub2, dep_p, dep_s = graph
        order.clear()

        delete_forever(parent)

        assert order == [ACCOUNT, ids(parent), ids(sub1, sub2), ids(dep_p, dep_s)]

    def test_reopening_locks_the_account_then_the_task(self, funded_user, order):
        task = make(funded_user, 'T')
        close_task(task)
        order.clear()

        uncomplete_task(task)

        assert order == [ACCOUNT, ids(task)]

    def test_adding_a_blocker_locks_the_account_then_both_tasks(self, funded_user, order):
        task, blocker = make(funded_user, 'T'), make(funded_user, 'B')
        order.clear()

        add_blocker(task, blocker)

        assert order == [ACCOUNT, ids(task, blocker)]

    def test_removing_a_blocker_locks_the_account_then_the_task(self, funded_user, order):
        task, blocker = make(funded_user, 'T'), make(funded_user, 'B')
        add_blocker(task, blocker)
        order.clear()

        remove_blocker(task, blocker)

        assert order == [ACCOUNT, ids(task)]

    def test_creating_with_blockers_locks_the_account_first_then_the_blockers(
        self, funded_user, order
    ):
        blocker = make(funded_user, 'B')
        order.clear()

        make(funded_user, 'New', blocked_by=[blocker.number])

        assert order[0] == ACCOUNT and ids(blocker) in order[1:]

    def test_the_writes_that_lock_a_task_or_several_take_the_account_first(
        self, funded_user, order
    ):
        task = make(funded_user, 'T', description='- [ ] a')
        other = make(funded_user, 'U')
        closed = make(funded_user, 'C')
        complete_task(closed)
        calls = [
            lambda: set_state(task, TaskState.IN_PROGRESS),
            lambda: update_task(task, changes={'title': 'New'}),
            lambda: update_task(other, changes={'parent': task}),
            lambda: toggle_checklist(task, 0, True),
            lambda: reschedule_overdue(funded_user),
            lambda: reorder(funded_user, scope='subtasks', ids=[other.pk], parent=task),
            lambda: tasks_service.set_resolution(closed, 'wont_do'),
        ]

        for call in calls:
            order.clear()
            call()
            assert order and order[0] == ACCOUNT

    def test_settling_an_unpaid_task_takes_the_account_first(self, funded_user, order):
        task = make(funded_user, 'Owed')
        Task.objects.filter(pk=task.pk).update(unpaid_at=timezone.now(), charged_credits=0)
        order.clear()

        assert billing_service.settle(str(funded_user.pk)) == 1

        assert order[0] == ACCOUNT


class TestTheRacesTheOrderCloses:
    def test_a_new_task_does_not_stay_blocked_by_a_blocker_that_closed_meanwhile(
        self, funded_user, monkeypatch
    ):
        """The blocker's row is locked before it is read: a close that committed once the lock was
        granted is seen, so the new task is not left Blocked with nothing to wait for."""
        blocker = make(funded_user, 'B')
        real = blockers_service.lock_tasks

        def closing_after_the_lock(*pks):
            locked = real(*pks)
            Task.objects.filter(pk=blocker.pk).update(completed_at=timezone.now())
            return locked

        monkeypatch.setattr(blockers_service, 'lock_tasks', closing_after_the_lock)

        new = make(funded_user, 'New', blocked_by=[blocker.number])

        assert reload(new).state == TaskState.TODO
        assert TaskBlocker.objects.filter(blocker=blocker, blocked=new).exists()

    def test_a_reopened_task_is_not_left_blocked_by_a_blocker_that_closed_meanwhile(
        self, funded_user, monkeypatch
    ):
        """The row is locked before the blockers are read (not after): the blocker that closes in
        between is read closed, so the reopened task is To do, not stuck Blocked."""
        task, blocker = make(funded_user, 'T'), make(funded_user, 'B')
        add_blocker(task, blocker)
        close_task(task)
        real = tasks_service.lock_tasks

        def closing_after_the_lock(*pks):
            locked = real(*pks)
            Task.objects.filter(pk=blocker.pk).update(completed_at=timezone.now())
            return locked

        monkeypatch.setattr(tasks_service, 'lock_tasks', closing_after_the_lock)

        uncomplete_task(task)

        assert reload(task).state == TaskState.TODO

    def test_three_links_forming_a_ring_are_refused_one_after_the_other(self, funded_user):
        a, b, c = (make(funded_user, name) for name in 'ABC')
        add_blocker(a, b)
        add_blocker(b, c)

        with pytest.raises(Exception, match='directly or through'):
            add_blocker(c, a)

        assert TaskBlocker.objects.count() == 2


class TestStaleCloseRefusal:
    def test_a_row_that_was_stale_is_sent_again_so_the_next_click_opens_the_dialog(
        self, web, funded_user
    ):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'The blocker')
        link(blocker, task)  # added in another tab, after this page was rendered

        response = web.post(
            reverse('todo:complete', args=[task.number]), {'ctx': 'row', 'view': 'all'}, **HTMX
        )

        body = text(response)
        assert response.status_code == 422 and 'Confirm to close it anyway.' in body
        row = body[body.index(f'<li id="task-{task.number}"') :]
        assert 'hx-swap-oob="true"' in row.split('>')[0] + '>' + row.split('>')[1]
        assert 'todoOpenCloseDialog($el)' in row and 'data-blockers="1"' in row

    def test_the_task_page_gets_its_complete_buttons_again(self, web, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'The blocker')
        link(blocker, task)

        response = web.post(reverse('todo:complete', args=[task.number]), {'ctx': 'page'}, **HTMX)

        body = text(response)
        assert response.status_code == 422
        assert 'id="complete-action"' in body and 'hx-swap-oob="true"' in body
        assert body.count('data-blockers="1"') == 2
        assert reload(task).completed_at is None


class TestClosingSubtasksThatAreBlocked:
    @pytest.fixture
    def tree(self, funded_user):
        parent = make(funded_user, 'Parent')
        sub = make(funded_user, 'Sub', parent=parent)
        outside = make(funded_user, 'Outside')
        add_blocker(sub, outside)
        return parent, sub, outside

    def post(self, web, parent, name='complete', **fields):
        return web.post(
            reverse(f'todo:{name}', args=[parent.number]), {'ctx': 'page', **fields}, **HTMX
        )

    def test_the_refusal_names_the_subtask_and_what_blocks_it(self, web, tree):
        parent, sub, outside = tree

        response = self.post(web, parent)

        assert response.status_code == 422
        assert f'#{sub.number} Sub is still blocked by #{outside.number} Outside.' in text(response)
        assert reload(parent).completed_at is None and reload(sub).completed_at is None

    @pytest.mark.parametrize('name', ['complete', 'wont-do'])
    def test_wont_do_asks_too_and_the_confirmation_covers_the_subtasks(self, web, tree, name):
        parent, sub, outside = tree

        assert self.post(web, parent, name, subtasks='wont_do').status_code == 422
        response = self.post(web, parent, name, subtasks='wont_do', confirm_blockers='1')

        assert response.status_code == 200
        assert reload(parent).completed_at is not None and reload(sub).resolution == 'wont_do'
        assert TaskBlocker.objects.filter(blocked=sub, blocker=outside).exists()

    def test_leaving_the_subtasks_open_asks_nothing(self, web, tree):
        parent, sub, _ = tree

        response = self.post(web, parent, subtasks='leave')

        assert response.status_code == 200
        assert reload(parent).completed_at is not None and reload(sub).completed_at is None

    def test_a_blocker_that_closes_in_the_same_cascade_is_not_named(self, web, funded_user):
        parent = make(funded_user, 'Parent')
        first = make(funded_user, 'First', parent=parent)
        second = make(funded_user, 'Second', parent=parent)
        add_blocker(second, first)  # both close together

        assert blockers_service.subtasks_with_open_blockers(parent) == []
        assert self.post(web, parent).status_code == 200

    def test_the_parents_own_blockers_and_the_subtasks_are_named_together(
        self, web, tree, funded_user
    ):
        parent, sub, outside = tree
        mine = make(funded_user, 'Mine')
        add_blocker(parent, mine)

        message = text(self.post(web, parent))

        assert f'This task is still blocked by #{mine.number} Mine.' in message
        assert f'#{sub.number} Sub is still blocked by #{outside.number} Outside.' in message

    def test_the_json_lists_the_subtasks(self, web, tree):
        parent, sub, outside = tree

        data = web.get(reverse('todo:open-blockers', args=[parent.number])).json()

        assert data['blockers'] == []
        assert data['subtasks'] == [
            {
                'number': sub.number,
                'title': 'Sub',
                'blockers': [{'number': outside.number, 'title': 'Outside'}],
            }
        ]

    def test_the_dialog_has_the_subtask_list_and_the_script_reads_it(self, web, tree):
        parent, _, _ = tree

        body = text(web.get(reverse('todo:task', args=[parent.number])))

        assert 'data-subtask-blockers' in body and 'needsConfirm' in body


class TestManyDependents:
    @pytest.fixture(autouse=True)
    def _free_tasks(self, config):
        config.free_tasks_per_month = 1000
        config.save()

    def taskblocker_queries(self, count: int, user) -> tuple[int, int]:
        blocker = make(user, f'Blocker {count}')
        for index in range(count):
            add_blocker(make(user, f'Dep {count}-{index}'), blocker)
        with CaptureQueriesContext(connection) as queries:
            close_task(blocker)
        reads = [q for q in queries.captured_queries if 'todo_taskblocker' in q['sql']]
        return len(reads), len(queries)

    def test_the_open_blocker_check_is_one_query_however_many_dependents(self, funded_user):
        few_reads, few_total = self.taskblocker_queries(3, funded_user)
        many_reads, many_total = self.taskblocker_queries(30, funded_user)

        print('Q', few_reads, few_total, many_reads, many_total)
        assert many_reads == few_reads
        # What is left per dependent is the state write, its held reminders and its notice.
        assert (many_total - few_total) / 27 <= 12

    def test_every_dependent_is_still_released_and_told(self, funded_user):
        blocker = make(funded_user, 'Blocker')
        dependents = [make(funded_user, f'Dep {index}') for index in range(5)]
        for dependent in dependents:
            add_blocker(dependent, blocker)
        still = make(funded_user, 'Still blocked')
        other = make(funded_user, 'Other open blocker')
        add_blocker(still, blocker)
        add_blocker(still, other)

        close_task(blocker)

        assert all(reload(dep).state == TaskState.TODO for dep in dependents)
        assert reload(still).state == TaskState.BLOCKED


class TestSuggestions:
    def test_tasks_that_would_be_refused_as_a_cycle_are_not_offered(self, funded_user):
        top, middle, bottom, free = (
            make(funded_user, name) for name in ('Top', 'Mid', 'Bot', 'Free')
        )
        add_blocker(middle, top)  # top blocks middle
        add_blocker(bottom, middle)  # middle blocks bottom

        offered = suggestions(funded_user, top, '')

        assert dependents_closure(top.pk) == {middle.pk, bottom.pk}
        assert offered == [free]

    def test_its_own_blockers_and_itself_are_not_offered_either(self, funded_user):
        task, blocker, free = make(funded_user, 'T'), make(funded_user, 'B'), make(funded_user, 'F')
        add_blocker(task, blocker)

        assert suggestions(funded_user, task, '') == [free]


class TestSuggestionRateLimit:
    def test_over_the_limit_the_box_offers_nothing_instead_of_an_error(
        self, web, funded_user, settings, ratelimit_clock
    ):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'TODO_BLOCKER_SUGGEST_USER': (2, 60),
        }
        task = make(funded_user, 'Main')
        make(funded_user, 'Other')
        url = reverse('todo:blocker-suggest', args=[task.number])

        first = web.get(url, **HTMX)
        web.get(url, **HTMX)
        third = web.get(url, **HTMX)

        assert text(first).count('<option') == 1
        assert third.status_code == 200 and text(third).count('<option') == 0

    def test_the_rule_exists_and_is_loose(self, settings):
        limit, window = settings.RATELIMIT_RULES['TODO_BLOCKER_SUGGEST_USER']
        assert (limit, window) == (120, 60)
