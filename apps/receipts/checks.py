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
#: a 50-megapixel PNG up to ~430 MB and pass at 512 (see `services.documents`).
MIN_DOCUMENT_MEMORY_MB = 512
#: The wall-clock timeout should be this many times the CPU limit or more: the CPU limit does not
#: grow with the load on the host, so it should be what refuses a CPU-heavy file.
MIN_TIMEOUT_TO_CPU_RATIO = 1.5


@register()
def check_document_budget(*args, **kwargs):
    """Flag a bad budget for reading receipt files (`services.documents`). Skipped when the child
    is switched off (`RECEIPTS_DOCUMENT_SANDBOX`).

    Only a value that cannot work at all is an Error (memory or timeout not positive): an Error
    stops `manage.py migrate`, i.e. the deployed container's start, over a receipts tuning
    mistake. The rest are Warnings -- a budget that is too small fails or retries receipts, which
    is refunded, but it is no reason to take QR codes and file transfer down."""
    if not getattr(settings, 'RECEIPTS_DOCUMENT_SANDBOX', True):
        return []
    memory = settings.RECEIPTS_DOCUMENT_MEMORY_MB
    cpu = settings.RECEIPTS_DOCUMENT_CPU_SECONDS
    timeout = settings.RECEIPTS_DOCUMENT_TIMEOUT_SECONDS
    problems: list[Error | Warning] = []
    if memory <= 0:
        problems.append(
            Error(
                f'RECEIPTS_DOCUMENT_MEMORY_MB is {memory}; it must be positive.',
                hint='A large photo needs ~430 MB of address space; the default is 1024.',
                id='receipts.E001',
            )
        )
    elif memory < MIN_DOCUMENT_MEMORY_MB:
        problems.append(
            Warning(
                f'RECEIPTS_DOCUMENT_MEMORY_MB is {memory}; below {MIN_DOCUMENT_MEMORY_MB} large '
                'photos (48 MP) are refused for good as too large.',
                hint='The default is 1024.',
                id='receipts.W003',
            )
        )
    if timeout <= 0:
        problems.append(
            Error(
                f'RECEIPTS_DOCUMENT_TIMEOUT_SECONDS is {timeout}; it must be positive.',
                id='receipts.E002',
            )
        )
    if cpu < 1:
        problems.append(
            Warning(
                f'RECEIPTS_DOCUMENT_CPU_SECONDS is {cpu}; it is treated as 1.',
                hint='Ordinary receipts take 0.1-3 CPU seconds; the default is 25.',
                id='receipts.W004',
            )
        )
    if timeout > 0 and cpu >= 1 and timeout < MIN_TIMEOUT_TO_CPU_RATIO * cpu:
        problems.append(
            Warning(
                f'RECEIPTS_DOCUMENT_TIMEOUT_SECONDS ({timeout}) is less than '
                f'{MIN_TIMEOUT_TO_CPU_RATIO}x RECEIPTS_DOCUMENT_CPU_SECONDS ({cpu}).',
                hint='On a busy host the wall clock, not the CPU limit, would end the child and '
                'the receipt would be retried instead of refused; use 2-3x.',
                id='receipts.W005',
            )
        )
    return problems


@register()
def check_document_child(*args, **kwargs):
    """Run the real document child on a tiny text payload and warn if it can't read it: a missing
    capability for the user switch, an unreadable virtualenv or a broken library import would
    otherwise show up only as every receipt being retried and finally failed. `migrate` (the
    deployed container's entrypoint) runs the checks as the deployed user, so this is where it is
    seen. Skipped when the child is off and under `DEBUG` (a child start costs ~0.3 s, which every
    local `manage.py` command would pay)."""
    if settings.DEBUG or not getattr(settings, 'RECEIPTS_DOCUMENT_SANDBOX', True):
        return []
    from .services import documents

    try:
        prepared = documents.prepare_input(b'Total: 42.10', documents.PLAIN)
        if prepared.text != 'Total: 42.10':
            raise ValueError('unexpected answer')
    except Exception as exc:
        return [
            Warning(
                f'The receipts document child could not read a test text ({type(exc).__name__}: '
                f'{exc}).',
                hint='Every receipt will be retried and finally fail. Check the log for the '
                'reason, RECEIPTS_DOCUMENT_CHILD_USER and the memory/CPU settings.',
                id='receipts.W006',
            )
        ]
    return []
