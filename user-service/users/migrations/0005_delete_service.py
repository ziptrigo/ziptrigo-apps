# Generated migration to remove Service model

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_remove_roles_permissions'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Service',
        ),
    ]
