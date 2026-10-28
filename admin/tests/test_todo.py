"""Unit tests for `admin/todo.py` (the To-do JWT API CLI, issue #187): argument parsing, the request
each command sends, the upload loop and the download's `.part` rename, with every network call
mocked. Like `test_receipts.py` and `test_shorturl.py`, this module never touches Django or a
database -- the CLI is a plain HTTP client over `/api/todo/`. The shared plumbing (`api_request`,
the token files) is tested in `test_cli_utils.py`.
"""

import base64
import hashlib
import json

import pytest
from typer.testing import CliRunner

from admin import todo

pytestmark = pytest.mark.unit

runner = CliRunner()

BASE = 'http://localhost:8000/api/todo'
HOME_ID = '11111111-1111-1111-1111-111111111111'
WORK_ID = '22222222-2222-2222-2222-222222222222'
REMINDER_ID = '33333333-3333-3333-3333-333333333333'
ATTACHMENT_ID = '44444444-4444-4444-4444-444444444444'
COMMENT_ID = '55555555-5555-5555-5555-555555555555'
FILTER_ID = '66666666-6666-6666-6666-666666666666'

HOME = {
    'id': HOME_ID,
    'name': 'Home',
    'color': '#336699',
    'description': 'Around the house',
    'position': 0,
    'task_count': 3,
}
WORK = {**HOME, 'id': WORK_ID, 'name': 'Work', 'color': '#993333', 'task_count': 0}

TASK: dict = {
    'id': '77777777-7777-7777-7777-777777777777',
    'number': 1234,
    'title': 'Pay rent',
    'description': 'Send **it** to #1250',
    'description_html': '<p>Send <strong>it</strong></p>',
    'priority': 1,
    'due_date': '2026-11-01',
    'due_time': '09:30:00',
    'due_at': '2026-11-01T09:30:00Z',
    'completed_at': None,
    'deleted_at': None,
    'position': 0,
    'source': 'api',
    'parent': None,
    'labels': [{'id': HOME_ID, 'name': 'Home', 'color': '#336699'}],
    'recurrence': {
        'rrule': 'FREQ=MONTHLY;BYMONTHDAY=1',
        'rrule_text': 'Every month on the 1st',
        'mode': 'fixed',
        'anchor_date': '2026-11-01',
        'ended_at': None,
    },
    'reminders': [
        {
            'id': REMINDER_ID,
            'minutes_before': 5,
            'channel': 'in_app',
            'fire_at': '2026-11-01T09:25:00Z',
            'sent_at': None,
        }
    ],
    'subtask_count': 2,
    'open_subtask_count': 1,
    'comment_count': 1,
    'attachment_count': 1,
    'charged_credits': 0,
    'unpaid': False,
    'created_at': '2026-10-10T09:00:00Z',
    'updated_at': '2026-10-10T09:00:00Z',
}
DETAIL = {
    **TASK,
    'attachments': [
        {
            'id': ATTACHMENT_ID,
            'task': 1234,
            'filename': 'contract.pdf',
            'size_bytes': 2048,
            'declared_type': 'application/pdf',
            'inline_type': 'application/pdf',
            'created_at': '2026-10-10T09:00:00Z',
        }
    ],
    'referenced_by': [
        {'number': 1300, 'title': 'Budget', 'comment': None},
        {'number': 1301, 'title': 'Taxes', 'comment': COMMENT_ID},
    ],
}
COMMENT = {
    'id': COMMENT_ID,
    'task': 1234,
    'body': 'asked the landlord',
    'body_html': '<p>asked the landlord</p>',
    'created_at': '2026-10-11T09:00:00Z',
    'edited_at': None,
    'deleted_at': None,
    'revision_count': 0,
}


def page(*tasks, count=None, limit=50, offset=0) -> dict:
    return {
        'count': len(tasks) if count is None else count,
        'limit': limit,
        'offset': offset,
        'results': list(tasks),
    }


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=None, content=b''):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.text = text if text is not None else json.dumps(self._json)
        self._content = content
        self.closed = False
        # Raised from `iter_content` after the first chunk: a connection dropped mid-download.
        self.fail_mid_stream: Exception | None = None

    def close(self):
        self.closed = True

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start : start + chunk_size]
            if self.fail_mid_stream:
                raise self.fail_mid_stream


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda *args, **kwargs: None)


@pytest.fixture
def token_file(token_dir):
    path = token_dir / '.todo_token.local'
    path.write_text('tok')
    return path


@pytest.fixture
def logged_out(token_file):
    token_file.unlink()
    return token_file


class Api:
    """Records every request and answers from a table of `(method, path) -> response`. A path is
    relative to `/api` (`/todo/tasks`), or the whole URL for a call to somewhere else (storage)."""

    def __init__(self, monkeypatch):
        self.calls: list[dict] = []
        self.routes: dict[tuple[str, str], FakeResponse | list[FakeResponse]] = {}
        for method in ('get', 'post', 'patch', 'delete', 'put'):
            monkeypatch.setattr(f'requests.{method}', self._handler(method))

    def _handler(self, method):
        def handle(url, **kwargs):
            self.calls.append({'method': method, 'url': url, **kwargs})
            path = url.removeprefix('http://localhost:8000/api')
            try:
                response = self.routes[(method, path)]
            except KeyError:
                raise AssertionError(f'unexpected {method.upper()} {path}') from None
            if isinstance(response, list):
                return response.pop(0)
            return response

        return handle

    def on(self, method, path, response):
        self.routes[(method, path)] = response
        return self

    def requests(self, method, path=None):
        return [
            c
            for c in self.calls
            if c['method'] == method and (path is None or c['url'].endswith(path))
        ]


