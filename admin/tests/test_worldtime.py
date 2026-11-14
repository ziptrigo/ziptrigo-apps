"""Unit tests for `admin/worldtime.py` (the World Time JWT API CLI, issue #269): argument parsing,
the request each command sends, how a tab and a location are named, and the questions asked, with
every network call mocked. Like the other CLI tests, this module never touches Django or a
database: the CLI is a plain HTTP client over `/api/time/`.
"""

import json

import pytest
from typer.testing import CliRunner

from admin import worldtime

pytestmark = pytest.mark.unit

runner = CliRunner()

TAB_ID = '11111111-1111-1111-1111-111111111111'
TEAM_ID = '22222222-2222-2222-2222-222222222222'
LISBON_ID = '33333333-3333-3333-3333-333333333333'
TOKYO_ID = '44444444-4444-4444-4444-444444444444'

TAB = {'id': TAB_ID, 'name': 'My times', 'position': 0, 'location_count': 2}
TEAM = {'id': TEAM_ID, 'name': 'Team', 'position': 1, 'location_count': 1}
LISBON = {
    'id': LISBON_ID,
    'title': 'Lisbon',
    'name': 'Lisbon',
    'label': '',
    'region': '',
    'country': '',
    'country_code': '',
    'timezone': 'Europe/Lisbon',
    'geoname_id': None,
    'home': True,
    'position': 0,
}
TOKYO = {
    **LISBON,
    'id': TOKYO_ID,
    'title': 'Tokyo office',
    'name': 'Tokyo',
    'label': 'Tokyo office',
    'region': 'Tokyo',
    'country': 'Japan',
    'country_code': 'JP',
    'timezone': 'Asia/Tokyo',
    'geoname_id': 1850147,
    'home': False,
    'position': 1,
}
DETAIL = {'id': TAB_ID, 'name': 'My times', 'position': 0, 'locations': [LISBON, TOKYO]}
PORTO = {
    'kind': 'city',
    'value': '2735943',
    'city_id': 2735943,
    'name': 'Porto',
    'detail': 'Portugal',
    'hint': '',
}
PORTO_ALEGRE = {
    'kind': 'city',
    'value': '3452925',
    'city_id': 3452925,
    'name': 'Porto Alegre',
    'detail': 'Rio Grande do Sul, Brazil',
    'hint': '',
}
PACIFIC = {
    'kind': 'zone',
    'value': 'America/Los_Angeles',
    'city_id': None,
    'name': 'Los Angeles',
    'detail': 'Pacific Time (PST)',
    'hint': 'Time zone',
}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.text = json.dumps(self._json)

    def close(self):
        pass

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda *args, **kwargs: None)


@pytest.fixture
def token_file(token_dir):
    path = token_dir / '.worldtime_token.local'
    path.write_text('tok')
    return path


class Api:
    """Records every request and answers from a table of `(method, path) -> response`. A path is
    relative to `/api` (`/time/tabs`)."""

    def __init__(self, monkeypatch):
        self.calls: list[dict] = []
        self.routes: dict[tuple[str, str], FakeResponse] = {}
        for method in ('get', 'post', 'patch', 'delete'):
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

    def on(self, method, path, json_data=None, status=200):
        self.routes[(method, path)] = FakeResponse(status, json_data)
        return self

    def sent(self, method, path=None):
        return [
            c
            for c in self.calls
            if c['method'] == method and (path is None or c['url'].endswith(path))
        ]

    def writes(self) -> list[dict]:
        return [c for c in self.calls if c['method'] != 'get' and '/auth/' not in c['url']]


@pytest.fixture
def api(monkeypatch, token_file):
    table = Api(monkeypatch)
    table.on('get', '/time/tabs', [TAB, TEAM])
    table.on('get', f'/time/tabs/{TAB_ID}', DETAIL)
    return table


def flat(result) -> str:
    """The output with the logger's line wrapping undone."""
    return ' '.join(result.output.split())


def invoke(*args, input=None):
    return runner.invoke(worldtime.app, [*args], input=input)


