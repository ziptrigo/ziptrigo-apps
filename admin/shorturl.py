#!python
"""
Short URLs CLI

Command line interface for the Short URLs JWT API (`/api/s/`, issue #162 phase 4), on the model of
`admin/receipts.py`: log in, create short links (with an alias, tags, UTM parameters, an expiry, a
click limit, a password), list and inspect them, change them, enable, disable and delete them, read
their statistics, and save a link's QR code as an image.

Creating and following a link is free; an alias, a password, the detailed statistics and every
active link beyond the free allowance cost credits once per link. Commands that can charge say so
and ask first (`--yes` skips the question, and then accepts whatever it costs).
"""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .utils import (
    EnvironmentAnnotation,
    api_request,
    api_url,
    get_headers,
    logger,
    password_from_prompt_or_env,
    raise_for_api_error,
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

# The token lives in `~/.shorturl_token.<environment>` (`admin.utils.token_file`).
CLI = 'shorturl'
PASSWORD_ENV = 'SHORTURL_PASSWORD'
LINK_PASSWORD_ENV = 'SHORTURL_LINK_PASSWORD'

YesAnnotation = Annotated[
    bool,
    typer.Option(
        '--yes', help='Do not ask before spending credits or deleting; accept whatever it costs.'
    ),
]


def _link_url(link_id: str, suffix: str = '') -> str:
    return api_url(f'/s/links/{link_id}{suffix}')


def _expiry(value: str) -> str:
    """`--expires` as the API wants it, normalized to extended ISO 8601. A bare date is sent as a
    date and the server reads it as the end of that day; a time without an offset is read in the
    server's time zone."""
    value = value.strip()
    try:
        # A date first: `datetime.fromisoformat` accepts a bare date too (as midnight).
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        raise typer.BadParameter(
            'use an ISO date or date-time, like 2026-12-31 or 2026-12-31T18:00:00+00:00.'
        ) from None


def _link_password() -> str:
    """The password for the link itself, never taken from the command line (it would sit in the
    shell history): the `SHORTURL_LINK_PASSWORD` variable, else a hidden prompt."""
    return password_from_prompt_or_env(None, LINK_PASSWORD_ENV)


def _utm(source, medium, campaign, term, content) -> dict[str, str]:
    values = {
        'utm_source': source,
        'utm_medium': medium,
        'utm_campaign': campaign,
        'utm_term': term,
        'utm_content': content,
    }
    return {name: value for name, value in values.items() if value}


def _console() -> Console:
    """A console for tables: the terminal's own width, or a wide one when output is piped (rich
    would otherwise assume 80 columns and cut the id and the link short)."""
    console = Console()
    return console if console.is_terminal else Console(width=160)


def _credits(amount: int) -> str:
    return f'{amount} credit{"" if amount == 1 else "s"}'


def _confirm_spend(cost: int | None, what: str, yes: bool) -> int | None:
    """Ask before spending `cost` credits on `what`. Returns the `expected_cost` to send (the
    confirmed amount), or `None` with `--yes` (accept any cost); exits 1 if declined. A `cost` of
    `None` is what the API reports for an action the link's state refuses: say so and exit 1
    rather than ask to pay for something that can't happen."""
    if yes:
        return None
    if cost is None:
        logger.error(f'Not possible right now: {what[0].lower()}{what[1:]}.')
        raise typer.Exit(1)
    if cost > 0 and not typer.confirm(f'{what} costs {_credits(cost)}. Continue?'):
        logger.error('Cancelled; nothing was charged.')
        raise typer.Exit(1)
    return cost


def _print_link(link: dict) -> None:
    rows = [
        ('ID', link['id']),
        ('Short link', link['short_url']),
        ('Destination', link['destination']),
        ('Title', link['title']),
        ('Tags', ', '.join(link['tags'])),
        ('State', link['state']),
        ('Expires', link['expires_at'] or ''),
        ('Click limit', str(link['max_clicks'] or '')),
        ('Password', 'yes' if link['password_protected'] else ''),
        ('Bots', 'counted' if not link['filter_bots'] else 'left out of the counts'),
        ('Clicks', f'{link["click_count"]} ({link["bot_click_count"]} flagged as bots)'),
        ('Last click', link['last_clicked_at'] or ''),
        ('Detailed analytics', 'open' if link['analytics_enabled'] else 'not open'),
        ('Created', link['created_at']),
    ]
    if link['takedown_reason']:
        rows.append(('Taken down', link['takedown_reason']))
    costs = link.get('costs')
    if costs:
        actions = (
            ('enable', costs['enable']),
            ('analytics', costs['analytics']),
            ('password', costs['password']),
        )
        wanted = [f'{name} {_credits(amount)}' for name, amount in actions if amount]
        if wanted:
            rows.append(('Would cost', ', '.join(wanted)))
        # `null`: the link's state refuses the action (not the same as free).
        refused = [name for name, amount in actions if amount is None]
        if refused:
            rows.append(('Not possible now', ', '.join(refused)))
    table = Table(show_header=False, box=None)
    table.add_column(style='cyan', no_wrap=True)
    table.add_column()
    for label, value in rows:
        if value != '':
            table.add_row(label, value)
    _console().print(table)


def _spent(link: dict) -> None:
    if link.get('credits_spent'):
        logger.info(f'Charged {_credits(link["credits_spent"])}.')


def _fetch_link(link_id: str) -> dict:
    response = api_request('get', _link_url(link_id), headers=get_headers(CLI))
    raise_for_api_error(response)
    return response.json()


def _create_payload(
    url, alias, title, tags, expires, max_clicks, include_bots, with_password, utm
) -> dict:
    payload: dict = {'destination': url, **utm}
    for name, value in (('alias', alias), ('title', title)):
        if value:
            payload[name] = value
    if tags:
        payload['tags'] = tags
    if expires:
        payload['expires_at'] = _expiry(expires)
    if max_clicks is not None:
        payload['max_clicks'] = max_clicks
    if include_bots:
        payload['filter_bots'] = False
    if with_password:
        payload['password'] = _link_password()
    return payload


@app.command(name='login')
def shorturl_login(
    environment: EnvironmentAnnotation,
    email: str,
    password: str | None = typer.Argument(
        None, help=f'Password. Omit to be prompted, or set {PASSWORD_ENV}.'
    ),
):
    """
    Authenticate with the API and store the token.

    Example:
        shorturl login local me@example.com
    """
    select_environment(environment)
    password = password_from_prompt_or_env(password, PASSWORD_ENV)

    response = api_request(
        'post', api_url('/auth/login'), json={'email': email, 'password': password}
    )
    raise_for_api_error(response, hints=False)
    try:
        save_token(CLI, response.json()['access_token'])
    except OSError as e:
        logger.error(f'Could not save the token to {token_file(CLI)}: {e}')
        raise typer.Exit(1) from e
    logger.info('Successfully authenticated!')


@app.command(name='create')
def shorturl_create(
    environment: EnvironmentAnnotation,
    url: str = typer.Argument(..., help='The address to shorten (http or https).'),
    alias: str = typer.Option('', '--alias', help='A custom code, 3-32 letters/digits/-/_ (paid).'),
    title: str = typer.Option('', '--title', help='A title (fetched from the page if omitted).'),
    tags: list[str] | None = typer.Option(None, '--tag', help='A tag; repeat for several.'),
    expires: str | None = typer.Option(None, '--expires', help='ISO date or date-time.'),
    max_clicks: int | None = typer.Option(
        None, '--max-clicks', help='End the link after N clicks.'
    ),
    password: bool = typer.Option(
        False,
        '--password',
        help=f'Protect it with a password (paid), asked with a hidden prompt or {LINK_PASSWORD_ENV}.',
    ),
    include_bots: bool = typer.Option(
        False, '--include-bots', help='Count bots and link previews too.'
    ),
    utm_source: str = typer.Option('', '--utm-source'),
    utm_medium: str = typer.Option('', '--utm-medium'),
    utm_campaign: str = typer.Option('', '--utm-campaign'),
    utm_term: str = typer.Option('', '--utm-term'),
    utm_content: str = typer.Option('', '--utm-content'),
    yes: YesAnnotation = False,
):
    """
    Create a short link. Free, unless it uses a paid extra: the price is shown and confirmed first.

    Example:
        shorturl create local https://example.com/very/long/address
        shorturl create prod https://example.com --alias spring-sale --tag promo --max-clicks 100
        shorturl create prod https://example.com --utm-source news --utm-campaign spring --yes
    """
    select_environment(environment)
    headers = get_headers(CLI)
    payload = _create_payload(
        url,
        alias,
        title,
        tags,
        expires,
        max_clicks,
        include_bots,
        password,
        _utm(utm_source, utm_medium, utm_campaign, utm_term, utm_content),
    )

    if not yes:
        response = api_request('post', api_url('/s/links/quote'), json=payload, headers=headers)
        raise_for_api_error(response)
        quote = response.json()
        if quote['credits']:
            for charge in quote['charges']:
                logger.info(f'{charge["label"]}: {_credits(charge["credits"])}')
            if not quote['affordable']:
                logger.error(
                    f'That costs {_credits(quote["credits"])} and you have {quote["balance"]}. '
                    'Top up your credits on the website.'
                )
                raise typer.Exit(1)
        payload['expected_cost'] = _confirm_spend(quote['credits'], 'Creating this link', yes)

    response = api_request('post', api_url('/s/links'), json=payload, headers=headers)
    raise_for_api_error(response)
    link = response.json()
    logger.info(f'Created {link["short_url"]} -> {link["destination"]}')
    logger.info(f'ID: {link["id"]}')
    _spent(link)


@app.command(name='quote')
def shorturl_quote(
    environment: EnvironmentAnnotation,
    url: str = typer.Argument(..., help='The address to shorten.'),
    alias: str = typer.Option('', '--alias'),
    password: bool = typer.Option(False, '--password', help='Price a link with a password.'),
):
    """
    Say what creating a link would cost, without creating it.

    Example:
        shorturl quote local https://example.com --alias spring-sale
    """
    select_environment(environment)
    payload: dict = {'destination': url}
    if alias:
        payload['alias'] = alias
    if password:
        payload['password'] = 'x'
    response = api_request(
        'post', api_url('/s/links/quote'), json=payload, headers=get_headers(CLI)
    )
    raise_for_api_error(response)
    quote = response.json()
    if not quote['charges']:
        logger.info('Free.')
    for charge in quote['charges']:
        logger.info(f'{charge["label"]}: {_credits(charge["credits"])}')
    logger.info(
        f'Total {_credits(quote["credits"])}; balance {quote["balance"]}'
        + ('' if quote['affordable'] else ' (not enough)')
    )


@app.command(name='list')
def shorturl_list(
    environment: EnvironmentAnnotation,
    status: str = typer.Option(
        'all',
        '--status',
        help='all, active, disabled, expired, capped or taken_down.',
    ),
    tag: str = typer.Option('', '--tag', help='Only links with this tag.'),
    q: str = typer.Option('', '--q', help='Search the code, destination, title and tags.'),
    sort: str = typer.Option(
        '-created_at', '--sort', help='-created_at, created_at, -click_count or click_count.'
    ),
    limit: int = typer.Option(20, '--limit', help='Max links to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of links to skip.'),
):
    """
    List your links.

    Example:
        shorturl list local
        shorturl list local --status active --tag promo --sort=-click_count
    """
    select_environment(environment)
    params: dict = {'status': status, 'sort': sort, 'limit': limit, 'offset': offset}
    for name, value in (('q', q), ('tag', tag)):
        if value:
            params[name] = value
    response = api_request('get', api_url('/s/links'), params=params, headers=get_headers(CLI))
    raise_for_api_error(response)
    body = response.json()
    links = body['results']
    if not links:
        logger.info('No links found.')
        return

    table = Table(title='Your Links')
    # The full id is what `get` and the other commands take, so it is never the column that shrinks.
    table.add_column('ID', style='cyan', no_wrap=True, min_width=36)
    table.add_column('Short link', no_wrap=True)
    table.add_column('Destination', overflow='ellipsis', max_width=48, no_wrap=True)
    table.add_column('Title', overflow='ellipsis', max_width=24, no_wrap=True)
    table.add_column('State', style='green', no_wrap=True)
    table.add_column('Clicks', style='magenta', justify='right')
    table.add_column('Created', no_wrap=True)
    for link in links:
        table.add_row(
            link['id'],
            link['short_url'],
            link['destination'],
            link['title'],
            link['state'],
            str(link['click_count']),
            link['created_at'][:10],
        )
    _console().print(table)

    shown_through = body['offset'] + len(links)
    if shown_through < body['count']:
        logger.info(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]}. '
            f'Use --offset {shown_through} to see more.'
        )


