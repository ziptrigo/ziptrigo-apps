"""Planning a To-do export (`services/export_plan.py`): the folders, collisions and packing."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from ..services.export_plan import (
    NO_PROJECT,
    ExportRow,
    part_file_name,
    plan_export,
    task_folder_name,
)

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def row(
    name: str = 'plan.pdf',
    *,
    number: int = 1000,
    title: str = 'Pay the rent',
    project: str = 'Home',
    comment: bool = False,
    size: int = 10,
    age: int = 0,
) -> ExportRow:
    return ExportRow(
        attachment_id=uuid.uuid4(),
        task_id=uuid.UUID(int=number),
        comment_id=uuid.uuid4() if comment else None,
        task_number=number,
        task_title=title,
        project=project,
        file_key=f'k/{uuid.uuid4()}',
        size_bytes=size,
        file_name=name,
        created_at=NOW + timedelta(seconds=age),
    )


def paths(rows, part_bytes=10**9) -> list[str]:
    return [item.path for item in plan_export(rows, part_bytes).items]


class TestLayout:
    def test_project_then_task_then_file(self):
        assert paths([row()]) == ['Home/1000 - Pay the rent/plan.pdf']

    def test_a_task_outside_every_project(self):
        assert paths([row(project='')]) == [f'{NO_PROJECT}/1000 - Pay the rent/plan.pdf']
        assert NO_PROJECT == 'No project'

    def test_a_comments_files_go_under_the_comments_folder(self):
        result = paths([row('a.pdf'), row('b.pdf', comment=True)])
        assert result == [
            'Home/1000 - Pay the rent/a.pdf',
            'Home/1000 - Pay the rent/comments/b.pdf',
        ]

    def test_a_task_with_no_file_has_no_folder(self):
        assert paths([row(number=1000)]) == ['Home/1000 - Pay the rent/plan.pdf']
        assert len(paths([])) == 0

    def test_two_tasks_with_one_title_have_their_own_folders(self):
        result = paths([row(number=1000), row(number=1001)])
        assert result == [
            'Home/1000 - Pay the rent/plan.pdf',
            'Home/1001 - Pay the rent/plan.pdf',
        ]

    def test_the_number_comes_first_so_a_long_title_is_what_is_cut(self):
        folder = task_folder_name(1234, 'T' * 300)
        assert folder.startswith('1234 - T') and len(folder) == 80

    def test_a_title_that_is_only_dots_leaves_the_number(self):
        assert task_folder_name(1000, '...') == '1000 - ...'.rstrip('. ')
        assert task_folder_name(1000, '').startswith('1000')

    def test_separators_in_a_title_or_project_cannot_make_folders(self):
        [path] = paths([row(title='a/b\\c', project='x/y')])
        assert path == 'x_y/1000 - a_b_c/plan.pdf'

    def test_a_project_named_like_a_reserved_name_is_avoided(self):
        assert paths([row(project='CON')])[0].startswith('_CON/')

    def test_projects_differing_only_by_case_share_one_folder_spelling(self):
        result = paths([row(project='Home', number=1000), row(project='HOME', number=1001)])
        assert {path.split('/')[0] for path in result} == {'Home'}


class TestCollisions:
    def test_two_files_with_one_name_on_a_task_are_numbered(self):
        result = paths([row(age=1), row(age=2), row(age=3)])
        assert result == [
            'Home/1000 - Pay the rent/plan (1).pdf',
            'Home/1000 - Pay the rent/plan (2).pdf',
            'Home/1000 - Pay the rent/plan.pdf',
        ]

    def test_the_earlier_file_keeps_the_plain_name(self):
        older, newer = row(age=1), row(age=2)
        plan = plan_export([newer, older], 10**9)
        by_id = {item.row.attachment_id: item.path for item in plan.items}
        assert by_id[older.attachment_id].endswith('/plan.pdf')
        assert by_id[newer.attachment_id].endswith('/plan (1).pdf')

    def test_the_tasks_own_file_wins_the_plain_name_over_a_comments(self):
        task_file, comment_file = row(age=5), row(age=1, comment=True)
        # Different folders, so no collision at all: the comment's is under comments/.
        result = paths([comment_file, task_file])
        assert 'Home/1000 - Pay the rent/plan.pdf' in result
        assert 'Home/1000 - Pay the rent/comments/plan.pdf' in result

    def test_names_are_compared_ignoring_case(self):
        result = paths([row('Plan.PDF', age=1), row('plan.pdf', age=2)])
        assert len({p.casefold() for p in result}) == 2

    def test_a_file_called_comments_does_not_take_the_comments_folder(self):
        result = paths([row('comments'), row('Comments', age=1), row('x.pdf', comment=True)])
        assert 'Home/1000 - Pay the rent/comments/x.pdf' in result
        folder_names = {p.rsplit('/', 1)[1] for p in result if p.count('/') == 2}
        assert 'comments' not in {n.casefold() for n in folder_names}
        assert len({p.casefold() for p in result}) == 3

    def test_every_path_is_unique_across_the_parts(self):
        rows = [row(age=n, size=60) for n in range(8)]
        plan = plan_export(rows, 100)
        assert plan.part_count > 1
        assert len({item.path.casefold() for item in plan.items}) == 8

    def test_the_plan_is_the_same_whatever_order_the_rows_come_in(self):
        rows = [row(f'{n}.pdf', number=1000 + n % 3, age=n) for n in range(8)]
        assert paths(rows) == paths(list(reversed(rows)))

    def test_a_name_that_cleans_to_nothing_becomes_file(self):
        assert paths([row('...')]) == ['Home/1000 - Pay the rent/file']


class TestParts:
    def test_everything_fits_one_part(self):
        plan = plan_export([row(f'{n}.pdf', size=10, age=n) for n in range(5)], 100)
        assert plan.part_count == 1 and {i.part_number for i in plan.items} == {1}

    def test_a_part_is_closed_when_the_next_file_would_take_it_over(self):
        plan = plan_export([row(f'{n}.pdf', size=40, age=n) for n in range(5)], 100)
        assert [i.part_number for i in plan.items] == [1, 1, 2, 2, 3]
        assert plan.part_count == 3

    def test_a_file_bigger_than_a_part_goes_alone(self):
        rows = [row('a.pdf', size=10), row('b.pdf', size=500), row('c.pdf', size=10)]
        plan = plan_export(rows, 100)
        assert [i.part_number for i in plan.items] == [1, 2, 3]

    def test_parts_follow_path_order(self):
        rows = [row(f'{n:02d}.pdf', size=30, age=n) for n in range(7)]
        plan = plan_export(rows, 100)
        assert [i.path for i in plan.items] == sorted(i.path for i in plan.items)
        assert [i.part_number for i in plan.items] == sorted(i.part_number for i in plan.items)

    def test_total_bytes_and_the_empty_plan(self):
        plan = plan_export([row(size=7), row('b.pdf', size=5)], 100)
        assert plan.total_bytes == 12
        empty = plan_export([], 100)
        assert empty.items == [] and empty.part_count == 0 and empty.total_bytes == 0


def test_part_file_names():
    assert part_file_name(date(2026, 11, 16), 1, 5) == 'task-files-2026-11-16-part-01-of-05.zip'
    assert part_file_name(date(2026, 11, 16), 1, 1) == 'task-files-2026-11-16.zip'
