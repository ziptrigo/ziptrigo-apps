"""
Checks done at startup.
"""

import os

from django.conf import settings
from django.core.checks import Error, Warning, register

from . import PROJECT_ROOT
from .common.environment import SUPPORTED_ENVIRONMENTS, select_env
from .services.email_service import (
    EMAIL_BACKEND_KIND_TO_CLASS,
    parse_email_backend_kinds,
)


@register()
def check_environment(*args, **kwargs):
    checks: list[Warning | Error] = []

    selection = select_env(PROJECT_ROOT)

    if selection.warnings:
        checks.append(
            Warning(
                '\n\n'.join(selection.warnings),
                hint='Either rename/remove the file(s), or add support for the environment.',
                id='W001',
            )
        )

    if selection.errors:
        if any('ENVIRONMENT environment variable' in e for e in selection.errors):
            checks.append(
                Error(
                    '\n'.join(selection.errors),
                    hint=f'Valid environments: {SUPPORTED_ENVIRONMENTS}',
                    id='E001',
                )
            )
        elif any('More than one environment file' in e for e in selection.errors):
            checks.append(
                Error(
                    '\n'.join(selection.errors),
                    hint='Have only one `.env.<env>` file or set the `ENVIRONMENT` variable.',
                    id='E002',
                )
            )
        else:
            checks.append(
                Error(
                    '\n'.join(selection.errors),
                    hint='Create one `.env.<env>` file or set the `ENVIRONMENT` variable.',
                    id='E003',
                )
            )

    if selection.environment:
        os.environ['ENVIRONMENT'] = str(selection.environment).lower()

    return checks


@register()
def check_email_backends(*args, **kwargs):
    checks: list[Error] = []

    raw_backends = getattr(settings, 'EMAIL_BACKENDS', '')
    kinds = parse_email_backend_kinds(raw_backends)

    if not kinds:
        checks.append(
            Error(
                'EMAIL_BACKENDS must be set to a comma-separated list of backend kinds.',
                hint='Example: EMAIL_BACKENDS=console or EMAIL_BACKENDS=ses,console',
                id='E010',
            )
        )
        return checks

    unknown = [k for k in kinds if k not in EMAIL_BACKEND_KIND_TO_CLASS]
    if unknown:
        checks.append(
            Error(
                f'Unknown EMAIL_BACKENDS kind(s): {unknown}',
                hint=f'Valid kinds: {sorted(EMAIL_BACKEND_KIND_TO_CLASS.keys())}',
                id='E011',
            )
        )

    return checks
