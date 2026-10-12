import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest


@pytest.fixture(scope='session')
def site_server():
    # Start the site from the repo root.
    env = os.environ.copy()
    env.setdefault('ENVIRONMENT', 'local')

    # Using a temporary database for E2E tests would be better,
    # but for now we'll assume the local environment is set up.

    process = subprocess.Popen(
        ['python', 'manage.py', 'runserver', '8000', '--noreload'],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Give it a few seconds to start
    time.sleep(3)

    if process.poll() is not None:
        stdout, stderr = process.communicate()
        pytest.fail(f'Server failed to start:\nSTDOUT: {stdout}\nSTDERR: {stderr}')

    yield 'http://localhost:8000'

    process.terminate()
    process.wait()


class ReceiptsServer:
    """A running site with fake S3 and a fake AI (see `receipts_e2e_settings.py`), and one
    ready-to-use account."""

    def __init__(self, url: str, email: str, password: str, env: dict[str, str]):
        self.url = url
        self.email = email
        self.password = password
        self._env = env

    def shell(self, code: str) -> None:
        """Run `code` in a Django shell against the same database as the server."""
        subprocess.run(
            [sys.executable, 'manage.py', 'shell', '-c', code],
            env=self._env,
            check=True,
            capture_output=True,
            text=True,
        )

    def login(self, page) -> None:
        page.goto(f'{self.url}/account/login/')
        page.fill('input[name="email"]', self.email)
        page.fill('input[name="password"]', self.password)
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: '/account/login' not in url)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture(scope='session')
def receipts_server():
    """The site for the Receipts e2e tests.

    Unlike `site_server` it runs with `tests_e2e.receipts_e2e_settings`: its own SQLite file
    (`db.e2e.sqlite3`, recreated here), an in-memory S3 and AI, and tasks run inline -- see that
    module for how the fakes are wired. Needs `.env.local`, like `site_server`.
    """
    port = _free_port()
    url = f'http://localhost:{port}'
    email = f'receipts_{uuid.uuid4().hex[:8]}@example.com'
    password = secrets.token_urlsafe(16)
    env = os.environ.copy()
    env.setdefault('ENVIRONMENT', 'local')
    env.update(
        DJANGO_SETTINGS_MODULE='tests_e2e.receipts_e2e_settings',
        BASE_URL=url,
        E2E_EMAIL=email,
        E2E_PASSWORD=password,
    )

    database = Path(__file__).resolve().parents[1] / 'db.e2e.sqlite3'
    database.unlink(missing_ok=True)
    subprocess.run(
        [sys.executable, 'manage.py', 'migrate', '--noinput'],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    server = ReceiptsServer(url, email, password, env)
    server.shell(
        'import os\n'
        'from apps.accounts.models import User\n'
        'from apps.billing.services import add_credits\n'
        "user = User.objects.create_user(email=os.environ['E2E_EMAIL'],"
        " password=os.environ['E2E_PASSWORD'], name='E2E')\n"
        'user.email_confirmed = True\n'
        'user.save()\n'
        "add_credits(user, 50, description='e2e', source='e2e')\n"
    )

    process = subprocess.Popen(
        [sys.executable, 'manage.py', 'runserver', str(port), '--noreload'],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(60):
            if process.poll() is not None:
                pytest.fail(f'Server failed to start:\n{process.stderr.read()}')
            try:
                urllib.request.urlopen(f'{url}/account/login/', timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail('The server did not come up')
        yield server
    finally:
        process.terminate()
        process.wait()
        database.unlink(missing_ok=True)
