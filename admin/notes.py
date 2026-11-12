#!python
"""
Notes CLI

Command line interface for the Notes JWT API (`/api/notes/`, issue #260), on the model of
`admin/todo.py` and `admin/shorturl.py`: log in, add notes from a file or from standard input,
list and search them, read one (its Markdown on standard output, so it pipes into a file), edit
it, archive, trash, restore, duplicate and download it, read and restore its earlier versions,
share it with a link, manage tags and run saved filters.

A note is addressed by its id (shown by `list`). The first 5 notes are free; the rest cost credits
and are renewed every year. Commands that can charge say so and ask first (`--yes` skips the
question and then accepts whatever it costs).
"""

import re
import sys
from datetime import date, datetime
from pathlib import Path
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

# The token lives in `~/.notes_token.<environment>` (`admin.utils.token_file`).
CLI = 'notes'
PASSWORD_ENV = 'NOTES_PASSWORD'

YesAnnotation = Annotated[
    bool,
    typer.Option(
        '--yes', help='Do not ask before spending credits or deleting; accept whatever it costs.'
    ),
]
NoteAnnotation = Annotated[str, typer.Argument(help='The note id (shown by `list`).')]

VIEWS = ('active', 'archived', 'trash', 'all')
SORTS = ('manual', 'updated', 'created', 'title')
DIRECTIONS = ('asc', 'desc')


# -- Plumbing ------------------------------------------------------------------------------------


def _say(message: str) -> None:
    """Log `message` as it is: the logger renders Rich markup, which a note title may contain."""
    logger.info(escape(message))


def _fail(message: str) -> typer.Exit:
    logger.error(escape(message))
    return typer.Exit(1)


def _console() -> Console:
    """A console for tables: the terminal's own width, or a wide one when output is piped (rich
    would otherwise assume 80 columns and cut ids and titles short)."""
    console = Console()
    return console if console.is_terminal else Console(width=200)


def _request(method: str, path: str, **kwargs):
    """`api_request` to `/api/notes<path>` with the stored token."""
    return api_request(method, api_url(f'/notes{path}'), headers=get_headers(CLI), **kwargs)


def _error_message(response) -> str:
    """The API's error as one line. A 409 for a save made on an out-of-date version says which
    version the note is at now."""
    text = api_error_text(response)
    if response.status_code == 409:
        try:
            body = response.json()
        except ValueError:
            body = {}
        version = body.get('current_version') if isinstance(body, dict) else None
        if version is not None:
            text += f' It is at version {version} now: read it with `get`, then edit it again.'
    return text


def _check(response) -> None:
    """Exit non-zero with the API's own error message unless the response is a success."""
    if not response.ok:
        raise _fail(_error_message(response))


def _call(method: str, path: str, **kwargs):
    """A request that must succeed; its JSON, or `None` for a 204."""
    response = _request(method, path, **kwargs)
    _check(response)
    return None if response.status_code == 204 else response.json()


def _confirm(question: str, yes: bool) -> None:
    if not yes and not typer.confirm(question):
        raise _fail('Cancelled; nothing was changed.')


def _credits(amount: int) -> str:
    return f'{amount} credit{"" if amount == 1 else "s"}'


def _confirm_spend(cost: int, what: str, yes: bool, *, can_ask: bool = True) -> int | None:
    """Ask before spending `cost` credits on `what`. Returns the `expected_cost` to send (the
    confirmed amount, 0 included, so a free slot taken meanwhile is refused rather than charged),
    or `None` with `--yes` (accept any cost); exits 1 if declined. `can_ask` is false when
    standard input was used up by the content: a cost can't be confirmed then, and is refused."""
    if yes:
        return None
    if cost > 0:
        if not can_ask:
            raise _fail(
                f'{what} costs {_credits(cost)}, and the content came from standard input, so '
                'it cannot be confirmed. Read it with --file, or pass --yes to accept the cost.'
            )
        if not typer.confirm(f'{what} costs {_credits(cost)}. Continue?'):
            raise _fail('Cancelled; nothing was charged.')
    return cost


def _cost_params(expected: int | None) -> dict | None:
    return None if expected is None else {'expected_cost': expected}


def _spent(note: dict) -> None:
    if note.get('charged_credits'):
        _say(f'Charged {_credits(note["charged_credits"])}.')


def _norm(name: str) -> str:
    return ' '.join(name.casefold().split())


def _day(value: str | None) -> str:
    return (value or '')[:10]


