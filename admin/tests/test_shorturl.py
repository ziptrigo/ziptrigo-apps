"""Unit tests for `admin/shorturl.py` (the Short URLs JWT API CLI, issue #162 phase 4): command
wiring, the cost confirmations and the payloads it sends, with every network call mocked. Like
`test_receipts.py`, this module never touches Django or a database -- the CLI is a plain HTTP
client over `/api/s/`. The shared plumbing (`api_request`, the token files) is tested in
`test_cli_utils.py`.
"""

import json

import pytest
from typer.testing import CliRunner

from admin import shorturl

pytestmark = pytest.mark.unit

runner = CliRunner()

ID = '11111111-1111-1111-1111-111111111111'
BASE = 'http://localhost:8000/api/s'

LINK: dict = {
    'id': ID,
    'code': 'Spring',
    'short_url': 'https://app.example/s/Spring',
    'is_alias': True,
    'destination': 'https://example.com/spring',
    'title': 'Spring sale',
    'tags': ['promo'],
    'status': 'active',
    'state': 'active',
    'expires_at': None,
    'max_clicks': 100,
    'password_protected': False,
    'filter_bots': True,
    'click_count': 12,
    'bot_click_count': 3,
    'last_clicked_at': '2026-10-10T09:00:00Z',
    'analytics_enabled': False,
    'takedown_reason': '',
    'created_at': '2026-10-01T09:00:00Z',
    'updated_at': '2026-10-01T09:00:00Z',
    'costs': {'enable': 0, 'analytics': 1, 'password': 1},
    'credits_spent': 0,
}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=None, content=b''):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.text = text if text is not None else json.dumps(self._json)
        self.content = content

    def close(self):
        pass

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda *args, **kwargs: None)


@pytest.fixture
def token_file(token_dir):
    path = token_dir / '.shorturl_token.local'
    path.write_text('tok')
    return path


@pytest.fixture
def logged_out(token_file):
    token_file.unlink()
    return token_file


class Api:
    """Records every request and answers from a table of `(method, path) -> response`."""

    def __init__(self, monkeypatch):
        self.calls: list[dict] = []
        self.routes: dict[tuple[str, str], FakeResponse] = {}
        for method in ('get', 'post', 'patch', 'delete', 'put'):
            monkeypatch.setattr(f'requests.{method}', self._handler(method))

    def _handler(self, method):
        def handle(url, **kwargs):
            self.calls.append({'method': method, 'url': url, **kwargs})
            path = url.removeprefix('http://localhost:8000/api')
            try:
                return self.routes[(method, path)]
            except KeyError:
                raise AssertionError(f'unexpected {method.upper()} {path}') from None

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


def invoke(*args, input=None):
    return runner.invoke(shorturl.app, [*args], input=input)


# -- login --


def test_login_stores_the_token_for_the_environment(logged_out, api, token_dir):
    api.on('post', '/auth/login', FakeResponse(200, {'access_token': 'tok123'}))

    result = invoke('login', 'local', 'me@example.com', 'pw')

    assert result.exit_code == 0
    assert (token_dir / '.shorturl_token.local').read_text() == 'tok123'
    assert api.calls[0]['json'] == {'email': 'me@example.com', 'password': 'pw'}


def test_login_reads_the_password_from_the_environment(logged_out, api, monkeypatch):
    api.on('post', '/auth/login', FakeResponse(200, {'access_token': 't'}))
    monkeypatch.setenv('SHORTURL_PASSWORD', 'env-pw')

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
    for args in (['list', 'local'], ['get', 'local', ID], ['create', 'local', 'https://x.example']):
        assert invoke(*args).exit_code == 1
    assert api.calls == []


def test_a_token_is_per_cli_and_environment(token_file, token_dir, api):
    (token_dir / '.shorturl_token.prod').write_text('prod-token')
    api.on(
        'get', '/s/links', FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []})
    )

    assert invoke('list', 'local').exit_code == 0
    assert api.calls[-1]['headers'] == {'Authorization': 'Bearer tok'}
    assert invoke('list', 'prod').exit_code == 0
    assert api.calls[-1]['headers'] == {'Authorization': 'Bearer prod-token'}


# -- create --


def quote(credits=0, charges=(), balance=10, affordable=True):
    return FakeResponse(
        200,
        {
            'credits': credits,
            'charges': list(charges),
            'balance': balance,
            'affordable': affordable,
        },
    )


