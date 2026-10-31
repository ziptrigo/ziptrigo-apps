"""Google Safe Browsing (issue #163): the client, the check at create and edit, and the daily
recheck that holds a link whose destination has become flagged. No test reaches the network:
`conftest.no_network` refuses `requests.post`, and a fake client stands in for Google."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests
from django.core import checks as django_checks
from django.utils import timezone

from apps.short_url import checks, jobs

from ..models import LinkReport, LinkStatus, ReportReason, ReportSource, ReportStatus, ShortLink
from ..services import (
    BlockedDomainError,
    LinkInput,
    UnsafeDestinationError,
    create_link,
    hold,
    is_available,
    reports,
    reputation,
    safe_browsing,
    update_link,
)
from ..services.safe_browsing import GoogleSafeBrowsingClient, SafeBrowsingUnavailable
from .api_support import BASE, bearer, post
from .factories import create_link as make_link

pytestmark = pytest.mark.django_db

BAD = 'https://bad.example.com/login'
GOOD = 'https://good.example.com/page'


class FakeClient:
    """Flags what it is told to; records every lookup."""

    def __init__(self, flagged=None, error=None):
        self.flagged = dict(flagged or {})
        self.error = error
        self.calls: list[list[str]] = []

    def lookup(self, urls):
        self.calls.append(list(urls))
        if self.error:
            raise self.error
        return {url: self.flagged[url] for url in urls if url in self.flagged}


@pytest.fixture
def enabled(settings):
    settings.SAFE_BROWSING_API_KEY = 'test-key'


@pytest.fixture
def fake(enabled):
    client = FakeClient({BAD: 'SOCIAL_ENGINEERING'})
    safe_browsing.set_client(client)
    return client


# -- the client ------------------------------------------------------------------------------


class TestGoogleClient:
    @pytest.fixture(autouse=True)
    def key(self, settings):
        settings.SAFE_BROWSING_API_KEY = 'secret-key'
        settings.SAFE_BROWSING_TIMEOUT_SECONDS = 2

    def reply(self, monkeypatch, status=200, body=None, exc=None):
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            if exc:
                raise exc
            return SimpleNamespace(status_code=status, json=lambda: body)

        monkeypatch.setattr(safe_browsing.requests, 'post', post)
        return calls

    def test_it_sends_the_urls_and_reads_the_matches(self, monkeypatch):
        calls = self.reply(
            monkeypatch,
            body={
                'matches': [
                    {'threatType': 'MALWARE', 'threat': {'url': BAD}},
                    {'threatType': 'SOCIAL_ENGINEERING', 'threat': {'url': BAD}},
                ]
            },
        )
        assert GoogleSafeBrowsingClient().lookup([BAD, GOOD, BAD]) == {BAD: 'MALWARE'}
        url, kwargs = calls[0]
        assert url == safe_browsing.ENDPOINT
        assert kwargs['params'] == {'key': 'secret-key'}
        assert kwargs['timeout'] == 2
        info = kwargs['json']['threatInfo']
        assert info['threatEntries'] == [{'url': BAD}, {'url': GOOD}]
        assert info['threatEntryTypes'] == ['URL']
        assert 'MALWARE' in info['threatTypes'] and 'SOCIAL_ENGINEERING' in info['threatTypes']

    def test_no_matches_is_an_empty_answer(self, monkeypatch):
        self.reply(monkeypatch, body={})
        assert GoogleSafeBrowsingClient().lookup([GOOD]) == {}

    def test_nothing_to_ask_makes_no_request(self, monkeypatch):
        calls = self.reply(monkeypatch, body={})
        assert GoogleSafeBrowsingClient().lookup([]) == {}
        assert calls == []

    def test_too_many_urls_is_a_programming_error(self, monkeypatch):
        self.reply(monkeypatch, body={})
        urls = [f'https://example.com/{n}' for n in range(safe_browsing.MAX_URLS_PER_REQUEST + 1)]
        with pytest.raises(ValueError):
            GoogleSafeBrowsingClient().lookup(urls)

    @pytest.mark.parametrize('status', [400, 403, 429, 500])
    def test_an_error_status_is_unavailable(self, monkeypatch, status):
        self.reply(monkeypatch, status=status, body={})
        with pytest.raises(SafeBrowsingUnavailable, match=str(status)):
            GoogleSafeBrowsingClient().lookup([GOOD])

    @pytest.mark.parametrize('body', [['x'], {'matches': [{'threatType': 'MALWARE'}]}, 'text'])
    def test_an_unreadable_reply_is_unavailable(self, monkeypatch, body):
        self.reply(monkeypatch, body=body)
        with pytest.raises(SafeBrowsingUnavailable):
            GoogleSafeBrowsingClient().lookup([GOOD])

    def test_a_network_error_is_unavailable_and_does_not_leak_the_key(self, monkeypatch):
        error = requests.ConnectionError(f'failed: {safe_browsing.ENDPOINT}?key=secret-key')
        self.reply(monkeypatch, exc=error)
        with pytest.raises(SafeBrowsingUnavailable) as caught:
            GoogleSafeBrowsingClient().lookup([GOOD])
        assert 'secret-key' not in str(caught.value)
        assert caught.value.__cause__ is None

    def test_a_non_json_reply_is_unavailable(self, monkeypatch):
        def broken():
            raise ValueError('no json')

        monkeypatch.setattr(
            safe_browsing.requests,
            'post',
            lambda *a, **k: SimpleNamespace(status_code=200, json=broken),
        )
        with pytest.raises(SafeBrowsingUnavailable):
            GoogleSafeBrowsingClient().lookup([GOOD])


# -- create and edit -------------------------------------------------------------------------


class TestCreateAndEdit:
    def test_without_a_key_nothing_is_asked(self, funded_user):
        client = FakeClient({BAD: 'MALWARE'})
        safe_browsing.set_client(client)
        link = create_link(funded_user, LinkInput(destination=BAD))
        assert client.calls == []
        assert link.reputation_checked_at is None

    def test_a_flagged_destination_is_refused_neutrally(self, funded_user, fake):
        with pytest.raises(UnsafeDestinationError) as caught:
            create_link(funded_user, LinkInput(destination=BAD))
        assert caught.value.message_dict == {'destination': ['That address cannot be used.']}
        assert isinstance(caught.value, BlockedDomainError)
        assert not ShortLink.objects.exists()

    def test_a_refusal_charges_nothing(self, funded_user, fake):
        from apps.billing.services import get_balance

        before = get_balance(funded_user)
        with pytest.raises(UnsafeDestinationError):
            create_link(funded_user, LinkInput(destination=BAD, alias='my-alias'))
        assert get_balance(funded_user) == before

    def test_a_clean_destination_is_created_and_marked_checked(self, funded_user, fake):
        link = create_link(funded_user, LinkInput(destination=GOOD))
        link.refresh_from_db()
        assert link.reputation_checked_at is not None
        assert fake.calls == [[GOOD]]

    def test_the_utm_result_is_what_is_looked_up(self, funded_user, enabled):
        client = FakeClient()
        safe_browsing.set_client(client)
        create_link(funded_user, LinkInput(destination=GOOD, utm={'utm_source': 'news'}))
        assert client.calls == [[f'{GOOD}?utm_source=news']]

    @pytest.mark.parametrize(
        'error', [SafeBrowsingUnavailable('HTTP 500'), SafeBrowsingUnavailable('timeout')]
    )
    def test_it_fails_open_and_leaves_the_link_unchecked(self, funded_user, enabled, error):
        safe_browsing.set_client(FakeClient(error=error))
        link = create_link(funded_user, LinkInput(destination=BAD))
        link.refresh_from_db()
        assert link.reputation_checked_at is None

    def test_a_failure_is_not_remembered(self, funded_user, enabled):
        client = FakeClient(error=SafeBrowsingUnavailable('HTTP 500'))
        safe_browsing.set_client(client)
        create_link(funded_user, LinkInput(destination=GOOD))
        client.error = None
        create_link(funded_user, LinkInput(destination=GOOD))
        assert len(client.calls) == 2

    def test_a_verdict_is_reused_for_a_while(self, funded_user, fake):
        create_link(funded_user, LinkInput(destination=GOOD))
        create_link(funded_user, LinkInput(destination=GOOD))
        assert len(fake.calls) == 1
        with pytest.raises(UnsafeDestinationError):
            create_link(funded_user, LinkInput(destination=BAD))
        with pytest.raises(UnsafeDestinationError):
            create_link(funded_user, LinkInput(destination=BAD))
        assert len(fake.calls) == 2

    def test_a_verdict_expires(self, funded_user, fake, monkeypatch):
        create_link(funded_user, LinkInput(destination=GOOD))
        later = reputation.time.monotonic() + reputation.CACHE_TTL + 1
        monkeypatch.setattr(reputation.time, 'monotonic', lambda: later)
        create_link(funded_user, LinkInput(destination=GOOD))
        assert len(fake.calls) == 2

    def test_editing_to_a_flagged_destination_is_refused(self, funded_user, fake):
        link = create_link(funded_user, LinkInput(destination=GOOD))
        with pytest.raises(UnsafeDestinationError):
            update_link(link, destination=BAD)
        link.refresh_from_db()
        assert link.destination == GOOD

    def test_editing_to_a_clean_destination_marks_it_checked(self, funded_user, fake):
        link = create_link(funded_user, LinkInput(destination=GOOD))
        ShortLink.objects.filter(pk=link.pk).update(reputation_checked_at=None)
        new = 'https://other.example.com/'
        update_link(link, destination=new)
        link.refresh_from_db()
        assert link.destination == new
        assert link.reputation_checked_at is not None
        assert fake.calls[-1] == [new]

    def test_a_new_destination_that_could_not_be_checked_is_left_for_the_recheck(
        self, funded_user, fake
    ):
        link = create_link(funded_user, LinkInput(destination=GOOD))
        fake.error = SafeBrowsingUnavailable('HTTP 500')
        update_link(link, destination='https://other.example.com/')
        link.refresh_from_db()
        assert link.reputation_checked_at is None

    def test_an_edit_that_keeps_the_destination_asks_nothing(self, funded_user, fake):
        link = create_link(funded_user, LinkInput(destination=GOOD))
        fake.calls.clear()
        update_link(link, title='New title', destination=GOOD)
        assert fake.calls == []

    def test_a_utm_only_edit_is_looked_up(self, funded_user, fake):
        link = create_link(funded_user, LinkInput(destination=GOOD))
        update_link(link, utm={'utm_campaign': 'spring'})
        assert fake.calls[-1] == [f'{GOOD}?utm_campaign=spring']

    def test_an_invalid_destination_keeps_its_own_error(self, funded_user, fake):
        link = create_link(funded_user, LinkInput(destination=GOOD))
        fake.calls.clear()
        with pytest.raises(Exception, match='http'):
            update_link(link, destination='ftp://example.com/')
        assert fake.calls == []


class TestApi:
    def test_a_flagged_create_is_a_403_with_the_neutral_message(self, client, funded_user, fake):
        response = post(client, '/links', bearer(funded_user), {'destination': BAD})
        assert response.status_code == 403
        assert response.json()['detail'] == 'destination: That address cannot be used.'

    def test_it_spends_no_create_budget(self, client, funded_user, fake, settings):
        settings.RATELIMIT_ENABLE = True
        for _ in range(60):
            post(client, '/links', bearer(funded_user), {'destination': BAD})
        response = post(client, '/links', bearer(funded_user), {'destination': GOOD})
        assert response.status_code == 201

    def test_a_flagged_patch_is_a_403(self, client, funded_user, fake):
        link = make_link(funded_user, destination=GOOD)
        response = client.patch(
            f'{BASE}/links/{link.pk}',
            data={'destination': BAD},
            content_type='application/json',
            **bearer(funded_user),
        )
        assert response.status_code == 403
        link.refresh_from_db()
        assert link.destination == GOOD


# -- the recheck -----------------------------------------------------------------------------


class TestRecheck:
    def test_without_a_key_it_does_nothing(self, funded_user):
        client = FakeClient({BAD: 'MALWARE'})
        safe_browsing.set_client(client)
        make_link(funded_user, destination=BAD)
        assert reputation.recheck() == reputation.RecheckResult()
        assert client.calls == []

    def test_a_flagged_link_is_held_with_a_report(self, funded_user, fake):
        link = make_link(funded_user, destination=BAD)
        result = reputation.recheck()
        assert result == reputation.RecheckResult(checked=1, held=1)
        link.refresh_from_db()
        assert link.held_for_review_at is not None
        assert not is_available(link)
        report = LinkReport.objects.get()
        assert report.link_id == link.pk
        assert report.source == ReportSource.SAFE_BROWSING
        assert report.reason == ReportReason.PHISHING
        assert report.status == ReportStatus.PENDING
        assert report.reporter_ip is None
        assert 'social_engineering' in report.details

    @pytest.mark.parametrize(
        ('threat', 'reason'),
        [
            ('MALWARE', ReportReason.MALWARE),
            ('UNWANTED_SOFTWARE', ReportReason.MALWARE),
            ('POTENTIALLY_HARMFUL_APPLICATION', ReportReason.MALWARE),
            ('SOMETHING_NEW', ReportReason.OTHER),
        ],
    )
    def test_the_threat_picks_the_reason(self, funded_user, fake, threat, reason):
        fake.flagged[BAD] = threat
        make_link(funded_user, destination=BAD)
        reputation.recheck()
        assert LinkReport.objects.get().reason == reason

    def test_a_clean_link_is_marked_checked(self, funded_user, fake):
        link = make_link(funded_user, destination=GOOD)
        reputation.recheck()
        link.refresh_from_db()
        assert link.reputation_checked_at is not None
        assert link.held_for_review_at is None
        assert not LinkReport.objects.exists()

    def test_one_request_covers_every_link_and_repeats_are_asked_once(self, funded_user, fake):
        for _ in range(3):
            make_link(funded_user, destination=GOOD)
        make_link(funded_user, destination=BAD)
        make_link(funded_user, destination=BAD)
        result = reputation.recheck()
        assert len(fake.calls) == 1
        assert sorted(fake.calls[0]) == sorted([GOOD, BAD])
        assert result == reputation.RecheckResult(checked=2, held=2)
        assert LinkReport.objects.count() == 2

    def test_a_recently_checked_link_is_left_alone(self, funded_user, fake):
        make_link(funded_user, destination=GOOD, reputation_checked_at=timezone.now())
        assert reputation.recheck() == reputation.RecheckResult()
        assert fake.calls == []

    def test_a_link_checked_long_ago_is_looked_at_again(self, funded_user, fake):
        old = timezone.now() - reputation.RECHECK_INTERVAL - timedelta(minutes=1)
        link = make_link(funded_user, destination=BAD, reputation_checked_at=old)
        assert reputation.recheck().held == 1
        link.refresh_from_db()
        assert link.held_for_review_at is not None

    def test_only_active_links_are_rechecked(self, funded_user, fake):
        make_link(funded_user, destination=BAD, status=LinkStatus.DISABLED)
        make_link(funded_user, destination=BAD, status=LinkStatus.DELETED)
        make_link(funded_user, destination=BAD, status=LinkStatus.TAKEN_DOWN)
        make_link(funded_user, destination=BAD, held_for_review_at=timezone.now())
        assert reputation.recheck() == reputation.RecheckResult()
        assert fake.calls == []

    def test_the_oldest_are_taken_first_and_the_batch_is_bounded(
        self, funded_user, fake, monkeypatch
    ):
        monkeypatch.setattr(reputation, 'RECHECK_BATCH', 2)
        now = timezone.now()
        stale = now - reputation.RECHECK_INTERVAL
        newest = make_link(
            funded_user, destination='https://c.example.com/', reputation_checked_at=stale
        )
        never = make_link(funded_user, destination='https://a.example.com/')
        older = make_link(
            funded_user,
            destination='https://b.example.com/',
            reputation_checked_at=stale - timedelta(days=3),
        )
        reputation.recheck(now=now)
        assert fake.calls == [['https://a.example.com/', 'https://b.example.com/']]
        for link in (newest, never, older):
            link.refresh_from_db()
        assert never.reputation_checked_at and older.reputation_checked_at
        assert newest.reputation_checked_at == stale  # waits for the next run

    def test_an_api_failure_propagates_and_marks_nothing(self, funded_user, enabled):
        safe_browsing.set_client(FakeClient(error=SafeBrowsingUnavailable('HTTP 503')))
        link = make_link(funded_user, destination=BAD)
        with pytest.raises(SafeBrowsingUnavailable):
            reputation.recheck()
        link.refresh_from_db()
        assert link.reputation_checked_at is None and link.held_for_review_at is None

    def test_the_job_wraps_the_recheck(self, funded_user, fake, caplog):
        make_link(funded_user, destination=BAD)
        with caplog.at_level('INFO', logger='apps.short_url.jobs'):
            jobs.recheck_destinations()
        assert 'held 1 link' in caplog.text
        assert ShortLink.objects.get().held_for_review_at is not None

    def test_the_job_is_registered(self):
        from apps.core.scheduler import get_jobs

        names = {job.name for job in get_jobs()}
        assert 'short_url.recheck_destinations' in names

    def test_a_held_link_is_not_held_twice(self, funded_user, fake):
        link = make_link(funded_user, destination=BAD)
        assert hold.hold_for_threat(link, BAD, 'MALWARE')
        assert not hold.hold_for_threat(link, BAD, 'MALWARE')
        assert LinkReport.objects.count() == 1

    def test_a_link_pointed_elsewhere_meanwhile_is_not_held(self, funded_user, fake):
        link = make_link(funded_user, destination=GOOD)
        assert not hold.hold_for_threat(link, BAD, 'MALWARE')
        link.refresh_from_db()
        assert link.held_for_review_at is None

    def test_the_automated_report_does_not_count_toward_visitor_holds(
        self, funded_user, fake, prices
    ):
        from ..models import ShortUrlSettings

        row = ShortUrlSettings.load()
        row.auto_hold_report_threshold = 1
        row.save()
        link = make_link(funded_user, destination=GOOD)
        LinkReport.objects.create(
            link=link, reason=ReportReason.MALWARE, source=ReportSource.SAFE_BROWSING
        )
        assert not hold.maybe_hold_for_reports(link)


class TestStaffDecision:
    @pytest.fixture
    def held_link(self, funded_user, fake):
        link = make_link(funded_user, destination=BAD)
        reputation.recheck()
        link.refresh_from_db()
        return link

    def test_dismissing_the_report_releases_the_link_for_good(self, held_link, fake, user):
        report = held_link.reports.get()
        assert reports.dismiss_report(report, by=user)
        held_link.refresh_from_db()
        assert held_link.held_for_review_at is None
        assert held_link.reputation_cleared_destination == BAD
        assert is_available(held_link)
        # The next recheck leaves the cleared destination alone.
        ShortLink.objects.filter(pk=held_link.pk).update(reputation_checked_at=None)
        fake.calls.clear()
        assert reputation.recheck() == reputation.RecheckResult()
        assert fake.calls == []

    def test_releasing_the_hold_clears_the_destination_too(self, held_link, fake, user):
        reports.release_hold_and_dismiss(held_link, by=user)
        held_link.refresh_from_db()
        assert held_link.held_for_review_at is None
        assert held_link.reputation_cleared_destination == BAD
        assert held_link.reports.get().status == ReportStatus.DISMISSED

    def test_dismissing_a_visitor_report_clears_nothing(self, funded_user, fake, user):
        link = make_link(funded_user, destination=BAD)
        report = LinkReport.objects.create(link=link, reason=ReportReason.SPAM)
        reports.dismiss_report(report, by=user)
        link.refresh_from_db()
        assert link.reputation_cleared_destination == ''

    def test_a_changed_destination_is_checked_again(self, held_link, fake, user):
        reports.dismiss_report(held_link.reports.get(), by=user)
        held_link.refresh_from_db()
        new = 'https://another.example.com/'
        fake.flagged[new] = 'MALWARE'
        ShortLink.objects.filter(pk=held_link.pk).update(
            destination=new, reputation_checked_at=None
        )
        assert reputation.recheck().held == 1


# -- the system check ------------------------------------------------------------------------


class TestCheck:
    def test_it_warns_without_a_key(self, settings):
        settings.SAFE_BROWSING_API_KEY = ''
        found = checks.check_safe_browsing_key()
        assert [m.id for m in found] == ['short_url.W002']
        assert isinstance(found[0], django_checks.Warning)

    def test_it_is_quiet_with_a_key(self, settings):
        settings.SAFE_BROWSING_API_KEY = 'k'
        assert checks.check_safe_browsing_key() == []


class TestAdmin:
    def test_the_report_list_shows_and_filters_by_source(self):
        from apps.short_url.admin import LinkReportAdmin

        assert 'source' in LinkReportAdmin.list_display
        assert 'source' in LinkReportAdmin.list_filter
