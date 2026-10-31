"""Google Safe Browsing, Lookup API v4 (issue #163): is this URL on one of Google's lists of
malware, phishing ("social engineering"), unwanted-software and harmful-app pages?

This module is only the client: `lookup` answers which of some URLs are flagged, and the policy
(when to ask, what to do with an answer) is `services.reputation`. `get_client()` / `set_client()`
are process-wide like the AI clients' (the suite installs a fake and never reaches the network).

The Lookup API takes the URLs themselves (not hash prefixes: that is the Update API, which keeps a
local database and is more work). A destination is therefore sent to Google, which the README says;
the request carries the API key in its query string, so it is never logged here (only the status).
Up to `MAX_URLS_PER_REQUEST` URLs go in one request.

Anything other than a clear answer -- a timeout, a non-200, an unreadable reply -- raises
`SafeBrowsingUnavailable`; callers fail open. Note that Google offers this API for non-commercial
use and points commercial use to Web Risk, which has the same shape (`uris:search`); switching is
a change to this module only.
"""

import logging
from collections.abc import Sequence
from typing import Protocol

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ENDPOINT = 'https://safebrowsing.googleapis.com/v4/threatMatches:find'
CLIENT_ID = 'ziptrigo-apps'
CLIENT_VERSION = '1.0'
MAX_URLS_PER_REQUEST = 500
THREAT_TYPES = (
    'MALWARE',
    'SOCIAL_ENGINEERING',
    'UNWANTED_SOFTWARE',
    'POTENTIALLY_HARMFUL_APPLICATION',
)


class SafeBrowsingUnavailable(Exception):
    """The lookup could not be answered (not configured, network, quota, a bad reply)."""


class SafeBrowsingClient(Protocol):
    def lookup(self, urls: Sequence[str]) -> dict[str, str]:
        """The flagged ones among `urls`, as `{url: threat type}`. Raises
        `SafeBrowsingUnavailable`."""
        ...


def is_configured() -> bool:
    return bool(settings.SAFE_BROWSING_API_KEY)


class GoogleSafeBrowsingClient:
    """`SafeBrowsingClient` over `requests`."""

    def lookup(self, urls: Sequence[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(urls))
        if not unique:
            return {}
        if len(unique) > MAX_URLS_PER_REQUEST:
            raise ValueError(f'At most {MAX_URLS_PER_REQUEST} URLs per request.')
        body = {
            'client': {'clientId': CLIENT_ID, 'clientVersion': CLIENT_VERSION},
            'threatInfo': {
                'threatTypes': list(THREAT_TYPES),
                'platformTypes': ['ANY_PLATFORM'],
                'threatEntryTypes': ['URL'],
                'threatEntries': [{'url': url} for url in unique],
            },
        }
        try:
            response = requests.post(
                ENDPOINT,
                params={'key': settings.SAFE_BROWSING_API_KEY},
                json=body,
                timeout=settings.SAFE_BROWSING_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            # The exception's text can hold the request URL, which carries the key.
            raise SafeBrowsingUnavailable(type(exc).__name__) from None
        if response.status_code != 200:
            raise SafeBrowsingUnavailable(f'HTTP {response.status_code}')
        try:
            matches = response.json().get('matches') or []
            flagged: dict[str, str] = {}
            for match in matches:
                url = match['threat']['url']
                # A URL on several lists keeps the first threat Google names for it.
                flagged.setdefault(url, str(match['threatType']))
        except ValueError, KeyError, TypeError, AttributeError:
            raise SafeBrowsingUnavailable('unreadable reply') from None
        return flagged


_client: SafeBrowsingClient | None = None


def get_client() -> SafeBrowsingClient:
    global _client
    if _client is None:
        _client = GoogleSafeBrowsingClient()
    return _client


def set_client(client: SafeBrowsingClient | None) -> None:
    """Replace the process-wide client (tests); `None` resets it."""
    global _client
    _client = client
