#!python
"""
World Time CLI

Command line interface for the World Time JWT API (`/api/time/`, issue #269), on the model of
`admin/notes.py` and `admin/todo.py`: log in, list tabs, see what time it is (or will be) in every
location of a tab, search for places, and add, label, reorder and remove locations.

A tab is named by its name or its id (the first tab when left out); a location by what its row is
called, its place name or its id. World Time is free, so nothing here costs credits.
"""

from datetime import datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from .utils import (
    EnvironmentAnnotation,
    api_error_text,
    api_request,
    api_url,
    get_headers,
    logger,
    password_from_prompt_or_env,
    save_token,
    select_environment,
    token_file,
)

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

# The token lives in `~/.worldtime_token.<environment>` (`admin.utils.token_file`).
CLI = 'worldtime'
PASSWORD_ENV = 'WORLDTIME_PASSWORD'

TabOption = Annotated[
    str | None,
    typer.Option('--tab', help='The tab, by name or id. Default: your first tab.'),
]
YesAnnotation = Annotated[bool, typer.Option('--yes', help='Do not ask before deleting.')]

WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


# -- Plumbing ------------------------------------------------------------------------------------


def _say(message: str) -> None:
    """Log `message` as it is: the logger renders Rich markup, which a name may contain."""
    logger.info(escape(message))


def _fail(message: str) -> typer.Exit:
    logger.error(escape(message))
    return typer.Exit(1)


def _console() -> Console:
    """A console for tables: the terminal's own width, or a wide one when output is piped."""
    console = Console()
    return console if console.is_terminal else Console(width=200)


def _request(method: str, path: str, **kwargs):
    """`api_request` to `/api/time<path>` with the stored token."""
    return api_request(method, api_url(f'/time{path}'), headers=get_headers(CLI), **kwargs)


def _call(method: str, path: str, **kwargs):
    """A request that must succeed; its JSON, or `None` for a 204."""
    response = _request(method, path, **kwargs)
    if not response.ok:
        raise _fail(api_error_text(response))
    return None if response.status_code == 204 else response.json()


def _confirm(question: str, yes: bool) -> None:
    if not yes and not typer.confirm(question):
        raise _fail('Cancelled; nothing was changed.')


def _same(one: str, other: str) -> bool:
    return one.strip().casefold() == other.strip().casefold()


def _find_tab(reference: str | None) -> dict:
    """The tab `reference` names (its id, or its name ignoring case); the first tab without one."""
    tabs = _call('get', '/tabs')
    if not tabs:
        raise _fail('You have no tabs.')
    if reference is None:
        return tabs[0]
    matches = [tab for tab in tabs if tab['id'] == reference or _same(tab['name'], reference)]
    if not matches:
        names = ', '.join(tab['name'] for tab in tabs)
        raise _fail(f'No tab called "{reference}". Your tabs: {names}.')
    return matches[0]


def _tab_detail(reference: str | None) -> dict:
    return _call('get', f'/tabs/{_find_tab(reference)["id"]}')


def _find_location(tab: dict, reference: str) -> dict:
    """The row of `tab` that `reference` names: its id, what it is called, or its place name."""
    rows = tab['locations']
    matches = [
        row
        for row in rows
        if row['id'] == reference or _same(row['title'], reference) or _same(row['name'], reference)
    ]
    if not matches:
        names = ', '.join(row['title'] for row in rows) or 'none'
        raise _fail(f'No location called "{reference}" in the tab {tab["name"]}. It has: {names}.')
    if len(matches) > 1:
        raise _fail(
            f'"{reference}" names {len(matches)} locations of the tab {tab["name"]}. '
            'Use the id shown by `locations`.'
        )
    return matches[0]


def _offset(minutes: int, prefix: str = '') -> str:
    """`+5`, `-3:30`, `+0` (with a prefix: `UTC+5`)."""
    sign = '-' if minutes < 0 else '+'
    hours, rest = divmod(abs(minutes), 60)
    return f'{prefix}{sign}{hours}' + (f':{rest:02d}' if rest else '')


