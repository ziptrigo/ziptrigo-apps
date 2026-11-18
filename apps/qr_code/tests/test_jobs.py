"""The QR code app's scheduler job: forgetting the IP of old share-link reports."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.scheduler import get_jobs

from .. import jobs
from ..models import QRShareReport
from .share_support import make_qrcode, make_report, make_user

pytestmark = pytest.mark.django_db


def test_the_job_is_registered_daily():
    spec = next(j for j in get_jobs() if j.name == 'qr_code.purge_report_ips')
    assert spec.func is jobs.purge_report_ips
    assert spec.interval == timedelta(hours=24)
    assert spec.description


def test_reports_older_than_90_days_lose_their_ip():
    qrcode = make_qrcode(make_user())
    old, recent = make_report(qrcode), make_report(qrcode)
    QRShareReport.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))

    jobs.purge_report_ips()
    jobs.purge_report_ips()  # idempotent

    old.refresh_from_db()
    recent.refresh_from_db()
    assert old.reporter_ip is None
    assert recent.reporter_ip == '203.0.113.9'
    # The rest of the report is kept.
    assert old.reason == 'spam'


def test_an_error_propagates(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('database down')

    monkeypatch.setattr(jobs, 'clear_old_ips', boom)
    with pytest.raises(RuntimeError):
        jobs.purge_report_ips()
