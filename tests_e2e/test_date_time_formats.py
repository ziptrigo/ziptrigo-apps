"""The date and time format a user picks (issue #245), end to end in a real browser: the account
page's radios, the date field and the time field that replace the native inputs, the public pages and
the AI chip. Run on the shared e2e server (`receipts_server`).
"""

import re
import subprocess
import sys
from datetime import date

import pytest
from playwright.sync_api import Browser, Page, expect

from tests_e2e.conftest import ReceiptsServer
from tests_e2e.todo_fakes import NUMERIC_TITLE

YEAR = date.today().year
NEXT_YEAR = YEAR + 1

PROLOGUE = (
    'import os\n'
    'from datetime import UTC, date, datetime, time, timedelta\n'
    'from apps.accounts.models import User\n'
    'from apps.accounts.services.timezones import user_today\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
)


def run(server: ReceiptsServer, code: str) -> str:
    """Run `code` in a Django shell against the server's database; its stdout."""
    result = subprocess.run(
        [sys.executable, 'manage.py', 'shell', '-c', PROLOGUE + code],
        env=server._env,
        check=True,
        capture_output=True,
        text=True,
    )
    # Django's shell announces the objects it imported before anything the code prints.
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ''


def set_formats(server: ReceiptsServer, date_format: str, time_format: str) -> None:
    run(
        server,
        f'User.objects.filter(pk=user.pk).update(date_format={date_format!r},'
        f' time_format={time_format!r})\n',
    )


def make_task(
    server: ReceiptsServer,
    title: str,
    *,
    days: int = 0,
    year: int | None = None,
    at: str | None = '22:00',
) -> str:
    """A task due `days` from the user's today (or on 5 November of `year`), at `at`; its number."""
    due = 'user_today(user) + timedelta(days=%d)' % days if year is None else f'date({year}, 11, 5)'
    when = f'time.fromisoformat({at!r})' if at else 'None'
    return run(
        server,
        'from apps.todo.services import tasks\n'
        f'task = tasks.create_task(user, title={title!r}, due_date={due}, due_time={when})\n'
        'print(task.number)\n',
    )


@pytest.fixture(autouse=True)
def _free_tasks_and_default_formats(receipts_server: ReceiptsServer):
    """Tasks cost nothing here (the account is shared with the rest of the suite and its credits are
    few), and every test starts and ends on the defaults."""
    run(
        receipts_server,
        'from apps.todo.models import TodoSettings\n'
        'settings = TodoSettings.load()\n'
        'settings.task_price = 0\n'
        'settings.save()\n',
    )
    # Pin the account's time zone: otherwise the first sign-in saves the browser's, and "today"
    # moves under the tasks these tests create from the shell.
    run(receipts_server, "User.objects.filter(pk=user.pk).update(timezone='UTC')\n")
    set_formats(receipts_server, 'mdy', '12h')
    yield
    set_formats(receipts_server, 'mdy', '12h')
    run(receipts_server, "User.objects.filter(pk=user.pk).update(timezone='')\n")


def save_details(page: Page, number: str) -> None:
    """Press Save on the task page's details form and wait until the panel was swapped."""
    with page.expect_response(lambda r: f'/todo/{number}/edit/' in r.url):
        page.click('#task-details-form button[type="submit"]')
    page.wait_for_timeout(300)  # htmx settles the swapped-in panel


# -- 1. Setting a format -----------------------------------------------------------------------


def test_the_account_page_sets_the_format_and_every_todo_date_follows(
    page: Page, receipts_server: ReceiptsServer
):
    today_task = make_task(receipts_server, 'format today task')
    next_year_task = make_task(receipts_server, 'format next year task', year=NEXT_YEAR)
    receipts_server.login(page)

    page.goto(f'{receipts_server.url}/account/')
    form = page.locator('#formats-form')
    expect(form.locator('input[value="mdy"]')).to_be_checked()
    expect(form.locator('input[value="12h"]')).to_be_checked()

    with page.expect_response(lambda r: r.url.endswith('/account/formats/')):
        form.locator('label', has_text='dd/mm/yyyy').click()
    with page.expect_response(lambda r: r.url.endswith('/account/formats/')):
        form.locator('label', has_text='24-hour').click()

    page.reload()
    expect(page.locator('#formats-form input[value="dmy"]')).to_be_checked()
    expect(page.locator('#formats-form input[value="24h"]')).to_be_checked()

    page.goto(f'{receipts_server.url}/todo/all/')
    expect(page.locator(f'#task-{today_task}')).to_contain_text('Today 22:00')
    expect(page.locator(f'#task-{next_year_task}')).to_contain_text(f'05/11/{NEXT_YEAR}, 22:00')

    page.goto(f'{receipts_server.url}/account/')
    with page.expect_response(lambda r: r.url.endswith('/account/formats/')):
        page.locator('#formats-form label', has_text='mm/dd/yyyy').click()
    with page.expect_response(lambda r: r.url.endswith('/account/formats/')):
        page.locator('#formats-form label', has_text='12-hour').click()

    page.goto(f'{receipts_server.url}/todo/all/')
    expect(page.locator(f'#task-{today_task}')).to_contain_text('Today 10:00 PM')
    expect(page.locator(f'#task-{next_year_task}')).to_contain_text(f'11/05/{NEXT_YEAR}, 10:00 PM')


