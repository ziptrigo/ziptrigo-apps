"""The "Apps" section of the account page (issue #246), end to end in a real browser.

Switching an app off takes it out of the header menu with no reload, the apps can be put in order
from the keyboard (the menu follows), and the choice survives a reload.
"""

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

RESET = (
    'import os\n'
    'from apps.accounts.models import User\n'
    "User.objects.filter(email=os.environ['E2E_EMAIL']).update(app_order=[], hidden_apps=[])\n"
)


def _nav_names(page: Page) -> list[str]:
    return page.eval_on_selector_all(
        '#product-nav a', 'links => links.map(link => link.getAttribute("aria-label"))'
    )


def test_switch_an_app_off_and_reorder_with_the_keyboard(
    page: Page, receipts_server: ReceiptsServer
):
    # The seeded account is shared by the other e2e tests: start and finish with the defaults.
    receipts_server.shell(RESET)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        # Proves below that nothing reloads the page.
        page.evaluate('window.__noReload = true')
        assert _nav_names(page) == [
            'QR Codes',
            'File Transfer',
            'Receipts',
            'Short URLs',
            'To-do',
        ]

        # Switching an app off saves at once and drops it from the header.
        with page.expect_response(lambda r: r.url.endswith('/account/apps/')) as saved:
            page.get_by_role('checkbox', name='Receipts').uncheck()
        assert saved.value.status == 200
        expect(page.locator('#product-nav a[aria-label="Receipts"]')).to_have_count(0)
        assert _nav_names(page) == ['QR Codes', 'File Transfer', 'Short URLs', 'To-do']

        # The arrow keys on a tile's handle move it; focus stays on the handle.
        handle = page.get_by_role('button', name='Reorder To-do')
        expect(handle).to_be_visible()
        handle.focus()
        with page.expect_response(lambda r: r.url.endswith('/account/apps/')) as moved:
            for _ in range(4):
                page.keyboard.press('ArrowUp')
        assert moved.value.status == 200
        expect(handle).to_be_focused()
        expect(page.locator('[data-apps-live]')).to_contain_text('To-do moved to position 1 of 5.')
        assert _nav_names(page) == ['To-do', 'QR Codes', 'File Transfer', 'Short URLs']
        assert page.evaluate('window.__noReload') is True

        # The choice survives a reload, switched-off app and order alike.
        page.reload()
        assert _nav_names(page) == ['To-do', 'QR Codes', 'File Transfer', 'Short URLs']
        expect(page.get_by_role('checkbox', name='Receipts')).not_to_be_checked()
        expect(page.get_by_role('checkbox', name='To-do')).to_be_checked()

        # The home page follows too, and switching the app back on restores its place.
        page.goto(f'{receipts_server.url}/')
        assert page.locator('a[href="/receipts/"]').count() == 0
        page.goto(f'{receipts_server.url}/account/')
        page.get_by_role('checkbox', name='Receipts').check()
        expect(page.locator('#product-nav a[aria-label="Receipts"]')).to_have_count(1)
        assert _nav_names(page) == ['To-do', 'QR Codes', 'File Transfer', 'Receipts', 'Short URLs']
    finally:
        receipts_server.shell(RESET)


def test_home_page_when_every_app_is_off(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(
        'import os\n'
        'from apps.accounts.models import User\n'
        'from apps.core.products import get_products\n'
        "User.objects.filter(email=os.environ['E2E_EMAIL']).update("
        'hidden_apps=[p.label for p in get_products()])\n'
    )
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/')
        expect(page.locator('#no-apps')).to_contain_text('All your apps are switched off')
        assert _nav_names(page) == []
        page.get_by_role('link', name='Choose your apps').click()
        page.wait_for_url('**/account/#apps')
        expect(page.get_by_role('checkbox', name='QR Codes')).not_to_be_checked()
    finally:
        receipts_server.shell(RESET)


def test_drag_a_tile_to_reorder(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(RESET)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        with page.expect_response(lambda r: r.url.endswith('/account/apps/')) as saved:
            page.get_by_role('button', name='Reorder To-do').drag_to(
                page.locator('[data-app="qr_code"]')
            )
        assert saved.value.status == 200
        assert _nav_names(page)[0] == 'To-do'
        page.reload()
        assert _nav_names(page)[0] == 'To-do'
    finally:
        receipts_server.shell(RESET)


def test_a_failed_save_says_so_and_shows_what_is_stored(
    page: Page, receipts_server: ReceiptsServer
):
    receipts_server.shell(RESET)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        # The save never reaches the server (a network error).
        page.route('**/account/apps/', lambda route: route.abort())
        page.get_by_role('checkbox', name='Short URLs').uncheck()
        alert = page.locator('#apps-form [role="alert"]')
        expect(alert).to_have_text("Couldn't save your apps. Try again.")
        # The tiles match the server again, and the header never changed.
        expect(page.get_by_role('checkbox', name='Short URLs')).to_be_checked()
        assert 'Short URLs' in _nav_names(page)

        # Once the network is back the next change saves and the line goes away.
        page.unroute('**/account/apps/')
        with page.expect_response(lambda r: r.url.endswith('/account/apps/')) as saved:
            page.get_by_role('checkbox', name='Short URLs').uncheck()
        assert saved.value.status == 200
        expect(page.locator('#apps-form [role="alert"]')).to_have_count(0)
        assert 'Short URLs' not in _nav_names(page)
    finally:
        receipts_server.shell(RESET)


def test_arrow_keys_announce_the_ends_of_the_list(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(RESET)
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        handle = page.get_by_role('button', name='Reorder QR Codes')
        handle.focus()
        page.keyboard.press('ArrowUp')
        expect(page.locator('[data-apps-live]')).to_have_text('QR Codes is already first.')
        page.get_by_role('button', name='Reorder To-do').focus()
        page.keyboard.press('ArrowDown')
        expect(page.locator('[data-apps-live]')).to_have_text('To-do is already last.')
    finally:
        receipts_server.shell(RESET)


def test_the_apps_are_one_column(page: Page, receipts_server: ReceiptsServer):
    receipts_server.shell(RESET)
    receipts_server.login(page)
    page.goto(f'{receipts_server.url}/account/')

    boxes = page.locator('[data-app]').evaluate_all(
        'tiles => tiles.map(tile => { const r = tile.getBoundingClientRect(); return [r.left, r.top]; })'
    )
    assert len(boxes) == 5
    assert len({left for left, _ in boxes}) == 1  # all the same left edge
    assert [top for _, top in boxes] == sorted(top for _, top in boxes)
