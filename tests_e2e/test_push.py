"""Web Push (issue #239) in a real browser: the account page section's states, the service worker
at the root, and the rule that a browser's subscription is re-saved after a sign-in only for the
account that turned push on in it.

Headless Chromium answers "denied" for notifications whatever the context grants, and a real
subscription needs the browser's push service (Google's), which it can't reach. So the tests set
`Notification.permission` themselves, and the ones that need a subscription replace
`navigator.serviceWorker.getRegistration` with a fake registration whose subscription points at a
made-up FCM endpoint. The server accepts it like any other (it never sends anything here).
"""

from playwright.sync_api import Browser, BrowserContext, Page, expect

from tests_e2e.conftest import ReceiptsServer

ENDPOINT = 'https://fcm.googleapis.com/fcm/send/e2e-push-device'

# A browser that already has a subscription for this site.
FAKE_SUBSCRIPTION = f"""
(() => {{
  const subscription = {{
    endpoint: '{ENDPOINT}',
    options: {{ applicationServerKey: null }},
    toJSON() {{
      return {{ endpoint: this.endpoint, keys: {{ p256dh: 'B' + 'a'.repeat(86), auth: 'tBHItJI5svbpez7KI4CCXg' }} }};
    }},
    unsubscribe: async () => true,
  }};
  const registration = {{ pushManager: {{ getSubscription: async () => subscription }} }};
  navigator.serviceWorker.getRegistration = async () => registration;
}})();
"""


def _context(browser: Browser, *, permission: str) -> BrowserContext:
    """A browser whose notification permission reads `permission` (`granted`, `denied`)."""
    context = browser.new_context()
    context.add_init_script(
        "Object.defineProperty(Notification, 'permission', { get: () => '%s' });" % permission
    )
    return context


def _section(page: Page):
    return page.locator('[data-push-settings]')


def test_section_states_and_service_worker(browser: Browser, receipts_server: ReceiptsServer):
    context = _context(browser, permission='granted')
    page = context.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        section = _section(page)
        expect(section).to_have_attribute('data-push-current', 'off')
        expect(section.get_by_role('button', name='Turn on notifications')).to_be_visible()

        scope = page.evaluate(
            "async () => (await navigator.serviceWorker.register('/sw.js', {scope: '/'})).scope"
        )
        assert scope == f'{receipts_server.url}/'
    finally:
        context.close()

    blocked = _context(browser, permission='denied')
    page = blocked.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        expect(_section(page)).to_have_attribute('data-push-current', 'blocked')
        expect(_section(page)).to_contain_text('Notifications are blocked for this site.')
    finally:
        blocked.close()

    unsupported = _context(browser, permission='granted')
    unsupported.add_init_script('delete window.PushManager;')
    page = unsupported.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        expect(_section(page)).to_have_attribute('data-push-current', 'unsupported')
        expect(_section(page)).to_contain_text("This browser can't show notifications")
    finally:
        unsupported.close()


def _subscribe_posts(page: Page) -> list[int]:
    statuses: list[int] = []
    page.on(
        'response',
        lambda r: statuses.append(r.status) if r.url.endswith('/push/subscribe/') else None,
    )
    return statuses


def test_subscription_is_resaved_only_for_the_account_that_turned_push_on(
    browser: Browser, receipts_server: ReceiptsServer
):
    context = _context(browser, permission='granted')
    context.add_init_script(FAKE_SUBSCRIPTION)
    page = context.new_page()
    posts = _subscribe_posts(page)
    try:
        receipts_server.login(page)

        # Nothing says this account turned push on in this browser: nothing is posted, and the
        # account page offers the button (the subscription is someone else's, or a stale one).
        page.goto(f'{receipts_server.url}/account/')
        expect(_section(page)).to_have_attribute('data-push-current', 'off')
        assert posts == []

        # Another account's record: still nothing.
        page.evaluate(
            "localStorage.setItem('zt-push', JSON.stringify({endpoint: '%s', owner: 'someone-else'}))"
            % ENDPOINT
        )
        page.reload()
        expect(_section(page)).to_have_attribute('data-push-current', 'off')
        assert posts == []

        # This account's record (as "Turn on" leaves it): the subscription is saved again, once.
        owner = page.locator('script[data-owner]').get_attribute('data-owner')
        page.evaluate(
            "owner => localStorage.setItem('zt-push', JSON.stringify({endpoint: '%s', owner}))"
            % ENDPOINT,
            owner,
        )
        with page.expect_response(lambda r: r.url.endswith('/push/subscribe/')) as saved:
            page.reload()
        assert saved.value.status == 201
        expect(_section(page)).to_have_attribute('data-push-current', 'on')

        # The session knows it now: further pages post nothing.
        page.goto(f'{receipts_server.url}/')
        page.goto(f'{receipts_server.url}/account/')
        expect(_section(page)).to_have_attribute('data-push-current', 'on')
        assert posts == [201]
    finally:
        context.close()
