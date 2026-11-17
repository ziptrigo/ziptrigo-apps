"""The task spreadsheet of an export (issue #326): `tasks.xlsx`, one workbook with two sheets,
**Tasks** and **Comments**. It goes into part 1 of the zips next to the files (`exports.py`), and is
also the quick "Export XLSX" download on its own (`views.exports.export_xlsx`), where the files
column holds file names only (there are no zip paths to name).

Written with openpyxl's **write-only** mode (rows are streamed to a temp file, never all in memory)
into any binary file object; the caller copies it on.

- *Tasks*: number, title, description, state, resolution, priority, due date, due time, project,
  section, labels (comma-separated), parent (its number), created, closed, comments (how many) and
  files (one line per file: its path in the zip, plus ` (part N)` when there is more than one zip).
  Every live task created in the range (and up to `as_of`): open, closed and subtasks; never the
  Trash.
- *Comments*: task number, task title, created, edited, comment (its Markdown as typed), files.
  The comments that are not deleted and were written up to `as_of`; never a comment's earlier
  revisions.
- *Files not listed above*: only when needed, see below.

**The workbook says what is in the zips.** The files in the zips were fixed when the export was
asked for, the workbook is written later (part 1 is built last), and a task may have been trashed
or a comment deleted in between. So the tasks and comments that the files (`FileListing`) refer to
are written too, whatever has happened to them since (`deleted_at` is ignored for those ids), and
a file is never unlisted. Only a row that no longer exists at all (hard-deleted) can't be written;
its files go into the "Files not listed above" sheet, by id, so no file in the zips is left out.

**Never a formula.** Titles, descriptions and comments can arrive by email. Every text cell is
written with the string data type (an inline string, never a formula), after the characters a
spreadsheet file can't hold (control characters) are dropped, and a text the shared rule
(`export_names.looks_like_formula`: a leading `=`, `+`, `-` or `@`...) calls formula-like also gets
the `quotePrefix` cell style: the text is kept as typed, nothing visible is added, and a
spreadsheet that edits the cell keeps it text. (The CSV exports, which have no styles, prefix an
apostrophe instead.) A text over the 32,767 characters a cell holds is cut.

Dates and times are real spreadsheet dates, **in the user's time zone** (a spreadsheet has no zone):
`Created` and `Closed` are the user's local date and time, `Due date` and `Due time` are the local
values the task keeps already. The header is bold and frozen and the columns have widths.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import IO, Any
from uuid import UUID

from django.db.models import Count, Q
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet._write_only import WriteOnlyWorksheet

from apps.accounts.models import User
from apps.accounts.services.timezones import user_tz
from apps.core.services.export_names import looks_like_formula

from ..models import Comment, Priority, Resolution, Task, TaskState

#: What the workbook is called inside the zip.
WORKBOOK_NAME = 'tasks.xlsx'

#: The longest text a spreadsheet cell holds.
CELL_MAX = 32_767

XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

TASK_COLUMNS = (
    ('Number', 8),
    ('Title', 40),
    ('Description', 60),
    ('State', 13),
    ('Resolution', 12),
    ('Priority', 12),
    ('Due date', 12),
    ('Due time', 9),
    ('Project', 22),
    ('Section', 22),
    ('Labels', 26),
    ('Parent', 8),
    ('Created', 17),
    ('Closed', 17),
    ('Comments', 10),
    ('Files', 50),
)

COMMENT_COLUMNS = (
    ('Task number', 11),
    ('Task title', 40),
    ('Created', 17),
    ('Edited', 17),
    ('Comment', 80),
    ('Files', 50),
)

DATE_FORMAT = 'yyyy-mm-dd'
TIME_FORMAT = 'hh:mm'
DATETIME_FORMAT = 'yyyy-mm-dd hh:mm'


@dataclass(slots=True)
class FileListing:
    """What each task's and comment's `Files` cell says: one line per file."""

    by_task: dict[UUID, list[str]] = field(default_factory=lambda: defaultdict(list))
    by_comment: dict[UUID, list[str]] = field(default_factory=lambda: defaultdict(list))
    task_ids: set[UUID] = field(default_factory=set)
    """Every task a file is on, a comment's files included: the workbook has a row for each."""

    def add(self, task_id: UUID, comment_id: UUID | None, line: str) -> None:
        self.task_ids.add(task_id)
        if comment_id is None:
            self.by_task[task_id].append(line)
        else:
            self.by_comment[comment_id].append(line)


@dataclass(frozen=True, slots=True)
class WorkbookStats:
    tasks: int
    comments: int


