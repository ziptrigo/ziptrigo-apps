"""The notification views (issue #169): the bell's count, the dropdown, the notifications page and
the actions on them -- ownership (404, never 403), counts, the read semantics, the redirects."""

import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core.models import Notification
from apps.core.services import notifications
from apps.core.views.notifications import PAGE_SIZE

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def other(db):
    return UserFactory()


@pytest.fixture
def web(client, user):
    client.force_login(user)
    return client


def make(user, title='Hello', **kwargs):
    kwargs.setdefault('source', 'test')
    return notifications.notify(user, title=title, **kwargs)


def badge_count(html: str) -> int:
    """The count a response puts in the badge: the number spoken to screen readers, 0 when empty."""
    assert 'id="notification-badge"' in html, 'no badge in the response'
    spoken = re.search(r'(\d+) new notification', html)
    return int(spoken.group(1)) if spoken else 0


def badge_tag(html: str) -> str:
    match = re.search(r'<span id="notification-badge"[^>]*>', html, re.S)
    assert match, 'no badge in the response'
    return match.group(0)


def row(html: str, title: str) -> str:
    """The `<li>` of the notification with this title."""
    [item] = [
        li for li in re.findall(r'<li .*?</li>', html, re.S) if f'>{title}<' in li or title in li
    ]
    return item


class TestSignedOut:
    @pytest.mark.parametrize(
        'name, args, method',
        [
            ('core:notifications', [], 'get'),
            ('core:notification-list', [], 'get'),
            ('core:notification-bell', [], 'get'),
            ('core:notification-dropdown', [], 'post'),
            ('core:notification-read-all', [], 'post'),
            ('core:notification-read', [uuid4()], 'post'),
            ('core:notification-dismiss', [uuid4()], 'post'),
            ('core:notification-open', [uuid4()], 'get'),
        ],
    )
    def test_everything_needs_a_login(self, client, name, args, method):
        response = getattr(client, method)(reverse(name, args=args))

        assert response.status_code == 302
        assert response['Location'].startswith(reverse('accounts:login'))

    @pytest.mark.parametrize(
        'name, method',
        [('core:notification-bell', 'get'), ('core:notification-dropdown', 'post')],
    )
    def test_the_background_fragments_answer_htmx_with_nothing_to_swap(self, client, name, method):
        response = getattr(client, method)(reverse(name), **HX)

        assert response.status_code == 204
        assert response.content == b''


class TestMethods:
    @pytest.mark.parametrize(
        'name, args',
        [
            ('core:notification-dropdown', []),
            ('core:notification-read-all', []),
            ('core:notification-read', [uuid4()]),
            ('core:notification-dismiss', [uuid4()]),
        ],
    )
    def test_mutating_endpoints_refuse_get(self, web, name, args):
        assert web.get(reverse(name, args=args)).status_code == 405

    @pytest.mark.parametrize(
        'name, args',
        [
            ('core:notifications', []),
            ('core:notification-list', []),
            ('core:notification-bell', []),
            ('core:notification-open', [uuid4()]),
        ],
    )
    def test_reading_endpoints_refuse_post(self, web, name, args):
        assert web.post(reverse(name, args=args)).status_code == 405

    @pytest.mark.parametrize(
        'name, args',
        [
            ('core:notification-dropdown', []),
            ('core:notification-read-all', []),
            ('core:notification-read', 'one'),
            ('core:notification-dismiss', 'one'),
        ],
    )
    def test_posts_need_the_csrf_token(self, user, name, args):
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        args = [make(user).pk] if args == 'one' else args

        response = client.post(reverse(name, args=args))

        assert response.status_code == 403


class TestBell:
    def test_shows_the_count_of_new_ones(self, web, user, other):
        make(user)
        make(user)
        make(other)

        html = web.get(reverse('core:notification-bell')).content.decode()

        assert badge_count(html) == 2
        assert '>2<' in html
        assert ' hidden' not in badge_tag(html)

    def test_is_hidden_but_still_polling_when_there_is_nothing_new(self, web):
        html = web.get(reverse('core:notification-bell')).content.decode()

        tag = badge_tag(html)
        assert ' hidden' in tag
        assert 'hx-trigger="every 60s"' in tag
        assert f'hx-get="{reverse("core:notification-bell")}"' in tag
        assert badge_count(html) == 0

    def test_read_ones_are_not_counted(self, web, user):
        make(user)
        notifications.mark_all_read(user)

        assert badge_count(web.get(reverse('core:notification-bell')).content.decode()) == 0

    def test_a_large_count_is_capped(self, web, user):
        Notification.objects.bulk_create(
            [Notification(user_id=user.pk, source='t', title=str(n)) for n in range(120)]
        )

        html = web.get(reverse('core:notification-bell')).content.decode()

        assert '99+' in html
        assert '>120<' not in html
        assert badge_count(html) == 120  # still spoken exactly

    def test_is_cheap(self, web, django_assert_max_num_queries):
        # The session and the user, plus the one COUNT.
        with django_assert_max_num_queries(3):
            web.get(reverse('core:notification-bell'))