class TestLogin:
    def test_it_stores_the_token_and_never_takes_a_password_argument(
        self, api, token_dir, monkeypatch
    ):
        monkeypatch.setenv('WORLDTIME_PASSWORD', 's3cret')
        api.on('post', '/auth/login', {'access_token': 'new-token'})
        result = invoke('login', 'local', 'me@example.com')
        assert result.exit_code == 0, result.output
        assert (token_dir / '.worldtime_token.local').read_text() == 'new-token'
        assert api.sent('post', '/auth/login')[0]['json'] == {
            'email': 'me@example.com',
            'password': 's3cret',
        }
        assert invoke('login', 'local', 'me@example.com', 's3cret').exit_code == 2

    def test_a_refused_login(self, api, monkeypatch):
        monkeypatch.setenv('WORLDTIME_PASSWORD', 'nope')
        api.on('post', '/auth/login', {'detail': 'Invalid credentials'}, status=401)
        result = invoke('login', 'local', 'me@example.com')
        assert result.exit_code == 1 and 'Invalid credentials' in flat(result)

    def test_commands_need_a_login(self, api, token_file):
        token_file.unlink()
        result = invoke('tabs', 'local')
        assert result.exit_code != 0 and not api.calls


class TestTabs:
    def test_list(self, api):
        result = invoke('tabs', 'local')
        assert result.exit_code == 0, result.output
        assert 'My times' in result.output and TEAM_ID in result.output

    def test_add(self, api):
        api.on('post', '/time/tabs', {**DETAIL, 'id': TEAM_ID, 'name': 'Team Europe'}, status=201)
        result = invoke('tab-add', 'local', 'Team', 'Europe')
        assert result.exit_code == 0, result.output
        assert api.sent('post', '/time/tabs')[0]['json'] == {'name': 'Team Europe'}
        assert 'Added the tab Team Europe' in flat(result)

    def test_rename_by_name_ignoring_case(self, api):
        api.on('patch', f'/time/tabs/{TEAM_ID}', {**DETAIL, 'id': TEAM_ID, 'name': 'Work'})
        result = invoke('tab-rename', 'local', 'team', 'Work')
        assert result.exit_code == 0, result.output
        assert api.sent('patch')[0]['json'] == {'name': 'Work'}
        assert 'Renamed the tab Team to Work.' in flat(result)

    def test_an_unknown_tab_names_the_ones_there_are(self, api):
        result = invoke('tab-rename', 'local', 'Nope', 'Work')
        assert result.exit_code == 1 and not api.writes()
        assert 'No tab called "Nope". Your tabs: My times, Team.' in flat(result)

    def test_delete_asks_first(self, api):
        api.on('delete', f'/time/tabs/{TEAM_ID}', status=204)
        declined = invoke('tab-delete', 'local', 'Team', input='n\n')
        assert declined.exit_code == 1 and not api.writes()
        assert 'Delete the tab Team and its 1 location(s)?' in flat(declined)
        assert invoke('tab-delete', 'local', TEAM_ID, '--yes').exit_code == 0
        assert len(api.sent('delete')) == 1

    def test_the_apis_refusal_is_shown(self, api):
        api.on('delete', f'/time/tabs/{TAB_ID}', {'detail': 'Keep at least one tab.'}, status=400)
        result = invoke('tab-delete', 'local', 'My times', '--yes')
        assert result.exit_code == 1 and 'Keep at least one tab.' in flat(result)

    def test_a_name_with_markup_is_shown_as_typed(self, api):
        hostile = '[/]'  # a closing tag with nothing to close crashes Rich when read as markup
        api.on('get', '/time/tabs', [{**TAB, 'name': hostile}])
        api.on('get', f'/time/tabs/{TAB_ID}', {**DETAIL, 'name': hostile})
        api.on('get', f'/time/tabs/{TAB_ID}/times', TestNow.MOMENT)
        for command in ('tabs', 'now', 'locations'):
            result = invoke(command, 'local')
            assert result.exit_code == 0, (command, result.output)
            assert hostile in result.output, command


