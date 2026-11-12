"""Unit tests for `admin/notes.py` (the Notes JWT API CLI, issue #260): argument parsing, the
request each command sends, the cost questions (and the `expected_cost` they send), the version
sent with an edit and the download's `.part` rename, with every network call mocked. Like
`test_todo.py` and `test_shorturl.py`, this module never touches Django or a database -- the CLI is
a plain HTTP client over `/api/notes/`. The shared plumbing (`api_request`, the token files) is
tested in `test_cli_utils.py`.
"""

import json

import pytest
from typer.testing import CliRunner

from admin import notes

pytestmark = pytest.mark.unit

runner = CliRunner()

NOTE_ID = '11111111-1111-1111-1111-111111111111'
WORK_ID = '22222222-2222-2222-2222-222222222222'
HOME_ID = '33333333-3333-3333-3333-333333333333'
LINK_ID = '44444444-4444-4444-4444-444444444444'
VERSION_ID = '55555555-5555-5555-5555-555555555555'
FILTER_ID = '66666666-6666-6666-6666-666666666666'

WORK = {
    'id': WORK_ID,
    'name': 'Work',
    'color': '#993333',
    'description': 'At the office',
    'position': 0,
    'note_count': 3,
}
HOME = {**WORK, 'id': HOME_ID, 'name': 'Home', 'color': '#336699', 'note_count': 0}

NOTE: dict = {
    'id': NOTE_ID,
    'title': 'Weekly plan',
    'content': '# Plan\n\n- [ ] one\n',
    'content_html': '<h1>Plan</h1>',
    'version': 3,
    'tags': [{'id': WORK_ID, 'name': 'Work', 'color': '#993333'}],
    'archived_at': None,
    'deleted_at': None,
    'is_free': True,
    'paid_through': None,
    'locked': False,
    'taken_down': False,
    'takedown_reason': None,
    'has_active_link': False,
    'charged_credits': 0,
    'restore_cost': None,
    'created_at': '2026-11-01T09:00:00Z',
    'updated_at': '2026-11-10T09:00:00Z',
}
ITEM: dict = {
    **{key: value for key, value in NOTE.items() if key not in ('content', 'content_html')},
    'excerpt': 'Plan one',
    'search_field': None,
    'search_snippet': None,
    'match_start': None,
    'match_end': None,
}
TRASHED = {**NOTE, 'deleted_at': '2026-11-11T09:00:00Z', 'restore_cost': 3}
SETTINGS = {
    'free_notes': 5,
    'free_notes_used': 5,
    'creation_price': 3,
    'renewal_price': 5,
    'renewal_days': 365,
    'next_note_cost': 3,
    'trash_days': 30,
}
VERSION = {
    'id': VERSION_ID,
    'number': 2,
    'title': 'Weekly plan',
    'editor_kind': 'anonymous',
    'editor_name': None,
    'via_link': True,
    'created_at': '2026-11-09T09:00:00Z',
    'updated_at': '2026-11-09T09:05:00Z',
}
LINK = {
    'id': LINK_ID,
    'url': 'https://app.example.com/n/abc',
    'mode': 'edit',
    'label': 'for Sam',
    'expires_at': None,
    'revoked_at': None,
    'active': True,
    'created_at': '2026-11-09T09:00:00Z',
    'last_edited_at': None,
}
SAVED = {
    'id': FILTER_ID,
    'name': 'Deploys',
    'position': 0,
    'params': {},
    'summary': '@Work · "deploy*"',
    'created_at': '2026-11-09T09:00:00Z',
}


def page(*items, count=None, limit=50, offset=0) -> dict:
    return {
        'count': len(items) if count is None else count,
        'limit': limit,
        'offset': offset,
        'results': list(items),
    }


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.text = text if text is not None else json.dumps(self._json)

    def close(self):
        pass

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda *args, **kwargs: None)


@pytest.fixture
def token_file(token_dir):
    path = token_dir / '.notes_token.local'
    path.write_text('tok')
    return path


@pytest.fixture
def logged_out(token_file):
    token_file.unlink()
    return token_file


class Api:
    """Records every request and answers from a table of `(method, path) -> response`. A path is
    relative to `/api` (`/notes/notes`)."""

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

    def writes(self) -> list[dict]:
        """Every call that changes something."""
        return [c for c in self.calls if c['method'] != 'get' and '/auth/' not in c['url']]


@pytest.fixture
def api(monkeypatch, token_file):
    return Api(monkeypatch)


def flat(result) -> str:
    """The output with the logger's line wrapping undone."""
    return ' '.join(result.output.split())


def invoke(*args, input=None):
    return runner.invoke(notes.app, [*args], input=input)


NOTE_PATH = f'/notes/notes/{NOTE_ID}'


# -- login --


def test_login_stores_the_token_for_the_environment(logged_out, api, token_dir, monkeypatch):
    api.on('post', '/auth/login', FakeResponse(200, {'access_token': 'tok123'}))
    monkeypatch.setenv('NOTES_PASSWORD', 'pw')

    result = invoke('login', 'local', 'me@example.com')

    assert result.exit_code == 0
    assert (token_dir / '.notes_token.local').read_text() == 'tok123'
    assert api.calls[0]['json'] == {'email': 'me@example.com', 'password': 'pw'}


