"""To-do: reading a date out of the title (issue #183), end to end in a real browser.

The server is `receipts_server` (see `receipts_e2e_settings.py`): the real site, with the AI
swapped for a fake that understands one title (`todo_fakes.DATED_TITLE`), so no model is called.
"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer
from tests_e2e.todo_fakes import (
    DATE_ONLY_TITLE,
    DATED_TITLE,
    RELATIVE_TITLE,
    RULE,
    TIME_ONLY_TITLE,
)


def _wired(page: Page, field: str) -> None:
    """Wait until `title_parse.js` has wired the box around `field` (a selector): keys typed before
    that fire no listener, and the chip would never come (the script is deferred and wires from
    `htmx.onLoad`, so a click that navigates can leave the input usable a moment too early)."""
    box = page.locator(f'[data-title-parse]:has({field})')
    expect(box).to_have_attribute('data-parse-ready', '1')


def _row_ids(page: Page) -> set[str]:
    return set(page.locator('#task-list li[id^="task-"]').evaluate_all('els => els.map(e => e.id)'))


def _submit_and_open_new_task(page: Page, receipts_server: ReceiptsServer) -> None:
    """Click Add task and open the task that appeared. The e2e database is shared by the tests of
    the module, which create tasks with the same titles, so the new row is found by its number."""
    before = _row_ids(page)
    page.click('#quick-add-form button[type="submit"]')
    expect(page.locator('#task-list li[id^="task-"]')).not_to_have_count(len(before))
    (new_id,) = _row_ids(page) - before
    page.goto(f'{receipts_server.url}/todo/{new_id.removeprefix("task-")}/')


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
    expect(chip).to_contain_text('9:00 AM')
    expect(chip).to_contain_text('Every month on the 1st')
    expect(chip).to_have_attribute('title', 'every month on the 1st at 9am')
    # Nothing is applied until it is accepted.
    expect(page.locator('#due-date-new')).to_have_value('')

    chip.get_by_role('button', name='Accept').click()

    expect(chip).to_be_hidden()
    expect(page.locator('#quick-title')).to_have_value('pay rent')
    # The fields submit ISO and show the user's wording (a new account is month first, 12-hour).
    expect(page.locator('#quick-add-form input[name="due_date"]')).to_have_value(
        re.compile(r'^\d{4}-\d{2}-01$')
    )
    expect(page.locator('#due-date-new')).to_have_value(re.compile(r'^\d{2}/01/\d{4}$'))
    expect(page.locator('#quick-add-form input[name="due_time"]')).to_have_value('09:00')
    expect(page.locator('#due-time-new')).to_have_value('9:00 AM')
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


def test_submitting_with_the_chip_showing_applies_it(page: Page, receipts_server: ReceiptsServer):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')

    _type_title(page, DATED_TITLE)
    expect(page.locator('#quick-parse-chip')).to_be_visible()
    # No click on Accept: submitting accepts the chip, so the task gets the date, time and repeat
    # and the cleaned title.
    _submit_and_open_new_task(page, receipts_server)

    expect(page.locator('h1')).to_contain_text('pay rent')
    expect(page.locator('h1')).not_to_contain_text('every month')
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value(
        re.compile(r'^\d{4}-\d{2}-01$')
    )
    expect(page.locator('#task-details-form input[name="due_time"]')).to_have_value('09:00')
    expect(page.locator('main')).to_contain_text('Every month on the 1st')


def test_submitting_after_dismissing_the_chip_sends_the_title_as_typed(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')

    _type_title(page, DATED_TITLE)
    chip = page.locator('#quick-parse-chip')
    expect(chip).to_be_visible()
    chip.get_by_role('button', name='Dismiss').click()
    _submit_and_open_new_task(page, receipts_server)

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
    # "Tomorrow" in the account's zone (the browser's, saved on the first sign-in; UTC if none yet).
    zone = ZoneInfo(page.locator('html').get_attribute('data-user-timezone') or 'UTC')
    tomorrow = datetime.now(zone).date() + timedelta(days=1)
    expect(form.locator('input[name="due_date"]')).to_have_value(tomorrow.isoformat())


def _zone(page: Page) -> ZoneInfo:
    """The account's zone (the browser's, saved on the first sign-in; UTC if none yet)."""
    return ZoneInfo(page.locator('html').get_attribute('data-user-timezone') or 'UTC')


def _accepted_moment(page: Page) -> datetime:
    form = page.locator('#quick-add-form')
    day = date.fromisoformat(form.locator('input[name="due_date"]').input_value())
    at = time.fromisoformat(form.locator('input[name="due_time"]').input_value())
    return datetime.combine(day, at)


def test_a_relative_time_is_counted_from_now(page: Page, receipts_server: ReceiptsServer):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    zone = _zone(page)

    before = datetime.now(zone).replace(second=0, microsecond=0, tzinfo=None)
    _type_title(page, RELATIVE_TITLE)
    chip = page.locator('#quick-parse-chip')
    expect(chip).to_be_visible()
    expect(chip).to_have_attribute('title', 'in 2 hours')
    chip.get_by_role('button', name='Accept').click()
    after = datetime.now(zone).replace(second=0, microsecond=0, tzinfo=None)

    expect(page.locator('#quick-title')).to_have_value('call dad')
    # Two hours from now, to the minute (the clock may tick over while the test runs); a clock
    # change within two hours would make the wall-clock sum differ, which the zones in use here
    # don't have.
    moment = _accepted_moment(page)
    assert before + timedelta(hours=2) <= moment <= after + timedelta(hours=2, minutes=1)


def test_a_time_with_no_day_is_today_unless_it_has_passed(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    zone = _zone(page)

    _type_title(page, TIME_ONLY_TITLE)
    chip = page.locator('#quick-parse-chip')
    expect(chip).to_be_visible()
    now = datetime.now(zone)
    expected_day = now.date() if now.time() <= time(10, 0) else now.date() + timedelta(days=1)
    expect(chip).to_contain_text('Today, 10:00 AM' if expected_day == now.date() else 'Tomorrow')
    chip.get_by_role('button', name='Accept').click()

    expect(page.locator('#quick-title')).to_have_value('call dad')
    expect(page.locator('#quick-add-form input[name="due_time"]')).to_have_value('10:00')
    # (If the minute rolled over 10:00 while the test ran, the other answer is the right one.)
    actual = date.fromisoformat(
        page.locator('#quick-add-form input[name="due_date"]').input_value()
    )
    assert actual in {expected_day, datetime.now(zone).date() + timedelta(days=1)}


def test_a_chip_that_waited_moves_a_relative_time_on(page: Page, receipts_server: ReceiptsServer):
    """The reading says "2 hours from when it was read": Accept shifts it by the minutes the chip
    sat there (the browser's clock is moved on 10 minutes, no real wait)."""
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    _wired(page, '#quick-title')
    page.clock.install()
    zone = _zone(page)

    before = datetime.now(zone).replace(second=0, microsecond=0, tzinfo=None)
    page.locator('#quick-title').press_sequentially(RELATIVE_TITLE)
    page.clock.run_for(700)  # the script's pause after the last key
    chip = page.locator('#quick-parse-chip')
    expect(chip).to_be_visible()
    page.clock.fast_forward('10:00')  # ten minutes pass in the page, not on the server
    chip.get_by_role('button', name='Accept').click()
    after = datetime.now(zone).replace(second=0, microsecond=0, tzinfo=None)

    moment = _accepted_moment(page)
    assert (
        before + timedelta(hours=2, minutes=10) <= moment <= after + timedelta(hours=2, minutes=11)
    )
