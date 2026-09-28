"""Tests for `core`'s own scheduled jobs: purging old `EmailVerification` rows (issue #58), and
purging expired `RateLimitCounter` rows (issue #53 code review)."""

from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.core.jobs import purge_expired_rate_limit_counters, purge_old_email_verifications
from apps.core.models import EmailVerification, RateLimitCounter
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


def test_purge_old_email_verifications_deletes_old_rows():
    old_id = start('old@example.com', 'test.purpose', build_email=_build_email)
    EmailVerification.objects.filter(pk=old_id).update(
        created_at=timezone.now() - timedelta(days=31)
    )
    recent_id = start('recent@example.com', 'test.purpose', build_email=_build_email)

    purge_old_email_verifications()

    assert not EmailVerification.objects.filter(pk=old_id).exists()
    assert EmailVerification.objects.filter(pk=recent_id).exists()


def test_purge_old_email_verifications_swallows_errors(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.core.jobs.purge_old', _boom)

    # Must not raise -- the scheduler's `run_due_jobs` relies on that to keep other jobs running.
    purge_old_email_verifications()


def test_purge_expired_rate_limit_counters_deletes_expired_rows(settings):
    settings.RATELIMIT_STORAGE = 'db'
    now = timezone.now()
    RateLimitCounter.objects.create(key='expired', count=1, expires_at=now - timedelta(minutes=1))
    RateLimitCounter.objects.create(key='current', count=1, expires_at=now + timedelta(minutes=1))

    purge_expired_rate_limit_counters()

    assert not RateLimitCounter.objects.filter(key='expired').exists()
    assert RateLimitCounter.objects.filter(key='current').exists()


def test_purge_expired_rate_limit_counters_swallows_errors(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.core.jobs.purge_expired', _boom)

    purge_expired_rate_limit_counters()
