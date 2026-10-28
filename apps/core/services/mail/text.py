"""An HTML email body as plain text, for a product that shows it as text (To-do's description).

Stdlib parser only. Scripts, styles and the head are dropped, block elements become line breaks, a
list item starts with `- `, whitespace is collapsed and a link becomes `text (url)` (just `url`
when the text is empty, and nothing added when the text already is the address). Only `http`,
`https` and `mailto` links are kept; anything else (`javascript:`, `data:`, a relative path) shows
its text alone.
"""

import re
from html.parser import HTMLParser

_SKIP_TAGS = frozenset({'script', 'style', 'head', 'title', 'noscript', 'template', 'svg'})
_BLOCK_TAGS = frozenset(
    {
        'p', 'div', 'br', 'tr', 'table', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'section', 'article', 'header', 'footer', 'blockquote', 'pre', 'hr',
    }
)  # fmt: skip
_LINK_SCHEMES = ('http://', 'https://', 'mailto:')


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        #: The open `<a>`: (href, index in `_parts` where its text starts), or `None`.
        self._link: tuple[str, int] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth:
            return
        elif tag == 'li':
            self._parts.append('\n- ')
        elif tag in _BLOCK_TAGS:
            self._parts.append('\n')
        elif tag in ('td', 'th'):
            self._parts.append(' ')
        elif tag == 'a':
            href = dict(attrs).get('href') or ''
            self._link = (href.strip(), len(self._parts))

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS and not self._skip_depth:
            self._parts.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth:
            return
        elif tag in _BLOCK_TAGS or tag == 'li':
            self._parts.append('\n')
        elif tag == 'a' and self._link is not None:
            href, start = self._link
            self._link = None
            label = ''.join(self._parts[start:]).strip()
            if not href.lower().startswith(_LINK_SCHEMES):
                return
            bare = href.split(':', 1)[1] if href.lower().startswith('mailto:') else href
            if label in (href, bare):
                return
            self._parts.append(f' ({href})' if label else href)

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        raw = ''.join(self._parts)
        lines = (re.sub(r'[ \t\r\f\v\xa0]+', ' ', line).strip() for line in raw.split('\n'))
        return re.sub(r'\n{2,}', '\n', '\n'.join(lines)).strip()


def html_to_text(html: str | bytes, *, max_chars: int = 1_000_000) -> str:
    """The visible text of an HTML document (see the module docstring), capped at `max_chars`."""
    if isinstance(html, bytes):
        html = html.decode('utf-8', 'replace')
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # the stdlib parser is lenient, but a body must never fail the message
        pass
    return parser.text()[:max_chars]
