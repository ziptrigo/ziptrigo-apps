"""The bell in the site header (issue #169): shown to signed-in users only, and free for visitors."""

from html.parser import HTMLParser

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.services import notifications

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


class _BellParser(HTMLParser):
    """Reads the bell's <button> the way the accessible name computation does: its `aria-*`
    attributes, and the text of its content without what is `aria-hidden`."""

    def __init__(self):
        super().__init__()
        self.attrs: dict[str, str | None] = {}
        self.parts: list[str] = []
        self._stack: list[bool] = []  # per open element inside the button: is it aria-hidden?
        self._in_button = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'button' and attrs.get('id') == 'notification-bell':
            self._in_button = True
            self.attrs = attrs
        elif self._in_button:
            self._stack.append(attrs.get('aria-hidden') == 'true')

    def handle_endtag(self, tag):
        if not self._in_button:
            return
        if tag == 'button' and not self._stack:
            self._in_button = False
        elif self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._in_button and not any(self._stack):
            self.parts.append(data)

    @property
    def accessible_name(self) -> str:
        return ' '.join(' '.join(self.parts).split())


def read_bell(html: str) -> _BellParser:
    parser = _BellParser()
    parser.feed(html)
    return parser


def test_anonymous_visitors_get_no_bell(client):
    html = client.get(reverse('core:home')).content.decode()

    assert 'id="notification-bell"' not in html
    assert 'notification-badge' not in html
    assert reverse('core:notification-bell') not in html


def test_anonymous_pages_do_not_touch_the_notifications_table(client):
    with CaptureQueriesContext(connection) as queries:
        client.get(reverse('core:home'))
        client.get(reverse('accounts:login'))

    assert not [q for q in queries if 'core_notification' in q['sql']]


def test_signed_in_users_get_the_bell_on_every_page(client, user):
    client.force_login(user)

    for url in (reverse('core:home'), reverse('feedback:submit'), reverse('core:notifications')):
        html = client.get(url).content.decode()

        assert 'id="notification-bell"' in html
        assert f'hx-post="{reverse("core:notification-dropdown")}"' in html
        assert 'id="notification-dropdown-body"' in html


def test_the_bell_polls_for_its_count_every_minute_while_the_tab_is_visible(client, user):
    client.force_login(user)

    html = client.get(reverse('core:home')).content.decode()

    assert f'hx-get="{reverse("core:notification-bell")}"' in html
    assert "every 60s [document.visibilityState === 'visible']" in html
    # ...and fetches once as soon as a hidden tab becomes visible again.
    assert "visibilitychange[document.visibilityState === 'visible'] from:document" in html


def test_the_buttons_accessible_name_includes_the_unread_count(client, user):
    notifications.notify(user, source='t', title='one')
    notifications.notify(user, source='t', title='two')
    client.force_login(user)

    bell = read_bell(client.get(reverse('core:home')).content.decode())

    # An aria-label would replace the name made from the content, hiding the badge's sr-only text.
    assert 'aria-label' not in bell.attrs
    assert 'aria-labelledby' not in bell.attrs
    assert bell.accessible_name == 'Notifications 2 new notifications'  # the digits are aria-hidden


def test_the_buttons_accessible_name_is_just_notifications_with_nothing_new(client, user):
    client.force_login(user)

    bell = read_bell(client.get(reverse('core:home')).content.decode())

    assert bell.accessible_name == 'Notifications'


def test_the_polled_badge_carries_the_count_for_the_button_name(client, user):
    """The polled badge replaces the one inside the button (same id), and carries the sr-only
    sentence the button's name is made from."""
    notifications.notify(user, source='t', title='one')
    client.force_login(user)

    html = client.get(reverse('core:notification-bell'), HTTP_HX_REQUEST='true').content.decode()

    assert 'id="notification-badge"' in html
    assert '<span class="sr-only">1 new notification</span>' in html


def test_the_out_of_band_badge_keeps_the_polling_element_and_its_id(client, user):
    notifications.notify(user, source='t', title='one')
    client.force_login(user)

    html = client.post(
        reverse('core:notification-read-all'),
        {'scope': 'page'},
        HTTP_HX_REQUEST='true',
    ).content.decode()

    assert 'id="notification-badge"' in html
    assert 'hx-swap-oob="true"' in html
    assert 'hx-get="' + reverse('core:notification-bell') + '"' in html


def test_the_button_points_at_its_panel_without_claiming_a_menu(client, user):
    client.force_login(user)

    html = client.get(reverse('core:home')).content.decode()
    bell = read_bell(html)

    assert bell.attrs['aria-controls'] == 'notification-dropdown'
    assert 'id="notification-dropdown"' in html
    assert 'aria-haspopup' not in html


def test_the_bell_comes_with_the_current_count(client, user):
    notifications.notify(user, source='t', title='one')
    notifications.notify(user, source='t', title='two')
    client.force_login(user)

    html = client.get(reverse('core:home')).content.decode()

    assert '2 new notifications' in html


def test_no_count_when_there_is_nothing_new(client, user):
    client.force_login(user)

    html = client.get(reverse('core:home')).content.decode()

    assert 'new notification' not in html
    assert 'id="notification-badge"' in html  # still there to poll


def test_the_count_is_one_query(client, user):
    client.force_login(user)
    client.get(reverse('core:home'))  # warm anything cached

    with CaptureQueriesContext(connection) as queries:
        client.get(reverse('core:home'))

    assert len([q for q in queries if 'core_notification' in q['sql']]) == 1


def test_the_header_is_positioned_for_the_dropdown_on_a_phone(client, user):
    client.force_login(user)

    html = client.get(reverse('core:home')).content.decode()

    assert '<header class="relative ' in html
    assert 'sm:right-0' in html


def test_escape_closes_the_dropdown_wherever_the_focus_is(client, user):
    """Focus can end on <body> (a swapped-out button), and a keydown there never reaches the
    wrapper, so the handler listens on the window."""
    client.force_login(user)

    html = client.get(reverse('core:home')).content.decode()

    assert '@keydown.escape.window="if (open)' in html


def test_the_lists_the_actions_swap_keep_keyboard_focus(client, user):
    client.force_login(user)

    home = client.get(reverse('core:home')).content.decode()
    page = client.get(reverse('core:notifications')).content.decode()

    assert 'id="notification-dropdown-body" aria-live="polite" data-keep-focus' in home
    assert 'id="notification-list" data-keep-focus' in page
