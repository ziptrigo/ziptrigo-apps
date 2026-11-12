"""The To-do JWT API's task states, resolutions and blockers (issue #275, API version 2): the
`state`, `resolution`, `closed_at`, `blocked_by` and `blocks` fields, the list filters, `close`,
`reopen`, `resolution` and `state`, `state` and `blocked_by` on create and `PATCH`, the blocker
endpoints and the muted tasks' overdue rules. Over Django's test client, against the real URLs."""

from datetime import date, timedelta

import pytest

from ..models import Comment, Resolution, Task, TaskBlocker, TaskState
from ..services import (
    add_blocker,
    close_task,
    complete_task,
    create_task,
    set_state,
    trash_task,
)
from .api_support import (  # noqa: F401 -- the fixtures are used by name
    auth,
    delete,
    get,
    limit_rule,
    other_auth,
    patch,
    post,
)
from .factories import make_task

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def numbers(response) -> list[int]:
    return [task['number'] for task in response.json()['results']]


def make(user, title='T', **fields) -> Task:
    return create_task(user, title=title, **fields)


def reload(task: Task) -> Task:
    return Task.objects.get(pk=task.pk)


class TestFields:
    def test_an_open_task(self, client, auth, funded_user):
        task = make(funded_user)

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert body['state'] == 'todo' and body['resolution'] is None
        assert body['closed_at'] is None
        assert body['blocked_by'] == [] and body['blocks'] == []
        assert 'completed_at' not in body

    @pytest.mark.parametrize('state', ['in_progress', 'on_hold'])
    def test_the_state_is_reported(self, client, auth, funded_user, state):
        task = make(funded_user, state=state)

        assert get(client, f'/tasks/{task.number}', auth).json()['state'] == state

    @pytest.mark.parametrize(
        ('resolution', 'expected'), [(Resolution.DONE, 'done'), (Resolution.WONT_DO, 'wont_do')]
    )
    def test_a_closed_task(self, client, auth, funded_user, resolution, expected):
        task = close_task(make(funded_user), resolution)

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert body['closed_at'] is not None and body['resolution'] == expected
        assert body['state'] == 'todo'  # closing resets it

    def test_a_closed_task_with_no_resolution_recorded_reads_as_done(
        self, client, auth, funded_user
    ):
        task = complete_task(make(funded_user))
        Task.objects.filter(pk=task.pk).update(resolution='')

        assert get(client, f'/tasks/{task.number}', auth).json()['resolution'] == 'done'

    def test_the_links_in_both_directions_with_closed_and_trashed_ones(
        self, client, auth, funded_user
    ):
        task = make(funded_user, 'Main')
        open_one, closed_one, trashed_one = (
            make(funded_user, n) for n in ('Open', 'Closed', 'Bin')
        )
        for other in (open_one, closed_one, trashed_one):
            add_blocker(task, other)
        complete_task(closed_one)
        trash_task(trashed_one)
        waiting = make(funded_user, 'Waiting')
        add_blocker(waiting, task)

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert body['blocked_by'] == [
            {'number': open_one.number, 'title': 'Open', 'open': True, 'trashed': False},
            {'number': closed_one.number, 'title': 'Closed', 'open': False, 'trashed': False},
            {'number': trashed_one.number, 'title': 'Bin', 'open': False, 'trashed': True},
        ]
        assert body['blocks'] == [
            {'number': waiting.number, 'title': 'Waiting', 'open': True, 'trashed': False}
        ]
        assert body['state'] == 'blocked'


