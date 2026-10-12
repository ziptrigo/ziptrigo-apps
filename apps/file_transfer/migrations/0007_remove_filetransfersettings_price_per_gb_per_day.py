"""Drop the deprecated `FileTransferSettings.price_per_gb_per_day` (issue #102, release 2 of #101).

The storage price lives in `billing.BillingSettings` since `0006_copy_price_to_billing_settings`,
which shipped a release earlier so an old container serving during that deploy kept the column it
read. Nothing reads this column any more.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('file_transfer', '0006_copy_price_to_billing_settings'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='filetransfersettings',
            name='price_per_gb_per_day',
        ),
    ]
