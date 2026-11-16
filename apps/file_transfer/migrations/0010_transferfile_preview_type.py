"""Add `TransferFile.preview_type`, the content type a file may be previewed as on the public
download page (issue #311), read from its bytes when the upload completed.

It has a database default (the #102 lesson: the container still serving while this migration runs
doesn't know the column and keeps inserting files). Existing files get `''` -- no preview -- until
`./manage.py file_transfer_backfill_previews` reads their first bytes.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('file_transfer', '0009_file_requests'),
    ]

    operations = [
        migrations.AddField(
            model_name='transferfile',
            name='preview_type',
            field=models.CharField(
                blank=True,
                db_default='',
                default='',
                help_text='The content type the public download page may preview this file as '
                '(`image/png`, `application/pdf`, `text/plain`, ...), read from its '
                "first bytes when the upload completed -- never from its name or the uploader's "
                'claimed type. Empty: no preview (including files uploaded before this column '
                'existed, until `file_transfer_backfill_previews` runs). See '
                '`apps.file_transfer.services.preview`.',
                max_length=40,
            ),
        ),
    ]