ALIAS_CHARGE = {'extra': 'alias', 'label': 'Custom alias', 'credits': 1}


def test_a_free_create_quotes_then_creates_with_the_quoted_cost(api):
    api.on('post', '/s/links/quote', quote())
    api.on('post', '/s/links', FakeResponse(201, LINK))

    result = invoke('create', 'local', 'https://example.com/spring')

    assert result.exit_code == 0
    assert 'Created https://app.example/s/Spring' in result.output
    assert 'Charged' not in result.output
    created = api.requests('post', '/s/links')[-1]['json']
    assert created == {'destination': 'https://example.com/spring', 'expected_cost': 0}


def test_every_option_reaches_the_payload(api, monkeypatch):
    api.on('post', '/s/links/quote', quote())
    api.on('post', '/s/links', FakeResponse(201, LINK))
    monkeypatch.setattr('typer.prompt', lambda *a, **k: 'open-sesame')

    result = invoke(
        'create',
        'local',
        'https://example.com/spring',
        '--alias',
        'Spring',
        '--title',
        'Spring sale',
        '--tag',
        'promo',
        '--tag',
        'spring',
        '--expires',
        '2026-12-31',
        '--max-clicks',
        '100',
        '--password',
        '--include-bots',
        '--utm-source',
        'news',
        '--utm-medium',
        'email',
        '--utm-campaign',
        'spring',
        '--utm-term',
        'sale',
        '--utm-content',
        'hero',
    )

    assert result.exit_code == 0
    assert api.requests('post', '/s/links')[-1]['json'] == {
        'destination': 'https://example.com/spring',
        'alias': 'Spring',
        'title': 'Spring sale',
        'tags': ['promo', 'spring'],
        'expires_at': '2026-12-31',
        'max_clicks': 100,
        'filter_bots': False,
        'password': 'open-sesame',
        'utm_source': 'news',
        'utm_medium': 'email',
        'utm_campaign': 'spring',
        'utm_term': 'sale',
        'utm_content': 'hero',
        'expected_cost': 0,
    }
    # The password is asked for once and sent to the quote and the create alike.
    assert api.requests('post', '/s/links/quote')[-1]['json']['password'] == 'open-sesame'


def test_the_link_password_can_come_from_the_environment(api, monkeypatch):
    api.on('post', '/s/links/quote', quote())
    api.on('post', '/s/links', FakeResponse(201, LINK))
    monkeypatch.setenv('SHORTURL_LINK_PASSWORD', 'from-env')

    assert invoke('create', 'local', 'https://x.example', '--password').exit_code == 0
    assert api.requests('post', '/s/links')[-1]['json']['password'] == 'from-env'


def test_a_paid_create_asks_and_sends_the_confirmed_amount(api):
    api.on('post', '/s/links/quote', quote(1, [ALIAS_CHARGE]))
    api.on('post', '/s/links', FakeResponse(201, {**LINK, 'credits_spent': 1}))

    result = invoke('create', 'local', 'https://x.example', '--alias', 'Spring', input='y\n')

    assert result.exit_code == 0
    assert 'Custom alias: 1 credit' in result.output
    assert 'costs 1 credit' in result.output
    assert api.requests('post', '/s/links')[-1]['json']['expected_cost'] == 1
    assert 'Charged 1 credit.' in result.output


def test_declining_a_paid_create_creates_nothing(api):
    api.on('post', '/s/links/quote', quote(1, [ALIAS_CHARGE]))

    result = invoke('create', 'local', 'https://x.example', '--alias', 'Spring', input='n\n')

    assert result.exit_code == 1
    assert 'nothing was charged' in result.output
    assert api.requests('post', '/s/links') == [] or all(
        c['url'].endswith('/quote') for c in api.requests('post')
    )


def test_a_create_you_cannot_afford_stops_before_asking(api):
    api.on('post', '/s/links/quote', quote(1, [ALIAS_CHARGE], balance=0, affordable=False))

    result = invoke('create', 'local', 'https://x.example', '--alias', 'Spring', input='y\n')

    assert result.exit_code == 1
    assert 'you have 0' in result.output
    assert [c['url'] for c in api.calls if c['method'] == 'post'] == [f'{BASE}/links/quote']


