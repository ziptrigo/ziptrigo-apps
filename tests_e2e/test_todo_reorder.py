"""To-do: reordering a list with the keyboard, end to end in a real browser.

The drag handle is core's shared `initSortableList` (`core/js/sortable_list.js`): the arrow keys on a
focused handle move its row, the move is announced, and one request saves a burst of key presses.
"""

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

SETUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Label\n'
    'from apps.todo.services import labels\n'
    "user = User.objects.get(email=os.environ['E2E_EMAIL'])\n"
    "Label.objects.filter(user=user, name__startswith='Zq ').delete()\n"
    "for name in ('Zq A', 'Zq B', 'Zq C'):\n"
    '    labels.create_label(user, name)\n'
)
CLEANUP = (
    'import os\n'
    'from apps.accounts.models import User\n'
    'from apps.todo.models import Label\n'
    "Label.objects.filter(user__email=os.environ['E2E_EMAIL'], name__startswith='Zq ').delete()\n"
)


def _names(page: Page) -> list[str]:
    return [
        name
        for name in page.eval_on_selector_all(
            '.drag-handle', 'els => els.map(e => e.getAttribute("data-name"))'
        )
        if name and name.startswith('Zq ')
    ]


def test_labels_reorder_with_the_arrow_keys(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(SETUP)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/todo/labels/')
        assert _names(page) == ['Zq A', 'Zq B', 'Zq C']

        handle = page.locator('.drag-handle[data-name="Zq C"]')
        handle.focus()
        # Two quick presses make one save; the focus stays on the handle and the move is announced.
        with page.expect_response(lambda r: r.url.endswith('/todo/labels/reorder/')) as saved:
            page.keyboard.press('ArrowUp')
            page.keyboard.press('ArrowUp')
        assert saved.value.status < 300
        expect(handle).to_be_focused()
        live = page.locator('[data-sortable-live]')
        expect(live).to_have_text('Zq C moved to position 1 of 3.')
        assert _names(page) == ['Zq C', 'Zq A', 'Zq B']

        # At the top another press only announces the end of the list.
        page.keyboard.press('ArrowUp')
        expect(live).to_have_text('Zq C is already first.')
        assert _names(page) == ['Zq C', 'Zq A', 'Zq B']

        page.reload()
        assert _names(page) == ['Zq C', 'Zq A', 'Zq B']
    finally:
        receipts_server.shell(CLEANUP)
