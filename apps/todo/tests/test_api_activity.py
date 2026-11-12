"""The To-do JWT API's activity log (`GET /api/todo/tasks/{number}/activity`, issue #275): the page
shape, paging with a cursor, the public wording of every sentence whatever the account's date and
time format, trashed and foreign tasks, and the channel the API's own changes are recorded under."""

import base64
import re
from datetime import date, time

import pytest

from ..models import Task, TaskEvent
from ..services import complete_task, create_task, trash_task, update_task
from .api_support import BASE, auth, get, patch, post  # noqa: F401 -- `auth` is a fixture
from .factories import make_task

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PATH = '/tasks/{}/activity'


def encoded(raw: str) -> str:
    """A cursor payload as base64url without padding, the format the API issues."""
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')


def make(user, title='T', **fields) -> Task:
    return create_task(user, title=title, **fields)


def many_edits(task: Task, count: int) -> None:
    for n in range(count):
        update_task(task, changes={'title': f'Title {n}'})


class TestPage:
    def test_newest_first_with_a_sentence_and_the_data_of_each_entry(
        self, client, auth, funded_user
    ):
        task = make(funded_user, 'Old')
        update_task(task, changes={'title': 'New'})

        body = get(client, PATH.format(task.number), auth).json()

        assert set(body) == {'results', 'next'} and body['next'] is None
        assert [entry['kind'] for entry in body['results']] == ['title', 'created']
        title, created = body['results']
        assert set(title) == {'id', 'kind', 'text', 'data', 'automatic', 'channel', 'created_at'}
        assert title['text'] == 'Title changed from "Old" to "New"'
        assert title['data'] == {'from': 'Old', 'to': 'New'}
        assert title['automatic'] is False and title['channel'] == 'system'  # not via a request
        assert created['text'] and created['data']['source'] == 'web'

    def test_paging_with_the_cursor_repeats_and_skips_nothing(self, client, auth, funded_user):
        task = make(funded_user)
        many_edits(task, 6)  # seven entries with `created`
        path = PATH.format(task.number)

        first = get(client, path, auth, limit=3).json()
        second = get(client, path, auth, limit=3, before=first['next']).json()
        third = get(client, path, auth, limit=3, before=second['next']).json()

        assert len(first['results']) == len(second['results']) == 3 and len(third['results']) == 1
        assert first['next'] and second['next'] and third['next'] is None
        ids = [entry['id'] for page in (first, second, third) for entry in page['results']]
        assert len(set(ids)) == 7
        assert ids == [
            str(pk)
            for pk in TaskEvent.objects.filter(task=task)
            .order_by('-created_at', '-id')
            .values_list('pk', flat=True)
        ]

    def test_entries_written_while_paging_never_repeat_or_skip_one(self, client, auth, funded_user):
        task = make(funded_user)
        many_edits(task, 4)
        path = PATH.format(task.number)
        first = get(client, path, auth, limit=2).json()

        update_task(task, changes={'title': 'Written meanwhile'})
        second = get(client, path, auth, limit=2, before=first['next']).json()

        shown = [entry['id'] for entry in first['results'] + second['results']]
        assert len(set(shown)) == 4
        assert 'Written meanwhile' not in [e['data'].get('to') for e in second['results']]

    def test_the_default_page_is_20_and_the_limit_is_clamped(self, client, auth, funded_user):
        task = make(funded_user)
        many_edits(task, 24)  # 25 entries
        path = PATH.format(task.number)

        default = get(client, path, auth).json()
        zero = get(client, path, auth, limit=0).json()
        huge = get(client, path, auth, limit=1000).json()

        assert len(default['results']) == 20 and default['next']
        assert len(zero['results']) == 1  # at least one
        assert len(huge['results']) == 25 and huge['next'] is None

    @pytest.mark.parametrize(
        'cursor',
        [
            'junk',
            '!!!',
            encoded('2026-01-01T00:00:00+00:00_x'),
            encoded('2026-01-01T00:00:00+00:00_'),
            encoded('not a time_00000000-0000-0000-0000-000000000000'),
            # the format before the cursor was URL-safe, with its `+` read as a space
            '2026-01-01T00:00:00 00:00_00000000-0000-0000-0000-000000000000',
            '2026-01-01T00:00:00+00:00_00000000-0000-0000-0000-000000000000',
        ],
    )
    def test_a_malformed_cursor_is_a_400(self, client, auth, funded_user, cursor):
        task = make(funded_user)

        response = get(client, PATH.format(task.number), auth, before=cursor)

        assert response.status_code == 400
        assert response.json() == {'detail': 'before: Not a valid cursor.'}

    def test_the_cursor_is_url_safe_and_works_unescaped_in_the_query_string(
        self, client, auth, funded_user
    ):
        task = make(funded_user)
        many_edits(task, 5)
        path = PATH.format(task.number)
        first = get(client, path, auth, limit=2).json()

        assert re.fullmatch(r'[A-Za-z0-9_-]+', first['next'])  # no `+`, `:`, `=` or `/`
        # put into the URL exactly as received, without percent-encoding
        second = client.get(f'{BASE}{path}?limit=2&before={first["next"]}', **auth)
        assert second.status_code == 200
        assert second.json()['results'][0]['id'] != first['results'][0]['id']

    def test_a_cursor_without_a_time_zone_is_a_400(self, client, auth, funded_user):
        task = make(funded_user)
        event = TaskEvent.objects.get(task=task)

        response = get(
            client,
            PATH.format(task.number),
            auth,
            before=encoded(f'2026-01-01T00:00:00_{event.pk}'),
        )

        assert response.status_code == 400

    def test_a_malformed_limit_is_a_422(self, client, auth, funded_user):
        task = make(funded_user)

        assert get(client, PATH.format(task.number), auth, limit='many').status_code == 422


