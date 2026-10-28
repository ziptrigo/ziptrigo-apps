#!python
"""
To-do CLI

Command line interface for the To-do JWT API (`/api/todo/`, issue #187), on the model of
`admin/receipts.py` and `admin/shorturl.py`: log in, add tasks (a date in the title is read for
you: "pay rent every month on the 1st"), list and search them, read and edit one, complete,
postpone, trash and restore, comment, manage labels and reminders, attach and download files, run
saved filters, and see or change the settings and the address tasks can be emailed to.

A task is addressed by its number (the `1234` of `#1234`). The first 10 tasks of a month are free,
then each costs credits; commands that create tasks say what they charged.
"""

import base64
import hashlib
import mimetypes
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

REPEAT = '\N{CLOCKWISE OPEN CIRCLE ARROW}'
BELL = '\N{BELL}'

YesAnnotation = Annotated[bool, typer.Option('--yes', help='Do not ask before going ahead.')]
NumberAnnotation = Annotated[int, typer.Argument(help='The task number (1234 for #1234).')]

POSTPONE_PRESETS = {
    'tomorrow': 'tomorrow',
    'next-week': 'next_week',
    'next-weekend': 'next_weekend',
}
CHANNELS = ('in_app', 'email', 'both')


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


def _flags(task: dict) -> str:
    repeats = task['recurrence'] is not None and task['recurrence']['ended_at'] is None
    return ' '.join(
        flag for flag, wanted in ((REPEAT, repeats), (BELL, bool(task['reminders']))) if wanted
    )


def _credits(amount: int) -> str:
    return f'{amount} credit{"" if amount == 1 else "s"}'


