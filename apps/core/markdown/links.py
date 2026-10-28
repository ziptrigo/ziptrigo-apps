"""Which link destinations the Markdown renderer lets through, and which are task links.

A destination is one of four kinds: an external `http`/`https` URL, an `http`/`https` URL on this
site's own host (`settings.BASE_URL`'s), a `mailto:` one, or a site-relative path (one leading
`/`). Anything else (`javascript:`, `data:`, `vbscript:`, a protocol-relative `//host`, a bare
word) is not a link at all and the renderer writes its text without an anchor.

Which URLs are links to a *task* is the caller's knowledge, not this module's: it passes a pattern
for the task page's path (`task_number_from_href`).
"""

import re
from enum import StrEnum
from urllib.parse import urlsplit

from django.conf import settings

_SCHEME = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.\-]*):')
_UNSAFE_CHARS = re.compile(r'[\x00-\x20\x7f]')


class LinkKind(StrEnum):
    EXTERNAL = 'external'
    #: An absolute `http`/`https` URL on `settings.BASE_URL`'s host; rendered like `RELATIVE`.
    SAME_SITE = 'same_site'
    MAIL = 'mail'
    RELATIVE = 'relative'


def _site_netloc() -> str | None:
    """The lower-cased host (and port) of `settings.BASE_URL`, if it has one."""
    try:
        return urlsplit(getattr(settings, 'BASE_URL', '')).netloc.lower() or None
    except ValueError:
        return None


def _is_same_site(href: str) -> bool:
    """Whether `href` is an `http`/`https` URL whose host is exactly this site's.

    The comparison is on the whole authority, so a different port, a userinfo
    (`https://app.example.com@evil.com`) or a longer name (`app.example.com.evil.com`) is another
    site.
    """
    site = _site_netloc()
    if site is None:
        return False
    try:
        parts = urlsplit(href)
    except ValueError:
        return False
    return parts.scheme.lower() in ('http', 'https') and parts.netloc.lower() == site


def classify_href(href: str) -> LinkKind | None:
    """The kind of link `href` is, or None when it must not become an anchor."""
    if not href or _UNSAFE_CHARS.search(href):
        return None
    if match := _SCHEME.match(href):
        scheme = match.group(1).lower()
        if scheme in ('http', 'https'):
            return LinkKind.SAME_SITE if _is_same_site(href) else LinkKind.EXTERNAL
        if scheme == 'mailto':
            return LinkKind.MAIL
        return None
    # Browsers read a backslash as a slash, so `/\host` is as protocol-relative as `//host`.
    if href.startswith('/') and not href.startswith(('//', '/\\')):
        return LinkKind.RELATIVE
    return None


def check_task_url_path(task_url_path: re.Pattern[str]) -> None:
    """Raise `ValueError` unless the pattern has the named group `number` the caller must supply."""
    if 'number' not in task_url_path.groupindex:
        raise ValueError("task_url_path needs a named group 'number'")


def task_number_from_href(href: str, task_url_path: re.Pattern[str] | None) -> int | None:
    """The task number a link points at, if it is a link to a task page.

    `task_url_path` is the caller's pattern for the task page's site-relative path, with a named
    group `number` (e.g. `^/todo/(?P<number>[0-9]{1,9})/?$`); it has to match the whole path
    (`fullmatch`). The link may be that path itself or an absolute URL with the host of
    `settings.BASE_URL` and that path; a query or fragment makes it an ordinary link. With no
    pattern nothing is a task link.
    """
    if task_url_path is None:
        return None
    if href.startswith('/'):
        match = task_url_path.fullmatch(href)
    else:
        try:
            parts = urlsplit(href)
        except ValueError:
            return None
        if parts.query or parts.fragment or not _is_same_site(href):
            return None
        match = task_url_path.fullmatch(parts.path)
    if match is None:
        return None
    try:
        return int(match.group('number'))
    except ValueError:
        return None