def test_login_takes_no_password_argument(logged_out, api, monkeypatch):
    """No CLI takes a password on the command line (shell history): a third argument is refused."""
    monkeypatch.delenv('NOTES_PASSWORD', raising=False)

    result = invoke('login', 'local', 'me@example.com', 'secret')

    assert result.exit_code == 2 and api.calls == []


def test_login_prompts_when_there_is_no_password(logged_out, api, monkeypatch):
    api.on('post', '/auth/login', FakeResponse(200, {'access_token': 't'}))
    monkeypatch.delenv('NOTES_PASSWORD', raising=False)
    prompts: list[dict] = []
    monkeypatch.setattr('typer.prompt', lambda *a, **k: prompts.append(k) or 'typed-pw')

    assert invoke('login', 'local', 'me@example.com').exit_code == 0
    assert api.calls[0]['json']['password'] == 'typed-pw'
    assert prompts == [{'hide_input': True}]


def test_a_failed_login_says_why_and_writes_no_token(logged_out, api, monkeypatch):
    api.on('post', '/auth/login', FakeResponse(401, {'detail': 'Invalid credentials'}))
    monkeypatch.setenv('NOTES_PASSWORD', 'bad')

    result = invoke('login', 'local', 'me@example.com')

    assert result.exit_code == 1
    assert 'Invalid credentials' in result.output
    assert 'run `login` again' not in result.output
    assert not logged_out.exists()


def test_every_command_but_login_needs_a_token(logged_out, api):
    for args in (['list', 'local'], ['get', 'local', NOTE_ID], ['add', 'local', 'x']):
        assert invoke(*args).exit_code == 1
    assert api.calls == []


def test_a_token_is_per_cli_and_environment(token_file, token_dir, api):
    (token_dir / '.notes_token.prod').write_text('prod-token')
    api.on('get', '/notes/notes', FakeResponse(200, page()))

    invoke('list', 'local')

    assert api.calls[0]['headers'] == {'Authorization': 'Bearer tok'}


# -- list --


def test_list_shows_the_notes(api):
    shared = {**ITEM, 'title': '[bold]Shared[/bold]', 'has_active_link': True, 'locked': True}
    api.on('get', '/notes/notes', FakeResponse(200, page(ITEM, shared)))

    result = invoke('list', 'local')

    assert result.exit_code == 0
    assert api.calls[0]['params'] == {'view': 'active', 'limit': 50, 'offset': 0}
    assert 'Weekly plan' in result.output and 'Work' in result.output
    assert '[bold]Shared[/bold]' in result.output  # Rich markup in a title is shown as typed
    assert 'shared, locked' in result.output
    assert NOTE_ID in result.output and '2026-11-10' in result.output


def test_list_with_nothing(api):
    api.on('get', '/notes/notes', FakeResponse(200, page()))

    result = invoke('list', 'local')

    assert result.exit_code == 0 and 'No notes found.' in result.output


def test_list_sends_every_option_resolving_tags_by_name(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK, HOME]))
    api.on('get', '/notes/notes', FakeResponse(200, page(ITEM)))

    result = invoke(
        'list',
        'local',
        '--view',
        'all',
        '--q',
        'deploy*',
        '--regex',
        '--tag',
        'WORK',
        '--tag',
        'none',
        '--tag',
        HOME_ID,
        '--shared',
        '--sort',
        'title',
        '--dir',
        'desc',
        '--limit',
        '10',
        '--offset',
        '20',
    )

    assert result.exit_code == 0
    assert api.requests('get', '/notes/notes')[0]['params'] == {
        'view': 'all',
        'limit': 10,
        'offset': 20,
        'q': 'deploy*',
        'mode': 'regex',
        'tag': f'{WORK_ID},none,{HOME_ID}',
        'shared': 'yes',
        'sort': 'title',
        'dir': 'desc',
    }


def test_list_not_shared(api):
    api.on('get', '/notes/notes', FakeResponse(200, page(ITEM)))

    invoke('list', 'local', '--not-shared')

    assert api.calls[0]['params']['shared'] == 'no'


def test_list_stops_on_an_unknown_tag(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK]))

    result = invoke('list', 'local', '--tag', 'nope')

    assert result.exit_code == 1 and 'No tag called "nope"' in result.output
    assert api.requests('get', '/notes/notes') == []


def test_list_shows_where_a_search_matched(api):
    hit = {
        **ITEM,
        'search_field': 'content',
        'search_snippet': 'remember to rotate the keys soon',
        'match_start': 12,
        'match_end': 27,
    }
    api.on('get', '/notes/notes', FakeResponse(200, page(hit)))

    result = invoke('list', 'local', '--q', 'rotate*keys')

    assert 'Where it matched' in result.output
    assert 'Weekly plan (content): remember to rotate the keys soon' in flat(result)


def test_list_says_how_to_see_more(api):
    api.on('get', '/notes/notes', FakeResponse(200, page(ITEM, count=120)))

    result = invoke('list', 'local')

    assert 'Showing 1-1 of 120 notes. Use --offset 1 to see more.' in flat(result)


