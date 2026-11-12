"""To-do task states and resolutions (issue #272), end to end in a real browser: the state chip moves a
row to its group on Today, a drag or arrow key never crosses a group, and the close dialog (a parent's
subtasks, the Won't do note) and the Closed page work with real Alpine, htmx and SortableJS.

Run on the shared e2e server (`receipts_server`). Tasks are made straight in the database, with numbers
from 91000, so no credits are spent; each test removes what it made.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SEED_TODAY = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.accounts.services.timezones import user_today\n'
    'from apps.todo.models import Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='St ').delete()\n"
    'today = user_today(user)\n'
    'for number, title, state in (\n'
    "    (91001, 'St doing', 'in_progress'),\n"
    "    (91002, 'St plain a', 'todo'),\n"
    "    (91003, 'St plain b', 'todo'),\n"
    "    (91004, 'St held', 'on_hold'),\n"
    '):\n'
    '    Task.objects.create(user=user, number=number, title=title, state=state, due_date=today)\n'
)
SEED_PARENT = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='St ').delete()\n"
    "parent = Task.objects.create(user=user, number=91010, title='St parent')\n"
    "Task.objects.create(user=user, number=91011, title='St child one', parent=parent)\n"
    "Task.objects.create(user=user, number=91012, title='St child two', parent=parent)\n"
    "Task.objects.create(user=user, number=91013, title='St solo')\n"
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Task\n'
    "Task.objects.filter(user__email=os.environ['E2E_EMAIL'], title__startswith='St ').delete()\n"
)


def _titles(page: Page) -> list[str]:
    """The `St ` rows of the list, in page order."""
    names = page.eval_on_selector_all(
        '#task-list li.task-row .task-title', 'els => els.map(e => e.textContent.trim())'
    )
    return [name for name in names if name.startswith('St ')]


def test_the_state_chip_moves_the_row_to_its_group(page: Page, receipts_server: ReceiptsServer):
    # The first page after a sign-in saves the browser's time zone, which decides what "today" is
    # (the seed uses the account's): let that settle before seeding, or the tasks can land on the
    # wrong day around midnight UTC.
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/todo/')
    page.wait_for_load_state('networkidle')
    receipts_server.shell(SEED_TODAY)
    try:
        page.goto(f'{receipts_server.url}/todo/')
        expect(page.locator('#task-list li.task-row', has_text='St held')).to_be_visible()
        assert _titles(page) == ['St doing', 'St plain a', 'St plain b', 'St held']

        row = page.locator('#task-list li.task-row', has_text='St plain b').first
        row.get_by_role('button', name=re.compile('State of St plain b')).click()
        menu = row.get_by_role('menu')
        expect(menu).to_be_visible()
        expect(menu.get_by_role('menuitemradio', name='To do')).to_have_attribute(
            'aria-checked', 'true'
        )
        menu.get_by_role('menuitemradio', name='In progress').click()

        # The whole list is re-rendered: the row is now in the In progress group, after the first one.
        expect(page.locator('#task-list li.task-row[data-group="in_progress"]')).to_have_count(2)
        assert _titles(page) == ['St doing', 'St plain b', 'St plain a', 'St held']
        row = page.locator('#task-list li.task-row', has_text='St plain b').first
        expect(row.get_by_role('button', name=re.compile('In progress'))).to_be_visible()

        # Escape closes the menu and gives the focus back to the chip.
        chip = row.get_by_role('button', name=re.compile('State of St plain b'))
        chip.click()
        expect(row.get_by_role('menu')).to_be_visible()
        page.keyboard.press('Escape')
        expect(row.get_by_role('menu')).to_be_hidden()
        expect(chip).to_be_focused()
    finally:
        receipts_server.shell(CLEANUP)


def test_the_state_menu_works_from_the_keyboard(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SEED_TODAY)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/')
        row = page.locator('#task-list li.task-row', has_text='St plain b').first
        chip = row.get_by_role('button', name=re.compile('State of St plain b'))
        menu = row.get_by_role('menu')
        todo = menu.get_by_role('menuitemradio', name='To do')
        doing = menu.get_by_role('menuitemradio', name='In progress')
        hold = menu.get_by_role('menuitemradio', name='On hold')

        # Opening focuses the checked item; the arrows move (wrapping), Home and End jump.
        chip.focus()
        page.keyboard.press('ArrowDown')
        expect(menu).to_be_visible()
        expect(todo).to_be_focused()
        page.keyboard.press('ArrowDown')
        expect(doing).to_be_focused()
        page.keyboard.press('ArrowDown')
        expect(hold).to_be_focused()
        page.keyboard.press('ArrowDown')
        expect(todo).to_be_focused()
        page.keyboard.press('ArrowUp')
        expect(hold).to_be_focused()
        page.keyboard.press('Home')
        expect(todo).to_be_focused()
        page.keyboard.press('End')
        expect(hold).to_be_focused()

        # Escape closes it and returns the focus to the chip; Enter on an item chooses it.
        page.keyboard.press('Escape')
        expect(menu).to_be_hidden()
        expect(chip).to_be_focused()
        page.keyboard.press('Enter')
        expect(todo).to_be_focused()
        page.keyboard.press('ArrowDown')
        page.keyboard.press('Enter')
        expect(page.locator('#task-list li.task-row[data-group="in_progress"]')).to_have_count(2)
    finally:
        receipts_server.shell(CLEANUP)


def test_an_arrow_key_never_moves_a_row_into_another_group(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(SEED_TODAY)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/')
        live = page.locator('[data-sortable-live]')
        first = page.locator('.drag-handle[data-name="St plain a"]')
        last = page.locator('.drag-handle[data-name="St plain b"]')

        # `St plain a` is first of the To do group: the In progress row above it is out of reach.
        first.focus()
        page.keyboard.press('ArrowUp')
        expect(live).to_have_text('St plain a is already first in its group.')
        # ... and `St plain b` is last of it, with the On hold row below.
        last.focus()
        page.keyboard.press('ArrowDown')
        expect(live).to_have_text('St plain b is already last in its group.')
        assert _titles(page) == ['St doing', 'St plain a', 'St plain b', 'St held']

        # Inside the group a move works and is saved: one request for the burst.
        first.focus()
        with page.expect_response(lambda r: r.url.endswith('/todo/reorder/')) as saved:
            page.keyboard.press('ArrowDown')
        assert saved.value.status < 300
        expect(live).to_have_text(re.compile(r'St plain a moved to position \d+ of \d+\.'))
        page.reload()
        assert _titles(page) == ['St doing', 'St plain b', 'St plain a', 'St held']
    finally:
        receipts_server.shell(CLEANUP)


def test_closing_a_parent_asks_about_its_open_subtasks(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SEED_PARENT)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/91010/')
        complete = page.get_by_role('button', name='Complete', exact=True)
        complete.click()

        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('It has 2 open subtasks:')
        expect(dialog.get_by_label('Close them as Done')).to_be_checked()
        expect(dialog.get_by_label('Leave them open')).to_be_visible()
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        expect(complete).to_be_focused()

        complete.click()
        dialog.get_by_label('Leave them open').check()
        dialog.get_by_role('button', name='Complete', exact=True).click()

        # The parent is done, its subtasks are still open (no tick on them).
        expect(page.locator('#task-panel')).to_contain_text('Done')
        expect(page.locator('#subtasks li.task-row')).to_have_count(2)
        expect(page.locator('#subtasks .is-done')).to_have_count(0)
    finally:
        receipts_server.shell(CLEANUP)


def test_close_as_wont_do_with_a_note_and_the_closed_page(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(SEED_PARENT)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/91013/')
        page.get_by_role('button', name="Close as won't do").click()
        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_label('Note (optional)')).to_be_focused()
        expect(dialog).not_to_contain_text('open subtask')  # it has none
        dialog.get_by_label('Note (optional)').fill('Not worth the trouble')
        dialog.get_by_role('button', name="Close as won't do").click()

        expect(page.locator('#task-panel')).to_contain_text("Won't do")
        expect(page.locator('#task-comments')).to_contain_text('Not worth the trouble')

        page.goto(f'{receipts_server.url}/todo/closed/')
        row = page.locator('#task-list li.task-row', has_text='St solo').first
        expect(row.locator('.is-wont-do')).to_be_visible()
        expect(row).to_contain_text("Won't do")

        page.get_by_role('link', name='Done', exact=True).click()
        expect(page.locator('#task-list').get_by_text('St solo')).to_have_count(0)
        assert 'resolution=done' in page.url
        page.get_by_role('link', name="Won't do", exact=True).click()
        expect(page.locator('#task-list').get_by_text('St solo').first).to_be_visible()
        assert 'resolution=wont_do' in page.url

        # Reopening from the crossed circle puts it back to open.
        page.locator('#task-list li.task-row', has_text='St solo').first.locator(
            '.is-wont-do'
        ).click()
        expect(page.locator('#task-list').get_by_text('St solo')).to_have_count(0)
    finally:
        receipts_server.shell(CLEANUP)
