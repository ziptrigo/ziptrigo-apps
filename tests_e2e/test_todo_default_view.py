"""The list To-do opens on, end to end in a real browser: choose Upcoming in To-do's settings, then the
header's To-do link (`/todo/`) opens Upcoming while the sidebar's Today still opens Today.

Run on the shared e2e server (`receipts_server`). The test puts the setting back to Today at the end.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

RESET = (
    'import os\n'
    'from apps.todo.models import TodoAccount\n'
    "TodoAccount.objects.filter(user__email=os.environ['E2E_EMAIL']).update(default_view='today')\n"
)


def test_todo_opens_on_the_chosen_list(page: Page, receipts_server: ReceiptsServer):
    receipts_server.login(page)
    try:
        page.goto(f'{receipts_server.url}/todo/settings/')
        page.select_option('#default-view', 'upcoming')
        page.locator('form[action$="/todo/settings/"] button[type="submit"]').click()
        expect(page.locator('#settings-msg')).to_contain_text('Saved.')

        page.locator('#product-nav a[aria-label="To-do"]').click()
        expect(page).to_have_url(re.compile(r'/todo/upcoming/$'))
        expect(page.locator('h1')).to_have_text('Upcoming')

        sidebar = page.locator('nav[aria-label="To-do"]')
        expect(sidebar.locator('a[aria-current="page"]')).to_have_attribute(
            'href', re.compile(r'/todo/upcoming/$')
        )
        sidebar.locator('a[href$="/todo/today/"]').click()
        expect(page).to_have_url(re.compile(r'/todo/today/$'))
        expect(page.locator('h1')).to_have_text('Today')
    finally:
        receipts_server.shell(RESET)