def _task_table(tasks: list[dict], title: str) -> Table:
    table = Table(title=title)
    table.add_column('#', style='cyan', no_wrap=True, justify='right')
    table.add_column('Due', no_wrap=True)
    table.add_column('Pri', style='red', no_wrap=True)
    table.add_column('Title', overflow='fold')
    table.add_column('Labels', style='blue')
    table.add_column('', no_wrap=True)
    for task in tasks:
        marker = '\N{CHECK MARK} ' if task['completed_at'] else ''
        table.add_row(
            str(task['number']),
            _due(task),
            _priority(task),
            Text(marker + task['title']),
            Text(', '.join(label['name'] for label in task['labels'])),
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


def _print_tasks(body: dict, title: str) -> None:
    if not body['results']:
        _say('No tasks found.')
        return
    _console().print(_task_table(body['results'], title))
    _show_page(body)


def _task_line(task: dict) -> str:
    """`#1234 "Pay rent" due 2026-11-01 09:30 !1 Every month on the 1st`."""
    parts = [f'#{task["number"]} "{task["title"]}"']
    if task['due_date']:
        parts.append(f'due {_due(task)}')
    if task['priority']:
        parts.append(_priority(task))
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


def _channel(value: str | None) -> str | None:
    if value is not None and value not in CHANNELS:
        raise typer.BadParameter(f'use one of: {", ".join(CHANNELS)}.')
    return value


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
    password: str | None = typer.Argument(
        None, help=f'Password. Omit to be prompted, or set {PASSWORD_ENV}.'
    ),
):
    """
    Authenticate with the API and store the token.

    Example:
        todo login local me@example.com
    """
    select_environment(environment)
    password = password_from_prompt_or_env(password, PASSWORD_ENV)

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
        'all', '--view', help='today, upcoming, all (open tasks), completed or trash.'
    ),
    q: str = typer.Option(
        '', '--q', help='Search titles, descriptions and comments (* and ? are wildcards).'
    ),
    regex: bool = typer.Option(False, '--regex', help='Read --q as a regular expression.'),
    include_completed: bool = typer.Option(
        False, '--include-completed', help='With --view all, completed tasks too.'
    ),
    priority: str | None = typer.Option(
        None, '--priority', help='Priorities 0 (none) to 3, comma-separated.'
    ),
    label: list[str] | None = typer.Option(
        None, '--label', help='A label name or id, or "none"; repeat for several.'
    ),
    date_from: str | None = typer.Option(None, '--from', help='Earliest due date, YYYY-MM-DD.'),
    date_to: str | None = typer.Option(None, '--to', help='Latest due date, YYYY-MM-DD.'),
    sort: str | None = typer.Option(None, '--sort', help='date, priority, created or title.'),
    limit: int = typer.Option(50, '--limit', help='Max tasks to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of tasks to skip.'),
):
    """
    List your tasks (a number, the due date and time, the priority, the title, the labels, a
    repeat mark and a bell for reminders).

    Example:
        todo list local
        todo list local --view today
        todo list local --q "rent*" --include-completed
        todo list local --priority 1,2 --label home --from 2026-11-01 --to 2026-11-30
    """
    select_environment(environment)
    params: dict = {'view': view, 'limit': limit, 'offset': offset}
    if q:
        params['q'] = q
        if regex:
            params['mode'] = 'regex'
    if include_completed:
        params['include_completed'] = 'true'
    if label:
        params['label'] = ','.join(_label_ids(label, allow_none=True))
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
    repeat: str | None = typer.Option(
        None, '--repeat', help='An RFC 5545 rule, like FREQ=MONTHLY;BYMONTHDAY=1.'
    ),
    mode: str | None = typer.Option(
        None, '--mode', help='fixed (default) or from_completion: how a repeat is counted.'
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
    """
    select_environment(environment)
    payload: dict = {'title': ' '.join(title), 'parse_title': not no_parse}
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


@app.command(name='get')
def todo_get(environment: EnvironmentAnnotation, number: NumberAnnotation):
    """
    Show everything about one task: its details, the description (as Markdown), the comments, the
    reminders, the files and the tasks that mention it.

    Example:
        todo get local 1234
    """
    select_environment(environment)
    task = _call('get', f'/tasks/{number}')
    comments = _call('get', f'/tasks/{number}/comments')

    console = _console()

    def row(label: str, value: str) -> None:
        if value:
            console.print(Text.assemble((f'{label}: ', 'cyan'), value))

    console.print(Text(f'#{task["number"]} {task["title"]}', style='bold'))
    status = 'in the trash' if task['deleted_at'] else 'done' if task['completed_at'] else 'open'
    row('Status', status)
    row('Priority', _priority(task))
    row('Due', _due(task))
    recurrence = task['recurrence']
    if recurrence is not None:
        ended = ' (stopped)' if recurrence['ended_at'] else ''
        mode = recurrence['mode'].replace('_', ' ')
        row('Repeats', f'{recurrence["rrule_text"]}, {mode}{ended}')
    row('Parent', f'#{task["parent"]}' if task['parent'] else '')
    row('Labels', ', '.join(label['name'] for label in task['labels']))
    row(
        'Subtasks',
        f'{task["open_subtask_count"]} open of {task["subtask_count"]}'
        if task['subtask_count']
        else '',
    )
    row('Source', task['source'])
    row('Created', task['created_at'])
    row('Updated', task['updated_at'])
    row('Completed', task['completed_at'] or '')
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
    if task['referenced_by']:
        console.print(Text('\nReferenced by', style='cyan'))
        for source in task['referenced_by']:
            where = ' (in a comment)' if source['comment'] else ''
            console.print(Text(f'#{source["number"]} {source["title"]}{where}'))


@app.command(name='edit')
def todo_edit(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    title: str | None = typer.Option(None, '--title'),
    description: str | None = typer.Option(None, '--description'),
    priority: int | None = typer.Option(None, '--priority', help='0 (none), 1 (highest), 2 or 3.'),
    due: str | None = typer.Option(
        None, '--due', help='YYYY-MM-DD, or YYYY-MM-DDTHH:MM with a time.'
    ),
    clear_due: bool = typer.Option(False, '--clear-due', help='Remove the date (and the time).'),
    label: list[str] | None = typer.Option(
        None, '--label', help='Add a label (a name or id); repeat for several.'
    ),
    remove_label: list[str] | None = typer.Option(
        None, '--remove-label', help='Take a label off; repeat for several.'
    ),
):
    """
    Change a task: only what you pass is changed.

    Example:
        todo edit local 1234 --title "Pay the rent" --priority 1
        todo edit local 1234 --due 2026-11-05T09:00 --label home --remove-label later
        todo edit local 1234 --clear-due
    """
    select_environment(environment)
    if due and clear_due:
        raise typer.BadParameter('--due and --clear-due cannot be used together.')

    payload: dict = {}
    if title is not None:
        payload['title'] = title
    if description is not None:
        payload['description'] = description
    if priority is not None:
        payload['priority'] = priority
    if due:
        payload['due_date'], due_time = _parse_due(due)
        if due_time:
            payload['due_time'] = due_time
    elif clear_due:
        payload['due_date'] = None
    if label or remove_label:
        current = [item['id'] for item in _call('get', f'/tasks/{number}')['labels']]
        added = _label_ids(label)
        removed = set(_label_ids(remove_label))
        payload['labels'] = [
            *(item for item in current if item not in removed),
            *(item for item in added if item not in current and item not in removed),
        ]
    if not payload:
        raise _fail('Nothing to change: pass at least one option.')

    task = _call('patch', f'/tasks/{number}', json=payload)
    _say(f'Updated {_task_line(task)}')


@app.command(name='done')
def todo_done(
    environment: EnvironmentAnnotation,
    numbers: list[int] = typer.Argument(..., help='The task numbers.'),
):
    """
    Complete tasks (and their open subtasks). A repeating task creates its next occurrence.

    Example:
        todo done local 1234 1235
    """
    select_environment(environment)
    failed = 0
    for number in numbers:
        response = _request('post', f'/tasks/{number}/complete')
        if not response.ok:
            failed += 1
            logger.error(escape(f'#{number}: {_error_message(response)}'))
            continue
        _say(f'Completed #{number} "{response.json()["title"]}"')
    if failed:
        raise typer.Exit(1)


@app.command(name='undone')
def todo_undone(environment: EnvironmentAnnotation, number: NumberAnnotation):
    """
    Make a completed task open again.

    Example:
        todo undone local 1234
    """
    select_environment(environment)
    task = _call('post', f'/tasks/{number}/uncomplete')
    _say(f'Reopened {_task_line(task)}')


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
    Reschedule every overdue task to today, keeping their times.

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


@app.command(name='comment')
def todo_comment(
    environment: EnvironmentAnnotation,
    number: NumberAnnotation,
    text: list[str] = typer.Argument(..., help='The comment (Markdown).'),
):
    """
    Add a comment to a task.

    Example:
        todo comment local 1234 asked the landlord, see #1250
    """
    select_environment(environment)
    comment = _call('post', f'/tasks/{number}/comments', json={'body': ' '.join(text)})
    _say(f'Commented on #{number} ({comment["id"]}).')


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


def _upload_file(number: int, path: Path) -> dict:
    """Attach one file: `POST .../attachments` for a presigned URL, `PUT` the bytes to it with the
    headers the API returned, then `POST .../complete`. Returns the attachment.

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
        },
        headers=headers,
        exit_on_error=False,
    )
    if not start.ok:
        raise UploadFailed(_error_message(start))
    ticket = start.json()

    try:
        put = api_request(
            'put',
            ticket['url'],
            data=path.read_bytes(),
            headers=ticket['headers'],
            timeout=FILE_UPLOAD_TIMEOUT_SECONDS,
            exit_on_error=False,
        )
    except NetworkError as e:
        raise UploadFailed(f'upload failed: {e}') from e
    if not put.ok:
        raise UploadFailed(f'the upload to storage failed ({put.status_code})')

    complete = api_request(
        'post',
        api_url(f'/todo/tasks/{number}/attachments/{ticket["attachment_id"]}/complete'),
        headers=headers,
        exit_on_error=False,
    )
    if not complete.ok:
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


