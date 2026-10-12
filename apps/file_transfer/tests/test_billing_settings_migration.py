"""The migrations that moved the storage price to `BillingSettings` (issue #98) and then dropped
the old `FileTransferSettings.price_per_gb_per_day` column (issue #102).

The suite runs with `--no-migrations`, so the data migration's function is called directly against
the real app registry rather than through the migration executor. The tables are built from the
current models, which no longer have the old column, so only the paths of `0006` that never read it
can run here.
"""

from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps
from django.db import migrations
from django.urls import reverse

from apps.billing.models import BillingSettings

from ..models import FileTransferSettings

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_copy = import_module('apps.file_transfer.migrations.0006_copy_price_to_billing_settings')
_drop = import_module(
    'apps.file_transfer.migrations.0007_remove_filetransfersettings_price_per_gb_per_day'
)


def test_an_existing_billing_row_is_left_alone():
    BillingSettings.objects.create(pk=1, storage_price_per_gb_per_day=Decimal('0.7'))
    FileTransferSettings.objects.create(pk=1)

    _copy.copy_price_to_billing_settings(apps, None)

    assert BillingSettings.objects.count() == 1
    assert BillingSettings.load().storage_price_per_gb_per_day == Decimal('0.7')


def test_without_a_file_transfer_settings_row_nothing_is_created():
    _copy.copy_price_to_billing_settings(apps, None)

    assert not BillingSettings.objects.exists()
    assert BillingSettings.load().storage_price_per_gb_per_day == Decimal('0.1')


def test_copy_migration_depends_on_billing_model_and_previous_file_transfer_migration():
    dependencies = _copy.Migration.dependencies

    assert ('billing', '0002_billingsettings') in dependencies
    assert (
        'file_transfer',
        '0005_alter_filetransfersettings_price_per_gb_per_day',
    ) in dependencies


def test_the_old_column_is_dropped_only_after_the_copy():
    assert _drop.Migration.dependencies == [
        ('file_transfer', '0006_copy_price_to_billing_settings')
    ]
    (operation,) = _drop.Migration.operations
    assert isinstance(operation, migrations.RemoveField)
    assert (operation.model_name, operation.name) == (
        'filetransfersettings',
        'price_per_gb_per_day',
    )


def test_the_model_no_longer_has_the_old_field():
    assert not hasattr(FileTransferSettings.load(), 'price_per_gb_per_day')


def test_the_admin_page_has_no_price_field(client, admin_user):
    client.force_login(admin_user)
    settings_row = FileTransferSettings.load()

    response = client.get(
        reverse('custom_admin:file_transfer_filetransfersettings_change', args=[settings_row.pk])
    )

    assert response.status_code == 200
    assert 'price_per_gb_per_day' not in response.content.decode()
