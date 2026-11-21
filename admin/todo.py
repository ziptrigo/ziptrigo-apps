#!python
"""
To-do CLI

Command line interface for the To-do JWT API (`/api/todo/`, issue #187), on the model of
`admin/receipts.py` and `admin/shorturl.py`: log in, add tasks (a date in the title is read for
you: "pay rent every month on the 1st"), list and search them, read and edit one, change its state
(to do, in progress, on hold; blocked is set by blockers), close it as done or won't do and reopen
it, block it by other tasks, read its history, postpone, trash and restore, comment, manage labels
and reminders, attach and download files, download all the files of the tasks created in a date
range as zip files and export the tasks as a spreadsheet (`export-files` and its companions,
issue #326), run saved filters, and see or change the settings and the address tasks can be
emailed to.

A task is addressed by its number (the `1234` of `#1234`). The first 10 tasks of a month are free,
then each costs credits; commands that create tasks say what they charged.
"""

import base64
import hashlib
import mimetypes
import re
import shlex
import time
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
    NetworkError,
    api_error_text,
    api_request,
    api_url,
    current_environment,
    format_wait,
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

# The token lives in `~/.todo_token.<environment>` (`admin.utils.token_file`).
CLI = 'todo'
PASSWORD_ENV = 'TODO_PASSWORD'

# A file can be up to `TodoSettings.max_attachment_bytes` (50 MiB by default); a slow connection
# legitimately needs longer than an ordinary API call to finish PUTting one.
FILE_UPLOAD_TIMEOUT_SECONDS = 600
FILE_DOWNLOAD_TIMEOUT_SECONDS = 600
# Read a file for hashing in chunks rather than all at once.
HASH_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
# Exports (issue #326): how often to ask whether it is ready (3 s at first, backing off by half
# again each time to at most 15 s: a zip file takes far longer to build than 3 s) and how long to
# wait for it by default (minutes). A zip file is about 100 MB.
EXPORT_POLL_SECONDS = 3
EXPORT_POLL_MAX_SECONDS = 15
EXPORT_WAIT_MINUTES = 30
#: What `--range` accepts (as typed, any case, `-` or `_`) -> what the API calls it. These are the
#: keys of `apps.core.date_ranges.DATE_RANGES`; a test fails when the two drift apart.
EXPORT_RANGES: dict[str, str] = {
    name.replace('_', '-'): name
    for name in (
        'any',
        'last_15_days',
        'last_30_days',
        'this_month',
        'this_quarter',
        'this_year',
        'custom',
    )
}
#: The start of every zip file's name (`task-files-2026-11-17-part-01-of-02.zip`).
EXPORT_ZIP_GLOB = 'task-files-*.zip'

REPEAT = '\N{CLOCKWISE OPEN CIRCLE ARROW}'
BELL = '\N{BELL}'

YesAnnotation = Annotated[bool, typer.Option('--yes', help='Do not ask before going ahead.')]
NumberAnnotation = Annotated[int, typer.Argument(help='The task number (1234 for #1234).')]

#: The states a person can choose, as typed (`in-progress`) and as the API names them. Blocked is
#: set only by blockers.
STATES = {'todo': 'todo', 'in-progress': 'in_progress', 'on-hold': 'on_hold'}
RESOLUTIONS = {'done': 'done', 'wont-do': 'wont_do'}
SUBTASK_CHOICES = {'done': 'done', 'wont-do': 'wont_do', 'leave': 'leave'}
STATE_LABELS = {
    'todo': 'To do',
    'in_progress': 'In progress',
    'blocked': 'Blocked',
    'on_hold': 'On hold',
}
RESOLUTION_LABELS = {'done': 'Done', 'wont_do': "Won't do"}
POSTPONE_PRESETS = {
    'tomorrow': 'tomorrow',
    'next-week': 'next_week',
    'next-weekend': 'next_weekend',
}
CHANNELS = ('in_app', 'email', 'both')
#: How long the Snooze button of a pushed reminder can wait, in minutes, and how it reads.
SNOOZE_MINUTES = {15: '15 minutes', 60: '1 hour', 480: '8 hours', 1440: '1 day'}


# -- Plumbing ------------------------------------------------------------------------------------


def _say(message: str) -> None:
    """Log `message` as it is: the logger renders Rich markup, which a task title may contain."""
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
    """`api_request` to `/api/todo<path>` with the stored token."""
    return api_request(method, api_url(f'/todo{path}'), headers=get_headers(CLI), **kwargs)


def _error_message(response) -> str:
    """The API's error as one line. A 409 from creating a task names the repeating-task
    occurrences waiting to be paid for."""
    if response.status_code == 409:
        try:
            body = response.json()
        except ValueError:
            body = {}
        numbers = body.get('unpaid_task_numbers') if isinstance(body, dict) else None
        if numbers:
            listed = ', '.join(f'#{number}' for number in numbers)
            return (
                f'409: New tasks are blocked until {listed} '
                f'{"is" if len(numbers) == 1 else "are"} paid for. '
                'Top up your credits on the website.'
            )
    return api_error_text(response)


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
        raise _fail('Cancelled.')


# -- Formatting ----------------------------------------------------------------------------------


def _due(task: dict) -> str:
    """The due date and time as the owner wrote them (`2026-11-01 09:30`, or just the date)."""
    if not task['due_date']:
        return ''
    due_time = (task['due_time'] or '')[:5]
    return f'{task["due_date"]} {due_time}'.strip()


def _priority(task: dict) -> str:
    return f'!{task["priority"]}' if task['priority'] else ''


def _choice(value: str, allowed: dict[str, str], option: str) -> str:
    """The API's name for `value`, typed with hyphens or underscores in any case; else a usage
    error naming the choices."""
    key = value.strip().lower().replace('_', '-')
    if key not in allowed:
        raise typer.BadParameter(f'{option}: use one of {", ".join(allowed)}.')
    return allowed[key]


def _status(task: dict) -> str:
    """What the task is: `Done`, `Won't do`, `In the trash` or, while open, its state."""
    if task['deleted_at']:
        return 'In the trash'
    if task['closed_at']:
        return RESOLUTION_LABELS.get(task['resolution'] or 'done', 'Done')
    return STATE_LABELS.get(task['state'], task['state'])


def _flags(task: dict) -> str:
    repeats = task['recurrence'] is not None and task['recurrence']['ended_at'] is None
    return ' '.join(
        flag for flag, wanted in ((REPEAT, repeats), (BELL, bool(task['reminders']))) if wanted
    )


def _credits(amount: int) -> str:
    return f'{amount} credit{"" if amount == 1 else "s"}'


def _place(task: dict) -> str:
    """Where the task is filed: `Project`, `Project / Section`, with `(archived)` when it is."""
    project = task.get('project')
    if not project:
        return ''
    text = project['name']
    if task.get('section'):
        text += f' / {task["section"]["name"]}'
    return f'{text} (archived)' if project.get('archived') else text


def _task_table(tasks: list[dict], title: str) -> Table:
    table = Table(title=title)
    table.add_column('#', style='cyan', no_wrap=True, justify='right')
    table.add_column('Due', no_wrap=True)
    table.add_column('Pri', style='red', no_wrap=True)
    table.add_column('State', no_wrap=True)
    table.add_column('Title', overflow='fold')
    table.add_column('Labels', style='blue')
    table.add_column('Project')
    table.add_column('', no_wrap=True)
    for task in tasks:
        # To do is the ordinary state: only the others (and a closed task's resolution) are shown.
        status = '' if task['state'] == 'todo' and not task['closed_at'] else _status(task)
        table.add_row(
            str(task['number']),
            _due(task),
            _priority(task),
            status,
            Text(task['title']),
            Text(', '.join(label['name'] for label in task['labels'])),
            Text(_place(task)),
            _flags(task),
        )
    return table


def _show_page(body: dict, noun: str = 'tasks') -> None:
    """The "Showing 1-50 of 120" line under a page of results."""
    shown_through = body['offset'] + len(body['results'])
    if shown_through < body['count']:
        _say(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]} {noun}. '
            f'Use --offset {shown_through} to see more.'
        )


def _snippet_line(task: dict) -> Text | None:
    """`#1234 title: ...the match...` for a task a search found text in (the match in bold), or
    `None` for one with no snippet (not searched, or found by its number alone)."""
    snippet = task.get('search_snippet')
    if not snippet:
        return None
    start, end = task.get('match_start'), task.get('match_end')
    line = Text(f'  #{task["number"]} {task["search_field"]}: ', style='cyan')
    if start is None or end is None:
        line.append(snippet)
    else:
        line.append(snippet[:start])
        line.append(snippet[start:end], style='bold')
        line.append(snippet[end:])
    return line


def _print_tasks(body: dict, title: str) -> None:
    if not body['results']:
        _say('No tasks found.')
        return
    console = _console()
    console.print(_task_table(body['results'], title))
    lines = [line for task in body['results'] if (line := _snippet_line(task)) is not None]
    if lines:
        console.print(Text('Where it matched (shown without accents or formatting):', style='dim'))
        for line in lines:
            console.print(line)
    _show_page(body)


def _task_line(task: dict) -> str:
    """`#1234 "Pay rent" due 2026-11-01 09:30 !1 Every month on the 1st`."""
    parts = [f'#{task["number"]} "{task["title"]}"']
    if task['due_date']:
        parts.append(f'due {_due(task)}')
    if task['priority']:
        parts.append(_priority(task))
    if not task['closed_at'] and task['state'] != 'todo':
        parts.append(f'[{_status(task)}]')
    if task['recurrence'] is not None and task['recurrence']['ended_at'] is None:
        parts.append(f'{REPEAT} {task["recurrence"]["rrule_text"]}')
    return ' '.join(parts)


def _spent(task: dict) -> None:
    if task.get('charged_credits'):
        _say(f'Charged {_credits(task["charged_credits"])}.')


# -- Parsing arguments ---------------------------------------------------------------------------


def _parse_due(value: str) -> tuple[str, str | None]:
    """`--due` as `(YYYY-MM-DD, HH:MM or None)`: a date, or a date and a time (`2026-11-01T09:30`
    or `2026-11-01 09:30`)."""
    text = value.strip().replace(' ', 'T', 1)
    try:
        if 'T' in text:
            moment = datetime.fromisoformat(text)
            return moment.date().isoformat(), moment.strftime('%H:%M')
        return date.fromisoformat(text).isoformat(), None
    except ValueError:
        raise typer.BadParameter(
            'use YYYY-MM-DD or YYYY-MM-DDTHH:MM, like 2026-11-01 or 2026-11-01T09:30.'
        ) from None


def _description(text: str | None, path: Path | None) -> str | None:
    if text is not None and path is not None:
        raise typer.BadParameter('--description and --description-file cannot be used together.')
    if path is None:
        return text
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise _fail(f'Could not read {path}: {e}') from e


def _norm(name: str) -> str:
    return ' '.join(name.casefold().split())


def _match_label(known: list[dict], ref: str) -> dict:
    """The label among `known` that `ref` names: its id, or its name ignoring case. An unknown one
    exits 1: a label is made with `label-add`."""
    for label in known:
        if label['id'] == ref.strip().lower() or _norm(label['name']) == _norm(ref):
            return label
    raise _fail(f'No label called "{ref}". Make it with `label-add`.')


def _label_ids(refs: list[str] | None, *, allow_none: bool = False) -> list[str]:
    """The ids of the labels named by `refs` (names or ids), without repeats. With `allow_none`,
    `none` stands for tasks without a label and is kept as it is."""
    if not refs:
        return []
    known = _call('get', '/labels')
    ids: list[str] = []
    for ref in refs:
        found = 'none' if allow_none and _norm(ref) == 'none' else _match_label(known, ref)['id']
        if found not in ids:
            ids.append(found)
    return ids


