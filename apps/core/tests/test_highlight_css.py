"""The `extended` Markdown profile's stylesheet is generated; the committed file must match."""

import re
from pathlib import Path

from django.contrib.staticfiles import finders

from apps.core import color_schemes
from apps.core.markdown import highlight_css
from apps.core.tests.test_color_schemes import _contrast


def test_the_committed_stylesheet_is_what_the_generator_writes():
    path = finders.find(highlight_css.CSS_PATH)
    assert path is not None
    assert Path(path).read_text() == highlight_css.render_css(), (
        'core/css/highlight.css is out of date: run `./manage.py build_highlight_css` '
        '(needed after a Pygments upgrade too: a style can change its colours).'
    )


def test_the_stylesheet_scopes_both_styles_and_styles_tables():
    css = highlight_css.render_css()
    assert 'pre.hl .hl-k' in css
    assert '.dark pre.hl .hl-k' in css
    assert '.md-table' in css and 'overflow-x: auto' in css


_COLOR = re.compile(r'(?<![-\w])color:\s*#([0-9a-fA-F]{6})\b')
_BACKGROUND = re.compile(r'background-color:\s*#([0-9a-fA-F]{6})\b')


def _sage(step: int) -> str:
    scheme = color_schemes.get_scheme(color_schemes.DEFAULT)
    return dict(zip(color_schemes.GRAY_STEPS, scheme.gray))[step].lstrip('#')


def _pairs(css: str, scope: str, block: str) -> list[tuple[str, str, str]]:
    """(rule, text colour, background) for every coloured token rule of one mode."""
    pairs = []
    for line in css.splitlines():
        if not line.startswith(f'{scope} .') or not (color := _COLOR.search(line)):
            continue
        own = _BACKGROUND.search(line)
        pairs.append((line, color.group(1), own.group(1) if own else block))
    return pairs


def _mode(css: str, start: str, end: str) -> str:
    return css.split(start)[1].split(end)[0]


def test_every_token_colour_reaches_aa_on_the_block_background_in_both_modes():
    css = highlight_css.render_css()
    light = _mode(css, '/* Light:', '/* Dark:')
    dark = _mode(css, '/* Dark:', '/* Tables')
    for scope, text, block in (('pre.hl', light, _sage(50)), ('.dark pre.hl', dark, _sage(900))):
        pairs = _pairs(text, scope, block)
        assert len(pairs) > 20
        failing = {
            line: round(_contrast(fg, bg), 2) for line, fg, bg in pairs if _contrast(fg, bg) < 4.5
        }
        assert not failing, failing


def test_the_block_background_follows_the_colour_scheme_and_has_no_hex():
    css = highlight_css.render_css()

    assert 'pre.hl { background: rgb(var(--c-gray-50));' in css
    assert '.dark pre.hl { background: rgb(var(--c-gray-900));' in css
    assert not re.search(r'pre\.hl \{[^}]*#', css)
    assert '.hll' not in css


def test_a_low_contrast_colour_is_raised_and_a_good_one_is_left_alone():
    assert highlight_css._readable('60a0b0', 'f6f8f7', '000000') != '60a0b0'
    assert _contrast(highlight_css._readable('60a0b0', 'f6f8f7', '000000'), 'f6f8f7') >= 4.5
    assert highlight_css._readable('000000', 'f6f8f7', '000000') == '000000'