def task_scope(
    user: User,
    start: datetime | None,
    end: datetime | None,
    as_of: datetime | None = None,
    prefix: str = '',
) -> dict[str, Any]:
    """The filter of "the user's live tasks created in the range", as keyword arguments for a
    query on `Task` (`prefix=''`) or, with a prefix, one that reaches it (`'task__'` on an
    attachment or a comment). `as_of` leaves out a task created after the export was asked for
    (a "last 30 days" range ends today, and the list is of what was asked for)."""
    scope: dict[str, Any] = {f'{prefix}user': user, f'{prefix}deleted_at__isnull': True}
    if start is not None:
        scope[f'{prefix}created_at__gte'] = start
    if end is not None:
        scope[f'{prefix}created_at__lt'] = end
    if as_of is not None:
        scope[f'{prefix}created_at__lte'] = as_of
    return scope


def _text(sheet: WriteOnlyWorksheet, value: object, *, wrap: bool = False) -> Cell:
    """A text cell: stripped of what a spreadsheet can't hold *first* (a leading control character
    would otherwise hide a formula from the guard, then vanish), guarded against formulas with the
    `quotePrefix` style, cut to what a cell holds, and typed as a string whatever it starts with."""
    text = ILLEGAL_CHARACTERS_RE.sub('', '' if value is None else str(value))
    cell = WriteOnlyCell(sheet, value=text[:CELL_MAX] or None)
    cell.data_type = 's'
    if text and looks_like_formula(text):
        cell.quotePrefix = True
    if wrap and text:
        cell.alignment = Alignment(wrap_text=True, vertical='top')
    return cell


def _number(sheet: WriteOnlyWorksheet, value: int | None) -> Cell:
    return WriteOnlyCell(sheet, value=value)


def _moment(
    sheet: WriteOnlyWorksheet, value: datetime | date | time | None, number_format: str
) -> Cell:
    cell = WriteOnlyCell(sheet, value=value)
    if value is not None:
        cell.number_format = number_format
    return cell


def _local(value: datetime | None, tz) -> datetime | None:
    """`value` as the user's wall clock: a spreadsheet has no time zone, so none is attached."""
    return value.astimezone(tz).replace(tzinfo=None, microsecond=0) if value else None


def _header(sheet: WriteOnlyWorksheet, columns: Sequence[tuple[str, int]]) -> list[Cell]:
    cells = []
    for name, _width in columns:
        cell = WriteOnlyCell(sheet, value=name)
        cell.font = Font(bold=True)
        cell.data_type = 's'
        cells.append(cell)
    return cells


def _prepare(sheet: Any, columns: Sequence[tuple[str, int]]) -> None:
    """Widths and the frozen header: both must be set before the first row is written."""
    for index, (_name, width) in enumerate(columns):
        sheet.column_dimensions[chr(ord('A') + index)].width = width
    sheet.freeze_panes = 'A2'


def _lines(listing: dict[UUID, list[str]], key: UUID) -> str:
    return '\n'.join(sorted(listing.get(key, ()), key=str.casefold))


UNLISTED_SHEET = 'Files not listed above'
UNLISTED_COLUMNS = (('Kind', 10), ('Id', 40), ('Files', 80))


def _write_unlisted(
    workbook: Workbook, files: FileListing, listed_tasks: set[UUID], listed_comments: set[UUID]
) -> None:
    """The last safety net: files of a task or comment that has no row at all any more (it was
    hard-deleted) are listed here by id, so no file in the zips is left unlisted. No sheet when
    there are none."""
    rows = [
        ('Task', key, lines) for key, lines in files.by_task.items() if key not in listed_tasks
    ] + [
        ('Comment', key, lines)
        for key, lines in files.by_comment.items()
        if key not in listed_comments
    ]
    if not rows:
        return
    sheet = workbook.create_sheet(UNLISTED_SHEET)
    _prepare(sheet, UNLISTED_COLUMNS)
    sheet.append(_header(sheet, UNLISTED_COLUMNS))
    for kind, key, lines in sorted(rows, key=lambda row: (row[0], str(row[1]))):
        sheet.append(
            [
                _text(sheet, kind),
                _text(sheet, str(key)),
                _text(sheet, '\n'.join(sorted(lines, key=str.casefold)), wrap=True),
            ]
        )