def _place(row: dict) -> str:
    """`Austin, Texas, United States`, or the zone for a row added as a time zone."""
    if row['geoname_id'] is None:
        return row['timezone']
    return ', '.join(part for part in (row['name'], row['region'], row['country']) if part)


def _result_text(result: dict) -> str:
    text = result['name'] + (f', {result["detail"]}' if result['detail'] else '')
    return text + (f' ({result["hint"]})' if result['hint'] else '')


# -- Commands: account ---------------------------------------------------------------------------


@app.command(name='login')
def worldtime_login(
    environment: EnvironmentAnnotation,
    email: str,
):
    """
    Authenticate with the API and store the token. The password is read from the
    WORLDTIME_PASSWORD environment variable, else asked for with the echo hidden: it is never an
    argument (it would stay in the shell's history).

    Example:
        worldtime login local me@example.com
    """
    select_environment(environment)
    password = password_from_prompt_or_env(None, PASSWORD_ENV)

    response = api_request(
        'post', api_url('/auth/login'), json={'email': email, 'password': password}
    )
    if not response.ok:
        raise _fail(api_error_text(response, hints=False))
    try:
        save_token(CLI, response.json()['access_token'])
    except OSError as e:
        raise _fail(f'Could not save the token to {token_file(CLI)}: {e}') from e
    logger.info('Successfully authenticated!')


# -- Commands: tabs ------------------------------------------------------------------------------


@app.command(name='tabs')
def worldtime_tabs(environment: EnvironmentAnnotation):
    """
    List your tabs.

    Example:
        worldtime tabs local
    """
    select_environment(environment)
    tabs = _call('get', '/tabs')
    table = Table(title='Tabs')
    table.add_column('Name')
    table.add_column('Locations', justify='right')
    table.add_column('Id', no_wrap=True)
    for tab in tabs:
        table.add_row(Text(tab['name']), str(tab['location_count']), tab['id'])
    _console().print(table)


@app.command(name='tab-add')
def worldtime_tab_add(
    environment: EnvironmentAnnotation,
    name: Annotated[list[str], typer.Argument(help='The name of the new tab.')],
):
    """
    Add a tab. It starts with one location, your account's time zone, as its home.

    Example:
        worldtime tab-add local Team Europe
    """
    select_environment(environment)
    tab = _call('post', '/tabs', json={'name': ' '.join(name)})
    _say(f'Added the tab {tab["name"]} ({tab["id"]}).')


@app.command(name='tab-rename')
def worldtime_tab_rename(
    environment: EnvironmentAnnotation,
    tab: Annotated[str, typer.Argument(help='The tab, by name or id.')],
    name: Annotated[list[str], typer.Argument(help='Its new name.')],
):
    """
    Rename a tab.

    Example:
        worldtime tab-rename local Team "Team Europe"
    """
    select_environment(environment)
    found = _find_tab(tab)
    renamed = _call('patch', f'/tabs/{found["id"]}', json={'name': ' '.join(name)})
    _say(f'Renamed the tab {found["name"]} to {renamed["name"]}.')


@app.command(name='tab-delete')
def worldtime_tab_delete(
    environment: EnvironmentAnnotation,
    tab: Annotated[str, typer.Argument(help='The tab, by name or id.')],
    yes: YesAnnotation = False,
):
    """
    Delete a tab and its locations (asks first; `--yes` skips the question). The last tab can't
    be deleted.

    Example:
        worldtime tab-delete local Family
    """
    select_environment(environment)
    found = _find_tab(tab)
    _confirm(f'Delete the tab {found["name"]} and its {found["location_count"]} location(s)?', yes)
    _call('delete', f'/tabs/{found["id"]}')
    _say(f'Deleted the tab {found["name"]}.')


# -- Commands: times -----------------------------------------------------------------------------


