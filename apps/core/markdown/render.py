"""`render`: Markdown to sanitised HTML."""

from collections.abc import Callable

import nh3
from django.utils.safestring import SafeString, mark_safe

from .parser import get_parser
from .references import referenced_numbers
from .renderer import TASK_URLS

#: Longer documents are refused. Callers cap their own fields well below this (the TODO app:
#: 20,000 for a description, 10,000 for a comment).
MAX_MARKDOWN_CHARS = 50_000

#: Given every task number a document mentions (once each, in order of appearance), the numbers
#: the caller recognises mapped to the URL each should link to. Called once per render.
ReferenceResolver = Callable[[list[int]], dict[int, str]]

ALLOWED_TAGS = frozenset(
    'p br strong em code pre h1 h2 h3 h4 h5 h6 ul ol li input blockquote hr a'.split()
)
ALLOWED_ATTRIBUTES = {
    'a': {'href', 'target', 'rel', 'class'},
    'code': {'class'},
    'input': {'type', 'checked', 'disabled', 'class', 'data-index'},
    'ol': {'start'},
    'li': {'class'},
}
ALLOWED_URL_SCHEMES = {'http', 'https', 'mailto'}


def render(markdown: str, *, reference_resolver: ReferenceResolver | None = None) -> SafeString:
    """The HTML for `markdown`, safe to put in a page.

    Raises `ValueError('Document too long')` over `MAX_MARKDOWN_CHARS` characters.
    Without a `reference_resolver`, `#1234` and links to task pages stay plain text and ordinary
    links.
    """
    if len(markdown) > MAX_MARKDOWN_CHARS:
        raise ValueError('Document too long')
    md = get_parser()
    env: dict = {}
    tokens = md.parse(markdown, env)
    if reference_resolver is not None and (numbers := referenced_numbers(tokens)):
        resolved = reference_resolver(numbers)
        env[TASK_URLS] = {number: url for number, url in resolved.items() if number in numbers}
    html = md.renderer.render(tokens, md.options, env)
    cleaned = nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel=None,
    )
    # Safe only because it went through `nh3.clean` with the allow-list above.
    return mark_safe(cleaned)
