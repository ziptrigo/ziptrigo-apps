"""The data migration that moved the storage price to `BillingSettings` (issue #98), and the
deprecated `FileTransferSettings.price_per_gb_per_day` field it left behind.

The suite runs with `--no-migrations`, so the migration's function is called directly against the
real app registry rather than through the migration executor.
"""

from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps
from django.urls import reverse

from apps.billing.models import BillingSettings

from ..models import FileTransferSettings

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_migration = import_module('apps.file_transfer.migrations.0006_copy_price_to_billing_settings')


def test_copies_a_customised_price():
    FileTransferSettings.objects.create(pk=1, price_per_gb_per_day=Decimal('0.3500'))

    _migration.copy_price_to_billing_settings(apps, None)

    assert BillingSettings.objects.count() == 1
    assert BillingSettings.load().storage_price_per_gb_per_day == Decimal('0.35')


def test_an_existing_billing_row_is_left_alone():
    BillingSettings.objects.create(pk=1, storage_price_per_gb_per_day=Decimal('0.7'))
    FileTransferSettings.objects.create(pk=1, price_per_gb_per_day=Decimal('2'))

    _migration.copy_price_to_billing_settings(apps, None)

    assert BillingSettings.objects.count() == 1
    assert BillingSettings.load().storage_price_per_gb_per_day == Decimal('0.7')


def test_rerunning_does_not_overwrite_a_newer_price():
    FileTransferSettings.objects.create(pk=1, price_per_gb_per_day=Decimal('0.35'))
    _migration.copy_price_to_billing_settings(apps, None)
    BillingSettings.objects.filter(pk=1).update(storage_price_per_gb_per_day=Decimal('0.9'))

    _migration.copy_price_to_billing_settings(apps, None)

    assert BillingSettings.load().storage_price_per_gb_per_day == Decimal('0.9')


def test_without_a_file_transfer_settings_row_nothing_is_created():
    _migration.copy_price_to_billing_settings(apps, None)

    assert not BillingSettings.objects.exists()
    assert BillingSettings.load().storage_price_per_gb_per_day == Decimal('0.1')


def test_reverse_is_a_noop():
    FileTransferSettings.objects.create(pk=1, price_per_gb_per_day=Decimal('0.3'))
    reverse_code = _migration.Migration.operations[0].reverse_code

    reverse_code(apps, None)

    assert FileTransferSettings.load().price_per_gb_per_day == Decimal('0.3')


def test_migration_depends_on_billing_model_and_latest_file_transfer_migration():
    dependencies = _migration.Migration.dependencies

    assert ('billing', '0002_billingsettings') in dependencies
    assert (
        'file_transfer',
        '0005_alter_filetransfersettings_price_per_gb_per_day',
    ) in dependencies


def test_deprecated_field_is_not_editable_in_the_admin(client, admin_user):
    client.force_login(admin_user)
    settings_row = FileTransferSettings.load()

    response = client.get(
        reverse('custom_admin:file_transfer_filetransfersettings_change', args=[settings_row.pk])
    )

    assert response.status_code == 200
    assert 'price_per_gb_per_day' not in response.content.decode()


def test_deprecated_field_is_not_read_by_metering(draft_transfer):
    from datetime import timedelta

    from django.utils import timezone

    from apps.billing.services import get_balance

    from ..models import TransferStatus
    from ..services.metering import meter_transfer

    FileTransferSettings.objects.update_or_create(pk=1, defaults={'price_per_gb_per_day': 50})
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.size_bytes = 1024**3
    draft_transfer.last_billed_at = timezone.now() - timedelta(hours=25)
    draft_transfer.save()
    balance_before = get_balance(draft_transfer.owner)

    meter_transfer(draft_transfer)

    # Billing settings' default (0.1/GB/day) applies: nothing spent yet, just accrued.
    assert get_balance(draft_transfer.owner) == balance_before
    assert draft_transfer.accrued == Decimal('0.1')