@pytest.fixture
def api(monkeypatch, token_file):
    return Api(monkeypatch)


def flat(result) -> str:
    """The output with the logger's line wrapping undone."""
    return ' '.join(result.output.split())


def invoke(*args, input=None):
    return runner.invoke(todo.app, [*args], input=input)


# -- login --


def test_login_stores_the_token_for_the_environment(logged_out, api, token_dir):
    api.on('post', '/auth/login', FakeResponse(200, {'access_token': 'tok123'}))

    result = invoke('login', 'local', 'me@example.com', 'pw')

    assert result.exit_code == 0
    assert (token_dir / '.todo_token.local').read_text() == 'tok123'
    assert api.calls[0]['json'] == {'email': 'me@example.com', 'password': 'pw'}


def test_login_reads_the_password_from_the_environment(logged_out, api, monkeypatch):
    api.on('post', '/auth/login', FakeResponse(200, {'access_token': 't'}))
    monkeypatch.setenv('TODO_PASSWORD', 'env-pw')

    assert invoke('login', 'local', 'me@example.com').exit_code == 0
    assert api.calls[0]['json']['password'] == 'env-pw'


def test_login_prompts_when_there_is_no_password(logged_out, api, monkeypatch):
    api.on('post', '/auth/login', FakeResponse(200, {'access_token': 't'}))
    monkeypatch.setattr('typer.prompt', lambda *a, **k: 'typed-pw')

    assert invoke('login', 'local', 'me@example.com').exit_code == 0
    assert api.calls[0]['json']['password'] == 'typed-pw'


def test_a_failed_login_says_why_and_writes_no_token(logged_out, api):
    api.on('post', '/auth/login', FakeResponse(401, {'detail': 'Invalid credentials'}))

    result = invoke('login', 'local', 'me@example.com', 'bad')

    assert result.exit_code == 1
    assert 'Invalid credentials' in result.output
    assert 'run `login` again' not in result.output
    assert not logged_out.exists()


def test_every_command_but_login_needs_a_token(logged_out, api):
    for args in (['list', 'local'], ['get', 'local', '1234'], ['add', 'local', 'x']):
        assert invoke(*args).exit_code == 1
    assert api.calls == []


def test_a_token_is_per_cli_and_environment(token_file, token_dir, api):
    (token_dir / '.todo_token.prod').write_text('prod-token')
    api.on('get', '/todo/tasks', FakeResponse(200, page()))

    assert invoke('list', 'local').exit_code == 0
    assert api.calls[-1]['headers'] == {'Authorization': 'Bearer tok'}
    assert invoke('list', 'prod').exit_code == 0
    assert api.calls[-1]['headers'] == {'Authorization': 'Bearer prod-token'}


# -- list --


def test_list_shows_a_table(api):
    done = {**TASK, 'number': 1235, 'title': 'Old [bold]one[/bold]', 'completed_at': 'x'}
    done.update(priority=0, due_date=None, due_time=None, recurrence=None, reminders=[], labels=[])
    api.on('get', '/todo/tasks', FakeResponse(200, page(TASK, done)))

    result = invoke('list', 'local')

    assert result.exit_code == 0
    assert api.calls[0]['params'] == {'view': 'all', 'limit': 50, 'offset': 0}
    lines = result.output.splitlines()
    first = next(line for line in lines if 'Pay rent' in line)
    assert '1234' in first and '2026-11-01 09:30' in first and '!1' in first and 'Home' in first
    assert todo.REPEAT in first and todo.BELL in first
    second = next(line for line in lines if 'Old' in line)
    assert '1235' in second and 'Old [bold]one[/bold]' in second  # no markup is interpreted
    assert todo.REPEAT not in second and todo.BELL not in second


def test_list_with_nothing_found(api):
    api.on('get', '/todo/tasks', FakeResponse(200, page()))

    result = invoke('list', 'local', '--view', 'today')

    assert 'No tasks found.' in result.output
    assert api.calls[0]['params']['view'] == 'today'


def test_list_sends_every_filter(api):
    api.on('get', '/todo/labels', FakeResponse(200, [HOME, WORK]))
    api.on('get', '/todo/tasks', FakeResponse(200, page()))

    result = invoke(
        'list',
        'local',
        '--view',
        'upcoming',
        '--q',
        'rent*',
        '--regex',
        '--include-completed',
        '--priority',
        '1,2',
        '--label',
        'home',
        '--label',
        'none',
        '--from',
        '2026-11-01',
        '--to',
        '2026-11-30',
        '--sort',
        'priority',
        '--limit',
        '10',
        '--offset',
        '20',
    )

    assert result.exit_code == 0
    assert api.requests('get', '/tasks')[-1]['params'] == {
        'view': 'upcoming',
        'q': 'rent*',
        'mode': 'regex',
        'include_completed': 'true',
        'label': f'{HOME_ID},none',
        'priority': '1,2',
        'date_from': '2026-11-01',
        'date_to': '2026-11-30',
        'sort': 'priority',
        'limit': 10,
        'offset': 20,
    }


def test_list_says_how_to_see_more(api):
    api.on('get', '/todo/tasks', FakeResponse(200, page(TASK, count=120)))

    result = invoke('list', 'local')

    assert 'Showing 1-1 of 120 tasks. Use --offset 1 to see more.' in result.output


def test_list_with_an_unknown_label_stops(api):
    api.on('get', '/todo/labels', FakeResponse(200, [HOME]))

    result = invoke('list', 'local', '--label', 'nope')

    assert result.exit_code == 1 and 'No label called "nope"' in result.output
    assert api.requests('get', '/tasks') == []