@app.command(name='get')
def shorturl_get(environment: EnvironmentAnnotation, link_id: str):
    """
    Show everything about one link, and what its next paid actions would cost.

    Example:
        shorturl get local 1b6e2f4a-...
    """
    select_environment(environment)
    _print_link(_fetch_link(link_id))


@app.command(name='update')
def shorturl_update(
    environment: EnvironmentAnnotation,
    link_id: str,
    destination: str | None = typer.Option(None, '--destination', help='Point it somewhere new.'),
    title: str | None = typer.Option(None, '--title'),
    tags: list[str] | None = typer.Option(
        None, '--tag', help='Replace the tags (repeat for several).'
    ),
    clear_tags: bool = typer.Option(False, '--clear-tags', help='Remove every tag.'),
    expires: str | None = typer.Option(None, '--expires', help='ISO date or date-time.'),
    clear_expires: bool = typer.Option(False, '--clear-expires', help='Remove the expiry.'),
    max_clicks: int | None = typer.Option(None, '--max-clicks'),
    clear_max_clicks: bool = typer.Option(False, '--clear-max-clicks', help='Remove the limit.'),
    password: bool = typer.Option(
        False,
        '--password',
        help=f'Set a password (paid once per link), asked with a hidden prompt or '
        f'{LINK_PASSWORD_ENV}.',
    ),
    clear_password: bool = typer.Option(False, '--clear-password', help='Remove the password.'),
    include_bots: bool | None = typer.Option(
        None,
        '--include-bots/--filter-bots',
        help='Count bots and link previews, or leave them out.',
    ),
    utm_source: str = typer.Option('', '--utm-source'),
    utm_medium: str = typer.Option('', '--utm-medium'),
    utm_campaign: str = typer.Option('', '--utm-campaign'),
    utm_term: str = typer.Option('', '--utm-term'),
    utm_content: str = typer.Option('', '--utm-content'),
    yes: YesAnnotation = False,
):
    """
    Change a link: only what you pass is changed. The code and alias never change.

    Example:
        shorturl update local 1b6e... --destination https://example.com/new
        shorturl update local 1b6e... --clear-expires --max-clicks 500
    """
    select_environment(environment)
    for name, option, clear in (
        ('tags', tags, clear_tags),
        ('expires', expires, clear_expires),
        ('max-clicks', max_clicks, clear_max_clicks),
        ('password', password or None, clear_password),
    ):
        if option and clear:
            raise typer.BadParameter(f'--{name} and --clear-{name} cannot be used together.')

    payload: dict = _utm(utm_source, utm_medium, utm_campaign, utm_term, utm_content)
    if destination is not None:
        payload['destination'] = destination
    if title is not None:
        payload['title'] = title
    if tags:
        payload['tags'] = tags
    elif clear_tags:
        payload['tags'] = []
    if expires:
        payload['expires_at'] = _expiry(expires)
    elif clear_expires:
        payload['expires_at'] = None
    if max_clicks is not None:
        payload['max_clicks'] = max_clicks
    elif clear_max_clicks:
        payload['max_clicks'] = None
    if include_bots is not None:
        payload['filter_bots'] = not include_bots
    if clear_password:
        payload['password'] = None
    if not payload and not password:
        logger.error('Nothing to change: pass at least one option.')
        raise typer.Exit(1)

    if password and not yes:
        cost = _fetch_link(link_id)['costs']['password']
        payload['expected_cost'] = _confirm_spend(cost, 'Setting a password on this link', yes)
    if password:
        # Asked last, so declining the price never costs anyone a typed password.
        payload['password'] = _link_password()

    response = api_request('patch', _link_url(link_id), json=payload, headers=get_headers(CLI))
    raise_for_api_error(response)
    link = response.json()
    logger.info(f'Updated {link["short_url"]} -> {link["destination"]}')
    _spent(link)


