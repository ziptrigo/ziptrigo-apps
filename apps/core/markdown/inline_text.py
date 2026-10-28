"""The text of inline tokens, shared by the plain-text export and the renderer's image labels."""

from collections.abc import Sequence

from markdown_it.token import Token

_TEXT_TOKENS = frozenset({'text', 'text_special', 'code_inline', 'task_reference'})


def inline_text(children: Sequence[Token]) -> str:
    """The text of inline `children`: literal text, code spans, `#1234` references, line breaks
    as newlines and an image's alt text. Emphasis marks and link destinations are dropped."""
    parts: list[str] = []
    for token in children:
        if token.type in _TEXT_TOKENS:
            parts.append(token.content)
        elif token.type in ('softbreak', 'hardbreak'):
            parts.append('\n')
        elif token.type == 'image':
            # The alt text; the URL is dropped.
            parts.append(inline_text(token.children or []))
    return ''.join(parts)