class TestWording:
    def test_the_text_is_the_public_wording_whatever_the_account_uses(
        self, client, auth, funded_user
    ):
        funded_user.date_format = 'dmy'
        funded_user.time_format = '24h'
        funded_user.save()
        task = make(funded_user)
        update_task(task, changes={'due_date': date(2026, 11, 5), 'due_time': time(15, 0)})

        body = get(client, PATH.format(task.number), auth).json()

        due = next(entry for entry in body['results'] if entry['kind'] == 'due')
        assert 'Nov 5, 2026, 3:00 PM' in due['text']
        assert '/' not in due['text'] and '15:00' not in due['text']

    def test_a_blocker_and_a_close_read_as_sentences(self, client, auth, funded_user):
        task, blocker = make(funded_user, 'Main'), make(funded_user, 'Blocker')
        post(client, f'/tasks/{task.number}/blockers', auth, {'number': blocker.number})
        post(client, f'/tasks/{task.number}/close', auth, {'resolution': 'wont_do'})

        body = get(client, PATH.format(task.number), auth).json()

        by_kind = {entry['kind']: entry for entry in body['results']}
        assert 'Blocker' in by_kind['blocker_added']['text']
        assert by_kind['blocked']['automatic'] is True
        assert by_kind['closed']['data']['resolution'] == 'wont_do'
        assert "won't do" in by_kind['closed']['text'].lower()


class TestAccess:
    def test_the_apis_own_changes_are_recorded_with_the_api_channel(
        self, client, auth, funded_user
    ):
        task = make(funded_user)
        patch(client, f'/tasks/{task.number}', auth, {'priority': 1})
        post(client, f'/tasks/{task.number}/state', auth, {'state': 'on_hold'})

        body = get(client, PATH.format(task.number), auth).json()

        by_kind = {entry['kind']: entry for entry in body['results']}
        assert by_kind['priority']['channel'] == 'api' and by_kind['state']['channel'] == 'api'
        assert by_kind['state']['data'] == {'from': 'todo', 'to': 'on_hold'}

    def test_a_task_in_the_trash_still_has_its_log(self, client, auth, funded_user):
        task = trash_task(complete_task(make(funded_user)))

        body = get(client, PATH.format(task.number), auth).json()

        assert [entry['kind'] for entry in body['results']][:2] == ['trashed', 'closed']

    def test_another_users_task_is_a_404(self, client, auth, other_user):
        theirs = make_task(other_user, number=1500)

        assert get(client, PATH.format(theirs.number), auth).status_code == 404
        assert get(client, PATH.format(999999), auth).status_code == 404

    def test_it_spends_no_rate_limit(self, client, auth, funded_user, settings):
        from .api_support import limit_rule

        task = make(funded_user)
        limit_rule(settings, 'TODO_EDIT_USER')
        limit_rule(settings, 'TODO_SEARCH_USER')

        for _ in range(5):
            assert get(client, PATH.format(task.number), auth).status_code == 200

    def test_a_small_page_costs_a_fixed_number_of_queries(
        self, client, auth, funded_user, django_assert_max_num_queries
    ):
        task = make(funded_user)
        many_edits(task, 15)

        with django_assert_max_num_queries(4):  # auth, the task, the page: never one per entry
            assert (
                len(get(client, PATH.format(task.number), auth, limit=16).json()['results']) == 16
            )
