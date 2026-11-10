"""The colour schemes (`apps.core.color_schemes`): the data, the stylesheet and the logos."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import get_template

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


def _text_pairs(scheme) -> dict[str, tuple[str, str]]:
    """The text-on-background pairs the templates use, as (text, background)."""
    brand = dict(zip(color_schemes.BRAND_STEPS, scheme.brand))
    gray = dict(zip(color_schemes.GRAY_STEPS, scheme.gray))
    button = (WHITE, brand[600]) if scheme.solid else (gray[900], brand[300])
    button_hover = (WHITE, brand[700]) if scheme.solid else (gray[900], brand[400])
    pairs = {
        'light button': button,
        'light button hover': button_hover,
        'light tinted pill': (brand[600], brand[100]),
        'light flash message': (brand[800], brand[100]),
        'light picker day hover': (brand[800], brand[100]),
        'light muted text': (gray[500], gray[50]),
        'light body text': (gray[700], gray[50]),
        'dark button': (gray[900], brand[300]),
        'dark button hover': (gray[900], brand[400]),
        'dark flash message': (brand[100], brand[800]),
        'dark muted text': (gray[400], gray[800]),
        'dark body text': (gray[300], gray[900]),
    }
    # Every accent step the templates use for text, on each surface a page puts text on.
    for step in LIGHT_TEXT_STEPS:
        for name, surface in (('white', WHITE), ('gray-50', gray[50]), ('gray-100', gray[100])):
            pairs[f'light text-sage-{step} on {name}'] = (brand[step], surface)
    for step in DARK_TEXT_STEPS:
        for name in (800, 900):
            pairs[f'dark text-sage-{step} on gray-{name}'] = (brand[step], gray[name])
    return pairs


#: `text-sage-<step>` classes that carry words, per mode. The lighter steps (300 to 500 in light mode)
#: are only ever on icons, which need no text contrast: see the guard test below.
LIGHT_TEXT_STEPS = (600, 700, 800)
DARK_TEXT_STEPS = (100, 300)
ICON_ONLY_STEPS = (300, 400, 500)
#: Elements that carry such a class and hold only an icon (the calendar button, the empty state's
#: circle, the completed task's check).
ICON_WRAPPERS = {
    'core/templates/core/components/date_picker.html: <button>',
    'core/templates/core/partials/empty_state.html: <div>',
    'todo/templates/todo/partials/task_row.html: <button>',
}


@pytest.mark.parametrize('scheme', color_schemes.all_schemes(), ids=lambda s: s.id)
def test_every_scheme_passes_wcag_aa_for_the_text_the_templates_put_on_it(scheme):
    """4.5:1 for the pairs the component recipes and the templates use, in light and in dark."""
    failing = {
        name: round(_contrast(*pair), 2)
        for name, pair in _text_pairs(scheme).items()
        if _contrast(*pair) < 4.5
    }
    assert not failing


def _accent_text_classes() -> set[tuple[bool, int]]:
    """Every `(is dark variant, step)` of a `text-sage-*` / `text-accent-*` class in the app's
    templates and scripts, hover states aside."""
    apps = Path(__file__).resolve().parents[2]
    found: set[tuple[bool, int]] = set()
    for path in [*apps.glob('*/templates/**/*.html'), *apps.glob('*/static/**/*.js')]:
        for prefix, step in re.findall(
            r'((?:[a-z-]+:)*)text-(?:sage|accent)-(\d+)', path.read_text()
        ):
            if 'hover:' not in prefix:
                found.add(('dark:' in prefix, int(step)))
    return found


def test_the_contrast_test_knows_every_accent_text_class_in_use():
    """A new `text-sage-<step>` must be added to the lists above, so its contrast is checked in
    every scheme (or it is declared icon-only)."""
    light = {step for dark, step in _accent_text_classes() if not dark}
    dark = {step for dark, step in _accent_text_classes() if dark}
    assert light <= {*LIGHT_TEXT_STEPS, *ICON_ONLY_STEPS}
    assert dark <= set(DARK_TEXT_STEPS)


def test_the_light_accent_steps_are_only_on_icons():
    """`text-sage-300/400/500` without `dark:` is too pale for words on a light page."""
    apps = Path(__file__).resolve().parents[2]
    pattern = re.compile(r'<(\w+)\b[^<>]*?(?<![:\w-])text-(?:sage|accent)-(?:300|400|500)\b')
    offenders = []
    for path in apps.glob('*/templates/**/*.html'):
        for tag in pattern.findall(path.read_text()):
            where = f'{path.relative_to(apps)}: <{tag}>'
            if tag not in ('i', 'span', 'svg') and where not in ICON_WRAPPERS:
                offenders.append(where)
    assert not offenders


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
        assert len(re.findall(r'--c-brand-\d+:', block)) == 10
        assert len(re.findall(r'--c-gray-\d+:', block)) == 11


def test_solid_schemes_swap_the_primary_variables_in_light_mode_only():
    css = color_schemes.render_css()
    assert '--c-primary: var(--c-brand-300);' in css.split('}')[0]
    for scheme in color_schemes.all_schemes():
        rule = f'html[data-scheme="{scheme.id}"]:not(.dark) {{'
        assert (rule in css) is scheme.solid
    # Variables only: no rule reaches into the page's elements.
    assert '.bg-brand-primary' not in css


def test_only_controls_take_the_primary_label_and_hover():
    """A `bg-brand-primary` progress bar must not turn into a button."""
    site = Path(finders.find('core/css/site.css')).read_text()
    rules = [line for line in site.splitlines() if '.bg-brand-primary' in line and '{' in line]
    assert rules
    assert all(
        line.startswith(":is(a, button, input[type='submit']).bg-brand-primary") for line in rules
    )


def test_the_reference_pages_data_is_what_the_module_generates():
    path = Path(settings.PROJECT_ROOT) / color_schemes.REFERENCE_JS_PATH
    assert path.read_text() == color_schemes.render_reference_js(), (
        f'{color_schemes.REFERENCE_JS_PATH} is out of date: run `./manage.py build_color_schemes`.'
    )


def test_the_reference_pages_tailwind_config_has_sages_values():
    source = (
        Path(settings.PROJECT_ROOT) / 'docs/ui-design/reference/assets/reference.js'
    ).read_text()
    sage = color_schemes.SCHEMES['sage']
    for step, value in zip(color_schemes.BRAND_STEPS, sage.brand):
        assert f"  {step}: '#{value}'" in source
    for step, value in zip(color_schemes.GRAY_STEPS, sage.gray):
        assert f"{step}: '#{value}'" in source


@pytest.mark.parametrize('scheme', color_schemes.all_schemes(), ids=lambda s: s.id)
def test_every_scheme_has_its_logos(scheme):
    for size in color_schemes.LOGO_SIZES:
        assert finders.find(scheme.logo_path(size)) is not None, (
            f'{scheme.logo_path(size)} is missing: run `./manage.py build_color_schemes`.'
        )


def test_base_template_maps_the_tailwind_colours_to_the_variables():
    source = Path(get_template('core/base.html').origin.name).read_text()
    assert "300: 'rgb(var(--c-brand-300) / <alpha-value>)'" in source
    assert "950: 'rgb(var(--c-gray-950) / <alpha-value>)'" in source
    assert 'accent: ACCENT,' in source
    assert 'sage: ACCENT,' in source
    assert "primary: 'rgb(var(--c-primary) / <alpha-value>)'" in source
    assert "on: 'rgb(var(--c-on-primary) / <alpha-value>)'" in source
    assert 'core/css/color_schemes.css' in source
