"""Abuse reports: the service (validation, dedupe over normalized addresses, the auto-hold
threshold, dismissal and its hold release) and the public flow on the preview page."""

from datetime import timedelta

import pytest
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import RateLimitCounter

from ..models import (
    MAX_DETAILS_LENGTH,
    LinkReport,
    LinkStatus,
    ReportStatus,
    ShortLink,
    ShortUrlSettings,
)
from ..services import hold, reports
from .factories import create_link, make_user

pytestmark = pytest.mark.django_db


def report(link, ip='203.0.113.1', **fields):
    return reports.create_report(
        link, reason=fields.pop('reason', 'phishing'), reporter_ip=ip, **fields
    )


@pytest.fixture
def link(user):
    return create_link(user, code='Promo1', destination='https://shop.example.com/x')


@pytest.fixture
def threshold(db):
    def set_threshold(value: int) -> None:
        row = ShortUrlSettings.load()
        row.auto_hold_report_threshold = value
        row.save()

    return set_threshold


class TestCreateReport:
    def test_it_records_the_report(self, link):
        created = report(link, reason='malware', details='Drops a trojan', reporter_email='a@b.co')
        created.refresh_from_db()
        assert created.link_id == link.pk
        assert (created.reason, created.details, created.reporter_email) == (
            'malware',
            'Drops a trojan',
            'a@b.co',
        )
        assert created.reporter_ip == '203.0.113.1'
        assert created.status == ReportStatus.PENDING

    def test_an_unknown_reason_is_refused(self, link):
        with pytest.raises(ValidationError, match='Choose a reason'):
            reports.create_report(link, reason='because')
        assert not LinkReport.objects.exists()

    def test_details_have_a_cap(self, link):
        reports.create_report(link, reason='spam', details='x' * MAX_DETAILS_LENGTH)
        with pytest.raises(ValidationError, match='at most 2000'):
            reports.create_report(link, reason='spam', details='x' * (MAX_DETAILS_LENGTH + 1))

    def test_a_report_needs_no_address(self, link):
        assert reports.create_report(link, reason='spam').reporter_ip is None

    @pytest.mark.parametrize('status', [LinkStatus.TAKEN_DOWN, LinkStatus.DELETED])
    def test_an_ended_link_cannot_be_reported(self, link, status):
        ShortLink.objects.filter(pk=link.pk).update(status=status)
        with pytest.raises(ValidationError, match='no longer available'):
            report(link)

    def test_it_never_touches_the_link_without_a_threshold(self, link):
        for n in range(1, 8):
            report(link, ip=f'203.0.113.{n}')
        link.refresh_from_db()
        assert link.held_for_review_at is None


class TestDedupe:
    def test_the_same_address_twice_in_ten_minutes_is_a_repeat(self, link):
        report(link, ip='203.0.113.1')
        with pytest.raises(ValidationError, match='already reported'):
            report(link, ip='203.0.113.1')
        assert LinkReport.objects.count() == 1

    def test_another_address_is_a_second_report(self, link):
        report(link, ip='203.0.113.1')
        report(link, ip='203.0.113.2')
        assert LinkReport.objects.count() == 2

    def test_the_same_address_on_another_link_is_not_a_repeat(self, link, user):
        other = create_link(user, code='Other1')
        report(link)
        report(other)
        assert LinkReport.objects.count() == 2

    def test_after_the_window_it_is_a_new_report(self, link):
        first = report(link)
        LinkReport.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - reports.DEDUPE_WINDOW - timedelta(seconds=1)
        )
        report(link)
        assert LinkReport.objects.count() == 2

    def test_an_ipv6_address_rotating_inside_its_64_is_the_same_visitor(self, link):
        report(link, ip='2001:db8:1:2::1')
        with pytest.raises(ValidationError, match='already reported'):
            report(link, ip='2001:db8:1:2:aaaa:bbbb:cccc:dddd')
        report(link, ip='2001:db8:1:3::1')  # another /64 is another visitor
        assert LinkReport.objects.count() == 2

    def test_an_ipv4_mapped_address_is_its_ipv4(self, link):
        report(link, ip='203.0.113.5')
        with pytest.raises(ValidationError, match='already reported'):
            report(link, ip='::ffff:203.0.113.5')

    def test_reports_without_an_address_are_never_repeats(self, link):
        reports.create_report(link, reason='spam')
        reports.create_report(link, reason='spam')
        assert LinkReport.objects.count() == 2


