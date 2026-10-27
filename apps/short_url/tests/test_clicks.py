from datetime import date

import pytest
from django.test import RequestFactory

from ..models import ClickEvent, ShortLink
from ..services import clicks, geoip
from ..services.clicks import referrer_host, visitor_hash
from .factories import create_link
from .test_geoip import FakeReader
from .test_useragents import IPHONE, WINDOWS_CHROME

pytestmark = pytest.mark.django_db

BOT = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'


def request_for(path='/s/x', *, agent=WINDOWS_CHROME, referer=None, ip='203.0.113.5'):
    extra = {'REMOTE_ADDR': ip, 'HTTP_HOST': 'app.example.test'}
    if agent is not None:
        extra['HTTP_USER_AGENT'] = agent
    if referer is not None:
        extra['HTTP_REFERER'] = referer
    return RequestFactory().get(path, **extra)


def fresh(link) -> ShortLink:
    return ShortLink.objects.get(pk=link.pk)


@pytest.fixture(autouse=True)
def allowed_hosts(settings):
    settings.ALLOWED_HOSTS = ['app.example.test', 'testserver']


@pytest.fixture
def link(user):
    return create_link(user)


class TestRecord:
    def test_a_person_s_click_bumps_the_headline_and_stores_an_event(self, link):
        assert clicks.record(link, request_for(agent=IPHONE, referer='https://t.co/abc?x=1'))
        link = fresh(link)
        assert (link.click_count, link.bot_click_count) == (1, 0)
        assert link.last_clicked_at is not None
        event = ClickEvent.objects.get()
        assert event.link_id == link.pk
        assert (event.device, event.browser, event.os) == ('mobile', 'Safari', 'iOS')
        assert event.referrer_host == 't.co'
        assert event.country == ''
        assert event.is_bot is False

    def test_the_event_holds_no_address_and_no_path(self, link):
        clicks.record(
            link,
            request_for(referer='https://news.example.org/a/secret/path?token=1', ip='203.0.113.5'),
        )
        event = ClickEvent.objects.get()
        fields = ('ip_hash', 'referrer_host', 'country', 'device', 'browser', 'os')
        stored = ' '.join(str(getattr(event, name)) for name in fields)
        assert '203.0.113.5' not in stored
        assert 'secret' not in stored and 'token' not in stored
        assert event.referrer_host == 'news.example.org'

    def test_the_country_comes_from_the_geoip_database(self, link, settings, monkeypatch):
        settings.GEOIP_PATH = '/fake.mmdb'
        monkeypatch.setattr(geoip, '_open', lambda path: FakeReader({'203.0.113.5': 'DE'}))
        geoip.reset()
        clicks.record(link, request_for(ip='203.0.113.5'))
        clicks.record(link, request_for(ip='203.0.113.6'))
        assert sorted(ClickEvent.objects.values_list('country', flat=True)) == ['', 'DE']

    def test_a_missing_geoip_database_leaves_the_country_unknown(self, link):
        clicks.record(link, request_for())
        assert ClickEvent.objects.get().country == ''

    def test_no_referer_is_empty(self, link):
        clicks.record(link, request_for())
        assert ClickEvent.objects.get().referrer_host == ''


class TestBotFiltering:
    def test_with_the_filter_on_a_bot_only_counts_as_a_bot(self, link):
        assert link.filter_bots
        assert clicks.record(link, request_for(agent=BOT))
        link = fresh(link)
        assert (link.click_count, link.bot_click_count) == (0, 1)
        assert link.last_clicked_at is None  # nobody visited
        event = ClickEvent.objects.get()
        assert event.is_bot and event.device == 'bot'  # the raw event is recorded either way

    def test_with_the_filter_off_a_bot_counts_in_both(self, user):
        link = create_link(user, filter_bots=False)
        clicks.record(link, request_for(agent=BOT))
        clicks.record(link, request_for())
        link = fresh(link)
        assert (link.click_count, link.bot_click_count) == (2, 1)

    def test_a_missing_user_agent_is_a_bot(self, link):
        clicks.record(link, request_for(agent=None))
        assert (fresh(link).click_count, fresh(link).bot_click_count) == (0, 1)

    @pytest.mark.parametrize(
        'agent',
        [BOT, None, 'curl/8.1.2', 'python-requests/2.31.0'],
        ids=['bot', 'none', 'curl', 'py'],
    )
    def test_on_a_capped_link_every_click_uses_the_cap_bot_or_not(self, user, agent):
        link = create_link(user, max_clicks=2)  # filter_bots is on
        assert link.filter_bots
        assert clicks.record(link, request_for(agent=agent))
        assert clicks.record(link, request_for(agent=agent))
        assert not clicks.record(link, request_for(agent=agent)), 'the cap is used up'
        link = fresh(link)
        assert (link.click_count, link.bot_click_count) == (2, 2)  # still flagged as bots
        assert link.last_clicked_at is not None
        assert ClickEvent.objects.filter(is_bot=True).count() == 2

    def test_on_a_capped_link_people_and_bots_share_the_cap(self, user):
        link = create_link(user, max_clicks=2)
        assert clicks.record(link, request_for(agent=BOT))
        assert clicks.record(link, request_for())
        assert not clicks.record(link, request_for())
        assert (fresh(link).click_count, fresh(link).bot_click_count) == (2, 1)

    def test_without_a_cap_bots_still_stay_out_of_the_headline(self, user):
        link = create_link(user)
        for _ in range(5):
            clicks.record(link, request_for(agent=BOT))
        assert (fresh(link).click_count, fresh(link).bot_click_count) == (0, 5)

    def test_bots_use_up_the_cap_when_not_filtered(self, user):
        link = create_link(user, max_clicks=2, filter_bots=False)
        assert clicks.record(link, request_for(agent=BOT))
        assert clicks.record(link, request_for(agent=BOT))
        assert not clicks.record(link, request_for())


