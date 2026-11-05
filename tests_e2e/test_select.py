"""The site's themed select (`core/components/select.html`), end to end in a real browser, on the
account page's date and time format dropdowns: opens a styled list (not the operating system's), picks
with the mouse and the keyboard, and saves on choosing.
"""

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

RESET = (
    'import os\n'
    'from apps.accounts.models import User\n'
    "User.objects.filter(email=os.environ['E2E_EMAIL']).update(date_format='mdy', time_format='12h')\n"
)


def test_the_dropdown_opens_and_closes(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(RESET)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/account/')

    button = page.get_by_label('Date format')
    expect(button).to_have_attribute('aria-expanded', 'false')
    expect(page.locator('#formats-form [role="listbox"]').first).to_be_hidden()

    button.click()
    expect(button).to_have_attribute('aria-expanded', 'true')
    listbox = page.locator('#formats-form [role="listbox"]').first
    expect(listbox).to_be_visible()
    expect(listbox.get_by_role('option')).to_have_count(2)
    expect(listbox.get_by_role('option', name='mm/dd/yyyy')).to_have_attribute(
        'aria-selected', 'true'
    )

    page.keyboard.press('Escape')
    expect(listbox).to_be_hidden()
    expect(button).to_be_focused()

    button.click()
    page.get_by_role('heading', name='Account Settings').click()  # outside closes it
    expect(listbox).to_be_hidden()


def test_choosing_with_the_keyboard_saves_once(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(RESET)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')

        button = page.get_by_label('Time format')
        button.focus()
        page.keyboard.press('ArrowDown')  # opens on the saved choice
        page.keyboard.press('ArrowDown')  # moves to 24-hour; nothing is saved yet
        expect(page.get_by_role('option', name='24-hour')).to_be_visible()
        expect(page.locator('input[name="time_format"]')).to_have_value('12h')
        with page.expect_response(lambda r: r.url.endswith('/account/formats/')) as saved:
            page.keyboard.press('Enter')
        assert saved.value.status == 200
        expect(button).to_contain_text('24-hour')
        expect(button).to_be_focused()
        expect(page.locator('input[name="time_format"]')).to_have_value('24h')

        page.reload()
        expect(page.get_by_label('Time format')).to_contain_text('24-hour')
    finally:
        receipts_server.shell(RESET)
