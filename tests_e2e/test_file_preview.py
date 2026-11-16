"""File previews on the public download page (issue #311) and sending without recipients
(issue #310), end to end in a real browser: the owner sends a transfer with no recipient, a second
(anonymous) browser context opens its link, sees the hint, opens a preview only by clicking, moves
among the previewable files with the keyboard, sees Markdown formatted and raw, an HTML file as
text and an unpreviewable file without a button.

A preview is a *sample*: an image is shown downsized, a PDF as downsized pages with page
navigation (built lazily, so "Preparing preview..." shows first), a text file as its first 64 KB.
"View full image / document" counts as a download, and the Download that follows it does not count
again (one download in the database).

Runs on the shared e2e server (`receipts_server`), where file transfer's S3 is an in-memory fake that
serves what was uploaded with the content type the preview asks for, tasks run inline (so the
preview build happens during the first request, in its sandboxed child process) and the bucket
answers CORS (so the dialog's Blob path works).
"""

import base64
import io
import re

from PIL import Image
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


def _big_png() -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (3200, 2000), (30, 120, 90)).save(out, format='PNG')
    return out.getvalue()


def _pdf(pages: int) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(612, 792)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


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


def _events(server: ReceiptsServer, name: str) -> int:
    """How many downloads of the file called `name` the log holds."""
    out = server.shell(
        'from apps.file_transfer.models import DownloadEvent\n'
        f"print('COUNT', DownloadEvent.objects.filter(file__name='{name}').count())\n"
    )
    match = re.search(r'COUNT (\d+)', out)
    assert match, out
    return int(match.group(1))


