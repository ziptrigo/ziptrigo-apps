"""The colour scheme picked under Appearance on the account page, end to end in a real browser: a
click applies it with no reload (the `themeChanged` listener in `core/base.html`), it follows the
account to other pages, and the solid schemes' primary button. Run on the shared e2e server
(`receipts_server`).
"""

from playwright.sync_api import Browser, Page, expect

from tests_e2e.conftest import ReceiptsServer


def set_appearance(server: ReceiptsServer, theme: str, scheme: str) -> None:
    server.shell(
        'import os\n'
        'from apps.accounts.models import User\n'
        "User.objects.filter(email=os.environ['E2E_EMAIL'])"
        f'.update(theme={theme!r}, color_scheme={scheme!r})\n'
    )


def choose(page: Page, name: str, value: str) -> None:
    """Click the radio `name`=`value` of the appearance form and wait for its save."""
    with page.expect_response(lambda r: r.url.endswith('/account/theme/') and r.status == 200):
        page.locator(f'#theme-form input[name="{name}"][value="{value}"]').check(force=True)


def primary_button_colours(page: Page) -> tuple[str, str]:
    button = page.locator('button.bg-brand-primary').first
    expect(button).to_be_visible()
    return button.evaluate(
        '(el) => { const s = getComputedStyle(el); return [s.backgroundColor, s.color]; }'
    )


def test_a_click_applies_the_scheme_without_a_reload(
    browser: Browser, receipts_server: ReceiptsServer
) -> None:
    set_appearance(receipts_server, 'light', 'sage')
    context = browser.new_context()
    page = context.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        html = page.locator('html')
        expect(html).to_have_attribute('data-scheme', 'sage')
        expect(page.locator('#theme-form input[name="color_scheme"]')).to_have_count(8)
        expect(page.locator('#theme-form input[name="color_scheme"]:checked')).to_have_value('sage')
        assert primary_button_colours(page)[0] == 'rgb(143, 168, 158)'
        page.evaluate('window.__stillHere = true')

        choose(page, 'color_scheme', 'ember')

        expect(html).to_have_attribute('data-scheme', 'ember')
        # The page was not reloaded.
        assert page.evaluate('window.__stillHere') is True
        logo = page.locator('header img[data-scheme-logo="32"]')
        expect(logo).to_have_attribute('src', '/static/core/images/schemes/ember/logo_32x32.png')
        expect(page.locator('link[rel="icon"]')).to_have_attribute(
            'href', '/static/core/images/schemes/ember/logo_32x32.png'
        )
        expect(page.locator('meta[name="theme-color"]')).to_have_attribute('content', '#e59d7e')
        assert logo.evaluate('(img) => img.complete && img.naturalWidth > 0') is True
        expect(page.locator('button.bg-brand-primary').first).to_have_css(
            'background-color', 'rgb(229, 157, 126)'
        )

        # It is stored on the account: the next page renders it from the server.
        page.goto(f'{receipts_server.url}/')
        expect(page.locator('html')).to_have_attribute('data-scheme', 'ember')
        expect(page.locator('img[data-scheme-logo="128"]')).to_have_attribute(
            'src', '/static/core/images/schemes/ember/logo_128x128.png'
        )
    finally:
        context.close()
        set_appearance(receipts_server, 'system', 'sage')


def test_the_scheme_survives_a_theme_change_and_a_new_session(
    browser: Browser, receipts_server: ReceiptsServer
) -> None:
    set_appearance(receipts_server, 'light', 'plum')
    context = browser.new_context()
    page = context.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')
        html = page.locator('html')
        expect(html).to_have_attribute('data-scheme', 'plum')

        choose(page, 'theme', 'dark')

        expect(html).to_have_class('dark')
        expect(html).to_have_attribute('data-scheme', 'plum')
        expect(page.locator('#theme-form input[name="color_scheme"]:checked')).to_have_value('plum')

        other = browser.new_context()
        try:
            other_page = other.new_page()
            receipts_server.login(other_page)
            expect(other_page.locator('html')).to_have_attribute('data-scheme', 'plum')
        finally:
            other.close()
    finally:
        context.close()
        set_appearance(receipts_server, 'system', 'sage')


def test_a_solid_scheme_has_a_dark_button_in_light_and_the_common_one_in_dark(
    browser: Browser, receipts_server: ReceiptsServer
) -> None:
    set_appearance(receipts_server, 'light', 'midnight')
    context = browser.new_context()
    page = context.new_page()
    try:
        receipts_server.login(page)
        page.goto(f'{receipts_server.url}/account/')

        # Light: accent 600 with a white label, and the selected theme option matches it.
        assert primary_button_colours(page) == ['rgb(54, 76, 140)', 'rgb(255, 255, 255)']
        selected = page.locator('#theme-form input[name="theme"]:checked + span')
        expect(selected).to_have_css('background-color', 'rgb(54, 76, 140)')
        expect(selected).to_have_css('color', 'rgb(255, 255, 255)')

        choose(page, 'theme', 'dark')

        # Dark: the common recipe, accent 300 with the dark label.
        button = page.locator('button.bg-brand-primary').first
        expect(button).to_have_css('background-color', 'rgb(143, 165, 220)')
        expect(button).to_have_css('color', 'rgb(26, 28, 39)')
    finally:
        context.close()
        set_appearance(receipts_server, 'system', 'sage')


def test_a_visitor_always_gets_sage(browser: Browser, receipts_server: ReceiptsServer) -> None:
    set_appearance(receipts_server, 'light', 'honey')
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(f'{receipts_server.url}/account/login/')
        expect(page.locator('html')).to_have_attribute('data-scheme', 'sage')
    finally:
        context.close()
        set_appearance(receipts_server, 'system', 'sage')
