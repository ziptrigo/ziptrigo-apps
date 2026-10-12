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

    # The vendor was created for it, unvetted.
    expect(page.locator('#vendor-field')).to_contain_text('New vendor')

    # Edit a field: Save is an htmx form that redirects back with a message. Typing another vendor
    # name moves this receipt to a new (vetted) vendor of that name.
    page.fill('#id_vendor', 'Staples Office Supply')
    page.fill('#id_notes', 'printer toner')
    expect(page.locator('#vendor-field')).to_contain_text('future receipts read as')
    page.click('button:has-text("Save")')
    expect(page.locator('body')).to_contain_text('Saved.')
    expect(page.locator('h1')).to_contain_text('Staples Office Supply')
    expect(page.locator('#vendor-field')).to_contain_text('Vetted')
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
    expect(page.locator('#receipt-list')).not_to_contain_text('Staples Office Supply')
    page.click('text=Trash (1)')
    expect(page.locator('main')).to_contain_text('Staples Office Supply')
    page.click('button:has-text("Restore")')
    expect(page.locator('main')).to_contain_text('The trash is empty.')

    page.goto(f'{url}/receipts/')
    expect(page.locator('#receipt-list li', has_text='Staples Office Supply')).to_contain_text(
        'Processed'
    )

    # A second copy is read as "Staples" again: that name now belongs to the vendor chosen above,
    # so it is filed there, and the name as read is kept on the receipt.
    page.goto(f'{url}/receipts/upload/')
    page.set_input_files('#file-input', _receipt_photo(tmp_path / 'staples-again.jpg'))
    expect(page.locator('#upload-list')).to_contain_text('Uploaded')
    page.click('text=Go to receipts')
    expect(page.locator('#receipt-list li', has_text='Staples Office Supply')).to_have_count(2)
    page.locator('#receipt-list li', has_text='Staples Office Supply').first.click()
    expect(page.locator('#vendor-field')).to_contain_text('Read on the receipt as')
    expect(page.locator('#vendor-field')).to_contain_text('Staples')
    expect(page.locator('#vendor-field')).to_contain_text('Filed under your vendor')


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


def _text_file(path, name: str, content: bytes) -> str:
    target = path / name
    target.write_bytes(content)
    return str(target)


