import os
import subprocess
import time

import pytest


@pytest.fixture(scope='session')
def site_server():
    # Start the site from the repo root.
    env = os.environ.copy()
    env.setdefault('ENVIRONMENT', 'dev')

    # Using a temporary database for E2E tests would be better,
    # but for now we'll assume the dev environment is set up.

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
