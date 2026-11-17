"""Receipts "Download files" (issue #315), end to end in a real browser: open it from the account
page's "App actions", choose a date range, open the dialog, confirm the cost, land on the export's page (the zip files are built by the inline task
backend during the request), download a zip and look inside it, then delete the export.

Runs on `receipts_server` (fake S3 and AI, see `receipts_e2e_settings.py`); its fake bucket serves
the zip like S3 would.
"""

import csv
import io
import re
import uuid
import zipfile

from PIL import Image, ImageDraw
from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer


def _photo(path) -> bytes:
    image = Image.new('RGB', (240, 320), (245, 245, 245))
    ImageDraw.Draw(image).text((20, 20), 'EXPORT ME', fill=(0, 0, 0))
    image.save(path, format='JPEG')
    return path.read_bytes()


def test_download_files_confirm_the_cost_download_the_zip_and_delete_it(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    url = receipts_server.url
    receipts_server.login(page)
    token = uuid.uuid4().hex[:10]

    # One receipt of our own, marked with a note so the list can be narrowed to it.
    photo = tmp_path / f'{token}.jpg'
    original = _photo(photo)
    page.goto(f'{url}/receipts/upload/')
    page.set_input_files('#file-input', str(photo))
    expect(page.locator('#upload-list')).to_contain_text('Uploaded')
    page.click('text=Go to receipts')
    page.locator('#receipt-list li', has_text='Staples').first.click()
    page.fill('#id_notes', token)
    page.click('button:has-text("Save")')
    expect(page.locator('body')).to_contain_text('Saved.')

    # The buttons live on the account page (App actions), not on the dashboard.
    page.goto(f'{url}/receipts/')
    expect(page.locator('#export-files-link')).to_have_count(0)
    expect(page.locator('#export-link')).to_have_count(0)
    page.goto(f'{url}/account/')
    block = page.locator('[data-app-actions=receipts]')
    expect(block).to_contain_text('Download files')
    expect(block.locator('input[name=date_from]')).to_be_hidden()
    block.locator('select[name=date_range]').select_option('custom')
    expect(block.locator('#receipts-export-from')).to_be_visible()
    block.locator('select[name=date_range]').select_option('any')
    expect(block.locator('#receipts-export-from')).to_be_hidden()

    page.click('#export-files-link')
    dialog = page.locator('#export-modal [role=dialog]')
    expect(dialog).to_contain_text('All your receipts')
    expect(dialog).to_contain_text(re.compile(r'\d+ files?'))
    expect(dialog).to_contain_text('1 zip file')
    expect(dialog).to_contain_text('(3 per file)')
    expect(dialog).to_contain_text('Year / Vendor')
    expect(dialog).to_contain_text('Vendor / Year')
    dialog.locator('input[value=vendor_year]').check()

    # Confirm: charged, and the export's page lists the zip (built inline).
    dialog.locator('button[type=submit]').click()
    page.wait_for_url(re.compile(r'/receipts/exports/[0-9a-f-]{36}/$'))
    expect(page.locator('body')).to_contain_text('preparing your zip files')
    expect(page.locator('#export-panel')).to_contain_text('Ready')
    expect(page.locator('#export-panel')).to_contain_text('with receipts.csv')

    with page.expect_download() as download_info:
        page.locator('#export-panel a:has-text("Download")').first.click()
    download = download_info.value
    assert download.suggested_filename.startswith(
        'receipts-'
    ) and download.suggested_filename.endswith('.zip')
    saved = tmp_path / 'saved.zip'
    download.save_as(saved)

    with zipfile.ZipFile(saved) as archive:
        assert archive.testzip() is None
        files = [n for n in archive.namelist() if n.endswith(f'{token}.jpg')]
        assert len(files) == 1
        # Vendor / Year layout: the vendor folder first, then the year (or "No date").
        assert files[0].count('/') == 2
        assert archive.read(files[0]) == original
        rows = list(csv.reader(io.StringIO(archive.read('receipts.csv').decode())))
    assert rows[0] == ['date', 'vendor', 'type', 'amount', 'currency', 'file', 'part']
    assert files[0] in [row[5] for row in rows[1:]] and rows[1][6] == '1'

    # It is listed, and deleting it (a confirmation first) removes it.
    page.click('text=Exports')
    expect(page.locator('main')).to_contain_text('1 file')
    page.locator('main a:has-text("Open")').first.click()
    page.click('button:has-text("Delete")')
    page.locator('[role=dialog] button:has-text("Delete")').click()
    expect(page.locator('body')).to_contain_text('The export was deleted.')
