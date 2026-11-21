"""The task page's details save themselves, in a real browser.

There is no Save button: a change of the priority, the date or the time is saved at once, and the
labels and repeat popovers save when they close (not on every box ticked inside, so the popover stays
open while choosing). A save swaps in only the parts of the panel it touched, so an editor open
elsewhere stays open and the status line ("Saved") is the same element throughout. Run on the shared
e2e server (`receipts_server`); the tasks and the label are made straight in the database and removed
after.

"Nothing was sent" is checked after a short wait (`QUIET_MS`): the script sends a change a tick after
it (a 0 ms timer), so 400 ms is plenty; a longer delay in the script would need a longer wait here.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer
from tests_e2e.todo_fakes import DATED_TITLE

NUMBER = 94101
NO_DATE = 94102
QUIET_MS = 400
SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Label, Task\n'
    'from apps.todo.services import get_account\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='Autosave ').delete()\n"
    "Label.objects.filter(user=user, name='autosavelabel').delete()\n"
    f"Task.objects.create(user=user, number={NUMBER}, title='Autosave details')\n"
    f"Task.objects.create(user=user, number={NO_DATE}, title='Autosave no date')\n"
    "Label.objects.create(user=user, name='autosavelabel', color='#336699')\n"
    'account = get_account(user)\n'
    'account.default_reminder_minutes = 5\n'
    "account.save(update_fields=['default_reminder_minutes'])\n"
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Label, Task\n'
    "email = os.environ['E2E_EMAIL']\n"
    "Task.objects.filter(user__email=email, title__startswith='Autosave ').delete()\n"
    "Label.objects.filter(user__email=email, name='autosavelabel').delete()\n"
)


def _saves(number: int):
    def is_save(response) -> bool:
        return f'/todo/{number}/edit/' in response.url and response.request.method == 'POST'

    return is_save


def _record_saves(page: Page, number: int) -> list[str]:
    sent: list[str] = []
    page.on('request', lambda r: sent.append(r.url) if f'/todo/{number}/edit/' in r.url else None)
    return sent


def test_the_details_save_themselves(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SETUP)
    url = f'{receipts_server.url}/todo/{NUMBER}/'
    is_save = _saves(NUMBER)
    try:
        receipts_server.login(page)
        page.goto(url)
        form = page.locator('#task-details-form')
        status = page.locator('#details-save-status')
        expect(form.locator('button[type="submit"]')).to_have_count(0)
        sent = _record_saves(page, NUMBER)
        page.evaluate("() => { window.__status = document.getElementById('details-save-status'); }")

        # A select saves on change, and says so in the same live region (it is never replaced).
        with page.expect_response(is_save):
            page.select_option(f'#priority-{NUMBER}', '2')
        expect(status).to_have_text('Saved')
        assert page.evaluate(
            "() => document.getElementById('details-save-status') === window.__status"
        )

        # The date saves once it is read (on Tab), then the time.
        with page.expect_response(is_save):
            page.locator(f'#due-date-{NUMBER}').fill('2030-12-24')
            page.locator(f'#due-date-{NUMBER}').press('Tab')
        expect(page.locator('#task-reminders')).to_contain_text('before')  # the default reminder
        with page.expect_response(is_save):
            page.locator(f'#due-time-{NUMBER}').fill('9am')
            page.locator(f'#due-time-{NUMBER}').press('Tab')
        expect(status).to_have_text('Saved')

        # Typing takes an earlier "Saved" away (it would sit next to a typo).
        page.locator(f'#due-time-{NUMBER}').fill('banana')
        expect(status).to_have_text('')
        page.locator(f'#due-time-{NUMBER}').fill('9:00 AM')
        page.locator(f'#due-time-{NUMBER}').press('Tab')

        # Ticking a label sends nothing while the popover is open; closing it saves. Closing it by
        # opening the description's editor saves too, and the editor stays open.
        sent.clear()
        form.get_by_role('button', name='Add labels').click()
        form.locator('input[name="labels"]').first.check()
        page.wait_for_timeout(QUIET_MS)
        assert sent == []
        with page.expect_response(is_save):
            page.get_by_role('button', name='Edit', exact=True).click()
        page.wait_for_timeout(300)  # the parts of the answer are swapped in
        expect(page.locator('#description-text')).to_be_visible()
        expect(form.get_by_role('button', name='1 selected')).to_be_visible()
        page.locator('#description-text').press('Escape')

        # The repeat popover saves on Done, not on each control.
        sent.clear()
        form.get_by_role('button', name=re.compile('^Repeat')).click()
        form.get_by_label('Frequency').select_option('week')
        page.wait_for_timeout(QUIET_MS)
        assert sent == []
        with page.expect_response(is_save):
            form.get_by_role('button', name='Done').click()
        expect(page.locator('#task-repeat')).to_contain_text('Every week')

        # An AI date chip in the "Or type a date" box is not accepted by saving another field.
        words = page.get_by_label('Or type a date')
        expect(page.locator('[data-title-parse]:has([data-parse-input])')).to_have_attribute(
            'data-parse-ready', '1'
        )
        words.press_sequentially(DATED_TITLE)
        chip = page.locator('#details-parse-chip')
        expect(chip).to_be_visible()
        with page.expect_response(is_save):
            page.select_option(f'#priority-{NUMBER}', '1')
        expect(form.locator('input[name="due_date"]')).to_have_value('2030-12-24')
        expect(chip).to_be_visible()
        chip.get_by_role('button', name='Dismiss').click()
        words.fill('')

        # Text typed into a field that a save replaces, while the save is on its way, is kept.
        def slow(route):
            page.wait_for_timeout(600)
            route.continue_()

        page.route(f'**/todo/{NUMBER}/edit/', slow)
        time_box = page.locator(f'#due-time-{NUMBER}')
        with page.expect_response(is_save):
            page.locator(f'#due-date-{NUMBER}').fill('2030-12-25')
            page.locator(f'#due-date-{NUMBER}').press('Enter')
            time_box.click()
            time_box.fill('')
            time_box.press_sequentially('10')
        page.wait_for_timeout(300)
        expect(page.locator(f'#due-time-{NUMBER}')).to_have_value('10')
        expect(page.locator(f'#due-time-{NUMBER}')).to_be_focused()
        page.unroute(f'**/todo/{NUMBER}/edit/')
        with page.expect_response(is_save):
            page.locator(f'#due-time-{NUMBER}').fill('9:30 AM')
            page.locator(f'#due-time-{NUMBER}').press('Tab')

        # Clearing the date asks first (it has a reminder); saying no puts the date back.
        sent.clear()
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.locator(f'#due-date-{NUMBER}').fill('')
        page.locator(f'#due-date-{NUMBER}').press('Tab')
        page.wait_for_timeout(QUIET_MS)
        assert sent == []
        expect(form.locator('input[name="due_date"]')).to_have_value('2030-12-25')
        expect(page.locator(f'#due-date-{NUMBER}')).not_to_have_value('')

        # Everything is there after a reload.
        page.reload()
        form = page.locator('#task-details-form')
        expect(page.locator(f'#priority-{NUMBER}')).to_have_value('1')
        expect(form.locator('input[name="due_date"]')).to_have_value('2030-12-25')
        expect(form.locator('input[name="due_time"]')).to_have_value('09:30')
        expect(form.locator('input[name="labels"]').first).to_be_checked()
        expect(form.locator('input[name="rrule"]')).to_have_value(re.compile('FREQ=WEEKLY'))

        # Setting something to the value it already has sends nothing.
        sent.clear()
        page.select_option(f'#priority-{NUMBER}', '1')
        page.wait_for_timeout(QUIET_MS)
        assert sent == []

        # A change not sent yet (the labels popover still open) is sent when the page is left.
        form.get_by_role('button', name='1 selected').click()
        form.locator('input[name="labels"]').first.uncheck()
        page.goto(f'{receipts_server.url}/todo/')
        labels = page.locator('#task-details-form input[name="labels"]').first
        for _ in range(10):
            page.goto(url)
            if not labels.is_checked():
                break
            page.wait_for_timeout(300)
        expect(labels).not_to_be_checked()
    finally:
        receipts_server.shell(CLEANUP)


def test_a_time_waits_for_a_date(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SETUP)
    is_save = _saves(NO_DATE)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/{NO_DATE}/')
        sent = _record_saves(page, NO_DATE)

        page.locator(f'#due-time-{NO_DATE}').fill('3pm')
        page.locator(f'#due-time-{NO_DATE}').press('Tab')
        page.wait_for_timeout(QUIET_MS)
        assert sent == []
        status = page.locator('#details-save-status')
        expect(status).to_have_text('Choose a date to save the time.')

        with page.expect_response(is_save):
            page.locator(f'#due-date-{NO_DATE}').fill('2030-12-24')
            page.locator(f'#due-date-{NO_DATE}').press('Tab')
        expect(status).to_have_text('Saved')
        page.reload()
        form = page.locator('#task-details-form')
        expect(form.locator('input[name="due_date"]')).to_have_value('2030-12-24')
        expect(form.locator('input[name="due_time"]')).to_have_value('15:00')
    finally:
        receipts_server.shell(CLEANUP)
