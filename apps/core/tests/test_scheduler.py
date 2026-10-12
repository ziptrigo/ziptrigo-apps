import time
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import ScheduledJob
from apps.core.scheduler import JobSpec, clear_registry, get_jobs, register, run_due_jobs, try_claim

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clear_registry():
    clear_registry()
    yield
    clear_registry()


def test_register_and_get_jobs():
    spec = JobSpec(name='test.job', func=lambda: None, interval=timedelta(minutes=1))
    register(spec)
    assert get_jobs() == [spec]


def test_registering_same_name_replaces_previous():
    register(JobSpec(name='test.job', func=lambda: None, interval=timedelta(minutes=1)))
    second = JobSpec(name='test.job', func=lambda: None, interval=timedelta(minutes=5))
    register(second)
    assert get_jobs() == [second]


def test_try_claim_succeeds_first_time_then_fails_until_due_again():
    spec = JobSpec(name='test.job', func=lambda: None, interval=timedelta(hours=1))
    ScheduledJob.objects.create(name=spec.name, interval_seconds=3600)

    assert try_claim(spec) is True
    # Immediately trying again: the lease is held and it isn't due yet either way.
    assert try_claim(spec) is False


def test_try_claim_succeeds_again_once_lease_expires():
    spec = JobSpec(
        name='test.job', func=lambda: None, interval=timedelta(hours=1), lease=timedelta(seconds=1)
    )
    row = ScheduledJob.objects.create(name=spec.name, interval_seconds=3600)
    assert try_claim(spec) is True

    # Simulate the lease having expired and the job being due again.
    ScheduledJob.objects.filter(pk=row.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1),
        last_started_at=timezone.now() - timedelta(hours=2),
    )
    assert try_claim(spec) is True


def test_run_due_jobs_runs_registered_job_and_records_success():
    calls = []
    register(JobSpec(name='test.job', func=lambda: calls.append(1), interval=timedelta(hours=1)))

    ran = run_due_jobs()

    assert ran == ['test.job']
    assert calls == [1]
    row = ScheduledJob.objects.get(name='test.job')
    assert row.run_count == 1
    assert row.last_success_at is not None
    assert row.last_error == ''


def test_run_due_jobs_records_error_and_leaves_run_count(monkeypatch):
    def _boom():
        raise RuntimeError('boom')

    register(JobSpec(name='test.job', func=_boom, interval=timedelta(hours=1)))

    ran = run_due_jobs()

    assert ran == ['test.job']
    row = ScheduledJob.objects.get(name='test.job')
    assert row.run_count == 0
    assert 'boom' in row.last_error
    assert row.last_success_at is None


def test_run_due_jobs_skips_jobs_not_yet_due():
    calls = []
    register(JobSpec(name='test.job', func=lambda: calls.append(1), interval=timedelta(hours=1)))

    run_due_jobs()
    ran_again = run_due_jobs()

    assert ran_again == []
    assert calls == [1]


def _make_due_again(name: str) -> None:
    """Move the last start back past the interval, leaving `locked_until` as the run left it."""
    ScheduledJob.objects.filter(name=name).update(
        last_started_at=timezone.now() - timedelta(minutes=6)
    )


def test_a_finished_job_releases_its_lease_and_runs_again_after_its_interval():
    # receipts.retry_due's shape: a lease longer than the interval. Before the lease was released
    # on finish, the job only ran every 10 minutes.
    calls = []
    register(
        JobSpec(
            name='test.job',
            func=lambda: calls.append(1),
            interval=timedelta(minutes=5),
            lease=timedelta(minutes=10),
        )
    )

    run_due_jobs()
    assert ScheduledJob.objects.get(name='test.job').locked_until is None

    _make_due_again('test.job')
    assert run_due_jobs() == ['test.job']
    assert calls == [1, 1]


def test_a_failed_job_releases_its_lease_too():
    def _boom():
        raise RuntimeError('boom')

    register(
        JobSpec(
            name='test.job', func=_boom, interval=timedelta(minutes=5), lease=timedelta(hours=1)
        )
    )

    run_due_jobs()

    assert ScheduledJob.objects.get(name='test.job').locked_until is None


def test_a_run_that_outlived_its_lease_does_not_release_a_newer_claim():
    spec_holder: list[JobSpec] = []

    def _slow():
        # While this run is still going, its lease expires and another runner claims the job. A
        # real re-claim comes a whole lease later; sleep so it isn't stamped in the same
        # millisecond as this run's claim (SQLite's `Now()` resolution).
        time.sleep(0.01)
        ScheduledJob.objects.filter(name='test.job').update(
            locked_until=timezone.now() - timedelta(seconds=1),
            last_started_at=timezone.now() - timedelta(hours=2),
        )
        assert try_claim(spec_holder[0]) is True

    spec = JobSpec(name='test.job', func=_slow, interval=timedelta(hours=1))
    spec_holder.append(spec)
    register(spec)

    run_due_jobs()

    # The newer claim's lease is still held, so nobody else can start the job.
    row = ScheduledJob.objects.get(name='test.job')
    assert row.locked_until is not None and row.locked_until > timezone.now()


def test_a_claim_that_never_finishes_keeps_its_lease():
    # A process killed mid-job never reaches `_run_job`'s release: the lease must hold.
    spec = JobSpec(
        name='test.job', func=lambda: None, interval=timedelta(minutes=5), lease=timedelta(hours=1)
    )
    ScheduledJob.objects.create(name=spec.name, interval_seconds=300)
    assert try_claim(spec) is True

    _make_due_again('test.job')

    assert try_claim(spec) is False
