"""`NotificationAdmin` (issue #169): read-only, filterable, searchable by user."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Notification
from apps.core.services import notifications

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

CHANGELIST = 'custom_admin:core_notification_changelist'


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


def titles(response) -> set[str]:
    return {n.title for n in response.context_data['cl'].result_list}


def test_lists_notifications(admin_client, user):
    notifications.notify(user, source='receipts', title='Storage paused')

    response = admin_client.get(reverse(CHANGELIST))

    assert response.status_code == 200
    assert titles(response) == {'Storage paused'}


def test_filters_by_source(admin_client, user):
    notifications.notify(user, source='receipts', title='from receipts')
    notifications.notify(user, source='file_transfer', title='from transfers')

    response = admin_client.get(reverse(CHANGELIST), {'source': 'receipts'})

    assert titles(response) == {'from receipts'}


def test_filters_by_read_and_dismissed_state(admin_client, user):
    new = notifications.notify(user, source='t', title='new')
    read = notifications.notify(user, source='t', title='read')
    gone = notifications.notify(user, source='t', title='gone')
    notifications.mark_read(user, [read.pk])
    notifications.dismiss(user, gone.pk)
    url = reverse(CHANGELIST)

    assert titles(admin_client.get(url, {'read': 'no'})) == {new.title}
    assert titles(admin_client.get(url, {'read': 'yes'})) == {'read', 'gone'}
    assert titles(admin_client.get(url, {'dismissed': 'yes'})) == {'gone'}
    assert titles(admin_client.get(url, {'dismissed': 'no'})) == {'new', 'read'}


def test_searches_by_user(admin_client, user):
    other = User.objects.create_user(email='someone.else@example.com', password='pw-12345-x')
    notifications.notify(user, source='t', title='mine')
    notifications.notify(other, source='t', title='theirs')

    response = admin_client.get(reverse(CHANGELIST), {'q': 'someone.else'})

    assert titles(response) == {'theirs'}


def test_is_read_only(admin_client, admin_user, user):
    n = notifications.notify(user, source='t', title='x')

    assert admin_client.get(reverse('custom_admin:core_notification_add')).status_code == 403
    detail = admin_client.get(reverse('custom_admin:core_notification_change', args=[n.pk]))
    assert detail.status_code == 200
    assert 'name="title"' not in detail.content.decode()
    posted = admin_client.post(
        reverse('custom_admin:core_notification_change', args=[n.pk]), {'title': 'changed'}
    )
    assert posted.status_code == 403
    assert (
        admin_client.post(
            reverse('custom_admin:core_notification_delete', args=[n.pk]), {'post': 'yes'}
        ).status_code
        == 403
    )
    n.refresh_from_db()
    assert n.title == 'x'


def test_deleting_a_user_deletes_their_notifications(admin_client, regular_user):
    notifications.notify(regular_user, source='t', title='x', url='/')

    response = admin_client.post(
        reverse('custom_admin:accounts_user_delete', args=[regular_user.pk]), {'post': 'yes'}
    )

    assert response.status_code == 302
    assert not User.objects.filter(pk=regular_user.pk).exists()
    assert not Notification.objects.exists()