def write_workbook(
    target: IO[bytes],
    user: User,
    *,
    start: datetime | None,
    end: datetime | None,
    files: FileListing,
    as_of: datetime | None = None,
) -> WorkbookStats:
    """Write the workbook of the user's tasks created in `[start, end)` into `target` (a binary
    file object, which the caller rewinds and copies on)."""
    tz = user_tz(user)
    scope = task_scope(user, start, end, as_of)
    # The tasks and comments the files refer to are written whatever happened to them since.
    task_filter = Q(**scope) | Q(id__in=files.task_ids)
    comment_filter = (
        Q(deleted_at__isnull=True, task__in=Task.objects.filter(task_filter).values('id'))
        & (Q(created_at__lte=as_of) if as_of is not None else Q())
    ) | Q(id__in=files.by_comment.keys())
    workbook = Workbook(write_only=True)

    tasks_sheet = workbook.create_sheet('Tasks')
    comments_sheet = workbook.create_sheet('Comments')
    _prepare(tasks_sheet, TASK_COLUMNS)
    _prepare(comments_sheet, COMMENT_COLUMNS)

    # Per task: its labels and its number of comments, in one query each for the whole range.
    labels: dict[UUID, list[str]] = defaultdict(list)
    for task_id, name in (
        Task.labels.through.objects.filter(task__in=Task.objects.filter(task_filter).values('id'))
        .order_by('label__position', 'label__name')
        .values_list('task_id', 'label__name')
    ):
        labels[task_id].append(name)
    comment_counts = dict(
        Comment.objects.filter(comment_filter)
        .values_list('task_id')
        .annotate(total=Count('id'))
        .order_by()
    )
    listed_tasks: set[UUID] = set()
    listed_comments: set[UUID] = set()

    tasks_sheet.append(_header(tasks_sheet, TASK_COLUMNS))
    task_total = 0
    task_rows = (
        Task.objects.filter(task_filter)
        .order_by('number')
        .values_list(
            'id',
            'number',
            'title',
            'description',
            'state',
            'resolution',
            'priority',
            'due_date',
            'due_time',
            'project__name',
            'section__name',
            'parent__number',
            'created_at',
            'completed_at',
        )
    )
    for (
        task_id,
        number,
        title,
        description,
        state,
        resolution,
        priority,
        due_date,
        due_time,
        project,
        section,
        parent,
        created_at,
        completed_at,
    ) in task_rows.iterator(chunk_size=1000):
        closed = completed_at is not None
        if task_id in files.by_task:
            listed_tasks.add(task_id)
        tasks_sheet.append(
            [
                _number(tasks_sheet, number),
                _text(tasks_sheet, title),
                _text(tasks_sheet, description, wrap=True),
                _text(tasks_sheet, 'Closed' if closed else TaskState(state).label),
                _text(
                    tasks_sheet,
                    # A closed task with no resolution (an old row) reads as Done.
                    Resolution(resolution or Resolution.DONE).label if closed else '',
                ),
                _text(tasks_sheet, Priority(priority).label if priority else ''),
                _moment(tasks_sheet, due_date, DATE_FORMAT),
                _moment(tasks_sheet, due_time, TIME_FORMAT),
                _text(tasks_sheet, project or ''),
                _text(tasks_sheet, section or ''),
                _text(tasks_sheet, ', '.join(labels.get(task_id, ()))),
                _number(tasks_sheet, parent),
                _moment(tasks_sheet, _local(created_at, tz), DATETIME_FORMAT),
                _moment(tasks_sheet, _local(completed_at, tz), DATETIME_FORMAT),
                _number(tasks_sheet, comment_counts.get(task_id, 0)),
                _text(tasks_sheet, _lines(files.by_task, task_id), wrap=True),
            ]
        )
        task_total += 1

    comments_sheet.append(_header(comments_sheet, COMMENT_COLUMNS))
    comment_total = 0
    comment_rows = (
        Comment.objects.filter(comment_filter)
        .order_by('task__number', 'created_at', 'id')
        .values_list('id', 'task__number', 'task__title', 'created_at', 'edited_at', 'body')
    )
    for comment_id, number, title, created_at, edited_at, body in comment_rows.iterator(
        chunk_size=1000
    ):
        if comment_id in files.by_comment:
            listed_comments.add(comment_id)
        comments_sheet.append(
            [
                _number(comments_sheet, number),
                _text(comments_sheet, title),
                _moment(comments_sheet, _local(created_at, tz), DATETIME_FORMAT),
                _moment(comments_sheet, _local(edited_at, tz), DATETIME_FORMAT),
                _text(comments_sheet, body, wrap=True),
                _text(comments_sheet, _lines(files.by_comment, comment_id), wrap=True),
            ]
        )
        comment_total += 1

    _write_unlisted(workbook, files, listed_tasks, listed_comments)
    workbook.save(target)
    return WorkbookStats(tasks=task_total, comments=comment_total)
