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
