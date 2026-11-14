"""Cleaning the short texts a user types (a tab's name, a row's label, an event's title)."""

import unicodedata


def clean_line(value: object) -> str:
    """`value` as one line of visible text: control and format characters dropped (zero-width
    spaces, bidirectional overrides: a text made of them would look empty, or read backwards, on a
    page other people open), white space collapsed and trimmed."""
    text = ''.join(
        ' ' if char.isspace() else char
        for char in str(value or '')
        if char.isspace() or unicodedata.category(char) not in ('Cc', 'Cf')
    )
    return ' '.join(text.split())
