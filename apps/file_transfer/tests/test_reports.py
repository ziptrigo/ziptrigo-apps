"""Abuse reports (issue #59): the `services.reports.create_report` service, and the public "report
this transfer" form on the download page (`views.download.report_transfer`).
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from ..models import AbuseReport, AbuseReportReason, AbuseReportStatus, TransferStatus
from ..services import password as password_service
from ..services.reports import create_report

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


# -- services.reports.create_report --


def test_create_report_records_fields(draft_transfer):
    _active(draft_transfer)

    report = create_report(
        draft_transfer,
        reason=AbuseReportReason.MALWARE,
        details='looks like malware',
        reporter_email='reporter@example.com',
        reporter_ip='203.0.113.1',
    )

    assert report.transfer_id == draft_transfer.id
    assert report.reason == AbuseReportReason.MALWARE
    assert report.status == AbuseReportStatus.PENDING
    assert report.reporter_email == 'reporter@example.com'
    assert report.reporter_ip == '203.0.113.1'


def test_create_report_rejects_unknown_reason(draft_transfer):
    with pytest.raises(ValidationError):
        create_report(draft_transfer, reason='not-a-real-reason')


def test_create_report_rejects_details_too_long(draft_transfer):
    with pytest.raises(ValidationError):
        create_report(draft_transfer, reason=AbuseReportReason.OTHER, details='x' * 2001)


def test_create_report_dedupes_same_ip_within_window(draft_transfer):
    create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.1')

    with pytest.raises(ValidationError):
        create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.1')

    assert AbuseReport.objects.filter(transfer=draft_transfer).count() == 1


def test_create_report_does_not_dedupe_different_ips(draft_transfer):
    create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.1')
    create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.2')
    assert AbuseReport.objects.filter(transfer=draft_transfer).count() == 2


def test_create_report_without_ip_never_dedupes(draft_transfer):
    create_report(draft_transfer, reason=AbuseReportReason.OTHER)
    create_report(draft_transfer, reason=AbuseReportReason.OTHER)
    assert AbuseReport.objects.filter(transfer=draft_transfer).count() == 2


# -- The public "report this transfer" form --


def test_report_form_shown_on_download_page(client, draft_transfer, uploaded_file):
    _active(draft_transfer)
    response = client.get(reverse('t:download', args=[draft_transfer.slug]))
    assert response.status_code == 200
    assert 'Report this transfer' in response.content.decode()


def test_report_transfer_success(client, draft_transfer, uploaded_file):
    _active(draft_transfer)

    response = client.post(
        reverse('t:report', args=[draft_transfer.slug]),
        data={'reason': AbuseReportReason.COPYRIGHT, 'details': 'stolen content'},
    )

    assert response.status_code == 200
    assert 'Thank you' in response.content.decode()
    report = AbuseReport.objects.get(transfer=draft_transfer)
    assert report.reason == AbuseReportReason.COPYRIGHT
    assert report.details == 'stolen content'


def test_report_transfer_invalid_reason_returns_422(client, draft_transfer, uploaded_file):
    _active(draft_transfer)

    response = client.post(
        reverse('t:report', args=[draft_transfer.slug]), data={'reason': 'not-a-choice'}
    )

    assert response.status_code == 422
    assert AbuseReport.objects.filter(transfer=draft_transfer).count() == 0


def test_report_transfer_does_not_require_password(client, draft_transfer, uploaded_file):
    """Issue #59: the report form must work even for a password-protected transfer, without the
    reporter needing (or being asked for) the password."""
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))

    response = client.post(
        reverse('t:report', args=[draft_transfer.slug]), data={'reason': AbuseReportReason.OTHER}
    )

    assert response.status_code == 200
    assert AbuseReport.objects.filter(transfer=draft_transfer).exists()


def test_report_transfer_unavailable_transfer_returns_404(client, draft_transfer):
    # Still a DRAFT -- never available.
    response = client.post(
        reverse('t:report', args=[draft_transfer.slug]), data={'reason': AbuseReportReason.OTHER}
    )
    assert response.status_code == 404
    assert AbuseReport.objects.count() == 0


def test_report_transfer_unknown_slug_returns_404(client):
    response = client.post(
        reverse('t:report', args=['does-not-exist']), data={'reason': AbuseReportReason.OTHER}
    )
    assert response.status_code == 404


def test_report_transfer_dedupe_shows_as_form_error(client, draft_transfer, uploaded_file):
    _active(draft_transfer)
    url = reverse('t:report', args=[draft_transfer.slug])
    client.post(url, data={'reason': AbuseReportReason.OTHER})

    response = client.post(url, data={'reason': AbuseReportReason.OTHER})

    assert response.status_code == 422
    assert AbuseReport.objects.filter(transfer=draft_transfer).count() == 1


def test_report_transfer_rate_limited_per_ip(client, draft_transfer, uploaded_file, settings):
    _active(draft_transfer)
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_REPORT_IP': (1, 60)}
    url = reverse('t:report', args=[draft_transfer.slug])

    client.post(url, data={'reason': AbuseReportReason.OTHER})
    response = client.post(url, data={'reason': AbuseReportReason.COPYRIGHT})

    assert response.status_code == 429
    assert 'Retry-After' in response