def test_yes_skips_the_quote_and_accepts_any_cost(api):
    api.on('post', '/s/links', FakeResponse(201, {**LINK, 'credits_spent': 2}))

    result = invoke('create', 'local', 'https://x.example', '--alias', 'Spring', '--yes')

    assert result.exit_code == 0
    assert [c['url'] for c in api.calls] == [f'{BASE}/links']
    assert 'expected_cost' not in api.calls[0]['json']
    assert 'Charged 2 credits.' in result.output


def test_the_apis_refusals_are_shown(api):
    api.on(
        'post',
        '/s/links/quote',
        FakeResponse(409, {'detail': 'alias: That alias is not available.'}),
    )

    result = invoke('create', 'local', 'https://x.example', '--alias', 'admin')

    assert result.exit_code == 1
    assert '409: alias: That alias is not available.' in result.output


def test_a_cost_that_changed_is_reported_and_exits_1(api):
    api.on('post', '/s/links/quote', quote())
    api.on(
        'post',
        '/s/links',
        FakeResponse(
            409, {'detail': 'The cost changed: it is now 1 credit. Send expected_cost=1.'}
        ),
    )

    result = invoke('create', 'local', 'https://x.example')

    assert result.exit_code == 1
    assert 'The cost changed' in result.output


def test_a_422_list_is_flattened(api):
    api.on(
        'post',
        '/s/links/quote',
        FakeResponse(
            422, {'detail': [{'loc': ['body', 'payload', 'max_clicks'], 'msg': 'not a number'}]}
        ),
    )

    result = invoke('create', 'local', 'https://x.example', '--max-clicks', '5')

    assert result.exit_code == 1
    assert 'max_clicks: not a number' in result.output


def test_a_bad_expiry_is_refused_before_any_request(api):
    result = invoke('create', 'local', 'https://x.example', '--expires', 'next week')

    assert result.exit_code != 0
    assert api.calls == []


@pytest.mark.parametrize(
    'given, sent',
    [
        ('2026-12-31', '2026-12-31'),
        ('20261231', '2026-12-31'),
        ('2026-12-31T18:00:00', '2026-12-31T18:00:00'),
        ('20261231T180000', '2026-12-31T18:00:00'),
    ],
)
def test_a_bare_date_is_found_by_what_it_parses_to_not_by_its_length(api, given, sent):
    api.on('post', '/s/links/quote', quote())
    api.on('post', '/s/links', FakeResponse(201, LINK))

    invoke('create', 'local', 'https://x.example', '--expires', given)

    assert api.requests('post', '/s/links')[-1]['json']['expires_at'] == sent


def test_a_date_time_expiry_is_sent_as_given(api):
    api.on('post', '/s/links/quote', quote())
    api.on('post', '/s/links', FakeResponse(201, LINK))

    invoke('create', 'local', 'https://x.example', '--expires', '2026-12-31T18:00:00+00:00')

    assert api.requests('post', '/s/links')[-1]['json']['expires_at'] == '2026-12-31T18:00:00+00:00'


# -- quote --


def test_quote_lists_the_charges_and_the_balance(api):
    api.on('post', '/s/links/quote', quote(1, [ALIAS_CHARGE], balance=0, affordable=False))

    result = invoke('quote', 'local', 'https://x.example', '--alias', 'Spring')

    assert result.exit_code == 0
    assert 'Custom alias: 1 credit' in result.output
    assert 'Total 1 credit; balance 0 (not enough)' in result.output
    assert api.calls[-1]['json'] == {'destination': 'https://x.example', 'alias': 'Spring'}


def test_quote_of_a_free_link(api):
    api.on('post', '/s/links/quote', quote())

    result = invoke('quote', 'local', 'https://x.example')

    assert 'Free.' in result.output and 'Total 0 credits; balance 10' in result.output


# -- list / get --


def test_list_shows_the_links_and_how_to_page(api):
    api.on(
        'get',
        '/s/links',
        FakeResponse(200, {'count': 45, 'limit': 20, 'offset': 0, 'results': [LINK]}),
    )

    result = invoke('list', 'local', '--status', 'active', '--tag', 'promo', '--q', 'spring')

    assert result.exit_code == 0
    assert ID in result.output and 'https://app.example/s/Spring' in result.output
    assert 'Use --offset 1 to see more.' in result.output
    assert api.calls[-1]['params'] == {
        'status': 'active',
        'sort': '-created_at',
        'limit': 20,
        'offset': 0,
        'q': 'spring',
        'tag': 'promo',
    }


