"""Container PID 1: runs gunicorn and (optionally) the `django_tasks_db` queue worker.

`db_worker` installs signal handlers, which only work on a process's main thread, so it can't be a
thread inside gunicorn like the scheduler is. This supervisor starts both, forwards SIGTERM/SIGINT
to them, and exits as soon as either child exits so Docker restarts the whole container.

Stdlib only.
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping


def _log(message: str) -> None:
    print(f'supervise: {message}', file=sys.stderr, flush=True)


def build_commands(env: Mapping[str, str] = os.environ) -> list[list[str]]:
    commands = [
        [
            'gunicorn',
            'config.wsgi:application',
            '--bind',
            '0.0.0.0:8000',
            '--access-logfile',
            '-',
            '-c',
            'gunicorn.conf.py',
        ]
    ]
    if env.get('RUN_TASK_WORKER', '1').lower() in ('true', '1'):
        commands.append(['python', 'manage.py', 'db_worker'])
    return commands


def supervise(
    commands: list[list[str]], grace_seconds: float = 30.0, poll_seconds: float = 0.5
) -> int:
    stopping = False

    def _on_signal(signum: int, _frame) -> None:
        nonlocal stopping
        stopping = True
        _log(f'received signal {signum}, stopping children')

    previous = {
        signal.SIGTERM: signal.signal(signal.SIGTERM, _on_signal),
        signal.SIGINT: signal.signal(signal.SIGINT, _on_signal),
    }
    try:
        children: list[subprocess.Popen] = []
        for command in commands:
            child = subprocess.Popen(command)
            children.append(child)
            _log(f'started {command[0]} (pid {child.pid}): {" ".join(command)}')

        while not stopping and all(child.poll() is None for child in children):
            time.sleep(poll_seconds)

        # Children that exited on their own, before we asked anything to stop.
        exited_early = {
            child.pid: child.returncode for child in children if child.poll() is not None
        }
        for pid, code in exited_early.items():
            _log(f'child pid {pid} exited with code {code}')

        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + grace_seconds
        for child in children:
            try:
                child.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                _log(f'child pid {child.pid} ignored SIGTERM for {grace_seconds}s, killing it')
                child.kill()
                child.wait()

        if stopping:
            # Exiting 0 or via our SIGTERM (negative return code) both count as a clean stop.
            bad = [c.returncode for c in children if c.returncode not in (0, -signal.SIGTERM)]
            return bad[0] if bad else 0
        for pid, code in exited_early.items():
            if code != 0:
                return code
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    sys.exit(supervise(build_commands()))