class TestListFilters:
    @pytest.fixture
    def zoo(self, funded_user):
        return {
            'todo': make(funded_user, 'Todo'),
            'doing': make(funded_user, 'Doing', state='in_progress'),
            'held': make(funded_user, 'Held', state='on_hold'),
            'done': complete_task(make(funded_user, 'Done')),
            'wont': close_task(make(funded_user, 'Wont'), Resolution.WONT_DO),
        }

    def test_view_closed_lists_both_resolutions_latest_first(self, client, auth, zoo):
        assert numbers(get(client, '/tasks', auth, view='closed')) == [
            zoo['wont'].number,
            zoo['done'].number,
        ]

    @pytest.mark.parametrize('resolution', ['done', 'wont_do'])
    def test_the_resolution_narrows_the_closed_view(self, client, auth, zoo, resolution):
        key = 'done' if resolution == 'done' else 'wont'

        response = get(client, '/tasks', auth, view='closed', resolution=resolution)

        assert numbers(response) == [zoo[key].number]

    def test_the_resolution_narrows_include_closed(self, client, auth, zoo):
        response = get(client, '/tasks', auth, include_closed='true', resolution='wont_do')

        assert set(numbers(response)) == {
            zoo['todo'].number,
            zoo['doing'].number,
            zoo['held'].number,
            zoo['wont'].number,
        }
        assert zoo['done'].number not in numbers(response)

    @pytest.mark.parametrize(
        'query',
        [
            {'resolution': 'done'},  # no closed tasks listed
            {'view': 'today', 'include_closed': 'true', 'resolution': 'done'},
            {'view': 'closed', 'resolution': 'maybe'},
        ],
    )
    def test_a_resolution_without_closed_tasks_or_an_unknown_one_is_a_400(
        self, client, auth, zoo, query
    ):
        assert get(client, '/tasks', auth, **query).status_code == 400

    def test_the_old_names_are_a_400_or_ignored(self, client, auth, zoo):
        assert get(client, '/tasks', auth, view='completed').status_code == 400
        # `include_completed` is not a parameter any more: it is ignored, and nothing is included
        assert zoo['done'].number not in numbers(get(client, '/tasks', auth, include_completed='1'))

    def test_the_state_filter(self, client, auth, zoo):
        assert numbers(get(client, '/tasks', auth, state='on_hold')) == [zoo['held'].number]
        assert set(numbers(get(client, '/tasks', auth, state='todo,in_progress'))) == {
            zoo['todo'].number,
            zoo['doing'].number,
        }

    def test_blocked_is_a_state_to_filter_by(self, client, auth, funded_user, zoo):
        blocked = make(funded_user, 'Blocked', blocked_by=[zoo['todo'].number])

        assert numbers(get(client, '/tasks', auth, state='blocked')) == [blocked.number]

    def test_closed_tasks_are_not_narrowed_by_the_state(self, client, auth, zoo):
        response = get(client, '/tasks', auth, include_closed='true', state='on_hold')

        assert set(numbers(response)) == {
            zoo['held'].number,
            zoo['done'].number,
            zoo['wont'].number,
        }

    def test_an_unknown_state_is_a_400(self, client, auth, zoo):
        assert get(client, '/tasks', auth, state='sleeping').status_code == 400
        assert get(client, '/tasks', auth, state='todo,sleeping').status_code == 400

    def test_the_state_filter_spends_the_search_budget(self, client, auth, settings, zoo):
        limit_rule(settings, 'TODO_SEARCH_USER')

        assert get(client, '/tasks', auth, state='todo').status_code == 200
        assert get(client, '/tasks', auth, state='todo').status_code == 429
        assert get(client, '/tasks', auth, view='closed', resolution='done').status_code == 200

    def test_today_lists_a_muted_task_that_is_not_overdue_elsewhere(
        self, client, auth, funded_user, wednesday
    ):
        held = make(funded_user, 'Held', due_date=wednesday - timedelta(days=2), state='on_hold')
        late = make(funded_user, 'Late', due_date=wednesday - timedelta(days=2))

        assert set(numbers(get(client, '/tasks', auth, view='today'))) == {held.number, late.number}
        assert numbers(get(client, '/tasks', auth, date_range='overdue')) == [late.number]

    def test_reschedule_overdue_leaves_muted_tasks_alone(
        self, client, auth, funded_user, wednesday
    ):
        blocker = make(funded_user, 'Blocker')
        held = make(funded_user, 'Held', due_date=wednesday - timedelta(days=2), state='on_hold')
        blocked = make(
            funded_user,
            'Blocked',
            due_date=wednesday - timedelta(days=2),
            blocked_by=[blocker.number],
        )
        late = make(funded_user, 'Late', due_date=wednesday - timedelta(days=2))

        response = post(client, '/tasks/reschedule-overdue', auth)

        assert response.json() == {'count': 1}
        assert reload(late).due_date == wednesday
        assert reload(held).due_date == wednesday - timedelta(days=2)
        assert reload(blocked).due_date == wednesday - timedelta(days=2)


class TestSavedFilterRoundTrip:
    def test_the_params_shown_select_what_the_filter_runs(self, client, auth, funded_user):
        make(funded_user, 'Plain')
        held = make(funded_user, 'Held', state='on_hold')
        wont = close_task(make(funded_user, 'Wont'), Resolution.WONT_DO)
        complete_task(make(funded_user, 'Done'))
        created = post(
            client,
            '/filters',
            auth,
            {
                'name': 'Paused or skipped',
                'params': {'states': ['on_hold'], 'include_closed': True, 'resolution': 'wont_do'},
            },
        ).json()

        ran = numbers(get(client, f'/filters/{created["id"]}/tasks', auth))
        params = created['params']
        listed = numbers(
            get(
                client,
                '/tasks',
                auth,
                include_closed=str(params['include_closed']).lower(),
                resolution=params['resolution'],
                state=','.join(params['states']),
            )
        )

        assert set(ran) == {held.number, wont.number}
        assert set(listed) == set(ran)
        assert created['summary'] == "state on hold · including won't do"


