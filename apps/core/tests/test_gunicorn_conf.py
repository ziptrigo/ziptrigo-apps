import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.core.scheduler import runner as runner_module

pytestmark = pytest.mark.unit

_CONF_PATH = Path(__file__).resolve().parents[3] / 'gunicorn.conf.py'


@pytest.fixture
def conf():
    # Not a package (gunicorn loads it by path), so load a fresh copy per test: it keeps the
    # runner in a module global.
    spec = importlib.util.spec_from_file_location('gunicorn_conf_under_test', _CONF_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def quiet_runner(monkeypatch):
    monkeypatch.setattr(runner_module, 'run_due_jobs', lambda: [])
    monkeypatch.setattr(runner_module, 'close_old_connections', lambda: None)
    monkeypatch.setattr(runner_module.connections, 'close_all', lambda: None)


def test_graceful_timeout_is_below_the_supervisor_grace(conf):
    assert conf.graceful_timeout < 30


def test_hooks_start_then_stop_and_join_the_scheduler_thread(conf, settings, quiet_runner):
    settings.SCHEDULER_ENABLED = True
    settings.SCHEDULER_TICK_SECONDS = 1

    conf.post_worker_init(SimpleNamespace())
    runner = conf._runner
    try:
        assert runner is not None
        assert isinstance(runner.thread, threading.Thread)
        assert runner.thread.is_alive()

        conf.worker_exit(SimpleNamespace(), SimpleNamespace())

        assert not runner.thread.is_alive()
    finally:
        if runner is not None:
            runner.stop()
            if runner.thread is not None:
                runner.thread.join(5)


def test_post_worker_init_does_nothing_when_disabled(conf, settings):
    settings.SCHEDULER_ENABLED = False

    conf.post_worker_init(SimpleNamespace())
    conf.worker_exit(SimpleNamespace(), SimpleNamespace())  # no runner: must not raise

    assert conf._runner is None
