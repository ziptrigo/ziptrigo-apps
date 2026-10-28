"""`render`: Markdown to sanitised HTML."""

import re
from collections.abc import Callable

import nh3
from django.utils.safestring import SafeString, mark_safe

from .limits import MAX_MARKDOWN_CHARS, check_length
from .links import check_task_url_path
from .parser import get_parser
from .references import TASK_URL_PATH, referenced_numbers
from .renderer import TASK_URLS

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


__all__ = ['MAX_MARKDOWN_CHARS', 'ReferenceResolver', 'render']


def render(
    markdown: str,
    *,
    reference_resolver: ReferenceResolver | None = None,
    task_url_path: re.Pattern[str] | None = None,
) -> SafeString:
    """The HTML for `markdown`, safe to put in a page.

    Raises `ValueError('Document too long')` over `MAX_MARKDOWN_CHARS` characters.

    `reference_resolver` turns task numbers into URLs (without one, `#1234` and links to task
    pages stay plain text and ordinary links). `task_url_path` is the caller's pattern for a task
    page's site-relative path, with a named group `number` and matching the whole path (e.g.
    `^/todo/(?P<number>[0-9]{1,9})/?$`); a link to that path, relative or absolute on the host of
    `settings.BASE_URL`, counts as a mention of that task. Without it URLs are never task
    references. `ValueError` also for a pattern with no `number` group.
    """
    check_length(markdown)
    env: dict = {}
    if task_url_path is not None:
        check_task_url_path(task_url_path)
        env[TASK_URL_PATH] = task_url_path
    md = get_parser()
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
