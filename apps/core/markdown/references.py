"""Task references in Markdown: `#1234` and links to a task page.

Two parser rules, both inert until the renderer is given a resolver:

* an inline rule that turns `#` plus 4 to 9 digits into a `task_reference` token (numbers start at
  1000, so `#1` to `#999` are never references and stay text);
* a core rule, run after markdown-it's own `linkify`, that marks every link whose destination is a
  task page with `meta['task_number']`. What a task page's path looks like is the caller's
  knowledge: `render` puts its `task_url_path` pattern in `env[TASK_URL_PATH]`, and without one no
  link is a task link.
"""

import re

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from .links import task_number_from_href

#: Beside whitespace, what may come right before the `#` (`*` and `_`: emphasis around it).
_BEFORE = frozenset('([{"\',*_')
#: Beside whitespace and the end of the text, what may come right after the digits.
_AFTER = frozenset(')]}"\',.;:!?*_')
#: `env` key holding the caller's `re.Pattern` for a task page's path (see `links`).
TASK_URL_PATH = 'task_url_path'
_DIGITS = re.compile(r'[0-9]{4,9}')


def task_reference(state: StateInline, silent: bool) -> bool:
    """Inline rule: `#1234` -> a `task_reference` token (`meta['number']`)."""
    # The same guard markdown-it's own `linkify` uses: nothing inside a link's text.
    if state.linkLevel > 0:
        return False
    pos = state.pos
    if state.src[pos] != '#':
        return False
    if pos > 0:
        before = state.src[pos - 1]
        if not (before.isspace() or before in _BEFORE):
            return False
    match = _DIGITS.match(state.src, pos + 1, state.posMax)
    if match is None:
        return False
    end = match.end()
    if end < state.posMax:
        after = state.src[end]
        if not (after.isspace() or after in _AFTER):
            return False
    if not silent:
        token = state.push('task_reference', '', 0)
        token.content = state.src[pos:end]
        token.meta = {'number': int(match.group())}
    state.pos = end
    return True


def mark_task_links(state: StateCore) -> None:
    """Core rule: tag links to a task page with `meta['task_number']`."""
    task_url_path = state.env.get(TASK_URL_PATH)
    if task_url_path is None:
        return
    for block in state.tokens:
        if block.type != 'inline' or not block.children:
            continue
        for token in block.children:
            if token.type != 'link_open':
                continue
            href = token.attrGet('href')
            if (
                isinstance(href, str)
                and (number := task_number_from_href(href, task_url_path)) is not None
            ):
                token.meta['task_number'] = number


def install_references(md: MarkdownIt) -> None:
    md.inline.ruler.before('emphasis', 'task_reference', task_reference)
    md.core.ruler.after('linkify', 'task_links', mark_task_links)


def referenced_numbers(tokens: list[Token]) -> list[int]:
    """Every task number the parsed document mentions, once each, in order of appearance."""
    numbers: dict[int, None] = {}
    for block in tokens:
        for token in block.children or []:
            if token.type == 'task_reference':
                numbers.setdefault(token.meta['number'])
            elif token.type == 'link_open' and 'task_number' in token.meta:
                numbers.setdefault(token.meta['task_number'])
    return list(numbers)