class TestClose:
    def test_an_empty_body_closes_as_done_with_the_open_subtasks(self, client, auth, funded_user):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)

        body = post(client, f'/tasks/{parent.number}/close', auth, {}).json()

        assert body['resolution'] == 'done' and body['closed_at'] is not None
        assert reload(sub).resolution == 'done'

    def test_wont_do(self, client, auth, funded_user):
        task = make(funded_user)

        body = post(client, f'/tasks/{task.number}/close', auth, {'resolution': 'wont_do'}).json()

        assert body['resolution'] == 'wont_do' and body['state'] == 'todo'

    @pytest.mark.parametrize(
        ('choice', 'expected'), [('done', 'done'), ('wont_do', 'wont_do'), ('leave', '')]
    )
    def test_what_happens_to_the_open_subtasks(self, client, auth, funded_user, choice, expected):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)

        post(client, f'/tasks/{parent.number}/close', auth, {'subtasks': choice})

        sub = reload(sub)
        assert sub.resolution == expected
        assert sub.is_open is (choice == 'leave')

    def test_a_note_becomes_a_comment(self, client, auth, funded_user):
        task = make(funded_user)

        response = post(
            client,
            f'/tasks/{task.number}/close',
            auth,
            {'resolution': 'wont_do', 'note': 'Not needed any more'},
        )

        assert response.status_code == 200
        assert [c.body for c in Comment.objects.filter(task=task)] == ['Not needed any more']
        assert response.json()['comment_count'] == 1

    def test_a_note_with_done_is_a_400_and_nothing_happens(self, client, auth, funded_user):
        task = make(funded_user)

        response = post(client, f'/tasks/{task.number}/close', auth, {'note': 'why'})

        assert response.status_code == 400 and 'note' in response.json()['detail']
        assert reload(task).is_open and not Comment.objects.exists()

    def test_an_empty_note_is_no_note(self, client, auth, funded_user):
        task = make(funded_user)

        response = post(client, f'/tasks/{task.number}/close', auth, {'note': '  '})

        assert response.status_code == 200 and not Comment.objects.exists()

    @pytest.mark.parametrize(
        'body',
        [
            {'resolution': 'maybe'},
            {'subtasks': 'all'},
            {'resolution': 'wont_do', 'note': 'x' * 10_001},
        ],
    )
    def test_a_bad_value_is_a_400(self, client, auth, funded_user, body):
        task = make(funded_user)

        response = post(client, f'/tasks/{task.number}/close', auth, body)

        assert response.status_code == 400
        assert reload(task).is_open

    def test_an_unknown_key_is_a_422(self, client, auth, funded_user):
        task = make(funded_user)

        assert post(client, f'/tasks/{task.number}/close', auth, {'done': True}).status_code == 422

    def test_a_task_in_the_trash_is_a_400(self, client, auth, funded_user):
        task = trash_task(make(funded_user))

        response = post(client, f'/tasks/{task.number}/close', auth, {})

        assert response.status_code == 400 and 'Restore' in response.json()['detail']

    def test_an_open_blocker_does_not_stop_it_and_frees_what_it_blocked(
        self, client, auth, funded_user
    ):
        blocker, waiting = make(funded_user, 'Blocker'), make(funded_user, 'Waiting')
        add_blocker(waiting, blocker)
        assert reload(waiting).state == TaskState.BLOCKED

        assert post(client, f'/tasks/{blocker.number}/close', auth, {}).status_code == 200
        assert reload(waiting).state == TaskState.TODO

        late = make(funded_user, 'Late')
        add_blocker(late, make(funded_user, 'Open one'))
        assert (
            post(client, f'/tasks/{late.number}/close', auth, {}).status_code == 200
        )  # asks nothing
        assert TaskBlocker.objects.filter(blocked=late).exists()  # the link is kept

    def test_closing_a_repeating_task_as_wont_do_creates_the_next_one(
        self, client, auth, funded_user, wednesday
    ):
        task = make(funded_user, 'Rent', due_date=date(2026, 10, 28), rrule='FREQ=DAILY')

        post(client, f'/tasks/{task.number}/close', auth, {'resolution': 'wont_do'})

        nxt = Task.objects.get(title='Rent', completed_at__isnull=True)
        assert nxt.due_date == date(2026, 10, 29) and nxt.state == TaskState.TODO

    def test_closing_a_closed_task_changes_nothing_but_adds_the_note(
        self, client, auth, funded_user
    ):
        task = complete_task(make(funded_user))

        response = post(
            client, f'/tasks/{task.number}/close', auth, {'resolution': 'wont_do', 'note': 'late'}
        )

        assert response.status_code == 200
        assert response.json()['resolution'] == 'done'  # not applied: use `resolution`
        assert Comment.objects.filter(task=task, body='late').exists()

    def test_the_edit_budget_is_spent_and_a_refusal_spends_nothing(
        self, client, auth, funded_user, settings, ratelimit_clock
    ):
        task, trashed = make(funded_user), trash_task(make(funded_user, 'Bin'))
        limit_rule(settings, 'TODO_EDIT_USER', 1)

        assert post(client, f'/tasks/{trashed.number}/close', auth, {}).status_code == 400
        assert (
            post(client, f'/tasks/{task.number}/close', auth, {'resolution': 'x'}).status_code
            == 400
        )
        assert post(client, f'/tasks/{task.number}/close', auth, {}).status_code == 200
        assert post(client, f'/tasks/{make(funded_user).number}/close', auth, {}).status_code == 429

    def test_a_note_spends_the_comment_budget_too_and_both_are_looked_at_first(
        self, client, auth, funded_user, settings, ratelimit_clock
    ):
        first, second = make(funded_user, 'A'), make(funded_user, 'B')
        limit_rule(settings, 'TODO_COMMENT_USER', 1)
        limit_rule(settings, 'TODO_EDIT_USER', 5)
        body = {'resolution': 'wont_do', 'note': 'why'}

        assert post(client, f'/tasks/{first.number}/close', auth, body).status_code == 200
        refused = post(client, f'/tasks/{second.number}/close', auth, body)

        assert refused.status_code == 429
        assert reload(second).is_open
        # The refused close spent no edit budget: a close without a note is still allowed.
        assert post(client, f'/tasks/{second.number}/close', auth, {}).status_code == 200


