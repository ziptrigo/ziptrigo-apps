"""Sharing a QR code (issue #5, PR 1), end to end in a real browser: the owner makes a link from the
dashboard, copies it, a second anonymous browser context opens it and downloads the image, and
revoking makes the page the neutral unavailable one at once.

Run on the shared e2e server (`receipts_server`). Every QR code this makes is named with the prefix
`Zq `; each test removes what it made first and last.
"""

import re
from pathlib import Path

from playwright.sync_api import Browser, Page, expect

from tests_e2e.conftest import ReceiptsServer

CLEANUP = (
    'import os\n'
    'from apps.qr_code.models import QRCode\n'
    "QRCode.objects.filter(created_by__email=os.environ['E2E_EMAIL'], name__startswith='Zq ')"
    '.delete()\n'
)
SEED = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.qr_code.models import QRCode\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "qr = QRCode.objects.create(created_by=user, name='Zq Shop door', qr_type='url',\n"
    "                           content='https://example.com/shop', image_file='x.png')\n"
    'print(qr.pk)\n'
)
UNAVAILABLE = "This QR code isn't available"


def _open_share(page: Page, server: ReceiptsServer, qr_id: str):
    """Open the dashboard row's menu and its Share item; the dialog."""
    page.goto(f'{server.url}/qr/')
    page.click(f'.qr-dropdown-btn[data-qr-id="{qr_id}"]')
    page.click(f'.qr-dropdown-menu[data-qr-id="{qr_id}"] [data-qr-share]')
    dialog = page.get_by_role('dialog', name='Share "Zq Shop door"')
    expect(dialog).to_be_visible()
    return dialog


def test_share_copy_open_download_and_revoke(
    page: Page, browser: Browser, receipts_server: ReceiptsServer, tmp_path: Path
):
    receipts_server.shell(CLEANUP)
    qr_id = receipts_server.shell(SEED).strip().splitlines()[-1]
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        dialog = _open_share(page, receipts_server, qr_id)
        expect(dialog).to_contain_text('This QR code has no links yet.')

        page.fill('#qr-share-label', 'for the printer')
        page.click('#qr-share-form button[type="submit"]')
        item = dialog.locator('#qr-share-links li', has_text='for the printer')
        expect(item).to_be_visible()
        address = item.locator('code').inner_text().strip()
        assert re.fullmatch(r'http://localhost:\d+/q/[A-Za-z0-9_-]{22}', address), address

        # Copy link flips its label (the clipboard itself may be refused in a headless browser).
        item.get_by_role('button', name='Copy link').click()
        expect(item.get_by_role('button', name='Copied')).to_be_visible()

        # Closing the dialog leaves the "Shared" icon on the row.
        dialog.get_by_role('button', name='Close').click()
        expect(dialog).to_be_hidden()
        expect(page.locator(f'#qr-shared-{qr_id} [title="Shared"]')).to_be_visible()

        # A visitor with no account opens the link: the image loads, nothing about the owner.
        visitor = visitor_context.new_page()
        visitor.goto(address)
        expect(visitor.get_by_role('heading', name='Zq Shop door')).to_be_visible()
        expect(visitor.get_by_text('https://example.com/shop')).to_be_visible()
        expect(visitor.locator('body')).not_to_contain_text('for the printer')
        expect(visitor.locator('body')).not_to_contain_text(receipts_server.email)
        image = visitor.locator('img[data-qr-image]')
        expect(image).to_be_visible()
        assert visitor.evaluate('document.querySelector("img[data-qr-image]").naturalWidth') > 0
        with visitor.expect_download() as download:
            visitor.get_by_role('link', name='Download').click()
        assert download.value.suggested_filename == 'zq-shop-door.png'
        assert Path(download.value.path()).read_bytes().startswith(b'\x89PNG')
        image_url = f'{address}/image'
        assert visitor.request.get(image_url).status == 200

        # Copy on the public page works for the visitor too.
        visitor.get_by_role('button', name='Copy').click()
        expect(visitor.get_by_role('button', name='Copied')).to_be_visible()

        # Revoke: Escape closes only the confirmation, the dialog stays; then confirm.
        dialog = _open_share(page, receipts_server, qr_id)
        dialog.get_by_role('button', name='Revoke').click()
        confirm = page.get_by_role('dialog', name='Revoke this link?')
        expect(confirm).to_be_visible()
        page.keyboard.press('Escape')
        expect(confirm).to_be_hidden()
        expect(dialog).to_be_visible()
        dialog.get_by_role('button', name='Revoke').click()
        confirm.get_by_role('button', name='Revoke').click()
        expect(page.get_by_text('Link revoked.')).to_be_visible()
        expect(page.get_by_role('dialog', name='Share "Zq Shop door"')).to_be_visible()
        expect(page.locator('#qr-share-links li').first).to_contain_text('Revoked')
        expect(page.locator(f'#qr-shared-{qr_id} [title="Shared"]')).to_have_count(0)

        visitor.goto(address)
        expect(visitor.get_by_role('heading', name=UNAVAILABLE)).to_be_visible()
        assert visitor.request.get(image_url).status == 404
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)


def test_share_from_the_editor_and_an_expiry(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    qr_id = receipts_server.shell(SEED).strip().splitlines()[-1]
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/qr/edit/{qr_id}/')
        page.click('#qr-share-btn')
        dialog = page.get_by_role('dialog', name='Share "Zq Shop door"')
        expect(dialog).to_be_visible()
        page.fill('#qr-share-expires', '12/31/2099')
        page.keyboard.press('Tab')
        page.click('#qr-share-form button[type="submit"]')
        item = dialog.locator('#qr-share-links li').first
        expect(item).to_contain_text('Expires')
        address = item.locator('code').inner_text().strip()

        visitor = visitor_context.new_page()
        visitor.goto(address)
        expect(visitor.get_by_role('heading', name='Zq Shop door')).to_be_visible()
        expect(visitor.locator('time[datetime]')).to_be_visible()

        # Escape closes the dialog and gives the focus back.
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)
