"""Task states and resolutions (issue #272).

`Task.state` (To do, In progress, On hold; every existing task starts To do, which the column's
`db_default` does for the rows already there) and `Task.resolution` (empty while open, `done` or
`wont_do` once closed): every task that is already completed becomes Done. The new columns carry a
`db_default` as well as a `default`, so the container still serving while this migration runs (it
doesn't know them) can keep inserting and completing tasks; its completions leave `resolution`
empty, which reads as Done. `TodoAccount.muted_overdue` is where a muted overdue task shows on Today.
"""

from django.db import migrations, models


def mark_completed_as_done(apps, schema_editor):
    """Every task that was completed before this release was closed as Done."""
    Task = apps.get_model('todo', 'Task')
    Task.objects.filter(completed_at__isnull=False, resolution='').update(resolution='done')


class Migration(migrations.Migration):
    dependencies = [
        ('todo', '0010_push_reminders'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='resolution',
            field=models.CharField(
                blank=True,
                choices=[('done', 'Done'), ('wont_do', "Won't do")],
                db_default='',
                default='',
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='task',
            name='state',
            field=models.CharField(
                choices=[
                    ('todo', 'To do'),
                    ('in_progress', 'In progress'),
                    ('on_hold', 'On hold'),
                ],
                db_default='todo',
                default='todo',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='todoaccount',
            name='muted_overdue',
            field=models.CharField(
                choices=[
                    ('dimmed', 'Today, dimmed'),
                    ('hidden', 'Hidden from Today'),
                    ('section', 'Own section'),
                ],
                db_default='dimmed',
                default='dimmed',
                help_text='Where an overdue task that is On hold shows on Today: dimmed at the '
                'bottom of Overdue, hidden, or in its own collapsed section.',
                max_length=8,
            ),
        ),
        migrations.RunPython(mark_completed_as_done, migrations.RunPython.noop),
    ]