def test_a_search_the_server_refuses_is_shown(api):
    api.on('get', '/todo/tasks', FakeResponse(400, {'detail': 'Invalid regular expression.'}))

    result = invoke('list', 'local', '--q', '/(/')

    assert result.exit_code == 1 and '400: Invalid regular expression.' in result.output


# -- add --


def test_add_reads_the_title_by_default(api):
    created = {**TASK, 'parsed': {'applied': True, 'label': 'Sun 1 Nov, 09:30', 'reason': ''}}
    api.on('post', '/todo/tasks', FakeResponse(201, created))

    result = invoke('add', 'local', 'pay rent every month on the 1st')

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {
        'title': 'pay rent every month on the 1st',
        'parse_title': True,
    }
    assert '#1234 "Pay rent" due 2026-11-01 09:30 !1' in result.output
    assert 'Every month on the 1st' in flat(result)
    assert 'Read from the title: Sun 1 Nov, 09:30' in result.output
    assert 'Charged' not in result.output


def test_add_joins_the_words_of_the_title(api):
    api.on('post', '/todo/tasks', FakeResponse(201, {**TASK, 'parsed': None}))

    assert invoke('add', 'local', 'call', 'the', 'dentist').exit_code == 0
    assert api.calls[0]['json']['title'] == 'call the dentist'


def test_add_keeps_the_title_with_no_parse(api):
    api.on('post', '/todo/tasks', FakeResponse(201, {**TASK, 'parsed': None}))

    invoke('add', 'local', 'on the 1st of Never', '--no-parse')

    assert api.calls[0]['json']['parse_title'] is False


def test_add_sends_every_option(api, tmp_path):
    notes = tmp_path / 'notes.md'
    notes.write_text('# Notes\n- [ ] one\n')
    api.on('get', '/todo/labels', FakeResponse(200, [HOME, WORK]))
    api.on(
        'post', '/todo/tasks', FakeResponse(201, {**TASK, 'parsed': None, 'charged_credits': 10})
    )

    result = invoke(
        'add',
        'local',
        'Report',
        '--due',
        '2026-11-05T15:00',
        '--priority',
        '2',
        '--label',
        'HOME',
        '--label',
        WORK_ID,
        '--parent',
        '1200',
        '--repeat',
        'FREQ=WEEKLY;BYDAY=MO',
        '--mode',
        'from_completion',
        '--description-file',
        str(notes),
        '--no-parse',
    )

    assert result.exit_code == 0
    assert api.requests('post', '/tasks')[-1]['json'] == {
        'title': 'Report',
        'parse_title': False,
        'description': '# Notes\n- [ ] one\n',
        'due_date': '2026-11-05',
        'due_time': '15:00',
        'priority': 2,
        'labels': [HOME_ID, WORK_ID],
        'parent': 1200,
        'rrule': 'FREQ=WEEKLY;BYDAY=MO',
        'recurrence_mode': 'from_completion',
    }
    assert 'Charged 10 credits.' in result.output


@pytest.mark.parametrize(
    ('due', 'date_', 'time_'),
    [
        ('2026-11-05', '2026-11-05', None),
        ('2026-11-05T09:00', '2026-11-05', '09:00'),
        ('2026-11-05 18:45', '2026-11-05', '18:45'),
    ],
)
def test_add_reads_a_date_with_or_without_a_time(api, due, date_, time_):
    api.on('post', '/todo/tasks', FakeResponse(201, {**TASK, 'parsed': None}))

    assert invoke('add', 'local', 'x', '--due', due).exit_code == 0
    body = api.calls[0]['json']
    assert body['due_date'] == date_ and body.get('due_time') == time_


def test_add_refuses_a_bad_date_before_calling(api):
    result = invoke('add', 'local', 'x', '--due', 'tomorrow')

    assert result.exit_code == 2 and api.calls == []


def test_add_description_and_description_file_conflict(api, tmp_path):
    notes = tmp_path / 'n.md'
    notes.write_text('x')

    result = invoke('add', 'local', 'x', '--description', 'a', '--description-file', str(notes))

    assert result.exit_code == 2 and api.calls == []


def test_add_description_file_that_is_missing(api, tmp_path):
    result = invoke('add', 'local', 'x', '--description-file', str(tmp_path / 'nope.md'))

    assert result.exit_code != 0 and api.calls == []


def test_add_blocked_by_an_unpaid_task_names_it(api):
    api.on(
        'post',
        '/todo/tasks',
        FakeResponse(
            409,
            {
                'detail': 'Top up your credits to add tasks: a repeating task is waiting to be paid.',
                'unpaid_task_numbers': [1301, 1302],
            },
        ),
    )

    result = invoke('add', 'local', 'x')

    assert result.exit_code == 1
    assert '#1301, #1302 are paid for' in flat(result)
    assert 'Top up your credits on the website' in flat(result)


def test_add_without_enough_credits_says_to_top_up(api):
    api.on('post', '/todo/tasks', FakeResponse(402, {'detail': 'Insufficient credits'}))

    result = invoke('add', 'local', 'x')

    assert result.exit_code == 1
    assert '402: Insufficient credits' in result.output and 'top up your credits' in result.output


def test_a_422_list_is_flattened(api):
    api.on(
        'post',
        '/todo/tasks',
        FakeResponse(
            422, {'detail': [{'loc': ['body', 'payload', 'labels', 0], 'msg': 'not a UUID'}]}
        ),
    )

    result = invoke('add', 'local', 'x')

    assert result.exit_code == 1 and '422: labels.0: not a UUID' in result.output


# -- get --


