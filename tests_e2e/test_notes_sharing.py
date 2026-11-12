"""Sharing notes (issue #260, PR 4), end to end in a real browser: the owner makes an editable link,
a second, anonymous browser context reads and edits the note through it, the owner sees the notice
and the version "by Someone via a shared link" and restores the earlier one, a QR code opens, a
visitor reports the note, and revoking makes the page unavailable.

Run on the shared e2e server (`receipts_server`). Every note this makes has the title prefix
`Zn `; each test removes what it made first and last.
"""

import re

from playwright.sync_api import Browser, Page, expect

from tests_e2e.conftest import ReceiptsServer

ORIGINAL = '# Plan\n\n```python\nx = 1\n```\n\n- [ ] ship it\n'
CLEANUP = (
    'import os\n'
    'from apps.notes.models import Note\n'
    "Note.objects.filter(user__email=os.environ['E2E_EMAIL'], title__startswith='Zn ').delete()\n"
)
SEED = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.notes.services import create_note\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    f"create_note(user, title='Zn Shared plan', content={ORIGINAL!r})\n"
)
UNAVAILABLE = "This note isn't available"


def _open_note(page: Page, server: ReceiptsServer) -> None:
    page.goto(f'{server.url}/notes/')
    page.click('li.note-row a:has-text("Zn Shared plan")')
    page.wait_for_url(re.compile(r'/notes/[0-9a-f-]{36}/$'))


def _make_link(page: Page, *, editable: bool, label: str) -> str:
    """Open the share panel, create a link and return its address."""
    page.click('a:has-text("Share")')
    panel = page.locator('#share-panel')
    expect(panel.get_by_role('heading', name='Share this note')).to_be_visible()
    if editable:
        panel.locator('#new-mode-edit').check()
        expect(
            panel.get_by_text("Anyone with this link can change the note's content.")
        ).to_be_visible()
    page.fill('#share-label', label)
    page.click('#share-form button[type="submit"]')
    item = panel.locator('#share-links li', has_text=label)
    expect(item).to_be_visible()
    address = item.locator('code').inner_text().strip()
    assert re.fullmatch(r'http://localhost:\d+/n/[A-Za-z0-9_-]{22}', address), address
    return address


