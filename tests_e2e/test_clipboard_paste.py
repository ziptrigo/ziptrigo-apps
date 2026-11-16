"""Pasting an image from the clipboard as a way to upload it (issue #313, `core/js/clipboard_paste.js`).

Playwright cannot fill the OS clipboard, so the test dispatches a `paste` event whose
`clipboardData` is a `DataTransfer`, which is what the browser hands the page for a real Ctrl+V.
The image is a real PNG drawn on a canvas in the page (the script decodes it to re-encode it as
WebP). Runs on the server of `receipts_server`, where Receipts', file transfer's and To-do's buckets
are in-memory fakes.
"""

import re

import pytest
from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

# `YYYY-MM-DD-HH-mm-SS.webp`, with `-2` for a second image in the same second.
WEBP_NAME = r'\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}(-\d+)?\.webp'

# Dispatches a paste on `selector` (the body when empty) and answers whether the page took it
# (`preventDefault`). `what`: 'image' (a real PNG), 'text', 'pdf' or 'image+text'.
PASTE_JS = """
async ([selector, what]) => {
    const dt = new DataTransfer();
    if (what === 'image' || what === 'image+text' || what === 'two-images') {
        const count = what === 'two-images' ? 2 : 1;
        for (let i = 0; i < count; i++) {
            const canvas = document.createElement('canvas');
            canvas.width = 40 + i;
            canvas.height = 30;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#336699';
            ctx.fillRect(0, 0, 40, 30);
            const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
            dt.items.add(new File([blob], 'image.png', { type: 'image/png' }));
        }
    }
    if (what === 'pdf') {
        dt.items.add(new File(['%PDF-1.4 x'], 'doc.pdf', { type: 'application/pdf' }));
    }
    if (what === 'text' || what === 'image+text') {
        dt.setData('text/plain', 'just some text');
    }
    const target = selector ? document.querySelector(selector) : document.body;
    const event = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    target.dispatchEvent(event);
    return event.defaultPrevented;
}
"""


def _paste(page: Page, what: str, selector: str = '') -> bool:
    return page.evaluate(PASTE_JS, [selector, what])


def _open_new_task(page: Page, server: ReceiptsServer, title: str) -> None:
    server.login(page)
    page.goto(f'{server.url}/todo/all/')
    page.fill('#quick-title', title)
    page.click('#quick-add-form button[type="submit"]')
    page.locator('#task-list').get_by_text(title).first.click()
    expect(page.locator('h1')).to_contain_text(title)


def test_receipts_upload_takes_a_pasted_image_and_ignores_the_rest(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/upload/')
    rows = page.locator('#upload-list li')
    expect(page.locator('#paste-hint')).to_be_visible()

    # Text, a PDF file and nothing at all are not taken: the paste is left alone, nothing is added.
    assert _paste(page, 'text') is False
    assert _paste(page, 'pdf') is False
    expect(rows).to_have_count(0)

    # An image is uploaded as `<time>.webp`, through the same path as a picked file.
    assert _paste(page, 'image') is True
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text('Uploaded')
    assert re.fullmatch(WEBP_NAME, rows.first.locator('span').first.inner_text())


def test_file_transfer_send_page_takes_a_pasted_image(page: Page, receipts_server: ReceiptsServer):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/transfer/send/')
    rows = page.locator('#file-list li')

    assert _paste(page, 'text') is False
    expect(rows).to_have_count(0)

    assert _paste(page, 'image') is True
    expect(rows).to_have_count(1)
    expect(rows.first.locator('.fa-check')).to_be_visible()
    assert re.match(WEBP_NAME, rows.first.locator('span').first.inner_text())

    # Two images in the same second are told apart.
    assert _paste(page, 'two-images') is True
    expect(rows).to_have_count(3)
    names = [rows.nth(i).locator('span').first.inner_text().split(' ')[0] for i in range(3)]
    assert len(set(names)) == 3


def test_todo_pasted_image_goes_to_the_task_or_to_the_comment_form(
    page: Page, receipts_server: ReceiptsServer
):
    _open_new_task(page, receipts_server, 'Paste task')
    expect(page.locator('#attachment-paste-hint')).to_be_visible()

    # Anywhere outside a comment form: an attachment of the task. Text is ignored.
    assert _paste(page, 'text') is False
    assert _paste(page, 'image') is True
    attachments = page.locator('#attachment-list')
    expect(attachments).to_contain_text('.webp')
    assert re.search(WEBP_NAME, attachments.inner_text())

    # In the comment form's text box: text (alone, or with an image) is the field's own paste;
    # an image alone becomes a file of the comment and adds nothing to the text.
    form = page.locator('#task-comments form[action*="/comments/"]').last
    box = form.locator('[data-comment-files]')
    expect(box).to_have_attribute('data-files-ready', '1')
    expect(box.locator('[data-paste-hint]')).to_be_visible()
    textarea = form.locator('textarea[name="body"]')
    textarea.fill('my note')
    textarea.focus()

    assert _paste(page, 'text', 'textarea[name="body"]') is False
    assert _paste(page, 'image+text', 'textarea[name="body"]') is False
    expect(box.locator('[data-files-attached] li')).to_have_count(0)

    assert _paste(page, 'image', 'textarea[name="body"]') is True
    chip = box.locator('[data-files-attached] li')
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text('.webp')
    expect(chip.locator('input[name="attachment_ids"]')).to_have_count(1)
    expect(textarea).to_have_value('my note')

    form.locator('button[type="submit"]').click()
    comment = page.locator('#task-comments ul > li').first
    expect(comment).to_contain_text('my note')
    expect(comment.get_by_role('list', name='Files attached to this comment')).to_contain_text(
        '.webp'
    )
    # The comment's file is not one of the task's own attachments: only the first paste is there.
    assert len(re.findall(WEBP_NAME, page.locator('#attachment-list').inner_text())) == 1


@pytest.mark.parametrize('what', ['text', 'pdf'])
def test_a_paste_without_an_image_adds_nothing_on_the_task_page(
    page: Page, receipts_server: ReceiptsServer, what: str
):
    _open_new_task(page, receipts_server, f'Ignored paste {what}')
    assert _paste(page, what) is False
    expect(page.locator('#attachment-uploads li')).to_have_count(0)
    expect(page.locator('#attachment-list')).to_contain_text('No files attached.')
