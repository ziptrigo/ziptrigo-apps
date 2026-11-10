from django.db import migrations, models


class Migration(migrations.Migration):
    """The version of the Terms of Use and Privacy Policy a user accepted, and when (issue #96).
    Existing accounts start with none and are asked on their next visit."""

    dependencies = [
        ('accounts', '0009_refresh_tokens'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='terms_version',
            field=models.CharField(blank=True, db_default='', default='', max_length=32),
        ),
        migrations.AddField(
            model_name='user',
            name='terms_accepted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