# -- 2. Typing a date ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('date_format', 'typed', 'shown'),
    [('dmy', '05/11/2026', '05/11/2026'), ('mdy', '11/05/2026', '11/05/2026')],
)
def test_a_typed_date_is_read_in_the_users_order_and_a_bad_one_changes_nothing(
    page: Page, receipts_server: ReceiptsServer, date_format, typed, shown
):
    set_formats(receipts_server, date_format, '12h')
    number = make_task(receipts_server, f'typed date {date_format}', days=3, at=None)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/{number}/')
    box = page.locator(f'#due-date-{number}')
    hidden = page.locator('#task-details-form input[name="due_date"]')

    box.fill(typed)
    box.press('Tab')
    expect(hidden).to_have_value('2026-11-05')
    save_details(page, number)
    expect(page.locator(f'#due-date-{number}')).to_have_value(shown)
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value('2026-11-05')

    # ISO is accepted whatever the order.
    box = page.locator(f'#due-date-{number}')
    box.fill('2026-11-06')
    box.press('Tab')
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value('2026-11-06')
    save_details(page, number)
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value('2026-11-06')

    # A date that doesn't exist is refused under the box and leaves the saved date alone.
    box = page.locator(f'#due-date-{number}')
    box.fill('31/02/2026' if date_format == 'dmy' else '02/31/2026')
    box.press('Tab')
    expect(box).to_have_attribute('aria-invalid', 'true')
    expect(
        page.locator(
            '#task-details-form [role="status"], #task-details-form p:visible',
            has_text='Enter a date as',
        )
    ).to_be_visible()
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value('2026-11-06')
    save_details(page, number)
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value('2026-11-06')


# -- 3. The calendar -----------------------------------------------------------------------------


def test_the_calendar_picks_a_day_and_postpone_greys_out_the_past(
    page: Page, receipts_server: ReceiptsServer
):
    set_formats(receipts_server, 'dmy', '24h')
    number = make_task(receipts_server, 'calendar task', days=1, at=None)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/{number}/')

    field = page.locator(f'#due-date-{number}').locator('xpath=ancestor::div[@data-field-wrap][1]')
    field.get_by_role('button', name='Choose a date').click()
    # Pick the 15th of the month shown: the box shows it in the user's order.
    calendar = field.get_by_role('dialog')
    expect(calendar).to_be_visible()
    day_15 = calendar.locator('button[data-date$="-15"]')
    picked = day_15.get_attribute('data-date')
    assert picked is not None
    day_15.click()
    year, month, day = picked.split('-')
    expect(page.locator(f'#due-date-{number}')).to_have_value(f'{day}/{month}/{year}')
    expect(page.locator('#task-details-form input[name="due_date"]')).to_have_value(picked)

    # Postpone's date can't be earlier than today.
    page.goto(f'{receipts_server.url}/todo/all/')
    row = page.locator(f'#task-{number}')
    row.get_by_role('button', name=re.compile('^Postpone')).click()
    menu_field = row.locator('[data-field-wrap]').first
    menu_field.get_by_role('button', name='Choose a date').click()
    menu_field.get_by_role('button', name='Previous month').click()
    expect(menu_field.locator('button[data-date]:not([disabled])')).to_have_count(0)


# -- 4. The time field -----------------------------------------------------------------------------


@pytest.mark.parametrize(('time_format', 'shown'), [('12h', '10:00 PM'), ('24h', '22:00')])
def test_a_typed_time_shows_on_the_users_clock_and_saves_as_hh_mm(
    page: Page, receipts_server: ReceiptsServer, time_format, shown
):
    set_formats(receipts_server, 'mdy', time_format)
    number = make_task(receipts_server, f'typed time {time_format}', days=2, at=None)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/{number}/')
    box = page.locator(f'#due-time-{number}')

    box.fill('10pm')
    box.press('Tab')
    expect(box).to_have_value(shown)
    expect(page.locator('#task-details-form input[name="due_time"]')).to_have_value('22:00')
    save_details(page, number)
    expect(page.locator(f'#due-time-{number}')).to_have_value(shown)
    expect(page.locator('#task-details-form input[name="due_time"]')).to_have_value('22:00')