@app.command(name='enable')
def shorturl_enable(environment: EnvironmentAnnotation, link_id: str, yes: YesAnnotation = False):
    """
    Enable a disabled link. An active link beyond the free allowance costs credits, once per link:
    the price is shown and confirmed first.

    Example:
        shorturl enable local 1b6e...
    """
    select_environment(environment)
    params: dict = {}
    if not yes:
        link = _fetch_link(link_id)
        if link['state'] == 'active':
            logger.info(f'{link["short_url"]} is already active.')
            return
        cost = link['costs']['enable']
        params['expected_cost'] = _confirm_spend(cost, 'Enabling this link', yes)
    response = api_request(
        'post', _link_url(link_id, '/enable'), params=params or None, headers=get_headers(CLI)
    )
    raise_for_api_error(response)
    link = response.json()
    logger.info(f'{link["short_url"]} is {link["state"]}.')
    _spent(link)


@app.command(name='disable')
def shorturl_disable(environment: EnvironmentAnnotation, link_id: str):
    """
    Disable a link: it shows the "not available" page until enabled again. Free.

    Example:
        shorturl disable local 1b6e...
    """
    select_environment(environment)
    response = api_request('post', _link_url(link_id, '/disable'), headers=get_headers(CLI))
    raise_for_api_error(response)
    link = response.json()
    logger.info(f'{link["short_url"]} is {link["state"]}.')


