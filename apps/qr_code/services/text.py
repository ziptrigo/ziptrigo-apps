"""Cleaning the short texts a user types (a share link's label). A copy of World Time's
`clean_line`: products may not import each other."""

import unicodedata


def clean_line(value: object) -> str:
    """`value` as one line of visible text: control and format characters dropped (zero-width
    spaces, bidirectional overrides: a text made of them would look empty, or read backwards, in
    the owner's list), white space collapsed and trimmed."""
    text = ''.join(
        ' ' if char.isspace() else char
        for char in str(value or '')
        if char.isspace() or unicodedata.category(char) not in ('Cc', 'Cf')
    )
    return ' '.join(text.split())


def clean_message(value: object) -> str:
    """`value` as a short multi-line message: line endings normalised to `\n`, control and format
    characters dropped (a tab is a space; zero-width and bidirectional-override characters would
    hide or reverse text in someone's inbox), white space inside a line collapsed, trailing space
    removed, more than one blank line in a row cut to one, and the whole trimmed."""
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    lines: list[str] = []
    for raw in text.split('\n'):
        kept = ''.join(
            ' ' if char.isspace() else char
            for char in raw
            if char.isspace() or unicodedata.category(char) not in ('Cc', 'Cf')
        )
        line = ' '.join(kept.split())
        if line or (lines and lines[-1]):
            lines.append(line)
    return '\n'.join(lines).strip()
