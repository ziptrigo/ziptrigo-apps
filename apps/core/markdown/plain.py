"""Plain text out of Markdown, and the normalised form search runs over.

The TODO app stores `search_text(markdown)` next to every Markdown column and matches queries
against it, so both sides ignore case, accents, punctuation and formatting.
"""

import unicodedata

from .inline_text import inline_text
from .limits import check_length
from .parser import get_parser

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
    separated by a single space. Raises `ValueError('Document too long')` over
    `MAX_MARKDOWN_CHARS`, like `render`.
    """
    check_length(markdown)
    writer = _Writer()
    for token in get_parser().parse(markdown):
        if token.type == 'inline':
            writer.write(inline_text(token.children or []).strip())
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

    `Cão, Ação & Coração!` -> `cao acao coracao`. Letters, digits and combining marks of every
    script are kept as word characters: NFKD first splits an accented Latin letter into the letter
    and a non-spacing mark, and that mark (combining class above 0) is dropped; the vowel signs of
    Devanagari and other abugidas (class 0, category M) stay, so `किताब` is still one word.
    """
    decomposed = unicodedata.normalize('NFKD', text)
    unmarked = ''.join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = unmarked.casefold()
    words = ''.join(
        ch if ch.isspace() or unicodedata.category(ch)[0] in 'LNM' else ' ' for ch in folded
    )
    return ' '.join(words.split())


def search_text(markdown: str) -> str:
    """What the TODO app stores for searching: `normalize_for_search(to_plain_text(markdown))`.

    Raises `ValueError('Document too long')` over `MAX_MARKDOWN_CHARS`.
    """
    return normalize_for_search(to_plain_text(markdown))