def test_get_shows_everything(api):
    api.on('get', '/todo/tasks/1234', FakeResponse(200, DETAIL))
    api.on(
        'get',
        '/todo/tasks/1234/comments',
        FakeResponse(
            200,
            [
                COMMENT,
                {**COMMENT, 'id': 'gone', 'body': None, 'deleted_at': '2026-10-12T00:00:00Z'},
                {**COMMENT, 'id': 'edited', 'body': 'second thoughts', 'edited_at': 'x'},
            ],
        ),
    )

    result = invoke('get', 'local', '1234')

    assert result.exit_code == 0
    out = result.output
    assert '#1234 Pay rent' in out
    assert 'Status: open' in out and 'Priority: !1' in out and 'Due: 2026-11-01 09:30' in out
    assert 'Repeats: Every month on the 1st, fixed' in out
    assert 'Labels: Home' in out and 'Subtasks: 1 open of 2' in out
    assert 'Send **it** to #1250' in out  # the Markdown, as written
    assert f'{REMINDER_ID}  5 minutes before (in_app)' in out
    assert f'{ATTACHMENT_ID}  contract.pdf (2,048 bytes)' in out
    assert 'asked the landlord' in out and '(deleted)' in out
    assert 'second thoughts' in out and '(edited)' in out
    assert '#1300 Budget' in out and '#1301 Taxes (in a comment)' in out


def test_get_of_a_task_that_is_not_there(api):
    api.on('get', '/todo/tasks/9999', FakeResponse(404, {'detail': 'Not Found'}))

    result = invoke('get', 'local', '9999')

    assert result.exit_code == 1 and '404: Not Found' in result.output


# -- edit --


def test_edit_sends_only_what_is_given(api):
    api.on('patch', '/todo/tasks/1234', FakeResponse(200, TASK))

    result = invoke(
        'edit',
        'local',
        '1234',
        '--title',
        'New',
        '--description',
        '',
        '--priority',
        '0',
        '--due',
        '2026-12-01T08:00',
    )

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {
        'title': 'New',
        'description': '',
        'priority': 0,
        'due_date': '2026-12-01',
        'due_time': '08:00',
    }
    assert 'Updated #1234' in result.output


def test_edit_clear_due(api):
    api.on('patch', '/todo/tasks/1234', FakeResponse(200, TASK))

    assert invoke('edit', 'local', '1234', '--clear-due').exit_code == 0
    assert api.calls[0]['json'] == {'due_date': None}


def test_edit_adds_and_removes_labels_against_the_current_ones(api):
    current = {**TASK, 'labels': [{'id': HOME_ID, 'name': 'Home', 'color': '#336699'}]}
    api.on('get', '/todo/tasks/1234', FakeResponse(200, current))
    api.on('get', '/todo/labels', FakeResponse(200, [HOME, WORK]))
    api.on('patch', '/todo/tasks/1234', FakeResponse(200, TASK))

    result = invoke('edit', 'local', '1234', '--label', 'work')
    removed = invoke('edit', 'local', '1234', '--remove-label', 'home', '--label', 'work')

    assert result.exit_code == 0 and removed.exit_code == 0
    assert [c['json'] for c in api.requests('patch')] == [
        {'labels': [HOME_ID, WORK_ID]},
        {'labels': [WORK_ID]},
    ]


def test_edit_with_nothing_to_change(api):
    result = invoke('edit', 'local', '1234')

    assert result.exit_code == 1 and 'Nothing to change' in result.output and api.calls == []


def test_edit_due_and_clear_due_conflict(api):
    result = invoke('edit', 'local', '1234', '--due', '2026-12-01', '--clear-due')

    assert result.exit_code == 2 and api.calls == []


# -- done, undone, postpone, trash, restore --


def test_done_completes_each_task_and_fails_if_any_does(api):
    api.on('post', '/todo/tasks/1234/complete', FakeResponse(200, TASK))
    api.on('post', '/todo/tasks/1235/complete', FakeResponse(404, {'detail': 'Not Found'}))
    api.on('post', '/todo/tasks/1236/complete', FakeResponse(200, {**TASK, 'title': 'Other'}))

    result = invoke('done', 'local', '1234', '1235', '1236')

    assert result.exit_code == 1
    assert 'Completed #1234 "Pay rent"' in result.output
    assert '#1235: 404: Not Found' in result.output
    assert 'Completed #1236 "Other"' in result.output  # the rest are not skipped


def test_undone(api):
    api.on('post', '/todo/tasks/1234/uncomplete', FakeResponse(200, TASK))

    result = invoke('undone', 'local', '1234')

    assert result.exit_code == 0 and 'Reopened #1234' in result.output


@pytest.mark.parametrize(
    ('when', 'body'),
    [
        ('tomorrow', {'preset': 'tomorrow'}),
        ('next-week', {'preset': 'next_week'}),
        ('next-weekend', {'preset': 'next_weekend'}),
        ('2026-12-24', {'preset': 'date', 'date': '2026-12-24'}),
    ],
)
def test_postpone(api, when, body):
    api.on('post', '/todo/tasks/1234/postpone', FakeResponse(200, TASK))

    result = invoke('postpone', 'local', '1234', when)

    assert result.exit_code == 0 and api.calls[0]['json'] == body
    assert 'Postponed #1234' in result.output


def test_postpone_refuses_an_unknown_when_before_calling(api):
    result = invoke('postpone', 'local', '1234', 'someday')

    assert result.exit_code == 2 and api.calls == []


def test_overdue_to_today(api):
    api.on('post', '/todo/tasks/reschedule-overdue', FakeResponse(200, {'count': 3}))

    result = invoke('overdue-to-today', 'local')

    assert result.exit_code == 0 and '3 overdue tasks moved to today.' in result.output


def test_trash_and_restore(api):
    api.on('delete', '/todo/tasks/1234', FakeResponse(204))
    api.on('post', '/todo/tasks/1234/restore', FakeResponse(200, TASK))

    trashed = invoke('trash', 'local', '1234')
    restored = invoke('restore', 'local', '1234')

    assert trashed.exit_code == 0 and '#1234 is in the trash.' in trashed.output
    assert restored.exit_code == 0 and 'Restored #1234' in restored.output


