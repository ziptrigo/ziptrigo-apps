"""Tests for `core`'s own scheduled jobs: purging old `EmailVerification` rows (issue #58),
purging expired `RateLimitCounter` rows (issue #53 code review), and deleting old notifications
(issue #169)."""

from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.contrib.sessions.models import Session
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from apps.core.jobs import (
    NOTIFICATION_RETENTION,
    TASK_RESULT_RETENTION,
    clear_expired_sessions,
    purge_expired_rate_limit_counters,
    purge_old_email_verifications,
    purge_old_notifications,
    purge_old_task_results,
)
from apps.core.models import EmailVerification, Notification, RateLimitCounter
from apps.core.scheduler import get_jobs
from apps.core.services.email_verification import EmailVerificationContext, start

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
    return 'subject', 'text', 'html'


def test_register_jobs_registers_the_purge_job():
    # Re-run `CoreConfig._register_jobs` (rather than relying on whatever the registry still
    # holds from `ready()` at process start) so this doesn't depend on test ordering against
    # `test_scheduler.py`'s `clear_registry()` fixture.
    django_apps.get_app_config('core')._register_jobs()

    names = [job.name for job in get_jobs()]
    assert 'core.purge_old_email_verifications' in names
    assert 'core.purge_expired_rate_limit_counters' in names
    assert 'core.purge_old_task_results' in names
    assert 'core.purge_old_notifications' in names
    assert 'core.clear_expired_sessions' in names


def test_purge_old_email_verifications_deletes_old_rows():
    old_id = start('old@example.com', 'test.purpose', build_email=_build_email)
    EmailVerification.objects.filter(pk=old_id).update(
        created_at=timezone.now() - timedelta(days=31)
    )
    recent_id = start('recent@example.com', 'test.purpose', build_email=_build_email)

    purge_old_email_verifications()

    assert not EmailVerification.objects.filter(pk=old_id).exists()
    assert EmailVerification.objects.filter(pk=recent_id).exists()


def test_purge_old_email_verifications_lets_errors_propagate(monkeypatch):
    # The runner logs the exception and records the run as failed; swallowing it here would make
    # the scheduler show a failing job as a success.
    def _boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.core.jobs.purge_old', _boom)

    with pytest.raises(RuntimeError, match='boom'):
        purge_old_email_verifications()


def test_purge_expired_rate_limit_counters_deletes_expired_rows(settings):
    settings.RATELIMIT_STORAGE = 'db'
    now = timezone.now()
    RateLimitCounter.objects.create(key='expired', count=1, expires_at=now - timedelta(minutes=1))
    RateLimitCounter.objects.create(key='current', count=1, expires_at=now + timedelta(minutes=1))

    purge_expired_rate_limit_counters()

    assert not RateLimitCounter.objects.filter(key='expired').exists()
    assert RateLimitCounter.objects.filter(key='current').exists()


def test_purge_expired_rate_limit_counters_lets_errors_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.core.jobs.purge_expired', _boom)

    with pytest.raises(RuntimeError, match='boom'):
        purge_expired_rate_limit_counters()


