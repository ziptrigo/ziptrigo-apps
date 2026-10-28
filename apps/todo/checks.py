"""System checks for the To-do app, run by `manage.py check` and at `runserver` startup.

Later sub-issues (email intake) register theirs here too.
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


@register()
def check_ai_configuration(*args, **kwargs):
    """Warn when the AI key is set but no model resolves for To-do (issue #183).

    Without a key the date parsing is simply off (no chip, no error), and that is fine. A key with
    neither `OPENAI_TODO_MODEL` nor the `OPENAI_RECEIPTS_MODEL` it falls back to is a forgotten
    variable that would otherwise only show up as a chip that never appears. A Warning, never an
    Error: `migrate` runs the checks and a failed one would stop the whole site from starting.
    """
    if settings.OPENAI_API_KEY and not settings.OPENAI_TODO_MODEL:
        return [
            Warning(
                'To-do date parsing is off: OPENAI_API_KEY is set but no model is.',
                hint='Set OPENAI_TODO_MODEL (or OPENAI_RECEIPTS_MODEL, which it falls back to).',
                id='todo.W001',
            )
        ]
    return []