class TestNow:
    MOMENT = {
        'at': '2026-12-01T12:00:00Z',
        'rows': [
            {
                'location': LISBON,
                'local': '2026-12-01T12:00',
                'zone_name': 'WET',
                'utc_offset_minutes': 0,
                'minutes_from_home': 0,
            },
            {
                'location': TOKYO,
                'local': '2026-12-01T21:00',
                'zone_name': 'JST',
                'utc_offset_minutes': 540,
                'minutes_from_home': 540,
            },
        ],
    }

    def test_the_first_tab_now(self, api):
        api.on('get', f'/time/tabs/{TAB_ID}/times', self.MOMENT)
        result = invoke('now', 'local')
        assert result.exit_code == 0, result.output
        out = flat(result)
        assert 'Lisbon (home)' in out and '12:00' in out and 'Tue 2026-12-01' in out
        assert 'Tokyo office' in out and '21:00' in out and '+9' in out and 'JST (UTC+9)' in out
        assert api.sent('get', '/times')[0].get('params') is None

    def test_another_tab_at_a_moment(self, api):
        api.on('get', f'/time/tabs/{TEAM_ID}/times', self.MOMENT)
        result = invoke('now', 'local', '--tab', 'Team', '--at', '2026-11-20T15:00')
        assert result.exit_code == 0, result.output
        assert api.sent('get', '/times')[0]['params'] == {'at': '2026-11-20T15:00'}
        # An offset travels as a parameter, so the HTTP library encodes its `+`.
        invoke('now', 'local', '--at', '2026-11-20T15:00+02:00')
        assert api.sent('get', '/times')[-1]['params'] == {'at': '2026-11-20T15:00+02:00'}

    def test_offsets(self):
        assert [worldtime._offset(m) for m in (0, 540, -300, 345, -210)] == [
            '+0', '+9', '-5', '+5:45', '-3:30',
        ]  # fmt: skip

    def test_locations(self, api):
        result = invoke('locations', 'local')
        out = flat(result)
        assert result.exit_code == 0 and LISBON_ID in out and TOKYO_ID in out
        assert 'Tokyo, Tokyo, Japan' in out and 'Europe/Lisbon' in out


class TestSearchAndAdd:
    def test_search(self, api):
        api.on('get', '/time/search', [PORTO, PACIFIC])
        result = invoke('search', 'local', 'porto')
        out = flat(result)
        assert result.exit_code == 0 and api.sent('get', '/search')[0]['params'] == {'q': 'porto'}
        assert 'Porto, Portugal' in out and '--city 2735943' in out
        assert '--zone America/Los_Angeles' in out and '(Time zone)' in out

    def test_nothing_found(self, api):
        api.on('get', '/time/search', [])
        assert 'No place matches.' in flat(invoke('search', 'local', 'zzzz'))
        result = invoke('add', 'local', 'zzzz')
        assert result.exit_code == 1 and not api.writes()

    def test_add_the_only_match(self, api):
        api.on('get', '/time/search', [PORTO])
        api.on('post', f'/time/tabs/{TAB_ID}/locations', {**TOKYO, 'title': 'Porto'}, status=201)
        result = invoke('add', 'local', 'porto')
        assert result.exit_code == 0, result.output
        assert api.sent('post')[0]['json'] == {'city': 2735943}

    def test_several_matches_ask_which(self, api):
        api.on('get', '/time/search', [PORTO, PORTO_ALEGRE, PACIFIC])
        api.on('post', f'/time/tabs/{TEAM_ID}/locations', {**TOKYO, 'title': 'LA'}, status=201)
        result = invoke('add', 'local', 'po', '--tab', 'Team', '--label', 'LA', input='3\n')
        assert result.exit_code == 0, result.output
        assert '2. Porto Alegre, Rio Grande do Sul, Brazil' in flat(result)
        assert api.sent('post')[0]['json'] == {'zone': 'America/Los_Angeles', 'label': 'LA'}

    def test_first_skips_the_question_and_a_bad_number_adds_nothing(self, api):
        api.on('get', '/time/search', [PORTO, PORTO_ALEGRE])
        api.on('post', f'/time/tabs/{TAB_ID}/locations', TOKYO, status=201)
        assert invoke('add', 'local', 'porto', '--first').exit_code == 0
        assert api.sent('post')[0]['json'] == {'city': 2735943}
        api.calls.clear()
        for answer in ('9\n', '0\n'):
            result = invoke('add', 'local', 'porto', input=answer)
            assert result.exit_code == 1 and not api.writes()
        assert 'Which one (0 to cancel)' in flat(result)

    def test_add_by_city_or_zone(self, api):
        api.on('post', f'/time/tabs/{TAB_ID}/locations', TOKYO, status=201)
        assert invoke('add', 'local', '--zone', 'UTC').exit_code == 0
        assert invoke('add', 'local', '--city', '5').exit_code == 0
        assert [call['json'] for call in api.sent('post')] == [{'zone': 'UTC'}, {'city': 5}]

    @pytest.mark.parametrize(
        'args', [(), ('porto', '--zone', 'UTC'), ('--city', '5', '--zone', 'UTC')]
    )
    def test_one_of_the_three(self, api, args):
        result = invoke('add', 'local', *args)
        assert result.exit_code == 1 and not api.writes()
        assert 'one of the three' in flat(result)


