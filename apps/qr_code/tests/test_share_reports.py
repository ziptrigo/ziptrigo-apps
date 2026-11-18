"""Reporting a shared QR code: the service (validation, the repeat window, availability), the
public form (htmx and plain, no login) and its rate limits."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.qr_code.models import QRShareLink, QRShareReport, ReportStatus
from apps.qr_code.services import (
    LinkUnavailableError,
    ReportUnavailableError,
    share,
    share_reports,
    takedown,
)

from .share_support import make_qrcode, make_report, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def qrcode(user):
    return make_qrcode(user, name='Reported door')


@pytest.fixture
def link(qrcode):
    return share.create_link(qrcode)


def report_url(link) -> str:
    return reverse('q:report', args=[link.code])


def fresh(link: QRShareLink) -> QRShareLink:
    return QRShareLink.objects.select_related('qrcode__created_by').get(pk=link.pk)


class TestService:
    def test_a_report_is_recorded_with_its_code_link_and_reporter(self, link):
        report = share_reports.create_report(
            fresh(link),
            reason='spam',
            details='ads',
            reporter_email='a@example.com',
            reporter_ip='203.0.113.7',
        )
        assert (report.qrcode_id, report.link_id) == (link.qrcode_id, link.pk)
        assert (report.reason, report.details, report.reporter_email) == (
            'spam',
            'ads',
            'a@example.com',
        )
        assert (report.reporter_ip, report.status) == ('203.0.113.7', ReportStatus.PENDING)

    def test_a_bad_reason_or_long_details_are_refused(self, link):
        with pytest.raises(ValidationError, match='Choose a reason'):
            share_reports.create_report(fresh(link), reason='nope')
        with pytest.raises(ValidationError, match='at most 2000'):
            share_reports.create_report(fresh(link), reason='spam', details='x' * 2001)
        assert QRShareReport.objects.count() == 0

    def test_a_repeat_from_the_same_address_inside_ten_minutes_is_refused(self, link):
        share_reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        with pytest.raises(ValidationError) as exc:
            share_reports.create_report(fresh(link), reason='other', reporter_ip='203.0.113.7')
        assert exc.value.messages == ['You already reported this QR code recently. Thank you.']
        QRShareReport.objects.update(created_at=timezone.now() - timedelta(minutes=11))
        share_reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        assert QRShareReport.objects.count() == 2

    def test_the_window_is_per_code_and_normalises_ipv6_to_its_64(self, user, link):
        share_reports.create_report(fresh(link), reason='spam', reporter_ip='2001:db8:1:2::1')
        with pytest.raises(ValidationError):
            share_reports.create_report(
                fresh(link), reason='spam', reporter_ip='2001:db8:1:2:ffff::9'
            )
        other = share.create_link(make_qrcode(user, name='Other'))
        share_reports.create_report(fresh(other), reason='spam', reporter_ip='2001:db8:1:2::1')

    def test_different_addresses_each_count(self, link):
        for ip in ('203.0.113.1', '203.0.113.2'):
            share_reports.create_report(fresh(link), reason='spam', reporter_ip=ip)
        assert QRShareReport.objects.count() == 2

    @pytest.mark.parametrize('how', ['revoked', 'deleted', 'taken_down', 'expired'])
    def test_a_link_that_no_longer_works_cannot_be_reported(self, qrcode, link, how):
        if how == 'revoked':
            share.revoke_link(fresh(link))
        elif how == 'deleted':
            qrcode.soft_delete()
        elif how == 'expired':
            QRShareLink.objects.filter(pk=link.pk).update(
                expires_at=timezone.now() - timedelta(minutes=1)
            )
        else:
            takedown.take_down_qrcode(qrcode, by=make_user(is_staff=True), reason='x')
        with pytest.raises(LinkUnavailableError) as exc:  # a typed error: the view never reads text
            share_reports.create_report(link, reason='spam')
        assert isinstance(exc.value, ReportUnavailableError)
        assert exc.value.messages == ['This QR code is no longer available.']
        assert QRShareReport.objects.count() == 0

    def test_a_repeat_is_not_the_unavailable_error(self, link):
        share_reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        with pytest.raises(ValidationError) as exc:
            share_reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        assert not isinstance(exc.value, LinkUnavailableError)

    def test_dismiss_is_conditional(self, link, qrcode):
        report = make_report(qrcode, link)
        staff = make_user(is_staff=True)
        assert share_reports.dismiss_report(report, by=staff, note='fine') is True
        report.refresh_from_db()
        assert (report.status, report.reviewed_by, report.resolution_note) == (
            ReportStatus.DISMISSED,
            staff,
            'fine',
        )
        assert share_reports.dismiss_report(report, by=staff) is False

    def test_a_report_survives_its_link_and_goes_with_its_code(self, link, qrcode):
        report = make_report(qrcode, link)
        QRShareLink.objects.filter(pk=link.pk).delete()
        report.refresh_from_db()
        assert report.link_id is None
        qrcode.delete()
        assert not QRShareReport.objects.filter(pk=report.pk).exists()


class TestTheForm:
    def test_no_login_is_needed_and_htmx_gets_the_thank_you(self, qrcode, link):
        response = Client().post(
            report_url(link), {'reason': 'phishing', 'details': 'it asks for passwords'}, **HTMX
        )
        assert response.status_code == 200
        assert 'Thank you, your report has been submitted.' in response.content.decode()
        report = QRShareReport.objects.get()
        assert (report.qrcode_id, report.reason, report.reporter_ip) == (
            qrcode.pk,
            'phishing',
            '127.0.0.1',
        )
        for header, value in (
            ('Cache-Control', 'no-store, private'),
            ('Referrer-Policy', 'strict-origin'),
            ('X-Robots-Tag', 'noindex, nofollow'),
        ):
            assert response[header] == value

    def test_a_plain_post_renders_the_whole_page_with_the_thank_you(self, link):
        response = Client().post(report_url(link), {'reason': 'spam'})
        body = response.content.decode()
        assert response.status_code == 200 and '<h1' in body and 'Thank you, your report' in body
        assert QRShareReport.objects.count() == 1

    def test_the_email_is_optional_and_validated(self, link):
        client = Client()
        ok = client.post(report_url(link), {'reason': 'spam', 'reporter_email': ''}, **HTMX)
        assert ok.status_code == 200
        bad = client.post(
            report_url(link),
            {'reason': 'spam', 'reporter_email': 'nope'},
            REMOTE_ADDR='203.0.113.50',
            **HTMX,
        )
        assert bad.status_code == 422

    def test_a_missing_reason_is_a_422_with_the_form_kept(self, link):
        response = Client().post(report_url(link), {'details': 'x'}, **HTMX)
        assert response.status_code == 422 and 'id="report-box"' in response.content.decode()
        assert QRShareReport.objects.count() == 0

    def test_a_missing_reason_without_htmx_re_renders_the_page_open(self, link):
        response = Client().post(report_url(link), {'details': 'x'})
        body = response.content.decode()
        assert response.status_code == 422 and '<h1' in body
        assert 'reportOpen: true' in body

    def test_a_repeat_is_a_form_error_not_a_second_row(self, link):
        client = Client()
        client.post(report_url(link), {'reason': 'spam'}, **HTMX)
        response = client.post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert response.status_code == 422
        assert 'You already reported this QR code recently.' in response.content.decode()
        assert QRShareReport.objects.count() == 1

    def test_when_the_link_became_unavailable_htmx_refreshes(self, link):
        share.revoke_link(fresh(link))
        response = Client().post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert response.status_code == 204 and response['HX-Refresh'] == 'true'
        assert response['Cache-Control'] == 'no-store, private'
        assert QRShareReport.objects.count() == 0

    def test_when_the_link_became_unavailable_a_plain_post_is_the_neutral_page(self, link):
        from django.template.loader import render_to_string

        share.revoke_link(fresh(link))
        response = Client().post(report_url(link), {'reason': 'spam'})
        assert response.status_code == 404
        assert response.content.decode() == render_to_string('qr_code/unavailable.html')

    def test_an_unknown_code_is_the_neutral_page_and_records_nothing(self):
        response = Client().post(reverse('q:report', args=['nope-nope-nope']), {'reason': 'spam'})
        assert response.status_code == 404 and QRShareReport.objects.count() == 0

    def test_only_post_works(self, link):
        assert Client().get(report_url(link)).status_code == 405

    def test_the_page_has_the_form_collapsed(self, link):
        body = Client().get(reverse('q:page', args=[link.code])).content.decode()
        assert 'Report this QR code' in body and f'action="{report_url(link)}"' in body
        assert 'reportOpen: false' in body and 'name="csrfmiddlewaretoken"' in body
        assert "won't share it with the owner of the QR code" in body or (
            'won&#x27;t share it with the owner of the QR code' in body
        )

    def test_the_csrf_check_applies(self, link):
        client = Client(enforce_csrf_checks=True)
        assert client.post(report_url(link), {'reason': 'spam'}).status_code == 403
        assert QRShareReport.objects.count() == 0

    def test_a_taken_down_codes_link_cannot_be_reported(self, qrcode, link):
        takedown.take_down_qrcode(qrcode, by=make_user(is_staff=True), reason='x')
        response = Client().post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert response.status_code == 204 and response['HX-Refresh'] == 'true'


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'QR_SHARE_REPORT_IP': (3, 3600),
            'QR_SHARE_REPORT_CODE': (2, 3600),
        }

    def test_every_submission_counts_against_the_ip(self, link):
        client = Client()
        for _ in range(3):
            client.post(report_url(link), {'reason': ''}, **HTMX)  # refused, still counted
        response = client.post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert response.status_code == 429 and response['HX-Retarget'] == '#report-msg'
        assert 'Retry-After' in response
        assert QRShareReport.objects.count() == 0

    def test_a_made_up_code_spends_the_ip_budget_but_not_any_codes(self, link):
        client = Client()
        for number in range(3):
            client.post(reverse('q:report', args=[f'made-up-{number}']), {'reason': 'spam'}, **HTMX)
        assert client.post(report_url(link), {'reason': 'spam'}, **HTMX).status_code == 429
        other = Client(REMOTE_ADDR='198.51.100.9')
        assert other.post(report_url(link), {'reason': 'spam'}, **HTMX).status_code == 200

    def test_the_code_ceiling_counts_only_reports_that_were_created(self, link):
        for number in range(5):  # failed attempts spend none of the code's budget
            Client(REMOTE_ADDR=f'198.51.100.{number}').post(
                report_url(link), {'reason': ''}, **HTMX
            )
        for number in range(2):
            ok = Client(REMOTE_ADDR=f'198.51.101.{number}').post(
                report_url(link), {'reason': 'spam'}, **HTMX
            )
            assert ok.status_code == 200
        over = Client(REMOTE_ADDR='198.51.102.1').post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert over.status_code == 429
        assert QRShareReport.objects.count() == 2

    def test_a_plain_over_the_limit_post_is_the_429_page(self, link):
        client = Client()
        for _ in range(3):
            client.post(report_url(link), {'reason': ''})
        response = client.post(report_url(link), {'reason': 'spam'})
        assert response.status_code == 429 and response['Cache-Control'] == 'no-store, private'
