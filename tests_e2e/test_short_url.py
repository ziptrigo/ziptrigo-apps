"""Short URLs, end to end in a real browser.

The server is `receipts_server` (see `receipts_e2e_settings.py`): the real site with in-memory
fakes where a product would reach out (it hosts every product, the name is historical), tasks run
inline and rate limiting off. The destination is never contacted: a visitor following a link is
Playwright's HTTP client asked not to follow the redirect (a browser would try to load the
destination), and the links are given a title so no page title is fetched.
"""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import ReceiptsServer

DESTINATION = 'https://destination.example'

# What a browser sends. Playwright's own client (and headless Chromium: "HeadlessChrome") would be
# counted as a bot by the site, and a person's click is what these tests count.
PERSON = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


def _follow(page: Page, url: str):
    """A visitor opening a short link: one request, the redirect not followed. Sent as a person
    (see `PERSON`), so it counts as a click."""
    response = page.request.get(url, max_redirects=0, headers={'User-Agent': PERSON})
    assert response.status == 302, response.status
    # 302, never 301 (the destination can be edited), and never cached.
    assert 'no-store' in response.headers['cache-control']
    return response.headers['location']


def _link_id(page: Page) -> str:
    match = re.search(r'/links/([0-9a-f-]{36})/', page.url)
    assert match, page.url
    return match.group(1)


def test_create_follow_edit_disable_and_read_the_stats(page: Page, receipts_server: ReceiptsServer):
    url = receipts_server.url
    receipts_server.login(page)

    # Create a link with a custom alias: it costs a credit, so the first submit only asks.
    page.goto(f'{url}/links/new/')
    page.fill('#id_destination', f'{DESTINATION}/first')
    page.fill('#id_title', 'Landing page')
    page.fill('#id_alias', 'e2e-promo')
    expect(page.locator('main')).to_contain_text('/s/e2e-promo')
    page.click('button:has-text("Create link")')
    expect(page.locator('#cost-confirm')).to_contain_text('This costs 1 credit')
    page.click('#cost-confirm button')
    expect(page.locator('body')).to_contain_text('Link created.')
    expect(page.locator('h1')).to_contain_text('Landing page')
    expect(page.locator('main')).to_contain_text(f'{url}/s/e2e-promo')
    link_id = _link_id(page)

    # A visitor follows it (the alias is case-insensitive) and lands on the destination.
    assert _follow(page, f'{url}/s/E2E-Promo') == f'{DESTINATION}/first'

    # Edit the destination: the same short link now goes elsewhere.
    page.goto(f'{url}/links/{link_id}/edit/')
    expect(page.locator('#id_alias')).to_have_count(0)  # the code can't be changed
    page.fill('#id_destination', f'{DESTINATION}/second')
    page.click('button:has-text("Save changes")')
    expect(page.locator('body')).to_contain_text('Link saved.')
    expect(page.locator('main')).to_contain_text(f'{DESTINATION}/second')
    assert _follow(page, f'{url}/s/e2e-promo') == f'{DESTINATION}/second'

    # The preview shows where it goes without following it, and counts nothing.
    page.goto(f'{url}/s/e2e-promo+')
    expect(page.locator('body')).to_contain_text('destination.example')

    # The stats page counts the two visits; the detailed part is a paid extra, offered here.
    page.goto(f'{url}/links/{link_id}/stats/')
    expect(page.locator('dl > div', has_text='Clicks').first).to_contain_text('2')
    expect(page.locator('dl > div', has_text='Unique visitors')).to_contain_text('1')
    expect(page.locator('main')).to_contain_text('Detailed analytics')

    # Disable it: visitors get the neutral page (a 404 that never says why), and it counts nothing.
    page.goto(f'{url}/links/{link_id}/')
    page.click('button:has-text("Disable")')
    expect(page.locator('body')).to_contain_text('Link disabled.')
    response = page.goto(f'{url}/s/e2e-promo')
    assert response is not None and response.status == 404
    expect(page.locator('h1')).to_have_text("This link isn't available")
    expect(page.locator('body')).not_to_contain_text('disabled')
    page.goto(f'{url}/links/{link_id}/stats/')
    expect(page.locator('dl > div', has_text='Clicks').first).to_contain_text('2')

    # Enable it again (it was one of the free active links: no charge) and it works once more.
    page.goto(f'{url}/links/{link_id}/')
    page.click('button:has-text("Enable")')
    expect(page.locator('body')).to_contain_text('Link enabled.')
    assert _follow(page, f'{url}/s/e2e-promo') == f'{DESTINATION}/second'


def test_the_dashboard_lists_the_link_and_a_refused_form_explains_itself(
    page: Page, receipts_server: ReceiptsServer
):
    url = receipts_server.url
    receipts_server.login(page)

    page.goto(f'{url}/links/new/')
    page.fill('#id_destination', f'{DESTINATION}/dashboard')
    page.fill('#id_title', 'Dashboard link')
    page.click('button:has-text("Create link")')
    expect(page.locator('body')).to_contain_text('Link created.')

    page.goto(f'{url}/links/')
    expect(page.locator('#link-list')).to_contain_text('Dashboard link')
    page.fill('input[name="q"]', 'no such link')
    expect(page.locator('#link-list')).not_to_contain_text('Dashboard link')
    expect(page).to_have_url(re.compile(r'\?q=no\+such\+link'))

    # A destination the site refuses shows the message in the form's slot and creates nothing.
    page.goto(f'{url}/links/new/')
    page.fill('#id_destination', 'https://127.0.0.1/admin')
    page.click('button:has-text("Create link")')
    expect(page.locator('#link-form-msg')).to_contain_text('Destination:')
    expect(page).to_have_url(f'{url}/links/new/')


def test_closing_the_delete_dialog_returns_focus_to_the_row_menu(
    page: Page, receipts_server: ReceiptsServer
):
    """Delete is a menu item, and the menu closes when it is clicked: closing the dialog must put
    focus back on the menu's button, not on the hidden item (which would leave it on <body>)."""
    url = receipts_server.url
    receipts_server.login(page)

    page.goto(f'{url}/links/new/')
    page.fill('#id_destination', f'{DESTINATION}/focus')
    page.fill('#id_title', 'Focus link')
    page.click('button:has-text("Create link")')
    expect(page.locator('body')).to_contain_text('Link created.')

    page.goto(f'{url}/links/?q=Focus+link')
    trigger = page.locator('#link-list button[aria-haspopup="menu"]').first
    trigger.click()
    page.locator('[role="menu"] button:has-text("Delete")').first.click()
    expect(page.locator('#confirm-modal-title')).to_have_text('Delete this link?')
    page.keyboard.press('Escape')

    expect(page.locator('#confirm-modal-title')).to_be_hidden()
    expect(trigger).to_be_focused()
