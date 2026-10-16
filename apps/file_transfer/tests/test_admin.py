import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from ..models import FileTransferSettings

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


@pytest.mark.parametrize('value', ['', '1,abc', '0,5', '-1', '1.5', '1,,x', '\u00b2'])
def test_settings_refuse_unparseable_anonymous_expiry_days(value):
    """`anonymous_allowed_expiry_days_list()` runs `int()` on every anonymous send, so a value it
    can't parse would turn that flow into a 500."""
    row = FileTransferSettings(anonymous_allowed_expiry_days=value)

    with pytest.raises(ValidationError) as excinfo:
        row.clean()

    assert 'anonymous_allowed_expiry_days' in excinfo.value.message_dict


def test_settings_accept_whole_days_with_spaces():
    row = FileTransferSettings(anonymous_allowed_expiry_days=' 1, 7 ,30')

    row.clean()

    assert row.anonymous_allowed_expiry_days_list() == [1, 7, 30]


def test_settings_admin_refuses_unparseable_anonymous_expiry_days(client, admin_user):
    client.force_login(admin_user)
    row = FileTransferSettings.load()
    change_url = reverse('custom_admin:file_transfer_filetransfersettings_change', args=[row.pk])
    form = client.get(change_url).context['adminform'].form
    data = {name: value for name, value in form.initial.items() if value is not None}
    data['anonymous_allowed_expiry_days'] = '1,abc'

    response = client.post(change_url, data)

    assert response.status_code == 200
    assert 'Enter whole numbers of days of at least 1' in response.content.decode()
    row.refresh_from_db()
    assert row.anonymous_allowed_expiry_days == '1,5,15,30'
