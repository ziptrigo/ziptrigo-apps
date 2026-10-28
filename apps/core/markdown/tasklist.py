"""Checklist items in the token stream, shared by the renderer and `checklist.py`.

markdown-it's `tasklists` plugin marks a list item that starts with `[ ]`, `[x]` or `[X]` followed
by whitespace with the class `task-list-item` and puts an `html_inline` checkbox first in its
paragraph. Everything that has to agree on "the Nth checklist item" (the `data-index` of a rendered
box, `toggle_checklist_item`, `count_checklist_items`) goes through `checklist_inlines`, so they
count the same items in the same order.
"""

from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

#: The plugin's own core rule; ours has to run after it.
_PLUGIN_RULE = 'github-tasklists'


def checklist_inlines(tokens: Sequence[Token]) -> list[Token]:
    """The `inline` token of each checklist item's first paragraph, in document order."""
    found: list[Token] = []
    for i, token in enumerate(tokens):
        if token.type != 'list_item_open':
            continue
        if 'task-list-item' not in str(token.attrGet('class') or '').split():
            continue
        if (
            i + 2 < len(tokens)
            and tokens[i + 1].type == 'paragraph_open'
            and tokens[i + 2].type == 'inline'
        ):
            found.append(tokens[i + 2])
    return found


def number_checkboxes(state: StateCore) -> None:
    """Core rule: replace the plugin's raw `html_inline` checkbox with a `task_checkbox` token.

    The token carries `meta['index']` (the item's position in the document, from 0) and
    `meta['checked']`, and the renderer writes the `<input>` from them, so no HTML is rewritten.
    """
    for index, inline in enumerate(checklist_inlines(state.tokens)):
        children = inline.children
        if not children or children[0].type != 'html_inline':
            continue
        box = Token('task_checkbox', 'input', 0)
        box.meta = {'index': index, 'checked': 'checked="checked"' in children[0].content}
        children[0] = box


def install_checkbox_numbering(md: MarkdownIt) -> None:
    md.core.ruler.after(_PLUGIN_RULE, 'task_checkbox_index', number_checkboxes)
