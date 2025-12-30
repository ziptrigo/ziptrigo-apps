import os
import subprocess
import time
import pytest
from playwright.sync_api import sync_playwright

@pytest.fixture(scope='session')
def users_server():
    # Start the users service
    # We need to make sure we are in the 'users' directory
    # and have the environment variables set.
    env = os.environ.copy()
    env['PYTHONPATH'] = os.getcwd()
    
    # Using a temporary database for E2E tests would be better, 
    # but for now we'll assume the dev environment is set up.
    
    process = subprocess.Popen(
        ['python', 'manage.py', 'runserver', '8010', '--noreload'],
        cwd='users',
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Give it a few seconds to start
    time.sleep(3)
    
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        pytest.fail(f'Users server failed to start:\nSTDOUT: {stdout}\nSTDERR: {stderr}')
    
    yield 'http://localhost:8010'
    
    process.terminate()
    process.wait()