def test_the_trash_shows_what_a_restore_costs(api):
    free = {**TRASHED, 'title': 'Free again', 'restore_cost': 0}
    api.on('get', '/notes/notes', FakeResponse(200, page(TRASHED, free)))

    result = invoke('list', 'local', '--view', 'trash')

    assert 'Deleted' in result.output and '2026-11-11' in result.output
    assert '3 credits' in result.output and 'free' in result.output


def test_a_bad_view_is_the_apis_400(api):
    api.on('get', '/notes/notes', FakeResponse(400, {'detail': 'view must be one of: active.'}))

    result = invoke('list', 'local', '--view', 'x')

    assert result.exit_code == 1 and '400: view must be one of' in flat(result)


# -- get --


def test_get_prints_the_markdown_on_stdout_and_the_rest_on_stderr(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))

    result = invoke('get', 'local', NOTE_ID)

    assert result.exit_code == 0
    assert result.stdout == '# Plan\n\n- [ ] one\n'
    assert 'Weekly plan' in result.stderr and 'Version' in result.stderr
    assert NOTE_ID in result.stderr and 'free' in result.stderr


def test_get_prints_markup_like_text_as_it_is(api):
    note = {**NOTE, 'title': '[red]x[/red]', 'content': '[bold]not markup[/bold]'}
    api.on('get', NOTE_PATH, FakeResponse(200, note))

    result = invoke('get', 'local', NOTE_ID)

    assert result.stdout == '[bold]not markup[/bold]\n'
    assert '[red]x[/red]' in result.stderr


def test_get_a_note_with_no_content_and_a_trashed_one(api):
    api.on('get', NOTE_PATH, FakeResponse(200, {**TRASHED, 'content': ''}))

    result = invoke('get', 'local', NOTE_ID)

    assert result.stdout == ''
    assert 'In the Trash since' in result.stderr and 'restoring costs 3 credits' in flat(result)


def test_get_says_when_a_note_is_locked_or_taken_down(api):
    note = {**NOTE, 'locked': True, 'taken_down': True, 'takedown_reason': 'Phishing'}
    api.on('get', NOTE_PATH, FakeResponse(200, note))

    result = invoke('get', 'local', NOTE_ID)

    assert 'Phishing' in result.stderr and 'top up your credits' in flat(result)


def test_get_another_users_note(api):
    api.on('get', NOTE_PATH, FakeResponse(404, {'detail': 'Not Found'}))

    result = invoke('get', 'local', NOTE_ID)

    assert result.exit_code == 1 and '404: Not Found' in result.output


# -- add --


@pytest.fixture
def plan(tmp_path):
    path = tmp_path / 'plan.md'
    path.write_text('# Plan\n')
    return path


def test_add_joins_the_words_of_the_title_and_asks_nothing_for_a_free_note(api):
    api.on('get', '/notes/settings', FakeResponse(200, {**SETTINGS, 'next_note_cost': 0}))
    api.on('post', '/notes/notes', FakeResponse(201, {**NOTE, 'title': 'Weekly plan'}))

    result = invoke('add', 'local', 'Weekly', 'plan')

    assert result.exit_code == 0
    # A free slot is still sent as the expectation: if it is taken meanwhile, nothing is charged.
    assert api.writes()[0]['json'] == {'title': 'Weekly plan', 'content': '', 'expected_cost': 0}
    assert 'Added "Weekly plan".' in result.output and NOTE_ID in result.output
    assert 'Charged' not in result.output


def test_add_reads_the_content_from_a_file_and_resolves_tags(api, tmp_path):
    plan = tmp_path / 'plan.md'
    plan.write_text('# Plan\n- [ ] one\n')
    api.on('get', '/notes/tags', FakeResponse(200, [WORK, HOME]))
    api.on('get', '/notes/settings', FakeResponse(200, {**SETTINGS, 'next_note_cost': 0}))
    api.on('post', '/notes/notes', FakeResponse(201, NOTE))

    result = invoke('add', 'local', 'Plan', '--file', str(plan), '--tag', 'work', '--tag', HOME_ID)

    assert result.exit_code == 0
    assert api.writes()[0]['json'] == {
        'title': 'Plan',
        'content': '# Plan\n- [ ] one\n',
        'tags': [WORK_ID, HOME_ID],
        'expected_cost': 0,
    }


def test_add_reads_the_content_from_standard_input_when_it_is_piped(api):
    api.on('get', '/notes/settings', FakeResponse(200, {**SETTINGS, 'next_note_cost': 0}))
    api.on('post', '/notes/notes', FakeResponse(201, NOTE))

    result = invoke('add', 'local', 'Piped', input='from a pipe\n')

    assert result.exit_code == 0
    assert api.writes()[0]['json']['content'] == 'from a pipe\n'


def test_add_with_file_dash_reads_standard_input(api):
    api.on('get', '/notes/settings', FakeResponse(200, {**SETTINGS, 'next_note_cost': 0}))
    api.on('post', '/notes/notes', FakeResponse(201, NOTE))

    invoke('add', 'local', 'Piped', '--file', '-', input='text')

    assert api.writes()[0]['json']['content'] == 'text'