def _resolve_label(ref: str) -> dict:
    return _match_label(_call('get', '/labels'), ref)


def _match_project(known: list[dict], ref: str) -> dict:
    """The project among `known` that `ref` names: its id, or its name ignoring case. An unknown
    one exits 1: a project is made with `project-add`."""
    for project in known:
        if project['id'] == ref.strip().lower() or _norm(project['name']) == _norm(ref):
            return project
    raise _fail(f'No project called "{ref}". Make it with `project-add`.')


def _resolve_project(ref: str) -> dict:
    """A project by name or id, archived ones included."""
    return _match_project(_call('get', '/projects', params={'archived': 'all'}), ref)


def _match_section(project: dict, ref: str) -> dict:
    for section in project['sections']:
        if section['id'] == ref.strip().lower() or _norm(section['name']) == _norm(ref):
            return section
    raise _fail(f'{project["name"]} has no section called "{ref}". Make it with `section-add`.')


def _channel(value: str | None) -> str | None:
    if value is not None and value not in CHANNELS:
        raise typer.BadParameter(f'use one of: {", ".join(CHANNELS)}.')
    return value


MUTED_OVERDUE_CHOICES = ('dimmed', 'hidden', 'section')
_MUTED_OVERDUE_TEXT = {
    'dimmed': 'dimmed (at the bottom of Overdue)',
    'hidden': 'hidden (not on Today)',
    'section': 'section (collapsed "On hold & blocked" at the bottom)',
}


def _snooze_text(minutes: int | None) -> str:
    if minutes is None:
        return '-'
    return SNOOZE_MINUTES.get(minutes, f'{minutes} minutes')


def _sha256_base64_file(path: Path) -> str:
    """Base64-encoded SHA-256 digest of the file at `path`, read in chunks. This is the value S3
    checks the upload against (`x-amz-checksum-sha256`) and the API binds into the presigned URL."""
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        while chunk := fh.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode()


# -- Commands: account ---------------------------------------------------------------------------


@app.command(name='login')
def todo_login(
    environment: EnvironmentAnnotation,
    email: str,
):
    """
    Authenticate with the API and store the token. The password is read from the TODO_PASSWORD
    environment variable, else asked for with the echo hidden: it is never an argument (it would
    stay in the shell's history).

    Example:
        todo login local me@example.com
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


# -- Commands: tasks -----------------------------------------------------------------------------


@app.command(name='list')
def todo_list(
    environment: EnvironmentAnnotation,
    view: str = typer.Option(
        'all', '--view', help='today, upcoming, all (open tasks), closed or trash.'
    ),
    q: str = typer.Option(
        '', '--q', help='Search titles, descriptions and comments (* and ? are wildcards).'
    ),
    regex: bool = typer.Option(False, '--regex', help='Read --q as a regular expression.'),
    include_closed: bool = typer.Option(
        False, '--include-closed', help="With --view all, closed tasks too (done and won't do)."
    ),
    include_completed: bool = typer.Option(
        False, '--include-completed', hidden=True, help='The old name of --include-closed.'
    ),
    resolution: str | None = typer.Option(
        None,
        '--resolution',
        help='Only closed tasks closed as done or wont-do (with --view closed or --include-closed).',
    ),
    state: list[str] | None = typer.Option(
        None,
        '--state',
        help='Only open tasks in this state: todo, in-progress, blocked or on-hold; '
        'repeat for several.',
    ),
    priority: str | None = typer.Option(
        None, '--priority', help='Priorities 0 (none) to 3, comma-separated.'
    ),
    label: list[str] | None = typer.Option(
        None, '--label', help='A label name or id, or "none"; repeat for several.'
    ),
    project: list[str] | None = typer.Option(
        None,
        '--project',
        help='A project name or id, or "none" for tasks in no project; repeat for several.',
    ),
    include_archived: bool = typer.Option(
        False, '--include-archived', help='With --view all, tasks of archived projects too.'
    ),
    date_from: str | None = typer.Option(None, '--from', help='Earliest due date, YYYY-MM-DD.'),
    date_to: str | None = typer.Option(None, '--to', help='Latest due date, YYYY-MM-DD.'),
    sort: str | None = typer.Option(
        None,
        '--sort',
        help='date, priority, created or title; or manual (with one --project: its own order).',
    ),
    direction: str | None = typer.Option(
        None,
        '--dir',
        help="asc or desc: the sort's direction (each sort's own default when left out).",
    ),
    limit: int = typer.Option(50, '--limit', help='Max tasks to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of tasks to skip.'),
):
    """
    List your tasks (a number, the due date and time, the priority, the state, the title, the
    labels, the project and section, a repeat mark and a bell for reminders). The state column is blank for To do, and says
    Done or Won't do for a closed task. On hold and Blocked tasks are never overdue.

    Example:
        todo list local
        todo list local --view today
        todo list local --q "rent*" --include-closed
        todo list local --view closed --resolution wont-do
        todo list local --state in-progress --state blocked
        todo list local --priority 1,2 --label home --from 2026-11-01 --to 2026-11-30
        todo list local --sort title --dir desc
        todo list local --project "Website relaunch" --sort manual
        todo list local --project none
    """
    select_environment(environment)
    view = 'closed' if view == 'completed' else view  # the name this view had before version 2
    params: dict = {'view': view, 'limit': limit, 'offset': offset}
    if q:
        params['q'] = q
        if regex:
            params['mode'] = 'regex'
    if include_closed or include_completed:
        params['include_closed'] = 'true'
    if resolution:
        params['resolution'] = _choice(resolution, RESOLUTIONS, '--resolution')
    if state:
        params['state'] = ','.join(
            _choice(item, {**STATES, 'blocked': 'blocked'}, '--state') for item in state
        )
    if label:
        params['label'] = ','.join(_label_ids(label, allow_none=True))
    if project:
        known: list[dict] | None = None
        ids: list[str] = []
        for ref in project:
            if _norm(ref) == 'none':
                found = 'none'
            else:
                if known is None:
                    known = _call('get', '/projects', params={'archived': 'all'})
                found = _match_project(known, ref)['id']
            if found not in ids:
                ids.append(found)
        params['project'] = ','.join(ids)
    if include_archived:
        params['include_archived'] = 'true'
    if direction:
        params['dir'] = _choice(direction, {'asc': 'asc', 'desc': 'desc'}, '--dir')
    for name, value in (
        ('priority', priority),
        ('date_from', date_from),
        ('date_to', date_to),
        ('sort', sort),
    ):
        if value:
            params[name] = value
    _print_tasks(_call('get', '/tasks', params=params), 'Your tasks')


@app.command(name='add')
def todo_add(
    environment: EnvironmentAnnotation,
    title: list[str] = typer.Argument(..., help='The title; a date in it is read for you.'),
    due: str | None = typer.Option(
        None, '--due', help='YYYY-MM-DD, or YYYY-MM-DDTHH:MM with a time.'
    ),
    priority: int | None = typer.Option(None, '--priority', help='1 (highest), 2 or 3.'),
    label: list[str] | None = typer.Option(
        None, '--label', help='A label name or id; repeat for several.'
    ),
    parent: int | None = typer.Option(
        None, '--parent', help='Make it a subtask of this task number.'
    ),
    state: str | None = typer.Option(
        None, '--state', help='todo (default), in-progress or on-hold. Blocked is set by blockers.'
    ),
    blocked_by: list[int] | None = typer.Option(
        None,
        '--blocked-by',
        help='The number of a task that blocks this one; repeat for several (at most 20). '
        'The task starts Blocked while one of them is open.',
    ),
    repeat: str | None = typer.Option(
        None, '--repeat', help='An RFC 5545 rule, like FREQ=MONTHLY;BYMONTHDAY=1.'
    ),
    mode: str | None = typer.Option(
        None, '--mode', help='fixed (default) or from_completion: how a repeat is counted.'
    ),
    project: str | None = typer.Option(
        None, '--project', help='Put it in this project (a name or an id).'
    ),
    section: str | None = typer.Option(
        None, '--section', help='A section of that project (a name or an id); needs --project.'
    ),
    no_parse: bool = typer.Option(
        False, '--no-parse', help='Keep the title as typed: do not read a date out of it.'
    ),
    description: str | None = typer.Option(None, '--description', help='The description.'),
    description_file: Path | None = typer.Option(
        None, '--description-file', help='Read the description from this file.'
    ),
):
    """
    Add a task. By default the title is read for a date, a time and a repeat, so "pay rent every
    month on the 1st" just works (what was read is shown; `--no-parse` keeps the title as typed).
    The first tasks of a month are free, then each costs credits.

    Example:
        todo add local "pay rent every month on the 1st"
        todo add local call the dentist --due 2026-11-05T15:00 --priority 2 --label health
        todo add local "write report" --parent 1234 --no-parse
        todo add local "send invoice" --blocked-by 1200 --blocked-by 1201
        todo add local "think about it" --state on-hold
        todo add local "review mockups" --project "Website relaunch" --section Design
    """
    select_environment(environment)
    if section and not project:
        raise typer.BadParameter('--section needs --project.')
    payload: dict = {'title': ' '.join(title), 'parse_title': not no_parse}
    if project:
        found = _resolve_project(project)
        payload['project'] = found['id']
        if section:
            payload['section'] = _match_section(found, section)['id']
    text = _description(description, description_file)
    if text is not None:
        payload['description'] = text
    if due:
        payload['due_date'], due_time = _parse_due(due)
        if due_time:
            payload['due_time'] = due_time
    if priority is not None:
        payload['priority'] = priority
    if label:
        payload['labels'] = _label_ids(label)
    if parent is not None:
        payload['parent'] = parent
    if state is not None:
        payload['state'] = _choice(state, STATES, '--state')
    if blocked_by:
        payload['blocked_by'] = list(dict.fromkeys(blocked_by))
    if repeat:
        payload['rrule'] = repeat
    if mode:
        payload['recurrence_mode'] = mode

    task = _call('post', '/tasks', json=payload)
    _say(f'Created {_task_line(task)}')
    parsed = task.get('parsed')
    if parsed and parsed['applied']:
        _say(f'Read from the title: {parsed["label"]}')
    _spent(task)


def _link_line(ref: dict) -> str:
    """`#1200 Title`, with a note for a task that no longer blocks (closed) or is in the Trash."""
    note = ' (in the trash)' if ref['trashed'] else '' if ref['open'] else ' (closed)'
    return f'#{ref["number"]} {ref["title"]}{note}'


def _print_history(number: int, limit: int, before: str | None) -> None:
    """A page of the task's activity log, newest first, and how to get the next one."""
    params: dict = {'limit': limit}
    if before:
        params['before'] = before
    body = _call('get', f'/tasks/{number}/activity', params=params)
    console = _console()
    console.print(Text('\nHistory', style='cyan'))
    if not body['results']:
        console.print(Text('Nothing recorded.'))
    for entry in body['results']:
        auto = ' (automatic)' if entry['automatic'] else ''
        console.print(Text(f'{entry["created_at"]}  {entry["text"]}{auto}  [{entry["channel"]}]'))
    if body['next']:
        console.print(
            Text(f'More: todo history <environment> {number} --before {body["next"]}', style='dim')
        )


