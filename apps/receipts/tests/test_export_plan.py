"""Planning an export (`services/export_plan.py`): names, collisions, packing into zips, the CSV."""

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ..services.export_plan import (
    FILE_NAME_MAX,
    FOLDER_NAME_MAX,
    CsvEntry,
    ExportRow,
    clean_file_name,
    clean_folder_name,
    iter_export_csv,
    part_file_name,
    plan_export,
)

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def row(
    name: str = 'invoice.pdf',
    *,
    vendor: str = 'Acme',
    when: date | None = date(2026, 3, 4),
    size: int = 10,
    age: int = 0,
) -> ExportRow:
    return ExportRow(
        receipt_id=uuid.uuid4(),
        file_key=f'k/{uuid.uuid4()}',
        size_bytes=size,
        file_name=name,
        receipt_date=when,
        vendor=vendor,
        document_type='invoice',
        amount=Decimal('12.5'),
        currency='USD',
        created_at=NOW + timedelta(seconds=age),
    )


def paths(rows, layout='year_vendor', part_bytes=10**9) -> list[str]:
    return [item.path for item in plan_export(rows, layout, part_bytes).items]


class TestLayouts:
    def test_year_then_vendor(self):
        assert paths([row()]) == ['2026/Acme/invoice.pdf']

    def test_vendor_then_year(self):
        assert paths([row()], 'vendor_year') == ['Acme/2026/invoice.pdf']

    def test_a_receipt_without_a_date_or_a_vendor(self):
        items = paths([row(when=None, vendor='')])
        assert items == ['No date/No vendor/invoice.pdf']
        assert paths([row(when=None, vendor='')], 'vendor_year') == [
            'No vendor/No date/invoice.pdf'
        ]

    def test_the_year_is_the_receipts_own_date(self):
        assert paths([row(when=date(2024, 12, 31))]) == ['2024/Acme/invoice.pdf']

    def test_an_unknown_layout_is_refused(self):
        with pytest.raises(ValueError):
            plan_export([row()], 'by_color', 100)

    def test_no_rows_is_an_empty_plan(self):
        plan = plan_export([], 'year_vendor', 100)
        assert plan.items == [] and plan.part_count == 0


class TestNames:
    def test_path_separators_in_a_vendor_cannot_make_folders(self):
        assert clean_folder_name('AC/DC \\ Inc', 'x') == 'AC_DC _ Inc'

    def test_characters_windows_refuses_are_replaced(self):
        assert clean_folder_name('a<b>c:d"e|f?g*h', 'x') == 'a_b_c_d_e_f_g_h'

    def test_control_and_format_characters_are_dropped_or_replaced(self):
        # a zero-width space and a right-to-left override vanish; a newline becomes a space
        assert clean_folder_name('Ac​me‮\nCo', 'x') == 'Acme Co'

    def test_leading_dots_and_trailing_dots_and_spaces_go(self):
        assert clean_folder_name('..hidden. . ', 'x') == 'hidden'
        assert clean_folder_name('...', 'No vendor') == 'No vendor'

    def test_dot_dot_cannot_climb_out(self):
        [path] = paths([row(vendor='..', name='../../etc/passwd')])
        assert path.startswith('2026/No vendor/')
        assert path.count('/') == 2 and '..' not in path.split('/')

    @pytest.mark.parametrize('name', ['CON', 'nul', 'COM1', 'lpt9', 'Aux'])
    def test_windows_reserved_names_are_avoided(self, name):
        assert clean_folder_name(name, 'x') == f'_{name}'

    def test_a_reserved_name_with_an_extension_is_avoided_too(self):
        assert clean_file_name('con.txt') == '_con.txt'
        assert clean_file_name('console.txt') == 'console.txt'

    def test_a_long_vendor_is_cut(self):
        name = clean_folder_name('V' * 300, 'x')
        assert len(name) == FOLDER_NAME_MAX

    def test_a_long_file_name_is_cut_but_keeps_its_extension(self):
        name = clean_file_name('f' * 300 + '.pdf')
        assert name.endswith('.pdf') and len(name) <= FILE_NAME_MAX

    def test_a_name_with_nothing_left_gets_a_fallback(self):
        assert clean_file_name('///') == '___'
        assert clean_file_name('...') == 'file'

    def test_an_email_body_name_is_kept(self):
        assert clean_file_name('email.html') == 'email.html'

    def test_a_non_ascii_name_is_kept(self):
        assert clean_folder_name('Café Zürich 日本', 'x') == 'Café Zürich 日本'


