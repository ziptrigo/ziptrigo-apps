"""File previews on the public download page (issue #311) and sending without recipients
(issue #310), end to end in a real browser: the owner sends a transfer with no recipient, a second
(anonymous) browser context opens its link, sees the hint, opens a preview only by clicking, moves
among the previewable files with the keyboard, and sees Markdown formatted and raw, an HTML file
as text and an unpreviewable file without a button.

Runs on the shared e2e server (`receipts_server`), where file transfer's S3 is an in-memory fake that
serves what was uploaded with the content type the preview asks for.
"""

import base64
import re

from playwright.sync_api import Browser, Page, expect

from tests_e2e.conftest import ReceiptsServer

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
)
HTML = b"<h1>Hi</h1><script>document.title='PWNED'</script>"
MARKDOWN = b"# Title\n\nSome **bold** text <script>document.title='PWNED'</script>\n"

CLEANUP = (
    'import os\n'
    'from apps.file_transfer.models import Transfer\n'
    "Transfer.objects.filter(owner__email=os.environ['E2E_EMAIL'], "
    "files__name__startswith='e2e-preview-').delete()\n"
)


def _file(name: str, content: bytes) -> dict:
    return {'name': name, 'mimeType': 'application/octet-stream', 'buffer': content}


def test_preview_dialog_hint_navigation_markdown_and_html(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/transfer/send/')
        page.set_input_files(
            '#file-input',
            [
                _file('e2e-preview-pic.png', PNG),
                _file('e2e-preview-notes.md', MARKDOWN),
                _file('e2e-preview-page.html', HTML),
                _file('e2e-preview-blob.bin', bytes(range(256)) * 4),
            ],
        )
        expect(page.locator('#file-list li .fa-check')).to_have_count(4)
        # Recipients are optional (issue #310): send with none.
        expect(page.locator('#id_recipients')).to_have_value('')
        page.click('#send-submit-btn')
        page.wait_for_url(re.compile(r'/transfer/sent/'))
        expect(page.get_by_role('heading', name='Your files are ready')).to_be_visible()
        link = page.locator('#download-url-input').input_value()

        visitor = visitor_context.new_page()
        visitor.goto(link)

        # The page says previews exist; nothing is open.
        expect(visitor.locator('#preview-hint')).to_have_text(
            re.compile(r'3 of 4 files can be previewed before you download them\.')
        )
        expect(visitor.locator('#preview-modal')).to_be_hidden()
        expect(visitor.get_by_role('button', name='Preview e2e-preview-blob.bin')).to_have_count(0)
        expect(visitor.get_by_text('no preview')).to_have_count(1)

        # An image: the dialog opens on a click, focus on Close, the image really loads.
        trigger = visitor.get_by_role('button', name='Preview e2e-preview-notes.md')
        trigger.click()
        dialog = visitor.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(visitor.get_by_role('button', name='Close', exact=True)).to_be_focused()
        expect(dialog).not_to_contain_text("doesn't count as a download")

        # Markdown: formatted by default, raw on the switch, the script never runs.
        expect(visitor.locator('#preview-markdown h1')).to_have_text('Title')
        expect(visitor.locator('#preview-markdown strong')).to_have_text('bold')
        expect(visitor.locator('#preview-markdown')).to_contain_text('<script>')
        expect(visitor.locator('#preview-view-formatted')).to_have_attribute('aria-pressed', 'true')
        visitor.click('#preview-view-raw')
        expect(visitor.locator('#preview-text')).to_contain_text('# Title')
        expect(visitor.locator('#preview-view-raw')).to_have_attribute('aria-pressed', 'true')
        visitor.click('#preview-view-formatted')
        expect(visitor.locator('#preview-markdown h1')).to_be_visible()

        # Keyboard: arrows move among the previewable files only (3 of them) and wrap.
        expect(visitor.locator('#preview-position')).to_have_text('1 of 3')
        visitor.keyboard.press('ArrowRight')
        expect(visitor.locator('#preview-title')).to_have_text('e2e-preview-page.html')
        # An HTML file is only ever text: its source is shown, no switch, nothing ran.
        expect(visitor.locator('#preview-text')).to_contain_text('<h1>Hi</h1>')
        expect(visitor.locator('#preview-view-raw')).to_be_hidden()
        visitor.keyboard.press('ArrowRight')
        expect(visitor.locator('#preview-title')).to_have_text('e2e-preview-pic.png')
        loaded = visitor.locator('#preview-image')
        expect(loaded).to_be_visible()
        visitor.wait_for_function(
            "() => { const i = document.getElementById('preview-image'); "
            'return i && i.complete && i.naturalWidth > 0; }'
        )
        visitor.keyboard.press('ArrowRight')
        expect(visitor.locator('#preview-position')).to_have_text('1 of 3')
        assert 'PWNED' not in visitor.title()

        # Escape closes it and focus goes back to the button that opened it.
        visitor.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        expect(trigger).to_be_focused()

        # The name opens it too; the backdrop closes it.
        visitor.get_by_role('button', name='e2e-preview-pic.png', exact=True).click()
        expect(visitor.locator('#preview-title')).to_have_text('e2e-preview-pic.png')
        visitor.mouse.click(5, 5)
        expect(dialog).to_be_hidden()
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)