@app.command(name='get')
def todo_get(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    history: bool = typer.Option(
        False, '--history', help='Also print the latest entries of the activity log.'
    ),
):
    """
    Show everything about one task: its details (the state, how it was closed), the description (as
    Markdown), what blocks it and what it blocks, the comments, the reminders, the files and the
    tasks that mention it. With `--history`, the latest changes made to it too.

    Example:
        todo get local 1234
        todo get local 1234 --history
    """
    select_environment(environment)
    task = _call('get', f'/tasks/{number}')
    comments = _call('get', f'/tasks/{number}/comments')

    console = _console()

    def row(label: str, value: str) -> None:
        if value:
            console.print(Text.assemble((f'{label}: ', 'cyan'), value))

    console.print(Text(f'#{task["number"]} {task["title"]}', style='bold'))
    if task['deleted_at']:
        status = 'in the trash'
    elif task['closed_at']:
        status = RESOLUTION_LABELS.get(task['resolution'] or 'done', 'Done').lower()
    else:
        status = 'open'
    row('Status', status)
    row('State', '' if task['closed_at'] else STATE_LABELS.get(task['state'], task['state']))
    row('Priority', _priority(task))
    row('Due', _due(task))
    recurrence = task['recurrence']
    if recurrence is not None:
        ended = ' (stopped)' if recurrence['ended_at'] else ''
        mode = recurrence['mode'].replace('_', ' ')
        row('Repeats', f'{recurrence["rrule_text"]}, {mode}{ended}')
    row('Parent', f'#{task["parent"]}' if task['parent'] else '')
    row('Labels', ', '.join(label['name'] for label in task['labels']))
    row('Project', _place(task))
    row(
        'Subtasks',
        f'{task["open_subtask_count"]} open of {task["subtask_count"]}'
        if task['subtask_count']
        else '',
    )
    row('Source', task['source'])
    if task.get('email_from'):
        proven = 'authenticated' if task.get('email_from_authenticated') else 'not authenticated'
        row('Email from', f'{task["email_from"]} ({proven})')
    row('Created', task['created_at'])
    row('Updated', task['updated_at'])
    row('Closed', task['closed_at'] or '')
    row('In the trash since', task['deleted_at'] or '')
    row(
        'Cost',
        f'{_credits(task["charged_credits"])}{" (still owed)" if task["unpaid"] else ""}'
        if task['charged_credits']
        else '',
    )

    if task['description']:
        console.print(Text('\nDescription', style='cyan'))
        console.print(Text(task['description']))
    if task['blocked_by']:
        console.print(Text('\nBlocked by', style='cyan'))
        for ref in task['blocked_by']:
            console.print(Text(_link_line(ref)))
    if task['blocks']:
        console.print(Text('\nBlocks', style='cyan'))
        for ref in task['blocks']:
            console.print(Text(_link_line(ref)))
    if task['reminders']:
        console.print(Text('\nReminders', style='cyan'))
        for reminder in task['reminders']:
            sent = f', sent {reminder["sent_at"]}' if reminder['sent_at'] else ''
            console.print(
                Text(
                    f'{reminder["id"]}  {reminder["minutes_before"]} minutes before '
                    f'({reminder["channel"]}), at {reminder["fire_at"]}{sent}'
                )
            )
    if task['attachments']:
        console.print(Text('\nFiles', style='cyan'))
        for attachment in task['attachments']:
            console.print(
                Text(
                    f'{attachment["id"]}  {attachment["filename"]} ({attachment["size_bytes"]:,} bytes)'
                )
            )
    if comments:
        console.print(Text('\nComments', style='cyan'))
        for comment in comments:
            if comment['deleted_at']:
                console.print(Text(f'{comment["created_at"]}  (deleted) {comment["id"]}'))
                continue
            edited = ' (edited)' if comment['edited_at'] else ''
            console.print(Text(f'{comment["created_at"]}{edited}  {comment["id"]}'))
            console.print(Text(comment['body'] or '', style='default'))
            _print_comment_files(comment)
    if task['referenced_by']:
        console.print(Text('\nReferenced by', style='cyan'))
        for source in task['referenced_by']:
            where = ' (in a comment)' if source['comment'] else ''
            console.print(Text(f'#{source["number"]} {source["title"]}{where}'))
    if history:
        _print_history(number, 20, None)


@app.command(name='history')
def todo_history(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    limit: int = typer.Option(20, '--limit', help='How many entries to show (1-100).'),
    before: str | None = typer.Option(
        None, '--before', help='The cursor printed under the previous page: show older entries.'
    ),
):
    """
    Show a task's activity log, newest first: every change made to it, who or what made it
    (automatic ones are marked) and through which entry point. Works for a task in the Trash.

    Example:
        todo history local 1234
        todo history local 1234 --limit 100
        todo history local 1234 --before 2026-10-10T09:00:00+00:00_5f0c...
    """
    select_environment(environment)
    _print_history(number, limit, before)


@app.command(name='edit')
def todo_edit(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    title: str | None = typer.Option(None, '--title'),
    description: str | None = typer.Option(None, '--description'),
    priority: int | None = typer.Option(None, '--priority', help='0 (none), 1 (highest), 2 or 3.'),
    due: str | None = typer.Option(
        None,
        '--due',
        help='YYYY-MM-DD, or YYYY-MM-DDTHH:MM with a time. A date alone KEEPS the time the task '
        'already has (use --clear-time to drop it).',
    ),
    clear_due: bool = typer.Option(False, '--clear-due', help='Remove the date (and the time).'),
    clear_time: bool = typer.Option(
        False,
        '--clear-time',
        help='Remove the time and keep the date (alone, or with a --due date).',
    ),
    label: list[str] | None = typer.Option(
        None, '--label', help='Add a label (a name or id); repeat for several.'
    ),
    remove_label: list[str] | None = typer.Option(
        None, '--remove-label', help='Take a label off; repeat for several.'
    ),
    state: str | None = typer.Option(
        None,
        '--state',
        help='todo, in-progress or on-hold (an open task; blocked is set by blockers).',
    ),
    blocked_by: list[int] | None = typer.Option(
        None,
        '--blocked-by',
        help='REPLACES the tasks that block this one with these numbers; repeat for several.',
    ),
    clear_blocked_by: bool = typer.Option(
        False, '--clear-blocked-by', help='Remove every blocker.'
    ),
    project: str | None = typer.Option(
        None,
        '--project',
        help='Move it to this project (a name or an id), with its subtasks. It leaves its section.',
    ),
    no_project: bool = typer.Option(False, '--no-project', help='Take it out of its project.'),
    section: str | None = typer.Option(
        None,
        '--section',
        help='Put it in this section (a name or an id) of its project, or of --project.',
    ),
    no_section: bool = typer.Option(False, '--no-section', help='Take it out of its section.'),
):
    """
    Change a task: only what you pass is changed.

    Example:
        todo edit local 1234 --title "Pay the rent" --priority 1
        todo edit local 1234 --due 2026-11-05T09:00 --label home --remove-label later
        todo edit local 1234 --due 2026-11-06          (moves the day, keeps its time)
        todo edit local 1234 --due 2026-11-06 --clear-time
        todo edit local 1234 --clear-due
        todo edit local 1234 --state in-progress
        todo edit local 1234 --blocked-by 1200 --blocked-by 1201   (replaces the blockers)
        todo edit local 1234 --clear-blocked-by
        todo edit local 1234 --project "Website relaunch" --section Design
        todo edit local 1234 --no-section
        todo edit local 1234 --no-project
    """
    select_environment(environment)
    if project and no_project:
        raise typer.BadParameter('--project and --no-project cannot be used together.')
    if section and (no_section or no_project):
        raise typer.BadParameter('--section cannot be used with --no-section or --no-project.')
    if blocked_by and clear_blocked_by:
        raise typer.BadParameter('--blocked-by and --clear-blocked-by cannot be used together.')
    if due and clear_due:
        raise typer.BadParameter('--due and --clear-due cannot be used together.')
    if clear_time and clear_due:
        raise typer.BadParameter('--clear-due already removes the time.')

    payload: dict = {}
    if title is not None:
        payload['title'] = title
    if description is not None:
        payload['description'] = description
    if priority is not None:
        payload['priority'] = priority
    if due:
        payload['due_date'], due_time = _parse_due(due)
        if due_time and clear_time:
            raise typer.BadParameter('--due with a time and --clear-time contradict each other.')
        if due_time:
            payload['due_time'] = due_time
    elif clear_due:
        payload['due_date'] = None
    if clear_time:
        payload['due_time'] = None
    if label or remove_label:
        current = [item['id'] for item in _call('get', f'/tasks/{number}')['labels']]
        added = _label_ids(label)
        removed = set(_label_ids(remove_label))
        payload['labels'] = [
            *(item for item in current if item not in removed),
            *(item for item in added if item not in current and item not in removed),
        ]
    if state is not None:
        payload['state'] = _choice(state, STATES, '--state')
    if blocked_by:
        payload['blocked_by'] = list(dict.fromkeys(blocked_by))
    elif clear_blocked_by:
        payload['blocked_by'] = []
    if no_project:
        payload['project'] = None
    elif project or section:
        if project:
            target = _resolve_project(project)
            payload['project'] = target['id']
        else:  # a section of the project the task is in
            current_project = _call('get', f'/tasks/{number}').get('project')
            if not current_project:
                raise _fail(f'#{number} is in no project: pass --project with --section.')
            target = _call('get', f'/projects/{current_project["id"]}')
        if section:
            payload['section'] = _match_section(target, section)['id']
    if no_section:
        payload['section'] = None
    if not payload:
        raise _fail('Nothing to change: pass at least one option.')

    task = _call('patch', f'/tasks/{number}', json=payload)
    _say(f'Updated {_task_line(task)}')


def _close_failure(number: int, response) -> str:
    """Why closing `number` failed. A 409 is the API saying it was already closed (nothing was
    changed, no note added): say how, and where to change it."""
    if response.status_code == 409:
        try:
            detail = str(response.json().get('detail', ''))
        except ValueError, AttributeError:
            detail = ''
        match = re.search(r'already closed as (.+?)\.?$', detail)
        if match:
            return (
                f'#{number} was already closed as {match.group(1)}; '
                'use `inv todo resolution` to change it'
            )
    return f'#{number}: {_error_message(response)}'


def _close_each(numbers: list[int], payload: dict, verb: str) -> None:
    """Close each task with `payload`, carrying on past a failure (exit 1 if any failed)."""
    failed = 0
    for number in numbers:
        response = _request('post', f'/tasks/{number}/close', json=payload)
        if not response.ok:
            failed += 1
            logger.error(escape(_close_failure(number, response)))
            continue
        _say(f'{verb} #{number} "{response.json()["title"]}"')
    if failed:
        raise typer.Exit(1)


@app.command(name='done')
def todo_done(
    environment: EnvironmentAnnotation,
    numbers: list[int] = typer.Argument(..., help='The task numbers.'),
):
    """
    Close tasks as Done (shorthand for `close`), and their open subtasks as Done too. A repeating
    task creates its next occurrence.

    Example:
        todo done local 1234 1235
    """
    select_environment(environment)
    _close_each(numbers, {}, 'Completed')


@app.command(name='close')
def todo_close(
    environment: EnvironmentAnnotation,
    numbers: list[int] = typer.Argument(..., help='The task numbers.'),
    wont_do: bool = typer.Option(
        False, '--wont-do', help="Close as won't do (skipped) instead of done."
    ),
    note: str | None = typer.Option(
        None, '--note', help='Why, as a comment on the task; only with --wont-do.'
    ),
    subtasks: str = typer.Option(
        'done',
        '--subtasks',
        help='What happens to the open subtasks: done (default), wont-do or leave.',
    ),
):
    """
    Close tasks as Done (the default) or, with `--wont-do`, as Won't do, and say what happens to
    their open subtasks. A repeating task creates its next occurrence either way. Closing a task
    that has open blockers is allowed (the links are kept) and frees the tasks it blocked.

    Example:
        todo close local 1234
        todo close local 1234 --wont-do --note "not needed any more"
        todo close local 1234 --subtasks leave
    """
    select_environment(environment)
    if note and not wont_do:
        raise typer.BadParameter('--note only goes with --wont-do.')
    payload: dict = {
        'resolution': 'wont_do' if wont_do else 'done',
        'subtasks': _choice(subtasks, SUBTASK_CHOICES, '--subtasks'),
    }
    if note:
        payload['note'] = note
    _close_each(numbers, payload, "Closed as won't do" if wont_do else 'Closed as done')


