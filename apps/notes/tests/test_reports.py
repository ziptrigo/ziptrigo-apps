"""Reporting a shared note: the service (validation, the repeat window, availability), the public
form (htmx and plain, no login) and its rate limits."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..models import NoteReport, ReportStatus, ShareLink
from ..services import (
    LinkUnavailableError,
    ReportUnavailableError,
    create_note,
    links,
    reports,
    takedown,
    trash,
)
from .factories import make_report, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def note(user, config):
    return create_note(user, title='Reported', content='content')


@pytest.fixture
def link(note):
    return links.create_link(note, mode='read')


def report_url(link) -> str:
    return reverse('n:report', args=[link.code])


def fresh(link: ShareLink) -> ShareLink:
    return ShareLink.objects.select_related('note__user').get(pk=link.pk)


class TestService:
    def test_a_report_is_recorded_with_its_note_link_and_reporter(self, link):
        report = reports.create_report(
            fresh(link),
            reason='spam',
            details='ads',
            reporter_email='a@example.com',
            reporter_ip='203.0.113.7',
        )
        assert (report.note_id, report.link_id) == (link.note_id, link.pk)
        assert (report.reason, report.details, report.reporter_email) == (
            'spam',
            'ads',
            'a@example.com',
        )
        assert (report.reporter_ip, report.status) == ('203.0.113.7', ReportStatus.PENDING)

    def test_a_bad_reason_or_long_details_are_refused(self, link):
        with pytest.raises(ValidationError, match='Choose a reason'):
            reports.create_report(fresh(link), reason='nope')
        with pytest.raises(ValidationError, match='at most 2000'):
            reports.create_report(fresh(link), reason='spam', details='x' * 2001)
        assert NoteReport.objects.count() == 0

    def test_a_repeat_from_the_same_address_inside_ten_minutes_is_refused(self, link):
        reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        with pytest.raises(ValidationError) as exc:
            reports.create_report(fresh(link), reason='other', reporter_ip='203.0.113.7')
        assert exc.value.messages == ['You already reported this note recently. Thank you.']
        NoteReport.objects.update(created_at=timezone.now() - timedelta(minutes=11))
        reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        assert NoteReport.objects.count() == 2

    def test_the_window_is_per_note_and_normalises_ipv6_to_its_64(self, user, config, link):
        reports.create_report(fresh(link), reason='spam', reporter_ip='2001:db8:1:2::1')
        with pytest.raises(ValidationError):
            reports.create_report(fresh(link), reason='spam', reporter_ip='2001:db8:1:2:ffff::9')
        other = links.create_link(create_note(user, title='Other'), mode='read')
        reports.create_report(fresh(other), reason='spam', reporter_ip='2001:db8:1:2::1')

    def test_different_addresses_each_count(self, link):
        for ip in ('203.0.113.1', '203.0.113.2'):
            reports.create_report(fresh(link), reason='spam', reporter_ip=ip)
        assert NoteReport.objects.count() == 2

    @pytest.mark.parametrize('how', ['revoked', 'trashed', 'taken_down', 'locked'])
    def test_a_link_that_no_longer_works_cannot_be_reported(self, note, link, how):
        if how == 'revoked':
            links.revoke_link(fresh(link))
        elif how == 'trashed':
            trash(note)
        elif how == 'locked':
            type(note).objects.filter(pk=note.pk).update(locked_at=timezone.now())
        else:
            takedown.take_down_note(note, by=make_user(is_staff=True), reason='x')
        with pytest.raises(LinkUnavailableError) as exc:  # a typed error: the view never reads text
            reports.create_report(link, reason='spam')
        assert isinstance(exc.value, ReportUnavailableError)
        assert exc.value.messages == ['This note is no longer available.']
        assert NoteReport.objects.count() == 0

    def test_a_repeat_is_not_the_unavailable_error(self, link):
        reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        with pytest.raises(ValidationError) as exc:
            reports.create_report(fresh(link), reason='spam', reporter_ip='203.0.113.7')
        assert not isinstance(exc.value, LinkUnavailableError)

    def test_dismiss_is_conditional(self, link, note):
        report = make_report(note, link)
        staff = make_user(is_staff=True)
        assert reports.dismiss_report(report, by=staff, note='fine') is True
        report.refresh_from_db()
        assert (report.status, report.reviewed_by, report.resolution_note) == (
            ReportStatus.DISMISSED,
            staff,
            'fine',
        )
        assert reports.dismiss_report(report, by=staff) is False

    def test_a_report_survives_its_link_and_goes_with_its_note(self, link, note):
        report = make_report(note, link)
        ShareLink.objects.filter(pk=link.pk).delete()
        report.refresh_from_db()
        assert report.link_id is None
        note.delete()
        assert not NoteReport.objects.filter(pk=report.pk).exists()


class TestTheForm:
    def test_no_login_is_needed_and_htmx_gets_the_thank_you(self, note, link):
        response = Client().post(
            report_url(link), {'reason': 'phishing', 'details': 'it asks for passwords'}, **HTMX
        )
        assert response.status_code == 200
        assert 'Thank you, your report has been submitted.' in response.content.decode()
        report = NoteReport.objects.get()
        assert (report.note_id, report.reason, report.reporter_ip) == (
            note.pk,
            'phishing',
            '127.0.0.1',
        )
        assert response['Cache-Control'] == 'no-store, private'

    def test_a_plain_post_renders_the_whole_page_with_the_thank_you(self, link):
        response = Client().post(report_url(link), {'reason': 'spam'})
        body = response.content.decode()
        assert response.status_code == 200 and '<h1' in body and 'Thank you, your report' in body
        assert NoteReport.objects.count() == 1

    def test_the_email_is_optional_and_validated(self, link):
        client = Client()
        assert (
            client.post(
                report_url(link), {'reason': 'spam', 'reporter_email': ''}, **HTMX
            ).status_code
            == 200
        )
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
        assert NoteReport.objects.count() == 0

    def test_a_repeat_is_a_form_error_not_a_second_row(self, link):
        client = Client()
        client.post(report_url(link), {'reason': 'spam'}, **HTMX)
        response = client.post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert response.status_code == 422
        assert 'You already reported this note recently.' in response.content.decode()
        assert NoteReport.objects.count() == 1

    def test_when_the_link_became_unavailable_htmx_refreshes(self, link):
        links.revoke_link(fresh(link))
        response = Client().post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert response.status_code == 204 and response['HX-Refresh'] == 'true'
        assert NoteReport.objects.count() == 0

    def test_the_page_has_the_form_collapsed(self, link):
        body = Client().get(reverse('n:page', args=[link.code])).content.decode()
        assert 'Report this note' in body and f'action="{report_url(link)}"' in body
        assert 'We won&#x27;t share it with the owner of the note.' in body or (
            "won't share it with the owner of the note" in body
        )


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _limits(self, settings, ratelimit_clock):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'NOTES_REPORT_IP': (3, 3600),
            'NOTES_REPORT_NOTE': (2, 3600),
        }

    def test_every_submission_counts_against_the_ip(self, link):
        client = Client()
        for _ in range(3):
            client.post(report_url(link), {'reason': ''}, **HTMX)  # refused, still counted
        response = client.post(report_url(link), {'reason': 'spam'}, **HTMX)
        assert response.status_code == 429 and response['HX-Retarget'] == '#report-msg'
        assert NoteReport.objects.count() == 0

    def test_the_note_ceiling_counts_only_reports_that_were_created(self, link):
        for number in range(5):  # failed attempts spend none of the note's budget
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
        assert NoteReport.objects.count() == 2
