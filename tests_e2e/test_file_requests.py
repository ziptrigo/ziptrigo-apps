"""File requests (issue #306), end to end in a real browser: the owner creates a request, a second,
anonymous browser context opens the link, uploads a file, removes and re-adds one and marks the
uploads done, and the owner gets the Done notice and downloads what was uploaded.

Runs on the shared e2e server (`receipts_server`), where file transfer's S3 is an in-memory fake
and tasks run inline. Every request this makes has the title prefix `E2E request`; each test
removes what it made first.
"""

import re

from playwright.sync_api import Browser, Page, expect

from tests_e2e.conftest import ReceiptsServer

CLEANUP = (
    'import os\n'
    'from apps.file_transfer.models import Transfer\n'
    "Transfer.objects.filter(owner__email=os.environ['E2E_EMAIL'], title__startswith='E2E request')"
    '.delete()\n'
)


def _create_request(page: Page, server: ReceiptsServer, title: str, **fields) -> str:
    """Create a request through the form and return its public link."""
    page.goto(f'{server.url}/transfer/request/new/')
    page.fill('#id_title', title)
    for name, value in fields.items():
        page.fill(f'#id_{name}', value)
    page.get_by_role('button', name='Create the request').click()
    page.wait_for_url(re.compile(r'/transfer/requests/[0-9a-f-]{36}/created/$'))
    link = page.locator('#download-url-input').input_value()
    assert re.fullmatch(r'http://localhost:\d+/t/[A-Za-z0-9]{22}/', link), link
    return link


def test_create_upload_remove_done_notice_and_download(
    page: Page, browser: Browser, receipts_server: ReceiptsServer, tmp_path
):
    receipts_server.shell(CLEANUP)
    title = 'E2E request photos'
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        link = _create_request(page, receipts_server, title, message='Send yours')

        # The created page: Copy link and a QR code popup (Escape closes it).
        expect(page.get_by_role('button', name='Copy link')).to_be_visible()
        page.get_by_role('button', name='QR code').click()
        dialog = page.get_by_role('dialog', name='QR code')
        expect(dialog.locator('img')).to_have_attribute(
            'src', re.compile(r'^data:image/png;base64,')
        )
        expect(dialog).to_contain_text(link)
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()

        # A visitor with no account: the upload page, with a name field and the limits.
        visitor = visitor_context.new_page()
        visitor.goto(link)
        expect(visitor.get_by_role('heading', name=title)).to_be_visible()
        expect(visitor.get_by_text('is asking for files')).to_be_visible()
        expect(visitor.get_by_text('Send yours')).to_be_visible()
        visitor.fill('#uploader-name', 'Jane')

        first = tmp_path / 'first.txt'
        first.write_text('hello from Jane')
        visitor.set_input_files('#file-input', str(first))
        row = visitor.locator('#file-list li', has_text='first.txt')
        expect(row.locator('.fa-check')).to_be_visible()

        # Removing asks first, and Cancel keeps the file.
        row.get_by_role('button', name='Remove first.txt').click()
        modal = visitor.get_by_role('dialog', name='Remove this file?')
        expect(modal).to_be_visible()
        modal.get_by_role('button', name='Cancel').click()
        expect(modal).to_be_hidden()
        expect(row).to_be_visible()
        row.get_by_role('button', name='Remove first.txt').click()
        visitor.get_by_role('dialog').get_by_role('button', name='Remove').click()
        expect(visitor.locator('#file-list li')).to_have_count(0)

        # Marking done with no files at all is refused with a message.
        visitor.click('#request-done-btn')
        visitor.get_by_role('dialog').get_by_role('button', name='Mark as done').click()
        expect(visitor.locator('#request-msg')).to_contain_text('Upload at least one file first.')

        # Upload again, then mark done through the confirmation dialog.
        second = tmp_path / 'second.txt'
        second.write_text('second file')
        visitor.set_input_files('#file-input', str(second))
        expect(
            visitor.locator('#file-list li', has_text='second.txt').locator('.fa-check')
        ).to_be_visible()
        visitor.click('#request-done-btn')
        dialog = visitor.get_by_role('dialog')
        expect(dialog).to_contain_text('No one will be able to add files after this.')
        dialog.get_by_role('button', name='Mark as done').click()

        # The page switches to the download view and says the owner was told.
        expect(visitor.get_by_text('has been told.')).to_be_visible()
        # The file name is also in the (hidden) preview dialog's title (#311): match the row's button.
        expect(visitor.get_by_role('button', name='second.txt', exact=True)).to_be_visible()
        expect(visitor.get_by_text('Mark as done')).to_have_count(0)
        # Nothing more can be added.
        assert visitor.request.post(
            f'{link}upload/files/', data={'name': 'x', 'size': 1}
        ).status in (
            403,
            415,
            422,
        )

        # The owner: the Done notice, and the request on the Requests tab with the file.
        page.goto(f'{receipts_server.url}/notifications/')
        expect(page.get_by_text(f'"{title}" is done')).to_be_visible()
        expect(page.get_by_text('Marked done by Jane.')).to_be_visible()

        page.goto(f'{receipts_server.url}/transfer/?kind=request')
        card = page.locator('[id^="transfer-row-"]', has_text=title)
        expect(card).to_contain_text('Done')
        expect(card).to_contain_text('1 file')
        card.get_by_role('button', name='Manage').click()
        expect(card.get_by_text('second.txt')).to_be_visible()
        expect(card.get_by_text('Jane').first).to_be_visible()

        # Downloading through the dashboard link serves the uploaded bytes.
        href = card.get_by_role('link', name='Download').first.get_attribute('href')
        response = page.request.get(f'{receipts_server.url}{href}')
        assert response.ok and response.text() == 'second file'

        # Reopen puts the link back in upload mode for the visitor.
        card.get_by_role('button', name='Reopen').click()
        expect(card.get_by_role('button', name='Mark as done')).to_be_visible()
        visitor.reload()
        expect(visitor.get_by_text('Mark as done').first).to_be_visible()
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)


def test_only_these_people_needs_an_account_with_the_address(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/transfer/request/new/')
        page.fill('#id_title', 'E2E request private')
        page.get_by_label('Only these people').check()
        page.fill('#id_emails', 'nobody@example.com')
        page.get_by_role('button', name='Create the request').click()
        page.wait_for_url(re.compile(r'/created/$'))
        expect(page.get_by_text('We emailed 1 person the link.')).to_be_visible()
        link = page.locator('#download-url-input').input_value()

        visitor = visitor_context.new_page()
        visitor.goto(link)
        # Not signed in: sent to the sign-in page, which returns here afterwards.
        expect(visitor).to_have_url(re.compile(r'/account/login/\?next=/t/'))
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)