@app.command(name='reopen')
def todo_reopen(environment: EnvironmentAnnotation, number: NumberAnnotation):
    """
    Make a closed task open again (To do, or Blocked while one of its blockers is open).

    Example:
        todo reopen local 1234
    """
    select_environment(environment)
    task = _call('post', f'/tasks/{number}/reopen')
    _say(f'Reopened {_task_line(task)}')


@app.command(name='undone', hidden=True)
def todo_undone(environment: EnvironmentAnnotation, number: NumberAnnotation):
    """
    The old name of `reopen`.

    Example:
        todo undone local 1234
    """
    select_environment(environment)
    task = _call('post', f'/tasks/{number}/reopen')
    _say(f'Reopened {_task_line(task)}')


@app.command(name='resolution')
def todo_resolution(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    resolution: str = typer.Argument(..., help='done or wont-do.'),
):
    """
    Change how a closed task was closed (done or wont-do). Nothing else happens: no new occurrence,
    no subtasks touched, and it keeps the time it was closed at.

    Example:
        todo resolution local 1234 wont-do
    """
    select_environment(environment)
    value = _choice(resolution, RESOLUTIONS, 'RESOLUTION')
    task = _call('post', f'/tasks/{number}/resolution', json={'resolution': value})
    _say(f'#{number} "{task["title"]}" is now closed as {RESOLUTION_LABELS[value].lower()}.')


@app.command(name='state')
def todo_state(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    state: str = typer.Argument(..., help='todo, in-progress or on-hold.'),
):
    """
    Set an open task's state. Blocked is not one you can set: it comes from `block`.

    Example:
        todo state local 1234 in-progress
        todo state local 1234 on-hold
    """
    select_environment(environment)
    value = _choice(state, STATES, 'STATE')
    task = _call('post', f'/tasks/{number}/state', json={'state': value})
    _say(f'#{number} "{task["title"]}" is now {STATE_LABELS[task["state"]]}.')


@app.command(name='block')
def todo_block(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    blockers: list[int] = typer.Argument(..., help='The numbers of the tasks that block it.'),
):
    """
    Make tasks block this one: while one of them is open this task is Blocked, and it goes back to
    To do when the last one closes. A task cannot block itself and the links cannot form a cycle.

    Example:
        todo block local 1234 1200 1201
    """
    select_environment(environment)
    failed = 0
    for blocker in blockers:
        response = _request('post', f'/tasks/{number}/blockers', json={'number': blocker})
        if not response.ok:
            failed += 1
            logger.error(escape(f'#{blocker}: {_error_message(response)}'))
            continue
        _say(f'#{blocker} blocks {_task_line(response.json())}')
    if failed:
        raise typer.Exit(1)


@app.command(name='block-suggest')
def todo_block_suggest(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    q: str = typer.Option('', '--q', help='Digits (a number) or words of the title.'),
):
    """
    Suggest tasks that could block this one: up to 10 of your open tasks that `block` would
    accept (not itself, not one that already blocks it, not one it blocks, directly or not).

    Example:
        todo block-suggest local 1234 --q rent
    """
    select_environment(environment)
    found = _call('get', f'/tasks/{number}/blockers/suggest', params={'q': q} if q else None)
    if not found:
        _say('No tasks to suggest.')
        return
    for task in found:
        _say(f'#{task["number"]} {task["title"]}')


@app.command(name='unblock')
def todo_unblock(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    blockers: list[int] = typer.Argument(..., help='The numbers of the tasks to take off.'),
):
    """
    Take tasks off this task's blockers. A Blocked task left with no open blocker goes back to To do.

    Example:
        todo unblock local 1234 1200
    """
    select_environment(environment)
    failed = 0
    for blocker in blockers:
        response = _request('delete', f'/tasks/{number}/blockers/{blocker}')
        if not response.ok:
            failed += 1
            logger.error(escape(f'#{blocker}: {_error_message(response)}'))
            continue
        _say(f'#{blocker} no longer blocks #{number}.')
    if failed:
        raise typer.Exit(1)


@app.command(name='postpone')
def todo_postpone(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    when: str = typer.Argument(..., help='tomorrow, next-week, next-weekend or YYYY-MM-DD.'),
):
    """
    Move a task's due date, keeping its time. A repeating schedule is not moved.

    Example:
        todo postpone local 1234 tomorrow
        todo postpone local 1234 2026-12-24
    """
    select_environment(environment)
    if when in POSTPONE_PRESETS:
        payload = {'preset': POSTPONE_PRESETS[when]}
    else:
        try:
            payload = {'preset': 'date', 'date': date.fromisoformat(when).isoformat()}
        except ValueError:
            raise typer.BadParameter(
                f'use {", ".join(POSTPONE_PRESETS)} or a date like 2026-12-24.'
            ) from None
    task = _call('post', f'/tasks/{number}/postpone', json=payload)
    _say(f'Postponed {_task_line(task)}')


@app.command(name='overdue-to-today')
def todo_overdue_to_today(environment: EnvironmentAnnotation):
    """
    Reschedule every overdue task to today, keeping their times. A task that is On hold or Blocked
    is never overdue, so it is not moved.

    Example:
        todo overdue-to-today local
    """
    select_environment(environment)
    count = _call('post', '/tasks/reschedule-overdue')['count']
    _say(f'{count} overdue task{"" if count == 1 else "s"} moved to today.')


@app.command(name='trash')
def todo_trash(environment: EnvironmentAnnotation, number: NumberAnnotation):
    """
    Move a task, and its subtasks, to the Trash (restorable until they are purged).

    Example:
        todo trash local 1234
    """
    select_environment(environment)
    _call('delete', f'/tasks/{number}')
    _say(f'#{number} is in the trash.')


@app.command(name='restore')
def todo_restore(environment: EnvironmentAnnotation, number: NumberAnnotation):
    """
    Take a task out of the Trash.

    Example:
        todo restore local 1234
    """
    select_environment(environment)
    task = _call('post', f'/tasks/{number}/restore')
    _say(f'Restored {_task_line(task)}')


# -- Commands: comments --------------------------------------------------------------------------


def _print_comment_files(comment: dict) -> None:
    for attachment in comment.get('attachments') or []:
        console = _console()
        console.print(
            Text(
                f'  file {attachment["id"]}  {attachment["filename"]} '
                f'({attachment["size_bytes"]:,} bytes)',
                style='cyan',
            )
        )


def _discard_draft(attachment_id: str) -> None:
    """Best effort: delete a comment file that was uploaded but will not be attached (the API
    removes an unattached one after a day anyway)."""
    try:
        response = api_request(
            'post',
            api_url(f'/todo/attachments/{attachment_id}/discard'),
            headers=get_headers(CLI),
            exit_on_error=False,
        )
    except NetworkError as e:
        logger.warning(escape(f'Could not discard the file {attachment_id}: {e}'))
        return
    if not response.ok:
        logger.warning(
            escape(f'Could not discard the file {attachment_id}: {_error_message(response)}')
        )


@app.command(name='comment')
def todo_comment(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    text: list[str] = typer.Argument(..., help='The comment (Markdown).'),
    files: list[Path] | None = typer.Option(
        None, '--file', help='A file to attach to the comment; repeat for several.'
    ),
):
    """
    Add a comment to a task, with files if you like. The files are uploaded first; if one fails
    nothing is commented and the ones already uploaded are removed.

    Example:
        todo comment local 1234 asked the landlord, see #1250
        todo comment local 1234 the signed copy --file contract.pdf --file photo.jpg
    """
    select_environment(environment)
    paths = files or []
    for path in paths:  # check them all before uploading any
        if not path.exists():
            raise _fail(f'Not found: {path}')
        if not path.is_file():
            raise _fail(f'Not a file: {path}')
        if path.stat().st_size == 0:
            raise _fail(f'Empty file: {path}')
    get_headers(CLI)  # not logged in: say so before reading any file

    uploaded: list[str] = []
    for path in paths:
        try:
            attachment = _upload_file(number, path, for_comment=True)
        except (UploadFailed, NetworkError) as e:
            for attachment_id in uploaded:
                _discard_draft(attachment_id)
            raise _fail(f'{path.name}: {e}. Nothing was commented.') from e
        uploaded.append(attachment['id'])
    payload: dict = {'body': ' '.join(text)}
    if uploaded:
        payload['attachment_ids'] = uploaded
    try:
        response = _request('post', f'/tasks/{number}/comments', json=payload, exit_on_error=False)
    except NetworkError as e:
        for attachment_id in uploaded:  # best effort: if the comment did commit, the server refuses
            _discard_draft(attachment_id)
        raise _fail(f'{e}. The comment may not have been added.') from e
    if not response.ok:
        for attachment_id in uploaded:
            _discard_draft(attachment_id)
        raise _fail(_error_message(response))
    comment = response.json()
    attached = f' with {len(uploaded)} file{"" if len(uploaded) == 1 else "s"}' if uploaded else ''
    _say(f'Commented on #{number}{attached} ({comment["id"]}).')


@app.command(name='comments')
def todo_comments(environment: EnvironmentAnnotation, number: NumberAnnotation):
    """
    Show a task's comments, oldest first.

    Example:
        todo comments local 1234
    """
    select_environment(environment)
    comments = _call('get', f'/tasks/{number}/comments')
    if not comments:
        _say('No comments.')
        return
    console = _console()
    for comment in comments:
        if comment['deleted_at']:
            console.print(Text(f'{comment["created_at"]}  (deleted)  {comment["id"]}'))
            continue
        edited = ' (edited)' if comment['edited_at'] else ''
        console.print(Text(f'{comment["created_at"]}{edited}  {comment["id"]}', style='cyan'))
        console.print(Text(comment['body'] or ''))
        _print_comment_files(comment)
        console.print()


# -- Commands: labels ----------------------------------------------------------------------------


@app.command(name='labels')
def todo_labels(environment: EnvironmentAnnotation):
    """
    List your labels.

    Example:
        todo labels local
    """
    select_environment(environment)
    labels = _call('get', '/labels')
    if not labels:
        _say('No labels. Make one with `label-add`.')
        return
    table = Table(title='Your labels')
    table.add_column('Name', no_wrap=True)
    table.add_column('Colour', no_wrap=True)
    table.add_column('Tasks', justify='right', style='magenta')
    table.add_column('Description')
    table.add_column('ID', style='cyan', no_wrap=True)
    for label in labels:
        table.add_row(
            Text(label['name'], style=label['color']),
            label['color'],
            str(label['task_count']),
            Text(label['description']),
            label['id'],
        )
    _console().print(table)


@app.command(name='label-add')
def todo_label_add(
    environment: EnvironmentAnnotation,
    name: str,
    color: str = typer.Option('', '--color', help='#rrggbb (default: sage green).'),
    description: str = typer.Option('', '--description', help='Shown when hovering the label.'),
):
    """
    Add a label.

    Example:
        todo label-add local home --color "#336699" --description "Around the house"
    """
    select_environment(environment)
    label = _call(
        'post', '/labels', json={'name': name, 'color': color, 'description': description}
    )
    _say(f'Added the label {label["name"]} ({label["color"]}).')