class TestGuards:
    def test_a_capped_link_refuses_even_a_filtered_bot(self, user):
        link = create_link(user, max_clicks=1, click_count=1)
        assert not clicks.record(link, request_for(agent=BOT))
        assert fresh(link).bot_click_count == 0
        assert not ClickEvent.objects.exists()

    def test_a_disabled_link_records_nothing(self, user):
        link = create_link(user, status='disabled')
        assert not clicks.record(link, request_for())
        assert fresh(link).click_count == 0
        assert not ClickEvent.objects.exists()

    def test_without_an_event_only_the_counter_moves(self, link):
        assert clicks.record(link, request_for(), with_event=False)
        assert fresh(link).click_count == 1
        assert not ClickEvent.objects.exists()

    def test_a_failing_event_write_does_not_lose_the_click(self, link, monkeypatch, caplog):
        def boom(self, *args, **kwargs):
            raise RuntimeError('database trouble')

        monkeypatch.setattr(ClickEvent, 'save', boom)
        assert clicks.record(link, request_for())
        assert fresh(link).click_count == 1
        assert 'Could not record the click event' in caplog.text


class TestVisitorHash:
    DAY = date(2026, 10, 20)

    def test_the_same_visitor_on_the_same_day_hashes_the_same(self):
        assert visitor_hash('203.0.113.5', self.DAY) == visitor_hash('203.0.113.5', self.DAY)

    def test_another_day_or_address_hashes_differently(self):
        base = visitor_hash('203.0.113.5', self.DAY)
        assert visitor_hash('203.0.113.5', date(2026, 10, 21)) != base
        assert visitor_hash('203.0.113.6', self.DAY) != base

    def test_it_is_a_keyed_hex_digest_that_does_not_contain_the_address(self, settings):
        value = visitor_hash('203.0.113.5', self.DAY)
        assert len(value) == 64 and int(value, 16) >= 0
        settings.SECRET_KEY = 'another-secret'
        assert visitor_hash('203.0.113.5', self.DAY) != value

    def test_an_ipv6_visitor_is_one_per_64(self):
        a = visitor_hash('2001:db8:1:2:aaaa:bbbb:cccc:dddd', self.DAY)
        b = visitor_hash('2001:db8:1:2:1111:2222:3333:4444', self.DAY)
        c = visitor_hash('2001:db8:1:3::1', self.DAY)
        assert a == b != c

    def test_an_ipv4_mapped_address_is_the_ipv4_visitor(self):
        assert visitor_hash('::ffff:203.0.113.5', self.DAY) == visitor_hash('203.0.113.5', self.DAY)

    def test_no_address_still_hashes(self):
        assert visitor_hash(None, self.DAY) == visitor_hash(None, self.DAY)

    def test_the_event_stores_the_hash_of_its_own_utc_day(self, link):
        clicks.record(link, request_for(ip='203.0.113.5'))
        event = ClickEvent.objects.get()
        assert event.ip_hash == visitor_hash('203.0.113.5', clicks.utc_day(event.at))

    def test_two_clicks_of_one_visitor_share_a_hash(self, link):
        clicks.record(link, request_for(ip='203.0.113.5'))
        clicks.record(link, request_for(ip='203.0.113.5'))
        clicks.record(link, request_for(ip='203.0.113.9'))
        hashes = list(ClickEvent.objects.values_list('ip_hash', flat=True))
        assert len(set(hashes)) == 2


class TestReferrerHost:
    @pytest.mark.parametrize(
        ('referer', 'expected'),
        [
            ('https://www.Google.com/search?q=x', 'google.com'),
            ('http://news.example.org:8080/a/b', 'news.example.org'),
            ('https://t.co/', 't.co'),
            ('https://example.com./x', 'example.com'),
            ('https://[2001:db8::1]/x', '2001:db8::1'),
            ('android-app://com.google.android.gm', ''),
            ('ftp://example.com/', ''),
            ('not a url', ''),
            ('', ''),
            ('https://exa mple.com/', ''),
            ('https://' + 'a' * 300 + '.com/', ''),
        ],
    )
    def test_the_host_alone(self, referer, expected):
        assert referrer_host(request_for(referer=referer)) == expected

    def test_our_own_pages_are_not_a_referrer(self, settings):
        assert referrer_host(request_for(referer='https://app.example.test/s/abc/unlock/')) == ''
        settings.BASE_URL = 'https://other.example.test'
        assert referrer_host(request_for(referer='https://other.example.test/s/abc+')) == ''
