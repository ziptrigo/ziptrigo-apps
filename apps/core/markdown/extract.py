"""`extract_references`: which tasks a document mentions, without rendering it."""

import re

from .limits import check_length
from .links import check_task_url_path
from .parser import get_parser
from .references import TASK_URL_PATH, referenced_numbers


def extract_references(markdown: str, *, task_url_path: re.Pattern[str] | None = None) -> list[int]:
    """The task numbers `markdown` mentions, once each, in order of appearance.

    The very traversal `render` does to find what to hand its resolver: `#` plus 4 to 9 digits
    (not inside code or a link's text) and, when the caller passes `task_url_path` (its pattern
    for a task page's site-relative path, with a named group `number`, as for `render`), links to
    a task page, relative or absolute on the host of `settings.BASE_URL`. Without a pattern URLs
    are never task references. Raises `ValueError('Document too long')` over
    `MAX_MARKDOWN_CHARS`, and for a pattern with no `number` group.
    """
    check_length(markdown)
    env: dict = {}
    if task_url_path is not None:
        check_task_url_path(task_url_path)
        env[TASK_URL_PATH] = task_url_path
    return referenced_numbers(get_parser().parse(markdown, env))
