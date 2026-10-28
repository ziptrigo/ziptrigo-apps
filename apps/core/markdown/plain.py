"""Plain text out of Markdown, and the normalised form search runs over.

The TODO app stores `search_text(markdown)` next to every Markdown column and matches queries
against it, so both sides ignore case, accents, punctuation and formatting.
"""

import unicodedata
from collections.abc import Sequence

from markdown_it.token import Token

from .parser import get_parser

_TEXT_TOKENS = frozenset({'text', 'text_special', 'code_inline', 'task_reference'})
#: Block tokens that end a line of text (a paragraph, a list item); others only separate by a space.
_LINE_ENDS = frozenset({'paragraph_close', 'list_item_close'})
_SEPARATORS = frozenset(
    {
        'heading_open',
        'heading_close',
        'blockquote_open',
        'blockquote_close',
        'bullet_list_open',
        'bullet_list_close',
        'ordered_list_open',
        'ordered_list_close',
        'hr',
    }
)


def _inline_text(children: Sequence[Token]) -> str:
    parts: list[str] = []
    for token in children:
        if token.type in _TEXT_TOKENS:
            parts.append(token.content)
        elif token.type in ('softbreak', 'hardbreak'):
            parts.append('\n')
        elif token.type == 'image':
            # The alt text; the URL is dropped.
            parts.append(_inline_text(token.children or []))
    return ''.join(parts)


class _Writer:
    """Collects text, writing the pending separator between two pieces of it (never at the ends)."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pending = ''

    def separate(self, separator: str) -> None:
        # A newline outranks a space.
        self.pending = '\n' if '\n' in (self.pending, separator) else separator

    def write(self, text: str) -> None:
        if not text:
            return
        if self.parts and self.pending:
            self.parts.append(self.pending)
        self.pending = ''
        self.parts.append(text)


def to_plain_text(markdown: str) -> str:
    """The text of `markdown` without its markup.

    Keeps the text of paragraphs, headings, code, link text (a bare URL is its own text), image alt
    text and `#1234` references; drops emphasis and heading marks, checklist boxes, link
    destinations and fence markers. Paragraphs and list items end in a newline, other blocks are
    separated by a single space.
    """
    writer = _Writer()
    for token in get_parser().parse(markdown):
        if token.type == 'inline':
            writer.write(_inline_text(token.children or []).strip())
        elif token.type in ('fence', 'code_block'):
            writer.separate(' ')
            writer.write(token.content.strip('\n'))
            writer.separate(' ')
        elif token.type in _LINE_ENDS:
            writer.separate('\n')
        elif token.type in _SEPARATORS:
            writer.separate(' ')
    return ''.join(writer.parts)


def normalize_for_search(text: str) -> str:
    """Lower-cased words separated by single spaces: no accents, no punctuation.

    `Cão, Ação & Coração!` -> `cao acao coracao`. Letters and digits of every script are kept.
    """
    decomposed = unicodedata.normalize('NFKD', text)
    unmarked = ''.join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = unmarked.casefold()
    words = ''.join(
        ch if ch.isspace() or unicodedata.category(ch)[0] in 'LN' else ' ' for ch in folded
    )
    return ' '.join(words.split())


def search_text(markdown: str) -> str:
    """What the TODO app stores for searching: `normalize_for_search(to_plain_text(markdown))`."""
    return normalize_for_search(to_plain_text(markdown))
