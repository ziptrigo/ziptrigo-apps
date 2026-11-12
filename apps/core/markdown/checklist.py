"""Flipping a checklist box in Markdown source.

Works on the source text, not the rendered HTML: the parser's token stream says which source line
holds the `index`th checklist item (the same numbering the renderer puts in `data-index`) and only
the three characters of its marker change.
"""

import re

from .inline_text import inline_text
from .parser import check_profile, get_parser
from .tasklist import checklist_inlines

_MARKER = re.compile(r'\[[ xX]\]')
#: Source lines with their terminators (`\r\n`, `\r` or `\n`, as markdown-it counts them).
_LINES = re.compile(r'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+')


def count_checklist_items(markdown: str, *, profile: str = 'basic') -> int:
    """How many `- [ ]` / `- [x]` items the document has, nested and quoted ones included."""
    return len(checklist_inlines(get_parser(check_profile(profile)).parse(markdown)))


def checklist_item_text(markdown: str, index: int, *, profile: str = 'basic') -> str:
    """The text of the `index`th checklist item as plain text (no box, no list marker, no emphasis
    marks, whitespace collapsed; the same numbering as `toggle_checklist_item`). Raises
    `IndexError` when there is no such item."""
    items = checklist_inlines(get_parser(check_profile(profile)).parse(markdown))
    if not 0 <= index < len(items):
        raise IndexError(f'No checklist item {index} (the document has {len(items)})')
    return ' '.join(inline_text(items[index].children or []).split())


def toggle_checklist_item(
    markdown: str, index: int, checked: bool, *, profile: str = 'basic'
) -> str:
    """`markdown` with the `index`th checklist item (from 0, in document order) checked or not.

    Only its `[ ]` / `[x]` changes; whitespace, the rest of the line and the line endings stay
    byte for byte. Raises `IndexError` when there is no such item.
    """
    items = checklist_inlines(get_parser(check_profile(profile)).parse(markdown))
    if not 0 <= index < len(items):
        raise IndexError(f'No checklist item {index} (the document has {len(items)})')
    line_map = items[index].map
    if line_map is None:
        raise ValueError('Checklist item without a source position')
    lines = _LINES.findall(markdown)
    line = lines[line_map[0]]
    # A line holds only container prefixes (`>`, list markers, spaces) before the item's own
    # text, so the first `[ ]` on it is the marker.
    marker = _MARKER.search(line)
    if marker is None:
        raise ValueError('Checklist marker not found on its source line')
    lines[line_map[0]] = (
        line[: marker.start()] + ('[x]' if checked else '[ ]') + line[marker.end() :]
    )
    return ''.join(lines)
