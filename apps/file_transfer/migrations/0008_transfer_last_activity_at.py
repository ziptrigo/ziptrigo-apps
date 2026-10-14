"""Add `Transfer.last_activity_at`, what `cleanup_drafts` measures a draft's idleness from.

It has a database default (`now()`), so the container still serving while this migration runs --
it doesn't know the column -- keeps inserting transfers (the #102 lesson). Existing rows get the
time of the migration, i.e. one more day before an old draft is cleaned up.
"""

import django.db.models.functions.datetime
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('file_transfer', '0007_remove_filetransfersettings_price_per_gb_per_day'),
    ]

    operations = [
        migrations.AddField(
            model_name='transfer',
            name='last_activity_at',
            field=models.DateTimeField(
                db_default=django.db.models.functions.datetime.Now(),
                default=django.utils.timezone.now,
                help_text='When the sender last did something to this draft (added, uploaded to, '
                'resumed or removed a file). `jobs.cleanup_drafts` deletes a draft or unconfirmed '
                'transfer once this is a day old.',
            ),
        ),
    ]
