"""The site shell of the frontend refresh (#214): the stylesheet and fonts, the header's current
product, the bell badge's count, flash messages and the font cache header."""

import re
from pathlib import Path
from wsgiref.headers import Headers

import pytest
from django.contrib import messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse

from config.static_headers import FONT_CACHE_CONTROL, add_static_headers

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

CORE_STATIC = Path(__file__).resolve().parents[1] / 'static' / 'core'
SITE_CSS = CORE_STATIC / 'css' / 'site.css'


def _header(html: str) -> str:
    return html[html.index('<header') : html.index('</header>')]


def test_site_css_is_linked_and_the_text_font_preloaded(client):
    html = client.get(reverse('core:home')).content.decode()
    assert 'core/css/site.css' in html
    assert re.search(
        r'<link rel="preload" href="[^"]*figtree-latin\.woff2" as="font"[^>]*crossorigin', html
    )


def test_every_font_in_site_css_exists():
    urls = re.findall(r"url\('([^']+\.woff2)'\)", SITE_CSS.read_text())
    assert urls, 'site.css declares no fonts'
    for url in urls:
        assert (SITE_CSS.parent / url).resolve().is_file(), url


def test_entrance_animations_do_not_hold_their_end_state():
    """A forwards fill would beat the cards' hover lift and a flash message's fade-out."""
    for rule in re.findall(r'\.animate-[\w-]+\s*\{[^}]*\}', SITE_CSS.read_text()):
        assert ' both' not in rule and ' forwards' not in rule, rule


def test_theme_crossfade_is_off_under_reduced_motion():
    css = SITE_CSS.read_text()
    block = css[css.index('@media (prefers-reduced-motion: no-preference)') :]
    assert 'html.theming' in block.split('@media (prefers-reduced-motion: reduce)')[0]
    assert 'html.theming' not in css.split('@media (prefers-reduced-motion: no-preference)')[0]


def test_header_marks_the_product_the_visitor_is_in(client, user):
    client.force_login(user)
    header = _header(client.get(reverse('todo:today')).content.decode())
    assert header.count('aria-current="true"') == 1
    assert re.search(r'<a href="/todo/"[^>]*aria-current="true"', header)


def test_header_marks_no_product_on_the_landing_page(client):
    header = _header(client.get(reverse('core:home')).content.decode())
    assert 'aria-current' not in header


def test_header_uses_outlined_icons_for_the_account_menu_and_bell(client, user):
    client.force_login(user)
    header = _header(client.get(reverse('core:home')).content.decode())
    assert 'far fa-user-circle' in header
    assert 'far fa-bell' in header


@pytest.mark.parametrize('count', [0, 3, 120])
def test_badge_carries_its_count(count):
    html = render_to_string('core/partials/notification_badge.html', {'count': count})
    assert f'data-count="{count}"' in html
    assert (' hidden' in html) == (count == 0)


def _flash(client, level, text):
    request = HttpRequest()
    storage = CookieStorage(request)
    storage.add(level, text)
    response = HttpResponse()
    storage.update(response)
    client.cookies.update(response.cookies)


def test_flash_message_can_be_dismissed_and_is_a_status(client):
    _flash(client, messages.SUCCESS, 'Saved.')
    html = client.get(reverse('core:home')).content.decode()
    assert 'role="status"' in html
    assert 'Saved.' in html
    assert 'aria-label="Dismiss"' in html


def test_error_flash_message_is_an_alert(client):
    _flash(client, messages.ERROR, 'It broke.')
    html = client.get(reverse('core:home')).content.decode()
    assert 'role="alert"' in html
    assert 'It broke.' in html


def test_flash_message_text_is_escaped(client):
    _flash(client, messages.SUCCESS, '<script>alert(1)</script>')
    html = client.get(reverse('core:home')).content.decode()
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_shared_scripts_are_loaded_and_the_skeleton_rows_exist(client):
    html = client.get(reverse('core:home')).content.decode()
    assert 'core/js/tabs.js' in html
    assert 'core/js/skeleton.js' in html
    assert '<template id="skeleton-rows">' in html
    assert html.count('class="flex items-center gap-4 px-4 py-3 sm:px-6"') == 3


def _template_files():
    root = Path(__file__).resolve().parents[2]
    return [
        path
        for path in root.glob('*/templates/**/*.html')
        if '/templates/admin/' not in path.as_posix()
    ]


