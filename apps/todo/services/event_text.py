"""The sentence of an activity-log entry (issue #274): `describe(event)`.

It reads only the entry's `kind` and `data` (see `services.events` for what each kind carries), so
rendering a page of entries costs no query, and an entry reads right after the task, label or file it
mentions is gone. Dates and times inside it are written in the viewer's formats
(`apps.core.datetime_format`: the active ones, the signed-in user's on a page; the public wording
anywhere else). The result is plain text: the template escapes it.
"""

from datetime import date, time
from typing import Any

from apps.core.datetime_format import format_date, format_time

from ..models import EventKind, Resolution, TaskSource, TaskState
from . import recurrence

_REASONS = {
    'closed': 'was closed',
    'trashed': 'was moved to the Trash',
    'deleted': 'was deleted',
    'removed': 'was removed as a blocker',
}


def _state(value: object) -> str:
    try:
        return TaskState(value).label
    except ValueError:
        return str(value or 'To do')


def _resolution(value: object) -> str:
    try:
        return Resolution(value).label
    except ValueError:
        return str(value or '')


def _ref(ref: dict[str, Any] | None) -> str:
    """`#1200 Title` for a `{number, title}` snapshot."""
    if not ref:
        return 'a task'
    title = ' '.join(str(ref.get('title') or '').split())
    return f'#{ref.get("number")} {title}'.strip()


def _refs(refs: list[dict[str, Any]]) -> str:
    return ', '.join(_ref(ref) for ref in refs)


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _time(value: object) -> time | None:
    try:
        return time.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _when(day: object, clock: object) -> str:
    """`11/05/2026, 9:00 AM`, `11/05/2026`, or `no date`."""
    parsed = _date(day)
    if parsed is None:
        return 'no date'
    text = format_date(parsed, 'full')
    moment = _time(clock)
    return f'{text}, {format_time(moment)}' if moment is not None else text


def _rule(rrule: object) -> str:
    return recurrence.describe(str(rrule)) if rrule else ''


def _plural(count: int, noun: str) -> str:
    return f'{count} {noun}' if count == 1 else f'{count} {noun}s'


def _created(data: dict[str, Any]) -> str:
    source = data.get('source')
    previous = data.get('previous')
    if source == TaskSource.RECURRENCE and previous:
        text = f'Created as the next occurrence of #{previous.get("number")}'
    elif source == TaskSource.API:
        text = 'Created through the API'
    elif source == TaskSource.EMAIL:
        text = 'Created from an email'
    else:
        text = 'Created'
    if data.get('parent'):
        text += f', as a subtask of {_ref(data["parent"])}'
    if data.get('rrule'):
        text += f'. Repeats: {_rule(data["rrule"])}'
    return text


def _closed(data: dict[str, Any]) -> str:
    text = f'Closed as {_resolution(data.get("resolution"))}'
    if data.get('via'):
        return f'{text} with its parent, {_ref(data["via"])}'
    count = int(data.get('subtasks') or 0)
    if count:
        text += (
            f', with {_plural(count, "open subtask")} closed as '
            f'{_resolution(data.get("subtasks_resolution"))}'
        )
    return text


def _due(data: dict[str, Any]) -> str:
    old = _when(data.get('from_date'), data.get('from_time'))
    new = _when(data.get('to_date'), data.get('to_time'))
    if data.get('to_date') is None:
        return f'Due date cleared (it was {old})'
    if data.get('from_date') is None:
        return f'Due date set to {new}'
    return f'Due date changed from {old} to {new}'


def _labels(data: dict[str, Any]) -> str:
    parts = []
    if data.get('added'):
        parts.append('added ' + ', '.join(f'@{name}' for name in data['added']))
    if data.get('removed'):
        parts.append('removed ' + ', '.join(f'@{name}' for name in data['removed']))
    return 'Labels: ' + '; '.join(parts)


