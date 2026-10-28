"""To-do: reading a date out of the title (issue #183), end to end in a real browser.

The server is `receipts_server` (see `receipts_e2e_settings.py`): the real site, with the AI
swapped for a fake that understands one title (`todo_fakes.DATED_TITLE`), so no model is called.
"""

import re
from datetime import UTC, datetime, timedelta

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer
from tests_e2e.todo_fakes import DATE_ONLY_TITLE, DATED_TITLE, RULE


def _wired(page: Page, field: str) -> None:
    """Wait until `title_parse.js` has wired the box around `field` (a selector): keys typed before
    that fire no listener, and the chip would never come (the script is deferred and wires from
    `htmx.onLoad`, so a click that navigates can leave the input usable a moment too early)."""
    box = page.locator(f'[data-title-parse]:has({field})')
    expect(box).to_have_attribute('data-parse-ready', '1')


def _type_title(page: Page, text: str) -> None:
    # `press_sequentially` types key by key, like a person: the script's pause starts at the last.
    _wired(page, '#quick-title')
    field = page.locator('#quick-title')
    field.fill('')
    field.press_sequentially(text)


def test_accepting_the_chip_fills_the_fields_and_strips_the_title(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')

    _type_title(page, DATED_TITLE)

    chip = page.locator('#quick-parse-chip')
    expect(chip).to_be_visible()
    expect(chip).to_contain_text('09:00')
    expect(chip).to_contain_text('Every month on the 1st')
    expect(chip).to_have_attribute('title', 'every month on the 1st at 9am')
    # Nothing is applied until it is accepted.
    expect(page.locator('#due-date-new')).to_have_value('')

    chip.get_by_role('button', name='Accept').click()

    expect(chip).to_be_hidden()
    expect(page.locator('#quick-title')).to_have_value('pay rent')
    expect(page.locator('#due-date-new')).to_have_value(re.compile(r'^\d{4}-\d{2}-01$'))
    expect(page.locator('#due-time-new')).to_have_value('09:00')
    expect(page.locator('#quick-add-form input[name="rrule"]')).to_have_value(RULE)
    expect(page.locator('#quick-add-form')).to_contain_text('Every month on the 1st')

    # The form now posts the explicit fields: the task is created repeating, as the chip said.
    page.click('#quick-add-form button[type="submit"]')
    row = page.locator('#task-list').get_by_text('pay rent').first
    expect(row).to_be_visible()
    row.click()
    expect(page.locator('h1')).to_contain_text('pay rent')
    expect(page.locator('main')).to_contain_text('Every month on the 1st')


def test_dismissing_and_escape_leave_the_form_alone(page: Page, receipts_server: ReceiptsServer):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    chip = page.locator('#quick-parse-chip')

    _type_title(page, DATED_TITLE)
    expect(chip).to_be_visible()
    chip.get_by_role('button', name='Dismiss').click()
    expect(chip).to_be_hidden()
    expect(page.locator('#quick-title')).to_have_value(DATED_TITLE)
    expect(page.locator('#due-date-new')).to_have_value('')
    # The same text is not read again.
    page.wait_for_timeout(1000)
    expect(chip).to_be_hidden()

    # Escape on the chip dismisses it too (a fresh page: the dismissed text is remembered).
    page.reload()
    _type_title(page, DATED_TITLE)
    expect(chip).to_be_visible()
    chip.focus()
    page.keyboard.press('Escape')
    expect(chip).to_be_hidden()
    expect(page.locator('#quick-title')).to_have_value(DATED_TITLE)
    expect(page.locator('#due-date-new')).to_have_value('')


def test_submitting_with_the_chip_showing_does_not_apply_it(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')

    _type_title(page, DATED_TITLE)
    expect(page.locator('#quick-parse-chip')).to_be_visible()
    page.click('#quick-add-form button[type="submit"]')

    # The task has the title as typed, and no date.
    row = page.locator('#task-list').get_by_text(DATED_TITLE).first
    expect(row).to_be_visible()
    row.click()
    expect(page.locator('h1')).to_contain_text(DATED_TITLE)
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value('')


def test_the_task_page_date_box_applies_only_the_date_fields(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    page.fill('#quick-title', 'Plain task')
    page.click('#quick-add-form button[type="submit"]')
    page.locator('#task-list').get_by_text('Plain task').first.click()

    box = page.get_by_label('Or type a date')
    _wired(page, '[id^="date-words-"]')
    box.press_sequentially(DATED_TITLE)
    chip = page.locator('#details-parse-chip')
    expect(chip).to_be_visible()
    chip.get_by_role('button', name='Accept').click()

    form = page.locator('#task-details-form')
    expect(form.locator('input[name="due_date"]')).to_have_value(re.compile(r'^\d{4}-\d{2}-01$'))
    expect(form.locator('input[name="due_time"]')).to_have_value('09:00')
    expect(form.locator('input[name="rrule"]')).to_have_value(RULE)
    expect(box).to_have_value('')  # the words are not a title: nothing else changed
    expect(page.locator('h1')).to_contain_text('Plain task')


def test_the_chip_says_what_stays_when_the_text_has_no_time_or_repeat(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    page.fill('#quick-title', 'Plain task')
    page.click('#quick-add-form button[type="submit"]')
    page.locator('#task-list').get_by_text('Plain task').first.click()

    form = page.locator('#task-details-form')
    box = page.get_by_label('Or type a date')
    chip = page.locator('#details-parse-chip')
    _wired(page, '[id^="date-words-"]')

    # Nothing to keep yet: the chip is the date alone.
    box.press_sequentially(DATE_ONLY_TITLE)
    expect(chip).to_be_visible()
    expect(chip).not_to_contain_text('kept')
    box.fill('')

    # Put a time and a repeat on the form, then read a date that has neither.
    box.press_sequentially(DATED_TITLE)
    expect(chip).to_be_visible()
    chip.get_by_role('button', name='Accept').click()
    expect(form.locator('input[name="rrule"]')).to_have_value(RULE)
    box.press_sequentially(DATE_ONLY_TITLE)
    expect(chip).to_contain_text('Tomorrow (time and repeat kept)')
    chip.get_by_role('button', name='Accept').click()

    # The date moved; the time and the repeat are what the chip said they were.
    expect(form.locator('input[name="due_time"]')).to_have_value('09:00')
    expect(form.locator('input[name="rrule"]')).to_have_value(RULE)
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)  # the user's zone is UTC
    expect(form.locator('input[name="due_date"]')).to_have_value(tomorrow.isoformat())
