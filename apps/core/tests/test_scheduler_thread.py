import threading

import pytest

from apps.core.scheduler import SchedulerRunner, start_scheduler_thread
from apps.core.scheduler import runner as runner_module

pytestmark = pytest.mark.unit


def test_disabled_by_default_starts_nothing(settings):
    settings.SCHEDULER_ENABLED = False

    assert start_scheduler_thread() is None


def test_enabled_runs_ticks_and_stops(settings, monkeypatch):
    settings.SCHEDULER_ENABLED = True
    settings.SCHEDULER_TICK_SECONDS = 1
    ticked = threading.Event()

    def fake_run_due_jobs() -> list[str]:
        ticked.set()
        return []

    monkeypatch.setattr(runner_module, 'run_due_jobs', fake_run_due_jobs)
    monkeypatch.setattr(runner_module, 'close_old_connections', lambda: None)
    monkeypatch.setattr(runner_module.connections, 'close_all', lambda: None)

    runner = start_scheduler_thread()
    try:
        assert runner is not None
        assert runner.thread is not None and runner.thread.name == 'scheduler'
        assert ticked.wait(5)
    finally:
        if runner is not None:
            runner.stop()

    assert runner.thread is not None
    runner.thread.join(5)
    assert not runner.thread.is_alive()


def test_run_forever_closes_old_connections_each_tick(monkeypatch):
    calls = []
    runner = SchedulerRunner(tick_seconds=0)

    def fake_close_old_connections() -> None:
        calls.append(1)
        if len(calls) >= 2:
            runner.stop()

    monkeypatch.setattr(runner_module, 'close_old_connections', fake_close_old_connections)
    monkeypatch.setattr(runner_module, 'run_due_jobs', lambda: [])
    monkeypatch.setattr(runner_module.connections, 'close_all', lambda: None)

    runner.run_forever()

    assert len(calls) == 2
