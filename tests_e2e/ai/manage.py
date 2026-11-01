"""Run a `manage.py` command against a running AI-agent test server's data.

    python tests_e2e/ai/manage.py [--port 8765] <manage.py arguments>

For the "(shell step)" rows of the test plans, e.g.
`python tests_e2e/ai/manage.py shell -c "from apps.todo.jobs import send_reminders; send_reminders()"`.
It uses the environment `serve.py --port <port>` recorded when it started: the same database,
bucket, mail (an email the command sends shows up in `/__e2e__/mail/`) and settings.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests_e2e import e2e_env  # noqa: E402


def main(argv: list[str]) -> int:
    port = e2e_env.DEFAULT_PORT
    if argv[:1] == ['--port'] and len(argv) > 1:
        port, argv = int(argv[1]), argv[2:]
    elif argv[:1] and argv[0].startswith('--port='):
        port, argv = int(argv[0].split('=', 1)[1]), argv[1:]
    if not argv:
        print(__doc__)
        return 2
    try:
        env = e2e_env.read_state(port)
    except FileNotFoundError:
        print(f'No test server was started on port {port}: run serve.py first.')
        return 1
    command = [sys.executable, 'manage.py', *argv]
    return subprocess.run(command, env={**os.environ, **env}, cwd=ROOT).returncode


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