@app.command(name='delete')
def shorturl_delete(environment: EnvironmentAnnotation, link_id: str, yes: YesAnnotation = False):
    """
    Delete a link. It stops working for good and its code is never issued again.

    Example:
        shorturl delete local 1b6e...
    """
    select_environment(environment)
    headers = get_headers(CLI)
    if not yes:
        link = _fetch_link(link_id)
        if not typer.confirm(f'Delete {link["short_url"]} ({link["destination"]})?'):
            logger.error('Cancelled.')
            raise typer.Exit(1)
    response = api_request('delete', _link_url(link_id), headers=headers)
    raise_for_api_error(response)
    logger.info('Deleted.')


@app.command(name='analytics')
def shorturl_analytics(
    environment: EnvironmentAnnotation, link_id: str, yes: YesAnnotation = False
):
    """
    Open a link's detailed statistics (time series and top lists). Costs credits once per link:
    the price is shown and confirmed first.

    Example:
        shorturl analytics local 1b6e...
    """
    select_environment(environment)
    params: dict = {}
    if not yes:
        cost = _fetch_link(link_id)['costs']['analytics']
        params['expected_cost'] = _confirm_spend(cost, 'Detailed analytics for this link', yes)
    response = api_request(
        'post', _link_url(link_id, '/analytics'), params=params or None, headers=get_headers(CLI)
    )
    raise_for_api_error(response)
    logger.info('Detailed analytics are open.')
    _spent(response.json())


