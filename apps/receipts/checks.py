"""System checks for the receipts app, run by `manage.py check` and at `runserver` startup."""

from django.conf import settings
from django.core.checks import Error, Warning, register


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


#: Below this a legitimate receipt can fail: ordinary files map 96-128 MB, a 48-megapixel HEIC or
#: a 50-megapixel PNG up to ~430 MB (see `services.documents`).
MIN_DOCUMENT_MEMORY_MB = 384
#: The wall-clock timeout should be this many times the CPU limit or more: the CPU limit does not
#: grow with the load on the host, so it should be what refuses a CPU-heavy file.
MIN_TIMEOUT_TO_CPU_RATIO = 1.5


@register()
def check_document_budget(*args, **kwargs):
    """Reject an absurd budget for reading receipt files (`services.documents`). A budget that is
    too small would fail ordinary receipts for good (`too_large`, a refund each) -- or, if the
    child can't run at all, retry every one of them -- and the only sign would be log lines. Skipped
    when the child is switched off (`RECEIPTS_DOCUMENT_SANDBOX`)."""
    if not getattr(settings, 'RECEIPTS_DOCUMENT_SANDBOX', True):
        return []
    memory = settings.RECEIPTS_DOCUMENT_MEMORY_MB
    cpu = settings.RECEIPTS_DOCUMENT_CPU_SECONDS
    timeout = settings.RECEIPTS_DOCUMENT_TIMEOUT_SECONDS
    problems: list[Error | Warning] = []
    if memory < MIN_DOCUMENT_MEMORY_MB:
        problems.append(
            Error(
                f'RECEIPTS_DOCUMENT_MEMORY_MB is {memory}; it must be at least '
                f'{MIN_DOCUMENT_MEMORY_MB}.',
                hint='A large photo needs ~430 MB of address space; the default is 1024.',
                id='receipts.E001',
            )
        )
    if cpu < 1:
        problems.append(
            Error(
                f'RECEIPTS_DOCUMENT_CPU_SECONDS is {cpu}; it must be at least 1.',
                hint='Ordinary receipts take 0.1-1.5 CPU seconds; the default is 25.',
                id='receipts.E002',
            )
        )
    if timeout <= 0:
        problems.append(
            Error(
                f'RECEIPTS_DOCUMENT_TIMEOUT_SECONDS is {timeout}; it must be positive.',
                id='receipts.E003',
            )
        )
    elif timeout <= cpu:
        problems.append(
            Error(
                f'RECEIPTS_DOCUMENT_TIMEOUT_SECONDS ({timeout}) must be larger than '
                f'RECEIPTS_DOCUMENT_CPU_SECONDS ({cpu}).',
                hint='The wall-clock timeout would otherwise refuse files before the CPU limit.',
                id='receipts.E004',
            )
        )
    elif timeout < MIN_TIMEOUT_TO_CPU_RATIO * cpu:
        problems.append(
            Warning(
                f'RECEIPTS_DOCUMENT_TIMEOUT_SECONDS ({timeout}) is less than '
                f'{MIN_TIMEOUT_TO_CPU_RATIO}x RECEIPTS_DOCUMENT_CPU_SECONDS ({cpu}).',
                hint='On a busy host the wall clock, not the CPU limit, would refuse files; '
                'use 2-3x.',
                id='receipts.W003',
            )
        )
    return problems