class TestAutoHold:
    def test_it_is_off_by_default(self, link, threshold):
        for n in range(1, 6):
            report(link, ip=f'203.0.113.{n}')
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_the_link_is_held_at_the_threshold(self, link, threshold):
        threshold(3)
        report(link, ip='203.0.113.1')
        report(link, ip='203.0.113.2')
        link.refresh_from_db()
        assert link.held_for_review_at is None
        report(link, ip='203.0.113.3')
        link.refresh_from_db()
        assert link.held_for_review_at is not None
        assert link.status == LinkStatus.ACTIVE  # a flag, not a status

    def test_the_instance_passed_in_sees_the_hold(self, link, threshold):
        threshold(1)
        report(link)
        assert link.held_for_review_at is not None

    def test_one_visitor_cannot_hold_a_link_alone(self, link, threshold):
        threshold(2)
        report(link, ip='2001:db8:1:2::1')
        # Same /64: refused as a repeat inside the window...
        with pytest.raises(ValidationError):
            report(link, ip='2001:db8:1:2::2')
        # ...and, once the window passed, still one visitor for the threshold.
        LinkReport.objects.update(created_at=timezone.now() - timedelta(hours=1))
        report(link, ip='2001:db8:1:2::3')
        report(link, ip='::ffff:203.0.113.9')
        assert LinkReport.objects.count() == 3
        link.refresh_from_db()
        # 2 distinct normalized addresses (the /64 and the mapped IPv4): at the threshold.
        assert link.held_for_review_at is not None

    def test_one_64_counts_once_however_many_reports(self, link, threshold):
        threshold(2)
        for n in range(1, 4):
            report(link, ip=f'2001:db8:1:2::{n}')
            LinkReport.objects.update(created_at=timezone.now() - timedelta(hours=1))
        link.refresh_from_db()
        assert LinkReport.objects.count() == 3
        assert link.held_for_review_at is None

    def test_reports_without_an_address_do_not_count(self, link, threshold):
        threshold(2)
        reports.create_report(link, reason='spam')
        reports.create_report(link, reason='spam')
        report(link, ip='203.0.113.1')
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_only_pending_reports_count(self, link, threshold):
        staff = make_user(is_staff=True)
        threshold(2)
        first = report(link, ip='203.0.113.1')
        reports.dismiss_report(first, by=staff)
        report(link, ip='203.0.113.2')
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_an_ended_link_is_not_held(self, link, threshold):
        threshold(1)
        report(link)
        ShortLink.objects.filter(pk=link.pk).update(held_for_review_at=None)
        link.refresh_from_db()
        for status in (LinkStatus.TAKEN_DOWN, LinkStatus.DELETED):
            ShortLink.objects.filter(pk=link.pk).update(status=status)
            link.refresh_from_db()
            assert hold.maybe_hold_for_reports(link) is False

    def test_a_disabled_link_can_be_held(self, link, threshold):
        threshold(1)
        ShortLink.objects.filter(pk=link.pk).update(status=LinkStatus.DISABLED)
        link.refresh_from_db()
        report(link)
        link.refresh_from_db()
        assert link.held_for_review_at is not None

    def test_a_link_already_held_is_left_alone(self, link, threshold):
        threshold(1)
        report(link, ip='203.0.113.1')
        link.refresh_from_db()
        held_at = link.held_for_review_at
        report(link, ip='203.0.113.2')
        link.refresh_from_db()
        assert link.held_for_review_at == held_at

    def test_the_hold_is_a_conditional_write(self, link, threshold):
        threshold(1)
        report(link)
        link.refresh_from_db()
        stale = ShortLink.objects.get(pk=link.pk)
        stale.held_for_review_at = None  # a stale read: it still thinks the link is free
        assert hold.maybe_hold_for_reports(stale) is False  # the UPDATE matched nothing