def test_the_time_suggestions_work_from_the_keyboard_and_nonsense_is_refused(
    page: Page, receipts_server: ReceiptsServer
):
    number = make_task(receipts_server, 'suggestions task', days=2, at=None)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/{number}/')
    box = page.locator(f'#due-time-{number}')
    listbox = page.locator('[role="listbox"][aria-label="Suggested times"]').first

    box.click()
    expect(listbox).to_be_visible()
    box.press('Escape')
    expect(listbox).to_be_hidden()

    # ArrowDown opens it, then one step per half hour from 12:00 AM: 44 steps reach 9:30 PM.
    box.press('ArrowDown')
    expect(listbox).to_be_visible()
    for _ in range(44):
        box.press('ArrowDown')
    expect(listbox.locator('[role="option"][aria-selected="false"]').nth(43)).to_have_text(
        '9:30 PM'
    )
    box.press('Enter')
    expect(box).to_have_value('9:30 PM')
    expect(page.locator('#task-details-form input[name="due_time"]')).to_have_value('21:30')

    box.fill('banana')
    box.press('Tab')
    expect(box).to_have_attribute('aria-invalid', 'true')
    expect(page.get_by_text('Enter a time like 9:00 PM.')).to_be_visible()
    expect(page.locator('#task-details-form input[name="due_time"]')).to_have_value('21:30')


# -- 5. htmx forms keep working with the new fields -------------------------------------------------


def test_the_receipts_date_range_refreshes_the_list_with_iso_dates(
    page: Page, receipts_server: ReceiptsServer
):
    set_formats(receipts_server, 'dmy', '12h')
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/receipts/')
    page.select_option('#date_range', 'custom')

    with page.expect_response(
        lambda r: '/receipts/list/' in r.url and 'date_from=2025-06-01' in r.url
    ):
        page.locator('#date_from').fill('01/06/2025')
        page.locator('#date_from').press('Tab')
    with page.expect_response(
        lambda r: '/receipts/list/' in r.url and 'date_to=2025-07-31' in r.url
    ):
        page.locator('#date_to').fill('31/07/2025')
        page.locator('#date_to').press('Tab')
    expect(page).to_have_url(re.compile(r'date_from=2025-06-01&date_to=2025-07-31'))