class TestLocations:
    def test_label_by_place_name_or_title(self, api):
        api.on('patch', f'/time/tabs/{TAB_ID}/locations/{TOKYO_ID}', {**TOKYO, 'title': 'HQ'})
        result = invoke('label', 'local', 'tokyo', 'HQ')
        assert result.exit_code == 0, result.output
        assert api.sent('patch')[0]['json'] == {'label': 'HQ'}
        assert 'Tokyo office is now called HQ.' in flat(result)
        assert invoke('label', 'local', 'Tokyo office').exit_code == 0
        assert api.sent('patch')[1]['json'] == {'label': ''}

    def test_home(self, api):
        api.on('patch', f'/time/tabs/{TAB_ID}/locations/{TOKYO_ID}', {**TOKYO, 'home': True})
        result = invoke('home', 'local', TOKYO_ID)
        assert result.exit_code == 0 and api.sent('patch')[0]['json'] == {'home': True}

    def test_remove_asks_first(self, api):
        api.on('delete', f'/time/tabs/{TAB_ID}/locations/{TOKYO_ID}', status=204)
        assert invoke('remove', 'local', 'Tokyo', input='n\n').exit_code == 1
        assert not api.writes()
        assert invoke('remove', 'local', 'Tokyo', '--yes').exit_code == 0
        assert len(api.sent('delete')) == 1

    def test_an_unknown_location_names_the_ones_there_are(self, api):
        result = invoke('remove', 'local', 'Paris', '--yes')
        assert result.exit_code == 1 and not api.writes()
        assert 'No location called "Paris" in the tab My times. It has: Lisbon, Tokyo office.' in (
            flat(result)
        )

    def test_an_ambiguous_name_asks_for_the_id(self, api):
        twin = {**TOKYO, 'id': '55555555-5555-5555-5555-555555555555', 'title': 'Tokyo'}
        api.on('get', f'/time/tabs/{TAB_ID}', {**DETAIL, 'locations': [LISBON, TOKYO, twin]})
        result = invoke('home', 'local', 'Tokyo')
        assert result.exit_code == 1 and 'names 2 locations' in flat(result)

    def test_reorder(self, api):
        api.on('post', f'/time/tabs/{TAB_ID}/locations/reorder', [TOKYO, LISBON])
        result = invoke('reorder', 'local', 'Tokyo', 'Lisbon')
        assert result.exit_code == 0, result.output
        assert api.sent('post')[0]['json'] == {'ids': [TOKYO_ID, LISBON_ID]}
        assert 'My times: Tokyo office, Lisbon' in flat(result)