@app.command(name='now')
def worldtime_now(
    environment: EnvironmentAnnotation,
    tab: TabOption = None,
    at: Annotated[
        str | None,
        typer.Option(
            '--at',
            help="Another moment instead of now: `2026-11-20T15:00` on the home location's "
            'clock, or with an offset (`2026-11-20T15:00+01:00`, `...Z`).',
        ),
    ] = None,
):
    """
    What time it is in every location of a tab, now or at `--at`.

    Examples:
        worldtime now local
        worldtime now local --tab Team --at 2026-11-20T15:00
    """
    select_environment(environment)
    found = _find_tab(tab)
    moment = _call('get', f'/tabs/{found["id"]}/times', params={'at': at} if at else None)
    table = Table(title=found['name'])
    table.add_column('Location')
    table.add_column('Time', justify='right', no_wrap=True)
    table.add_column('Date', no_wrap=True)
    table.add_column('From home', justify='right')
    table.add_column('Zone')
    for row in moment['rows']:
        local = datetime.fromisoformat(row['local'])
        location = row['location']
        table.add_row(
            Text(location['title'] + (' (home)' if location['home'] else '')),
            f'{local:%H:%M}',
            f'{WEEKDAYS[local.weekday()]} {local.date().isoformat()}',
            '' if location['home'] else _offset(row['minutes_from_home']),
            f'{row["zone_name"]} ({_offset(row["utc_offset_minutes"], "UTC")})',
        )
    _console().print(table)


@app.command(name='locations')
def worldtime_locations(environment: EnvironmentAnnotation, tab: TabOption = None):
    """
    The locations of a tab, in order, with their ids.

    Example:
        worldtime locations local --tab Team
    """
    select_environment(environment)
    detail = _tab_detail(tab)
    table = Table(title=detail['name'])
    table.add_column('Location')
    table.add_column('Place')
    table.add_column('Time zone')
    table.add_column('Id', no_wrap=True)
    for row in detail['locations']:
        table.add_row(
            Text(row['title'] + (' (home)' if row['home'] else '')),
            Text(_place(row)),
            row['timezone'],
            row['id'],
        )
    _console().print(table)


# -- Commands: search and locations --------------------------------------------------------------


@app.command(name='search')
def worldtime_search(
    environment: EnvironmentAnnotation,
    query: Annotated[list[str], typer.Argument(help='A city, a time zone or an abbreviation.')],
):
    """
    What the location search offers for some text: cities (by name, or another name such as
    Lisboa), time zones by name (`Europe/Lisbon`, `UTC`) and by abbreviation (`PST`).

    Examples:
        worldtime search local porto
        worldtime search local PST
    """
    select_environment(environment)
    results = _call('get', '/search', params={'q': ' '.join(query)})
    if not results:
        _say('No place matches.')
        return
    table = Table(title='Places')
    table.add_column('#', justify='right')
    table.add_column('Place')
    table.add_column('Kind')
    table.add_column('Add with')
    for index, result in enumerate(results, start=1):
        option = '--city' if result['kind'] == 'city' else '--zone'
        table.add_row(
            str(index), Text(_result_text(result)), result['kind'], f'{option} {result["value"]}'
        )
    _console().print(table)


@app.command(name='add')
def worldtime_add(
    environment: EnvironmentAnnotation,
    query: Annotated[
        list[str] | None,
        typer.Argument(help='What to search for. Leave out with --city or --zone.'),
    ] = None,
    tab: TabOption = None,
    city: Annotated[
        int | None, typer.Option('--city', help='A GeoNames id, as `search` shows it.')
    ] = None,
    zone: Annotated[
        str | None, typer.Option('--zone', help='A time zone: `Europe/Lisbon`, `UTC`.')
    ] = None,
    label: Annotated[str, typer.Option('--label', help='Your own name for the row.')] = '',
    first: Annotated[
        bool, typer.Option('--first', help='Take the first match without asking.')
    ] = False,
):
    """
    Add a location to a tab: search for it (when several places match you choose one, or pass
    `--first`), or name it exactly with `--city` or `--zone`.

    Examples:
        worldtime add local porto
        worldtime add local austin --tab Team --label "Austin office" --first
        worldtime add local --zone UTC
    """
    select_environment(environment)
    text = ' '.join(query or [])
    given = sum(1 for value in (text, city, zone) if value)
    if given != 1:
        raise _fail('Give some text to search for, or --city, or --zone: one of the three.')
    found = _find_tab(tab)
    if text:
        results = _call('get', '/search', params={'q': text})
        if not results:
            raise _fail(f'No place matches "{text}".')
        choice = results[0]
        if len(results) > 1 and not first:
            for index, result in enumerate(results, start=1):
                _say(f'{index}. {_result_text(result)}')
            number = typer.prompt('Which one', type=int, default=1)
            if not 1 <= number <= len(results):
                raise _fail('Cancelled; nothing was added.')
            choice = results[number - 1]
        key = 'city' if choice['kind'] == 'city' else 'zone'
        body: dict = {key: int(choice['value']) if key == 'city' else choice['value']}
    else:
        body = {'city': city} if city else {'zone': zone}
    if label:
        body['label'] = label
    row = _call('post', f'/tabs/{found["id"]}/locations', json=body)
    _say(f'Added {row["title"]} ({_place(row)}) to the tab {found["name"]}.')