def test_the_todo_search_dates_are_disabled_until_custom_and_search_when_filled(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/search/?q=anything')
    from_box = page.locator('#search-date-from')
    hidden = page.locator('#search-form input[name="date_from"]')

    expect(from_box).to_be_disabled()
    expect(hidden).to_be_disabled()
    page.select_option('#search-date-range', 'custom')
    expect(from_box).to_be_enabled()
    expect(hidden).to_be_enabled()

    with page.expect_response(
        lambda r: '/todo/search/list/' in r.url and 'date_from=2026-01-15' in r.url
    ):
        from_box.fill('01/15/2026')
        from_box.press('Tab')
    expect(page).to_have_url(re.compile(r'date_from=2026-01-15'))


# -- 6. Short URL expiry -----------------------------------------------------------------------------


def test_a_short_link_expiry_is_a_date_and_a_time_in_the_browsers_zone(
    browser: Browser, receipts_server: ReceiptsServer
):
    set_formats(receipts_server, 'dmy', '24h')
    context = browser.new_context(timezone_id='Asia/Tokyo')
    page = context.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/links/new/')
        page.fill('#id_destination', 'https://destination.example/expiry')
        page.fill('#id_title', 'Expiry link')
        page.locator('#id_expires_date').fill('31/12/2030')
        page.locator('#id_expires_date').press('Tab')
        page.locator('#id_expires_time').fill('18:30')
        page.locator('#id_expires_time').press('Tab')
        # 18:30 in Tokyo (UTC+9) is 09:30 UTC.
        expect(page.locator('#id_expires_at_utc')).to_have_value('2030-12-31T09:30:00.000Z')
        page.click('button:has-text("Create link")')
        expect(page.locator('body')).to_contain_text('Link created.')
        expect(page.locator('main')).to_contain_text('31/12/2030, 18:30')

        # The edit form splits the stored instant back into the browser's local date and time.
        page.goto(page.url.rstrip('/') + '/edit/')
        expect(page.locator('#id_expires_date')).to_have_value('31/12/2030')
        expect(page.locator('#id_expires_time')).to_have_value('18:30')
        # The submitted UTC value is the server's, until a box changes.
        expect(page.locator('#id_expires_at_utc')).to_have_value('2030-12-31T09:30Z')
    finally:
        context.close()


# -- 7. Public pages -----------------------------------------------------------------------------------


def test_a_public_page_is_the_same_for_a_signed_in_owner_and_a_visitor(
    browser: Browser, receipts_server: ReceiptsServer
):
    set_formats(receipts_server, 'dmy', '24h')
    slug = run(
        receipts_server,
        'from apps.file_transfer.models import Transfer, TransferStatus\n'
        'transfer = Transfer.objects.create(owner=user, status=TransferStatus.ACTIVE,'
        ' expires_at=datetime(2030, 11, 5, 21, 5, tzinfo=UTC))\n'
        'print(transfer.slug)\n',
    )
    url = f'{receipts_server.url}/t/{slug}/'

    owner_context = browser.new_context(timezone_id='UTC')
    owner = owner_context.new_page()
    visitor_context = browser.new_context(timezone_id='UTC')
    visitor = visitor_context.new_page()
    try:
        receipts_server.login(owner)
        owner.goto(url)
        visitor.goto(url)
        for page in (owner, visitor):
            expect(page.locator('body')).to_contain_text('Nov 5, 2030, 9:05 PM')
            expect(page.locator('html')).to_have_attribute('data-date-format', 'public')
    finally:
        owner_context.close()
        visitor_context.close()


# -- 8. Field errors -------------------------------------------------------------------------------------


def test_a_refused_save_puts_its_message_under_the_new_date_field(
    page: Page, receipts_server: ReceiptsServer
):
    number = make_task(receipts_server, 'refused date task', days=1, at=None)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/{number}/')

    # A value the box would never produce, as a forged request would send it.
    page.evaluate(
        """() => { const input = document.querySelector('#task-details-form input[name="due_date"]');
                   input.value = 'not-a-date'; }"""
    )
    page.click('#task-details-form button[type="submit"]')

    box = page.locator(f'#due-date-{number}')
    expect(box).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#task-details-form [data-field-error-message]').first).to_be_visible()


# -- 9. The AI chip ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('date_format', 'iso'), [('dmy', f'{NEXT_YEAR}-11-05'), ('mdy', f'{NEXT_YEAR}-05-11')]
)
def test_the_chip_reads_a_numeric_date_in_the_users_order(
    page: Page, receipts_server: ReceiptsServer, date_format, iso
):
    set_formats(receipts_server, date_format, '12h')
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/all/')
    expect(page.locator('[data-title-parse]:has(#quick-title)')).to_have_attribute(
        'data-parse-ready', '1'
    )
    page.locator('#quick-title').press_sequentially(NUMERIC_TITLE)

    chip = page.locator('#quick-parse-chip')
    # Both orders write it 05/11: May 11 month first, 5 November day first.
    expect(chip).to_contain_text(f'05/11/{NEXT_YEAR}')
    chip.get_by_role('button', name='Accept').click()

    expect(page.locator('#quick-add-form input[name="due_date"]')).to_have_value(iso)
    expect(page.locator('#due-date-new')).to_have_value(f'05/11/{NEXT_YEAR}')
    expect(page.locator('#quick-title')).to_have_value('pay rent')


# -- 10. {% local_time %} --------------------------------------------------------------------------------------


@pytest.mark.parametrize(('date_format', 'time_format'), [('mdy', '12h'), ('dmy', '24h')])
def test_a_comment_time_is_in_the_users_format_and_the_browsers_zone(
    browser: Browser, receipts_server: ReceiptsServer, date_format, time_format
):
    set_formats(receipts_server, date_format, time_format)
    number = make_task(receipts_server, f'comment time {date_format}', days=1, at=None)
    context = browser.new_context(timezone_id='Asia/Tokyo')
    page = context.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/{number}/')
        page.fill('#task-comments textarea[name="body"]', 'a comment')
        page.click('#task-comments form[hx-post$="/comments/"] button[type="submit"]')
        stamp = page.locator('#task-comments time[data-local-time]').first
        expect(stamp).to_be_visible()
        # The script rewrites the tooltip once htmx has put the comment on the page.
        expect(stamp).to_have_attribute('title', re.compile(r'^\d{2}/\d{2}/\d{4}, [^U]*$'))
        title = stamp.get_attribute('title') or ''
        # What the browser's own zone says the moment is, written by hand in the expected wording.
        parts = stamp.evaluate(
            """el => { const d = new Date(el.getAttribute('datetime'));
                       return [d.getFullYear(), d.getMonth() + 1, d.getDate(), d.getHours(), d.getMinutes()]; }"""
        )
        year, month, day, hour, minute = parts
        first, second = (day, month) if date_format == 'dmy' else (month, day)
        clock = (
            f'{hour:02d}:{minute:02d}'
            if time_format == '24h'
            else f'{hour % 12 or 12}:{minute:02d} {"AM" if hour < 12 else "PM"}'
        )
        assert title == f'{first:02d}/{second:02d}/{year}, {clock}'
    finally:
        context.close()