# -- Commands: saved filters ---------------------------------------------------------------------


def _resolve_filter(ref: str) -> dict:
    """A saved filter by id or by name (ignoring case)."""
    filters = _call('get', '/filters')
    for saved in filters:
        if saved['id'] == ref.strip().lower() or _norm(saved['name']) == _norm(ref):
            return saved
    raise _fail(f'No saved filter called "{ref}". See `filters`.')


@app.command(name='filters')
def todo_filters(environment: EnvironmentAnnotation):
    """
    List your saved filters.

    Example:
        todo filters local
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
        table.add_row(Text(saved['name']), Text(saved['summary'] or 'all open tasks'), saved['id'])
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
    rows = [
        ('Default due time', settings['default_due_time'][:5]),
        ('Default reminder', default_reminder),
        ('Default channel', settings['default_reminder_channel']),
        (
            'Accepted senders',
            ', '.join(settings['sender_allowlist']) or 'anyone (see `settings` on the website)',
        ),
        (
            'Tasks this month',
            f'{settings["tasks_created_this_month"]} '
            f'({settings["free_tasks_per_month"]} free, then {settings["task_price"]} credits each)',
        ),
        ('Files stored', f'{settings["stored_bytes"]:,} bytes'),
        ('Storage cost', f'{settings["storage_credits_per_day"]} credits a day'),
    ]
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
):
    """
    Show the settings and this month's usage; pass options to change the defaults.

    Example:
        todo settings local
        todo settings local --default-due-time 08:30 --default-reminder 15 --default-channel both
        todo settings local --default-reminder none
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


if __name__ == '__main__':
    app()
