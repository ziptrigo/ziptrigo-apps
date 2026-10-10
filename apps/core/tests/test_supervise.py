import os
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

# A child that records its pid in the file given as argv[1] once it is fully set up, then sleeps.
_MARKING_CHILD = textwrap.dedent(
    """
    import os, signal, sys, time
    if sys.argv[2] == 'ignore-sigterm':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(sys.argv[1], 'w') as f:
        f.write(str(os.getpid()))
    time.sleep(60)
    """
)


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


def test_child_killed_by_signal_maps_to_128_plus_signum():
    killed = [sys.executable, '-c', 'import os, signal; os.kill(os.getpid(), signal.SIGKILL)']

    code = supervise.supervise([killed, _SLEEP], grace_seconds=2, poll_seconds=0.05)

    assert code == 137


@pytest.mark.parametrize(
    ('returncode', 'expected'), [(0, 0), (3, 3), (-9, 137), (-15, 143), (-11, 139)]
)
def test_exit_status(returncode, expected):
    assert supervise.exit_status(returncode) == expected


@pytest.mark.parametrize(
    ('env', 'warns'),
    [
        ({'ENVIRONMENT': 'prod'}, True),
        ({'ENVIRONMENT': 'prod', 'SCHEDULER_ENABLED': '0'}, True),
        ({'ENVIRONMENT': 'prod', 'SCHEDULER_ENABLED': 'false'}, True),
        ({'ENVIRONMENT': 'prod', 'SCHEDULER_ENABLED': '1'}, False),
        ({'ENVIRONMENT': 'prod', 'SCHEDULER_ENABLED': 'True'}, False),
        ({'ENVIRONMENT': 'dev'}, False),
        ({}, False),
    ],
)
def test_startup_warnings(env, warns):
    result = supervise.startup_warnings(env)

    assert bool(result) is warns
    if warns:
        assert 'SCHEDULER_ENABLED is off' in result[0]


def _start_supervisor(tmp_path: Path, modes: list[str], grace: float):
    """Run `supervise()` in a subprocess with one marking child per mode, and wait until every
    child has finished its own setup (so the supervisor's handlers certainly exist already)."""
    child_path = tmp_path / 'child.py'
    child_path.write_text(_MARKING_CHILD)
    markers = [tmp_path / f'child{i}.pid' for i in range(len(modes))]
    commands = [[sys.executable, str(child_path), str(m), mode] for m, mode in zip(markers, modes)]
    script = (
        'import sys, supervise\n'
        f'sys.exit(supervise.supervise({commands!r}, grace_seconds={grace}, poll_seconds=0.05))\n'
    )
    proc = subprocess.Popen(
        [sys.executable, '-c', script],
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 20
    while not all(m.exists() and m.read_text() for m in markers):
        assert time.monotonic() < deadline, 'children never started'
        assert proc.poll() is None
        time.sleep(0.05)
    return proc, [int(m.read_text()) for m in markers]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_sigterm_stops_children_and_exits_zero(tmp_path):
    proc, pids = _start_supervisor(tmp_path, ['default', 'default'], grace=5)
    try:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
        assert not any(_alive(pid) for pid in pids)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_child_ignoring_sigterm_is_killed_after_grace_and_status_is_137(tmp_path):
    proc, pids = _start_supervisor(tmp_path, ['ignore-sigterm', 'default'], grace=0.5)
    try:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 137
        assert not any(_alive(pid) for pid in pids)
        assert 'SIGKILLed' in proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
