"""The size guard shared by everything that parses a whole document."""

#: Longer documents are refused. Callers cap their own fields well below this (the TODO app:
#: 20,000 for a description, 10,000 for a comment).
MAX_MARKDOWN_CHARS = 50_000


def check_length(markdown: str) -> None:
    """Raise `ValueError('Document too long')` over `MAX_MARKDOWN_CHARS` characters."""
    if len(markdown) > MAX_MARKDOWN_CHARS:
        raise ValueError('Document too long')