@app.command(name='stats')
def shorturl_stats(
    environment: EnvironmentAnnotation,
    link_id: str,
    range_: str = typer.Option('30', '--range', help='7, 30, 90 or all (days back, UTC).'),
):
    """
    Show a link's statistics: the totals, and the time series and top lists once the detailed
    analytics are open (`analytics`).

    Example:
        shorturl stats local 1b6e... --range 7
    """
    select_environment(environment)
    response = api_request(
        'get',
        _link_url(link_id, '/stats'),
        params={'range': range_},
        headers=get_headers(CLI),
    )
    raise_for_api_error(response)
    body = response.json()
    totals = body['totals']
    console = _console()
    console.print(
        f'Clicks {totals["clicks"]}   Unique visitors {totals["uniques"]}   '
        f'Bots {totals["bot_clicks"]} ({"left out" if totals["filter_bots"] else "counted"})'
    )
    detail = body['detail']
    if detail is None:
        logger.info(
            f'Detailed analytics are not open. They cost {_credits(body["analytics_cost"])} once: '
            f'run `shorturl analytics <env> {link_id}`.'
        )
        return

    console.print(
        f'\n{detail["start"]} to {detail["end"]}: {detail["clicks"]} clicks, '
        f'{detail["uniques"]} visitors'
    )
    series = Table(title='Clicks over time')
    series.add_column('From', no_wrap=True)
    series.add_column('Clicks', justify='right', style='magenta')
    for bucket in detail['buckets']:
        series.add_row(bucket['label'], str(bucket['clicks']))
    console.print(series)
    for top in detail['tables']:
        if not top['rows']:
            continue
        table = Table(title=top['title'])
        table.add_column('Value')
        table.add_column('Clicks', justify='right', style='magenta')
        table.add_column('%', justify='right')
        for row in top['rows']:
            table.add_row(row['label'], str(row['count']), str(row['percent']))
        console.print(table)