def test_add_asks_before_spending_and_sends_the_confirmed_cost(api, plan):
    api.on('get', '/notes/settings', FakeResponse(200, SETTINGS))
    api.on('post', '/notes/notes', FakeResponse(201, {**NOTE, 'charged_credits': 3}))

    result = invoke('add', 'local', 'Plan', '--file', str(plan), input='y\n')

    assert result.exit_code == 0
    assert 'Adding this note costs 3 credits. Continue?' in flat(result)
    assert api.writes()[0]['json']['expected_cost'] == 3
    assert 'Charged 3 credits.' in result.output


def test_add_declined_charges_nothing(api, plan):
    api.on('get', '/notes/settings', FakeResponse(200, SETTINGS))

    result = invoke('add', 'local', 'Plan', '--file', str(plan), input='n\n')

    assert result.exit_code == 1 and 'nothing was charged' in result.output
    assert api.writes() == []


def test_add_cannot_ask_about_a_cost_when_standard_input_was_the_content(api):
    api.on('get', '/notes/settings', FakeResponse(200, SETTINGS))

    result = invoke('add', 'local', 'Plan', input='piped content\ny\n')

    assert result.exit_code == 1
    assert 'content came from standard input' in flat(result) and 'pass --yes' in flat(result)
    assert api.writes() == []


def test_add_with_piped_content_and_yes_accepts_the_cost(api):
    api.on('post', '/notes/notes', FakeResponse(201, {**NOTE, 'charged_credits': 3}))

    result = invoke('add', 'local', 'Plan', '--yes', input='piped content')

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {'title': 'Plan', 'content': 'piped content'}


def test_add_yes_skips_the_question_and_sends_no_expectation(api):
    api.on('post', '/notes/notes', FakeResponse(201, {**NOTE, 'charged_credits': 3}))

    result = invoke('add', 'local', 'Plan', '--yes')

    assert result.exit_code == 0
    assert api.calls[0]['json'] == {'title': 'Plan', 'content': ''}
    assert api.requests('get', '/notes/settings') == []  # no question, so no quote either


def test_add_when_the_cost_changed_meanwhile(api):
    api.on('get', '/notes/settings', FakeResponse(200, {**SETTINGS, 'next_note_cost': 0}))
    detail = 'The cost changed: it is now 3 credits. Send expected_cost=3 to confirm it.'
    api.on('post', '/notes/notes', FakeResponse(409, {'detail': detail}))

    result = invoke('add', 'local', 'Plan')

    assert result.exit_code == 1 and '409: The cost changed' in flat(result)


def test_add_with_too_few_credits_points_at_the_website(api, plan):
    api.on('get', '/notes/settings', FakeResponse(200, SETTINGS))
    api.on('post', '/notes/notes', FakeResponse(402, {'detail': 'Insufficient credits'}))

    result = invoke('add', 'local', 'Plan', '--file', str(plan), input='y\n')

    assert result.exit_code == 1 and 'top up your credits on the website' in flat(result)


def test_add_stops_on_an_unknown_tag_before_anything_is_written(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK]))

    result = invoke('add', 'local', 'Plan', '--tag', 'nope')

    assert result.exit_code == 1 and 'No tag called "nope"' in result.output
    assert api.writes() == []


def test_add_reports_an_unreadable_file(api, tmp_path):
    result = invoke('add', 'local', 'Plan', '--file', str(tmp_path / 'missing.md'))

    assert result.exit_code == 1 and 'Could not read' in result.output
    assert api.calls == []


def test_a_title_in_rich_markup_is_printed_as_typed(api):
    api.on('post', '/notes/notes', FakeResponse(201, {**NOTE, 'title': '[bold]x[/bold]'}))

    result = invoke('add', 'local', '[bold]x[/bold]', '--yes')

    assert 'Added "[bold]x[/bold]".' in result.output


# -- edit --


def test_edit_reads_the_note_and_sends_its_version(api, tmp_path):
    plan = tmp_path / 'plan.md'
    plan.write_text('new text')
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('patch', NOTE_PATH, FakeResponse(200, {**NOTE, 'title': 'Plan v2', 'version': 4}))

    result = invoke('edit', 'local', NOTE_ID, '--title', 'Plan v2', '--file', str(plan))

    assert result.exit_code == 0
    assert api.writes()[0]['json'] == {
        'title': 'Plan v2',
        'content': 'new text',
        'base_version': 3,
    }
    assert 'Saved "Plan v2" (version 4).' in result.output


def test_edit_a_title_only_sends_no_content(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('patch', NOTE_PATH, FakeResponse(200, NOTE))

    invoke('edit', 'local', NOTE_ID, '--title', 'New')

    assert api.writes()[0]['json'] == {'title': 'New', 'base_version': 3}


def test_edit_adds_and_removes_tags_by_name(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))  # it has Work
    api.on('get', '/notes/tags', FakeResponse(200, [WORK, HOME]))
    api.on('patch', NOTE_PATH, FakeResponse(200, NOTE))

    result = invoke('edit', 'local', NOTE_ID, '--tag', 'home', '--remove-tag', 'WORK')

    assert result.exit_code == 0
    # Tags alone are not a save of the text: no version is sent.
    assert api.writes()[0]['json'] == {'tags': [HOME_ID]}