class TestDismissAndRelease:
    def test_dismissing_records_who_and_when(self, link):
        staff = make_user(is_staff=True)
        created = report(link)
        assert reports.dismiss_report(created, by=staff, note='looks fine') is True
        created.refresh_from_db()
        assert created.status == ReportStatus.DISMISSED
        assert created.reviewed_by == staff and created.reviewed_at is not None
        assert created.resolution_note == 'looks fine'

    def test_dismissing_twice_does_nothing_the_second_time(self, link):
        staff = make_user(is_staff=True)
        created = report(link)
        assert reports.dismiss_report(created, by=staff) is True
        assert reports.dismiss_report(created, by=staff) is False

    def test_dismissing_the_last_pending_report_releases_the_hold(self, link, threshold):
        staff = make_user(is_staff=True)
        threshold(2)
        first = report(link, ip='203.0.113.1')
        second = report(link, ip='203.0.113.2')
        link.refresh_from_db()
        assert link.held_for_review_at is not None
        reports.dismiss_report(first, by=staff)
        link.refresh_from_db()
        assert link.held_for_review_at is not None  # one is still pending
        reports.dismiss_report(second, by=staff)
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_releasing_dismisses_the_pending_reports(self, link, threshold):
        staff = make_user(is_staff=True)
        threshold(1)
        report(link)
        link.refresh_from_db()
        reports.release_hold_and_dismiss(link, by=staff, note='released')
        link.refresh_from_db()
        assert link.held_for_review_at is None
        only = LinkReport.objects.get()
        assert only.status == ReportStatus.DISMISSED and only.resolution_note == 'released'
        # The very next report does not hold it again at once (it is the only pending one).
        threshold(2)
        report(link, ip='203.0.113.9')
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_release_hold_is_idempotent(self, link):
        hold.release_hold(link)
        hold.release_hold(link)
        link.refresh_from_db()
        assert link.held_for_review_at is None


HTMX = {'HTTP_HX_REQUEST': 'true'}