def test_list_with_nothing_says_so(api):
    api.on(
        'get', '/s/links', FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []})
    )

    assert 'No links found.' in invoke('list', 'local').output


def test_a_rejected_list_filter_is_shown(api):
    api.on('get', '/s/links', FakeResponse(400, {'detail': 'status must be one of: all, active'}))

    result = invoke('list', 'local', '--status', 'sleeping')

    assert result.exit_code == 1 and 'status must be one of' in result.output


def test_get_prints_the_link_and_what_it_would_cost(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(200, LINK))

    result = invoke('get', 'local', ID)

    assert result.exit_code == 0
    assert 'https://example.com/spring' in result.output
    assert '12 (3 flagged as bots)' in result.output
    assert 'analytics 1 credit, password 1 credit' in result.output


def test_get_of_a_missing_link_is_a_404_exit(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(404, {'detail': 'Not Found'}))

    result = invoke('get', 'local', ID)

    assert result.exit_code == 1 and '404: Not Found' in result.output


def test_an_expired_session_says_to_log_in_again(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(401, {'detail': 'Unauthorized'}))

    assert 'run `login` again' in invoke('get', 'local', ID).output


# -- update --


def test_update_sends_only_what_was_passed(api):
    api.on('patch', f'/s/links/{ID}', FakeResponse(200, LINK))

    result = invoke('update', 'local', ID, '--destination', 'https://example.com/new')

    assert result.exit_code == 0
    assert api.calls[-1]['json'] == {'destination': 'https://example.com/new'}


def test_update_can_clear_things(api):
    api.on('patch', f'/s/links/{ID}', FakeResponse(200, LINK))

    invoke(
        'update',
        'local',
        ID,
        '--clear-tags',
        '--clear-expires',
        '--clear-max-clicks',
        '--clear-password',
        '--filter-bots',
    )

    assert api.calls[-1]['json'] == {
        'tags': [],
        'expires_at': None,
        'max_clicks': None,
        'password': None,
        'filter_bots': True,
    }


def test_update_replaces_tags_and_merges_utm(api):
    api.on('patch', f'/s/links/{ID}', FakeResponse(200, LINK))

    invoke('update', 'local', ID, '--tag', 'a', '--tag', 'b', '--utm-source', 'x', '--include-bots')

    assert api.calls[-1]['json'] == {
        'tags': ['a', 'b'],
        'utm_source': 'x',
        'filter_bots': False,
    }


def test_update_with_nothing_to_change_makes_no_request(api):
    result = invoke('update', 'local', ID)

    assert result.exit_code == 1 and 'Nothing to change' in result.output
    assert api.calls == []


@pytest.mark.parametrize(
    'args',
    [
        ['--tag', 'a', '--clear-tags'],
        ['--expires', '2027-01-01', '--clear-expires'],
        ['--max-clicks', '5', '--clear-max-clicks'],
        ['--password', '--clear-password'],
    ],
)
def test_update_refuses_a_value_and_its_clear_together(api, args):
    result = invoke('update', 'local', ID, *args)

    assert result.exit_code != 0 and api.calls == []


def test_setting_a_password_asks_about_the_cost_first(api, monkeypatch):
    monkeypatch.setattr('typer.prompt', lambda *a, **k: 'pw')
    api.on('get', f'/s/links/{ID}', FakeResponse(200, LINK))
    api.on('patch', f'/s/links/{ID}', FakeResponse(200, {**LINK, 'credits_spent': 1}))

    result = invoke('update', 'local', ID, '--password', input='y\n')

    assert result.exit_code == 0
    assert 'Setting a password on this link costs 1 credit' in result.output
    assert api.calls[-1]['json'] == {'password': 'pw', 'expected_cost': 1}
    assert 'Charged 1 credit.' in result.output


def test_the_password_is_asked_after_the_price(api, monkeypatch):
    """Declining the cost must not have cost anyone a typed password."""
    asked = []
    monkeypatch.setattr('typer.prompt', lambda *a, **k: asked.append('prompt') or 'pw')
    api.on('get', f'/s/links/{ID}', FakeResponse(200, LINK))

    result = invoke('update', 'local', ID, '--password', input='n\n')

    assert result.exit_code == 1 and asked == []

    api.on('patch', f'/s/links/{ID}', FakeResponse(200, LINK))
    assert invoke('update', 'local', ID, '--password', input='y\n').exit_code == 0
    assert asked == ['prompt']