def test_edit_keeps_the_notes_other_tags_when_adding(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('get', '/notes/tags', FakeResponse(200, [WORK, HOME]))
    api.on('patch', NOTE_PATH, FakeResponse(200, NOTE))

    invoke('edit', 'local', NOTE_ID, '--tag', 'home', '--tag', 'work')

    assert api.writes()[0]['json'] == {'tags': [WORK_ID, HOME_ID]}


def test_edit_stops_on_an_unknown_tag_before_writing(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK]))

    result = invoke('edit', 'local', NOTE_ID, '--title', 'x', '--remove-tag', 'nope')

    assert result.exit_code == 1 and 'No tag called "nope"' in result.output
    assert api.writes() == []


def test_edit_with_nothing_to_change(api):
    result = invoke('edit', 'local', NOTE_ID)

    assert result.exit_code == 1 and 'Nothing to change' in result.output
    assert api.calls == []


def test_edit_a_conflict_prints_the_message_and_exits_1(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    conflict = {
        'detail': 'This note was changed by someone else while you were editing.',
        'current_version': 5,
        'current_title': 'Theirs',
        'current_content': 'theirs',
    }
    api.on('patch', NOTE_PATH, FakeResponse(409, conflict))

    result = invoke('edit', 'local', NOTE_ID, '--title', 'Mine')

    assert result.exit_code == 1
    assert '409: This note was changed by someone else while you were editing.' in flat(result)
    assert 'version 5 now' in flat(result)


def test_edit_a_locked_note(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on(
        'patch',
        NOTE_PATH,
        FakeResponse(402, {'detail': 'This note is locked until its renewal is paid.'}),
    )

    result = invoke('edit', 'local', NOTE_ID, '--title', 'x')

    assert result.exit_code == 1 and 'locked' in result.output


# -- archive, unarchive, trash --


def test_archive_and_unarchive(api):
    api.on('post', f'{NOTE_PATH}/archive', FakeResponse(200, NOTE))
    api.on('post', f'{NOTE_PATH}/unarchive', FakeResponse(200, NOTE))

    assert 'Archived "Weekly plan".' in invoke('archive', 'local', NOTE_ID).output
    assert (
        'Moved back to your notes: "Weekly plan".' in invoke('unarchive', 'local', NOTE_ID).output
    )


def test_trash_asks_nothing_and_says_so(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('delete', NOTE_PATH, FakeResponse(204))

    result = invoke('trash', 'local', NOTE_ID)

    assert result.exit_code == 0 and 'Moved "Weekly plan" to the Trash.' in result.output
    assert len(api.requests('delete')) == 1


# -- restore --


def test_restore_asks_and_sends_the_cost(api):
    api.on('get', NOTE_PATH, FakeResponse(200, TRASHED))
    api.on('post', f'{NOTE_PATH}/restore', FakeResponse(200, NOTE))

    result = invoke('restore', 'local', NOTE_ID, input='y\n')

    assert result.exit_code == 0
    assert 'Restoring "Weekly plan" costs 3 credits. Continue?' in flat(result)
    assert api.writes()[0]['params'] == {'expected_cost': 3}
    assert 'Restored "Weekly plan".' in result.output


def test_restore_declined(api):
    api.on('get', NOTE_PATH, FakeResponse(200, TRASHED))

    result = invoke('restore', 'local', NOTE_ID, input='n\n')

    assert result.exit_code == 1 and api.writes() == []


def test_restore_that_is_free_is_sent_with_its_expectation(api):
    api.on('get', NOTE_PATH, FakeResponse(200, {**TRASHED, 'restore_cost': 0}))
    api.on('post', f'{NOTE_PATH}/restore', FakeResponse(200, NOTE))

    result = invoke('restore', 'local', NOTE_ID)

    assert result.exit_code == 0 and api.writes()[0]['params'] == {'expected_cost': 0}


def test_restore_yes_sends_no_expectation(api):
    api.on('get', NOTE_PATH, FakeResponse(200, TRASHED))
    api.on('post', f'{NOTE_PATH}/restore', FakeResponse(200, NOTE))

    invoke('restore', 'local', NOTE_ID, '--yes')

    assert api.writes()[0].get('params') is None


def test_restore_a_note_that_is_not_in_the_trash(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))

    result = invoke('restore', 'local', NOTE_ID)

    assert result.exit_code == 0 and 'is not in the Trash' in result.output
    assert api.writes() == []


# -- delete --


def test_delete_needs_the_trash_and_asks(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))

    result = invoke('delete', 'local', NOTE_ID, '--yes')

    assert result.exit_code == 1 and 'not in the Trash' in result.output
    assert api.writes() == []


def test_delete_asks_first(api):
    api.on('get', NOTE_PATH, FakeResponse(200, TRASHED))
    api.on('post', f'{NOTE_PATH}/delete-forever', FakeResponse(204))

    declined = invoke('delete', 'local', NOTE_ID, input='n\n')
    assert declined.exit_code == 1 and api.writes() == []

    result = invoke('delete', 'local', NOTE_ID, input='y\n')
    assert result.exit_code == 0 and 'Deleted "Weekly plan" for good.' in result.output
    assert 'cannot be undone' in flat(result)


def test_delete_yes(api):
    api.on('get', NOTE_PATH, FakeResponse(200, TRASHED))
    api.on('post', f'{NOTE_PATH}/delete-forever', FakeResponse(204))

    assert invoke('delete', 'local', NOTE_ID, '--yes').exit_code == 0
    assert len(api.writes()) == 1


# -- duplicate --


def test_duplicate_asks_and_sends_the_cost(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('get', '/notes/settings', FakeResponse(200, SETTINGS))
    copy = {**NOTE, 'id': WORK_ID, 'title': 'Weekly plan (copy)', 'charged_credits': 3}
    api.on('post', f'{NOTE_PATH}/duplicate', FakeResponse(201, copy))

    result = invoke('duplicate', 'local', NOTE_ID, input='y\n')

    assert result.exit_code == 0
    assert 'Duplicating this note costs 3 credits. Continue?' in flat(result)
    assert api.writes()[0]['params'] == {'expected_cost': 3}
    assert 'Added "Weekly plan (copy)".' in result.output and 'Charged 3 credits.' in result.output


def test_duplicate_yes_sends_no_expectation(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('post', f'{NOTE_PATH}/duplicate', FakeResponse(201, NOTE))

    assert invoke('duplicate', 'local', NOTE_ID, '--yes').exit_code == 0
    assert api.writes()[0].get('params') is None and len(api.writes()) == 1


def test_duplicate_declined(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('get', '/notes/settings', FakeResponse(200, SETTINGS))

    result = invoke('duplicate', 'local', NOTE_ID, input='n\n')

    assert result.exit_code == 1 and api.writes() == []


def test_duplicate_a_taken_down_note_is_refused_before_a_price_is_asked(api):
    api.on('get', NOTE_PATH, FakeResponse(200, {**NOTE, 'taken_down': True}))
    api.on('get', '/notes/settings', FakeResponse(200, SETTINGS))

    result = invoke('duplicate', 'local', NOTE_ID)

    assert (
        result.exit_code == 1 and 'This note was taken down and cannot be copied.' in result.output
    )
    assert 'Continue?' not in flat(result)
    assert api.writes() == [] and api.requests('get', '/notes/settings') == []


def test_duplicate_a_note_taken_down_meanwhile_shows_the_servers_refusal(api):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    api.on('get', '/notes/settings', FakeResponse(200, {**SETTINGS, 'next_note_cost': 0}))
    api.on(
        'post',
        f'{NOTE_PATH}/duplicate',
        FakeResponse(409, {'detail': 'This note was taken down and cannot be copied.'}),
    )

    result = invoke('duplicate', 'local', NOTE_ID)

    assert result.exit_code == 1 and 'cannot be copied' in result.output


# -- download --


def test_download_writes_a_file_named_after_the_title(api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api.on('get', NOTE_PATH, FakeResponse(200, {**NOTE, 'title': 'Weekly plan: v2!'}))

    result = invoke('download', 'local', NOTE_ID)

    assert result.exit_code == 0
    assert (tmp_path / 'weekly-plan-v2.md').read_text() == NOTE['content']
    assert not list(tmp_path.glob('.*.part'))
    assert 'weekly-plan-v2.md' in result.output


def test_download_to_a_folder_or_a_path_and_a_title_with_nothing_to_name_it_by(api, tmp_path):
    api.on('get', NOTE_PATH, FakeResponse(200, {**NOTE, 'title': '???'}))

    assert invoke('download', 'local', NOTE_ID, '--out', str(tmp_path)).exit_code == 0
    assert (tmp_path / 'note.md').exists()
    target = tmp_path / 'chosen.md'
    assert invoke('download', 'local', NOTE_ID, '--out', str(target)).exit_code == 0
    assert target.read_text() == NOTE['content']


def test_download_refuses_to_overwrite_without_force(api, tmp_path):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    target = tmp_path / 'out.md'
    target.write_text('mine')

    refused = invoke('download', 'local', NOTE_ID, '--out', str(target))
    assert refused.exit_code == 1 and target.read_text() == 'mine'
    assert 'already exists' in refused.output

    assert invoke('download', 'local', NOTE_ID, '--out', str(target), '--force').exit_code == 0
    assert target.read_text() == NOTE['content']


def test_download_into_a_missing_folder(api, tmp_path):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))

    result = invoke('download', 'local', NOTE_ID, '--out', str(tmp_path / 'no' / 'out.md'))

    assert result.exit_code == 1 and 'Not a folder' in result.output


def test_download_leaves_no_partial_file_when_the_rename_fails(api, tmp_path, monkeypatch):
    api.on('get', NOTE_PATH, FakeResponse(200, NOTE))
    from pathlib import Path

    def broken(self, target):
        raise OSError('disk full')

    monkeypatch.setattr(Path, 'replace', broken)
    target = tmp_path / 'out.md'

    result = invoke('download', 'local', NOTE_ID, '--out', str(target))

    assert result.exit_code == 1 and 'disk full' in result.output
    assert not target.exists() and not list(tmp_path.glob('.*.part'))


# -- versions --


def test_versions_lists_who_saved_each(api):
    owner = {**VERSION, 'id': WORK_ID, 'number': 1, 'editor_kind': 'owner', 'via_link': False}
    user = {
        **VERSION,
        'id': HOME_ID,
        'number': 3,
        'editor_kind': 'user',
        'editor_name': 'Vera',
        'title': '[b]x[/b]',
    }
    api.on('get', f'{NOTE_PATH}/versions', FakeResponse(200, [user, VERSION, owner]))

    result = invoke('versions', 'local', NOTE_ID)

    assert result.exit_code == 0
    assert 'Vera (via a link)' in flat(result) and 'Someone (via a link)' in flat(result)
    assert 'You' in result.output and '[b]x[/b]' in result.output
    assert VERSION_ID in result.output and '2026-11-09 09:05' in result.output


def test_version_restore_asks_first(api):
    api.on(
        'get', f'{NOTE_PATH}/versions/{VERSION_ID}', FakeResponse(200, {**VERSION, 'content': 'x'})
    )
    api.on(
        'post',
        f'{NOTE_PATH}/versions/{VERSION_ID}/restore',
        FakeResponse(200, {**NOTE, 'version': 6}),
    )

    declined = invoke('version-restore', 'local', NOTE_ID, VERSION_ID, input='n\n')
    assert declined.exit_code == 1 and api.writes() == []

    result = invoke('version-restore', 'local', NOTE_ID, VERSION_ID, input='y\n')
    assert result.exit_code == 0
    assert 'Restore version 2' in flat(result) and 'is now version 6' in flat(result)


def test_version_restore_yes(api):
    api.on(
        'get', f'{NOTE_PATH}/versions/{VERSION_ID}', FakeResponse(200, {**VERSION, 'content': 'x'})
    )
    api.on('post', f'{NOTE_PATH}/versions/{VERSION_ID}/restore', FakeResponse(200, NOTE))

    assert invoke('version-restore', 'local', NOTE_ID, VERSION_ID, '--yes').exit_code == 0
    assert len(api.writes()) == 1


# -- links --


def test_links_lists_them_with_their_state(api):
    revoked = {**LINK, 'id': WORK_ID, 'revoked_at': '2026-11-10T09:00:00Z', 'active': False}
    expired = {**LINK, 'id': HOME_ID, 'expires_at': '2026-11-01T09:00:00Z', 'active': False}
    api.on('get', f'{NOTE_PATH}/links', FakeResponse(200, [LINK, revoked, expired]))

    result = invoke('links', 'local', NOTE_ID)

    assert result.exit_code == 0
    assert 'active' in result.output and 'revoked' in result.output and 'expired' in result.output
    assert 'https://app.example.com/n/abc' in result.output and 'for Sam' in result.output


def test_links_with_none(api):
    api.on('get', f'{NOTE_PATH}/links', FakeResponse(200, []))

    assert 'No share links' in invoke('links', 'local', NOTE_ID).output


def test_share_makes_a_read_only_link_by_default(api):
    api.on('post', f'{NOTE_PATH}/links', FakeResponse(201, {**LINK, 'mode': 'read'}))

    result = invoke('share', 'local', NOTE_ID)

    assert result.exit_code == 0
    assert api.writes()[0]['json'] == {'mode': 'read'}
    assert 'Made a read-only link: https://app.example.com/n/abc' in result.output


def test_share_edit_with_a_label_and_a_date(api):
    api.on('post', f'{NOTE_PATH}/links', FakeResponse(201, LINK))

    result = invoke(
        'share', 'local', NOTE_ID, '--edit', '--label', 'for Sam', '--expires', '2026-12-31'
    )

    assert result.exit_code == 0
    assert api.writes()[0]['json'] == {
        'mode': 'edit',
        'label': 'for Sam',
        'expires_at': '2026-12-31',  # a bare date: the server reads it as the end of that day
    }
    assert 'Made an editable link: https://app.example.com/n/abc' in result.output


def test_share_with_a_date_time(api):
    api.on('post', f'{NOTE_PATH}/links', FakeResponse(201, LINK))

    invoke('share', 'local', NOTE_ID, '--expires', '2026-12-31 18:00')

    assert api.writes()[0]['json']['expires_at'] == '2026-12-31T18:00:00'


def test_share_refuses_a_bad_date_before_calling(api):
    result = invoke('share', 'local', NOTE_ID, '--expires', 'soon')

    assert result.exit_code == 2 and api.calls == []


def test_share_a_refusal(api):
    detail = 'A note can have at most 10 active links.'
    api.on('post', f'{NOTE_PATH}/links', FakeResponse(400, {'detail': detail}))

    result = invoke('share', 'local', NOTE_ID)

    assert result.exit_code == 1 and 'at most 10 active links' in flat(result)


def test_unshare_asks_first(api):
    api.on('delete', f'{NOTE_PATH}/links/{LINK_ID}', FakeResponse(204))

    declined = invoke('unshare', 'local', NOTE_ID, LINK_ID, input='n\n')
    assert declined.exit_code == 1 and api.writes() == []

    result = invoke('unshare', 'local', NOTE_ID, LINK_ID, input='y\n')
    assert result.exit_code == 0 and 'Revoked the link.' in result.output
    assert invoke('unshare', 'local', NOTE_ID, LINK_ID, '--yes').exit_code == 0


# -- tags --


def test_tags_lists_them(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK, HOME]))

    result = invoke('tags', 'local')

    assert result.exit_code == 0
    assert (
        'Work' in result.output and '#993333' in result.output and 'At the office' in result.output
    )
    assert WORK_ID in result.output


def test_tags_with_none(api):
    api.on('get', '/notes/tags', FakeResponse(200, []))

    assert 'No tags' in invoke('tags', 'local').output


def test_tag_add(api):
    api.on('post', '/notes/tags', FakeResponse(201, WORK))

    result = invoke(
        'tag-add', 'local', 'work', '--color', '#993333', '--description', 'At the office'
    )

    assert result.exit_code == 0
    assert api.writes()[0]['json'] == {
        'name': 'work',
        'color': '#993333',
        'description': 'At the office',
    }
    assert 'Added the tag Work (#993333).' in result.output


def test_tag_edit_by_name_sends_only_what_is_passed(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK, HOME]))
    api.on('patch', f'/notes/tags/{WORK_ID}', FakeResponse(200, {**WORK, 'name': 'Job'}))

    result = invoke('tag-edit', 'local', 'WORK', '--name', 'Job')

    assert result.exit_code == 0 and api.writes()[0]['json'] == {'name': 'Job'}
    assert 'Updated the tag Job' in result.output


