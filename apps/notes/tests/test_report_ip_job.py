"""The Notes job that forgets the IP of old note reports."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.scheduler import get_jobs

from .. import jobs
from ..models import NoteReport
from .factories import make_link, make_note, make_report

pytestmark = pytest.mark.django_db


def test_the_job_is_registered_daily():
    spec = next(j for j in get_jobs() if j.name == 'notes.purge_report_ips')
    assert spec.func is jobs.purge_report_ips
    assert spec.interval == timedelta(hours=24)
    assert spec.description


def test_reports_older_than_90_days_lose_their_ip(user):
    note = make_note(user)
    link = make_link(note)
    old = make_report(note, link, reporter_ip='198.51.100.7')
    recent = make_report(note, link, reporter_ip='198.51.100.8')
    NoteReport.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))

    jobs.purge_report_ips()
    jobs.purge_report_ips()  # idempotent

    old.refresh_from_db()
    recent.refresh_from_db()
    assert old.reporter_ip is None
    assert old.link_id == link.pk
    assert recent.reporter_ip == '198.51.100.8'


def test_an_error_propagates(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('database down')

    monkeypatch.setattr(jobs, 'clear_old_ips', boom)
    with pytest.raises(RuntimeError):
        jobs.purge_report_ips()