@app.command(name='label')
def worldtime_label(
    environment: EnvironmentAnnotation,
    location: Annotated[str, typer.Argument(help='The location, by name or id.')],
    label: Annotated[
        list[str] | None, typer.Argument(help='Your name for it. Leave out to clear it.')
    ] = None,
    tab: TabOption = None,
):
    """
    Give a location your own name, or clear it (the place's name shows again).

    Examples:
        worldtime label local Austin Austin office
        worldtime label local "Austin office"
    """
    select_environment(environment)
    detail = _tab_detail(tab)
    row = _find_location(detail, location)
    changed = _call(
        'patch',
        f'/tabs/{detail["id"]}/locations/{row["id"]}',
        json={'label': ' '.join(label or [])},
    )
    _say(f'{row["title"]} is now called {changed["title"]}.')


@app.command(name='home')
def worldtime_home(
    environment: EnvironmentAnnotation,
    location: Annotated[str, typer.Argument(help='The location, by name or id.')],
    tab: TabOption = None,
):
    """
    Make a location the home of its tab: the row the others are compared with.

    Example:
        worldtime home local Tokyo --tab Team
    """
    select_environment(environment)
    detail = _tab_detail(tab)
    row = _find_location(detail, location)
    _call('patch', f'/tabs/{detail["id"]}/locations/{row["id"]}', json={'home': True})
    _say(f'{row["title"]} is now the home of the tab {detail["name"]}.')


@app.command(name='remove')
def worldtime_remove(
    environment: EnvironmentAnnotation,
    location: Annotated[str, typer.Argument(help='The location, by name or id.')],
    tab: TabOption = None,
    yes: YesAnnotation = False,
):
    """
    Remove a location from a tab (asks first; `--yes` skips the question).

    Example:
        worldtime remove local "Los Angeles" --tab Team
    """
    select_environment(environment)
    detail = _tab_detail(tab)
    row = _find_location(detail, location)
    _confirm(f'Remove {row["title"]} from the tab {detail["name"]}?', yes)
    _call('delete', f'/tabs/{detail["id"]}/locations/{row["id"]}')
    _say(f'Removed {row["title"]} from the tab {detail["name"]}.')


@app.command(name='reorder')
def worldtime_reorder(
    environment: EnvironmentAnnotation,
    locations: Annotated[
        list[str], typer.Argument(help='The locations in their new order, by name or id.')
    ],
    tab: TabOption = None,
):
    """
    Put the locations of a tab in a new order. Locations you leave out keep their place among
    the others.

    Example:
        worldtime reorder local Tokyo Lisbon "New York" --tab Team
    """
    select_environment(environment)
    detail = _tab_detail(tab)
    ids = [_find_location(detail, name)['id'] for name in locations]
    rows = _call('post', f'/tabs/{detail["id"]}/locations/reorder', json={'ids': ids})
    _say(f'{detail["name"]}: ' + ', '.join(row['title'] for row in rows))


if __name__ == '__main__':
    app()