def test_a_preview_is_a_sample_and_view_full_is_one_download(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    visitor_context = browser.new_context(accept_downloads=True)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/transfer/send/')
        page.set_input_files(
            '#file-input',
            [
                _file('e2e-preview-big.png', _big_png()),
                _file('e2e-preview-doc.pdf', _pdf(3)),
                _file('e2e-preview-log.txt', b'line\n' * 20_000),
            ],
        )
        expect(page.locator('#file-list li .fa-check')).to_have_count(3)
        page.fill('#id_max_downloads', '10')
        page.click('#send-submit-btn')
        page.wait_for_url(re.compile(r'/transfer/sent/'))
        link = page.locator('#download-url-input').input_value()

        visitor = visitor_context.new_page()
        requested: list[str] = []
        visitor.on('request', lambda request: requested.append(request.url))
        visitor.goto(link)
        expect(visitor.locator('#preview-hint')).to_have_text(
            re.compile(r'All 3 files can be previewed')
        )

        # The PDF: the first answer says "preparing" (the real build runs inline on the server, so
        # the answer is faked once to see the state), then its downsized pages arrive.
        answers = {'count': 0}

        def preparing_first(route):
            answers['count'] += 1
            if answers['count'] == 1:
                route.fulfill(
                    json={'status': 'preparing', 'pages': 0, 'total_pages': 0, 'page_urls': []}
                )
            else:
                route.continue_()

        visitor.route('**/preview/*/status/**', preparing_first)
        visitor.get_by_role('button', name='Preview e2e-preview-doc.pdf').click()
        expect(visitor.locator('#preview-preparing')).to_be_visible()
        expect(visitor.locator('#preview-page')).to_be_visible(timeout=15_000)
        expect(visitor.locator('#preview-page-label')).to_have_text('Page 1 of 3')
        expect(visitor.locator('#preview-sample-note')).to_have_text(
            'Smaller versions of the pages.'
        )
        visitor.wait_for_function(
            "() => { const i = document.getElementById('preview-page'); "
            'return i && i.complete && i.naturalWidth > 0 && i.naturalWidth <= 1600 && '
            'i.naturalHeight <= 1600; }'
        )
        visitor.unroute('**/preview/*/status/**')

        # Pages: the page buttons and Page Up / Page Down (the arrow keys switch files).
        visitor.click('#preview-page-next')
        expect(visitor.locator('#preview-page-label')).to_have_text('Page 2 of 3')
        visitor.keyboard.press('PageDown')
        expect(visitor.locator('#preview-page-label')).to_have_text('Page 3 of 3')
        expect(visitor.locator('#preview-page-next')).to_be_disabled()
        visitor.keyboard.press('PageUp')
        expect(visitor.locator('#preview-page-label')).to_have_text('Page 2 of 3')
        assert 'View full document' in visitor.locator('#preview-view-full').inner_text()

        # Previewing nothing so far counted as a download.
        assert _events(receipts_server, 'e2e-preview-doc.pdf') == 0

        # Left goes to the previous file: the image, shown downsized (3200 px wide became 1600).
        visitor.keyboard.press('ArrowLeft')
        expect(visitor.locator('#preview-title')).to_have_text('e2e-preview-big.png')
        visitor.wait_for_function(
            "() => { const i = document.getElementById('preview-image'); "
            'return i && i.complete && i.naturalWidth === 1600 && i.naturalHeight === 1000; }'
        )
        expect(visitor.locator('#preview-sample-note')).to_have_text(
            'A smaller version of the image.'
        )
        expect(visitor.locator('#preview-counted')).to_have_count(0)
        assert _events(receipts_server, 'e2e-preview-big.png') == 0

        # View full image: the whole file, and it counts as one download.
        visitor.click('#preview-view-full')
        expect(visitor.locator('#preview-full-image')).to_be_visible()
        visitor.wait_for_function(
            "() => { const i = document.getElementById('preview-full-image'); "
            'return i && i.complete && i.naturalWidth === 3200; }'
        )
        expect(visitor.locator('#preview-counted')).to_have_text('This counted as a download.')
        expect(visitor.locator('#preview-view-full')).to_be_hidden()
        assert _events(receipts_server, 'e2e-preview-big.png') == 1

        # Open in new tab: the same Blob the dialog shows, so no request and no second download.
        before = len(requested)
        open_tab = visitor.locator('#preview-open-tab')
        expect(open_tab).to_be_visible()
        assert (open_tab.get_attribute('href') or '').startswith('blob:')
        assert open_tab.get_attribute('target') == '_blank'
        with visitor.context.expect_page() as tab_info:
            open_tab.click()
        tab = tab_info.value
        tab.wait_for_load_state()
        assert tab.url.startswith('blob:')
        tab.close()
        assert not [url for url in requested[before:] if '/files/' in url]
        assert _events(receipts_server, 'e2e-preview-big.png') == 1

        # Download after it: the browser saves what it already has, no request, still one download.
        before = len(requested)
        with visitor.expect_download() as download_info:
            visitor.click('#preview-download')
        assert download_info.value.suggested_filename == 'e2e-preview-big.png'
        assert not [url for url in requested[before:] if '/files/' in url]
        assert _events(receipts_server, 'e2e-preview-big.png') == 1

        # Text is a sample of the first 64 KB; the arrow keys scroll it, they don't switch files.
        visitor.keyboard.press('ArrowRight')
        expect(visitor.locator('#preview-title')).to_have_text('e2e-preview-doc.pdf')
        visitor.keyboard.press('ArrowRight')
        expect(visitor.locator('#preview-title')).to_have_text('e2e-preview-log.txt')
        expect(visitor.locator('#preview-text')).to_contain_text('line')
        expect(visitor.locator('#preview-text-note')).to_have_text('Showing the first 64 KB.')
        assert len(visitor.locator('#preview-text').inner_text()) <= 64 * 1024
        expect(visitor.locator('#preview-text')).to_be_visible()
        visitor.locator('#preview-text').focus()
        expect(visitor.locator('#preview-text')).to_be_focused()
        visitor.keyboard.press('ArrowRight')
        expect(visitor.locator('#preview-title')).to_have_text('e2e-preview-log.txt')

        # View full document on the text: the whole file (100 KB), one more download.
        visitor.click('#preview-view-full')
        expect(visitor.locator('#preview-counted')).to_be_visible()
        visitor.wait_for_function(
            "() => document.getElementById('preview-text').textContent.length >= 100000"
        )
        assert _events(receipts_server, 'e2e-preview-log.txt') == 1

        # If the browser may not read the bucket, the whole file is shown from its own URL and the
        # Download that follows is still the same one download (the server remembers).
        visitor.route(
            '**/__e2e__/ft-s3/**',
            lambda route: (
                route.abort() if route.request.resource_type == 'fetch' else route.continue_()
            ),
        )
        visitor.keyboard.press('Escape')
        visitor.get_by_role('button', name='Preview e2e-preview-doc.pdf').click()
        expect(visitor.locator('#preview-page')).to_be_visible(timeout=15_000)
        visitor.click('#preview-view-full')
        expect(visitor.locator('#preview-full-pdf')).to_be_attached()
        expect(visitor.locator('#preview-counted')).to_be_visible()
        assert _events(receipts_server, 'e2e-preview-doc.pdf') == 1
        with visitor.expect_download() as download_info:
            visitor.click('#preview-download')
        assert download_info.value.suggested_filename == 'e2e-preview-doc.pdf'
        assert _events(receipts_server, 'e2e-preview-doc.pdf') == 1
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)