@app.command(name='label-edit')
def todo_label_edit(
    environment: EnvironmentAnnotation,
    label: str = typer.Argument(..., help='The label (a name or an id).'),
    name: str | None = typer.Option(None, '--name', help='Rename it.'),
    color: str | None = typer.Option(None, '--color', help='#rrggbb.'),
    description: str | None = typer.Option(None, '--description'),
):
    """
    Rename, recolour or re-describe a label: only what you pass is changed.

    Example:
        todo label-edit local home --name house --color "#445566"
    """
    select_environment(environment)
    payload = {
        key: value
        for key, value in (('name', name), ('color', color), ('description', description))
        if value is not None
    }
    if not payload:
        raise _fail('Nothing to change: pass at least one option.')
    found = _resolve_label(label)
    updated = _call('patch', f'/labels/{found["id"]}', json=payload)
    _say(f'Updated the label {updated["name"]} ({updated["color"]}).')


@app.command(name='label-delete')
def todo_label_delete(
    environment: EnvironmentAnnotation,
    label: str = typer.Argument(..., help='The label (a name or an id).'),
    yes: YesAnnotation = False,
):
    """
    Delete a label. The tasks that had it keep their other labels.

    Example:
        todo label-delete local home
    """
    select_environment(environment)
    found = _resolve_label(label)
    _confirm(
        f'Delete the label {found["name"]} ({found["task_count"]} task'
        f'{"" if found["task_count"] == 1 else "s"})?',
        yes,
    )
    _call('delete', f'/labels/{found["id"]}')
    _say(f'Deleted the label {found["name"]}.')


# -- Commands: projects --------------------------------------------------------------------------


@app.command(name='projects')
def todo_projects(
    environment: EnvironmentAnnotation,
    archived: bool = typer.Option(False, '--archived', help='The archived projects instead.'),
    everything: bool = typer.Option(False, '--all', help='Active and archived projects.'),
):
    """
    List your projects, with their open and closed task counts and their sections.

    Example:
        todo projects local
        todo projects local --archived
    """
    select_environment(environment)
    which = 'all' if everything else 'true' if archived else 'false'
    projects = _call('get', '/projects', params={'archived': which})
    if not projects:
        _say('No projects. Make one with `project-add`.')
        return
    table = Table(title='Your projects')
    table.add_column('Name')
    table.add_column('Open', justify='right', style='magenta')
    table.add_column('Closed', justify='right')
    table.add_column('Sections')
    table.add_column('', no_wrap=True)
    table.add_column('ID', style='cyan', no_wrap=True)
    for project in projects:
        table.add_row(
            Text(project['name'], style=project['color']),
            str(project['open_count']),
            str(project['closed_count']),
            Text(', '.join(section['name'] for section in project['sections'])),
            'archived' if project['archived_at'] else '',
            project['id'],
        )
    _console().print(table)


@app.command(name='project-add')
def todo_project_add(
    environment: EnvironmentAnnotation,
    name: list[str] = typer.Argument(..., help='The name of the project.'),
    color: str = typer.Option('', '--color', help='#rrggbb (default: sage green).'),
    description: str = typer.Option('', '--description', help='Markdown, shown on its page.'),
):
    """
    Add a project. Projects are free.

    Example:
        todo project-add local Website relaunch --color "#5b8def"
    """
    select_environment(environment)
    project = _call(
        'post',
        '/projects',
        json={'name': ' '.join(name), 'color': color, 'description': description},
    )
    _say(f'Added the project {project["name"]} ({project["color"]}).')


@app.command(name='project-edit')
def todo_project_edit(
    environment: EnvironmentAnnotation,
    project: str = typer.Argument(..., help='The project (a name or an id).'),
    name: str | None = typer.Option(None, '--name', help='Rename it.'),
    color: str | None = typer.Option(None, '--color', help='#rrggbb.'),
    description: str | None = typer.Option(None, '--description', help='Markdown.'),
):
    """
    Rename, recolour or describe a project: only what you pass is changed.

    Example:
        todo project-edit local "Website relaunch" --name Website --color "#445566"
    """
    select_environment(environment)
    payload = {
        key: value
        for key, value in (('name', name), ('color', color), ('description', description))
        if value is not None
    }
    if not payload:
        raise _fail('Nothing to change: pass at least one option.')
    found = _resolve_project(project)
    updated = _call('patch', f'/projects/{found["id"]}', json=payload)
    _say(f'Updated the project {updated["name"]}.')


@app.command(name='project-archive')
def todo_project_archive(
    environment: EnvironmentAnnotation,
    project: str = typer.Argument(..., help='The project (a name or an id).'),
    yes: YesAnnotation = False,
):
    """
    Archive a project: nothing is deleted. Its open tasks leave Today, Upcoming and All, are never
    overdue, and their reminders wait until you unarchive it.

    Example:
        todo project-archive local "Website relaunch"
    """
    select_environment(environment)
    found = _resolve_project(project)
    if found['archived_at']:
        _say(f'{found["name"]} is already archived.')
        return
    count = found['open_count']
    _confirm(
        f'Archive {found["name"]}? Its {count} open task{"" if count == 1 else "s"} '
        'leave your lists and their reminders wait.',
        yes,
    )
    _call('post', f'/projects/{found["id"]}/archive', json={})
    _say(f'Archived {found["name"]}.')


@app.command(name='project-unarchive')
def todo_project_unarchive(
    environment: EnvironmentAnnotation,
    project: str = typer.Argument(..., help='The project (a name or an id).'),
):
    """
    Make an archived project active again. Each task that missed reminders gets one.

    Example:
        todo project-unarchive local "Website relaunch"
    """
    select_environment(environment)
    found = _resolve_project(project)
    if not found['archived_at']:
        _say(f'{found["name"]} is not archived.')
        return
    _call('post', f'/projects/{found["id"]}/unarchive', json={})
    _say(f'{found["name"]} is active again.')


@app.command(name='project-delete')
def todo_project_delete(
    environment: EnvironmentAnnotation,
    project: str = typer.Argument(..., help='The project (a name or an id).'),
    yes: YesAnnotation = False,
):
    """
    Delete a project. Its tasks are kept: they are then in no project.

    Example:
        todo project-delete local "Website relaunch"
    """
    select_environment(environment)
    found = _resolve_project(project)
    count = found['open_count'] + found['closed_count']
    _confirm(
        f'Delete the project {found["name"]}? Its {count} task{"" if count == 1 else "s"} '
        f'{"is" if count == 1 else "are"} kept, in no project.',
        yes,
    )
    _call('delete', f'/projects/{found["id"]}')
    _say(f'Deleted the project {found["name"]}.')


@app.command(name='section-add')
def todo_section_add(
    environment: EnvironmentAnnotation,
    project: str = typer.Argument(..., help='The project (a name or an id).'),
    name: list[str] = typer.Argument(..., help='The name of the section.'),
):
    """
    Add a section to a project.

    Example:
        todo section-add local "Website relaunch" Design
    """
    select_environment(environment)
    found = _resolve_project(project)
    _call('post', f'/projects/{found["id"]}/sections', json={'name': ' '.join(name)})
    _say(f'Added the section {" ".join(name)} to {found["name"]}.')


@app.command(name='section-rename')
def todo_section_rename(
    environment: EnvironmentAnnotation,
    project: str = typer.Argument(..., help='The project (a name or an id).'),
    section: str = typer.Argument(..., help='The section (a name or an id).'),
    name: list[str] = typer.Argument(..., help='Its new name.'),
):
    """
    Rename a section of a project.

    Example:
        todo section-rename local "Website relaunch" Design Visuals
    """
    select_environment(environment)
    found = _resolve_project(project)
    target = _match_section(found, section)
    _call(
        'patch',
        f'/projects/{found["id"]}/sections/{target["id"]}',
        json={'name': ' '.join(name)},
    )
    _say(f'Renamed the section {target["name"]} to {" ".join(name)}.')


@app.command(name='section-delete')
def todo_section_delete(
    environment: EnvironmentAnnotation,
    project: str = typer.Argument(..., help='The project (a name or an id).'),
    section: str = typer.Argument(..., help='The section (a name or an id).'),
    yes: YesAnnotation = False,
):
    """
    Delete a section. Its tasks are kept: they stay in the project, in no section.

    Example:
        todo section-delete local "Website relaunch" Design
    """
    select_environment(environment)
    found = _resolve_project(project)
    target = _match_section(found, section)
    _confirm(
        f'Delete the section {target["name"]} of {found["name"]}? Its tasks stay in the project.',
        yes,
    )
    _call('delete', f'/projects/{found["id"]}/sections/{target["id"]}')
    _say(f'Deleted the section {target["name"]}.')


# -- Commands: reminders -------------------------------------------------------------------------


@app.command(name='remind')
def todo_remind(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    minutes_before: int = typer.Argument(..., help='Minutes before the due time (0 to 43200).'),
    channel: str | None = typer.Option(
        None, '--channel', help='in_app, email or both (default: your default channel).'
    ),
):
    """
    Add a reminder to a task that has a due date.

    Example:
        todo remind local 1234 60 --channel email
    """
    select_environment(environment)
    payload: dict = {'minutes_before': minutes_before}
    if _channel(channel):
        payload['channel'] = channel
    reminder = _call('post', f'/tasks/{number}/reminders', json=payload)
    _say(
        f'Reminder {reminder["id"]}: {reminder["minutes_before"]} minutes before '
        f'({reminder["channel"]}), at {reminder["fire_at"]}.'
    )


@app.command(name='unremind')
def todo_unremind(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    reminder_id: str = typer.Argument(..., help='The reminder id (shown by `get`).'),
):
    """
    Remove a reminder from a task.

    Example:
        todo unremind local 1234 1b6e2f4a-...
    """
    select_environment(environment)
    _call('delete', f'/tasks/{number}/reminders/{reminder_id}')
    _say('Reminder removed.')


# -- Commands: files -----------------------------------------------------------------------------


class UploadFailed(Exception):
    """One file's upload failed; the message says why. The batch carries on with the next file."""


def _cancel_upload(attachment_id: str) -> None:
    """Best effort: tell the API this upload will not be completed, so it stops counting toward the
    task's file cap and its object is removed. Idempotent on the server; a failure here is only
    logged (the server abandons the upload after 24 hours anyway)."""
    try:
        response = api_request(
            'post',
            api_url(f'/todo/attachments/{attachment_id}/cancel'),
            headers=get_headers(CLI),
            exit_on_error=False,
        )
    except NetworkError as e:
        logger.warning(escape(f'Could not cancel the upload {attachment_id}: {e}'))
        return
    if not response.ok:
        logger.warning(
            escape(f'Could not cancel the upload {attachment_id}: {_error_message(response)}')
        )


