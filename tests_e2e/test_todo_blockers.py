"""To-do blockers (issue #273), end to end in a real browser: adding a blocker on the task page (the
list and the Blocked chip answer at once), closing the blocker frees the task and the notification
page says so, closing a task that still has open blockers asks (the dialog names them, fetched as
JSON), and the quick-add box's "Blocked by" field.

Run on the shared e2e server (`receipts_server`). Tasks are made straight in the database, with
numbers from 92000, so no credits are spent; each test removes what it made.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SEED_PAIR = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='Bk ').delete()\n"
    "Task.objects.create(user=user, number=92001, title='Bk main')\n"
    "Task.objects.create(user=user, number=92002, title='Bk blocker')\n"
)
SEED_LINKED = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Task, TaskBlocker\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='Bk ').delete()\n"
    "waits = Task.objects.create(user=user, number=92003, title='Bk waits', state='blocked')\n"
    "first = Task.objects.create(user=user, number=92004, title='Bk first blocker')\n"
    "second = Task.objects.create(user=user, number=92005, title='Bk second blocker')\n"
    'TaskBlocker.objects.create(blocker=first, blocked=waits)\n'
    'TaskBlocker.objects.create(blocker=second, blocked=waits)\n'
)
CLEANUP = (
    'import os\n'
    'from apps.core.models import Notification\n'
    'from apps.todo.models import Task\n'
    "email = os.environ['E2E_EMAIL']\n"
    "Task.objects.filter(user__email=email, title__startswith='Bk ').delete()\n"
    "Notification.objects.filter(user__email=email, key__startswith='todo_').delete()\n"
)


def test_adding_a_blocker_blocks_the_task_and_closing_it_frees_it(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(SEED_PAIR)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/92001/')
        panel = page.locator('#task-panel')
        expect(
            panel.get_by_role('button', name=re.compile('State of Bk main: To do'))
        ).to_be_visible()

        box = page.get_by_label('Add a blocker')
        box.fill('Bk bl')  # the list of suggestions follows what is typed
        expect(page.locator('#blocker-options option')).to_have_count(1)
        expect(page.locator('#blocker-options option')).to_have_attribute('value', '#92002')
        box.fill('92002')
        page.locator('#blockers').get_by_role('button', name='Add', exact=True).click()

        blockers = page.locator('#blockers')
        expect(blockers.locator('[data-blocker="92002"]')).to_contain_text('#92002 Bk blocker')
        expect(
            panel.get_by_role('button', name=re.compile('State of Bk main: Blocked'))
        ).to_be_visible()
        expect(page.locator('#muted-note')).to_contain_text('This task is blocked')

        # A refusal: the message lands in the section's slot and the page stays as it was.
        page.get_by_label('Add a blocker').fill('92001')
        page.locator('#blockers').get_by_role('button', name='Add', exact=True).click()
        expect(page.locator('#blocker-msg')).to_contain_text('A task cannot block itself.')

        # The other task shows it under "Blocks", and closing it (nothing blocks it) needs no question.
        page.goto(f'{receipts_server.url}/todo/92002/')
        expect(page.locator('#blockers [data-blocks="92001"]')).to_contain_text('#92001 Bk main')
        page.get_by_role('button', name='Complete', exact=True).click()
        expect(page.locator('#task-panel')).to_contain_text('Done')

        # The blocked task is To do again, and the notifications page says so.
        page.goto(f'{receipts_server.url}/todo/92001/')
        expect(
            page.locator('#task-panel').get_by_role(
                'button', name=re.compile('State of Bk main: To do')
            )
        ).to_be_visible()
        expect(page.locator('#blockers [data-blocker="92002"]')).to_contain_text('Done')
        page.goto(f'{receipts_server.url}/notifications/')
        expect(page.get_by_text('#92001 Bk main is no longer blocked')).to_be_visible()

        # Reopening the blocker does not block it again; the owner is only told.
        page.goto(f'{receipts_server.url}/todo/92002/')
        page.get_by_role('button', name='Reopen').click()
        expect(page.get_by_role('button', name='Complete', exact=True)).to_be_visible()
        page.goto(f'{receipts_server.url}/notifications/')
        expect(page.get_by_text('#92002 was reopened and blocks #92001')).to_be_visible()
        page.goto(f'{receipts_server.url}/todo/92001/')
        expect(
            page.locator('#task-panel').get_by_role(
                'button', name=re.compile('State of Bk main: To do')
            )
        ).to_be_visible()

        # Removing a blocker with the button.
        page.get_by_role('button', name=re.compile('Remove blocker #92002')).click()
        expect(page.locator('#blockers [data-blocker]')).to_have_count(0)
    finally:
        receipts_server.shell(CLEANUP)


def test_closing_a_task_with_open_blockers_asks_and_names_them(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(SEED_LINKED)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/92003/')
        complete = page.get_by_role('button', name='Complete', exact=True)
        complete.click()

        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('2 open tasks are still blocking it:')
        expect(dialog).to_contain_text('#92004 Bk first blocker')
        expect(dialog).to_contain_text('#92005 Bk second blocker')
        expect(dialog).to_contain_text('Closing it keeps the links.')
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        expect(complete).to_be_focused()
        expect(page.get_by_role('button', name='Reopen')).to_have_count(0)  # still open

        complete.click()
        dialog.get_by_role('button', name='Complete', exact=True).click()

        expect(page.get_by_role('button', name='Reopen')).to_be_visible()
        # The links are kept.
        expect(page.locator('#blockers [data-blocker]')).to_have_count(2)
    finally:
        receipts_server.shell(CLEANUP)


def test_a_rows_circle_opens_the_same_question(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SEED_LINKED)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/all/')
        row = page.locator('#task-list li.task-row', has_text='Bk waits').first
        expect(
            row.get_by_role('button', name=re.compile('State of Bk waits: Blocked'))
        ).to_be_visible()

        row.get_by_role('button', name='Complete Bk waits').click()

        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('#92004 Bk first blocker')
        dialog.get_by_role('button', name='Cancel').click()
        expect(dialog).to_be_hidden()
        # A blocker with nothing blocking it closes straight away, and frees nothing (two remain).
        blocker_row = page.locator('#task-list li.task-row', has_text='Bk first blocker').first
        blocker_row.get_by_role('button', name='Complete Bk first blocker').click()
        expect(page.locator('#task-list li.task-row', has_text='Bk first blocker')).to_have_count(0)
        expect(
            row.get_by_role('button', name=re.compile('State of Bk waits: Blocked'))
        ).to_be_visible()
    finally:
        receipts_server.shell(CLEANUP)


def test_quick_add_takes_the_numbers_of_its_blockers(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SEED_PAIR)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/all/')
        page.get_by_label('Task title').fill('Bk quick')
        page.get_by_label('Blocked by').fill('#92002')
        page.get_by_role('button', name='Add task').click()

        row = page.locator('#task-list li.task-row', has_text='Bk quick').first
        expect(
            row.get_by_role('button', name=re.compile('State of Bk quick: Blocked'))
        ).to_be_visible()

        page.get_by_label('Task title').fill('Bk refused')
        page.get_by_label('Blocked by').fill('1')
        page.get_by_role('button', name='Add task').click()
        expect(page.locator('#quick-add-msg')).to_contain_text('There is no task #1.')
        expect(page.locator('#task-list li.task-row', has_text='Bk refused')).to_have_count(0)
    finally:
        receipts_server.shell(CLEANUP)
