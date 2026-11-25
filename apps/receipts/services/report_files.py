"""The report as a file (issue #351): CSV, XLSX and PDF of a `reports.Report`.

All three show the same table: the vendor (or category) -- a vendor report also says whether it is
vetted --, its currency, the number of receipts, the total, its share of that currency's total, the
average and the date of the last receipt, then **one total line per currency** (amounts in
different currencies are never added together). They are free and built in memory (a report has
one row per vendor and currency, never one per receipt).

- *CSV*: ISO dates, amounts with two decimals and no thousands separator, `\\r\\n` line endings
  like the dashboard's CSV, every text cell through the formula guard (`cell_text`: an apostrophe
  before `=`, `+`, `-`, `@`...).
- *XLSX* (openpyxl, write-only mode): real numbers for counts and amounts, real dates, a bold
  frozen header, and **never a formula**: every text is an inline string (control characters
  dropped first) and a formula-like text also gets the `quotePrefix` cell style, so nothing
  visible is added. A second sheet lists what the report covers.
- *PDF* (reportlab, Helvetica): a title, the sentence saying what is covered, the table and the
  totals. Text is escaped before it reaches a `Paragraph` (which reads a little markup). The
  standard fonts hold Latin text only: a name in another script prints without those characters
  rather than failing.

Dates in the files use the public wording (`Nov 5, 2026`) in the PDF and the filter sentence, ISO
values in the CSV and real dates in the XLSX, whatever the account's date format, like the other
exports.
"""

import csv
import io
from collections.abc import Sequence
from typing import IO, Any
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet._write_only import WriteOnlyWorksheet
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.datetime_format import PUBLIC, format_date
from apps.core.services.export_names import cell_text, looks_like_formula

from .reports import GROUP_VENDOR, Report, public_description, report_title

CSV_CONTENT_TYPE = 'text/csv; charset=utf-8'
XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PDF_CONTENT_TYPE = 'application/pdf'

#: The longest text a spreadsheet cell holds.
CELL_MAX = 32_767
TOTAL_LABEL = 'Total'


def file_name(report: Report, extension: str, today) -> str:
    """`receipts-by-vendor-2026-11-25.xlsx` (`today` in the user's time zone)."""
    return f'receipts-by-{report.filters.group}-{today.isoformat()}.{extension}'


def _columns(report: Report) -> list[str]:
    name = 'Vendor' if report.filters.group == GROUP_VENDOR else 'Category'
    columns = [name]
    if report.filters.group == GROUP_VENDOR:
        columns.append('Vetted')
    columns += ['Currency', 'Receipts', 'Total', 'Share (%)', 'Average', 'Last receipt']
    return columns


def _by_vendor(report: Report) -> bool:
    return report.filters.group == GROUP_VENDOR


def _vetted_text(vetted: bool | None) -> str:
    return '' if vetted is None else ('Yes' if vetted else 'No')


# -- CSV ------------------------------------------------------------------------------------------


