import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

from ..models import Feedback, FeedbackStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

CHANGELIST = 'custom_admin:feedback_feedback_changelist'


@pytest.fixture()
def items(user):
    return {
        status: Feedback.objects.create(
            created_by=user, description=f'{status} feedback', status=status
        )
        for status in FeedbackStatus.values
    }


def test_status_filter_returns_the_right_rows(client, admin_user, items):
    client.force_login(admin_user)

    response = client.get(reverse(CHANGELIST), {'status__exact': 'in_process'})

    assert response.status_code == 200
    assert list(response.context['cl'].result_list) == [items['in_process']]


def test_changelist_offers_a_status_filter_and_search(client, admin_user, items, user):
    client.force_login(admin_user)

    response = client.get(reverse(CHANGELIST), {'q': 'closed feedback'})

    html = response.content.decode()
    assert response.status_code == 200
    assert [spec.field_path for spec in response.context['cl'].filter_specs] == [
        'status',
        'created_at',
    ]
    assert list(response.context['cl'].result_list) == [items['closed']]
    assert user.email in html


@pytest.mark.parametrize(
    'action, status',
    [
        ('mark_new', FeedbackStatus.NEW),
        ('mark_in_process', FeedbackStatus.IN_PROCESS),
        ('mark_closed', FeedbackStatus.CLOSED),
    ],
)
def test_bulk_actions_set_the_status(client, admin_user, items, action, status):
    client.force_login(admin_user)
    selected = [items['new'], items['closed']]
    before = {f.pk: f.updated_at for f in selected}

    response = client.post(
        reverse(CHANGELIST),
        {'action': action, '_selected_action': [str(f.pk) for f in selected]},
        follow=True,
    )

    assert response.status_code == 200
    for feedback in selected:
        feedback.refresh_from_db()
        assert feedback.status == status
        assert feedback.updated_at > before[feedback.pk]
    items['in_process'].refresh_from_db()
    assert items['in_process'].status == FeedbackStatus.IN_PROCESS


def _staff_with(*codenames: str):
    staff = UserFactory(is_staff=True)
    for codename in codenames:
        # `user_permissions` is a many-to-many manager `ty` can't see through the field type.
        staff.user_permissions.add(  # ty: ignore[unresolved-attribute]
            Permission.objects.get(content_type__app_label='feedback', codename=codename)
        )
    return staff


@pytest.mark.parametrize('action', ['mark_new', 'mark_in_process', 'mark_closed'])
def test_view_only_staff_cannot_run_bulk_actions(client, items, action):
    client.force_login(_staff_with('view_feedback'))
    selected = items['new']

    page = client.get(reverse(CHANGELIST))
    client.post(
        reverse(CHANGELIST),
        {'action': action, '_selected_action': [str(selected.pk)]},
        follow=True,
    )

    # No bulk action is offered at all, so there's no action form on the changelist.
    assert page.context['action_form'] is None
    selected.refresh_from_db()
    assert selected.status == FeedbackStatus.NEW


def test_staff_with_change_permission_is_offered_the_bulk_actions(client, items):
    client.force_login(_staff_with('view_feedback', 'change_feedback'))

    page = client.get(reverse(CHANGELIST))

    choices = dict(page.context['action_form'].fields['action'].choices)
    assert {'mark_new', 'mark_in_process', 'mark_closed'} <= set(choices)


def test_add_is_disabled(client, admin_user):
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:feedback_feedback_add'))

    assert response.status_code == 403


def test_only_the_status_can_be_edited(client, admin_user, items):
    client.force_login(admin_user)
    feedback = items['new']
    url = reverse('custom_admin:feedback_feedback_change', args=[feedback.pk])

    page = client.get(url)
    response = client.post(
        url, {'status': 'closed', 'description': 'tampered', '_save': 'Save'}, follow=True
    )

    assert 'name="description"' not in page.content.decode()
    assert 'new feedback' in page.content.decode()
    assert response.status_code == 200
    feedback.refresh_from_db()
    assert feedback.status == FeedbackStatus.CLOSED
    assert feedback.description == 'new feedback'


def test_deleted_user_is_shown_as_such(client, admin_user, user):
    Feedback.objects.create(created_by=user, description='orphan')
    user.delete()
    client.force_login(admin_user)

    response = client.get(reverse(CHANGELIST))

    assert '(deleted user)' in response.content.decode()