def test_a_password_that_cannot_be_set_says_so_instead_of_asking_to_pay(api, monkeypatch):
    asked = []
    monkeypatch.setattr('typer.prompt', lambda *a, **k: asked.append('prompt') or 'pw')
    api.on(
        'get',
        f'/s/links/{ID}',
        FakeResponse(200, {**LINK, 'costs': {**LINK['costs'], 'password': None}}),
    )

    result = invoke('update', 'local', ID, '--password')

    assert result.exit_code == 1
    assert 'Not possible right now: setting a password on this link' in result.output
    assert 'Continue?' not in result.output and asked == []
    assert api.requests('patch') == []


def test_a_password_already_paid_for_asks_nothing(api, monkeypatch):
    monkeypatch.setattr('typer.prompt', lambda *a, **k: 'pw')
    api.on(
        'get',
        f'/s/links/{ID}',
        FakeResponse(200, {**LINK, 'costs': {**LINK['costs'], 'password': 0}}),
    )
    api.on('patch', f'/s/links/{ID}', FakeResponse(200, LINK))

    result = invoke('update', 'local', ID, '--password')

    assert result.exit_code == 0 and 'costs' not in result.output
    assert api.calls[-1]['json']['expected_cost'] == 0


def test_declining_the_password_cost_changes_nothing(api, monkeypatch):
    monkeypatch.setattr('typer.prompt', lambda *a, **k: 'pw')
    api.on('get', f'/s/links/{ID}', FakeResponse(200, LINK))

    result = invoke('update', 'local', ID, '--password', input='n\n')

    assert result.exit_code == 1
    assert api.requests('patch') == []


def test_yes_skips_the_password_question_and_the_cost_guard(api, monkeypatch):
    monkeypatch.setattr('typer.prompt', lambda *a, **k: 'pw')
    api.on('patch', f'/s/links/{ID}', FakeResponse(200, LINK))

    assert invoke('update', 'local', ID, '--password', '--yes').exit_code == 0
    assert api.calls == [api.calls[0]] and 'expected_cost' not in api.calls[0]['json']


# -- enable / disable / delete --


def test_enable_asks_when_it_costs_and_confirms_the_amount(api):
    api.on(
        'get',
        f'/s/links/{ID}',
        FakeResponse(200, {**LINK, 'state': 'disabled', 'costs': {**LINK['costs'], 'enable': 1}}),
    )
    api.on('post', f'/s/links/{ID}/enable', FakeResponse(200, {**LINK, 'credits_spent': 1}))

    result = invoke('enable', 'local', ID, input='y\n')

    assert result.exit_code == 0
    assert 'Enabling this link costs 1 credit' in result.output
    assert api.requests('post')[-1]['params'] == {'expected_cost': 1}
    assert 'Charged 1 credit.' in result.output


def test_a_free_enable_does_not_ask(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(200, {**LINK, 'state': 'disabled'}))
    api.on('post', f'/s/links/{ID}/enable', FakeResponse(200, LINK))

    result = invoke('enable', 'local', ID)

    assert result.exit_code == 0 and 'Continue?' not in result.output
    assert api.requests('post')[-1]['params'] == {'expected_cost': 0}


def test_enabling_what_the_state_refuses_says_so_instead_of_asking_to_pay(api):
    held = {**LINK, 'state': 'disabled', 'costs': {**LINK['costs'], 'enable': None}}
    api.on('get', f'/s/links/{ID}', FakeResponse(200, held))

    result = invoke('enable', 'local', ID)

    assert result.exit_code == 1
    assert 'Not possible right now: enabling this link' in result.output
    assert 'Continue?' not in result.output and api.requests('post') == []


def test_enabling_an_active_link_says_it_is_already_active(api):
    api.on(
        'get',
        f'/s/links/{ID}',
        FakeResponse(200, {**LINK, 'costs': {**LINK['costs'], 'enable': None}}),
    )

    result = invoke('enable', 'local', ID)

    assert result.exit_code == 0 and 'already active' in result.output
    assert api.requests('post') == []


def test_enable_yes_makes_one_request_without_the_guard(api):
    api.on('post', f'/s/links/{ID}/enable', FakeResponse(200, LINK))

    assert invoke('enable', 'local', ID, '--yes').exit_code == 0
    assert len(api.calls) == 1 and api.calls[0]['params'] is None


