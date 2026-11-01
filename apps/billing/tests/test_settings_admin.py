"""Admin tests for `BillingSettings` (issue #98): a superuser-only singleton settings page,
mirroring `apps.core.tests.test_core_settings_admin`."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.billing.models import BillingSettings

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_changelist_redirects_superuser_to_singleton_change_form(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('custom_admin:billing_billingsettings_changelist'))
    assert response.status_code == 302
    assert response['Location'] == reverse('custom_admin:billing_billingsettings_change', args=[1])

    assert client.get(response['Location']).status_code == 200


def test_page_forbidden_for_non_superuser_staff(client, regular_user):
    regular_user.is_staff = True
    regular_user.save()
    client.force_login(regular_user)

    response = client.get(reverse('custom_admin:billing_billingsettings_changelist'))
    assert response.status_code in (302, 403)


def test_add_is_disabled_once_the_row_exists(client, admin_user):
    client.force_login(admin_user)
    BillingSettings.load()

    assert client.get(reverse('custom_admin:billing_billingsettings_add')).status_code == 403


def test_delete_is_disabled(client, admin_user):
    client.force_login(admin_user)
    settings_row = BillingSettings.load()

    response = client.post(
        reverse('custom_admin:billing_billingsettings_delete', args=[settings_row.pk]),
        {'post': 'yes'},
    )

    assert response.status_code == 403
    assert BillingSettings.objects.filter(pk=settings_row.pk).exists()


def test_price_can_be_edited_from_the_admin(client, admin_user):
    client.force_login(admin_user)
    settings_row = BillingSettings.load()

    response = client.post(
        reverse('custom_admin:billing_billingsettings_change', args=[settings_row.pk]),
        {'storage_price_per_gb_per_day': '0.2500', 'welcome_credits': '1000', '_save': 'Save'},
    )

    assert response.status_code == 302
    settings_row.refresh_from_db()
    assert settings_row.storage_price_per_gb_per_day == Decimal('0.25')


def test_welcome_credits_can_be_edited_and_turned_off_from_the_admin(client, admin_user):
    client.force_login(admin_user)
    settings_row = BillingSettings.load()
    assert settings_row.welcome_credits == 1000  # the default

    for value in ('250', '0'):
        response = client.post(
            reverse('custom_admin:billing_billingsettings_change', args=[settings_row.pk]),
            {'storage_price_per_gb_per_day': '0.1', 'welcome_credits': value, '_save': 'Save'},
        )
        assert response.status_code == 302
        settings_row.refresh_from_db()
        assert settings_row.welcome_credits == int(value)


@pytest.mark.parametrize('value', ['-1', '1000001', 'lots'])
def test_bad_welcome_credits_are_rejected(client, admin_user, value):
    client.force_login(admin_user)
    settings_row = BillingSettings.load()

    response = client.post(
        reverse('custom_admin:billing_billingsettings_change', args=[settings_row.pk]),
        {'storage_price_per_gb_per_day': '0.1', 'welcome_credits': value, '_save': 'Save'},
    )

    assert response.status_code == 200
    settings_row.refresh_from_db()
    assert settings_row.welcome_credits == 1000


def test_negative_price_is_rejected(client, admin_user):
    client.force_login(admin_user)
    settings_row = BillingSettings.load()

    response = client.post(
        reverse('custom_admin:billing_billingsettings_change', args=[settings_row.pk]),
        {'storage_price_per_gb_per_day': '-1', 'welcome_credits': '1000', '_save': 'Save'},
    )

    assert response.status_code == 200
    settings_row.refresh_from_db()
    assert settings_row.storage_price_per_gb_per_day == Decimal('0.1')
