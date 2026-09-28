import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_settings_changelist_redirects_superuser_to_singleton_change_form(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('custom_admin:file_transfer_filetransfersettings_changelist'))
    assert response.status_code == 302

    change_response = client.get(response['Location'])
    assert change_response.status_code == 200


def test_settings_page_forbidden_for_non_superuser_staff(client, regular_user):
    regular_user.is_staff = True
    regular_user.save()
    client.force_login(regular_user)

    response = client.get(reverse('custom_admin:file_transfer_filetransfersettings_changelist'))
    assert response.status_code in (302, 403)


def test_transfer_admin_is_read_only(client, admin_user, draft_transfer):
    client.force_login(admin_user)
    response = client.get(
        reverse('custom_admin:file_transfer_transfer_change', args=[draft_transfer.id])
    )
    assert response.status_code == 200
    # Read-only ModelAdmin: no Save button in the change form.
    assert 'name="_save"' not in response.content.decode()


def test_transfer_admin_add_is_disabled(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('custom_admin:file_transfer_transfer_add'))
    assert response.status_code == 403
