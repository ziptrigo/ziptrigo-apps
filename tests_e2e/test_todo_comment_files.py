"""To-do: files on comments, end to end in a real browser (the upload script, the chip, saving the
comment with its file, downloading it, and removing it in an edit).

The server is `receipts_server`: the real site, with To-do's bucket swapped for an in-memory one the
browser can PUT to and GET from (`site_fakes.TodoE2EStorage`).
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

# A valid 1x1 PNG: the server sniffs the bytes, so a real image is offered "Open".
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


def test_a_comment_carries_a_file_and_an_edit_removes_it(
    page: Page, receipts_server: ReceiptsServer
):
    _open_new_task(page, receipts_server, 'Comment files task')
    form = page.locator('#task-comments form[action*="/comments/"]').last
    box = form.locator('[data-comment-files]')
    expect(box).to_have_attribute('data-files-ready', '1')

    box.locator('[data-files-input]').set_input_files(
        {'name': 'receipt photo.png', 'mimeType': 'image/png', 'buffer': PNG}
    )

    # The file uploads at once and becomes a chip; the form carries its id.
    chip = box.locator('[data-files-attached] li')
    expect(chip).to_contain_text('receipt photo.png')
    expect(chip.locator('input[name="attachment_ids"]')).to_have_count(1)
    expect(form.locator('button[type="submit"]')).to_be_enabled()

    form.locator('textarea[name="body"]').fill('Here is the photo')
    form.locator('button[type="submit"]').click()

    comment = page.locator('#task-comments ul > li').first
    expect(comment).to_contain_text('Here is the photo')
    expect(comment.get_by_role('list', name='Files attached to this comment')).to_contain_text(
        'receipt photo.png'
    )
    # The task's own Attachments block is not the place for it.
    expect(page.locator('#attachment-list')).to_contain_text('No files attached.')

    # Downloading goes through a presigned URL to the bucket and returns the bytes.
    href = comment.get_by_role('link', name='Download').get_attribute('href')
    assert href is not None
    response = page.request.get(f'{receipts_server.url}{href}')
    assert response.status == 200
    assert response.body() == PNG
    expect(comment.get_by_role('link', name='Open')).to_be_visible()

    # Edit: tick Remove and save; the file goes.
    comment.get_by_role('button', name='Comment actions').click()
    comment.get_by_role('button', name='Edit').click()
    edit_form = comment.locator('form')
    edit_form.get_by_label('Remove').check()
    edit_form.locator('button[type="submit"]').click()

    expect(page.locator('#task-comments')).not_to_contain_text('receipt photo.png')
    expect(page.locator('#task-comments ul > li').first).to_contain_text('Here is the photo')


def test_a_removed_chip_is_not_sent_and_a_bad_file_shows_its_error(
    page: Page, receipts_server: ReceiptsServer
):
    _open_new_task(page, receipts_server, 'Comment files discard task')
    form = page.locator('#task-comments form[action*="/comments/"]').last
    box = form.locator('[data-comment-files]')
    expect(box).to_have_attribute('data-files-ready', '1')

    box.locator('[data-files-input]').set_input_files(
        {'name': 'empty.txt', 'mimeType': 'text/plain', 'buffer': b''}
    )
    expect(box.locator('[data-files-uploads]')).to_contain_text('This file is empty.')

    box.locator('[data-files-input]').set_input_files(
        {'name': 'drop-me.png', 'mimeType': 'image/png', 'buffer': PNG}
    )
    chip = box.locator('[data-files-attached] li')
    expect(chip).to_contain_text('drop-me.png')
    chip.get_by_role('button', name=re.compile('^Remove')).click()
    expect(box.locator('[data-files-attached] li')).to_have_count(0)

    form.locator('textarea[name="body"]').fill('No file on this one')
    form.locator('button[type="submit"]').click()
    comment = page.locator('#task-comments ul > li').first
    expect(comment).to_contain_text('No file on this one')
    expect(comment).not_to_contain_text('drop-me.png')


def test_the_tasks_own_attachments_still_upload_through_the_shared_script(
    page: Page, receipts_server: ReceiptsServer
):
    _open_new_task(page, receipts_server, 'Task files task')

    page.locator('#attachment-file-input').set_input_files(
        {'name': 'task-file.png', 'mimeType': 'image/png', 'buffer': PNG}
    )

    expect(page.locator('#attachment-list')).to_contain_text('task-file.png')
    expect(page.locator('#attachment-uploads li')).to_have_count(0)
