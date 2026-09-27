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