def _repeat(data: dict[str, Any]) -> str:
    old, new = data.get('from') or '', data.get('to') or ''
    if not new:
        if data.get('reason') == 'ended':
            return f'Repeat ended: {_rule(old)}'
        return f'Repeat stopped (it was: {_rule(old)})'
    mode = ' (from completion)' if data.get('mode') == 'from_completion' else ''
    if not old:
        return f'Repeat set: {_rule(new)}{mode}'
    if old == new:
        return f'Repeat mode changed: {_rule(new)}{mode or " (fixed schedule)"}'
    return f'Repeat changed from {_rule(old)} to {_rule(new)}{mode}'


def _priority(value: object) -> str:
    return f'priority {value}' if value else 'no priority'


def describe(event: Any) -> str:
    """The sentence for a `TaskEvent` (or anything with `kind` and `data`)."""
    kind, data = event.kind, event.data or {}
    match kind:
        case EventKind.CREATED:
            return _created(data)
        case EventKind.STATE:
            return f'State changed from {_state(data.get("from"))} to {_state(data.get("to"))}'
        case EventKind.CLOSED:
            return _closed(data)
        case EventKind.REOPENED:
            text = 'Reopened'
            if data.get('to') == TaskState.BLOCKED:
                text += ' (still blocked: a blocker is open)'
            return text
        case EventKind.RESOLUTION:
            return (
                f'Resolution changed from {_resolution(data.get("from"))} '
                f'to {_resolution(data.get("to"))}'
            )
        case EventKind.TITLE:
            return f'Title changed from "{data.get("from")}" to "{data.get("to")}"'
        case EventKind.DESCRIPTION:
            return 'Description edited'
        case EventKind.CHECKLIST:
            verb = 'ticked' if data.get('checked') else 'unticked'
            text = f'Checklist item {verb}'
            return f'{text}: {data["text"]}' if data.get('text') else text
        case EventKind.PRIORITY:
            return (
                f'Priority changed from {_priority(data.get("from"))} '
                f'to {_priority(data.get("to"))}'
            )
        case EventKind.DUE:
            return _due(data)
        case EventKind.LABELS:
            return _labels(data)
        case EventKind.REPEAT:
            return _repeat(data)
        case EventKind.BLOCKER_ADDED:
            return f'Blocker added: {_ref(data)}'
        case EventKind.BLOCKER_REMOVED:
            return f'Blocker removed: {_ref(data)}'
        case EventKind.BLOCKED:
            blockers = data.get('blockers') or []
            return f'Blocked by {_refs(blockers)}' if blockers else 'Blocked'
        case EventKind.UNBLOCKED:
            blockers = data.get('blockers') or []
            reasons = '; '.join(
                f'{_ref(item)} {_REASONS.get(str(item.get("reason")), "ended")}'
                for item in blockers
            )
            return f'Unblocked: {reasons}' if reasons else 'Unblocked'
        case EventKind.SUBTASK_ADDED:
            verb = 'moved in' if data.get('moved') else 'added'
            return f'Subtask {verb}: {_ref(data)}'
        case EventKind.SUBTASK_REMOVED:
            return f'Subtask moved out: {_ref(data)}'
        case EventKind.SUBTASK_CLOSED:
            return f'Subtask {_ref(data)} closed as {_resolution(data.get("resolution"))}'
        case EventKind.MOVED:
            if data.get('parent'):
                return f'Moved under {_ref(data["parent"])}'
            return 'Made a top-level task'
        case EventKind.ATTACHMENT_ADDED | EventKind.ATTACHMENT_REMOVED:
            added = kind == EventKind.ATTACHMENT_ADDED
            where = ' to a comment' if data.get('comment') and added else ''
            where = ' from a comment' if data.get('comment') and not added else where
            return f'File {"attached" if added else "removed"}{where}: {data.get("filename")}'
        case EventKind.TRASHED | EventKind.RESTORED:
            text = 'Moved to the Trash' if kind == EventKind.TRASHED else 'Restored'
            if data.get('via'):
                text += f' with its parent, {_ref(data["via"])}'
            return text
    return str(getattr(event, 'kind', ''))