def _upload_file(number: int, path: Path, *, for_comment: bool = False) -> dict:
    """Attach one file: `POST .../attachments` for a presigned URL, `PUT` the bytes to it with the
    headers the API returned, then `POST .../complete`. Returns the attachment. With `for_comment`
    the file is a draft for a comment (attached by sending its id with the comment).

    Raises:
        UploadFailed: the API refused the file (with its message), or the `PUT` failed.
    """
    headers = get_headers(CLI)
    content_type = mimetypes.guess_type(path.name)[0] or ''
    start = api_request(
        'post',
        api_url(f'/todo/tasks/{number}/attachments'),
        json={
            'filename': path.name,
            'size': path.stat().st_size,
            'content_type': content_type,
            'checksum_sha256': _sha256_base64_file(path),
            **({'for_comment': True} if for_comment else {}),
        },
        headers=headers,
        exit_on_error=False,
    )
    if not start.ok:
        raise UploadFailed(_error_message(start))
    ticket = start.json()
    attachment_id = ticket['attachment_id']

    # From here on a row is pending on the server, counting toward the task's file cap: any failure
    # cancels it (`POST /attachments/{id}/cancel`) instead of leaving it for the 24 hour sweep.
    try:
        put = api_request(
            'put',
            ticket['url'],
            data=path.read_bytes(),
            headers=ticket['headers'],
            timeout=FILE_UPLOAD_TIMEOUT_SECONDS,
            exit_on_error=False,
        )
    except (NetworkError, OSError) as e:
        _cancel_upload(attachment_id)
        raise UploadFailed(f'upload failed: {e}') from e
    if not put.ok:
        _cancel_upload(attachment_id)
        raise UploadFailed(f'the upload to storage failed ({put.status_code})')

    try:
        complete = api_request(
            'post',
            api_url(f'/todo/tasks/{number}/attachments/{attachment_id}/complete'),
            headers=headers,
            exit_on_error=False,
        )
    except NetworkError:
        _cancel_upload(attachment_id)
        raise
    if not complete.ok:
        _cancel_upload(attachment_id)  # a no-op for an upload the server already discarded
        raise UploadFailed(_error_message(complete))
    return complete.json()


@app.command(name='upload')
def todo_upload(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    paths: list[Path] = typer.Argument(..., help='The files to attach.'),
):
    """
    Attach files to a task: each goes straight to storage, then is verified. An empty file (or a
    folder) is skipped with a warning; a file that fails doesn't stop the rest (the exit code is
    non-zero if any did). Files are billed as storage once attached.

    Example:
        todo upload local 1234 contract.pdf photo.jpg
    """
    select_environment(environment)
    get_headers(CLI)  # not logged in: say so before reading any file

    files: list[Path] = []
    for path in paths:
        if not path.exists():
            raise _fail(f'Not found: {path}')
        if not path.is_file():
            logger.warning(escape(f'Skipping a folder: {path}'))
        elif path.stat().st_size == 0:
            logger.warning(escape(f'Skipping empty file: {path}'))
        else:
            files.append(path)
    if not files:
        raise _fail('No files to upload.')

    failed = 0
    for path in files:
        try:
            attachment = _upload_file(number, path)
        except (UploadFailed, NetworkError) as e:
            failed += 1
            logger.error(escape(f'{path.name}: {e}'))
        else:
            _say(f'{path.name}: attached as {attachment["id"]}')

    _say(f'{len(files) - failed} of {len(files)} attached.')
    if failed:
        raise typer.Exit(1)


@app.command(name='download')
def todo_download(
    environment: EnvironmentAnnotation,
    attachment_id: str = typer.Argument(..., help='The attachment id (shown by `get`).'),
    out: Path | None = typer.Option(
        None, '--out', '-o', help='Where to save it: a file, or a folder (default: here).'
    ),
    force: bool = typer.Option(False, '--force', help='Overwrite an existing file.'),
):
    """
    Download an attachment. It is written to a temporary file next to the target and renamed into
    place only when complete, so a dropped connection never leaves a cut-off file.

    Example:
        todo download local 1b6e2f4a-... --out ./contract.pdf
    """
    select_environment(environment)
    link = _call('get', f'/attachments/{attachment_id}/url', params={'disposition': 'attachment'})
    name = Path(link['filename'].replace('\\', '/')).name or 'attachment'
    target = out if out is not None else Path(name)
    if target.is_dir():
        target = target / name
    if not target.parent.is_dir():
        raise _fail(f'Not a folder: {target.parent}')
    if target.exists() and not force:
        raise _fail(f'{target} already exists. Use --force to overwrite it.')

    import requests

    # The presigned URL carries its own authorisation: the API token is never sent to storage.
    response = api_request('get', link['url'], stream=True, timeout=FILE_DOWNLOAD_TIMEOUT_SECONDS)
    try:
        if not response.ok:
            raise _fail(f'The download failed ({response.status_code}).')
        partial = target.with_name(f'.{target.name}.part')
        size = 0
        try:
            with partial.open('wb') as fh:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    fh.write(chunk)
                    size += len(chunk)
            partial.replace(target)
        except (requests.exceptions.RequestException, OSError) as e:
            partial.unlink(missing_ok=True)
            raise _fail(f'Download failed, nothing written to {target}: {e}') from e
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    finally:
        response.close()
    _say(f'Wrote {target} ({size:,} bytes).')


@app.command(name='rename-file')
def todo_rename_file(
    environment: EnvironmentAnnotation,
    attachment_id: str = typer.Argument(..., help='The attachment id (shown by `get`).'),
    name: list[str] = typer.Argument(..., help='The new name, without the extension.'),
):
    """
    Rename an attachment (a task's file or a comment's). Give the name without the extension,
    which the file keeps: renaming 2026-11-21-10-45-30.webp to Whiteboard makes Whiteboard.webp.

    Example:
        todo rename-file local 1b6e2f4a-... Whiteboard after the meeting
    """
    select_environment(environment)
    attachment = _call('patch', f'/attachments/{attachment_id}', json={'filename': ' '.join(name)})
    _say(f'Renamed to {attachment["filename"]}.')


# -- Commands: saved filters ---------------------------------------------------------------------


def _resolve_filter(ref: str) -> dict:
    """A saved filter by id or by name (ignoring case)."""
    filters = _call('get', '/filters')
    for saved in filters:
        if saved['id'] == ref.strip().lower() or _norm(saved['name']) == _norm(ref):
            return saved
    raise _fail(f'No saved filter called "{ref}". See `filters`.')


def _filter_counts() -> dict[str, int | None]:
    """How many open tasks each saved filter selects (`GET /filters/counts`), `{}` when they
    can't be had (an older server, or an error): the list is shown without them."""
    try:
        response = _request('get', '/filters/counts', exit_on_error=False)
    except NetworkError:
        return {}
    if not response.ok:
        return {}
    body = response.json()
    return body if isinstance(body, dict) else {}


@app.command(name='filters')
def todo_filters(environment: EnvironmentAnnotation):
    """
    List your saved filters, with how many open tasks each selects ("-" when that can't be told
    now).

    Example:
        todo filters local
    """
    select_environment(environment)
    filters = _call('get', '/filters')
    if not filters:
        _say('No saved filters. Save one from the Search page of the website.')
        return
    counts = _filter_counts()
    table = Table(title='Your saved filters')
    table.add_column('Name', no_wrap=True)
    table.add_column('Selects', overflow='fold')
    table.add_column('Open', justify='right', no_wrap=True)
    table.add_column('ID', style='cyan', no_wrap=True)
    for saved in filters:
        count = counts.get(saved['id'])
        table.add_row(
            Text(saved['name']),
            Text(saved['summary'] or 'all open tasks'),
            '-' if count is None else str(count),
            saved['id'],
        )
    _console().print(table)


@app.command(name='filter-run')
def todo_filter_run(
    environment: EnvironmentAnnotation,
    filter_: str = typer.Argument(..., metavar='FILTER', help='The filter (an id or a name).'),
    limit: int = typer.Option(50, '--limit', help='Max tasks to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of tasks to skip.'),
):
    """
    Run a saved filter: the tasks it selects.

    Example:
        todo filter-run local 1b6e2f4a-...
        todo filter-run local rent --limit 20
    """
    select_environment(environment)
    saved = _resolve_filter(filter_)
    body = _call('get', f'/filters/{saved["id"]}/tasks', params={'limit': limit, 'offset': offset})
    _print_tasks(body, saved['name'])


# -- Commands: settings and the inbox ------------------------------------------------------------


def _print_settings(settings: dict) -> None:
    reminder = settings['default_reminder_minutes']
    default_reminder = (
        'none'
        if reminder is None
        else f'{reminder} minutes before ({settings["default_reminder_channel"]})'
    )
    muted = settings.get('muted_overdue')
    rows = [
        ('Default due time', settings['default_due_time'][:5]),
        ('Default reminder', default_reminder),
        ('Default channel', settings['default_reminder_channel']),
        ('Snooze for', _snooze_text(settings.get('snooze_minutes'))),
        ('Muted overdue on Today', _MUTED_OVERDUE_TEXT.get(muted, muted) if muted else '-'),
        (
            'Accepted senders',
            ', '.join(settings['sender_allowlist']) or 'anyone (see `settings` on the website)',
        ),
        (
            'Tasks this month',
            f'{settings["tasks_created_this_month"]} '
            f'({settings["free_tasks_per_month"]} free, then {settings["task_price"]} credits each)',
        ),
        ('Credits', f'{settings["credits_balance"]:,}' if 'credits_balance' in settings else '-'),
        ('Files stored', f'{settings["stored_bytes"]:,} bytes'),
        ('Storage cost', f'{settings["storage_credits_per_day"]} credits a day'),
    ]
    if 'max_attachment_bytes' in settings:
        rows.append(('Largest file', f'{settings["max_attachment_bytes"]:,} bytes'))
    if 'max_attachments_per_task' in settings:
        rows.append(('Files per task', str(settings['max_attachments_per_task'])))
    if settings['creation_blocked']:
        rows.append(('New tasks', 'blocked until a repeating task is paid for: top up'))
    if settings['storage_frozen']:
        rows.append(('Storage', 'paused: top up to upload or download files'))
    table = Table(show_header=False, box=None)
    table.add_column(style='cyan', no_wrap=True)
    table.add_column()
    for label, value in rows:
        table.add_row(label, Text(value))
    _console().print(table)


@app.command(name='settings')
def todo_settings(
    environment: EnvironmentAnnotation,
    default_due_time: str | None = typer.Option(
        None,
        '--default-due-time',
        help='HH:MM: the time a task with a date and no time is treated as due.',
    ),
    default_reminder: str | None = typer.Option(
        None,
        '--default-reminder',
        help='Minutes before the due time of the reminder new tasks get, or "none".',
    ),
    default_channel: str | None = typer.Option(
        None, '--default-channel', help='in_app, email or both.'
    ),
    snooze: int | None = typer.Option(
        None,
        '--snooze',
        help='Minutes the Snooze button of a pushed reminder waits: 15, 60, 480 or 1440.',
    ),
    muted_overdue: str | None = typer.Option(
        None,
        '--muted-overdue',
        help='Where an overdue On hold or Blocked task shows on Today: dimmed, hidden or section.',
    ),
):
    """
    Show the settings and this month's usage; pass options to change the defaults.

    Example:
        todo settings local
        todo settings local --default-due-time 08:30 --default-reminder 15 --default-channel both
        todo settings local --default-reminder none
        todo settings local --snooze 60
        todo settings local --muted-overdue section
    """
    select_environment(environment)
    payload: dict = {}
    if default_due_time is not None:
        try:
            payload['default_due_time'] = datetime.strptime(default_due_time, '%H:%M').strftime(
                '%H:%M'
            )
        except ValueError:
            raise typer.BadParameter('use HH:MM, like 09:00.') from None
    if default_reminder is not None:
        if default_reminder.lower() == 'none':
            payload['default_reminder_minutes'] = None
        else:
            try:
                payload['default_reminder_minutes'] = int(default_reminder)
            except ValueError:
                raise typer.BadParameter('use a number of minutes, or "none".') from None
    if _channel(default_channel):
        payload['default_reminder_channel'] = default_channel
    if snooze is not None:
        if snooze not in SNOOZE_MINUTES:
            raise typer.BadParameter('use 15, 60, 480 or 1440 minutes.')
        payload['snooze_minutes'] = snooze
    if muted_overdue is not None:
        if muted_overdue not in MUTED_OVERDUE_CHOICES:
            raise typer.BadParameter('use dimmed, hidden or section.')
        payload['muted_overdue'] = muted_overdue

    if payload:
        settings = _call('patch', '/settings', json=payload)
        _say('Saved.')
    else:
        settings = _call('get', '/settings')
    _print_settings(settings)


