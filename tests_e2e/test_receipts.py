"""Receipts, end to end in a real browser.

The server is `receipts_server` (see `receipts_e2e_settings.py`): the real site with S3 and the AI
model replaced by in-memory fakes, so this covers the real pages, htmx and the upload script
without any network. The fake AI answers every receipt with the same data (Staples, 42.10 USD).
"""

import base64
import hashlib
import re
import uuid

from PIL import Image, ImageDraw
from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer


def _receipt_photo(path) -> str:
    image = Image.new('RGB', (240, 320), (245, 245, 245))
    ImageDraw.Draw(image).text((20, 20), 'STAPLES 42.10', fill=(0, 0, 0))
    image.save(path, format='JPEG')
    return str(path)


def test_upload_review_edit_trash_and_restore(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    url = receipts_server.url
    receipts_server.login(page)

    # Upload a photo: browser SHA-256 -> start -> PUT to the (fake) bucket -> complete.
    page.goto(f'{url}/receipts/upload/')
    page.set_input_files('#file-input', _receipt_photo(tmp_path / 'staples.jpg'))
    expect(page.locator('#upload-list')).to_contain_text('Uploaded')

    # The fake AI read it during `complete`: it is on the dashboard, processed.
    page.click('text=Go to receipts')
    row = page.locator('#receipt-list li', has_text='Staples')
    expect(row).to_contain_text('Processed')
    expect(row).to_contain_text('USD 42.10')
    expect(row).to_contain_text('2026-10-09')

    # The detail page previews the uploaded image from the bucket.
    row.click()
    expect(page.locator('h1')).to_contain_text('Staples')
    preview = page.locator('#receipt-preview img')
    expect(preview).to_be_visible()
    assert preview.evaluate('img => img.complete && img.naturalWidth > 0')

    # Edit a field: Save is an htmx form that redirects back with a message.
    page.fill('#id_vendor', 'Staples Inc')
    page.fill('#id_notes', 'printer toner')
    page.click('button:has-text("Save")')
    expect(page.locator('body')).to_contain_text('Saved.')
    expect(page.locator('h1')).to_contain_text('Staples Inc')
    expect(page.locator('#id_notes')).to_have_value('printer toner')

    # A refused edit shows its message in the form's slot and changes nothing.
    page.fill('#id_amount', 'forty')
    page.click('button:has-text("Save")')
    expect(page.locator('#receipt-form-msg')).to_contain_text(
        'Amount: Enter the amount as a number.'
    )
    page.reload()
    expect(page.locator('#id_amount')).to_have_value('42.10')

    # Trash it, then restore it from the Trash page.
    page.click('button:has-text("Move to trash")')
    expect(page.locator('body')).to_contain_text('Moved to the trash')
    expect(page.locator('#receipt-list')).not_to_contain_text('Staples Inc')
    page.click('text=Trash (1)')
    expect(page.locator('main')).to_contain_text('Staples Inc')
    page.click('button:has-text("Restore")')
    expect(page.locator('main')).to_contain_text('The trash is empty.')

    page.goto(f'{url}/receipts/')
    expect(page.locator('#receipt-list li', has_text='Staples Inc')).to_contain_text('Processed')


def test_the_dashboard_filters_and_the_search_update_the_list_and_the_url(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    url = receipts_server.url
    receipts_server.login(page)
    page.goto(f'{url}/receipts/upload/')
    page.set_input_files('#file-input', _receipt_photo(tmp_path / 'one.jpg'))
    expect(page.locator('#upload-list')).to_contain_text('Uploaded')
    page.goto(f'{url}/receipts/')

    page.fill('#q', 'no such vendor')
    expect(page.locator('#receipt-list')).to_contain_text('No receipts found.')
    expect(page).to_have_url(re.compile(r'\?q=no\+such\+vendor'))
    assert page.get_attribute('#export-link', 'href') == '/receipts/export.csv?q=no+such+vendor'

    page.fill('#q', 'staples')
    expect(page.locator('#receipt-list')).to_contain_text('Staples')


def test_an_email_body_is_shown_in_a_sandbox_that_runs_nothing(
    page: Page, receipts_server: ReceiptsServer
):
    html = (
        b'<html><body><h1>Your Shop receipt</h1>'
        b'<img src="https://tracker.invalid/pixel.gif">'
        b'<script>document.body.setAttribute("data-ran", "1")</script>'
        b'</body></html>'
    )
    receipt_id = uuid.uuid4()
    key = f'receipts/users/e2e/files/{receipt_id}'
    receipts_server.shell(
        'import os\n'
        'from apps.accounts.models import User\n'
        'from apps.receipts.models import Receipt\n'
        "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
        f"Receipt.objects.create(id='{receipt_id}', user=user, status='processed', vendor='Shop',"
        f" source='email', content_type='text/html', file_key='{key}', size_bytes={len(html)})\n"
    )
    checksum = base64.b64encode(hashlib.sha256(html).digest()).decode()
    page.context.request.put(
        f'{receipts_server.url}/__e2e__/s3/{key}',
        data=html,
        headers={'x-amz-checksum-sha256': checksum},
    )
    finished = []
    blocked = []
    page.on('requestfinished', lambda request: finished.append(request.url))
    page.on('requestfailed', lambda request: blocked.append((request.url, request.failure)))

    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/{receipt_id}/')

    frame = page.frame_locator('iframe[sandbox]')
    expect(frame.locator('h1')).to_have_text('Your Shop receipt')
    assert page.locator('iframe[sandbox]').get_attribute('sandbox') == ''
    expect(frame.locator('body[data-ran]')).to_have_count(0)  # the script did not run
    # The tracking pixel was never fetched: the CSP stopped it (the request may be seen, but fails).
    assert not [u for u in finished if 'tracker.invalid' in u]
    assert [f for u, f in blocked if 'tracker.invalid' in u and 'csp' in (f or '').lower()]
