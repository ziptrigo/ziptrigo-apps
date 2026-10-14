"""The shared drag & drop zone (`core/js/drop_zone.js`) on the pages that use it.

Playwright can't perform a native OS drag, so the test dispatches a `drop` event on the zone. Two
shapes: a plain `DataTransfer` carrying a file (what a browser gives when entries aren't available,
exercising the `dataTransfer.files` path), and a stand-in `dataTransfer` whose items answer
`webkitGetAsEntry()` with a folder tree, exercising the recursive walk (flat result, like the "Add
folder" picker). Runs on the server of `receipts_server` (see `receipts_e2e_settings.py`), where
file transfer's S3 is also an in-memory fake.
"""

import pytest
from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

DROP_FILE_JS = """
(zoneSelector) => {
    const dt = new DataTransfer();
    dt.items.add(new File(['hello'], 'dropped.txt', { type: 'text/plain' }));
    const zone = document.querySelector(zoneSelector);
    zone.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true, cancelable: true }));
    zone.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }));
    zone.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
}
"""

# A dropped folder `receipts-folder/` holding `a.txt` and `inner/b.txt`, as `webkitGetAsEntry()`
# reports it. `readEntries` hands over one entry per call, to check that the walk keeps asking
# until it gets an empty batch.
DROP_FOLDER_JS = """
(zoneSelector) => {
    const fileEntry = (name) => ({
        isFile: true,
        isDirectory: false,
        file: (resolve) => resolve(new File([name], name, { type: 'text/plain' })),
    });
    const dirEntry = (name, children) => ({
        isFile: false,
        isDirectory: true,
        name,
        createReader: () => {
            let i = 0;
            return {
                readEntries: (resolve) => resolve(i < children.length ? [children[i++]] : []),
            };
        },
    });
    const folder = dirEntry('dropped-folder', [fileEntry('a.txt'), dirEntry('inner', [fileEntry('b.txt')])]);
    const dt = {
        types: ['Files'],
        files: [],
        items: [{ kind: 'file', webkitGetAsEntry: () => folder }],
    };
    const zone = document.querySelector(zoneSelector);
    const drop = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(drop, 'dataTransfer', { value: dt });
    zone.dispatchEvent(drop);
}
"""

# (page path, drop zone, where the added files are listed, what a row shows for a file named N)
PAGES = [
    pytest.param('/receipts/upload/', '#drop-zone', '#upload-list li', id='receipts'),
    pytest.param('/transfer/send/', '#drop-zone', '#file-list li', id='file-transfer-send'),
]


@pytest.mark.parametrize(('path', 'zone', 'rows'), PAGES)
def test_dropping_a_file_adds_it_to_the_list(
    page: Page, receipts_server: ReceiptsServer, path: str, zone: str, rows: str
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}{path}')
    expect(page.locator(rows)).to_have_count(0)

    page.evaluate(DROP_FILE_JS, zone)

    expect(page.locator(rows)).to_have_count(1)
    expect(page.locator(rows)).to_contain_text('dropped.txt')


@pytest.mark.parametrize(('path', 'zone', 'rows'), PAGES)
def test_dropping_a_folder_adds_every_file_in_it_flat(
    page: Page, receipts_server: ReceiptsServer, path: str, zone: str, rows: str
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}{path}')

    page.evaluate(DROP_FOLDER_JS, zone)

    expect(page.locator(rows)).to_have_count(2)
    expect(page.locator(rows).filter(has_text='a.txt')).to_have_count(1)
    expect(page.locator(rows).filter(has_text='b.txt')).to_have_count(1)
