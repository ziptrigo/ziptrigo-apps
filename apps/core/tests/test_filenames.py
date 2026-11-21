"""`apps.core.services.filenames`: renaming a file while keeping its extension, and the template
filters that show the two apart."""

import pytest
from django.template import Context, Template

from apps.core.services.filenames import clean_stem, renamed, split_extension

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('report.pdf', ('report', '.pdf')),
        ('2026-11-21-10-45-30.webp', ('2026-11-21-10-45-30', '.webp')),
        ('archive.tar.gz', ('archive.tar', '.gz')),
        ('README', ('README', '')),
        ('.env', ('.env', '')),
        ('Report v1.2 final', ('Report v1.2 final', '')),
        ('a.verylongextension', ('a.verylongextension', '')),
        ('', ('', '')),
    ],
)
def test_split_extension(name, expected):
    assert split_extension(name) == expected


@pytest.mark.parametrize(
    ('current', 'typed', 'expected'),
    [
        ('photo.webp', 'Whiteboard', 'Whiteboard.webp'),
        ('photo.webp', 'Whiteboard.webp', 'Whiteboard.webp'),
        ('photo.webp', 'Whiteboard.WEBP', 'Whiteboard.webp'),
        ('photo.webp', 'Whiteboard.png', 'Whiteboard.png.webp'),
        ('README', 'NOTES.md', 'NOTES.md'),
        ('photo.webp', '  a  b  ', 'a b.webp'),
        ('photo.webp', 'a/b\\c', 'a_b_c.webp'),
        ('photo.webp', '‮evil\x00', 'evil.webp'),
        ('photo.webp', '   ', ''),
        ('photo.webp', '.webp', '.webp.webp'),
    ],
)
def test_renamed(current, typed, expected):
    assert renamed(current, typed) == expected


def test_renamed_cuts_the_name_not_the_extension():
    result = renamed('photo.webp', 'x' * 300, max_length=20)

    assert result == 'x' * 15 + '.webp'


def test_clean_stem_collapses_white_space():
    assert clean_stem('a\t\n b') == 'a b'


@pytest.mark.parametrize(
    ('typed', 'expected'),
    [
        (
            '\U0001f468\u200d\U0001f469\u200d\U0001f467',
            '\U0001f468\u200d\U0001f469\u200d\U0001f467',
        ),
        ('\u200d', ''),
        ('a\u200d', 'a'),
        ('\u200d\u200dx', 'x'),
        (' \u200d ', ''),
        ('a \u200db', 'a b'),
        ('a\u200d\u200db', 'ab'),
        ('a\u200bb', 'ab'),
    ],
)
def test_a_zero_width_joiner_is_kept_only_between_characters(typed, expected):
    assert clean_stem(typed) == expected


def test_the_template_filters():
    template = Template('{% load filenames %}[{{ n|file_stem }}][{{ n|file_extension }}]')

    assert template.render(Context({'n': 'shot.png'})) == '[shot][.png]'
    assert template.render(Context({'n': None})) == '[][]'
