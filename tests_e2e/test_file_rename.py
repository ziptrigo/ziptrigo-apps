"""Renaming files in a real browser: a To-do task's attachment (the row's Rename, Escape, Enter and
the focus after saving), a comment's freshly attached file (the chip's name box), a file on the file
transfer send page (the pencil, which must not submit the send form) and a receipt's file. The name
is edited without its extension, which is kept.

The server is `receipts_server`: the real site with in-memory buckets the browser can PUT to.
"""

import re
import uuid

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

# A valid 1x1 PNG: the server sniffs the bytes.
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
    '0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082'
)


def _open_new_task(page: Page, server: ReceiptsServer, title: str) -> None:
    server.login(page)
    page.goto(f'{server.url}/todo/all/')
    page.fill('#quick-title', title)
    page.click('#quick-add-form button[type="submit"]')
    page.locator('#task-list').get_by_text(title).first.click()
    expect(page.locator('h1')).to_contain_text(title)


def test_a_task_attachment_is_renamed_in_place(page: Page, receipts_server: ReceiptsServer):
    _open_new_task(page, receipts_server, 'Rename attachment task')
    page.set_input_files(
        '#attachment-file-input',
        {'name': '2026-11-21-10-45-30.png', 'mimeType': 'image/png', 'buffer': PNG},
    )
    listing = page.locator('#attachment-list')
    expect(listing).to_contain_text('2026-11-21-10-45-30.png')

    # Rename shows a box with the name only; Escape puts the row back unchanged.
    listing.get_by_role('button', name='Rename 2026-11-21-10-45-30.png').click()
    box = listing.locator('input[name="filename"]')
    expect(box).to_be_focused()
    expect(box).to_have_value('2026-11-21-10-45-30')
    expect(listing.locator('[data-file-extension]')).to_have_text('.png')
    box.press('Escape')
    expect(box).to_be_hidden()
    expect(listing).to_contain_text('2026-11-21-10-45-30.png')

    # An empty name is refused under the list; a good one saves and the focus returns to Rename.
    listing.get_by_role('button', name='Rename 2026-11-21-10-45-30.png').click()
    box.fill('   ')
    box.press('Enter')
    expect(page.locator('#attachment-msg')).to_contain_text('Give the file a name.')
    box.fill('Whiteboard')
    box.press('Enter')
    expect(listing).to_contain_text('Whiteboard.png')
    expect(page.locator('#attachment-msg')).to_be_empty()
    expect(listing.get_by_role('button', name='Rename Whiteboard.png')).to_be_focused()


def test_a_comment_file_is_renamed_before_posting(page: Page, receipts_server: ReceiptsServer):
    _open_new_task(page, receipts_server, 'Rename comment file task')
    form = page.locator('#task-comments form[action*="/comments/"]').last
    files = form.locator('[data-comment-files]')
    expect(files).to_have_attribute('data-files-ready', '1')
    files.locator('[data-files-input]').set_input_files(
        {'name': 'clip.png', 'mimeType': 'image/png', 'buffer': PNG}
    )

    name = files.locator('[data-files-attached] li input[name^="file_name_"]')
    expect(name).to_have_value('clip')
    name.fill('Sketch')
    form.locator('textarea[name="body"]').fill('See the sketch')
    form.locator('button[type="submit"]').click()

    comment = page.locator('#task-comments ul > li').first
    expect(comment).to_contain_text('See the sketch')
    expect(comment.get_by_role('list', name='Files attached to this comment')).to_contain_text(
        'Sketch.png'
    )


def test_a_file_on_the_send_page_is_renamed_without_sending(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/transfer/send/')
    page.set_input_files(
        '#file-input', {'name': 'scan.png', 'mimeType': 'image/png', 'buffer': PNG}
    )
    row = page.locator('#file-list li').first
    expect(row.locator('.fa-check')).to_be_visible()

    row.get_by_role('button', name='Rename scan.png').click()
    box = row.get_by_label('New name for scan.png')
    expect(box).to_be_focused()
    expect(box).to_have_value('scan')
    box.fill('Signed contract')
    box.press('Enter')  # must save the name, not submit the send form

    expect(row).to_contain_text('Signed contract.png')
    expect(page).to_have_url(re.compile(r'/transfer/send/'))  # still on the send page
    expect(row.get_by_role('button', name='Rename Signed contract.png')).to_be_focused()

    # A reload keeps the new name.
    page.reload()
    expect(page.locator('#file-list li').first).to_contain_text('Signed contract.png')


def test_a_receipts_file_is_renamed(page: Page, receipts_server: ReceiptsServer):
    receipt_id = uuid.uuid4()
    key = f'receipts/users/e2e/files/{receipt_id}'
    receipts_server.shell(
        'import os\n'
        'from apps.accounts.models import User\n'
        'from apps.receipts.models import Receipt\n'
        "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
        f"Receipt.objects.create(id='{receipt_id}', user=user, status='processed', vendor='Shop',"
        f" source='upload', content_type='image/png', file_key='{key}', size_bytes={len(PNG)},"
        " original_filename='IMG_4410.png')\n"
    )
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/{receipt_id}/')

    file_line = page.locator('#receipt-file-name')
    expect(file_line).to_contain_text('IMG_4410.png')
    file_line.get_by_role('button', name='Rename IMG_4410.png').click()
    box = file_line.locator('input[name="filename"]')
    expect(box).to_have_value('IMG_4410')
    box.fill('')
    box.type(' ')
    file_line.get_by_role('button', name='Save the new name').click()
    expect(page.locator('#receipt-file-msg')).to_contain_text('Give the file a name.')

    box.fill('Lunch with Sam')
    file_line.get_by_role('button', name='Save the new name').click()
    expect(page.locator('body')).to_contain_text('Renamed to Lunch with Sam.png.')
    expect(page.locator('#receipt-file-name')).to_contain_text('File: Lunch with Sam.png')