def _slug(title: str) -> str:
    """`Weekly plan: v2` -> `weekly-plan-v2` (the website's file name for a note's download)."""
    slug = re.sub(r'[^a-z0-9]+', '-', title.casefold()).strip('-')
    return slug or 'note'


def _read_text(path: Path | None, *, stdin_when_piped: bool = False) -> str | None:
    """The text of `path` (`-` is standard input). Without a path, standard input when asked for
    and it is piped (never waits on a terminal); `None` when there is nothing to read."""
    if path is None:
        if stdin_when_piped and not sys.stdin.isatty():
            return sys.stdin.read()
        return None
    if str(path) == '-':
        return sys.stdin.read()
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        raise _fail(f'Could not read {path}: {e}') from e


# -- Tags ----------------------------------------------------------------------------------------


def _match_tag(known: list[dict], ref: str) -> dict:
    """The tag among `known` that `ref` names: its id, or its name ignoring case. An unknown one
    exits 1: a tag is made with `tag-add`."""
    for tag in known:
        if tag['id'] == ref.strip().lower() or _norm(tag['name']) == _norm(ref):
            return tag
    raise _fail(f'No tag called "{ref}". Make it with `tag-add`.')


def _tag_ids(
    refs: list[str] | None, *, allow_none: bool = False, known: list[dict] | None = None
) -> list[str]:
    """The ids of the tags named by `refs` (names or ids), without repeats; with `allow_none`,
    `none` stands for notes without a tag and is kept as it is. Reads the tags (unless `known`
    has them already), and an unknown name stops here, before anything is written."""
    if not refs:
        return []
    if known is None:
        known = _call('get', '/tags')
    ids: list[str] = []
    for ref in refs:
        if allow_none and ref.strip().lower() == 'none':
            tag_id = 'none'
        else:
            tag_id = _match_tag(known, ref)['id']
        if tag_id not in ids:
            ids.append(tag_id)
    return ids


# -- Formatting ----------------------------------------------------------------------------------


def _flags(note: dict) -> str:
    flags = []
    if note.get('has_active_link'):
        flags.append('shared')
    if note.get('locked'):
        flags.append('locked')
    if note.get('taken_down'):
        flags.append('taken down')
    if note.get('archived_at') and not note.get('deleted_at'):
        flags.append('archived')
    return ', '.join(flags)


def _tag_names(note: dict) -> str:
    return ', '.join(tag['name'] for tag in note['tags'])


def _show_page(body: dict) -> None:
    """The "Showing 1-50 of 120" line under a page of results."""
    shown_through = body['offset'] + len(body['results'])
    if shown_through < body['count']:
        _say(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]} notes. '
            f'Use --offset {shown_through} to see more.'
        )


def _snippet_line(note: dict) -> Text | None:
    """`title: ...the match...` for a note a search found text in (the match in bold), or `None`
    for one with no snippet (not searched)."""
    snippet = note.get('search_snippet')
    if not snippet:
        return None
    start, end = note.get('match_start'), note.get('match_end')
    line = Text(f'  {note["title"]} ({note["search_field"]}): ', style='cyan')
    if start is None or end is None:
        line.append(snippet)
    else:
        line.append(snippet[:start])
        line.append(snippet[start:end], style='bold')
        line.append(snippet[end:])
    return line


def _print_notes(body: dict, title: str, *, trash: bool = False) -> None:
    if not body['results']:
        _say('No notes found.')
        return
    table = Table(title=title)
    table.add_column('Title', overflow='fold')
    table.add_column('Tags', style='blue')
    table.add_column('Flags', style='green')
    table.add_column('Deleted' if trash else 'Updated', no_wrap=True)
    if trash:
        table.add_column('Restore', justify='right', style='magenta', no_wrap=True)
    table.add_column('ID', style='cyan', no_wrap=True, min_width=36)
    for note in body['results']:
        cells = [
            Text(note['title']),
            Text(_tag_names(note)),
            _flags(note),
            _day(note['deleted_at'] if trash else note['updated_at']),
        ]
        if trash:
            cost = note.get('restore_cost')
            cells.append('' if cost is None else ('free' if cost == 0 else _credits(cost)))
        cells.append(note['id'])
        table.add_row(*cells)
    console = _console()
    console.print(table)
    lines = [line for note in body['results'] if (line := _snippet_line(note)) is not None]
    if lines:
        console.print(Text('Where it matched (shown without accents or formatting):', style='dim'))
        for line in lines:
            console.print(line)
    _show_page(body)


