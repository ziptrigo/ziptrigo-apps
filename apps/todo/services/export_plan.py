"""Planning an export (issue #326): which folder and file name each attached file gets inside the
zips and which zip it goes into. Pure functions: no database, no storage. The naming rules and the
packing are shared with Receipts (`apps.core.services.export_names`).

The layout is `<Project>/<number> - <title>/<file>`, with `No project` for a task outside every
project; a file attached to a comment goes under `<number> - <title>/comments/`. A task with no
files gets no folder (it is still a row of the workbook). Folders and files are unique ignoring
case across every zip (`name (1).pdf`; the first spelling of a folder wins), so extracting all the
zips into one folder merges cleanly; the order of rows is deterministic (task number, the task's own
files before its comments', then when the file was attached, then id), so the earlier file keeps the
plain name. Items are then sorted by path and packed in order: a zip is closed when the next file
would take it over the part size (a file is never cut in two; a file bigger than the part size
goes alone).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from apps.core.services.export_names import (
    Folders,
    clean_file_name,
    clean_folder_name,
    pack_parts,
    unique_path,
)
from apps.core.services.export_names import part_file_name as _part_file_name

NO_PROJECT = 'No project'
COMMENTS_FOLDER = 'comments'


@dataclass(frozen=True, slots=True)
class ExportRow:
    """One attached file as the planner sees it (what it needs is copied into the export)."""

    attachment_id: UUID
    task_id: UUID
    comment_id: UUID | None
    task_number: int
    task_title: str
    project: str
    file_key: str
    size_bytes: int
    file_name: str
    created_at: datetime


@dataclass(slots=True)
class PlannedItem:
    row: ExportRow
    path: str
    part_number: int = 0


@dataclass(slots=True)
class Plan:
    items: list[PlannedItem] = field(default_factory=list)
    part_count: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(item.row.size_bytes for item in self.items)


def _row_order(row: ExportRow) -> tuple:
    return (row.task_number, row.comment_id is not None, row.created_at, str(row.attachment_id))


def task_folder_name(number: int, title: str) -> str:
    """`1042 - Pay the rent`: the number first, so a long title is the part that is cut."""
    return clean_folder_name(f'{number} - {title}', str(number))


def plan_export(rows: list[ExportRow], part_bytes: int) -> Plan:
    """Give every row its path and its part."""
    folders = Folders()
    used: set[str] = set()
    items: list[PlannedItem] = []
    for row in sorted(rows, key=_row_order):
        project = folders.resolve('', clean_folder_name(row.project, NO_PROJECT))
        task = folders.resolve(project, task_folder_name(row.task_number, row.task_title))
        directory = f'{project}/{task}'
        # The folder of the comments' files is a name in the task's folder: a file of the task
        # called `comments` must not take it.
        used.add(f'{directory}/{COMMENTS_FOLDER}'.casefold())
        if row.comment_id is not None:
            directory = f'{directory}/{COMMENTS_FOLDER}'
        path = unique_path(directory, clean_file_name(row.file_name), used)
        items.append(PlannedItem(row=row, path=path))

    items.sort(key=lambda item: (item.path.casefold(), item.path))
    numbers = pack_parts((item.row.size_bytes for item in items), part_bytes)
    for item, number in zip(items, numbers, strict=True):
        item.part_number = number
    return Plan(items=items, part_count=numbers[-1] if numbers else 0)


def part_file_name(created: date, number: int, count: int) -> str:
    """What a part is downloaded as: `task-files-2026-11-16-part-01-of-05.zip` (no suffix for an
    export that fits one zip)."""
    return _part_file_name(DOWNLOAD_PREFIX, created, number, count)


#: What a zip is called, and what the spreadsheet is (`tasks-2026-11-16.xlsx`).
DOWNLOAD_PREFIX = 'task-files'