def test_restore_refused_under_a_trashed_parent(api):
    api.on(
        'post',
        '/todo/tasks/1234/restore',
        FakeResponse(400, {'detail': 'Restore the parent task first.'}),
    )

    result = invoke('restore', 'local', '1234')

    assert result.exit_code == 1 and 'Restore the parent task first.' in result.output


# -- comments --


def test_comment_joins_the_words(api):
    api.on('post', '/todo/tasks/1234/comments', FakeResponse(201, COMMENT))

    result = invoke('comment', 'local', '1234', 'asked', 'the', 'landlord')

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {'body': 'asked the landlord'}
    assert COMMENT_ID in result.output


def test_comments_lists_them(api):
    api.on(
        'get',
        '/todo/tasks/1234/comments',
        FakeResponse(200, [COMMENT, {**COMMENT, 'id': 'gone', 'body': None, 'deleted_at': 'x'}]),
    )

    result = invoke('comments', 'local', '1234')

    assert 'asked the landlord' in result.output and '(deleted)' in result.output


def test_comments_when_there_are_none(api):
    api.on('get', '/todo/tasks/1234/comments', FakeResponse(200, []))

    assert 'No comments.' in invoke('comments', 'local', '1234').output


# -- labels --


def test_labels_table(api):
    api.on('get', '/todo/labels', FakeResponse(200, [HOME, WORK]))

    result = invoke('labels', 'local')

    assert result.exit_code == 0
    home = next(line for line in result.output.splitlines() if 'Home' in line)
    assert '#336699' in home and '3' in home and 'Around the house' in home and HOME_ID in home


def test_label_add(api):
    api.on('post', '/todo/labels', FakeResponse(201, HOME))

    result = invoke(
        'label-add', 'local', 'Home', '--color', '#336699', '--description', 'Around the house'
    )

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {
        'name': 'Home',
        'color': '#336699',
        'description': 'Around the house',
    }


def test_label_edit_by_name_or_id(api):
    api.on('get', '/todo/labels', FakeResponse(200, [HOME, WORK]))
    api.on('patch', f'/todo/labels/{HOME_ID}', FakeResponse(200, {**HOME, 'name': 'House'}))

    by_name = invoke('label-edit', 'local', 'home', '--name', 'House')
    by_id = invoke('label-edit', 'local', HOME_ID, '--color', '#000000', '--description', '')

    assert by_name.exit_code == 0 and by_id.exit_code == 0
    assert [c['json'] for c in api.requests('patch')] == [
        {'name': 'House'},
        {'color': '#000000', 'description': ''},
    ]


def test_label_edit_with_nothing_to_change(api):
    result = invoke('label-edit', 'local', 'home')

    assert result.exit_code == 1 and 'Nothing to change' in result.output and api.calls == []


def test_label_delete_asks_first(api):
    api.on('get', '/todo/labels', FakeResponse(200, [HOME]))
    api.on('delete', f'/todo/labels/{HOME_ID}', FakeResponse(204))

    declined = invoke('label-delete', 'local', 'home', input='n\n')
    accepted = invoke('label-delete', 'local', 'home', input='y\n')
    forced = invoke('label-delete', 'local', 'home', '--yes')

    assert declined.exit_code == 1 and 'Cancelled.' in declined.output
    assert 'Delete the label Home (3 tasks)?' in accepted.output
    assert accepted.exit_code == 0 and forced.exit_code == 0
    assert len(api.requests('delete')) == 2


# -- reminders --


def test_remind(api):
    api.on(
        'post',
        '/todo/tasks/1234/reminders',
        FakeResponse(201, TASK['reminders'][0]),
    )

    result = invoke('remind', 'local', '1234', '60', '--channel', 'email')

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {'minutes_before': 60, 'channel': 'email'}
    assert REMINDER_ID in result.output


def test_remind_defaults_the_channel_on_the_server(api):
    api.on('post', '/todo/tasks/1234/reminders', FakeResponse(201, TASK['reminders'][0]))

    assert invoke('remind', 'local', '1234', '5').exit_code == 0
    assert api.calls[0]['json'] == {'minutes_before': 5}


def test_remind_refuses_an_unknown_channel_before_calling(api):
    result = invoke('remind', 'local', '1234', '5', '--channel', 'sms')

    assert result.exit_code == 2 and api.calls == []


def test_remind_a_task_without_a_date(api):
    api.on(
        'post',
        '/todo/tasks/1234/reminders',
        FakeResponse(400, {'detail': 'Set a due date first.'}),
    )

    result = invoke('remind', 'local', '1234', '5')

    assert result.exit_code == 1 and 'Set a due date first.' in result.output


def test_unremind(api):
    api.on('delete', f'/todo/tasks/1234/reminders/{REMINDER_ID}', FakeResponse(204))

    result = invoke('unremind', 'local', '1234', REMINDER_ID)

    assert result.exit_code == 0 and 'Reminder removed.' in result.output


# -- upload --


def ticket(attachment_id=ATTACHMENT_ID, name='key'):
    return FakeResponse(
        201,
        {
            'attachment_id': attachment_id,
            'url': f'https://fake-s3.test/{name}?put',
            'headers': {'x-amz-checksum-sha256': 'sum'},
            'expires_in': 3600,
        },
    )


def checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def test_sha256_base64_file_matches_hashlib_and_streams(tmp_path, monkeypatch):
    data = b'abc' * 1000
    path = tmp_path / 'f.bin'
    path.write_bytes(data)
    monkeypatch.setattr(todo, 'HASH_CHUNK_BYTES', 7)

    assert todo._sha256_base64_file(path) == checksum(data)