class TestDropdown:
    def open(self, web, **extra):
        return web.post(reverse('core:notification-dropdown'), **HX, **extra)

    def test_lists_the_newest_first(self, web, user):
        make(user, title='first')
        make(user, title='second')

        html = self.open(web).content.decode()

        assert html.index('second') < html.index('first')

    def test_opening_marks_what_it_lists_as_read_and_clears_the_badge(self, web, user):
        make(user)
        make(user)

        html = self.open(web).content.decode()

        assert notifications.unread_count(user) == 0
        assert badge_count(html) == 0
        assert 'hx-swap-oob="true"' in html

    def test_what_was_new_still_looks_new_in_that_response(self, web, user):
        make(user, title='fresh')
        old = make(user, title='seen before')
        notifications.mark_read(user, [old.pk])

        html = self.open(web).content.decode()

        assert 'font-semibold' in row(html, 'fresh')
        assert 'font-semibold' not in row(html, 'seen before')

    def test_read_ones_stay_listed_until_opened_or_dismissed(self, web, user):
        keep = make(user, title='stays')
        opened = make(user, title='clicked')
        dismissed = make(user, title='removed')
        notifications.mark_opened(user, opened.pk)
        notifications.dismiss(user, dismissed.pk)
        self.open(web)  # reads `keep`

        html = self.open(web).content.decode()

        assert 'stays' in html
        assert 'clicked' not in html
        assert 'removed' not in html
        keep.refresh_from_db()
        assert keep.read_at is not None

    def test_lists_only_the_most_recent_and_the_rest_stay_counted(self, web, user):
        for n in range(notifications.DROPDOWN_LIMIT + 2):
            make(user, title=f'n{n:02d}')

        html = self.open(web).content.decode()

        assert len(re.findall(r'<li[ >]', html)) == notifications.DROPDOWN_LIMIT
        assert 'n11' in html
        assert 'n01' not in html
        assert notifications.unread_count(user) == 2
        assert badge_count(html) == 2

    def test_the_others_notifications_are_not_there(self, web, other):
        make(other, title='secret')

        html = self.open(web).content.decode()

        assert 'secret' not in html
        assert 'No new notifications.' in html

    def test_links_open_the_notification_and_the_page(self, web, user):
        n = make(user, url='/transfer/')

        html = self.open(web).content.decode()

        assert f'href="{reverse("core:notification-open", args=[n.pk])}"' in html
        assert f'href="{reverse("core:notifications")}"' in html
        assert 'Mark all as read' in html or notifications.unread_count(user) == 0

    def test_a_notification_without_a_url_is_not_a_link(self, web, user):
        n = make(user, title='plain')

        html = self.open(web).content.decode()

        assert 'plain' in html
        assert reverse('core:notification-open', args=[n.pk]) not in html

    def test_everything_the_caller_wrote_is_escaped(self, web, user):
        make(user, title='<script>alert(1)</script>', body='<img src=x onerror=alert(2)>')

        html = self.open(web).content.decode()

        assert '<script>alert' not in html
        assert '<img src=x' not in html
        assert '&lt;script&gt;' in html

    def test_the_number_of_queries_does_not_grow_with_the_list(self, web, user):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        make(user)
        with CaptureQueriesContext(connection) as few:
            self.open(web)
        for _ in range(8):
            make(user)
        with CaptureQueriesContext(connection) as many:
            self.open(web)

        assert len(many) <= len(few) + 1  # the update that marks them read