def _print_note_header(note: dict) -> None:
    """What `get` says about a note, on standard error (the Markdown itself goes to standard
    output)."""
    rows = [
        ('Title', note['title']),
        ('ID', note['id']),
        ('Version', str(note['version'])),
        ('Tags', _tag_names(note)),
        ('Updated', note['updated_at']),
        ('Billing', 'free' if note['is_free'] else f'renews by {_day(note["paid_through"])}'),
        ('Flags', _flags(note)),
    ]
    if note['deleted_at']:
        cost = note.get('restore_cost')
        restore = '' if cost is None else f' (restoring costs {_credits(cost)})'
        rows.append(('In the Trash since', f'{_day(note["deleted_at"])}{restore}'))
    if note.get('takedown_reason'):
        rows.append(('Taken down', note['takedown_reason']))
    if note['locked']:
        rows.append(('Locked', 'its renewal could not be paid: top up your credits to unlock it'))
    table = Table(show_header=False, box=None)
    table.add_column(style='cyan', no_wrap=True)
    table.add_column()
    for label, value in rows:
        if value:
            table.add_row(label, Text(value))
    console = Console(stderr=True)
    if not console.is_terminal:  # rich would otherwise assume 80 columns and cut the id short
        console = Console(stderr=True, width=200)
    console.print(table)


# -- Commands: account ---------------------------------------------------------------------------