def test_the_runner_records_a_failing_core_job_as_failed(monkeypatch):
    from apps.core.models import ScheduledJob
    from apps.core.scheduler import JobSpec, clear_registry, register, run_due_jobs

    def _boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.core.jobs.purge_old', _boom)
    clear_registry()
    try:
        register(
            JobSpec(
                name='core.purge_old_email_verifications',
                func=purge_old_email_verifications,
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
        run_due_jobs()
    finally:
        clear_registry()
        django_apps.get_app_config('core')._register_jobs()

    row = ScheduledJob.objects.get(name='core.purge_old_email_verifications')
    assert row.last_success_at is None
    assert 'boom' in row.last_error


def _task_result(status: str, age: timedelta, finished: bool = True) -> DBTaskResult:
    now = timezone.now()
    return DBTaskResult.objects.create(
        status=status,
        enqueued_at=now - age,
        finished_at=now - age if finished else None,
        args_kwargs={'args': [], 'kwargs': {}},
        task_path='apps.core.tests.test_jobs.noop',
        queue_name='default',
        backend_name='default',
    )


def test_purge_old_task_results_deletes_only_old_finished_rows():
    old = TASK_RESULT_RETENTION + timedelta(days=1)
    old_ok = _task_result('SUCCESSFUL', old)
    old_failed = _task_result('FAILED', old)
    recent_ok = _task_result('SUCCESSFUL', timedelta(days=1))
    old_ready = _task_result('READY', old, finished=False)
    old_running = _task_result('RUNNING', old, finished=False)

    purge_old_task_results()
    purge_old_task_results()  # idempotent

    assert set(DBTaskResult.objects.values_list('pk', flat=True)) == {
        recent_ok.pk,
        old_ready.pk,
        old_running.pk,
    }
    assert not DBTaskResult.objects.filter(pk__in=[old_ok.pk, old_failed.pk]).exists()


def test_purge_old_task_results_runs_every_batch(monkeypatch):
    monkeypatch.setattr('apps.core.jobs.PURGE_BATCH', 1)
    old = TASK_RESULT_RETENTION + timedelta(days=1)
    for status in ('SUCCESSFUL', 'FAILED', 'SUCCESSFUL'):
        _task_result(status, old)
    recent = _task_result('SUCCESSFUL', timedelta(days=1))

    purge_old_task_results()

    assert list(DBTaskResult.objects.values_list('pk', flat=True)) == [recent.pk]


def test_clear_expired_sessions_deletes_only_expired_sessions():
    now = timezone.now()
    Session.objects.create(
        session_key='expired', session_data='x', expire_date=now - timedelta(minutes=1)
    )
    Session.objects.create(
        session_key='live', session_data='x', expire_date=now + timedelta(days=1)
    )

    clear_expired_sessions()

    assert list(Session.objects.values_list('session_key', flat=True)) == ['live']


def _notification(user, age: timedelta, **fields) -> Notification:
    notification = Notification.objects.create(user_id=user.pk, source='t', title='x', **fields)
    Notification.objects.filter(pk=notification.pk).update(created_at=timezone.now() - age)
    return notification


def test_notifications_are_kept_for_ninety_days():
    assert NOTIFICATION_RETENTION == timedelta(days=90)


def test_purge_old_notifications_deletes_only_the_old_ones(user):
    old = _notification(user, NOTIFICATION_RETENTION + timedelta(days=1))
    # Past the cutoff whatever their state: unread, read and dismissed alike.
    old_read = _notification(
        user, NOTIFICATION_RETENTION + timedelta(days=5), read_at=timezone.now()
    )
    old_dismissed = _notification(
        user, NOTIFICATION_RETENTION + timedelta(days=9), dismissed_at=timezone.now()
    )
    recent = _notification(user, NOTIFICATION_RETENTION - timedelta(days=1))
    fresh = _notification(user, timedelta())

    purge_old_notifications()

    assert set(Notification.objects.all()) == {recent, fresh}
    for gone in (old, old_read, old_dismissed):
        assert not Notification.objects.filter(pk=gone.pk).exists()


def test_purge_old_notifications_works_in_batches(user, monkeypatch):
    monkeypatch.setattr('apps.core.jobs.PURGE_BATCH', 2)
    for _ in range(5):
        _notification(user, NOTIFICATION_RETENTION + timedelta(days=1))
    keep = _notification(user, timedelta())

    purge_old_notifications()

    assert list(Notification.objects.all()) == [keep]


def test_purge_old_notifications_with_nothing_to_do(user):
    purge_old_notifications()

    assert not Notification.objects.exists()


def test_purge_old_notifications_lets_errors_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.core.jobs.Notification.objects', type('M', (), {'filter': _boom})())

    with pytest.raises(RuntimeError, match='boom'):
        purge_old_notifications()


def test_the_notification_job_is_registered_daily():
    django_apps.get_app_config('core')._register_jobs()

    [job] = [job for job in get_jobs() if job.name == 'core.purge_old_notifications']
    assert job.interval == timedelta(hours=24)
