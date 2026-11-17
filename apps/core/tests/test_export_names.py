"""The app-independent half of planning an export (`services/export_names.py`, shared by Receipts
and To-do, issues #315 and #326): path-safe names, case-insensitive uniqueness, packing, the zip
names and the spreadsheet formula guard."""

from datetime import date

import pytest

from ..services.export_names import (
    FILE_NAME_MAX,
    FOLDER_NAME_MAX,
    Folders,
    cell_text,
    clean_file_name,
    clean_folder_name,
    pack_parts,
    part_file_name,
    split_extension,
    unique_path,
)

pytestmark = [pytest.mark.unit]


class TestFolderNames:
    def test_path_separators_cannot_make_folders(self):
        assert clean_folder_name('AC/DC \\ Inc', 'x') == 'AC_DC _ Inc'

    def test_characters_windows_refuses_are_replaced(self):
        assert clean_folder_name('a<b>c:d"e|f?g*h', 'x') == 'a_b_c_d_e_f_g_h'

    def test_control_and_format_characters_are_dropped_or_replaced(self):
        # a zero-width space and a right-to-left override vanish; a newline becomes a space
        assert clean_folder_name('Ac​me‮\nCo', 'x') == 'Acme Co'

    def test_leading_dots_and_trailing_dots_and_spaces_go(self):
        assert clean_folder_name('..hidden. . ', 'x') == 'hidden'
        assert clean_folder_name('...', 'No project') == 'No project'
        assert clean_folder_name('', 'No project') == 'No project'

    @pytest.mark.parametrize('name', ['CON', 'nul', 'COM1', 'lpt9', 'Aux'])
    def test_windows_reserved_names_are_avoided(self, name):
        assert clean_folder_name(name, 'x') == f'_{name}'

    def test_a_long_name_is_cut(self):
        assert len(clean_folder_name('V' * 300, 'x')) == FOLDER_NAME_MAX

    def test_a_non_ascii_name_is_kept(self):
        assert clean_folder_name('Café Zürich 日本', 'x') == 'Café Zürich 日本'


class TestFileNames:
    def test_a_reserved_name_with_an_extension_is_avoided_too(self):
        assert clean_file_name('con.txt') == '_con.txt'
        assert clean_file_name('console.txt') == 'console.txt'

    def test_a_long_name_is_cut_but_keeps_its_extension(self):
        name = clean_file_name('f' * 300 + '.pdf')
        assert name.endswith('.pdf') and len(name) <= FILE_NAME_MAX

    def test_a_name_with_nothing_left_gets_a_fallback(self):
        assert clean_file_name('...') == 'file'
        assert clean_file_name('...', 'attachment') == 'attachment'

    def test_dot_dot_cannot_climb_out(self):
        assert '..' not in clean_file_name('../../etc/passwd').split('/')
        assert '/' not in clean_file_name('../../etc/passwd')

    def test_split_extension(self):
        assert split_extension('report.pdf') == ('report', '.pdf')
        assert split_extension('archive.tar.gz') == ('archive.tar', '.gz')
        assert split_extension('README') == ('README', '')
        assert split_extension('.hidden') == ('.hidden', '')
        assert split_extension('notes.a long tail') == ('notes.a long tail', '')


class TestUniqueness:
    def test_the_first_spelling_of_a_folder_wins(self):
        folders = Folders()
        assert folders.resolve('', 'Acme') == 'Acme'
        assert folders.resolve('', 'ACME') == 'Acme'
        assert folders.resolve('Acme', 'Sub') == 'Sub'
        assert folders.resolve('Acme', 'SUB') == 'Sub'

    def test_the_same_name_under_another_parent_is_another_folder(self):
        folders = Folders()
        assert folders.resolve('a', 'X') == 'X'
        assert folders.resolve('b', 'x') == 'x'

    def test_a_taken_path_is_numbered_and_the_case_does_not_matter(self):
        used: set[str] = set()
        assert unique_path('a/b', 'Invoice.PDF', used) == 'a/b/Invoice.PDF'
        assert unique_path('a/b', 'invoice.pdf', used) == 'a/b/invoice (1).pdf'
        assert unique_path('a/b', 'INVOICE.pdf', used) == 'a/b/INVOICE (2).pdf'
        assert unique_path('a/c', 'invoice.pdf', used) == 'a/c/invoice.pdf'

    def test_a_file_without_an_extension_is_numbered_after_its_name(self):
        used: set[str] = set()
        assert unique_path('d', 'receipt', used) == 'd/receipt'
        assert unique_path('d', 'receipt', used) == 'd/receipt (1)'


class TestPacking:
    def test_everything_fits_one_part(self):
        assert pack_parts([10] * 5, 100) == [1, 1, 1, 1, 1]

    def test_a_part_is_closed_when_the_next_file_would_take_it_over(self):
        assert pack_parts([40] * 5, 100) == [1, 1, 2, 2, 3]

    def test_a_file_exactly_filling_a_part_stays_in_it(self):
        assert pack_parts([50, 50], 100) == [1, 1]

    def test_a_file_bigger_than_a_part_goes_alone(self):
        assert pack_parts([10, 500, 10], 100) == [1, 2, 3]

    def test_zero_byte_files_do_not_open_new_parts(self):
        assert pack_parts([0] * 5, 100) == [1] * 5

    def test_nothing_is_nothing(self):
        assert pack_parts([], 100) == []


def test_part_file_names():
    assert (
        part_file_name('receipts', date(2026, 11, 16), 1, 5)
        == 'receipts-2026-11-16-part-01-of-05.zip'
    )
    assert (
        part_file_name('task-files', date(2026, 11, 16), 12, 12)
        == 'task-files-2026-11-16-part-12-of-12.zip'
    )
    assert part_file_name('receipts', date(2026, 11, 16), 1, 1) == 'receipts-2026-11-16.zip'


class TestCellText:
    @pytest.mark.parametrize(
        'text',
        ['=1+1', '+1', '-1', '@SUM(A1)', ' =1', '\t=1', '\r=1', '=HYPERLINK("http://evil","x")'],
    )
    def test_what_a_spreadsheet_would_read_as_a_formula_gets_an_apostrophe(self, text):
        assert cell_text(text) == "'" + text

    @pytest.mark.parametrize('text', ['Pay rent', '1 + 1', 'a=b', '', '#1'])
    def test_other_text_is_untouched(self, text):
        assert cell_text(text) == text

    def test_none_is_empty_and_numbers_are_text(self):
        assert cell_text(None) == ''
        assert cell_text(12) == '12'