class TestReportOnThePreviewPage:
    def test_the_preview_offers_the_form(self, client, link):
        content = client.get('/s/Promo1+').content.decode()
        assert 'Report this link' in content
        assert 'hx-post="/s/Promo1/report"' in content
        assert 'name="reason"' in content and 'name="details"' in content
        assert 'name="reporter_email"' in content

    def test_an_htmx_report_returns_the_thank_you(self, client, link):
        response = client.post(
            '/s/Promo1/report', {'reason': 'phishing', 'details': 'fake login'}, **HTMX
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Thank you, your report has been submitted.' in content
        assert '<html' not in content  # just the box
        created = LinkReport.objects.get()
        assert created.link_id == link.pk and created.details == 'fake login'

    def test_the_report_records_the_client_address(self, client, link):
        client.post('/s/Promo1/report', {'reason': 'spam'}, HTTP_X_REAL_IP='198.51.100.7', **HTMX)
        assert LinkReport.objects.get().reporter_ip == '198.51.100.7'

    def test_a_plain_post_gets_the_whole_page_back(self, client, link):
        response = client.post('/s/Promo1/report', {'reason': 'spam'})
        assert response.status_code == 200
        content = response.content.decode()
        assert '<html' in content and 'Thank you, your report has been submitted.' in content
        assert LinkReport.objects.count() == 1

    def test_the_lookup_ignores_case_and_a_trailing_slash(self, client, link):
        assert client.post('/s/promo1/report/', {'reason': 'spam'}, **HTMX).status_code == 200

    def test_a_missing_reason_is_a_422_with_the_form_again(self, client, link):
        response = client.post('/s/Promo1/report', {'details': 'x'}, **HTMX)
        assert response.status_code == 422
        assert 'hx-post="/s/Promo1/report"' in response.content.decode()
        assert not LinkReport.objects.exists()

    def test_a_bad_email_is_a_422(self, client, link):
        response = client.post(
            '/s/Promo1/report', {'reason': 'spam', 'reporter_email': 'not-an-email'}, **HTMX
        )
        assert response.status_code == 422

    def test_overlong_details_are_a_422(self, client, link):
        response = client.post(
            '/s/Promo1/report', {'reason': 'spam', 'details': 'x' * 2001}, **HTMX
        )
        assert response.status_code == 422
        assert not LinkReport.objects.exists()

    def test_a_repeat_is_a_form_error_not_a_second_row(self, client, link):
        client.post('/s/Promo1/report', {'reason': 'spam'}, **HTMX)
        response = client.post('/s/Promo1/report', {'reason': 'spam'}, **HTMX)
        assert response.status_code == 422
        assert 'already reported this link' in response.content.decode()
        assert LinkReport.objects.count() == 1

    def test_a_plain_post_with_errors_shows_them_on_the_page(self, client, link):
        response = client.post('/s/Promo1/report', {'details': 'x'})
        assert response.status_code == 422
        assert '<html' in response.content.decode()

    def test_only_post_is_allowed(self, client, link):
        assert client.get('/s/Promo1/report').status_code == 405

    def test_no_login_is_needed_and_the_owner_is_not_exposed(self, client, link, user):
        response = client.post('/s/Promo1/report', {'reason': 'spam'}, **HTMX)
        assert user.email not in response.content.decode()

    def test_the_response_is_not_cached(self, client, link):
        response = client.post('/s/Promo1/report', {'reason': 'spam'}, **HTMX)
        assert response['Cache-Control'] == 'no-store, private'

    def test_a_report_counts_no_click(self, client, link):
        client.post('/s/Promo1/report', {'reason': 'spam'}, **HTMX)
        link.refresh_from_db()
        assert link.click_count == 0

    def test_the_reporting_threshold_holds_the_link_for_visitors(self, client, link, threshold):
        threshold(2)
        client.post('/s/Promo1/report', {'reason': 'spam'}, HTTP_X_REAL_IP='203.0.113.1', **HTMX)
        assert client.get('/s/Promo1').status_code == 302
        client.post('/s/Promo1/report', {'reason': 'spam'}, HTTP_X_REAL_IP='203.0.113.2', **HTMX)
        gone = client.get('/s/Promo1')
        assert gone.status_code == 404
        assert gone.content == client.get('/s/neverissued').content
        assert client.get('/s/Promo1+').status_code == 404


class TestReportingAUnavailableLink:
    @pytest.mark.parametrize(
        'fields',
        [
            {'status': LinkStatus.DISABLED},
            {'status': LinkStatus.DELETED},
            {'status': LinkStatus.TAKEN_DOWN},
            {'held_for_review_at': timezone.now()},
            {'expires_at': timezone.now() - timedelta(hours=1)},
        ],
    )
    def test_htmx_asks_the_page_to_reload_to_the_neutral_one(self, client, user, fields):
        gone = create_link(user, code='Gone01', **fields)
        response = client.post('/s/Gone01/report', {'reason': 'spam'}, **HTMX)
        assert response.status_code == 204
        assert response['HX-Refresh'] == 'true'
        assert not LinkReport.objects.filter(link=gone).exists()

    def test_a_plain_post_gets_the_neutral_404(self, client, user):
        create_link(user, code='Gone01', status=LinkStatus.DISABLED)
        response = client.post('/s/Gone01/report', {'reason': 'spam'})
        assert response.status_code == 404
        assert response.content == client.get('/s/neverissued').content

    def test_an_unknown_code_creates_nothing(self, client):
        assert client.post('/s/neverissued/report', {'reason': 'spam'}).status_code == 404
        assert not LinkReport.objects.exists()


class TestReportingAProtectedLink:
    @pytest.fixture
    def protected(self, user):
        return create_link(
            user,
            code='Locked1',
            title='Secret title',
            destination='https://hidden.example.com/place',
            password_hash=make_password('hunter2'),
        )

    def test_the_preview_offers_the_form_without_revealing_anything(self, client, protected):
        content = client.get('/s/Locked1+').content.decode()
        assert 'Report this link' in content
        assert 'hidden.example.com' not in content and 'Secret title' not in content

    def test_a_report_needs_no_password_and_unlocks_nothing(self, client, protected):
        response = client.post('/s/Locked1/report', {'reason': 'phishing'}, **HTMX)
        assert response.status_code == 200
        assert LinkReport.objects.get().link_id == protected.pk
        assert 'hidden.example.com' not in response.content.decode()
        assert client.get('/s/Locked1')['Location'] == '/s/Locked1/unlock'

    def test_a_plain_post_keeps_the_destination_hidden(self, client, protected):
        content = client.post('/s/Locked1/report', {'reason': 'phishing'}).content.decode()
        assert 'hidden.example.com' not in content and 'Secret title' not in content


@pytest.fixture
def limits(settings, ratelimit_clock):
    settings.RATELIMIT_ENABLE = True

    def set_rules(**rules):
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, **rules}

    return set_rules