@app.command(name='inbox')
def todo_inbox(environment: EnvironmentAnnotation):
    """
    Show the address tasks can be emailed to (the subject becomes the title, the body the
    description, the attachments the task's files).

    Example:
        todo inbox local
    """
    select_environment(environment)
    inbox = _call('get', '/inbox')
    _say(f'Email tasks to: {inbox["address"]}')
    senders = inbox['sender_allowlist']
    _say(f'Accepted senders: {", ".join(senders) if senders else "anyone"}')
    emails = inbox.get('recent_emails') or []
    if not emails:
        return
    table = Table(title='Recent emails')
    table.add_column('Received', no_wrap=True)
    table.add_column('From', overflow='fold')
    table.add_column('Subject', overflow='fold')
    table.add_column('Outcome', no_wrap=True)
    table.add_column('Task', style='cyan', no_wrap=True, justify='right')
    for email in emails:
        outcome = email['status'].replace('_', ' ')
        table.add_row(
            email['received_at'] or '',
            Text(email['from_address']),
            Text(email['subject']),
            outcome + (f' ({email["note"]})' if email['note'] else ''),
            f'#{email["task"]}' if email['task'] else '',
        )
    _console().print(table)


@app.command(name='regenerate')
def todo_regenerate(environment: EnvironmentAnnotation, yes: YesAnnotation = False):
    """
    Retire the email address now and issue a new one. Mail sent to the old address is dropped.

    Example:
        todo regenerate local
    """
    select_environment(environment)
    get_headers(CLI)
    _confirm('Retire the current address? Mail sent to it from now on is dropped.', yes)
    inbox = _call('post', '/inbox/regenerate')
    _say(f'Your new address: {inbox["address"]}')


# -- Commands: exports (issue #326) -------------------------------------------------------------


def _size_text(size: int) -> str:
    """`884 B`, `12.3 MB` (1,024-based, like the website)."""
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{int(value)} B' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{size} B'  # pragma: no cover - the loop always returns


def _range_body(range_: str | None, date_from: str | None, date_to: str | None) -> dict:
    """The "Task created" range as the export endpoints take it. Checked here, before any
    request: `--range` is one of `EXPORT_RANGES`, and the dates go with `--range custom` (or with
    no `--range`, which the API reads as Custom)."""
    body: dict = {}
    if range_:
        key = range_.strip().lower().replace('_', '-')
        if key not in EXPORT_RANGES:
            raise _fail(f'Choose --range {", ".join(EXPORT_RANGES)}.')
        body['date_range'] = EXPORT_RANGES[key]
        if EXPORT_RANGES[key] != 'custom' and (date_from or date_to):
            raise _fail('--from and --to go with --range custom, not with another range.')
    for name, value in (('date_from', date_from), ('date_to', date_to)):
        if value:
            body[name] = value
    return body


def _export_path(export_id: str, suffix: str = '') -> str:
    return f'/exports/{export_id}{suffix}'


def _describe_quote(quote: dict) -> None:
    files = quote['file_count']
    parts = quote['part_count']
    _say(
        f'{files} file{"" if files == 1 else "s"} ({_size_text(quote["total_bytes"])}) in '
        f'{parts} zip file{"" if parts == 1 else "s"} of about '
        f'{_size_text(quote["part_bytes"])} each, with a tasks.xlsx of the tasks and comments.'
    )
    _say(
        f'Cost: {_credits(quote["cost"])} ({quote["price_per_file"]} per file). '
        f'Your balance is {quote["balance"]}. They stay available for {quote["retention_days"]} '
        'days.'
    )


def _fetch_hint(export_id: str, out: Path) -> str:
    return (
        f'Fetch it later with: todo export-download {current_environment()} {export_id} '
        f'--out {shlex.quote(str(out))}'
    )


def _refused_creating(response) -> str:
    """Why creating the export was refused with a 409 (nothing was charged): another export is
    being prepared, or the cost is no longer the one that was quoted. The API words are for an API
    client (its cost message says to send `expected_cost`), so a changed cost is put in the CLI's
    own words and says to run the command again for the new quote."""
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get('detail') if isinstance(body, dict) else None
    if isinstance(detail, str) and detail.startswith('The cost changed'):
        return (
            'The cost changed since it was quoted, so nothing was charged. Run the command again '
            'to see the new quote.'
        )
    return (
        f'{_error_message(response).rstrip(".")}. Check `todo exports {current_environment()}`, then '
        'try again.'
    )


def _wait_for_export(export_id: str, minutes: float, hint: str) -> dict:
    """Poll the export until it is no longer being prepared; returns it. Ctrl-C, running out of
    time and an error that ends the wait all leave it building (it carries on in the background)
    and say how to fetch it later. A dropped connection, a 5xx and a 429 that `api_request` gave
    up on are not errors: they are tried again until the time is up."""
    limit = minutes * 60
    waited = 0.0
    delay = float(EXPORT_POLL_SECONDS)
    shown: int | None = None
    _say('Preparing your zip files (Ctrl-C stops waiting; the export carries on without you)...')
    try:
        while True:
            problem: str | None = None
            try:
                response = _request('get', _export_path(export_id), exit_on_error=False)
            except NetworkError as e:
                problem = f'Could not reach the site ({e})'
            else:
                if response.status_code >= 500 or response.status_code == 429:
                    problem = f'The site answered {response.status_code}'
                else:
                    try:
                        _check(response)
                    except typer.Exit:
                        _say(hint)
                        raise
                    export = response.json()
                    if export['status'] != 'building':
                        return export
                    built = sum(1 for part in export['parts'] if part['built'])
                    if built != shown:
                        shown = built
                        _say(f'Written so far: {built} of {export["part_count"]} zip files.')
            if problem:
                logger.warning(escape(f'{problem}; trying again...'))
            if waited >= limit:
                _say(f'Still not ready after {format_wait(limit)}. {hint}')
                raise typer.Exit(1)
            time.sleep(delay)
            waited += delay
            delay = min(delay * 1.5, float(EXPORT_POLL_MAX_SECONDS))
    except KeyboardInterrupt:
        _say(f'Stopped waiting. The export is still being prepared. {hint}')
        raise typer.Exit(130) from None


class DownloadFailed(Exception):
    """One zip file could not be downloaded; the message says why. The rest carry on."""


def _download_part(export_id: str, part: dict, target: Path) -> None:
    """Download one zip file: ask the API for a short-lived URL, then stream it **without the API
    token** (it is a presigned storage URL) to `.<name>.part` next to `target`, renamed into place
    only once complete and of the size the export says, so an interrupted download never leaves a
    cut-off zip behind.

    Raises:
        DownloadFailed: with the reason.
    """
    import requests

    try:
        info = _request(
            'get', _export_path(export_id, f'/parts/{part["number"]}/url'), exit_on_error=False
        )
    except NetworkError as e:
        raise DownloadFailed(str(e)) from e
    if not info.ok:
        raise DownloadFailed(_error_message(info))
    ticket = info.json()
    try:
        response = api_request(
            'get',
            ticket['url'],
            stream=True,
            exit_on_error=False,
            timeout=FILE_DOWNLOAD_TIMEOUT_SECONDS,
        )
    except NetworkError as e:
        raise DownloadFailed(f'download failed: {e}') from e
    try:
        if not response.ok:
            raise DownloadFailed(f'the download from storage failed ({response.status_code})')
        partial = target.with_name(f'.{target.name}.part')
        size = 0
        try:
            with partial.open('wb') as fh:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    fh.write(chunk)
                    size += len(chunk)
            if size != ticket['size_bytes']:
                raise DownloadFailed(f'received {size} bytes, expected {ticket["size_bytes"]}')
            partial.replace(target)
        except (requests.exceptions.RequestException, OSError) as e:
            partial.unlink(missing_ok=True)
            raise DownloadFailed(f'download failed: {e}') from e
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    finally:
        response.close()


def _download_export(
    export: dict, out: Path, *, force: bool, resume: bool = False, paid: bool = False
) -> None:
    """Download every zip file of a ready export into the folder `out` (created if needed).

    Before anything is downloaded, a file of the same name is refused unless `force` is set
    (`--force` replaces it). With `resume` (`export-download`, for finishing an interrupted run) a
    file that is already complete (the size the export says) is skipped instead of refused, and
    only one of a different size is. `paid` says the export was just created and charged (a clash
    then points at `export-download`, not at making another). Exits 1 if any file failed (the
    others are still downloaded).

    Resuming compares sizes only (the API has no checksum per file), so use a folder per export.
    """
    parts = [part for part in export['parts'] if part['built']]
    if not parts:
        raise _fail('This export has no zip files to download.')
    targets: list[tuple[dict, Path]] = []
    for part in parts:
        name = Path(part['filename']).name
        if name in ('', '.', '..'):
            raise _fail(f'The site gave an unusable file name: {part["filename"]!r}.')
        targets.append((part, out / name))
    clashes = [
        target
        for part, target in targets
        if target.exists()
        and not force
        and not (resume and target.stat().st_size == part['size_bytes'])
    ]
    if clashes:
        listing = ', '.join(str(target) for target in clashes)
        if paid:
            raise _fail(
                f'{listing} already exists. The export is paid for and ready: run `todo '
                f'export-download {current_environment()} {export["id"]} --out <a folder for this '
                'export>` (add --force to replace the existing file) rather than exporting '
                'again, which would charge a second time.'
            )
        raise _fail(f'{listing} already exists. Use --force to replace it.')
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise _fail(f'Could not create {out}: {e}') from e

    failed = 0
    try:
        for part, target in targets:
            if resume and not force and target.exists():
                _say(f'{target.name}: already downloaded, skipped.')
                continue
            _say(f'{target.name}: downloading {_size_text(part["size_bytes"])}...')
            try:
                _download_part(export['id'], part, target)
            except DownloadFailed as e:
                failed += 1
                logger.error(escape(f'{target.name}: {e}'))
            else:
                _say(f'Wrote {target}.')
    except KeyboardInterrupt:
        # The part being written was cleaned up; the export is paid for and ready, so say how to
        # get the rest without paying again (as Ctrl-C while waiting does).
        _say(
            f'Stopped downloading. The export is ready and paid for. {_fetch_hint(export["id"], out)}'
        )
        raise typer.Exit(130) from None
    if failed:
        raise _fail(
            f'{failed} of {len(targets)} zip files failed. Run `todo export-download '
            f'{current_environment()} {export["id"]} --out {shlex.quote(str(out))}` to get the '
            'rest.'
        )
    _say(f'Downloaded {len(targets)} zip file{"" if len(targets) == 1 else "s"} to {out}.')