def test_tag_edit_with_nothing_to_change_or_an_unknown_tag(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK]))

    assert invoke('tag-edit', 'local', 'work').exit_code == 1
    assert api.calls == []
    result = invoke('tag-edit', 'local', 'nope', '--name', 'x')
    assert result.exit_code == 1 and 'No tag called "nope"' in result.output


def test_tag_delete_asks_first(api):
    api.on('get', '/notes/tags', FakeResponse(200, [WORK]))
    api.on('delete', f'/notes/tags/{WORK_ID}', FakeResponse(204))

    declined = invoke('tag-delete', 'local', 'work', input='n\n')
    assert declined.exit_code == 1 and api.writes() == []

    result = invoke('tag-delete', 'local', 'work', input='y\n')
    assert result.exit_code == 0 and 'Delete the tag Work (3 notes)?' in flat(result)
    assert invoke('tag-delete', 'local', WORK_ID, '--yes').exit_code == 0


# -- filters --


def test_filters_lists_them(api):
    api.on('get', '/notes/filters', FakeResponse(200, [SAVED]))

    result = invoke('filters', 'local')

    assert result.exit_code == 0 and 'Deploys' in result.output
    assert '@Work' in result.output and FILTER_ID in result.output


def test_filters_with_none(api):
    api.on('get', '/notes/filters', FakeResponse(200, []))

    assert 'No saved filters' in invoke('filters', 'local').output


