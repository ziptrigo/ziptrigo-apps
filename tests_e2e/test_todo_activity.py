"""The To-do activity log (issue #274), end to end in a real browser: the task page shows no history
until the muted "History" button is pressed, the first press loads the latest 20 entries and moves
focus to the heading, "Show more" appends the rest and moves focus to the first new row, an action on
the task shows up in the open history, and the button works from the keyboard.

Run on the shared e2e server (`receipts_server`). The task is made straight in the database, number
93001, with entries written by `services.events`; each test removes what it made.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SEED = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Task\n'
    'from apps.todo.services import events\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='Ac ').delete()\n"
    "task = Task.objects.create(user=user, number=93001, title='Ac main')\n"
    "events.record(task, 'created', {'source': 'web'})\n"
    'for n in range(24):\n'
    "    events.record(task, 'title', {'from': f'Ac old {n}', 'to': f'Ac old {n + 1}'})\n"
    "events.record(task, 'state', {'from': 'todo', 'to': 'in_progress'}, automatic=True)\n"
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Task\n'
    "Task.objects.filter(user__email=os.environ['E2E_EMAIL'], title__startswith='Ac ').delete()\n"
)


def test_history_loads_on_demand_pages_and_follows_actions(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(SEED)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/93001/')

        toggle = page.locator('#task-activity-toggle')
        body = page.locator('#task-activity-body')
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        expect(body).to_be_hidden()
        expect(page.locator('#task-activity-list')).to_have_count(0)
        expect(page.get_by_text('Title changed from')).to_have_count(0)

        toggle.click()
        expect(toggle).to_have_attribute('aria-expanded', 'true')
        expect(toggle).to_have_text('Hide history')
        expect(page.locator('#task-activity-heading')).to_be_focused()
        rows = page.locator('#task-activity-list > li')
        expect(rows).to_have_count(20)
        expect(rows.first).to_contain_text('State changed from To do to In progress')
        expect(rows.first).to_contain_text('Automatic')
        expect(rows.nth(1)).not_to_contain_text('Automatic')

        page.get_by_role('button', name='Show more').click()
        expect(rows).to_have_count(26)
        expect(rows.nth(20)).to_be_focused()
        expect(rows.last).to_contain_text('Created')
        expect(page.get_by_role('button', name='Show more')).to_have_count(0)

        # An action on the task shows up in the open history (the first page is fetched again).
        page.get_by_role('button', name='Complete', exact=True).click()
        expect(page.locator('#task-panel')).to_contain_text('Done')
        expect(page.locator('#task-activity-list > li').first).to_contain_text('Closed as Done')

        # Hiding and showing again needs no new request and keeps what was loaded.
        toggle.click()
        expect(body).to_be_hidden()
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        toggle.click()
        expect(body).to_be_visible()
        expect(page.locator('#task-activity-list > li').first).to_contain_text('Closed as Done')
    finally:
        receipts_server.shell(CLEANUP)


def test_history_works_from_the_keyboard(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SEED)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/93001/')

        page.locator('#task-activity-toggle').focus()
        page.keyboard.press('Enter')
        expect(page.locator('#task-activity-heading')).to_be_focused()
        expect(page.locator('#task-activity-list > li')).to_have_count(20)

        more = page.get_by_role('button', name='Show more')
        more.focus()
        page.keyboard.press('Enter')
        expect(page.locator('#task-activity-list > li')).to_have_count(26)
        expect(page.locator('#task-activity-list > li').nth(20)).to_be_focused()

        page.locator('#task-activity-toggle').focus()
        page.keyboard.press('Space')
        expect(page.locator('#task-activity-body')).to_be_hidden()
        expect(page.locator('#task-activity-toggle')).to_have_attribute(
            'aria-expanded', re.compile('false')
        )
    finally:
        receipts_server.shell(CLEANUP)
