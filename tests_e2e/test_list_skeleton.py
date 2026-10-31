"""Skeleton rows while a list refreshes slowly (issue #214), end to end in a real browser.

The list requests are held by a route handler, so "slow" is whatever the test says, not a timing race.
Run on the shared e2e server (`receipts_server`), against the Receipts dashboard.
"""

import time

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

LIST = '**/receipts/list/**'
SKELETON = '#receipt-list [data-skeleton-rows]'


def _dashboard(page: Page, server: ReceiptsServer) -> None:
    server.login(page)
    page.goto(f'{server.url}/receipts/')
    expect(page.locator('#receipt-list')).to_be_visible()


def _wait_for(page: Page, held: list, count: int, seconds: float = 5.0) -> None:
    """Wait until `count` requests are being held. `page.wait_for_timeout` (not `time.sleep`) lets the
    sync API run the route handlers meanwhile."""
    end = time.monotonic() + seconds
    while len(held) < count and time.monotonic() < end:
        page.wait_for_timeout(50)
    assert len(held) >= count, f'only {len(held)} request(s) were made'


def test_a_slow_refresh_shows_the_rows_and_keeps_the_filters(
    page: Page, receipts_server: ReceiptsServer
):
    _dashboard(page, receipts_server)
    page.locator('[data-tabs]').get_by_role('link', name='Failed').click()
    status = page.locator('#receipt-list input[name="status"]')
    expect(status).to_have_count(1)

    held = []
    page.route(LIST, lambda route: held.append(route))
    page.locator('#q').fill('a')

    # After the pause the rows appear, and the list's own content (with its hidden inputs) is still there.
    expect(page.locator(SKELETON)).to_be_visible()
    expect(status).to_have_count(1)
    _wait_for(page, held, 1)

    # Typing again while the rows show replaces the request; it must still carry the tab (status=failed).
    page.locator('#q').fill('ab')
    _wait_for(page, held, 2)
    assert 'status=failed' in held[-1].request.url
    assert 'q=ab' in held[-1].request.url

    held[-1].continue_()
    page.unroute(LIST)
    expect(page.locator(SKELETON)).to_have_count(0)
    expect(page.locator('#receipt-list')).to_be_visible()
    expect(page.locator('#receipt-list')).not_to_have_attribute('aria-busy', 'true')


def test_a_fast_refresh_never_shows_the_rows(page: Page, receipts_server: ReceiptsServer):
    _dashboard(page, receipts_server)
    page.evaluate(
        """() => { window.__skeletonSeen = false;
                   new MutationObserver(() => { if (document.querySelector('[data-skeleton-rows]')) window.__skeletonSeen = true; })
                     .observe(document.body, { childList: true, subtree: true }); }"""
    )

    page.locator('#q').fill('anything')
    page.wait_for_timeout(1200)

    assert page.evaluate('window.__skeletonSeen') is False


def test_a_failed_refresh_removes_the_rows_and_the_list_still_works(
    page: Page, receipts_server: ReceiptsServer
):
    _dashboard(page, receipts_server)
    held = []
    page.route(LIST, lambda route: held.append(route))
    page.locator('#q').fill('zzz')
    expect(page.locator(SKELETON)).to_be_visible()
    _wait_for(page, held, 1)

    held[-1].abort()
    page.unroute(LIST)

    expect(page.locator(SKELETON)).to_have_count(0)
    # htmx still drives the page: another tab loads its list.
    page.locator('[data-tabs]').get_by_role('link', name='Failed').click()
    expect(page.locator('[data-tabs] [aria-current="page"]')).to_contain_text('Failed')