@pytest.mark.parametrize(
    'stale',
    [
        'hover:opacity-90',
        'focus:ring-',
        'focus:outline-none',
        'text-brand-primary',
        'sm:rounded-md',
    ],
)
def test_templates_do_not_bring_back_the_old_class_patterns(stale):
    """The refresh took these out: the site draws focus (site.css), buttons darken instead of fading, and
    brand-coloured text is sage-600 (docs/ui-design/README.md)."""
    offenders = [
        f'{path.relative_to(path.parents[3])}'
        for path in _template_files()
        if stale in path.read_text()
        # the checkbox in the confirm modal and similar only ever use accent-sage-500 now
    ]
    assert not offenders, offenders


def test_field_focus_does_not_erase_an_error_border():
    css = SITE_CSS.read_text()
    assert ":not([class*='border-red-'], [aria-invalid='true'])" in css
    assert "[class*='border-red-']" in css


def test_the_segmented_control_is_styled_from_its_aria_state():
    css = SITE_CSS.read_text()
    assert ".segmented > [aria-selected='true']" in css
    assert ".segmented > [aria-current='true']" in css
    root = Path(__file__).resolve().parents[2]
    for name in (
        'file_transfer/templates/file_transfer/dashboard.html',
        'short_url/templates/short_url/stats.html',
    ):
        html = (root / name).read_text()
        assert 'class="segmented ' in html, name
        assert 'shadow-sm dark:bg-gray-700' not in html, (
            f'{name}: the selected look must come from aria, not classes'
        )


def test_underline_tabs_do_not_press_like_buttons():
    root = Path(__file__).resolve().parents[2]
    for name in (
        'receipts/templates/receipts/partials/tabs.html',
        'receipts/templates/receipts/partials/vendor_tabs.html',
        'short_url/templates/short_url/partials/tabs.html',
    ):
        assert 'btn inline-flex items-center border-b-2' not in (root / name).read_text(), name


def test_the_skeleton_keeps_the_lists_content_in_the_dom():
    """The list holds hidden inputs the next request reads (status, sort, vendor): the rows go beside it."""
    css = SITE_CSS.read_text()
    assert '[data-list][data-skeleton] > :not([data-skeleton-rows]) { display: none; }' in css
    script = (CORE_STATIC / 'js' / 'skeleton.js').read_text()
    assert 'target.innerHTML =' not in script, 'the list must not be rebuilt from a string'


def test_every_underline_tab_bar_is_marked_for_the_sliding_indicator():
    root = Path(__file__).resolve().parents[2]
    for name in (
        'receipts/templates/receipts/partials/tabs.html',
        'receipts/templates/receipts/partials/vendor_tabs.html',
        'short_url/templates/short_url/partials/tabs.html',
    ):
        assert '<nav data-tabs ' in (root / name).read_text(), name


class TestFontCacheHeader:
    def test_fonts_are_cached_for_a_year(self):
        headers = Headers([('Cache-Control', 'max-age=60, public')])
        add_static_headers(
            headers, '/x/core/fonts/figtree-latin.woff2', '/core/fonts/figtree-latin.woff2'
        )
        assert headers.get_all('Cache-Control') == [FONT_CACHE_CONTROL]

    def test_other_static_files_keep_whitenoises_default(self):
        headers = Headers([('Cache-Control', 'max-age=60, public')])
        add_static_headers(headers, '/x/core/css/site.css', '/core/css/site.css')
        add_static_headers(headers, '/x/core/js/components.js', '/core/js/components.js')
        assert headers.get_all('Cache-Control') == ['max-age=60, public']

    def test_it_is_wired_into_the_settings(self, settings):
        assert settings.WHITENOISE_ADD_HEADERS_FUNCTION is add_static_headers


def test_the_confirm_modal_title_is_not_a_heading_but_names_the_dialog():
    """The modal sits in the page before its content and its title is empty until opened: a heading
    would show up in the outline as an empty h3 after the h1. The dialog is named by `aria-labelledby`."""
    html = render_to_string('core/partials/confirm_modal.html')

    assert '<h3' not in html
    assert 'aria-labelledby="confirm-modal-title"' in html
    assert '<p id="confirm-modal-title"' in html
