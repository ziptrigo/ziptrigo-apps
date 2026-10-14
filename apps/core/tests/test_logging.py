"""`settings.LOGGING` must put errors on the console in production.

Logging config is process-global (and `conftest.py` re-enables propagation so `caplog` works), so
each case runs `django.setup()` in a fresh interpreter and reads its stderr.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]

_SCRIPT = """
import logging
import pytest  # noqa: F401 -- settings treat a process that imported pytest as a test run
import django

django.setup()
try:
    1 / 0
except ZeroDivisionError:
    logging.getLogger('django.request').error('request-boom', exc_info=True)
logging.getLogger('django.security.SuspiciousOperation').warning('security-warn')
logging.getLogger('botocore.third.party').warning('third-party-warn')
logging.getLogger('botocore.third.party').info('third-party-info')
logging.getLogger('apps.core.x').info('apps-info')
"""


def _run(debug: str) -> str:
    env = {
        **os.environ,
        'DEBUG': debug,
        'ENVIRONMENT': 'local',
        'DJANGO_SETTINGS_MODULE': 'config.settings',
    }
    result = subprocess.run(
        [sys.executable, '-c', _SCRIPT],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stderr


@pytest.mark.parametrize('debug', ['False', 'True'])
def test_errors_and_warnings_reach_the_console_once(debug):
    err = _run(debug)

    # Each record is printed exactly once (no second copy through root or Django's own handler).
    assert err.count('ERROR django.request: request-boom') == 1
    assert err.count('ZeroDivisionError') == 1  # the traceback
    assert err.count('security-warn') == 1
    assert err.count('third-party-warn') == 1
    assert err.count('apps-info') == 1
    # Root only takes WARNING and up.
    assert 'third-party-info' not in err