def test_enable_a_blocked_destination_exits_1(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(200, {**LINK, 'state': 'disabled'}))
    api.on(
        'post',
        f'/s/links/{ID}/enable',
        FakeResponse(403, {'detail': 'destination: That address cannot be used.'}),
    )

    result = invoke('enable', 'local', ID)

    assert result.exit_code == 1 and '403' in result.output


def test_disable(api):
    api.on('post', f'/s/links/{ID}/disable', FakeResponse(200, {**LINK, 'state': 'disabled'}))

    result = invoke('disable', 'local', ID)

    assert result.exit_code == 0 and 'is disabled.' in result.output


def test_delete_confirms_by_name(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(200, LINK))
    api.on('delete', f'/s/links/{ID}', FakeResponse(204))

    result = invoke('delete', 'local', ID, input='y\n')

    assert result.exit_code == 0
    assert 'Delete https://app.example/s/Spring (https://example.com/spring)?' in result.output
    assert 'Deleted.' in result.output


def test_declining_a_delete_deletes_nothing(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(200, LINK))

    result = invoke('delete', 'local', ID, input='n\n')

    assert result.exit_code == 1 and api.requests('delete') == []


def test_delete_yes_needs_no_lookup(api):
    api.on('delete', f'/s/links/{ID}', FakeResponse(204))

    assert invoke('delete', 'local', ID, '--yes').exit_code == 0
    assert [c['method'] for c in api.calls] == ['delete']


def test_a_held_link_cannot_be_deleted(api):
    api.on('delete', f'/s/links/{ID}', FakeResponse(409, {'detail': 'This link is under review'}))

    result = invoke('delete', 'local', ID, '--yes')

    assert result.exit_code == 1 and 'under review' in result.output


# -- analytics / stats --


def test_analytics_that_cannot_be_opened_says_so(api):
    api.on(
        'get',
        f'/s/links/{ID}',
        FakeResponse(200, {**LINK, 'costs': {**LINK['costs'], 'analytics': None}}),
    )

    result = invoke('analytics', 'local', ID)

    assert result.exit_code == 1 and 'Not possible right now' in result.output
    assert api.requests('post') == []


def test_get_lists_what_is_not_possible(api):
    api.on(
        'get',
        f'/s/links/{ID}',
        FakeResponse(200, {**LINK, 'costs': {'enable': None, 'analytics': 0, 'password': None}}),
    )

    result = invoke('get', 'local', ID)

    assert 'Not possible now' in result.output and 'enable, password' in result.output


def test_analytics_confirms_the_price(api):
    api.on('get', f'/s/links/{ID}', FakeResponse(200, LINK))
    api.on('post', f'/s/links/{ID}/analytics', FakeResponse(200, {**LINK, 'credits_spent': 1}))

    result = invoke('analytics', 'local', ID, input='y\n')

    assert result.exit_code == 0
    assert 'Detailed analytics for this link costs 1 credit' in result.output
    assert api.requests('post')[-1]['params'] == {'expected_cost': 1}
    assert 'Detailed analytics are open.' in result.output


STATS_LOCKED = {
    'range': '30',
    'totals': {'clicks': 7, 'bot_clicks': 3, 'uniques': 5, 'filter_bots': True},
    'analytics_enabled': False,
    'analytics_cost': 1,
    'detail': None,
}
STATS_OPEN = {
    **STATS_LOCKED,
    'analytics_enabled': True,
    'analytics_cost': 0,
    'detail': {
        'start': '2026-09-21',
        'end': '2026-10-20',
        'clicks': 7,
        'uniques': 5,
        'bot_clicks': 3,
        'bucket_days': 1,
        'buckets': [
            {'start': '2026-10-19', 'label': 'Oct 19', 'clicks': 2},
            {'start': '2026-10-20', 'label': 'Oct 20', 'clicks': 5},
        ],
        'tables': [
            {
                'kind': 'referrer',
                'title': 'Top referrers',
                'rows': [
                    {'value': 'news.example', 'label': 'news.example', 'count': 5, 'percent': 71}
                ],
            },
            {'kind': 'country', 'title': 'Top countries', 'rows': []},
        ],
    },
}


def test_stats_of_a_link_without_the_detail_says_how_to_open_it(api):
    api.on('get', f'/s/links/{ID}/stats', FakeResponse(200, STATS_LOCKED))

    result = invoke('stats', 'local', ID)

    assert result.exit_code == 0
    assert 'Clicks 7' in result.output and 'Unique visitors 5' in result.output
    assert 'cost 1 credit once' in result.output
    assert api.calls[-1]['params'] == {'range': '30'}


