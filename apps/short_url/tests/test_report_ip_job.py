"""The Short URLs job that forgets the IP of old link reports."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.scheduler import get_jobs

from .. import jobs
from ..models import LinkReport
from .factories import create_link

pytestmark = pytest.mark.django_db


def test_the_job_is_registered_daily():
    spec = next(j for j in get_jobs() if j.name == 'short_url.purge_report_ips')
    assert spec.func is jobs.purge_report_ips
    assert spec.interval == timedelta(hours=24)
    assert spec.description


def test_reports_older_than_90_days_lose_their_ip(user):
    link = create_link(user)
    old = LinkReport.objects.create(link=link, reason='spam', reporter_ip='192.0.2.1')
    recent = LinkReport.objects.create(link=link, reason='spam', reporter_ip='192.0.2.2')
    no_ip = LinkReport.objects.create(link=link, reason='phishing')  # e.g. Safe Browsing's
    LinkReport.objects.filter(pk__in=[old.pk, no_ip.pk]).update(
        created_at=timezone.now() - timedelta(days=91)
    )

    jobs.purge_report_ips()
    jobs.purge_report_ips()  # idempotent

    old.refresh_from_db()
    recent.refresh_from_db()
    no_ip.refresh_from_db()
    assert old.reporter_ip is None
    assert old.reason == 'spam'
    assert recent.reporter_ip == '192.0.2.2'
    assert no_ip.reporter_ip is None


def test_an_error_propagates(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('database down')

    monkeypatch.setattr(jobs, 'clear_old_ips', boom)
    with pytest.raises(RuntimeError):
        jobs.purge_report_ips()