class TestCollisions:
    def test_two_files_with_one_name_in_a_folder_are_numbered(self):
        result = paths([row(age=1), row(age=2), row(age=3)])
        assert result == [
            '2026/Acme/invoice (1).pdf',
            '2026/Acme/invoice (2).pdf',
            '2026/Acme/invoice.pdf',
        ]

    def test_the_older_receipt_keeps_the_plain_name(self):
        older, newer = row(age=1), row(age=2)
        plan = plan_export([newer, older], 'year_vendor', 10**9)
        by_id = {item.row.receipt_id: item.path for item in plan.items}
        assert by_id[older.receipt_id] == '2026/Acme/invoice.pdf'
        assert by_id[newer.receipt_id] == '2026/Acme/invoice (1).pdf'

    def test_names_are_compared_ignoring_case(self):
        result = paths([row('Invoice.PDF', age=1), row('invoice.pdf', age=2)])
        assert len({p.casefold() for p in result}) == 2

    def test_a_file_without_an_extension_is_numbered_after_its_name(self):
        assert paths([row('receipt', age=1), row('receipt', age=2)]) == [
            '2026/Acme/receipt',
            '2026/Acme/receipt (1)',
        ]

    def test_vendors_that_end_up_the_same_folder_share_it(self):
        result = paths([row('a.pdf', vendor='AC/DC'), row('b.pdf', vendor='AC\\DC')])
        assert {p.rsplit('/', 1)[0] for p in result} == {'2026/AC_DC'}

    def test_vendors_differing_only_by_case_share_one_folder_spelling(self):
        result = paths([row('a.pdf', vendor='Acme', age=1), row('b.pdf', vendor='ACME', age=2)])
        assert {p.rsplit('/', 1)[0] for p in result} == {'2026/Acme'}

    def test_two_files_that_clean_to_one_name_are_both_kept(self):
        result = paths([row('a:b.pdf', age=1), row('a?b.pdf', age=2)])
        assert len(result) == 2 and len({p.casefold() for p in result}) == 2

    def test_the_plan_is_the_same_whatever_order_the_rows_come_in(self):
        rows = [row(f'{n}.pdf', vendor=v, age=n) for n, v in enumerate('abcab')]
        assert paths(rows) == paths(list(reversed(rows)))

    def test_every_path_is_unique_across_the_parts(self):
        rows = [row(age=n, size=60) for n in range(8)]
        plan = plan_export(rows, 'year_vendor', 100)
        assert plan.part_count > 1
        assert len({item.path.casefold() for item in plan.items}) == 8


class TestParts:
    def test_everything_fits_one_part(self):
        plan = plan_export([row(f'{n}.pdf', size=10) for n in range(5)], 'year_vendor', 100)
        assert plan.part_count == 1 and {i.part_number for i in plan.items} == {1}

    def test_a_part_is_closed_when_the_next_file_would_take_it_over(self):
        rows = [row(f'{n}.pdf', size=40) for n in range(5)]
        plan = plan_export(rows, 'year_vendor', 100)
        assert [i.part_number for i in plan.items] == [1, 1, 2, 2, 3]
        assert plan.part_count == 3

    def test_a_file_exactly_filling_a_part_stays_in_it(self):
        plan = plan_export([row('a.pdf', size=50), row('b.pdf', size=50)], 'year_vendor', 100)
        assert plan.part_count == 1

    def test_a_file_bigger_than_a_part_goes_alone(self):
        rows = [row('a.pdf', size=10), row('b.pdf', size=500), row('c.pdf', size=10)]
        plan = plan_export(rows, 'year_vendor', 100)
        assert [i.part_number for i in plan.items] == [1, 2, 3]

    def test_a_file_is_never_split_and_parts_follow_path_order(self):
        rows = [row(f'{n:02d}.pdf', size=30) for n in range(7)]
        plan = plan_export(rows, 'year_vendor', 100)
        assert [i.path for i in plan.items] == sorted(i.path for i in plan.items)
        assert [i.part_number for i in plan.items] == sorted(i.part_number for i in plan.items)

    def test_total_bytes(self):
        plan = plan_export([row(size=7), row('b.pdf', size=5)], 'year_vendor', 100)
        assert plan.total_bytes == 12

    def test_zero_byte_files_do_not_open_new_parts(self):
        plan = plan_export([row(f'{n}.pdf', size=0) for n in range(5)], 'year_vendor', 100)
        assert plan.part_count == 1


def test_part_file_names():
    assert part_file_name(date(2026, 11, 16), 1, 5) == 'receipts-2026-11-16-part-01-of-05.zip'
    assert part_file_name(date(2026, 11, 16), 12, 12) == 'receipts-2026-11-16-part-12-of-12.zip'
    assert part_file_name(date(2026, 11, 16), 1, 1) == 'receipts-2026-11-16.zip'


class TestCsv:
    def parse(self, entries) -> list[list[str]]:
        text = b''.join(iter_export_csv(entries)).decode()
        return list(csv.reader(io.StringIO(text)))

    def entry(self, **kw) -> CsvEntry:
        values = {
            'receipt_date': date(2026, 3, 4),
            'vendor': 'Acme',
            'document_type': 'invoice',
            'amount': Decimal('12.5'),
            'currency': 'USD',
            'path': '2026/Acme/invoice.pdf',
            'part_number': 2,
        }
        values.update(kw)
        return CsvEntry(**values)

    def test_header_and_a_line(self):
        rows = self.parse([self.entry()])
        assert rows[0] == ['date', 'vendor', 'type', 'amount', 'currency', 'file', 'part']
        assert rows[1] == [
            '2026-03-04',
            'Acme',
            'invoice',
            '12.50',
            'USD',
            '2026/Acme/invoice.pdf',
            '2',
        ]

    def test_missing_values_are_empty(self):
        rows = self.parse([self.entry(receipt_date=None, amount=None, vendor='', document_type='')])
        assert rows[1][:5] == ['', '', '', '', 'USD']

    def test_a_vendor_that_looks_like_a_formula_is_neutralised(self):
        rows = self.parse([self.entry(vendor='=HYPERLINK("http://evil","x")')])
        assert rows[1][1].startswith("'=")

    def test_commas_and_quotes_survive(self):
        rows = self.parse([self.entry(vendor='Smith, "Bob" & Sons')])
        assert rows[1][1] == 'Smith, "Bob" & Sons'

    def test_lines_end_like_the_dashboards_export(self):
        text = b''.join(iter_export_csv([self.entry()])).decode()
        assert text.endswith('\r\n') and '\r\n' in text

    def test_no_entries_is_just_the_header(self):
        assert len(self.parse([])) == 1