def post_report(client, ip, code='Promo1', reason='spam'):
    return client.post(f'/s/{code}/report', {'reason': reason}, HTTP_X_REAL_IP=ip, **HTMX)


class TestReportRateLimits:
    def test_the_ip_rule_is_strict_and_counts_every_submission(self, client, link, limits):
        limits(SU_REPORT_IP=(2, 3600), SU_REPORT_LINK=(100, 3600))
        assert post_report(client, '203.0.113.1', reason='').status_code == 422  # invalid counts
        assert post_report(client, '203.0.113.1').status_code == 200
        blocked = post_report(client, '203.0.113.1')
        assert blocked.status_code == 429
        assert blocked['Retry-After']
        assert blocked['HX-Retarget'] == '#report-msg'
        assert LinkReport.objects.count() == 1

    def test_another_address_has_its_own_budget(self, client, link, limits):
        limits(SU_REPORT_IP=(1, 3600), SU_REPORT_LINK=(100, 3600))
        assert post_report(client, '203.0.113.1').status_code == 200
        assert post_report(client, '203.0.113.2').status_code == 200

    def test_the_link_ceiling_counts_only_reports_that_were_created(self, client, link, limits):
        limits(SU_REPORT_IP=(100, 3600), SU_REPORT_LINK=(2, 3600))
        # Failed attempts (no reason, the dedupe window) don't spend the link's budget.
        for _ in range(4):
            assert post_report(client, '203.0.113.1', reason='').status_code == 422
        assert post_report(client, '203.0.113.1').status_code == 200
        assert post_report(client, '203.0.113.1').status_code == 422  # dedupe: not counted
        assert post_report(client, '203.0.113.2').status_code == 200
        blocked = post_report(client, '203.0.113.3')
        assert blocked.status_code == 429
        assert LinkReport.objects.count() == 2

    def test_the_link_ceiling_is_per_link(self, client, link, user, limits):
        create_link(user, code='Other1')
        limits(SU_REPORT_IP=(100, 3600), SU_REPORT_LINK=(1, 3600))
        assert post_report(client, '203.0.113.1').status_code == 200
        assert post_report(client, '203.0.113.2').status_code == 429
        assert post_report(client, '203.0.113.3', code='Other1').status_code == 200

    def test_a_made_up_code_makes_no_per_link_key(self, client, limits):
        limits(SU_REPORT_IP=(100, 3600), SU_REPORT_LINK=(1, 3600))
        for _ in range(3):
            assert post_report(client, '203.0.113.1', code='neverissued').status_code == 204
        # Only the one per-address counter exists: no key was made for the made-up link.
        assert RateLimitCounter.objects.count() == 1

    def test_a_plain_post_over_the_limit_is_the_429_page(self, client, link, limits):
        limits(SU_REPORT_IP=(1, 3600))
        client.post('/s/Promo1/report', {'reason': 'spam'}, HTTP_X_REAL_IP='203.0.113.1')
        response = client.post('/s/Promo1/report', {'reason': 'spam'}, HTTP_X_REAL_IP='203.0.113.1')
        assert response.status_code == 429
        assert response['Retry-After']
