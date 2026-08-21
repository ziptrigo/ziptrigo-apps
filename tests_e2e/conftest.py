import os
import subprocess
import time

import pytest


@pytest.fixture(scope='session')
def users_server():
    # Start the user service. It must run from its own directory (both services name their
    # settings package `config`) with the repo root importable for the `shared/` packages.
    env = os.environ.copy()
    env['PYTHONPATH'] = os.getcwd()
    env.setdefault('ENVIRONMENT', 'dev')

    # Using a temporary database for E2E tests would be better,
    # but for now we'll assume the dev environment is set up.

    process = subprocess.Popen(
        ['python', 'manage.py', 'runserver', '8010', '--noreload'],
        cwd='user-service',
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Give it a few seconds to start
    time.sleep(3)

    if process.poll() is not None:
        stdout, stderr = process.communicate()
        pytest.fail(f'Users server failed to start:\nSTDOUT: {stdout}\nSTDERR: {stderr}')

    yield 'http://localhost:8010'

    process.terminate()
    process.wait()