@app.command(name='preview')
def shorturl_preview(environment: EnvironmentAnnotation, link_id: str):
    """
    Show what the public preview page (`/s/<code>+`) shows, and whether the link works right now.

    Example:
        shorturl preview local 1b6e...
    """
    select_environment(environment)
    response = api_request('get', _link_url(link_id, '/preview'), headers=get_headers(CLI))
    raise_for_api_error(response)
    preview = response.json()
    console = _console()
    console.print(f'Short link   {preview["short_url"]}')
    console.print(f'Preview page {preview["preview_url"]}')
    console.print(f'Goes to      {preview["destination"]}  ({preview["host"]})')
    if preview['title']:
        console.print(f'Title        {preview["title"]}')
    console.print(f'Password     {"yes" if preview["password_protected"] else "no"}')
    console.print(f'Working now  {"yes" if preview["available"] else "no"}')


@app.command(name='qr')
def shorturl_qr(
    environment: EnvironmentAnnotation,
    link_id: str,
    out: Path = typer.Option(..., '--out', help='Where to save the PNG.'),
):
    """
    Save a link's QR code as a PNG.

    Example:
        shorturl qr local 1b6e... --out spring-sale.png
    """
    select_environment(environment)
    response = api_request('get', _link_url(link_id, '/qr.png'), headers=get_headers(CLI))
    raise_for_api_error(response)
    content_type = response.headers.get('Content-Type', '')
    if not content_type.startswith('image/png'):
        logger.error(f'Expected a PNG, got {content_type or "no content type"}.')
        raise typer.Exit(1)
    try:
        out.write_bytes(response.content)
    except OSError as e:
        logger.error(f'Could not write {out}: {e}')
        raise typer.Exit(1) from e
    logger.info(f'Saved {out} ({len(response.content):,} bytes).')


if __name__ == '__main__':
    app()
