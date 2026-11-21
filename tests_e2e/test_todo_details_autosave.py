"""The task page's details save themselves, in a real browser.

There is no Save button: a change of the priority, the date or the time is saved at once, and the
labels and repeat popovers save when they close (not on every box ticked inside, so the popover stays
open while choosing). Run on the shared e2e server (`receipts_server`); the task and the label are made
straight in the database and removed after.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

NUMBER = 94101
SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Label, Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='Autosave ').delete()\n"
    "Label.objects.filter(user=user, name='autosavelabel').delete()\n"
    f"Task.objects.create(user=user, number={NUMBER}, title='Autosave details')\n"
    "Label.objects.create(user=user, name='autosavelabel', color='#336699')\n"
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Label, Task\n'
    "Task.objects.filter(user__email=os.environ['E2E_EMAIL'], title__startswith='Autosave ').delete()\n"
    "Label.objects.filter(user__email=os.environ['E2E_EMAIL'], name='autosavelabel').delete()\n"
)


def _is_save(response) -> bool:
    return f'/todo/{NUMBER}/edit/' in response.url and response.request.method == 'POST'


def test_the_details_save_themselves(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SETUP)
    url = f'{receipts_server.url}/todo/{NUMBER}/'
    try:
        receipts_server.login(page)
        page.goto(url)
        form = page.locator('#task-details-form')
        expect(form.locator('button[type="submit"]')).to_have_count(0)
        sent: list[str] = []
        page.on(
            'request', lambda r: sent.append(r.url) if f'/todo/{NUMBER}/edit/' in r.url else None
        )

        # A select saves on change, and says so.
        with page.expect_response(_is_save):
            page.select_option(f'#priority-{NUMBER}', '2')
        expect(page.locator('#details-save-status')).to_have_text('Saved')

        # The date saves once it is read (on Tab), then the time.
        with page.expect_response(_is_save):
            page.locator(f'#due-date-{NUMBER}').fill('2030-12-24')
            page.locator(f'#due-date-{NUMBER}').press('Tab')
        with page.expect_response(_is_save):
            page.locator(f'#due-time-{NUMBER}').fill('9am')
            page.locator(f'#due-time-{NUMBER}').press('Tab')

        # Ticking a label sends nothing while the popover is open; closing it saves.
        sent.clear()
        form.get_by_role('button', name='Add labels').click()
        form.locator('input[name="labels"]').first.check()
        page.wait_for_timeout(400)
        assert sent == []
        with page.expect_response(_is_save):
            page.keyboard.press('Escape')

        # The repeat popover saves on Done, not on each control.
        sent.clear()
        page.locator('#task-details-form').get_by_role('button', name=re.compile('^Repeat')).click()
        page.locator('#task-details-form').get_by_label('Frequency').select_option('week')
        page.wait_for_timeout(400)
        assert sent == []
        with page.expect_response(_is_save):
            page.locator('#task-details-form').get_by_role('button', name='Done').click()

        # Everything is there after a reload.
        page.reload()
        form = page.locator('#task-details-form')
        expect(page.locator(f'#priority-{NUMBER}')).to_have_value('2')
        expect(form.locator('input[name="due_date"]')).to_have_value('2030-12-24')
        expect(form.locator('input[name="due_time"]')).to_have_value('09:00')
        expect(form.locator('input[name="labels"]').first).to_be_checked()
        expect(form.locator('input[name="rrule"]')).to_have_value(re.compile('FREQ=WEEKLY'))

        # Text typed in another field while a save is in flight survives the panel being swapped.
        def slow(route):
            page.wait_for_timeout(600)
            route.continue_()

        page.route(f'**/todo/{NUMBER}/edit/', slow)
        words = page.get_by_label('Or type a date')
        with page.expect_response(_is_save):
            page.select_option(f'#priority-{NUMBER}', '3')
            words.click()
            words.press_sequentially('next fri')
        page.wait_for_timeout(300)
        expect(page.get_by_label('Or type a date')).to_have_value('next fri')
        expect(page.get_by_label('Or type a date')).to_be_focused()
        page.unroute(f'**/todo/{NUMBER}/edit/')
        page.get_by_label('Or type a date').fill('')

        # Setting something to the value it already has sends nothing.
        sent.clear()
        page.select_option(f'#priority-{NUMBER}', '3')
        page.wait_for_timeout(400)
        assert sent == []
    finally:
        receipts_server.shell(CLEANUP)