class TestPage:
    def test_lists_the_users_notifications_newest_first(self, web, user, other):
        make(user, title='older')
        make(user, title='newer')
        make(other, title='theirs')

        html = web.get(reverse('core:notifications')).content.decode()

        assert html.index('newer') < html.index('older')
        assert 'theirs' not in html
        assert '<title>Notifications - ZipTrigo</title>' in html

    def test_hides_dismissed_ones_but_keeps_opened_and_read_ones(self, web, user):
        gone = make(user, title='dismissed one')
        opened = make(user, title='opened one')
        read = make(user, title='read one')
        notifications.dismiss(user, gone.pk)
        notifications.mark_opened(user, opened.pk)
        notifications.mark_read(user, [read.pk])

        html = web.get(reverse('core:notifications')).content.decode()

        assert 'dismissed one' not in html
        assert 'opened one' in html
        assert 'read one' in html

    def test_read_ones_look_different_from_new_ones(self, web, user):
        make(user, title='is new')
        read = make(user, title='is read')
        notifications.mark_read(user, [read.pk])

        html = web.get(reverse('core:notifications')).content.decode()

        assert 'font-semibold' in row(html, 'is new')
        assert 'font-semibold' not in row(html, 'is read')
        assert html.count('Mark as read:') == 1  # only the new one offers it

    def test_looking_at_the_page_does_not_mark_anything_read(self, web, user):
        make(user)

        web.get(reverse('core:notifications'))

        assert notifications.unread_count(user) == 1

    def test_is_paginated(self, web, user):
        for n in range(PAGE_SIZE + 1):
            make(user, title=f'n{n:02d}')

        first = web.get(reverse('core:notifications')).content.decode()
        second = web.get(reverse('core:notifications'), {'page': 2}).content.decode()

        assert len(re.findall(r'<li[ >]', first)) == PAGE_SIZE
        assert 'n20' in first
        assert 'n00' not in first
        assert 'Page 1 of 2' in first
        assert len(re.findall(r'<li[ >]', second)) == 1
        assert 'n00' in second

    def test_a_page_out_of_range_shows_the_last_one(self, web, user):
        make(user, title='only')

        response = web.get(reverse('core:notifications'), {'page': 99})

        assert response.status_code == 200
        assert 'only' in response.content.decode()

    def test_empty(self, web):
        html = web.get(reverse('core:notifications')).content.decode()

        assert 'No notifications yet.' in html
        assert 'all caught up' in html

    def test_shows_the_source_and_time(self, web, user):
        make(user, title='from a product', source='file_transfer')
        make(user, title='from elsewhere', source='some_new_app')

        html = web.get(reverse('core:notifications')).content.decode()

        assert 'File Transfer' in row(html, 'from a product')  # the product's own name
        assert 'Some new app' in row(html, 'from elsewhere')  # made readable
        assert 'just now' in html

    def test_the_list_partial_is_for_htmx(self, web, user):
        make(user, title='in the partial')

        html = web.get(reverse('core:notification-list'), **HX).content.decode()

        assert 'in the partial' in html
        assert '<html' not in html
        assert 'hx-swap-oob="true"' in html

    def test_the_list_partial_opened_directly_goes_to_the_page(self, web):
        response = web.get(reverse('core:notification-list'), {'page': '2'})

        assert response.status_code == 302
        assert response['Location'] == f'{reverse("core:notifications")}?page=2'


class TestMarkRead:
    def test_marks_one_and_answers_the_page_list(self, web, user):
        a, b = make(user, title='aaa'), make(user, title='bbb')

        response = web.post(
            reverse('core:notification-read', args=[a.pk]), {'scope': 'page', 'page': '1'}, **HX
        )

        assert response.status_code == 200
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.read_at is not None
        assert b.read_at is None
        html = response.content.decode()
        assert 'aaa' in html and 'bbb' in html
        assert badge_count(html) == 1

    def test_another_users_is_a_404_and_untouched(self, web, other):
        theirs = make(other)

        response = web.post(reverse('core:notification-read', args=[theirs.pk]), **HX)

        assert response.status_code == 404
        theirs.refresh_from_db()
        assert theirs.read_at is None

    def test_without_htmx_it_redirects_to_the_page(self, web, user):
        a = make(user)

        response = web.post(reverse('core:notification-read', args=[a.pk]), {'page': '2'})

        assert response.status_code == 302
        assert response['Location'] == f'{reverse("core:notifications")}?page=2'
        a.refresh_from_db()
        assert a.read_at is not None