def test_upload_sends_the_whole_flow(api, tmp_path):
    path = tmp_path / 'contract.pdf'
    path.write_bytes(b'%PDF-1.4 hello')
    api.on('post', '/todo/tasks/1234/attachments', ticket())
    api.on('put', 'https://fake-s3.test/key?put', FakeResponse(200))
    api.on(
        'post',
        f'/todo/tasks/1234/attachments/{ATTACHMENT_ID}/complete',
        FakeResponse(200, DETAIL['attachments'][0]),
    )

    result = invoke('upload', 'local', '1234', str(path))

    assert result.exit_code == 0
    start, put, complete = api.calls
    assert start['json'] == {
        'filename': 'contract.pdf',
        'size': 14,
        'content_type': 'application/pdf',
        'checksum_sha256': checksum(b'%PDF-1.4 hello'),
    }
    assert put['data'] == b'%PDF-1.4 hello'
    assert put['headers'] == {'x-amz-checksum-sha256': 'sum'}  # exactly what the API returned
    assert complete['headers'] == {'Authorization': 'Bearer tok'}
    assert put['timeout'] == todo.FILE_UPLOAD_TIMEOUT_SECONDS
    assert 'contract.pdf: attached as' in result.output and '1 of 1 attached.' in result.output


def test_upload_skips_an_empty_file_and_a_folder_with_a_warning(api, tmp_path):
    good = tmp_path / 'good.txt'
    good.write_bytes(b'data')
    empty = tmp_path / 'empty.txt'
    empty.write_bytes(b'')
    folder = tmp_path / 'folder'
    folder.mkdir()
    api.on('post', '/todo/tasks/1234/attachments', ticket())
    api.on('put', 'https://fake-s3.test/key?put', FakeResponse(200))
    api.on(
        'post',
        f'/todo/tasks/1234/attachments/{ATTACHMENT_ID}/complete',
        FakeResponse(200, DETAIL['attachments'][0]),
    )

    result = invoke('upload', 'local', '1234', str(empty), str(good), str(folder))

    assert result.exit_code == 0
    assert 'Skipping empty file' in result.output and 'Skipping a folder' in result.output
    assert len(api.requests('put')) == 1 and '1 of 1 attached.' in result.output


def test_upload_with_only_empty_files_uploads_nothing(api, tmp_path):
    empty = tmp_path / 'empty.txt'
    empty.write_bytes(b'')

    result = invoke('upload', 'local', '1234', str(empty))

    assert result.exit_code == 1 and 'No files to upload.' in result.output and api.calls == []


def test_upload_of_a_missing_file_stops_before_anything(api, tmp_path):
    result = invoke('upload', 'local', '1234', str(tmp_path / 'nope.txt'))

    assert result.exit_code == 1 and 'Not found' in result.output and api.calls == []


def test_one_failing_upload_does_not_stop_the_rest(api, tmp_path):
    first = tmp_path / 'a.txt'
    first.write_bytes(b'aaa')
    second = tmp_path / 'b.txt'
    second.write_bytes(b'bbb')
    third = tmp_path / 'c.txt'
    third.write_bytes(b'ccc')
    api.on(
        'post',
        '/todo/tasks/1234/attachments',
        [
            ticket('a-id', 'a'),
            FakeResponse(402, {'detail': 'Storage is paused: top up your credits to upload.'}),
            ticket('c-id', 'c'),
        ],
    )
    api.on('put', 'https://fake-s3.test/a?put', FakeResponse(200))
    api.on('put', 'https://fake-s3.test/c?put', FakeResponse(200))
    api.on(
        'post',
        '/todo/tasks/1234/attachments/a-id/complete',
        FakeResponse(200, {**DETAIL['attachments'][0], 'id': 'a-id'}),
    )
    api.on(
        'post',
        '/todo/tasks/1234/attachments/c-id/complete',
        FakeResponse(200, {**DETAIL['attachments'][0], 'id': 'c-id'}),
    )

    result = invoke('upload', 'local', '1234', str(first), str(second), str(third))

    assert result.exit_code == 1
    assert 'b.txt: 402: Storage is paused' in result.output
    assert 'a.txt: attached as a-id' in result.output and 'c.txt: attached as c-id' in result.output
    assert '2 of 3 attached.' in result.output


def test_a_failed_put_is_reported_and_not_completed(api, tmp_path):
    path = tmp_path / 'a.txt'
    path.write_bytes(b'aaa')
    api.on('post', '/todo/tasks/1234/attachments', ticket())
    api.on('put', 'https://fake-s3.test/key?put', FakeResponse(403))

    result = invoke('upload', 'local', '1234', str(path))

    assert result.exit_code == 1
    assert 'the upload to storage failed (403)' in result.output
    assert api.requests('post', '/complete') == []


def test_a_connection_error_during_the_put_is_a_failure_not_a_crash(api, tmp_path, monkeypatch):
    import requests

    path = tmp_path / 'a.txt'
    path.write_bytes(b'aaa')
    api.on('post', '/todo/tasks/1234/attachments', ticket())

    def refuse(url, **kwargs):
        raise requests.exceptions.ConnectionError('reset')

    monkeypatch.setattr('requests.put', refuse)

    result = invoke('upload', 'local', '1234', str(path))

    assert result.exit_code == 1 and 'upload failed: reset' in result.output


