"""`apps.core.services.notifications` (issue #169): creating, the read/opened/dismissed states and
the scoping of every query to the requesting user."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core.models import Notification
from apps.core.models.notification import BODY_MAX_LENGTH, KEY_MAX_LENGTH, TITLE_MAX_LENGTH
from apps.core.services import notifications

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def other(db):
    return UserFactory()


def make(user, title='Hello', **kwargs):
    return notifications.notify(user, source='test', title=title, **kwargs)


class TestNotify:
    def test_creates_a_new_notification(self, user):
        notification = notifications.notify(
            user, source='file_transfer', title='Hello', body='Details', url='/transfer/'
        )

        assert Notification.objects.get() == notification
        assert notification.user_id == user.pk
        assert notification.source == 'file_transfer'
        assert (notification.title, notification.body, notification.url) == (
            'Hello',
            'Details',
            '/transfer/',
        )
        assert notification.read_at is None
        assert notification.opened_at is None
        assert notification.dismissed_at is None
        assert notification.created_at is not None

    def test_body_and_url_are_optional(self, user):
        notification = notifications.notify(user, source='test', title='Hi')

        assert (notification.body, notification.url) == ('', '')

    def test_a_user_id_will_do(self, user):
        notification = notifications.notify(user.pk, source='test', title='Hi')

        assert notification.user_id == user.pk

    def test_the_title_is_one_line(self, user):
        assert make(user, title='  two \n  lines\t').title == 'two lines'

    def test_text_is_stored_as_given_not_as_html(self, user):
        notification = make(user, title='<b>bold</b>', body='<script>x</script>')

        assert notification.title == '<b>bold</b>'
        assert notification.body == '<script>x</script>'

    def test_long_text_is_cut_with_an_ellipsis(self, user):
        notification = make(user, title='t' * 500, body='b' * 5000)

        assert len(notification.title) == TITLE_MAX_LENGTH
        assert notification.title.endswith('…')
        assert len(notification.body) == BODY_MAX_LENGTH
        assert notification.body.endswith('…')

    def test_text_at_the_limit_is_kept_whole(self, user):
        notification = make(user, title='t' * TITLE_MAX_LENGTH, body='b' * BODY_MAX_LENGTH)

        assert notification.title == 't' * TITLE_MAX_LENGTH
        assert notification.body == 'b' * BODY_MAX_LENGTH

    def test_a_body_keeps_its_line_breaks(self, user):
        assert make(user, body='one\r\ntwo').body == 'one\ntwo'

    @pytest.mark.parametrize('title', ['', '   ', '\n'])
    def test_a_title_is_required(self, user, title):
        with pytest.raises(ValueError):
            make(user, title=title)

    @pytest.mark.parametrize('source', ['', '  ', 'x' * 51])
    def test_a_source_is_required_and_bounded(self, user, source):
        with pytest.raises(ValueError):
            notifications.notify(user, source=source, title='Hi')

    @pytest.mark.parametrize(
        'url',
        [
            'https://evil.example/',
            'http://evil.example/',
            '//evil.example/',
            '///evil.example/',
            '/\\evil.example/',
            '\\\\evil.example',
            'javascript:alert(1)',
            'transfer/',
            '/has space',
            '/new\nline',
            '/' + 'a' * 500,
        ],
    )
    def test_an_url_that_is_not_a_same_site_path_is_refused(self, user, url):
        with pytest.raises(ValueError):
            make(user, url=url)
        assert not Notification.objects.exists()

    @pytest.mark.parametrize('url', ['/', '/transfer/', '/receipts/abc/?page=2#top'])
    def test_same_site_paths_are_accepted(self, user, url):
        assert make(user, url=url).url == url

    def test_the_key_is_optional_and_stored(self, user):
        assert make(user).key == ''
        assert make(user, key=' transfer:abc ').key == 'transfer:abc'

    def test_a_key_longer_than_the_column_is_refused(self, user):
        with pytest.raises(ValueError):
            make(user, key='k' * (KEY_MAX_LENGTH + 1))
        assert make(user, key='k' * KEY_MAX_LENGTH).key == 'k' * KEY_MAX_LENGTH

    def test_it_is_part_of_the_callers_transaction(self, user):
        with pytest.raises(RuntimeError), transaction.atomic():
            make(user)
            raise RuntimeError

        assert not Notification.objects.exists()


class TestIsSafeUrl:
    @pytest.mark.parametrize('url', ['', 'https://x/', '//x', '/\\x', 'x', '/a b'])
    def test_unsafe(self, url):
        assert not notifications.is_safe_url(url)

    @pytest.mark.parametrize('url', ['/', '/a', '/a/b?c=d#e'])
    def test_safe(self, url):
        assert notifications.is_safe_url(url)


class TestUnreadCount:
    def test_counts_new_notifications_of_the_user_only(self, user, other):
        make(user)
        make(user)
        make(other)

        assert notifications.unread_count(user) == 2
        assert notifications.unread_count(other) == 1

    def test_read_ones_do_not_count(self, user):
        notification = make(user)
        make(user)
        notifications.mark_read(user, [notification.pk])

        assert notifications.unread_count(user) == 1

    def test_a_dismissed_one_does_not_count_even_if_it_was_never_read(self, user):
        notification = make(user)
        Notification.objects.filter(pk=notification.pk).update(dismissed_at=timezone.now())

        assert notifications.unread_count(user) == 0

    def test_it_is_one_query(self, user, django_assert_num_queries):
        make(user)

        with django_assert_num_queries(1):
            notifications.unread_count(user)


class TestLists:
    def test_the_dropdown_lists_the_newest_first(self, user):
        old = make(user, title='old')
        new = make(user, title='new')
        Notification.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        assert notifications.dropdown_notifications(user) == [new, old]

    def test_the_dropdown_is_capped(self, user):
        for n in range(notifications.DROPDOWN_LIMIT + 3):
            make(user, title=str(n))

        assert len(notifications.dropdown_notifications(user)) == notifications.DROPDOWN_LIMIT

    def test_the_dropdown_leaves_out_dismissed_and_opened_ones_but_keeps_read_ones(self, user):
        dismissed = make(user, title='dismissed')
        opened = make(user, title='opened')
        read = make(user, title='read')
        new = make(user, title='new')
        notifications.dismiss(user, dismissed.pk)
        notifications.mark_opened(user, opened.pk)
        notifications.mark_read(user, [read.pk])

        assert set(notifications.dropdown_notifications(user)) == {read, new}

    def test_the_dropdown_is_the_users_own(self, user, other):
        make(other)

        assert notifications.dropdown_notifications(user) == []

    def test_the_page_lists_everything_not_dismissed_opened_ones_included(self, user, other):
        dismissed = make(user, title='dismissed')
        opened = make(user, title='opened')
        new = make(user, title='new')
        make(other)
        notifications.dismiss(user, dismissed.pk)
        notifications.mark_opened(user, opened.pk)

        assert set(notifications.page_notifications(user)) == {opened, new}


class TestMarkRead:
    def test_marks_the_given_ones(self, user):
        a, b = make(user), make(user)

        assert notifications.mark_read(user, [a.pk]) == 1

        a.refresh_from_db()
        b.refresh_from_db()
        assert a.read_at is not None
        assert b.read_at is None

    def test_accepts_ids_as_strings(self, user):
        a = make(user)

        assert notifications.mark_read(user, [str(a.pk)]) == 1

    def test_ids_of_someone_else_are_ignored(self, user, other):
        theirs = make(other)

        assert notifications.mark_read(user, [theirs.pk]) == 0

        theirs.refresh_from_db()
        assert theirs.read_at is None

    def test_does_not_move_an_earlier_read_time(self, user):
        a = make(user)
        earlier = timezone.now() - timedelta(days=1)
        Notification.objects.filter(pk=a.pk).update(read_at=earlier)

        assert notifications.mark_read(user, [a.pk]) == 0

        a.refresh_from_db()
        assert a.read_at == earlier

    def test_nothing_to_mark(self, user):
        assert notifications.mark_read(user, []) == 0

    def test_mark_all_read_marks_everything_new_of_the_user_only(self, user, other):
        make(user)
        make(user)
        theirs = make(other)

        assert notifications.mark_all_read(user) == 2

        assert notifications.unread_count(user) == 0
        theirs.refresh_from_db()
        assert theirs.read_at is None

    def test_mark_all_read_leaves_them_listed(self, user):
        a = make(user)

        notifications.mark_all_read(user)

        assert notifications.dropdown_notifications(user) == [a]


class TestOpenAndDismiss:
    def test_opening_marks_it_opened_and_read(self, user):
        a = make(user, url='/x/')

        opened = notifications.mark_opened(user, a.pk)

        a.refresh_from_db()
        assert opened.pk == a.pk
        assert opened.url == '/x/'
        assert a.opened_at is not None
        assert a.read_at is not None

    def test_opening_keeps_an_earlier_read_time(self, user):
        a = make(user)
        earlier = timezone.now() - timedelta(days=1)
        Notification.objects.filter(pk=a.pk).update(read_at=earlier)

        notifications.mark_opened(user, a.pk)

        a.refresh_from_db()
        assert a.read_at == earlier

    def test_opening_twice_keeps_the_first_time(self, user):
        a = make(user)
        notifications.mark_opened(user, a.pk)
        a.refresh_from_db()
        first = a.opened_at

        notifications.mark_opened(user, a.pk)

        a.refresh_from_db()
        assert a.opened_at == first

    def test_dismissing_hides_it_and_counts_it_as_read(self, user):
        a = make(user)

        notifications.dismiss(user, a.pk)

        a.refresh_from_db()
        assert a.dismissed_at is not None
        assert a.read_at is not None
        assert notifications.unread_count(user) == 0
        assert a not in notifications.page_notifications(user)

    def test_dismissing_twice_is_harmless(self, user):
        a = make(user)
        notifications.dismiss(user, a.pk)
        a.refresh_from_db()
        first = a.dismissed_at

        notifications.dismiss(user, a.pk)

        a.refresh_from_db()
        assert a.dismissed_at == first

    def test_another_users_notification_does_not_exist(self, user, other):
        theirs = make(other)

        for call in (
            notifications.get_notification,
            notifications.dismiss,
            notifications.mark_opened,
        ):
            with pytest.raises(Notification.DoesNotExist):
                call(user, theirs.pk)

        theirs.refresh_from_db()
        assert (theirs.read_at, theirs.opened_at, theirs.dismissed_at) == (None, None, None)

    def test_an_unknown_id_does_not_exist(self, user):
        with pytest.raises(Notification.DoesNotExist):
            notifications.get_notification(user, uuid4())


class TestDeleteForUrl:
    def test_deletes_the_users_notifications_for_that_url(self, user, other):
        make(user, url='/a/')
        make(user, url='/a/')
        keep = make(user, url='/b/')
        theirs = make(other, url='/a/')

        assert notifications.delete_for_url(user, '/a/') == 2

        assert set(Notification.objects.all()) == {keep, theirs}

    def test_can_be_limited_to_a_source(self, user):
        mine = notifications.notify(user, source='receipts', title='x', url='/a/')
        keep = notifications.notify(user, source='file_transfer', title='x', url='/a/')

        assert notifications.delete_for_url(user, '/a/', source='receipts') == 1

        assert list(Notification.objects.all()) == [keep]
        assert not Notification.objects.filter(pk=mine.pk).exists()

    def test_nothing_to_delete(self, user):
        assert notifications.delete_for_url(user, '/a/') == 0


class TestDeleteForKey:
    def test_deletes_only_the_notifications_with_that_key(self, user):
        gone = make(user, key='transfer:1', url='/t/')
        also_gone = make(user, key='transfer:1', url='/t/')
        other_key = make(user, key='transfer:2', url='/t/')  # same url, other subject
        keyless = make(user, url='/t/')

        assert notifications.delete_for_key(user, 'transfer:1') == 2

        assert set(Notification.objects.all()) == {other_key, keyless}
        assert not Notification.objects.filter(pk__in=[gone.pk, also_gone.pk]).exists()

    def test_is_scoped_to_the_user(self, user, other):
        mine = make(user, key='k')
        theirs = make(other, key='k')

        assert notifications.delete_for_key(user, 'k') == 1

        assert list(Notification.objects.all()) == [theirs]
        assert not Notification.objects.filter(pk=mine.pk).exists()

    def test_can_be_limited_to_a_source(self, user):
        mine = notifications.notify(user, source='receipts', title='x', key='k')
        keep = notifications.notify(user, source='file_transfer', title='x', key='k')

        assert notifications.delete_for_key(user, 'k', source='receipts') == 1

        assert list(Notification.objects.all()) == [keep]
        assert not Notification.objects.filter(pk=mine.pk).exists()

    def test_a_user_id_will_do(self, user):
        make(user, key='k')

        assert notifications.delete_for_key(user.pk, 'k') == 1

    def test_deletes_read_opened_and_dismissed_ones_too(self, user):
        n = make(user, key='k')
        notifications.mark_opened(user, n.pk)
        notifications.dismiss(user, n.pk)

        assert notifications.delete_for_key(user, 'k') == 1

    def test_nothing_to_delete(self, user):
        assert notifications.delete_for_key(user, 'k') == 0

    @pytest.mark.parametrize('key', ['', '   '])
    def test_an_empty_key_is_refused_so_it_cannot_wipe_the_keyless_ones(self, user, key):
        keyless = make(user)

        with pytest.raises(ValueError):
            notifications.delete_for_key(user, key)

        assert list(Notification.objects.all()) == [keyless]

    def test_it_is_part_of_the_callers_transaction(self, user):
        make(user, key='k')

        with pytest.raises(RuntimeError), transaction.atomic():
            notifications.delete_for_key(user, 'k')
            raise RuntimeError

        assert Notification.objects.count() == 1
