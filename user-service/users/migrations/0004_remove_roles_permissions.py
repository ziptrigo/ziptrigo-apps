# Generated migration to remove roles and permissions

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_user_email_confirmed_user_email_confirmed_at_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='UserServicePermission',
        ),
        migrations.DeleteModel(
            name='UserServiceRole',
        ),
        migrations.DeleteModel(
            name='UserGlobalPermission',
        ),
        migrations.DeleteModel(
            name='UserGlobalRole',
        ),
        migrations.DeleteModel(
            name='UserServiceAssignment',
        ),
        migrations.DeleteModel(
            name='RolePermission',
        ),
        migrations.DeleteModel(
            name='Role',
        ),
        migrations.DeleteModel(
            name='Permission',
        ),
    ]