def test_a_rejected_complete_is_reported(api, tmp_path):
    path = tmp_path / 'a.txt'
    path.write_bytes(b'aaa')
    api.on('post', '/todo/tasks/1234/attachments', ticket())
    api.on('put', 'https://fake-s3.test/key?put', FakeResponse(200))
    api.on(
        'post',
        f'/todo/tasks/1234/attachments/{ATTACHMENT_ID}/complete',
        FakeResponse(400, {'detail': 'The upload did not complete correctly.'}),
    )

    result = invoke('upload', 'local', '1234', str(path))

    assert result.exit_code == 1 and 'The upload did not complete correctly.' in result.output


# -- download --


def link(url='https://fake-s3.test/key?get', filename='contract.pdf'):
    return FakeResponse(
        200, {'url': url, 'disposition': 'attachment', 'filename': filename, 'expires_in': 300}
    )


def test_download_streams_to_a_part_file_then_renames(api, tmp_path):
    api.on('get', f'/todo/attachments/{ATTACHMENT_ID}/url', link())
    storage = FakeResponse(200, content=b'%PDF contents')
    api.on('get', 'https://fake-s3.test/key?get', storage)
    out = tmp_path / 'saved.pdf'

    result = invoke('download', 'local', ATTACHMENT_ID, '--out', str(out))

    assert result.exit_code == 0
    assert out.read_bytes() == b'%PDF contents'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.todo_token.local', 'saved.pdf']
    assert storage.closed
    asked, fetched = api.calls
    assert asked['params'] == {'disposition': 'attachment'}
    assert fetched['stream'] is True
    assert 'headers' not in fetched  # the API token is never sent to storage
    assert 'Wrote' in result.output