@app.command(name='login')
def notes_login(
    environment: EnvironmentAnnotation,
    email: str,
):
    """
    Authenticate with the API and store the token. The password is read from the NOTES_PASSWORD
    environment variable, else asked for with the echo hidden: it is never an argument (it would
    stay in the shell's history).

    Example:
        notes login local me@example.com
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


# -- Commands: notes -----------------------------------------------------------------------------


@app.command(name='list')
def notes_list(
    environment: EnvironmentAnnotation,
    view: str = typer.Option('active', '--view', help='active, archived, trash or all.'),
    q: str = typer.Option(
        '', '--q', help='Search titles and contents (`*` and `?` are wildcards).'
    ),
    regex: bool = typer.Option(False, '--regex', help='Read --q as a regular expression.'),
    tag: list[str] | None = typer.Option(
        None, '--tag', help='Only notes with this tag (a name or an id, or `none`); repeat for any.'
    ),
    shared: bool | None = typer.Option(
        None, '--shared/--not-shared', help='Only shared (or only not shared) notes.'
    ),
    sort: str | None = typer.Option(None, '--sort', help='manual, updated, created or title.'),
    direction: str | None = typer.Option(None, '--dir', help='asc or desc.'),
    limit: int = typer.Option(50, '--limit', help='Max notes to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of notes to skip.'),
):
    """
    List and search your notes.

    Example:
        notes list local
        notes list local --q "deploy*" --tag work --shared --sort updated
        notes list local --view trash
    """
    select_environment(environment)
    params: dict = {'view': view, 'limit': limit, 'offset': offset}
    if q:
        params['q'] = q
        if regex:
            params['mode'] = 'regex'
    if tag:
        params['tag'] = ','.join(_tag_ids(tag, allow_none=True))
    if shared is not None:
        params['shared'] = 'yes' if shared else 'no'
    if sort:
        params['sort'] = sort
    if direction:
        params['dir'] = direction
    body = _call('get', '/notes', params=params)
    titles = {'active': 'Your notes', 'archived': 'Archived', 'trash': 'Trash', 'all': 'All notes'}
    _print_notes(body, titles.get(view, 'Your notes'), trash=view == 'trash')


@app.command(name='get')
def notes_get(environment: EnvironmentAnnotation, note_id: NoteAnnotation):
    """
    Print a note: its Markdown on standard output (so it can be redirected to a file), and what
    else there is to know about it -- title, id, version, tags, billing -- on standard error.

    Example:
        notes get local 1b6e2f4a-...
        notes get local 1b6e2f4a-... > plan.md
    """
    select_environment(environment)
    note = _call('get', f'/notes/{note_id}')
    _print_note_header(note)
    content = note['content']
    if content:
        typer.echo(content, nl=not content.endswith('\n'))


@app.command(name='add')
def notes_add(
    environment: EnvironmentAnnotation,
    title: list[str] = typer.Argument(..., help='The title (the words are joined).'),
    file: Path | None = typer.Option(
        None, '--file', '-f', help='Read the content from this file (`-` is standard input).'
    ),
    tag: list[str] | None = typer.Option(None, '--tag', help='A tag (name or id); repeat.'),
    yes: YesAnnotation = False,
):
    """
    Add a note. The content comes from `--file`, or from standard input when it is piped, or is
    empty. Free while one of your free slots is open; otherwise it costs credits, shown and
    confirmed first.

    Example:
        notes add local "Weekly plan" --file plan.md --tag work
        cat plan.md | notes add local Weekly plan
    """
    select_environment(environment)
    from_stdin = str(file) == '-' or (file is None and not sys.stdin.isatty())
    content = _read_text(file, stdin_when_piped=True) or ''
    payload: dict = {'title': ' '.join(title), 'content': content}
    tag_ids = _tag_ids(tag)
    if tag_ids:
        payload['tags'] = tag_ids
    if not yes:
        cost = _call('get', '/settings')['next_note_cost']
        payload['expected_cost'] = _confirm_spend(
            cost, 'Adding this note', yes, can_ask=not from_stdin
        )
    note = _call('post', '/notes', json=payload)
    _say(f'Added "{note["title"]}".')
    _say(f'ID: {note["id"]}')
    _spent(note)


@app.command(name='edit')
def notes_edit(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    title: str | None = typer.Option(None, '--title', help='A new title.'),
    file: Path | None = typer.Option(
        None, '--file', '-f', help='Replace the content with this file (`-` is standard input).'
    ),
    tag: list[str] | None = typer.Option(None, '--tag', help='Add a tag (name or id); repeat.'),
    remove_tag: list[str] | None = typer.Option(
        None, '--remove-tag', help='Remove a tag (name or id); repeat.'
    ),
):
    """
    Change a note: only what you pass is changed. The note is read first and its version sent with
    the change, so a note someone else changed meanwhile is refused (exit 1) rather than
    overwritten.

    Example:
        notes edit local 1b6e2f4a-... --title "Weekly plan v2" --file plan.md
        notes edit local 1b6e2f4a-... --tag work --remove-tag later
    """
    select_environment(environment)
    content = _read_text(file)
    if title is None and content is None and not tag and not remove_tag:
        raise _fail('Nothing to change: pass at least one option.')
    known = _call('get', '/tags') if tag or remove_tag else None
    adding = _tag_ids(tag, known=known)
    removing = _tag_ids(remove_tag, known=known)
    note = _call('get', f'/notes/{note_id}')
    payload: dict = {}
    if title is not None:
        payload['title'] = title
    if content is not None:
        payload['content'] = content
    if payload:
        payload['base_version'] = note['version']
    if adding or removing:
        current = [item['id'] for item in note['tags']]
        wanted = [tag_id for tag_id in current if tag_id not in removing]
        wanted += [tag_id for tag_id in adding if tag_id not in wanted and tag_id not in removing]
        payload['tags'] = wanted
    updated = _call('patch', f'/notes/{note_id}', json=payload)
    _say(f'Saved "{updated["title"]}" (version {updated["version"]}).')


def _simple_action(note_id: str, action: str, done: str) -> None:
    note = _call('post', f'/notes/{note_id}/{action}')
    _say(f'{done} "{note["title"]}".')


@app.command(name='archive')
def notes_archive(environment: EnvironmentAnnotation, note_id: NoteAnnotation):
    """
    Archive a note: it leaves the main list (its share links keep working).

    Example:
        notes archive local 1b6e2f4a-...
    """
    select_environment(environment)
    _simple_action(note_id, 'archive', 'Archived')


@app.command(name='unarchive')
def notes_unarchive(environment: EnvironmentAnnotation, note_id: NoteAnnotation):
    """
    Move an archived note back to the main list.

    Example:
        notes unarchive local 1b6e2f4a-...
    """
    select_environment(environment)
    _simple_action(note_id, 'unarchive', 'Moved back to your notes:')


@app.command(name='trash')
def notes_trash(environment: EnvironmentAnnotation, note_id: NoteAnnotation):
    """
    Move a note to the Trash (it can be restored until it is deleted for good).

    Example:
        notes trash local 1b6e2f4a-...
    """
    select_environment(environment)
    note = _call('get', f'/notes/{note_id}')
    _call('delete', f'/notes/{note_id}')
    _say(f'Moved "{note["title"]}" to the Trash.')


@app.command(name='restore')
def notes_restore(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    yes: YesAnnotation = False,
):
    """
    Take a note out of the Trash. When that costs credits (a free note with no free slot left, or
    a renewal it missed) the price is shown and confirmed first.

    Example:
        notes restore local 1b6e2f4a-...
    """
    select_environment(environment)
    note = _call('get', f'/notes/{note_id}')
    cost = note.get('restore_cost')
    if cost is None:
        _say(f'"{note["title"]}" is not in the Trash.')
        return
    expected = _confirm_spend(cost, f'Restoring "{note["title"]}"', yes)
    restored = _call('post', f'/notes/{note_id}/restore', params=_cost_params(expected))
    _say(f'Restored "{restored["title"]}".')


@app.command(name='delete')
def notes_delete(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    yes: YesAnnotation = False,
):
    """
    Delete a note for good (it has to be in the Trash first), with its history and links. Nothing
    is refunded.

    Example:
        notes delete local 1b6e2f4a-...
    """
    select_environment(environment)
    note = _call('get', f'/notes/{note_id}')
    if not note['deleted_at']:
        raise _fail(
            f'"{note["title"]}" is not in the Trash. Move it there with `trash`, then delete it.'
        )
    _confirm(f'Delete "{note["title"]}" for good? This cannot be undone.', yes)
    _call('post', f'/notes/{note_id}/delete-forever')
    _say(f'Deleted "{note["title"]}" for good.')


@app.command(name='duplicate')
def notes_duplicate(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    yes: YesAnnotation = False,
):
    """
    Copy a note (its content, no tags) as "TITLE (copy)". A new note like any other: free while a
    slot is open, else its price is shown and confirmed first.

    Example:
        notes duplicate local 1b6e2f4a-...
    """
    select_environment(environment)
    expected = None
    if not yes:
        cost = _call('get', '/settings')['next_note_cost']
        expected = _confirm_spend(cost, 'Duplicating this note', yes)
    copy = _call('post', f'/notes/{note_id}/duplicate', params=_cost_params(expected))
    _say(f'Added "{copy["title"]}".')
    _say(f'ID: {copy["id"]}')
    _spent(copy)


@app.command(name='download')
def notes_download(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    out: Path | None = typer.Option(
        None, '--out', '-o', help='Where to save it: a file, or a folder (default: here).'
    ),
    force: bool = typer.Option(False, '--force', help='Overwrite an existing file.'),
):
    """
    Save a note's Markdown to a file named after its title. It is written to a temporary file next
    to the target and renamed into place only when complete.

    Example:
        notes download local 1b6e2f4a-... --out ./plan.md
    """
    select_environment(environment)
    note = _call('get', f'/notes/{note_id}')
    name = f'{_slug(note["title"])}.md'
    target = out if out is not None else Path(name)
    if target.is_dir():
        target = target / name
    if not target.parent.is_dir():
        raise _fail(f'Not a folder: {target.parent}')
    if target.exists() and not force:
        raise _fail(f'{target} already exists. Use --force to overwrite it.')
    partial = target.with_name(f'.{target.name}.part')
    try:
        partial.write_text(note['content'], encoding='utf-8')
        partial.replace(target)
    except OSError as e:
        partial.unlink(missing_ok=True)
        raise _fail(f'Could not write {target}: {e}') from e
    _say(f'Wrote {target} ({len(note["content"].encode("utf-8")):,} bytes).')


# -- Commands: versions --------------------------------------------------------------------------


def _who(version: dict) -> str:
    kind = version['editor_kind']
    if kind == 'owner':
        who = 'You'
    elif kind == 'user':
        who = version['editor_name'] or 'A signed-in user'
    else:
        who = 'Someone'
    return f'{who} (via a link)' if version['via_link'] else who


@app.command(name='versions')
def notes_versions(environment: EnvironmentAnnotation, note_id: NoteAnnotation):
    """
    List a note's history, newest first.

    Example:
        notes versions local 1b6e2f4a-...
    """
    select_environment(environment)
    versions = _call('get', f'/notes/{note_id}/versions')
    table = Table(title='Versions')
    table.add_column('#', style='cyan', justify='right', no_wrap=True)
    table.add_column('Saved', no_wrap=True)
    table.add_column('By')
    table.add_column('Title', overflow='fold')
    table.add_column('ID', style='cyan', no_wrap=True, min_width=36)
    for version in versions:
        table.add_row(
            str(version['number']),
            version['updated_at'][:16].replace('T', ' '),
            Text(_who(version)),
            Text(version['title']),
            version['id'],
        )
    _console().print(table)


@app.command(name='version-restore')
def notes_version_restore(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    version_id: Annotated[str, typer.Argument(help='The version id (shown by `versions`).')],
    yes: YesAnnotation = False,
):
    """
    Make an earlier version the note's content again. It is a new save: what it replaces stays in
    the history.

    Example:
        notes version-restore local 1b6e2f4a-... 7c9e6679-...
    """
    select_environment(environment)
    version = _call('get', f'/notes/{note_id}/versions/{version_id}')
    _confirm(
        f'Restore version {version["number"]} ("{version["title"]}")? '
        'The current text stays in the history.',
        yes,
    )
    note = _call('post', f'/notes/{note_id}/versions/{version_id}/restore')
    _say(
        f'Restored version {version["number"]}: "{note["title"]}" is now version {note["version"]}.'
    )


# -- Commands: share links -----------------------------------------------------------------------


def _expiry(value: str) -> str:
    """`--expires` as the API wants it. A bare date is sent as a date and the server reads it as
    the end of that day in your time zone; a time without an offset is read in your time zone."""
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


def _link_state(link: dict) -> str:
    if link['revoked_at']:
        return 'revoked'
    return 'active' if link['active'] else 'expired'


@app.command(name='links')
def notes_links(environment: EnvironmentAnnotation, note_id: NoteAnnotation):
    """
    List a note's share links.

    Example:
        notes links local 1b6e2f4a-...
    """
    select_environment(environment)
    links = _call('get', f'/notes/{note_id}/links')
    if not links:
        _say('No share links. Make one with `share`.')
        return
    table = Table(title='Share links')
    table.add_column('Mode', no_wrap=True)
    table.add_column('State', style='green', no_wrap=True)
    table.add_column('Expires', no_wrap=True)
    table.add_column('Label')
    table.add_column('URL', no_wrap=True)
    table.add_column('ID', style='cyan', no_wrap=True, min_width=36)
    for link in links:
        table.add_row(
            link['mode'],
            _link_state(link),
            _day(link['expires_at']),
            Text(link['label']),
            link['url'],
            link['id'],
        )
    _console().print(table)


@app.command(name='share')
def notes_share(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    edit: bool = typer.Option(
        False, '--edit', help="Let anyone with the link change the note's content."
    ),
    label: str = typer.Option('', '--label', help='A reminder of who it is for (never public).'),
    expires: str | None = typer.Option(
        None, '--expires', help='ISO date (the end of that day) or date-time.'
    ),
):
    """
    Make a share link: anyone who has the address can read the note (and with --edit, change its
    content). The address is printed.

    Example:
        notes share local 1b6e2f4a-...
        notes share local 1b6e2f4a-... --edit --label "for Sam" --expires 2026-12-31
    """
    select_environment(environment)
    payload: dict = {'mode': 'edit' if edit else 'read'}
    if label:
        payload['label'] = label
    if expires:
        payload['expires_at'] = _expiry(expires)
    link = _call('post', f'/notes/{note_id}/links', json=payload)
    _say(f'Made {"an editable" if edit else "a read-only"} link: {link["url"]}')
    _say(f'Link ID: {link["id"]}')


@app.command(name='unshare')
def notes_unshare(
    environment: EnvironmentAnnotation,
    note_id: NoteAnnotation,
    link_id: Annotated[str, typer.Argument(help='The link id (shown by `links`).')],
    yes: YesAnnotation = False,
):
    """
    Revoke a share link: it stops working for good (make another to share again).

    Example:
        notes unshare local 1b6e2f4a-... 7c9e6679-...
    """
    select_environment(environment)
    _confirm('Revoke this link? It stops working for good.', yes)
    _call('delete', f'/notes/{note_id}/links/{link_id}')
    _say('Revoked the link.')


# -- Commands: tags ------------------------------------------------------------------------------


def _resolve_tag(ref: str) -> dict:
    return _match_tag(_call('get', '/tags'), ref)


@app.command(name='tags')
def notes_tags(environment: EnvironmentAnnotation):
    """
    List your tags.

    Example:
        notes tags local
    """
    select_environment(environment)
    tags = _call('get', '/tags')
    if not tags:
        _say('No tags. Make one with `tag-add`.')
        return
    table = Table(title='Your tags')
    table.add_column('Name', no_wrap=True)
    table.add_column('Colour', no_wrap=True)
    table.add_column('Notes', justify='right', style='magenta')
    table.add_column('Description')
    table.add_column('ID', style='cyan', no_wrap=True)
    for tag in tags:
        table.add_row(
            Text(tag['name'], style=tag['color']),
            tag['color'],
            str(tag['note_count']),
            Text(tag['description']),
            tag['id'],
        )
    _console().print(table)


@app.command(name='tag-add')
def notes_tag_add(
    environment: EnvironmentAnnotation,
    name: str,
    color: str = typer.Option('', '--color', help='#rrggbb (default: sage green).'),
    description: str = typer.Option('', '--description', help='Shown when hovering the tag.'),
):
    """
    Add a tag.

    Example:
        notes tag-add local work --color "#336699" --description "Work notes"
    """
    select_environment(environment)
    tag = _call('post', '/tags', json={'name': name, 'color': color, 'description': description})
    _say(f'Added the tag {tag["name"]} ({tag["color"]}).')


@app.command(name='tag-edit')
def notes_tag_edit(
    environment: EnvironmentAnnotation,
    tag: str = typer.Argument(..., help='The tag (a name or an id).'),
    name: str | None = typer.Option(None, '--name', help='Rename it.'),
    color: str | None = typer.Option(None, '--color', help='#rrggbb.'),
    description: str | None = typer.Option(None, '--description'),
):
    """
    Rename, recolour or re-describe a tag: only what you pass is changed.

    Example:
        notes tag-edit local work --name job --color "#445566"
    """
    select_environment(environment)
    payload = {
        key: value
        for key, value in (('name', name), ('color', color), ('description', description))
        if value is not None
    }
    if not payload:
        raise _fail('Nothing to change: pass at least one option.')
    found = _resolve_tag(tag)
    updated = _call('patch', f'/tags/{found["id"]}', json=payload)
    _say(f'Updated the tag {updated["name"]} ({updated["color"]}).')


@app.command(name='tag-delete')
def notes_tag_delete(
    environment: EnvironmentAnnotation,
    tag: str = typer.Argument(..., help='The tag (a name or an id).'),
    yes: YesAnnotation = False,
):
    """
    Delete a tag. The notes that had it keep their other tags.

    Example:
        notes tag-delete local work
    """
    select_environment(environment)
    found = _resolve_tag(tag)
    count = found['note_count']
    _confirm(f'Delete the tag {found["name"]} ({count} note{"" if count == 1 else "s"})?', yes)
    _call('delete', f'/tags/{found["id"]}')
    _say(f'Deleted the tag {found["name"]}.')


# -- Commands: saved filters ---------------------------------------------------------------------


def _resolve_filter(ref: str) -> dict:
    """A saved filter by id or by name (ignoring case)."""
    for saved in _call('get', '/filters'):
        if saved['id'] == ref.strip().lower() or _norm(saved['name']) == _norm(ref):
            return saved
    raise _fail(f'No saved filter called "{ref}". See `filters`.')


@app.command(name='filters')
def notes_filters(environment: EnvironmentAnnotation):
    """
    List your saved filters.

    Example:
        notes filters local
    """
    select_environment(environment)
    filters = _call('get', '/filters')
    if not filters:
        _say('No saved filters. Save one from the Search page of the website.')
        return
    table = Table(title='Your saved filters')
    table.add_column('Name', no_wrap=True)
    table.add_column('Selects', overflow='fold')
    table.add_column('ID', style='cyan', no_wrap=True)
    for saved in filters:
        table.add_row(Text(saved['name']), Text(saved['summary']), saved['id'])
    _console().print(table)


@app.command(name='filter-run')
def notes_filter_run(
    environment: EnvironmentAnnotation,
    filter_: str = typer.Argument(..., metavar='FILTER', help='The filter (an id or a name).'),
    limit: int = typer.Option(50, '--limit', help='Max notes to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of notes to skip.'),
):
    """
    Run a saved filter: the notes it selects.

    Example:
        notes filter-run local 1b6e2f4a-...
        notes filter-run local deploys --limit 20
    """
    select_environment(environment)
    saved = _resolve_filter(filter_)
    body = _call('get', f'/filters/{saved["id"]}/notes', params={'limit': limit, 'offset': offset})
    _print_notes(body, saved['name'])


if __name__ == '__main__':
    app()
