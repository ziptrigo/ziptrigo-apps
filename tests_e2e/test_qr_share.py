"""Sharing a QR code (issue #5, PRs 1 to 3), end to end in a real browser: the owner makes a link
from the dashboard, copies it, a second anonymous browser context opens it and downloads the
image, emails it to a person (the email shows at `/__e2e__/mail/` and its link opens the page),
and revoking makes the page the neutral unavailable one at once. A visitor reports the QR code from
the page (no login); staff taking it down (done in the shell: the admin is covered by unit tests)
makes the link the neutral page and shows the owner the reason on the dashboard and in the dialog.

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


def test_send_the_link_by_email_resend_and_refusals(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    qr_id = receipts_server.shell(SEED).strip().splitlines()[-1]
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        dialog = _open_share(page, receipts_server, qr_id)
        page.click('#qr-share-form button[type="submit"]')
        item = dialog.locator('#qr-share-links li').first
        address = item.locator('code').inner_text().strip()

        item.get_by_role('button', name='Send by email').click()
        form = item.locator('form[id^="link-mail-"]')
        form.get_by_label('Email addresses').fill('Ann@Example.com, bob@example.com')
        form.get_by_label('Message (optional)').fill('Here is the <b>door</b> code')
        form.get_by_role('button', name='Send email').click()
        expect(dialog).to_contain_text('Emailed the link to 2 people.')
        people = item.locator('ul[id^="link-people-"] li')
        expect(people).to_have_count(2)
        expect(people.first).to_contain_text('Ann@example.com')

        # An invalid address is a refusal under the field, and nothing more is added.
        item.get_by_role('button', name='Send by email').click()
        form = item.locator('form[id^="link-mail-"]')
        form.get_by_label('Email addresses').fill('not-an-address')
        form.get_by_role('button', name='Send email').click()
        expect(dialog.locator('#qr-share-msg')).to_contain_text(
            '"not-an-address" is not a valid email address.'
        )
        expect(people).to_have_count(2)

        # A resend inside the cooldown is refused with the wait.
        people.first.get_by_role('button', name='Resend').click()
        expect(dialog.locator('#qr-share-msg')).to_contain_text(
            'This person was emailed recently; you can resend in'
        )

        # The email: a fixed subject, the owner's name, the message as text, and the link.
        page.goto(f'{receipts_server.url}/__e2e__/mail/?to=ann@example.com')
        email = page.locator('article.email')
        expect(email).to_have_count(1)
        expect(email.locator('.subject')).to_have_text('A QR code was shared with you')
        expect(email.locator('.text-body')).to_contain_text('shared the QR code "Zq Shop door"')
        expect(email.locator('.text-body')).to_contain_text('Here is the')
        # The HTML part escapes what the owner typed (the viewer shows its source).
        expect(email.locator('.html-body')).to_contain_text(
            'Here is the &lt;b&gt;door&lt;/b&gt; code'
        )
        expect(email.locator('.text-body')).to_contain_text(address)

        # The link in the email opens the shared page for someone with no account.
        visitor = visitor_context.new_page()
        visitor.goto(address)
        expect(visitor.get_by_role('heading', name='Zq Shop door')).to_be_visible()
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)


TAKE_DOWN = (
    'from apps.accounts.models import User\n'
    'from apps.qr_code.models import QRCode, QRShareReport\n'
    'from apps.qr_code.services import take_down_qrcode\n'
    "staff = User.objects.create_user(email='zq-staff@example.com', is_staff=True)\n"
    "qr = QRCode.objects.get(pk='{qr_id}')\n"
    "take_down_qrcode(qr, by=staff, reason='Pretends to be a bank', internal_note='SECRET NOTE')\n"
    "print(QRShareReport.objects.filter(qrcode=qr).values_list('status', flat=True).first())\n"
    'staff.delete()\n'
)


def test_report_a_shared_qr_code_then_a_takedown(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    qr_id = receipts_server.shell(SEED).strip().splitlines()[-1]
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        dialog = _open_share(page, receipts_server, qr_id)
        page.click('#qr-share-form button[type="submit"]')
        item = dialog.locator('#qr-share-links li').first
        address = item.locator('code').inner_text().strip()

        # A visitor with no account opens the report form (collapsed until asked for) and sends it.
        visitor = visitor_context.new_page()
        visitor.goto(address)
        form = visitor.locator('#report-box form')
        expect(form).to_be_hidden()
        visitor.get_by_role('button', name='Report this QR code').click()
        expect(form).to_be_visible()
        visitor.get_by_label('Reason').select_option('phishing')
        visitor.get_by_label('Details (optional)').fill('Asks for bank details')
        visitor.get_by_role('button', name='Submit report').click()
        expect(visitor.locator('#report-box')).to_contain_text(
            'Thank you, your report has been submitted.'
        )

        # Staff take the code down: the report is actioned, the link is the neutral page.
        status = receipts_server.shell(TAKE_DOWN.format(qr_id=qr_id)).strip().splitlines()[-1]
        assert status == 'actioned'
        visitor.goto(address)
        expect(visitor.get_by_role('heading', name=UNAVAILABLE)).to_be_visible()
        assert visitor.request.get(f'{address}/image').status == 404

        # The owner sees the reason (never the internal note) on the dashboard and in the dialog.
        page.goto(f'{receipts_server.url}/qr/')
        row = page.locator('li', has=page.locator(f'[data-qr-id="{qr_id}"]')).first
        expect(row.locator('[data-taken-down]')).to_contain_text('Pretends to be a bank')
        expect(page.locator('body')).not_to_contain_text('SECRET NOTE')
        page.click(f'.qr-dropdown-btn[data-qr-id="{qr_id}"]')
        page.click(f'.qr-dropdown-menu[data-qr-id="{qr_id}"] [data-qr-share]')
        dialog = page.get_by_role('dialog', name='Share "Zq Shop door"')
        expect(dialog.locator('#qr-share-taken-down')).to_contain_text('Pretends to be a bank')
        expect(dialog.get_by_role('button', name='Create link')).to_have_count(0)
        expect(dialog.get_by_role('button', name='Revoke')).to_be_visible()
        expect(page.locator(f'#qr-shared-{qr_id} [title="Shared"]')).to_have_count(0)
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)
