"""Shared helper for forms with a free-text "one email per line" field (the send page's
recipients, and the dashboard's add-recipients action)."""

import re

from django import forms

_SPLIT_RE = re.compile(r'[,;\n]+')


def parse_email_list(raw: str) -> list[str]:
    """Split a textarea's raw text on commas, semicolons and newlines, and validate each as an
    email address. Raises `forms.ValidationError` naming the first invalid entry."""
    email_field = forms.EmailField()
    emails = []
    for part in _SPLIT_RE.split(raw):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            emails.append(email_field.clean(candidate))
        except forms.ValidationError:
            raise forms.ValidationError(f'"{candidate}" is not a valid email address.')
    return emails
