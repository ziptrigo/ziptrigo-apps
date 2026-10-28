"""System checks for the To-do app, run by `manage.py check` and at `runserver` startup.

Later sub-issues (AI parsing, email intake) register theirs here too.
"""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_todo_storage_configuration(*args, **kwargs):
    """Warn about a half-configured attachment storage (local development; production refuses to
    start without the bucket and both keys, see `config/settings.py`).

    Everything empty is fine -- attachments then simply don't work in that environment -- and so is
    everything set. A bucket without credentials (and without a custom endpoint like Floci, which
    accepts any key), or credentials without a bucket, is a forgotten variable that would otherwise
    only show up as a failing upload.
    """
    problems: list[Warning] = []
    has_keys = bool(settings.TODO_AWS_ACCESS_KEY_ID and settings.TODO_AWS_SECRET_ACCESS_KEY)
    has_any_key = bool(settings.TODO_AWS_ACCESS_KEY_ID or settings.TODO_AWS_SECRET_ACCESS_KEY)
    if settings.TODO_S3_BUCKET and not has_keys:
        problems.append(
            Warning(
                'TODO_S3_BUCKET is set but TODO_AWS_ACCESS_KEY_ID / TODO_AWS_SECRET_ACCESS_KEY '
                'are not both set.',
                hint='To-do attachments need their own IAM credentials (any value works for '
                'Floci).',
                id='todo.W002',
            )
        )
    elif has_any_key and not settings.TODO_S3_BUCKET:
        problems.append(
            Warning(
                'TODO_AWS_ACCESS_KEY_ID / TODO_AWS_SECRET_ACCESS_KEY are set but TODO_S3_BUCKET '
                'is not.',
                hint='Set the bucket attachments are stored in.',
                id='todo.W002',
            )
        )
    return problems