def test_download_defaults_to_the_files_name_in_a_folder(api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api.on(
        'get', f'/todo/attachments/{ATTACHMENT_ID}/url', link(filename='../../evil/contract.pdf')
    )
    api.on('get', 'https://fake-s3.test/key?get', FakeResponse(200, content=b'x'))
    folder = tmp_path / 'downloads'
    folder.mkdir()

    here = invoke('download', 'local', ATTACHMENT_ID)
    inside = invoke('download', 'local', ATTACHMENT_ID, '--out', str(folder), '--force')

    assert here.exit_code == 0 and inside.exit_code == 0
    assert (tmp_path / 'contract.pdf').read_bytes() == b'x'  # a path in the name is only a name
    assert (folder / 'contract.pdf').read_bytes() == b'x'


def test_download_refuses_to_overwrite_without_force(api, tmp_path):
    out = tmp_path / 'saved.pdf'
    out.write_bytes(b'mine')
    api.on('get', f'/todo/attachments/{ATTACHMENT_ID}/url', link())
    api.on('get', 'https://fake-s3.test/key?get', FakeResponse(200, content=b'theirs'))

    refused = invoke('download', 'local', ATTACHMENT_ID, '--out', str(out))
    forced = invoke('download', 'local', ATTACHMENT_ID, '--out', str(out), '--force')

    assert refused.exit_code == 1 and 'already exists' in refused.output
    assert forced.exit_code == 0 and out.read_bytes() == b'theirs'


def test_a_dropped_connection_leaves_no_partial_file_and_keeps_the_old_one(api, tmp_path):
    import requests

    out = tmp_path / 'saved.pdf'
    out.write_bytes(b'old')
    storage = FakeResponse(200, content=b'abcdef')
    storage.fail_mid_stream = requests.exceptions.ChunkedEncodingError('reset by peer')
    api.on('get', f'/todo/attachments/{ATTACHMENT_ID}/url', link())
    api.on('get', 'https://fake-s3.test/key?get', storage)

    result = invoke('download', 'local', ATTACHMENT_ID, '--out', str(out), '--force')

    assert result.exit_code == 1 and 'reset by peer' in result.output
    assert out.read_bytes() == b'old'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.todo_token.local', 'saved.pdf']
    assert storage.closed


def test_download_when_storage_is_paused(api, tmp_path):
    api.on(
        'get',
        f'/todo/attachments/{ATTACHMENT_ID}/url',
        FakeResponse(402, {'detail': 'Storage is paused: top up your credits.'}),
    )

    result = invoke('download', 'local', ATTACHMENT_ID, '--out', str(tmp_path / 'x'))

    assert result.exit_code == 1 and '402: Storage is paused' in result.output


def test_download_when_storage_refuses(api, tmp_path):
    api.on('get', f'/todo/attachments/{ATTACHMENT_ID}/url', link())
    api.on('get', 'https://fake-s3.test/key?get', FakeResponse(403))

    result = invoke('download', 'local', ATTACHMENT_ID, '--out', str(tmp_path / 'x'))

    assert result.exit_code == 1 and 'The download failed (403)' in result.output
    assert not (tmp_path / 'x').exists()


def test_download_into_a_folder_that_does_not_exist(api, tmp_path):
    api.on('get', f'/todo/attachments/{ATTACHMENT_ID}/url', link())

    result = invoke('download', 'local', ATTACHMENT_ID, '--out', str(tmp_path / 'no' / 'x.pdf'))

    assert result.exit_code == 1 and 'Not a folder' in result.output
    assert len(api.calls) == 1  # nothing was fetched


# -- saved filters --


SAVED = {
    'id': FILTER_ID,
    'name': 'Urgent home',
    'position': 1,
    'params': {},
    'summary': 'priority 1 · @Home',
    'created_at': '2026-10-01T09:00:00Z',
}


def test_filters_table(api):
    api.on(
        'get',
        '/todo/filters',
        FakeResponse(200, [SAVED, {**SAVED, 'id': 'b', 'name': 'All', 'summary': ''}]),
    )

    result = invoke('filters', 'local')

    assert result.exit_code == 0
    assert 'Urgent home' in result.output and 'priority 1' in result.output
    assert FILTER_ID in result.output and 'all open tasks' in result.output


def test_filters_when_there_are_none(api):
    api.on('get', '/todo/filters', FakeResponse(200, []))

    assert 'No saved filters.' in invoke('filters', 'local').output


def test_filter_run_by_id_or_name(api):
    api.on('get', '/todo/filters', FakeResponse(200, [SAVED]))
    api.on('get', f'/todo/filters/{FILTER_ID}/tasks', FakeResponse(200, page(TASK)))

    by_id = invoke('filter-run', 'local', FILTER_ID, '--limit', '10')
    by_name = invoke('filter-run', 'local', 'URGENT   home', '--offset', '5')

    assert by_id.exit_code == 0 and by_name.exit_code == 0
    assert 'Pay rent' in by_id.output
    runs = api.requests('get', '/tasks')
    assert runs[0]['params'] == {'limit': 10, 'offset': 0}
    assert runs[1]['params'] == {'limit': 50, 'offset': 5}


def test_filter_run_of_an_unknown_filter(api):
    api.on('get', '/todo/filters', FakeResponse(200, [SAVED]))

    result = invoke('filter-run', 'local', 'nope')

    assert result.exit_code == 1 and 'No saved filter called "nope"' in result.output


# -- settings, inbox --


SETTINGS = {
    'default_due_time': '09:00:00',
    'default_reminder_minutes': 5,
    'default_reminder_channel': 'in_app',
    'sender_allowlist': ['me@example.com'],
    'tasks_created_this_month': 4,
    'free_tasks_per_month': 10,
    'task_price': 10,
    'creation_blocked': False,
    'stored_bytes': 2048,
    'storage_credits_per_day': '0.0001',
    'storage_frozen': False,
}


def test_settings_shows_them(api):
    api.on('get', '/todo/settings', FakeResponse(200, SETTINGS))

    result = invoke('settings', 'local')

    assert result.exit_code == 0
    out = result.output
    assert 'Default due time' in out and '09:00' in out
    assert '5 minutes before (in_app)' in out and 'me@example.com' in out
    assert '4 (10 free, then 10 credits each)' in out and '2,048 bytes' in out


def test_settings_warns_about_a_block_and_a_frozen_storage(api):
    api.on(
        'get',
        '/todo/settings',
        FakeResponse(200, {**SETTINGS, 'creation_blocked': True, 'storage_frozen': True}),
    )

    out = invoke('settings', 'local').output

    assert 'blocked until a repeating task is paid for' in out and 'paused' in out


def test_settings_changes_are_sent_together(api):
    api.on('patch', '/todo/settings', FakeResponse(200, SETTINGS))

    result = invoke(
        'settings',
        'local',
        '--default-due-time',
        '8:30',
        '--default-reminder',
        '15',
        '--default-channel',
        'both',
    )

    assert result.exit_code == 0 and 'Saved.' in result.output
    assert api.calls[0]['json'] == {
        'default_due_time': '08:30',
        'default_reminder_minutes': 15,
        'default_reminder_channel': 'both',
    }


def test_settings_default_reminder_none(api):
    api.on(
        'patch', '/todo/settings', FakeResponse(200, {**SETTINGS, 'default_reminder_minutes': None})
    )

    result = invoke('settings', 'local', '--default-reminder', 'None')

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {'default_reminder_minutes': None}
    assert 'none' in result.output


@pytest.mark.parametrize(
    'args',
    [
        ['--default-due-time', 'noon'],
        ['--default-reminder', 'soon'],
        ['--default-channel', 'sms'],
    ],
)
def test_settings_refuses_bad_values_before_calling(api, args):
    result = invoke('settings', 'local', *args)

    assert result.exit_code == 2 and api.calls == []


def test_inbox(api):
    api.on(
        'get',
        '/todo/inbox',
        FakeResponse(200, {'address': 'todo+abc12345@ziptrigo.com', 'sender_allowlist': []}),
    )

    result = invoke('inbox', 'local')

    assert 'Email tasks to: todo+abc12345@ziptrigo.com' in result.output
    assert 'Accepted senders: anyone' in result.output


def test_regenerate_asks_first(api):
    api.on(
        'post',
        '/todo/inbox/regenerate',
        FakeResponse(200, {'address': 'todo+new00000@ziptrigo.com', 'sender_allowlist': []}),
    )

    declined = invoke('regenerate', 'local', input='n\n')
    assert declined.exit_code == 1 and api.calls == []

    accepted = invoke('regenerate', 'local', input='y\n')
    forced = invoke('regenerate', 'local', '--yes')

    assert accepted.exit_code == 0 and forced.exit_code == 0
    assert 'Your new address: todo+new00000@ziptrigo.com' in accepted.output
    assert len(api.requests('post')) == 2


def test_regenerate_hourly_limit_says_when_to_retry(api):
    api.on(
        'post',
        '/todo/inbox/regenerate',
        FakeResponse(429, {'detail': 'Too many requests'}, headers={'Retry-After': '1800'}),
    )

    result = invoke('regenerate', 'local', '--yes')

    assert result.exit_code == 1 and 'try again in 30 minutes' in result.output
    assert len(api.calls) == 1  # an hourly rule is not slept on


def test_a_short_rate_limit_is_retried(api):
    api.on(
        'get',
        '/todo/tasks',
        [
            FakeResponse(429, {'detail': 'Slow down'}, headers={'Retry-After': '2'}),
            FakeResponse(200, page(TASK)),
        ],
    )

    result = invoke('list', 'local')

    assert result.exit_code == 0 and 'Pay rent' in result.output
    assert len(api.calls) == 2


def test_the_site_is_announced_on_stderr(api):
    api.on('get', '/todo/tasks', FakeResponse(200, page()))

    result = runner.invoke(todo.app, ['list', 'local'])

    assert 'Site: http://localhost:8000 (local)' in result.output
