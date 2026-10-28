"""Which link destinations the Markdown renderer lets through, and which are task links.

A destination is one of three kinds: an external `http`/`https` URL, a `mailto:` one, or a
site-relative path (one leading `/`). Anything else (`javascript:`, `data:`, `vbscript:`, a
protocol-relative `//host`, a bare word) is not a link at all and the renderer writes its text
without an anchor.
"""

import re
from enum import StrEnum
from urllib.parse import urlsplit

from django.conf import settings

_SCHEME = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.\-]*):')
_UNSAFE_CHARS = re.compile(r'[\x00-\x20\x7f]')
#: `/todo/<number>` with or without the trailing slash: the TODO app's task page.
_TASK_PATH = re.compile(r'/todo/([0-9]{1,9})/?')


class LinkKind(StrEnum):
    EXTERNAL = 'external'
    MAIL = 'mail'
    RELATIVE = 'relative'


def classify_href(href: str) -> LinkKind | None:
    """The kind of link `href` is, or None when it must not become an anchor."""
    if not href or _UNSAFE_CHARS.search(href):
        return None
    if match := _SCHEME.match(href):
        scheme = match.group(1).lower()
        if scheme in ('http', 'https'):
            return LinkKind.EXTERNAL
        if scheme == 'mailto':
            return LinkKind.MAIL
        return None
    # Browsers read a backslash as a slash, so `/\host` is as protocol-relative as `//host`.
    if href.startswith('/') and not href.startswith(('//', '/\\')):
        return LinkKind.RELATIVE
    return None


def task_number_from_href(href: str) -> int | None:
    """The task number a link points at, if it is a link to a task page.

    That is `/todo/<number>/` (the slash is optional), site-relative or absolute with the host of
    `settings.BASE_URL`; a query or fragment makes it an ordinary link.
    """
    if href.startswith('/'):
        match = _TASK_PATH.fullmatch(href)
        return int(match.group(1)) if match else None
    try:
        parts = urlsplit(href)
        base = urlsplit(settings.BASE_URL)
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or parts.query or parts.fragment:
        return None
    if not parts.netloc or parts.netloc.lower() != base.netloc.lower():
        return None
    match = _TASK_PATH.fullmatch(parts.path)
    return int(match.group(1)) if match else None
