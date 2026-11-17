"""To-do "Download files" and "Export XLSX" (issue #326), end to end in a real browser: attach a file
to a task and one to a comment, open the To-do block of the account page's "App actions", choose
a "Task created" range, open the dialog, confirm the cost, land on the export's page (the zip files
are built by the inline task backend during the request), download a zip and look inside it
(the files in `<project>/<number> - <title>/` and `tasks.xlsx`), download the spreadsheet on its own,
then delete the export.

Runs on `receipts_server` (fake S3 for Receipts and To-do, see `receipts_e2e_settings.py`); To-do's
fake bucket serves the zip like S3 would (`site_fakes.TodoE2EStorage`).
"""

import io
import re
import uuid
import zipfile

from openpyxl import load_workbook
from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

# A valid 1x1 PNG: the server sniffs the bytes.
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
    '0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082'
)


def _task_with_files(page: Page, server: ReceiptsServer, title: str) -> str:
    """A task with a file of its own and a comment with one; returns the task's number."""
    page.goto(f'{server.url}/todo/all/')
    page.fill('#quick-title', title)
    page.click('#quick-add-form button[type="submit"]')
    page.locator('#task-list').get_by_text(title).first.click()
    expect(page.locator('h1')).to_contain_text(title)
    number = re.search(r'/todo/(\d+)/', page.url).group(1)  # ty: ignore[unresolved-attribute]

    page.locator('#attachment-file-input').set_input_files(
        {'name': 'task-file.png', 'mimeType': 'image/png', 'buffer': PNG}
    )
    expect(page.locator('#attachment-list')).to_contain_text('task-file.png')

    form = page.locator('#task-comments form[action*="/comments/"]').last
    box = form.locator('[data-comment-files]')
    expect(box).to_have_attribute('data-files-ready', '1')
    box.locator('[data-files-input]').set_input_files(
        {'name': 'comment-file.png', 'mimeType': 'image/png', 'buffer': PNG}
    )
    expect(box.locator('[data-files-attached] li')).to_contain_text('comment-file.png')
    form.locator('textarea[name="body"]').fill('=HYPERLINK("http://evil.example","click")')
    form.locator('button[type="submit"]').click()
    expect(page.locator('#task-comments ul > li').first).to_contain_text('comment-file.png')
    return number


def test_download_files_and_export_xlsx(page: Page, receipts_server: ReceiptsServer, tmp_path):
    url = receipts_server.url
    # The export covers every task the shared test account made so far, 3 credits a file: top up.
    receipts_server.shell(
        'import os\n'
        'from apps.accounts.models import User\n'
        'from apps.billing.services import add_credits\n'
        "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
        "add_credits(user, 500, description='e2e export', source='e2e')\n"
    )
    receipts_server.login(page)
    token = uuid.uuid4().hex[:8]
    title = f'Export me {token}'
    number = _task_with_files(page, receipts_server, title)

    page.goto(f'{url}/account/')
    block = page.locator('[data-app-actions=todo]')
    expect(block).to_contain_text('Download your task files as zip files')
    expect(block.locator('legend')).to_have_text('Task created')
    expect(block.locator('input[name=date_from]')).to_be_hidden()
    block.locator('select[name=date_range]').select_option('custom')
    expect(block.locator('#todo-export-from')).to_be_visible()
    block.locator('select[name=date_range]').select_option('last_15_days')
    expect(block.locator('#todo-export-from')).to_be_hidden()

    page.click('#todo-export-files')
    dialog = page.locator('#todo-export-modal [role=dialog]')
    expect(dialog).to_contain_text(re.compile(r'\d+ files?'))
    expect(dialog).to_contain_text('1 zip file')
    expect(dialog).to_contain_text('(3 per file)')
    expect(dialog).to_contain_text('tasks.xlsx')

    # Confirm: charged, and the export's page lists the zip (built inline).
    dialog.locator('button[type=submit]').click()
    page.wait_for_url(re.compile(r'/todo/exports/[0-9a-f-]{36}/$'))
    expect(page.locator('body')).to_contain_text('preparing your zip files')
    expect(page.locator('#todo-export-panel')).to_contain_text('Ready')
    expect(page.locator('#todo-export-panel')).to_contain_text('with tasks.xlsx')

    with page.expect_download() as download_info:
        page.locator('#todo-export-panel a:has-text("Download")').first.click()
    download = download_info.value
    assert download.suggested_filename.startswith('task-files-')
    assert download.suggested_filename.endswith('.zip')
    saved = tmp_path / 'saved.zip'
    download.save_as(saved)

    folder = f'No project/{number} - {title}'
    with zipfile.ZipFile(saved) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert f'{folder}/task-file.png' in names
        assert f'{folder}/comments/comment-file.png' in names
        assert archive.read(f'{folder}/task-file.png') == PNG
        workbook = load_workbook(io.BytesIO(archive.read('tasks.xlsx')))
    tasks = workbook['Tasks']
    mine = [row for row in tasks.iter_rows(min_row=2) if row[1].value == title]
    assert len(mine) == 1
    assert f'{folder}/task-file.png' in mine[0][15].value
    comment = [
        row for row in workbook['Comments'].iter_rows(min_row=2) if row[0].value == int(number)
    ]
    assert len(comment) == 1 and f'{folder}/comments/comment-file.png' in comment[0][5].value
    # The comment looked like a formula: it is text as typed (no visible apostrophe), with the
    # quote-prefix cell style.
    assert comment[0][4].data_type == 's' and comment[0][4].value.startswith('=')
    assert comment[0][4].quotePrefix is True

    # The quick XLSX: the same workbook on its own, with file names.
    page.goto(f'{url}/account/')
    block = page.locator('[data-app-actions=todo]')
    with page.expect_download() as xlsx_info:
        block.locator('#todo-export-xlsx').click()
    xlsx = xlsx_info.value
    assert xlsx.suggested_filename.startswith('tasks-') and xlsx.suggested_filename.endswith(
        '.xlsx'
    )
    xlsx_path = tmp_path / 'tasks.xlsx'
    xlsx.save_as(xlsx_path)
    quick = load_workbook(xlsx_path)['Tasks']
    mine = [row for row in quick.iter_rows(min_row=2) if row[1].value == title]
    assert len(mine) == 1 and mine[0][15].value == 'task-file.png'

    # It is listed, and deleting it (a confirmation first) removes it.
    block.locator('a:has-text("Your exports")').click()
    expect(page.locator('main')).to_contain_text('zip file')
    page.locator('main a:has-text("Open")').first.click()
    page.click('button:has-text("Delete")')
    page.locator('[role=dialog] button:has-text("Delete")').click()
    expect(page.locator('body')).to_contain_text('The export was deleted.')
