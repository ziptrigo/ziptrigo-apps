"""The per-user time zone (issue #177), end to end in a real browser.

The browser reports a zone of its own (`timezone_id`); a signed-in account with none gets it saved
without asking, and the account page offers it again when the saved zone is another one.
"""

from playwright.sync_api import Browser, expect

from tests_e2e.conftest import ReceiptsServer


def test_browser_zone_is_saved_once_and_offered_on_the_account_page(
    browser: Browser, receipts_server: ReceiptsServer
):
    # The seeded account is shared, and any earlier test that signed in saved its browser's zone,
    # so start from an account with none.
    receipts_server.shell(
        'import os\n'
        'from apps.accounts.models import User\n'
        "User.objects.filter(email=os.environ['E2E_EMAIL']).update(timezone='')\n"
    )
    context = browser.new_context(timezone_id='Europe/Lisbon')
    page = context.new_page()
    try:
        # The account has no zone yet, so the page the login lands on posts the browser's, once
        # per browser session.
        with page.expect_response(lambda r: r.url.endswith('/account/timezone/')) as detection:
            receipts_server.login(page)
        assert detection.value.status == 204

        page.goto(f'{receipts_server.url}/account/')
        expect(page.locator('html')).to_have_attribute('data-user-timezone', 'Europe/Lisbon')
        select = page.locator('#profile-timezone')
        expect(select).to_have_value('Europe/Lisbon')
        expect(page.locator('#timezone-detected')).to_be_hidden()

        # Choosing another zone offers the detected one again; "Use it" picks it back.
        select.select_option('Asia/Tokyo')
        expect(page.locator('#timezone-detected')).to_be_visible()
        expect(page.locator('[data-timezone-detected]')).to_have_text('Europe/Lisbon')
        page.click('[data-timezone-use]')
        expect(select).to_have_value('Europe/Lisbon')
        expect(page.locator('#timezone-detected')).to_be_hidden()

        # A saved zone is changed on the account page, and the hint comes back after the htmx swap.
        select.select_option('Asia/Tokyo')
        page.click('#profile-form button[type="submit"]')
        expect(page.locator('#profile-form')).to_contain_text('Profile updated successfully.')
        expect(page.locator('#profile-timezone')).to_have_value('Asia/Tokyo')
        expect(page.locator('#timezone-detected')).to_be_visible()

        # Detection never overrides it: a new browser session (a new context) keeps Tokyo.
        other = browser.new_context(timezone_id='Europe/Lisbon')
        other_page = other.new_page()
        receipts_server.login(other_page)
        other_page.goto(f'{receipts_server.url}/account/')
        expect(other_page.locator('html')).to_have_attribute('data-user-timezone', 'Asia/Tokyo')
        expect(other_page.locator('#profile-timezone')).to_have_value('Asia/Tokyo')
        other.close()
    finally:
        context.close()
        # The seeded account is shared by the other e2e tests; leave it as it was found.
        receipts_server.shell(
            'import os\n'
            'from apps.accounts.models import User\n'
            "User.objects.filter(email=os.environ['E2E_EMAIL']).update(timezone='')\n"
        )


def test_detection_from_the_account_page_is_not_undone_by_saving_the_name(
    browser: Browser, receipts_server: ReceiptsServer
):
    """The page is rendered with "Not set", the browser's zone is saved from that very page, and
    the visitor then saves their name: the zone must stay."""
    context = browser.new_context(timezone_id='Europe/Lisbon')
    page = context.new_page()
    original_name = ''
    try:
        receipts_server.login(page)
        # Back to an account with no zone and no detection yet in this session.
        receipts_server.shell(
            'import os\n'
            'from apps.accounts.models import User\n'
            "User.objects.filter(email=os.environ['E2E_EMAIL']).update(timezone='')\n"
        )
        page.evaluate('sessionStorage.clear()')

        with page.expect_response(lambda r: r.url.endswith('/account/timezone/')) as detection:
            page.goto(f'{receipts_server.url}/account/')
        assert detection.value.status == 204

        # The select follows the saved zone instead of staying on "Not set".
        select = page.locator('#profile-timezone')
        expect(select).to_have_value('Europe/Lisbon')
        expect(page.locator('#timezone-detected')).to_be_hidden()

        # Even with the select put back on "Not set" (as the page was rendered), saving the name
        # leaves the zone alone, because it is the zone the form was rendered with.
        select.select_option('')
        original_name = page.input_value('#profile-name')
        page.fill('#profile-name', 'Renamed Account')
        page.click('#profile-form button[type="submit"]')
        expect(page.locator('#profile-form')).to_contain_text('Profile updated successfully.')
        expect(page.locator('#profile-timezone')).to_have_value('Europe/Lisbon')
    finally:
        context.close()
        receipts_server.shell(
            'import os\n'
            'from apps.accounts.models import User\n'
            "User.objects.filter(email=os.environ['E2E_EMAIL']).update(timezone='', name=%r)\n"
            % original_name
        )
