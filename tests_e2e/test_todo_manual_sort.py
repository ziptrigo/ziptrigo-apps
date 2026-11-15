"""To-do: the Manual sort on All tasks and clicking a row to open the task, in a real browser.

Manual shows drag handles on the All page; the arrow keys move a row and one request saves the order,
which survives a reload. A click on a row's empty space opens the task, while the state menu, the title
link and a text selection keep doing their own thing. Run on the shared e2e server (`receipts_server`);
tasks are made straight in the database (numbers from 93001, so no credits are spent) and removed after.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Task\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Task.objects.filter(user=user, title__startswith='Ms ').delete()\n"
    "for number, name in ((93001, 'Ms A'), (93002, 'Ms B'), (93003, 'Ms C')):\n"
    '    Task.objects.create(user=user, number=number, title=name)\n'
)
CLEANUP = (
    'import os\n'
    'from apps.todo.models import Task\n'
    "Task.objects.filter(user__email=os.environ['E2E_EMAIL'], title__startswith='Ms ').delete()\n"
)


def _names(page: Page) -> list[str]:
    return [
        name
        for name in page.eval_on_selector_all(
            '.drag-handle', 'els => els.map(e => e.getAttribute("data-name"))'
        )
        if name and name.startswith('Ms ')
    ]


def test_manual_sort_reorders_all_tasks_and_keeps_the_order(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(SETUP)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/all/')
        sort_bar = page.get_by_text('Sort by').locator('..')
        links = sort_bar.locator('a')
        assert links.all_inner_texts()[-1].strip() == 'Manual'
        expect(page.locator('.drag-handle')).to_have_count(0)

        links.last.click()
        expect(page).to_have_url(re.compile(r'/todo/all/\?sort=manual$'))
        expect(page.locator('.drag-handle').first).to_be_visible()
        assert _names(page) == ['Ms A', 'Ms B', 'Ms C']
        # no direction to flip: the bar shows no arrow on Manual
        expect(links.last.locator('i')).to_have_count(0)

        page.locator('.drag-handle[data-name="Ms C"]').focus()
        with page.expect_response(lambda r: r.url.endswith('/todo/reorder/')) as saved:
            page.keyboard.press('ArrowUp')
            page.keyboard.press('ArrowUp')
        assert saved.value.status == 204
        assert _names(page) == ['Ms C', 'Ms A', 'Ms B']

        page.reload()
        assert _names(page) == ['Ms C', 'Ms A', 'Ms B']
        # the other sorts are untouched
        page.goto(f'{receipts_server.url}/todo/all/?sort=title')
        expect(page.locator('.drag-handle')).to_have_count(0)
        titles = page.locator('a.task-title').all_inner_texts()
        assert [t for t in titles if t.startswith('Ms ')] == ['Ms A', 'Ms B', 'Ms C']
    finally:
        receipts_server.shell(CLEANUP)


def test_clicking_a_row_opens_the_task_but_not_its_controls(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(SETUP)
    try:
        receipts_server.login(page)
        all_url = f'{receipts_server.url}/todo/all/'
        page.goto(all_url)
        row = page.locator('#task-93001')
        box = row.bounding_box()
        assert box is not None

        def click_empty_space():
            # first line of the row, well right of the title and left of the action buttons
            row.click(position={'x': box['width'] * 0.6, 'y': 10})

        # the state pill opens its menu and stays on the page
        pill = row.get_by_role('button', name=re.compile('State of Ms A'))
        pill.click()
        expect(row.get_by_role('menu')).to_be_visible()
        assert page.url == all_url

        # a press while a menu is open only closes it
        click_empty_space()
        expect(row.get_by_role('menu')).to_be_hidden()
        assert page.url == all_url

        # dragging across the row's text to select it does not open it (the click that ends a drag
        # arrives with a selection)
        chips = row.get_by_text('#93001')
        text_box = chips.bounding_box()
        assert text_box is not None
        y = text_box['y'] + text_box['height'] / 2
        page.mouse.move(text_box['x'] + text_box['width'] + 40, y)
        page.mouse.down()
        page.mouse.move(text_box['x'] + 1, y, steps=8)
        page.mouse.up()
        assert page.evaluate('getSelection().toString()') != ''
        assert page.url == all_url
        page.evaluate('getSelection().removeAllRanges()')

        # the empty space opens the task
        click_empty_space()
        expect(page).to_have_url(re.compile(r'/todo/93001/$'))

        # and so does the title link, as before
        page.go_back()
        page.locator('#task-93002 a.task-title').click()
        expect(page).to_have_url(re.compile(r'/todo/93002/$'))
    finally:
        receipts_server.shell(CLEANUP)
