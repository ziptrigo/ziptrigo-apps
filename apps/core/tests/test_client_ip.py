"""Unit tests for `apps.core.services.client_ip` (moved here from `file_transfer` in issue #53
so `accounts` and `qr_code` can use it too -- see the module docstring). Issue #53 code review:
`X-Real-IP` is now only honoured when `REMOTE_ADDR` is a trusted proxy (`settings.TRUSTED_PROXIES`)
-- most tests below don't override `REMOTE_ADDR`, so `RequestFactory`'s own default (`127.0.0.1`,
inside the default trust list) keeps them exercising the "trusted" path unless a test says
otherwise.
"""

import pytest
from django.test import RequestFactory

from apps.core.services.client_ip import client_ip

pytestmark = [pytest.mark.unit]


def _request(**meta):
    return RequestFactory().get('/', **meta)


def test_prefers_x_real_ip_over_remote_addr():
    request = _request(HTTP_X_REAL_IP='203.0.113.9', REMOTE_ADDR='127.0.0.1')
    assert client_ip(request) == '203.0.113.9'


def test_falls_back_to_remote_addr_without_x_real_ip():
    request = _request(REMOTE_ADDR='198.51.100.7')
    assert client_ip(request) == '198.51.100.7'


def test_ignores_client_supplied_x_forwarded_for():
    """`X-Forwarded-For` is fully client-controlled (nginx here doesn't sanitise it) -- must
    never be trusted, only `X-Real-IP` (set by nginx itself) is."""
    request = _request(HTTP_X_FORWARDED_FOR='1.2.3.4', REMOTE_ADDR='198.51.100.7')
    assert client_ip(request) == '198.51.100.7'


def test_falls_back_to_remote_addr_for_an_unparseable_x_real_ip():
    """A malformed header must never turn into "no IP at all" (`None`, which every per-IP rate
    limit treats as *unlimited*) -- it falls back to the trusted `REMOTE_ADDR` instead (issue #53
    code review)."""
    request = _request(HTTP_X_REAL_IP='not-an-ip', REMOTE_ADDR='198.51.100.7')
    assert client_ip(request) == '198.51.100.7'


def test_returns_none_when_nothing_is_present():
    request = _request(REMOTE_ADDR='')
    assert client_ip(request) is None


def test_accepts_ipv6():
    request = _request(HTTP_X_REAL_IP='2001:db8::1')
    assert client_ip(request) == '2001:db8::1'


class TestTrustedProxyGating:
    """`X-Real-IP` is only honoured when `REMOTE_ADDR` -- the real TCP peer -- matches
    `settings.TRUSTED_PROXIES` (issue #53 code review)."""

    def test_ignores_x_real_ip_when_remote_addr_is_not_a_trusted_proxy(self):
        request = _request(HTTP_X_REAL_IP='203.0.113.9', REMOTE_ADDR='198.51.100.7')
        assert client_ip(request) == '198.51.100.7'

    def test_honours_x_real_ip_for_rfc1918_remote_addr(self):
        request = _request(HTTP_X_REAL_IP='203.0.113.9', REMOTE_ADDR='10.0.0.5')
        assert client_ip(request) == '203.0.113.9'

    def test_honours_x_real_ip_for_ipv6_unique_local_remote_addr(self):
        request = _request(HTTP_X_REAL_IP='203.0.113.9', REMOTE_ADDR='fd00::1')
        assert client_ip(request) == '203.0.113.9'

    def test_trusted_proxies_setting_is_configurable(self, settings):
        settings.TRUSTED_PROXIES = ['203.0.113.100/32']

        trusted = _request(HTTP_X_REAL_IP='9.9.9.9', REMOTE_ADDR='203.0.113.100')
        untrusted = _request(HTTP_X_REAL_IP='9.9.9.9', REMOTE_ADDR='203.0.113.101')

        assert client_ip(trusted) == '9.9.9.9'
        assert client_ip(untrusted) == '203.0.113.101'

    def test_malformed_trusted_proxies_entry_is_ignored_not_fatal(self, settings):
        settings.TRUSTED_PROXIES = ['not-a-cidr', '10.0.0.0/8']
        request = _request(HTTP_X_REAL_IP='9.9.9.9', REMOTE_ADDR='10.1.2.3')
        assert client_ip(request) == '9.9.9.9'
