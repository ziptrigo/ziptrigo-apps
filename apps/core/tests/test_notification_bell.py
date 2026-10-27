"""The bell in the site header (issue #169): shown to signed-in users only, and free for visitors."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.services import notifications

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


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
        assert 'aria-label="Notifications"' in html
        assert f'hx-post="{reverse("core:notification-dropdown")}"' in html
        assert 'id="notification-dropdown-body"' in html


def test_the_bell_polls_for_its_count_every_minute(client, user):
    client.force_login(user)

    html = client.get(reverse('core:home')).content.decode()

    assert f'hx-get="{reverse("core:notification-bell")}"' in html
    assert 'hx-trigger="every 60s"' in html


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
