"""`apps.core.services.ip_retention.clear_old_ips`, the rule behind the products' report IP jobs.
The products' own tests check each job; these use QR code reports as the model, since `core` has
no model with an IP of its own (tests are exempt from the import contract)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.services import ip_retention
from apps.qr_code.models import QRShareReport
from apps.qr_code.tests.share_support import make_qrcode, make_report, make_user


@pytest.fixture
def qrcode(db):
    return make_qrcode(make_user())


def _aged(report, days):
    QRShareReport.objects.filter(pk=report.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )


def test_only_rows_past_the_retention_are_cleared(qrcode):
    old, edge, recent = (make_report(qrcode) for _ in range(3))
    _aged(old, 120)
    _aged(edge, 90)
    _aged(recent, 89)

    assert ip_retention.clear_old_ips(QRShareReport.objects.all()) == 2

    ips = dict(QRShareReport.objects.values_list('pk', 'reporter_ip'))
    assert ips[old.pk] is None
    assert ips[edge.pk] is None
    assert ips[recent.pk] == '203.0.113.9'


def test_it_works_in_batches_and_is_idempotent(qrcode, django_assert_num_queries):
    for _ in range(5):
        _aged(make_report(qrcode), 100)

    # Three batches of two (2, 2, 1): a select and an update each, then the empty select.
    with django_assert_num_queries(7):
        assert ip_retention.clear_old_ips(QRShareReport.objects.all(), batch=2) == 5
    with django_assert_num_queries(1):
        assert ip_retention.clear_old_ips(QRShareReport.objects.all(), batch=2) == 0
    assert not QRShareReport.objects.filter(reporter_ip__isnull=False).exists()


def test_the_queryset_limits_what_is_cleared(qrcode):
    other = make_qrcode(make_user())
    mine, theirs = make_report(qrcode), make_report(other)
    _aged(mine, 100)
    _aged(theirs, 100)

    assert ip_retention.clear_old_ips(QRShareReport.objects.filter(qrcode=qrcode)) == 1
    theirs.refresh_from_db()
    assert theirs.reporter_ip == '203.0.113.9'