class TestReopen:
    def test_a_closed_task_is_open_and_to_do_again(self, client, auth, funded_user):
        task = close_task(make(funded_user), Resolution.WONT_DO)

        body = post(client, f'/tasks/{task.number}/reopen', auth).json()

        assert body['closed_at'] is None and body['resolution'] is None
        assert body['state'] == 'todo'

    def test_it_is_blocked_while_a_blocker_is_open(self, client, auth, funded_user):
        task = make(funded_user)
        add_blocker(task, make(funded_user, 'Blocker'))
        complete_task(task)

        body = post(client, f'/tasks/{task.number}/reopen', auth).json()

        assert body['state'] == 'blocked'

    def test_reopening_an_open_task_changes_nothing(self, client, auth, funded_user):
        task = make(funded_user, state='on_hold')

        body = post(client, f'/tasks/{task.number}/reopen', auth).json()

        assert body['state'] == 'on_hold'

    def test_a_task_in_the_trash_is_a_400_that_spends_nothing(
        self, client, auth, funded_user, settings, ratelimit_clock
    ):
        trashed = trash_task(complete_task(make(funded_user)))
        limit_rule(settings, 'TODO_EDIT_USER')

        assert post(client, f'/tasks/{trashed.number}/reopen', auth).status_code == 400
        assert post(client, f'/tasks/{make(funded_user).number}/reopen', auth).status_code == 200