def report_csv(report: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(_columns(report))
    for row in report.rows:
        cells: list[object] = [row.name]
        if _by_vendor(report):
            cells.append(_vetted_text(row.vetted))
        cells += [
            row.currency,
            row.count,
            f'{row.total:.2f}',
            f'{row.share:.1f}',
            f'{row.average:.2f}',
            row.last_date.isoformat() if row.last_date else '',
        ]
        writer.writerow(cell_text(value) for value in cells)
    for total in report.totals:
        cells = [TOTAL_LABEL]
        if _by_vendor(report):
            cells.append('')
        cells += [total.currency, total.count, f'{total.total:.2f}', '', f'{total.average:.2f}', '']
        writer.writerow(cell_text(value) for value in cells)
    return buffer.getvalue()


# -- XLSX -----------------------------------------------------------------------------------------


def _text(sheet: WriteOnlyWorksheet, value: object, *, bold: bool = False) -> Cell:
    """A text cell: stripped of what a spreadsheet can't hold *first* (a leading control character
    would otherwise hide a formula from the guard, then vanish), typed as a string whatever it
    starts with, and `quotePrefix`ed when it looks like a formula."""
    text = ILLEGAL_CHARACTERS_RE.sub('', '' if value is None else str(value))
    cell = WriteOnlyCell(sheet, value=text[:CELL_MAX] or None)
    cell.data_type = 's'
    if text and looks_like_formula(text):
        cell.quotePrefix = True
    if bold:
        cell.font = Font(bold=True)
    return cell


def _number(sheet: WriteOnlyWorksheet, value: Any, number_format: str, *, bold: bool = False):
    cell = WriteOnlyCell(sheet, value=value)
    if value is not None:
        cell.number_format = number_format
    if bold:
        cell.font = Font(bold=True)
    return cell


def _blank(sheet: WriteOnlyWorksheet) -> Cell:
    return WriteOnlyCell(sheet, value=None)


def report_xlsx(report: Report, target: IO[bytes]) -> None:
    """Write the workbook into `target` (a binary file object, which the caller rewinds)."""
    by_vendor = _by_vendor(report)
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet('By vendor' if by_vendor else 'By category')
    columns = _columns(report)
    widths = [34] + ([9] if by_vendor else []) + [10, 10, 16, 10, 14, 14]
    for index, width in enumerate(widths):
        sheet.column_dimensions[chr(ord('A') + index)].width = width
    sheet.freeze_panes = 'A2'

    header = []
    for name in columns:
        cell = WriteOnlyCell(sheet, value=name)
        cell.font = Font(bold=True)
        cell.data_type = 's'
        if name in ('Receipts', 'Total', 'Share (%)', 'Average'):
            cell.alignment = Alignment(horizontal='right')
        header.append(cell)
    sheet.append(header)

    amount = '#,##0.00'
    for row in report.rows:
        cells = [_text(sheet, row.name)]
        if by_vendor:
            cells.append(_text(sheet, _vetted_text(row.vetted)))
        cells += [
            _text(sheet, row.currency),
            _number(sheet, row.count, '0'),
            _number(sheet, row.total, amount),
            _number(sheet, row.share / 100, '0.0%'),
            _number(sheet, row.average, amount),
            _number(sheet, row.last_date, 'yyyy-mm-dd'),
        ]
        sheet.append(cells)
    if report.totals:
        sheet.append([])
    for total in report.totals:
        cells = [_text(sheet, TOTAL_LABEL, bold=True)]
        if by_vendor:
            cells.append(_blank(sheet))
        cells += [
            _text(sheet, total.currency, bold=True),
            _number(sheet, total.count, '0', bold=True),
            _number(sheet, total.total, amount, bold=True),
            _blank(sheet),
            _number(sheet, total.average, amount, bold=True),
            _blank(sheet),
        ]
        sheet.append(cells)

    about = workbook.create_sheet('Filters')
    about.column_dimensions['A'].width = 110
    about.append([_text(about, report_title(report.filters.group), bold=True)])
    about.append([_text(about, public_description(report.filters))])
    about.append(
        [
            _text(
                about,
                'Amounts in different currencies are not converted or added together: '
                'there is one total per currency.',
            )
        ]
    )
    workbook.save(target)


# -- PDF ------------------------------------------------------------------------------------------


def _para(text: object, style: ParagraphStyle) -> Paragraph:
    """A paragraph of user text: escaped first, since a `Paragraph` reads `<b>`, `&amp;`, etc."""
    return Paragraph(escape('' if text is None else str(text)), style)


def report_pdf(report: Report, target: IO[bytes]) -> None:
    """Write the PDF into `target`."""
    by_vendor = _by_vendor(report)
    styles = getSampleStyleSheet()
    body = ParagraphStyle('cell', parent=styles['BodyText'], fontSize=8.5, leading=10.5)
    bold = ParagraphStyle('cellbold', parent=body, fontName='Helvetica-Bold')
    right = ParagraphStyle('cellright', parent=body, alignment=2)
    right_bold = ParagraphStyle('cellrightbold', parent=bold, alignment=2)
    head = ParagraphStyle('head', parent=bold, textColor=colors.white)
    head_right = ParagraphStyle('headright', parent=head, alignment=2)

    document = SimpleDocTemplate(
        target,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=report_title(report.filters.group),
        author='ZipTrigo',
    )

    story: list[Any] = [
        _para(report_title(report.filters.group), styles['Title']),
        _para(public_description(report.filters), styles['BodyText']),
        Spacer(1, 3 * mm),
    ]

    columns = _columns(report)
    right_columns = {'Receipts', 'Total', 'Share (%)', 'Average'}
    data: list[list[Any]] = [
        [_para(name, head_right if name in right_columns else head) for name in columns]
    ]
    for row in report.rows:
        cells: list[Any] = [_para(row.name, body)]
        if by_vendor:
            cells.append(_para(_vetted_text(row.vetted), body))
        cells += [
            _para(row.currency, body),
            _para(row.count, right),
            _para(f'{row.total:,.2f}', right),
            _para(f'{row.share:.1f}', right),
            _para(f'{row.average:,.2f}', right),
            _para(format_date(row.last_date, 'full', PUBLIC) if row.last_date else '', body),
        ]
        data.append(cells)
    first_total = len(data)
    for total in report.totals:
        cells = [_para(TOTAL_LABEL, bold)]
        if by_vendor:
            cells.append(_para('', bold))
        cells += [
            _para(total.currency, bold),
            _para(total.count, right_bold),
            _para(f'{total.total:,.2f}', right_bold),
            _para('', bold),
            _para(f'{total.average:,.2f}', right_bold),
            _para('', bold),
        ]
        data.append(cells)

    page_width = A4[0] - 30 * mm
    widths = _widths(by_vendor, page_width)

    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#475a53')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, 1), (-1, -1), 0.25, colors.HexColor('#c6cecb')),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        (
            'ROWBACKGROUNDS',
            (0, 1),
            (-1, first_total - 1),
            [colors.white, colors.HexColor('#f6f8f7')],
        ),
    ]
    if report.totals:
        style += [
            ('BACKGROUND', (0, first_total), (-1, -1), colors.HexColor('#dfe4e2')),
            ('LINEABOVE', (0, first_total), (-1, first_total), 0.8, colors.HexColor('#475a53')),
        ]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(style))
    story.append(table)
    story.append(Spacer(1, 3 * mm))
    story.append(
        _para(
            'Amounts in different currencies are not converted or added together: '
            'there is one total per currency.',
            ParagraphStyle('note', parent=body, textColor=colors.HexColor('#66716d')),
        )
    )

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#66716d'))
        canvas.drawString(15 * mm, 9 * mm, 'ZipTrigo Receipts')
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f'Page {doc.page}')
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)


def _widths(by_vendor: bool, page_width: float) -> Sequence[float]:
    """Column widths: the name takes what the fixed columns leave."""
    others = [value * mm for value in (17, 19, 24, 16, 22, 25)]
    vetted = [15 * mm] if by_vendor else []
    # currency, receipts, total, share, average, last receipt
    return [page_width - sum(others) - sum(vetted), *vetted, *others]
