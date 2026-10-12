"""System checks for the receipts app, run by `manage.py check` and at `runserver` startup."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_receipts_configuration(*args, **kwargs):
    """Warn about a half-configured Receipts setup (local development; production refuses to start
    without the required settings, see `config/settings.py`).

    Everything empty is fine -- receipts uploads and AI extraction then simply don't work in that
    environment -- and so is everything set. A bucket without credentials (and without a custom
    endpoint like Floci, which accepts any key) or an API key without a model is a forgotten
    variable that would otherwise only show up as a failing upload or a failed receipt.
    """
    problems: list[Warning] = []
    if bool(settings.OPENAI_API_KEY) != bool(settings.OPENAI_RECEIPTS_MODEL):
        problems.append(
            Warning(
                'Receipts AI is only partly configured: set both OPENAI_API_KEY and '
                'OPENAI_RECEIPTS_MODEL (or neither).',
                hint='Until then every receipt fails extraction.',
                id='receipts.W001',
            )
        )
    has_keys = bool(settings.RECEIPTS_AWS_ACCESS_KEY_ID and settings.RECEIPTS_AWS_SECRET_ACCESS_KEY)
    if settings.RECEIPTS_S3_BUCKET and not has_keys:
        problems.append(
            Warning(
                'RECEIPTS_S3_BUCKET is set but RECEIPTS_AWS_ACCESS_KEY_ID / '
                'RECEIPTS_AWS_SECRET_ACCESS_KEY are not.',
                hint='Receipts storage needs its own IAM credentials (any value works for Floci).',
                id='receipts.W002',
            )
        )
    return problems