class TestResolution:
    def test_change_how_a_task_was_closed(self, client, auth, funded_user):
        task = complete_task(make(funded_user))
        closed_at = reload(task).completed_at

        body = post(client, f'/tasks/{task.number}/resolution', auth, {'resolution': 'wont_do'})

        assert body.status_code == 200 and body.json()['resolution'] == 'wont_do'
        assert reload(task).completed_at == closed_at  # kept

    def test_it_creates_no_occurrence_and_touches_no_subtasks(
        self, client, auth, funded_user, wednesday
    ):
        task = make(funded_user, 'Rent', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(task)
        before = Task.objects.count()

        post(client, f'/tasks/{task.number}/resolution', auth, {'resolution': 'wont_do'})

        assert Task.objects.count() == before

    def test_an_open_task_has_none_to_change(self, client, auth, funded_user):
        task = make(funded_user)

        response = post(client, f'/tasks/{task.number}/resolution', auth, {'resolution': 'done'})

        assert response.status_code == 400
        assert 'Only a closed task' in response.json()['detail']

    @pytest.mark.parametrize('body', [{'resolution': 'maybe'}, {'resolution': ''}])
    def test_a_bad_value_is_a_400(self, client, auth, funded_user, body):
        task = complete_task(make(funded_user))

        assert post(client, f'/tasks/{task.number}/resolution', auth, body).status_code == 400

    def test_the_body_is_required_and_closed(self, client, auth, funded_user):
        task = complete_task(make(funded_user))

        assert post(client, f'/tasks/{task.number}/resolution', auth, {}).status_code == 422
        assert (
            post(
                client, f'/tasks/{task.number}/resolution', auth, {'resolution': 'done', 'x': 1}
            ).status_code
            == 422
        )

    def test_a_task_in_the_trash_is_a_400(self, client, auth, funded_user):
        task = trash_task(complete_task(make(funded_user)))

        assert (
            post(
                client, f'/tasks/{task.number}/resolution', auth, {'resolution': 'done'}
            ).status_code
            == 400
        )


class TestState:
    @pytest.mark.parametrize('state', ['todo', 'in_progress', 'on_hold'])
    def test_set_the_state(self, client, auth, funded_user, state):
        task = make(funded_user, state='in_progress' if state == 'todo' else 'todo')

        response = post(client, f'/tasks/{task.number}/state', auth, {'state': state})

        assert response.status_code == 200 and response.json()['state'] == state
        assert reload(task).state == state

    def test_blocked_is_refused_with_the_services_message(self, client, auth, funded_user):
        task = make(funded_user)

        response = post(client, f'/tasks/{task.number}/state', auth, {'state': 'blocked'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'state: Choose To do, In progress or On hold.'

    def test_a_blocked_task_can_be_given_another_state_and_keeps_its_links(
        self, client, auth, funded_user
    ):
        task = make(funded_user)
        add_blocker(task, make(funded_user, 'Blocker'))

        body = post(client, f'/tasks/{task.number}/state', auth, {'state': 'in_progress'}).json()

        assert body['state'] == 'in_progress' and len(body['blocked_by']) == 1

    def test_an_unknown_state_is_a_400_and_a_missing_one_a_422(self, client, auth, funded_user):
        task = make(funded_user)

        assert post(client, f'/tasks/{task.number}/state', auth, {'state': 'x'}).status_code == 400
        assert post(client, f'/tasks/{task.number}/state', auth, {}).status_code == 422

    def test_a_closed_task_must_be_reopened_first(self, client, auth, funded_user):
        task = complete_task(make(funded_user))

        response = post(client, f'/tasks/{task.number}/state', auth, {'state': 'on_hold'})

        assert response.status_code == 400 and 'Reopen' in response.json()['detail']

    def test_a_task_in_the_trash_is_a_400(self, client, auth, funded_user):
        task = trash_task(make(funded_user))

        assert (
            post(client, f'/tasks/{task.number}/state', auth, {'state': 'on_hold'}).status_code
            == 400
        )

    def test_nothing_propagates_between_a_parent_and_its_subtasks(self, client, auth, funded_user):
        parent = make(funded_user, 'P')
        sub = make(funded_user, 'S', parent=parent)

        post(client, f'/tasks/{parent.number}/state', auth, {'state': 'on_hold'})

        assert reload(sub).state == TaskState.TODO

    def test_a_refusal_spends_no_budget(self, client, auth, funded_user, settings, ratelimit_clock):
        task = make(funded_user)
        limit_rule(settings, 'TODO_EDIT_USER')

        assert (
            post(client, f'/tasks/{task.number}/state', auth, {'state': 'blocked'}).status_code
            == 400
        )
        assert (
            post(client, f'/tasks/{task.number}/state', auth, {'state': 'on_hold'}).status_code
            == 200
        )
        assert (
            post(client, f'/tasks/{task.number}/state', auth, {'state': 'todo'}).status_code == 429
        )


class TestCreateAndPatch:
    def test_create_with_a_state(self, client, auth):
        response = post(client, '/tasks', auth, {'title': 'x', 'state': 'in_progress'})

        assert response.status_code == 201 and response.json()['state'] == 'in_progress'

    def test_create_blocked_is_a_400_and_charges_nothing(self, client, auth, funded_user):
        response = post(client, '/tasks', auth, {'title': 'x', 'state': 'blocked'})

        assert response.status_code == 400
        assert response.json()['detail'] == 'state: Choose To do, In progress or On hold.'
        assert not Task.objects.exists()

    def test_create_with_blockers(self, client, auth, funded_user):
        blocker = make(funded_user, 'Blocker')
        done = complete_task(make(funded_user, 'Done'))

        response = post(
            client,
            '/tasks',
            auth,
            {'title': 'x', 'state': 'on_hold', 'blocked_by': [blocker.number, done.number]},
        )

        assert response.status_code == 201
        body = response.json()
        assert body['state'] == 'blocked'  # an open blocker wins over the state asked for
        assert [ref['number'] for ref in body['blocked_by']] == [blocker.number, done.number]
        assert (
            get(client, f'/tasks/{blocker.number}', auth).json()['blocks'][0]['number']
            == body['number']
        )

    def test_create_with_only_a_closed_blocker_keeps_the_state(self, client, auth, funded_user):
        done = complete_task(make(funded_user, 'Done'))

        body = post(client, '/tasks', auth, {'title': 'x', 'blocked_by': [done.number]}).json()

        assert body['state'] == 'todo' and len(body['blocked_by']) == 1

    @pytest.mark.parametrize(
        ('blocked_by', 'message'),
        [([1], 'blocked_by: There is no task #1.'), ([2000], 'There is no task #2000.')],
    )
    def test_an_unknown_blocker_is_a_400_with_nothing_created(
        self, client, auth, funded_user, blocked_by, message
    ):
        response = post(client, '/tasks', auth, {'title': 'x', 'blocked_by': blocked_by})

        assert response.status_code == 400 and message in response.json()['detail']
        assert not Task.objects.exists()

    def test_another_users_task_cannot_block_and_looks_like_a_missing_one(
        self, client, auth, other_user
    ):
        theirs = make(other_user, 'Theirs')

        response = post(client, '/tasks', auth, {'title': 'x', 'blocked_by': [theirs.number]})

        assert response.status_code == 400
        assert response.json()['detail'] == f'blocked_by: There is no task #{theirs.number}.'

    def test_a_trashed_blocker_is_a_400(self, client, auth, funded_user):
        gone = trash_task(make(funded_user, 'Gone'))

        response = post(client, '/tasks', auth, {'title': 'x', 'blocked_by': [gone.number]})

        assert response.status_code == 400 and 'trash' in response.json()['detail']

    def test_more_than_twenty_blockers_is_a_422(self, client, auth, funded_user):
        response = post(
            client, '/tasks', auth, {'title': 'x', 'blocked_by': list(range(1000, 1021))}
        )

        assert response.status_code == 422

    def test_patch_the_state(self, client, auth, funded_user):
        task = make(funded_user)

        body = patch(
            client, f'/tasks/{task.number}', auth, {'state': 'on_hold', 'title': 'New'}
        ).json()

        assert body['state'] == 'on_hold' and body['title'] == 'New'

    def test_patch_state_null_and_blocked_and_closed_are_400s(self, client, auth, funded_user):
        task, closed = make(funded_user), complete_task(make(funded_user, 'C'))

        assert patch(client, f'/tasks/{task.number}', auth, {'state': None}).status_code == 400
        assert patch(client, f'/tasks/{task.number}', auth, {'state': 'blocked'}).status_code == 400
        assert (
            patch(client, f'/tasks/{closed.number}', auth, {'state': 'on_hold'}).status_code == 400
        )

    def test_patch_blocked_by_replaces_the_set(self, client, auth, funded_user):
        task = make(funded_user)
        a, b, c = (make(funded_user, name) for name in 'ABC')
        add_blocker(task, a)
        add_blocker(task, b)

        body = patch(
            client, f'/tasks/{task.number}', auth, {'blocked_by': [b.number, c.number]}
        ).json()

        assert [ref['number'] for ref in body['blocked_by']] == [b.number, c.number]
        assert body['state'] == 'blocked'
        assert not TaskBlocker.objects.filter(blocked=task, blocker=a).exists()

    def test_patch_blocked_by_null_or_empty_removes_every_blocker(self, client, auth, funded_user):
        for value in (None, []):
            task = make(funded_user)
            add_blocker(task, make(funded_user, 'Blocker'))

            body = patch(client, f'/tasks/{task.number}', auth, {'blocked_by': value}).json()

            assert body['blocked_by'] == [] and body['state'] == 'todo'

    def test_removing_the_blockers_leaves_an_on_hold_task_on_hold(self, client, auth, funded_user):
        task = make(funded_user, state='on_hold')
        add_blocker(task, make(funded_user, 'Blocker'))
        set_state(task, TaskState.ON_HOLD)

        body = patch(client, f'/tasks/{task.number}', auth, {'blocked_by': []}).json()

        assert body['state'] == 'on_hold'

    def test_patching_the_same_blockers_changes_nothing(self, client, auth, funded_user):
        task, blocker = make(funded_user), make(funded_user, 'Blocker')
        add_blocker(task, blocker)

        response = patch(client, f'/tasks/{task.number}', auth, {'blocked_by': [blocker.number]})

        assert response.status_code == 200 and len(response.json()['blocked_by']) == 1

    def test_a_refusal_changes_nothing_and_spends_nothing(
        self, client, auth, funded_user, settings, ratelimit_clock
    ):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        add_blocker(blocker, task)  # so `blocker` blocking `task` would be a cycle
        limit_rule(settings, 'TODO_EDIT_USER')

        response = patch(
            client,
            f'/tasks/{task.number}',
            auth,
            {'title': 'Changed', 'priority': 1, 'blocked_by': [blocker.number]},
        )

        assert (
            response.status_code == 400
            and 'directly or through other tasks' in response.json()['detail']
        )
        task = reload(task)
        assert task.title == 'Task' and task.priority == 0
        assert patch(client, f'/tasks/{task.number}', auth, {'title': 'Fine'}).status_code == 200

    def test_a_late_refusal_rolls_the_whole_patch_back(
        self, client, auth, funded_user, monkeypatch
    ):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')

        def boom(*args, **kwargs):
            from django.core.exceptions import ValidationError

            raise ValidationError('Refused late.')

        monkeypatch.setattr('apps.todo.services.blockers.add_blocker', boom)

        response = patch(
            client,
            f'/tasks/{task.number}',
            auth,
            {'title': 'Changed', 'state': 'on_hold', 'blocked_by': [blocker.number]},
        )

        assert response.status_code == 400
        task = reload(task)
        assert task.title == 'Task' and task.state == TaskState.TODO

    @pytest.mark.parametrize(
        ('blocked_by', 'fragment'),
        [
            ([999], 'There is no task #999.'),
            (['self'], None),
        ],
    )
    def test_unknown_blockers_are_a_400(self, client, auth, funded_user, blocked_by, fragment):
        task = make(funded_user)
        if fragment is None:
            blocked_by = [task.number]
            fragment = 'cannot block itself'

        response = patch(client, f'/tasks/{task.number}', auth, {'blocked_by': blocked_by})

        assert response.status_code == 400 and fragment in response.json()['detail']

    def test_a_trashed_task_must_be_restored_before_its_blockers_change(
        self, client, auth, funded_user
    ):
        task, blocker = trash_task(make(funded_user)), make(funded_user, 'B')

        response = patch(client, f'/tasks/{task.number}', auth, {'blocked_by': [blocker.number]})

        assert response.status_code == 400

    def test_a_closed_task_can_be_given_blockers(self, client, auth, funded_user):
        task, blocker = complete_task(make(funded_user)), make(funded_user, 'B')

        body = patch(client, f'/tasks/{task.number}', auth, {'blocked_by': [blocker.number]}).json()

        assert body['state'] == 'todo' and len(body['blocked_by']) == 1  # a link only
        assert body['closed_at'] is not None

    def test_the_other_users_get_404s(self, client, auth, other_user):
        theirs = make(other_user)

        assert (
            patch(client, f'/tasks/{theirs.number}', auth, {'state': 'on_hold'}).status_code == 404
        )
        assert patch(client, f'/tasks/{theirs.number}', auth, {'blocked_by': []}).status_code == 404


class TestBlockerEndpoints:
    def test_add_and_remove(self, client, auth, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')

        added = post(client, f'/tasks/{task.number}/blockers', auth, {'number': blocker.number})

        assert added.status_code == 200
        body = added.json()
        assert body['state'] == 'blocked' and body['number'] == task.number
        assert [ref['number'] for ref in body['blocked_by']] == [blocker.number]

        removed = delete(client, f'/tasks/{task.number}/blockers/{blocker.number}', auth)

        assert removed.status_code == 204
        assert reload(task).state == TaskState.TODO
        assert not TaskBlocker.objects.exists()

    def test_adding_twice_and_removing_twice_are_harmless(self, client, auth, funded_user):
        task, blocker = make(funded_user, 'Task'), make(funded_user, 'Blocker')
        path = f'/tasks/{task.number}/blockers'

        assert post(client, path, auth, {'number': blocker.number}).status_code == 200
        assert post(client, path, auth, {'number': blocker.number}).status_code == 200
        assert TaskBlocker.objects.count() == 1
        assert delete(client, f'{path}/{blocker.number}', auth).status_code == 204
        assert delete(client, f'{path}/{blocker.number}', auth).status_code == 204

    def test_removing_a_number_that_is_not_there_is_204_for_any_number(
        self, client, auth, funded_user, other_user
    ):
        task = make(funded_user)
        theirs = make_task(other_user, number=1500)

        assert delete(client, f'/tasks/{task.number}/blockers/999999', auth).status_code == 204
        assert (
            delete(client, f'/tasks/{task.number}/blockers/{theirs.number}', auth).status_code
            == 204
        )

    @pytest.mark.parametrize(
        ('number', 'message'),
        [
            (999999, 'number: There is no task #999999.'),
            ('self', 'A task cannot block itself.'),
        ],
    )
    def test_refusals_are_400s(self, client, auth, funded_user, number, message):
        task = make(funded_user)
        number = task.number if number == 'self' else number

        response = post(client, f'/tasks/{task.number}/blockers', auth, {'number': number})

        assert response.status_code == 400 and message in response.json()['detail']

    def test_another_users_task_is_no_task(self, client, auth, funded_user, other_user):
        task = make(funded_user)
        theirs = make_task(other_user, number=1500)  # a number we don't have

        response = post(client, f'/tasks/{task.number}/blockers', auth, {'number': theirs.number})

        assert response.status_code == 400
        assert response.json()['detail'] == f'number: There is no task #{theirs.number}.'

    def test_a_cycle_is_refused_however_long(self, client, auth, funded_user):
        a, b, c = (make(funded_user, name) for name in 'ABC')
        assert (
            post(client, f'/tasks/{a.number}/blockers', auth, {'number': b.number}).status_code
            == 200
        )
        assert (
            post(client, f'/tasks/{b.number}/blockers', auth, {'number': c.number}).status_code
            == 200
        )

        response = post(client, f'/tasks/{c.number}/blockers', auth, {'number': a.number})

        assert response.status_code == 400
        assert 'directly or through other tasks' in response.json()['detail']

    def test_a_trashed_blocker_is_refused(self, client, auth, funded_user):
        task, gone = make(funded_user), trash_task(make(funded_user, 'Gone'))

        response = post(client, f'/tasks/{task.number}/blockers', auth, {'number': gone.number})

        assert response.status_code == 400 and 'trash' in response.json()['detail']

    def test_the_cap(self, client, auth, funded_user, config):
        config.task_price = 0
        config.save()
        task = make(funded_user)
        for _ in range(20):
            assert (
                post(
                    client,
                    f'/tasks/{task.number}/blockers',
                    auth,
                    {'number': make(funded_user).number},
                ).status_code
                == 200
            )

        response = post(
            client, f'/tasks/{task.number}/blockers', auth, {'number': make(funded_user).number}
        )

        assert response.status_code == 400 and 'at most 20 blockers' in response.json()['detail']

    def test_a_task_in_the_trash_takes_no_changes(self, client, auth, funded_user):
        task, blocker = make(funded_user), make(funded_user, 'B')
        add_blocker(task, blocker)
        trash_task(task)

        assert (
            post(
                client, f'/tasks/{task.number}/blockers', auth, {'number': make(funded_user).number}
            ).status_code
            == 400
        )
        assert (
            delete(client, f'/tasks/{task.number}/blockers/{blocker.number}', auth).status_code
            == 400
        )

    def test_another_users_task_is_a_404(self, client, auth, other_user):
        theirs = make(other_user)

        assert (
            post(client, f'/tasks/{theirs.number}/blockers', auth, {'number': 1}).status_code == 404
        )
        assert delete(client, f'/tasks/{theirs.number}/blockers/1', auth).status_code == 404

    def test_the_body_is_closed(self, client, auth, funded_user):
        task = make(funded_user)

        assert post(client, f'/tasks/{task.number}/blockers', auth, {}).status_code == 422
        assert (
            post(client, f'/tasks/{task.number}/blockers', auth, {'number': 1, 'x': 1}).status_code
            == 422
        )

    def test_the_edit_budget_is_spent_only_by_a_real_change(
        self, client, auth, funded_user, settings, ratelimit_clock
    ):
        task, blocker, other = (make(funded_user, name) for name in ('T', 'B', 'O'))
        limit_rule(settings, 'TODO_EDIT_USER')
        path = f'/tasks/{task.number}/blockers'

        assert post(client, path, auth, {'number': 999999}).status_code == 400  # a refusal: free
        assert post(client, path, auth, {'number': blocker.number}).status_code == 200
        assert (
            post(client, path, auth, {'number': blocker.number}).status_code == 200
        )  # no-op: free
        assert delete(client, f'{path}/{other.number}', auth).status_code == 204  # no link: free
        assert post(client, path, auth, {'number': other.number}).status_code == 429

    def test_closing_the_blocker_frees_the_task(self, client, auth, funded_user):
        task, blocker = make(funded_user, 'T'), make(funded_user, 'B')
        post(client, f'/tasks/{task.number}/blockers', auth, {'number': blocker.number})

        post(client, f'/tasks/{blocker.number}/close', auth, {})

        body = get(client, f'/tasks/{task.number}', auth).json()
        assert body['state'] == 'todo'
        assert body['blocked_by'] == [
            {'number': blocker.number, 'title': 'B', 'open': False, 'trashed': False}
        ]


@pytest.mark.parametrize(
    ('method', 'path', 'payload'),
    [
        ('post', '/tasks/1000/close', {}),
        ('post', '/tasks/1000/reopen', None),
        ('post', '/tasks/1000/resolution', {'resolution': 'done'}),
        ('post', '/tasks/1000/state', {'state': 'todo'}),
        ('post', '/tasks/1000/blockers', {'number': 1001}),
        ('delete', '/tasks/1000/blockers/1001', None),
        ('get', '/tasks/1000/activity', None),
    ],
)
def test_the_new_endpoints_need_a_token(client, method, path, payload):
    call = getattr(client, method)
    kwargs = {'data': payload or {}, 'content_type': 'application/json'} if method == 'post' else {}

    assert call(f'/api/todo{path}', **kwargs).status_code == 401


def test_every_field_of_the_close_body_is_optional(client, auth, funded_user):
    task = make(funded_user)

    assert post(client, f'/tasks/{task.number}/close', auth, {}).json()['resolution'] == 'done'