def test_edit_through_a_link_notice_history_restore_and_revoke(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    receipts_server.shell(SEED)
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        _open_note(page, receipts_server)
        address = _make_link(page, editable=True, label='the team')
        expect(page.locator('#share-indicator')).to_contain_text('Shared')

        # The QR code opens in a popup with the address, and Escape closes it.
        page.click('#share-links li button:has-text("QR code")')
        dialog = page.get_by_role('dialog', name='QR code')
        expect(dialog).to_be_visible()
        expect(dialog.locator('img[data-qr-image]')).to_have_attribute(
            'src', re.compile(r'^data:image/png;base64,')
        )
        expect(dialog).to_contain_text(address)
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()

        # A visitor with no account reads it: coloured code, no owner, no label.
        visitor = visitor_context.new_page()
        visitor.goto(address)
        expect(visitor.get_by_role('heading', name='Zn Shared plan')).to_be_visible()
        expect(visitor.locator('#note-body pre.hl')).to_be_visible()
        expect(visitor.locator('body')).not_to_contain_text('the team')
        expect(visitor.locator('body')).not_to_contain_text(receipts_server.email)
        for name in ('Copy note', 'Raw', 'Download', 'QR code', 'Duplicate', 'Edit'):
            expect(
                visitor.get_by_role(
                    'link' if name in ('Raw', 'Download', 'Edit') else 'button', name=name
                ).first
            ).to_be_visible()

        # ... and edits the content (the title is read only).
        visitor.click('a:has-text("Edit")')
        expect(visitor.locator('#note-title')).to_have_attribute('readonly', '')
        visitor.fill('#note-content', 'edited by a visitor\n\n- [ ] ship it\n')
        expect(visitor.locator('#note-preview')).to_contain_text('edited by a visitor')
        visitor.click('button[type="submit"]:has-text("Save")')
        visitor.wait_for_url(address)
        expect(visitor.get_by_text('Saved.')).to_be_visible()
        expect(visitor.locator('#note-body')).to_contain_text('edited by a visitor')

        # Ticking a box through the editable link saves too.
        with visitor.expect_response(lambda r: r.url.endswith('/checklist')) as saved:
            visitor.locator('#note-body input.task-list-item-checkbox').first.check()
        assert saved.value.status == 200

        # The owner has one notice, the new text and a version made by "Someone via a shared link".
        page.goto(f'{receipts_server.url}/notifications/')
        notices = page.get_by_text('was edited through a shared link')
        expect(notices).to_have_count(1)
        _open_note(page, receipts_server)
        expect(page.locator('#note-body')).to_contain_text('edited by a visitor')
        page.click('a:has-text("History")')
        expect(page.locator('li', has_text='Someone via a shared link').first).to_be_visible()

        # Restore the owner's original version (the one before the visitor's session).
        page.click('li:has-text("Version 1") button:has-text("Restore")')
        page.click('[role="dialog"] button[type="submit"]')
        expect(page.get_by_text('Restored version 1.')).to_be_visible()
        expect(page.locator('#note-body')).to_contain_text('x = 1')
        expect(page.locator('#note-body')).not_to_contain_text('edited by a visitor')

        # Revoking from the panel makes the page unavailable at once.
        page.click('a:has-text("Share")')
        page.click('#share-links li button:has-text("Revoke")')
        page.click('[role="dialog"] button[type="submit"]')
        expect(page.get_by_text('Link revoked.')).to_be_visible()
        expect(page.locator('#share-links li').first).to_contain_text('Revoked')
        visitor.goto(address)
        expect(visitor.get_by_role('heading', name=UNAVAILABLE)).to_be_visible()
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)


def test_a_read_only_link_raw_download_duplicate_and_report(
    page: Page, browser: Browser, receipts_server: ReceiptsServer
):
    receipts_server.shell(CLEANUP)
    receipts_server.shell(SEED)
    visitor_context = browser.new_context()
    try:
        receipts_server.login(page)
        _open_note(page, receipts_server)
        address = _make_link(page, editable=False, label='read only')

        visitor = visitor_context.new_page()
        visitor.goto(address)
        expect(visitor.locator('a:has-text("Edit")')).to_have_count(0)
        expect(visitor.locator('#note-body input.task-list-item-checkbox').first).to_be_disabled()

        raw = visitor.request.get(f'{address}/raw')
        assert raw.status == 200 and raw.headers['content-type'].startswith('text/plain')
        assert raw.text() == ORIGINAL
        assert raw.headers['referrer-policy'] == 'strict-origin'
        download = visitor.request.get(f'{address}/download')
        assert 'attachment; filename="zn-shared-plan.md"' in download.headers['content-disposition']
        assert visitor.request.get(f'{address}/edit').status == 404  # a read link has no editor

        # Duplicate asks a visitor without an account to sign in, and brings them back.
        visitor.click('button:has-text("Duplicate")')
        visitor.wait_for_url(re.compile(r'/account/login/\?next='))
        assert f'next=/n/{address.rsplit("/", 1)[1]}' in visitor.url

        # Report: the form is collapsed, needs no login.
        visitor.goto(address)
        visitor.click('button:has-text("Report this note")')
        visitor.select_option('#report-box select[name="reason"]', 'spam')
        visitor.fill('#report-box textarea[name="details"]', 'looks like advertising')
        visitor.click('#report-box button[type="submit"]')
        expect(visitor.get_by_text('Thank you, your report has been submitted.')).to_be_visible()
    finally:
        visitor_context.close()
        receipts_server.shell(CLEANUP)
