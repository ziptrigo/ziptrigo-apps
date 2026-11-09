"""The colour schemes (`apps.core.color_schemes`): the data, the stylesheet and the logos."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.staticfiles import finders

from apps.core import color_schemes

pytestmark = pytest.mark.unit

HEX = re.compile(r'^[0-9a-f]{6}$')
WHITE = 'ffffff'


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    lighter, darker = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_sage_is_the_default_and_first():
    assert color_schemes.DEFAULT == 'sage'
    assert color_schemes.CHOICES[0] == ('sage', 'Sage')
    assert len(color_schemes.all_schemes()) == 8


@pytest.mark.parametrize('scheme', color_schemes.all_schemes(), ids=lambda s: s.id)
def test_every_scheme_has_both_whole_scales(scheme):
    assert re.fullmatch(r'[a-z]+', scheme.id)
    assert len(scheme.brand) == len(color_schemes.BRAND_STEPS)
    assert len(scheme.gray) == len(color_schemes.GRAY_STEPS)
    assert all(HEX.match(value) for value in (*scheme.brand, *scheme.gray))
    # Lightest first, like Tailwind's steps.
    for scale in (scheme.brand, scheme.gray):
        luminances = [_luminance(value) for value in scale]
        assert luminances == sorted(luminances, reverse=True)


@pytest.mark.parametrize('scheme', color_schemes.all_schemes(), ids=lambda s: s.id)
def test_every_scheme_passes_wcag_aa_for_the_text_the_templates_put_on_it(scheme):
    """4.5:1 for the pairs the component recipes use, in light and in dark."""
    brand = dict(zip(color_schemes.BRAND_STEPS, scheme.brand))
    gray = dict(zip(color_schemes.GRAY_STEPS, scheme.gray))
    light_button = (WHITE, brand[600]) if scheme.solid else (gray[900], brand[300])
    light_hover = (WHITE, brand[700]) if scheme.solid else (gray[900], brand[400])
    pairs = {
        'light button': light_button,
        'light button hover': light_hover,
        'light link on white': (brand[600], WHITE),
        'light link on page': (brand[600], gray[50]),
        'light tinted pill': (brand[600], brand[100]),
        'light flash message': (brand[800], brand[100]),
        'light muted text': (gray[500], gray[50]),
        'dark button': (gray[900], brand[300]),
        'dark link on card': (brand[300], gray[800]),
        'dark flash message': (brand[100], brand[800]),
        'dark muted text': (gray[400], gray[800]),
        'dark body text': (gray[300], gray[900]),
    }
    failing = {
        name: round(_contrast(*pair), 2) for name, pair in pairs.items() if _contrast(*pair) < 4.5
    }
    assert not failing


def test_get_scheme_falls_back_to_the_default():
    assert color_schemes.get_scheme('plum').id == 'plum'
    assert color_schemes.get_scheme('nope').id == 'sage'
    assert color_schemes.get_scheme(None).id == 'sage'


def test_scheme_for_reads_the_users_attribute_and_defaults_for_a_visitor():
    assert color_schemes.scheme_for(SimpleNamespace(color_scheme='ember')).id == 'ember'
    assert color_schemes.scheme_for(SimpleNamespace()).id == 'sage'
    assert color_schemes.scheme_for(None).id == 'sage'


def test_the_committed_stylesheet_is_what_the_module_generates():
    path = finders.find(color_schemes.CSS_PATH)
    assert path is not None
    assert Path(path).read_text() == color_schemes.render_css(), (
        'core/css/color_schemes.css is out of date: run `./manage.py build_color_schemes`.'
    )


def test_stylesheet_defines_every_variable_for_every_scheme():
    css = color_schemes.render_css()
    assert ':root, html[data-scheme="sage"]' in css
    assert '--c-brand-300: 143 168 158;' in css
    for scheme in color_schemes.all_schemes():
        block = css.split(f'html[data-scheme="{scheme.id}"] {{')[1].split('}')[0]
        assert block.count('--c-brand-') == 10
        assert block.count('--c-gray-') == 11


def test_solid_schemes_get_a_white_on_dark_button_in_light_mode_only():
    css = color_schemes.render_css()
    for scheme in color_schemes.all_schemes():
        rule = f'html[data-scheme="{scheme.id}"]:not(.dark) .bg-brand-primary'
        assert (rule in css) is scheme.solid


@pytest.mark.parametrize('scheme', color_schemes.all_schemes(), ids=lambda s: s.id)
def test_every_scheme_has_its_logos(scheme):
    for size in color_schemes.LOGO_SIZES:
        assert finders.find(scheme.logo_path(size)) is not None, (
            f'{scheme.logo_path(size)} is missing: run `./manage.py build_color_schemes`.'
        )


def test_base_template_maps_the_tailwind_colours_to_the_variables():
    base = Path(finders.find('core/css/site.css')).parents[3] / 'templates/core/base.html'
    source = base.read_text()
    assert "300: 'rgb(var(--c-brand-300) / <alpha-value>)'" in source
    assert "950: 'rgb(var(--c-gray-950) / <alpha-value>)'" in source
    assert "primary: 'rgb(var(--c-brand-300) / <alpha-value>)'" in source
    assert 'core/css/color_schemes.css' in source
