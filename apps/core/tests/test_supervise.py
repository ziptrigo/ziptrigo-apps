import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import supervise

pytestmark = pytest.mark.unit

_SLEEP = [sys.executable, '-c', 'import time; time.sleep(60)']
_REPO_ROOT = Path(supervise.__file__).resolve().parent


def _has_worker(commands: list[list[str]]) -> bool:
    return any('db_worker' in command for command in commands)


def test_build_commands_includes_worker_by_default():
    commands = supervise.build_commands({})
    assert commands[0][0] == 'gunicorn'
    assert _has_worker(commands)


@pytest.mark.parametrize('value', ['1', 'true', 'True'])
def test_build_commands_worker_enabled(value):
    assert _has_worker(supervise.build_commands({'RUN_TASK_WORKER': value}))


@pytest.mark.parametrize('value', ['0', 'false', 'False'])
def test_build_commands_worker_disabled(value):
    commands = supervise.build_commands({'RUN_TASK_WORKER': value})
    assert not _has_worker(commands)
    assert len(commands) == 1


def test_failing_child_stops_the_other_and_returns_its_code():
    failing = [sys.executable, '-c', 'import sys; sys.exit(3)']
    started = time.monotonic()

    code = supervise.supervise([failing, _SLEEP], grace_seconds=2, poll_seconds=0.05)

    assert code == 3
    assert time.monotonic() - started < 30


def test_child_exiting_zero_unexpectedly_returns_one():
    quiet = [sys.executable, '-c', 'pass']

    assert supervise.supervise([quiet, _SLEEP], grace_seconds=2, poll_seconds=0.05) == 1


def test_sigterm_stops_children_and_exits_zero():
    script = textwrap.dedent(
        """
        import sys, supervise
        sleeper = [sys.executable, '-c', 'import time; time.sleep(60)']
        print('ready', flush=True)
        sys.exit(supervise.supervise([sleeper, sleeper], grace_seconds=5, poll_seconds=0.05))
        """
    )
    proc = subprocess.Popen(
        [sys.executable, '-c', script],
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == 'ready'
        # Give supervise() a moment to install its handlers and spawn the children.
        time.sleep(1)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
