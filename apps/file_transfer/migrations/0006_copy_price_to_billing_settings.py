"""Copy the storage price from `FileTransferSettings` into `BillingSettings` (issue #98).

The per-GB-per-day storage price is now shared by every product, so it lives in
`billing.BillingSettings`. This data migration lives here rather than in `billing` because
`billing` can't depend on a product, and it needs both models to exist. The old column
(`FileTransferSettings.price_per_gb_per_day`) is left in place for now -- an old container may
still be serving while this runs -- and is dropped by a later release.
"""

from django.db import migrations


def copy_price_to_billing_settings(apps, schema_editor):
    """Carry an admin-customised price over. With no `FileTransferSettings` row (nobody ever
    opened the settings page or metered anything) there's nothing to carry: `BillingSettings`
    simply starts at its default, which is the same default the old field had."""
    FileTransferSettings = apps.get_model('file_transfer', 'FileTransferSettings')
    BillingSettings = apps.get_model('billing', 'BillingSettings')

    old = FileTransferSettings.objects.filter(pk=1).first()
    if old is None:
        return
    BillingSettings.objects.update_or_create(
        pk=1, defaults={'storage_price_per_gb_per_day': old.price_per_gb_per_day}
    )


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_billingsettings'),
        ('file_transfer', '0005_alter_filetransfersettings_price_per_gb_per_day'),
    ]

    operations = [
        # No reverse: the old column was never touched, so there is nothing to restore.
        migrations.RunPython(copy_price_to_billing_settings, migrations.RunPython.noop),
    ]
