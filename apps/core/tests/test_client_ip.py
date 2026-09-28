"""Unit tests for `apps.core.services.client_ip` (moved here from `file_transfer` in issue #53
so `accounts` and `qr_code` can use it too -- see the module docstring)."""

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


def test_returns_none_for_an_unparseable_x_real_ip():
    request = _request(HTTP_X_REAL_IP='not-an-ip', REMOTE_ADDR='198.51.100.7')
    assert client_ip(request) is None


def test_returns_none_when_nothing_is_present():
    request = _request(REMOTE_ADDR='')
    assert client_ip(request) is None


def test_accepts_ipv6():
    request = _request(HTTP_X_REAL_IP='2001:db8::1')
    assert client_ip(request) == '2001:db8::1'