def test_stats_with_the_detail(api):
    api.on('get', f'/s/links/{ID}/stats', FakeResponse(200, STATS_OPEN))

    result = invoke('stats', 'local', ID, '--range', '7')

    assert result.exit_code == 0
    assert 'Oct 20' in result.output and 'Top referrers' in result.output
    assert 'news.example' in result.output
    assert 'Top countries' not in result.output  # an empty list is left out
    assert api.calls[-1]['params'] == {'range': '7'}


def test_an_unknown_range_is_the_apis_400(api):
    api.on('get', f'/s/links/{ID}/stats', FakeResponse(400, {'detail': 'range must be one of: 7'}))

    assert invoke('stats', 'local', ID, '--range', 'week').exit_code == 1


# -- preview / qr --


def test_preview(api):
    api.on(
        'get',
        f'/s/links/{ID}/preview',
        FakeResponse(
            200,
            {
                'short_url': 'https://app.example/s/Spring',
                'preview_url': 'https://app.example/s/Spring+',
                'destination': 'https://example.com/spring',
                'host': 'example.com',
                'title': 'Spring sale',
                'created_at': '2026-10-01T09:00:00Z',
                'password_protected': True,
                'available': False,
            },
        ),
    )

    result = invoke('preview', 'local', ID)

    assert result.exit_code == 0
    assert 'https://app.example/s/Spring+' in result.output
    assert 'Password     yes' in result.output and 'Working now  no' in result.output


def test_qr_saves_the_png(api, tmp_path):
    api.on(
        'get',
        f'/s/links/{ID}/qr.png',
        FakeResponse(200, headers={'Content-Type': 'image/png'}, content=b'\x89PNG-bytes'),
    )
    out = tmp_path / 'spring.png'

    result = invoke('qr', 'local', ID, '--out', str(out))

    assert result.exit_code == 0
    assert out.read_bytes() == b'\x89PNG-bytes'
    assert 'Saved' in result.output


def test_qr_refuses_something_that_is_not_a_png(api, tmp_path):
    api.on(
        'get',
        f'/s/links/{ID}/qr.png',
        FakeResponse(200, headers={'Content-Type': 'text/html'}, content=b'<html>'),
    )
    out = tmp_path / 'spring.png'

    assert invoke('qr', 'local', ID, '--out', str(out)).exit_code == 1
    assert not out.exists()


def test_qr_failing_with_a_404_writes_nothing(api, tmp_path):
    api.on('get', f'/s/links/{ID}/qr.png', FakeResponse(404, {'detail': 'Not Found'}))
    out = tmp_path / 'spring.png'

    assert invoke('qr', 'local', ID, '--out', str(out)).exit_code == 1
    assert not out.exists()


def test_qr_needs_out(api):
    assert invoke('qr', 'local', ID).exit_code != 0


# -- the shared request plumbing, through a command --


def test_a_short_rate_limit_is_waited_out_and_retried(api, monkeypatch):
    slept = []
    monkeypatch.setattr('time.sleep', lambda s: slept.append(s))
    responses = iter(
        [
            FakeResponse(429, headers={'Retry-After': '3'}),
            FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []}),
        ]
    )
    monkeypatch.setattr('requests.get', lambda url, **kw: next(responses))

    assert invoke('list', 'local').exit_code == 0
    assert slept == [3.0]


def test_an_hourly_rate_limit_is_not_slept_on(api, monkeypatch):
    slept = []
    monkeypatch.setattr('time.sleep', lambda s: slept.append(s))
    monkeypatch.setattr(
        'requests.post',
        lambda url, **kw: FakeResponse(
            429, {'detail': 'Too many requests.'}, headers={'Retry-After': '2400'}
        ),
    )

    result = invoke('create', 'local', 'https://x.example', '--yes')

    assert result.exit_code == 1
    assert slept == [] and 'rate limited, try again in 40 minutes' in result.output


def test_a_connection_error_exits_1_with_a_message(api, monkeypatch):
    import requests

    def refuse(url, **kwargs):
        raise requests.exceptions.ConnectionError('connection refused')

    monkeypatch.setattr('requests.get', refuse)

    result = invoke('list', 'local')

    assert result.exit_code == 1 and 'Request failed: connection refused' in result.output