@app.command(name='export-files')
def todo_export_files(
    environment: EnvironmentAnnotation,
    date_range: str | None = typer.Option(
        None,
        '--range',
        help='Tasks created in: any, last-15-days, last-30-days, this-month, this-quarter, '
        'this-year or custom (default: any, or custom when --from/--to is given).',
    ),
    date_from: str | None = typer.Option(
        None, '--from', help='Earliest creation date, YYYY-MM-DD (with --range custom).'
    ),
    date_to: str | None = typer.Option(
        None, '--to', help='Latest creation date, YYYY-MM-DD, inclusive (with --range custom).'
    ),
    out: Path = typer.Option(
        Path('.'), '--out', '-o', help='The folder to save the zip files in (created if needed).'
    ),
    yes: bool = typer.Option(
        False, '--yes', '-y', help='Do not ask to confirm the cost (and accept whatever it is).'
    ),
    force: bool = typer.Option(False, '--force', help='Replace zip files that already exist.'),
    wait: float = typer.Option(
        EXPORT_WAIT_MINUTES, '--wait', help='Minutes to wait for the zip files to be prepared.'
    ),
):
    """
    Download the files attached to the tasks you created in a date range (and to their comments;
    never the Trash) as zip files: each of about 100 MB, independent, the first with a tasks.xlsx
    of the tasks and comments. It costs credits per file (nothing is asked for a free export), so
    the cost is shown and you are asked to confirm it first. The zip files are prepared in the
    background; this command waits for them and downloads them. Ctrl-C stops waiting without
    cancelling the export. Use a folder of its own for each export: files are told apart by name
    and size only.

    Example:
        todo export-files local --out ./my-task-files
        todo export-files local --out ./2026 --from 2026-01-01 --to 2026-12-31
        todo export-files prod --range this-year --yes
    """
    select_environment(environment)
    get_headers(CLI)
    body = _range_body(date_range, date_from, date_to)
    if out.exists() and not out.is_dir():
        raise _fail(f'Not a folder: {out}')
    # Before anything is quoted or charged: a folder that already holds zip files of an export
    # would clash with the new ones (their names are task-files-<date>[-part-NN-of-MM].zip).
    existing = sorted(out.glob(EXPORT_ZIP_GLOB)) if out.is_dir() else []
    if existing and not force:
        raise _fail(
            f'{out} already holds {", ".join(p.name for p in existing)}. Use an empty folder '
            '(--out), or --force to replace files of the same name. Nothing was charged.'
        )

    quote = _call('post', '/exports/quote', json=body)
    if quote['file_count'] == 0:
        raise _fail(
            'There are no files to download in this range. Use `todo export-xlsx '
            f'{current_environment()} --out tasks.xlsx` for the task list.'
        )
    _describe_quote(quote)
    if quote['frozen']:
        raise _fail('Storage is paused until you add credits.')
    if not quote['affordable']:
        raise _fail(
            f'That costs {_credits(quote["cost"])} and your balance is {quote["balance"]}. '
            'Top up your credits on the website.'
        )

    # `--yes` accepts whatever it costs (no `expected_cost` is sent); otherwise the amount that was
    # shown and confirmed is sent, so a cost that changed meanwhile is refused, not charged. A free
    # export is not asked about.
    create_body = body
    if not yes:
        if quote['cost'] > 0 and not typer.confirm(
            f'This costs {_credits(quote["cost"])}. Continue?'
        ):
            raise _fail('Cancelled; nothing was charged.')
        create_body = {**body, 'expected_cost': quote['cost']}

    created = _request('post', '/exports', json=create_body)
    if created.status_code == 409:
        # Another export is being prepared, or the cost changed since it was quoted: either way
        # nothing was charged, and the message says which.
        raise _fail(_refused_creating(created))
    _check(created)
    export = created.json()
    if export['charged_credits']:
        _say(f'Charged {_credits(export["charged_credits"])}.')
    _say(f'Export {export["id"]} started.')

    export = _wait_for_export(export['id'], wait, _fetch_hint(export['id'], out))
    if export['status'] != 'ready':
        refunded = export['refunded_credits']
        raise _fail(
            f'The export {export["status"]}'
            + (f' and {_credits(refunded)} were refunded.' if refunded else '.')
        )
    if export['skipped_count']:
        _say(
            f'{export["skipped_count"]} file(s) had vanished and were left out '
            f'({_credits(export["refunded_credits"])} refunded).'
        )
    _download_export(export, out, force=force, paid=True)


@app.command(name='exports')
def todo_exports(
    environment: EnvironmentAnnotation,
    limit: int = typer.Option(20, '--limit', help='Max exports to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of exports to skip.'),
):
    """
    List your exports of files (see `export-files`), newest first.

    Example:
        todo exports local
    """
    select_environment(environment)
    body = _call('get', '/exports', params={'limit': limit, 'offset': offset})
    exports = body['results']
    if not exports:
        _say('No exports.')
        return

    table = Table(title='Your Exports')
    table.add_column('ID', style='cyan', no_wrap=True)
    table.add_column('Created', style='white', no_wrap=True)
    table.add_column('Status', style='green', no_wrap=True)
    table.add_column('Files', style='white', justify='right')
    table.add_column('Charged', style='magenta', justify='right')
    table.add_column('Expires', style='white', no_wrap=True)
    for export in exports:
        table.add_row(
            export['id'],
            export['created_at'][:10],
            export['status'],
            str(export['file_count']),
            str(export['charged_credits']),
            (export['expires_at'] or '')[:10],
        )
    _console().print(table)

    shown_through = body['offset'] + len(exports)
    if shown_through < body['count']:
        _say(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]}. '
            f'Use --offset {shown_through} to see more.'
        )


@app.command(name='export-get')
def todo_export_get(environment: EnvironmentAnnotation, export_id: str):
    """
    Show one export: its status, what it was made from, the cost and its zip files.

    Example:
        todo export-get local 1b6e2f4a-...
    """
    select_environment(environment)
    export = _call('get', _export_path(export_id))

    console = _console()
    selection = '; '.join(export['filters']['summary']) or 'every task'
    files = f'{export["file_count"]}'
    if export['skipped_count']:
        files += f' ({export["skipped_count"]} left out: gone when zipped)'
    ready_parts = sum(1 for part in export['parts'] if part['built'])
    rows = [
        ('ID', export['id']),
        ('Status', export['status']),
        ('Selection', selection),
        ('Files', files),
        ('Size', _size_text(export['total_bytes'])),
        ('Zip files', f'{export["part_count"]} ({ready_parts} written)'),
        ('Charged', _credits(export['charged_credits'])),
        ('Refunded', _credits(export['refunded_credits']) if export['refunded_credits'] else ''),
        ('Created', export['created_at']),
        ('Ready', export['ready_at'] or ''),
        ('Expires', export['expires_at'] or ''),
    ]
    for label, value in rows:
        if value:
            console.print(f'[cyan]{label}:[/cyan] {escape(str(value))}', highlight=False)

    if export['parts']:
        table = Table(title='Zip files')
        table.add_column('#', style='cyan', justify='right', no_wrap=True)
        table.add_column('Name', style='white')
        table.add_column('Files', style='white', justify='right')
        table.add_column('Size', style='white', justify='right')
        table.add_column('Written', style='green', no_wrap=True)
        for part in export['parts']:
            table.add_row(
                str(part['number']),
                part['filename'],
                str(part['file_count']),
                _size_text(part['size_bytes']) if part['built'] else '',
                'yes' if part['built'] else 'no',
            )
        console.print(table)


@app.command(name='export-download')
def todo_export_download(
    environment: EnvironmentAnnotation,
    export_id: str,
    out: Path = typer.Option(
        Path('.'), '--out', '-o', help='The folder to save the zip files in (created if needed).'
    ),
    force: bool = typer.Option(False, '--force', help='Replace zip files that already exist.'),
    wait: float = typer.Option(
        EXPORT_WAIT_MINUTES, '--wait', help='Minutes to wait if it is still being prepared.'
    ),
):
    """
    Download the zip files of an export (waits for it if it is still being prepared). Each is
    written to a temporary file and renamed when complete. A file that is already there with the
    right size is skipped, so an interrupted download can be finished by running this again; one
    with a different size is refused unless you pass --force. Use a folder of its own for each
    export: files are told apart by name and size only (there is no checksum), so a zip file of
    another export with the same name and size would be taken for this one. No credits are charged.

    Example:
        todo export-download local 1b6e2f4a-... --out ./my-task-files
    """
    select_environment(environment)
    get_headers(CLI)
    if out.exists() and not out.is_dir():
        raise _fail(f'Not a folder: {out}')
    export = _call('get', _export_path(export_id))
    if export['status'] == 'building':
        export = _wait_for_export(export_id, wait, _fetch_hint(export_id, out))
    if export['status'] != 'ready':
        raise _fail(
            f'This export is {export["status"]}, so there is nothing to download. '
            'Start a new one with `export-files`.'
        )
    _download_export(export, out, force=force, resume=True)


@app.command(name='export-delete')
def todo_export_delete(
    environment: EnvironmentAnnotation,
    export_id: str,
    yes: YesAnnotation = False,
):
    """
    Delete an export: its zip files are removed now. Nothing is refunded. An export that is still
    being prepared can't be deleted.

    Example:
        todo export-delete local 1b6e2f4a-...
    """
    select_environment(environment)
    get_headers(CLI)
    _confirm('The zip files are deleted now and nothing is refunded. Delete?', yes)
    _call('delete', _export_path(export_id))
    _say('Deleted.')


def _content_disposition_name(response) -> str | None:
    """The file name of a `Content-Disposition: attachment; filename="..."` header, if any."""
    match = re.search(r'filename="?([^";]+)"?', response.headers.get('Content-Disposition', ''))
    return Path(match.group(1).replace('\\', '/')).name if match else None


@app.command(name='export-xlsx')
def todo_export_xlsx(
    environment: EnvironmentAnnotation,
    out: Path = typer.Option(
        ..., '--out', '-o', help='Where to write the workbook: a file, or an existing folder.'
    ),
    date_range: str | None = typer.Option(
        None, '--range', help='As for `export-files`: tasks created in this range.'
    ),
    date_from: str | None = typer.Option(None, '--from', help='Earliest creation date.'),
    date_to: str | None = typer.Option(None, '--to', help='Latest creation date, inclusive.'),
    force: bool = typer.Option(False, '--force', help='Replace the file if it exists.'),
):
    """
    Export the tasks created in a date range as a spreadsheet (tasks.xlsx: a Tasks sheet and a
    Comments sheet, with the attached files' names). Free; the Trash is never exported. It is
    written to a temporary file next to --out and renamed into place only when complete.

    Example:
        todo export-xlsx local --out tasks.xlsx
        todo export-xlsx local --out ./exports --range this-year
        todo export-xlsx local --out q3.xlsx --from 2026-07-01 --to 2026-09-30 --force
    """
    select_environment(environment)
    get_headers(CLI)
    params = _range_body(date_range, date_from, date_to)
    # Checked before the request: it spends one of the few per-minute exports.
    into_folder = out.is_dir()
    if not into_folder and not out.parent.is_dir():
        raise _fail(f'Not a folder: {out.parent}')
    if not into_folder and out.exists() and not force:
        raise _fail(f'{out} already exists. Use --force to replace it.')

    import requests

    response = _request(
        'get',
        '/export.xlsx',
        params=params,
        stream=True,
        timeout=FILE_DOWNLOAD_TIMEOUT_SECONDS,
    )
    try:
        _check(response)
        target = out / (_content_disposition_name(response) or 'tasks.xlsx') if into_folder else out
        if into_folder and target.exists() and not force:
            raise _fail(f'{target} already exists. Use --force to replace it.')
        partial = target.with_name(f'.{target.name}.part')
        size = 0
        try:
            with partial.open('wb') as fh:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    fh.write(chunk)
                    size += len(chunk)
            partial.replace(target)
        except (requests.exceptions.RequestException, OSError) as e:
            partial.unlink(missing_ok=True)
            raise _fail(f'Export failed, nothing written to {target}: {e}') from e
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    finally:
        response.close()
    _say(f'Wrote {target} ({size:,} bytes).')


if __name__ == '__main__':
    app()
