from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('todo', '0015_delivery_task_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='all_position',
            field=models.IntegerField(db_default=0, default=0),
        ),
    ]