def test_an_unsupported_file_type_is_refused_in_its_row(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/upload/')
    requests = []
    page.on('request', lambda request: requests.append(request.url))

    page.set_input_files('#file-input', _text_file(tmp_path, 'notes.txt', b'not a receipt'))

    row = page.locator('#upload-list li')
    expect(row).to_contain_text('Unsupported file type')
    assert not [url for url in requests if '/upload/' in url]  # never even started


def test_a_file_that_is_not_what_it_claims_is_rejected_by_the_server(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    """A `.jpg` that is really text passes the browser's checks, uploads, and is refused (422)
    by `complete`: the row shows the server's message and offers no "View receipt" link."""
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/upload/')

    page.set_input_files(
        '#file-input', _text_file(tmp_path, 'fake.jpg', b'MZ this is not an image')
    )

    row = page.locator('#upload-list li')
    expect(row).to_contain_text('✕')
    expect(row.locator('p')).not_to_contain_text('Uploaded')
    expect(row.get_by_role('link', name='View receipt')).to_have_count(0)
    expect(row.get_by_role('button', name='Cancel upload of fake.jpg')).to_be_hidden()


def test_a_frozen_account_gets_a_402_row_with_a_top_up_link(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    """The account is frozen after the page loaded (so the pickers are still enabled), which is
    what `start` answers 402 for."""
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/upload/')
    set_frozen = (
        'import os\n'
        'from django.utils import timezone\n'
        'from apps.accounts.models import User\n'
        'from apps.receipts.models import ReceiptsAccount\n'
        'from apps.receipts.services import get_account\n'
        "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
        'get_account(user)\n'
        'ReceiptsAccount.objects.filter(pk=user.pk).update(frozen_at={value})\n'
    )
    receipts_server.shell(set_frozen.format(value='timezone.now()'))
    try:
        page.set_input_files('#file-input', _receipt_photo(tmp_path / 'frozen.jpg'))

        row = page.locator('#upload-list li')
        expect(row).to_contain_text('paused')
        top_up = row.get_by_role('link', name='Top up')
        expect(top_up).to_be_visible()
        assert 'credits' in top_up.get_attribute('href')
    finally:
        receipts_server.shell(set_frozen.format(value='None'))


def test_cancelling_during_the_upload_creates_no_receipt(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    held = []
    page.route('**/__e2e__/s3/**', lambda route: held.append(route))  # the PUT never answers
    completed = []
    page.on(
        'request',
        lambda request: completed.append(request.url) if '/complete/' in request.url else None,
    )
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/upload/')

    page.set_input_files('#file-input', _receipt_photo(tmp_path / 'cancel.jpg'))
    row = page.locator('#upload-list li')
    expect(row).to_contain_text('Uploading')
    page.get_by_role('button', name='Cancel upload of cancel.jpg').click()

    expect(page.locator('#upload-list li')).to_have_count(0)
    for route in held:
        route.abort()
    page.wait_for_timeout(500)  # long enough for a (wrong) `complete` to have been sent
    assert completed == []


def test_once_the_file_is_in_the_bucket_it_can_no_longer_be_cancelled(
    page: Page, receipts_server: ReceiptsServer, tmp_path
):
    """The bug this guards: a cancel after the PUT removed the row, but `complete` still ran and
    billed the receipt. Now the button goes away as soon as the PUT succeeds, and the receipt that
    `complete` makes is the one the row links to."""
    held = []
    page.route('**/upload/*/complete/', lambda route: held.append(route))
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/upload/')

    page.set_input_files('#file-input', _receipt_photo(tmp_path / 'late.jpg'))
    row = page.locator('#upload-list li')
    expect(row).to_contain_text('Finishing')
    expect(row.get_by_role('button', name='Cancel upload of late.jpg')).to_be_hidden()

    held[0].continue_()
    expect(row).to_contain_text('Uploaded')
    row.get_by_role('link', name='View receipt').click()
    expect(page.locator('h1')).to_contain_text('Staples')


# -- Vendors -------------------------------------------------------------------------------------


def _seed_vendor(
    server: ReceiptsServer,
    name: str,
    *,
    vetted: bool = False,
    receipts: int = 1,
    read: str = '',
) -> None:
    """A vendor of the e2e account with its own name as an alias, and `receipts` processed receipts
    filed under it (without a file: these tests look at the vendor, not the preview)."""
    server.shell(
        'import os\n'
        'from datetime import date\n'
        'from decimal import Decimal\n'
        'from django.utils import timezone\n'
        'from apps.accounts.models import User\n'
        'from apps.receipts.models import Receipt, Vendor, VendorAlias\n'
        'from apps.receipts.services.vendors import normalize_key\n'
        "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
        f'vendor = Vendor.objects.create(user=user, name={name!r},'
        f' vetted_at=timezone.now() if {vetted} else None)\n'
        f'VendorAlias.objects.create(user=user, vendor=vendor, key=normalize_key({name!r}),'
        f" name={name!r}, source='user')\n"
        f'for n in range({receipts}):\n'
        f"    Receipt.objects.create(user=user, status='processed', vendor={name!r},"
        f' vendor_ref=vendor, vendor_extracted={read!r}, amount=Decimal("10.00"), currency="USD",'
        f' receipt_date=date(2026, 9, 1 + n), source="upload", content_type="image/jpeg")\n'
    )


def test_a_new_vendor_is_vetted_and_renamed_from_the_receipt_page(
    page: Page, receipts_server: ReceiptsServer
):
    _seed_vendor(receipts_server, 'Zed Widgets Inc', read='ZED WIDGETS, INC.')
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/')
    page.locator('#receipt-list li', has_text='Zed Widgets Inc').click()
    field = page.locator('#vendor-field')
    expect(field).to_contain_text('New vendor')
    expect(field).to_contain_text('First receipt from this vendor.')

    # "Mark as vetted" re-renders the field in place.
    field.get_by_role('button', name='Mark as vetted').click()
    expect(field).to_contain_text('Vetted')
    expect(field.get_by_role('button', name='Mark as vetted')).to_have_count(0)
    expect(field).to_contain_text('Read on the receipt as')

    # "Rename vendor" opens the dialog; Escape closes it, and renaming reloads the page.
    field.get_by_role('button', name='Rename vendor').click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    expect(dialog.get_by_label('Name')).to_be_focused()
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    field.get_by_role('button', name='Rename vendor').click()
    dialog.get_by_label('Name').fill('Zed Widgets Worldwide')
    dialog.get_by_role('button', name='Rename').click()
    expect(page.locator('body')).to_contain_text('Renamed to Zed Widgets Worldwide.')
    expect(page.locator('h1')).to_contain_text('Zed Widgets Worldwide')


def test_the_vendor_field_suggests_vendors_and_teaches_the_one_picked(
    page: Page, receipts_server: ReceiptsServer
):
    # (Read as something with another key than its own name: a vetted vendor keeps the name it is
    # called, so moving its receipts away would teach nothing and the field would not say it does.)
    _seed_vendor(receipts_server, 'Combo Alpha', vetted=True, read='Combo Alpha Supply Co.')
    _seed_vendor(receipts_server, 'Combo Beta', vetted=True)
    _seed_vendor(receipts_server, 'Combo Gamma')
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/')
    page.locator('#receipt-list li', has_text='Combo Alpha').click()

    field = page.locator('#vendor-field')
    vendor = page.locator('#id_vendor')
    vendor.fill('')
    vendor.type('Combo')
    listbox = page.get_by_role('listbox', name='Your vendors')
    expect(listbox).to_be_visible()
    expect(listbox.get_by_role('option')).to_have_count(4)  # three vendors and "use as new"
    expect(listbox).to_contain_text('Not vetted yet')

    # Keyboard: the arrows move, Enter picks (and does not submit the form).
    vendor.press('ArrowDown')
    vendor.press('ArrowDown')
    expect(vendor).to_have_attribute('aria-activedescendant', 'vendor-option-v2')
    vendor.press('Enter')
    expect(vendor).to_have_value('Combo Beta')
    expect(listbox).to_be_hidden()
    expect(page.locator('input[name="vendor_id"]')).not_to_have_value('')
    expect(field).to_contain_text('future receipts read as')
    expect(field).to_contain_text('Combo Alpha Supply Co.')
    assert page.url.rstrip('/').rsplit('/', 1)[-1] != ''  # still on the receipt's page

    page.click('button:has-text("Save")')
    expect(page.locator('h1')).to_contain_text('Combo Beta')

    # Typing a name that is nobody's offers it as a new vendor; Escape closes the list.
    vendor = page.locator('#id_vendor')
    vendor.fill('')
    vendor.type('Combo Delta')
    expect(page.get_by_role('option', name='Use “Combo Delta” as a new vendor')).to_be_visible()
    vendor.press('Escape')
    expect(page.get_by_role('listbox', name='Your vendors')).to_be_hidden()


def test_the_vendors_page_vets_in_bulk_merges_and_filters_the_dashboard(
    page: Page, receipts_server: ReceiptsServer
):
    _seed_vendor(receipts_server, 'Bulk One', receipts=2)
    _seed_vendor(receipts_server, 'Bulk Two')
    _seed_vendor(receipts_server, 'Bulk Three', vetted=True)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/vendors/')

    page.fill('#vendor-q', 'Bulk')
    rows = page.locator('#vendor-list li[id^="vendor-"]')
    expect(rows).to_have_count(3)
    expect(page).to_have_url(re.compile(r'\?q=Bulk'))

    # The bulk bar appears with the first tick; "Mark as vetted" refreshes the list in place.
    bar = page.get_by_role('region', name='Actions for the selected vendors')
    expect(bar).to_be_hidden()
    page.get_by_label('Select Bulk One').check()
    page.get_by_label('Select Bulk Two').check()
    expect(bar).to_contain_text('2 selected')
    bar.get_by_role('button', name='Mark as vetted').click()
    expect(page.locator('#vendor-list')).to_contain_text('Marked 2 vendors as vetted.')
    expect(page.locator('#vendor-list li', has_text='Bulk Two')).to_contain_text('Vetted')

    # Merge all three into the busiest vetted one.
    for name in ('Bulk One', 'Bulk Two', 'Bulk Three'):
        page.get_by_label(f'Select {name}').check()
    bar.get_by_role('button', name='Merge').click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_contain_text('Merge 3 vendors')
    expect(dialog).to_contain_text('Which name should the 4 receipts show?')
    dialog.get_by_label('Bulk One').check()
    expect(dialog.get_by_role('button', name='Merge into Bulk One')).to_be_visible()
    dialog.get_by_role('button', name='Merge into Bulk One').click()
    expect(page.locator('body')).to_contain_text('Merged 2 vendors into Bulk One.')
    expect(page.locator('#vendor-list li[id^="vendor-"]')).to_have_count(1)

    # The receipts count leads to the dashboard filtered to the vendor, with a chip to remove it.
    page.get_by_role('link', name='4 receipts from Bulk One').click()
    chip = page.get_by_text('Vendor: Bulk One')
    expect(chip).to_be_visible()
    expect(page.locator('#receipt-list li[id^="receipt-"]')).to_have_count(4)
    page.get_by_role('link', name='Remove the vendor filter').click()
    expect(chip).to_have_count(0)
    expect(page).not_to_have_url(re.compile(r'vendor='))


def test_a_rename_that_clashes_offers_to_merge_and_an_empty_vendor_can_be_deleted(
    page: Page, receipts_server: ReceiptsServer
):
    _seed_vendor(receipts_server, 'Clash Coffee, Inc.', receipts=2, vetted=True)
    _seed_vendor(receipts_server, 'Clash Brew')
    _seed_vendor(receipts_server, 'Clash Empty', receipts=0)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/vendors/?q=Clash')

    # Deleting is only possible for a vendor without receipts, and asks first.
    page.get_by_role('button', name='Actions for Clash Empty').click()
    expect(page.get_by_role('menuitem', name='Delete')).to_be_visible()
    page.get_by_role('menuitem', name='Delete').click()
    page.get_by_role('dialog').get_by_role('button', name='Delete').click()
    expect(page.locator('body')).to_contain_text('Deleted Clash Empty.')

    page.get_by_role('button', name='Actions for Clash Brew').click()
    page.get_by_role('menuitem', name='Rename').click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_label('Name').fill('Clash Coffee')
    dialog.get_by_role('button', name='Rename').click()
    expect(dialog).to_contain_text('You already have a vendor called Clash Coffee, Inc.')
    expect(dialog).to_contain_text('Merge Clash Brew into it?')
    dialog.get_by_role('button', name='Merge into Clash Coffee, Inc.').click()
    expect(page.locator('body')).to_contain_text('Merged 1 vendor into Clash Coffee, Inc.')
    expect(page.locator('#vendor-list li[id^="vendor-"]')).to_have_count(1)
