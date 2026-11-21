# Renaming an attachment is logged (`attachment_renamed`): only the choices change, no column does.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('todo', '0020_default_view'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taskevent',
            name='kind',
            field=models.CharField(choices=[('created', 'Created'), ('state', 'State changed'), ('closed', 'Closed'), ('reopened', 'Reopened'), ('resolution', 'Resolution changed'), ('title', 'Title changed'), ('description', 'Description edited'), ('checklist', 'Checklist item'), ('priority', 'Priority changed'), ('due', 'Due date changed'), ('labels', 'Labels changed'), ('repeat', 'Repeat changed'), ('blocker_added', 'Blocker added'), ('blocker_removed', 'Blocker removed'), ('blocked', 'Blocked'), ('unblocked', 'Unblocked'), ('subtask_added', 'Subtask added'), ('subtask_removed', 'Subtask moved out'), ('subtask_closed', 'Subtask closed'), ('moved', 'Moved'), ('project', 'Project changed'), ('section', 'Section changed'), ('attachment_added', 'File attached'), ('attachment_removed', 'File removed'), ('attachment_renamed', 'File renamed'), ('trashed', 'Moved to the Trash'), ('restored', 'Restored')], max_length=24),
        ),
    ]