def test_filter_run_by_name_or_id(api):
    api.on('get', '/notes/filters', FakeResponse(200, [SAVED]))
    api.on('get', f'/notes/filters/{FILTER_ID}/notes', FakeResponse(200, page(ITEM)))

    by_name = invoke('filter-run', 'local', 'DEPLOYS', '--limit', '5', '--offset', '10')

    assert by_name.exit_code == 0 and 'Weekly plan' in by_name.output
    assert api.requests('get', '/notes')[-1]['params'] == {'limit': 5, 'offset': 10}
    assert invoke('filter-run', 'local', FILTER_ID).exit_code == 0


def test_filter_run_prints_a_name_with_markup_as_typed(api):
    saved = {**SAVED, 'name': 'Work [/] [bold]x'}
    api.on('get', '/notes/filters', FakeResponse(200, [saved]))
    api.on('get', f'/notes/filters/{FILTER_ID}/notes', FakeResponse(200, page(ITEM)))

    result = invoke('filter-run', 'local', FILTER_ID)

    assert result.exit_code == 0, result.output
    assert 'Work [/] [bold]x' in flat(result)


def test_filter_run_an_unknown_filter(api):
    api.on('get', '/notes/filters', FakeResponse(200, [SAVED]))

    result = invoke('filter-run', 'local', 'nope')

    assert result.exit_code == 1 and 'No saved filter called "nope"' in result.output


# -- errors --


def test_a_rate_limit_says_when_to_try_again(api):
    api.on(
        'get',
        '/notes/notes',
        FakeResponse(429, {'detail': 'Too many requests'}, headers={'Retry-After': '1800'}),
    )

    result = invoke('list', 'local')

    assert result.exit_code == 1 and 'rate limited, try again in 30 minutes' in flat(result)


def test_an_expired_token_says_to_log_in_again(api):
    api.on('get', '/notes/notes', FakeResponse(401, {'detail': 'Unauthorized'}))

    result = invoke('list', 'local')

    assert result.exit_code == 1 and 'run `login` again' in flat(result)


def test_a_validation_list_is_flattened(api):
    detail = [{'loc': ['query', 'limit'], 'msg': 'Input should be a valid integer'}]
    api.on('get', '/notes/notes', FakeResponse(422, {'detail': detail}))

    result = invoke('list', 'local')

    assert result.exit_code == 1 and 'limit: Input should be a valid integer' in flat(result)
