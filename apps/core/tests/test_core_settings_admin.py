"""Admin tests for `CoreSettings` (issue #58): a superuser-only singleton settings page, mirroring
`apps.file_transfer.tests.test_admin`'s coverage of `FileTransferSettingsAdmin`.
"""

import pytest
from django.urls import reverse

from apps.core.models import CoreSettings, EmailVerification

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_settings_changelist_redirects_superuser_to_singleton_change_form(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('custom_admin:core_coresettings_changelist'))
    assert response.status_code == 302

    change_response = client.get(response['Location'])
    assert change_response.status_code == 200


def test_settings_page_forbidden_for_non_superuser_staff(client, regular_user):
    regular_user.is_staff = True
    regular_user.save()
    client.force_login(regular_user)

    response = client.get(reverse('custom_admin:core_coresettings_changelist'))
    assert response.status_code in (302, 403)


def test_settings_load_creates_singleton_with_defaults(db):
    settings_row = CoreSettings.load()

    assert settings_row.pk == 1
    assert settings_row.email_verification_code_length == 6
    assert settings_row.email_verification_validity_minutes == 30
    assert settings_row.email_verification_max_attempts == 5
    assert settings_row.email_verification_resend_cooldown_seconds == 60
    assert CoreSettings.objects.count() == 1


def test_settings_can_be_edited_from_the_admin(client, admin_user):
    client.force_login(admin_user)
    settings_row = CoreSettings.load()

    response = client.post(
        reverse('custom_admin:core_coresettings_change', args=[settings_row.pk]),
        {
            'email_verification_code_length': 8,
            'email_verification_validity_minutes': 15,
            'email_verification_max_attempts': 3,
            'email_verification_resend_cooldown_seconds': 30,
            '_save': 'Save',
        },
    )
    assert response.status_code == 302

    settings_row.refresh_from_db()
    assert settings_row.email_verification_code_length == 8
    assert settings_row.email_verification_validity_minutes == 15
    assert settings_row.email_verification_max_attempts == 3
    assert settings_row.email_verification_resend_cooldown_seconds == 30


def test_email_verification_admin_is_read_only(client, admin_user):
    from apps.core.services.email_verification import EmailVerificationContext, start

    def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
        return 'subject', 'text', 'html'

    verification_id = start('someone@example.com', 'test.purpose', build_email=build_email)

    client.force_login(admin_user)
    response = client.get(
        reverse('custom_admin:core_emailverification_change', args=[verification_id])
    )
    assert response.status_code == 200
    assert 'name="_save"' not in response.content.decode()


def test_email_verification_admin_add_is_disabled(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('custom_admin:core_emailverification_add'))
    assert response.status_code == 403


def test_email_verification_admin_delete_is_disabled(client, admin_user):
    from apps.core.services.email_verification import EmailVerificationContext, start

    def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
        return 'subject', 'text', 'html'

    verification_id = start('someone@example.com', 'test.purpose', build_email=build_email)

    client.force_login(admin_user)
    response = client.get(
        reverse('custom_admin:core_emailverification_delete', args=[verification_id])
    )
    assert response.status_code == 403
    assert EmailVerification.objects.filter(pk=verification_id).exists()
