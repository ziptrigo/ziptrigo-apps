"""Copy the storage price from `FileTransferSettings` into `BillingSettings` (issue #98).

The per-GB-per-day storage price is now shared by every product, so it lives in
`billing.BillingSettings`. This data migration lives here rather than in `billing` because
`billing` can't depend on a product, and it needs both models to exist. The old column
(`FileTransferSettings.price_per_gb_per_day`) is left in place for now -- an old container may
still be serving while this runs -- and is dropped by a later release. Don't edit the storage
price while release 1 deploys (the old container's admin still writes the old column, which
nothing copies afterwards); verify `BillingSettings` matches the intended price before release 2
drops that column.
"""

from django.db import migrations


def copy_price_to_billing_settings(apps, schema_editor):
    """Carry an admin-customised price over, once. With no `FileTransferSettings` row (nobody ever
    opened the settings page or metered anything) there's nothing to carry: `BillingSettings`
    simply starts at its default, which is the same default the old field had. An existing
    `BillingSettings` row is left alone: on the first run the table was only just created (by
    `billing.0002`) and is empty, so a row means the price was already set there -- re-running
    this migration (rolling back and forward again) must not overwrite a newer price with the
    stale one in the old column."""
    FileTransferSettings = apps.get_model('file_transfer', 'FileTransferSettings')
    BillingSettings = apps.get_model('billing', 'BillingSettings')

    if BillingSettings.objects.exists():
        return
    old = FileTransferSettings.objects.filter(pk=1).first()
    if old is None:
        return
    BillingSettings.objects.create(pk=1, storage_price_per_gb_per_day=old.price_per_gb_per_day)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_billingsettings'),
        ('file_transfer', '0005_alter_filetransfersettings_price_per_gb_per_day'),
    ]

    operations = [
        # No reverse: the old column was never touched, so there is nothing to restore.
        migrations.RunPython(copy_price_to_billing_settings, migrations.RunPython.noop),
    ]