class TestMarkAllRead:
    def test_marks_everything_including_what_the_dropdown_left_out(self, web, user, other):
        for _ in range(notifications.DROPDOWN_LIMIT + 5):
            make(user)
        theirs = make(other)

        response = web.post(reverse('core:notification-read-all'), {'scope': 'page'}, **HX)

        assert response.status_code == 200
        assert notifications.unread_count(user) == 0
        assert badge_count(response.content.decode()) == 0
        theirs.refresh_from_db()
        assert theirs.read_at is None

    def test_in_the_dropdown_the_notifications_stay_listed(self, web, user):
        make(user, title='still here')

        response = web.post(reverse('core:notification-read-all'), {'scope': 'dropdown'}, **HX)

        assert 'still here' in response.content.decode()
        assert notifications.unread_count(user) == 0

    def test_without_htmx_it_redirects_to_the_page(self, web):
        response = web.post(reverse('core:notification-read-all'))

        assert response.status_code == 302
        assert response['Location'] == reverse('core:notifications')


class TestDismiss:
    def test_hides_it_and_answers_the_list_it_came_from(self, web, user):
        gone, kept = make(user, title='gone'), make(user, title='kept')

        page = web.post(
            reverse('core:notification-dismiss', args=[gone.pk]), {'scope': 'page'}, **HX
        ).content.decode()

        assert 'gone' not in page and 'kept' in page
        assert badge_count(page) == 1
        gone.refresh_from_db()
        assert gone.dismissed_at is not None

        dropdown = web.post(
            reverse('core:notification-dismiss', args=[kept.pk]), {'scope': 'dropdown'}, **HX
        ).content.decode()

        assert 'kept' not in dropdown
        assert 'No new notifications.' in dropdown

    def test_it_is_gone_from_the_page_too(self, web, user):
        n = make(user, title='bye')
        web.post(reverse('core:notification-dismiss', args=[n.pk]), **HX)

        assert 'bye' not in web.get(reverse('core:notifications')).content.decode()

    def test_another_users_is_a_404_and_untouched(self, web, other):
        theirs = make(other)

        response = web.post(reverse('core:notification-dismiss', args=[theirs.pk]), **HX)

        assert response.status_code == 404
        theirs.refresh_from_db()
        assert theirs.dismissed_at is None

    def test_an_unknown_id_is_a_404(self, web):
        assert web.post(reverse('core:notification-dismiss', args=[uuid4()])).status_code == 404

    def test_without_htmx_it_redirects_to_the_page(self, web, user):
        n = make(user)

        response = web.post(reverse('core:notification-dismiss', args=[n.pk]))

        assert response.status_code == 302
        assert response['Location'] == reverse('core:notifications')
        n.refresh_from_db()
        assert n.dismissed_at is not None


class TestOpen:
    def test_marks_it_read_and_opened_and_redirects_to_its_url(self, web, user):
        n = make(user, url='/transfer/?x=1')

        response = web.get(reverse('core:notification-open', args=[n.pk]))

        assert response.status_code == 302
        assert response['Location'] == '/transfer/?x=1'
        n.refresh_from_db()
        assert n.read_at is not None
        assert n.opened_at is not None
        assert notifications.dropdown_notifications(user) == []

    def test_without_a_url_it_goes_to_the_page(self, web, user):
        n = make(user)

        response = web.get(reverse('core:notification-open', args=[n.pk]))

        assert response['Location'] == reverse('core:notifications')

    @pytest.mark.parametrize(
        'url',
        ['https://evil.example/', '//evil.example/', '/\\evil.example', 'javascript:alert(1)'],
    )
    def test_a_stored_url_that_is_not_a_same_site_path_is_never_followed(self, web, user, url):
        # `notify` refuses these, so write the row directly: the view must not rely on that.
        n = Notification.objects.create(user_id=user.pk, source='t', title='x', url=url)

        response = web.get(reverse('core:notification-open', args=[n.pk]))

        assert response.status_code == 302
        assert response['Location'] == reverse('core:notifications')
        n.refresh_from_db()
        assert n.opened_at is not None

    def test_another_users_is_a_404_and_untouched(self, web, other):
        theirs = make(other, url='/transfer/')

        response = web.get(reverse('core:notification-open', args=[theirs.pk]))

        assert response.status_code == 404
        theirs.refresh_from_db()
        assert (theirs.read_at, theirs.opened_at) == (None, None)

    def test_a_dismissed_one_can_still_be_followed(self, web, user):
        n = make(user, url='/transfer/')
        notifications.dismiss(user, n.pk)

        assert web.get(reverse('core:notification-open', args=[n.pk]))['Location'] == '/transfer/'


def test_old_ones_do_not_break_the_page(web, user):
    n = make(user, title='ancient')
    Notification.objects.filter(pk=n.pk).update(created_at=timezone.now() - timedelta(days=80))

    assert 'ancient' in web.get(reverse('core:notifications')).content.decode()
