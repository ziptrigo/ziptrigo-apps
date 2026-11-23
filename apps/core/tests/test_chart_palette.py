"""The chart palette (`--c-chart-*` in core/css/site.css, issue #347) is readable on the cards of every scheme."""

import re
from pathlib import Path

import pytest

from apps.core import color_schemes

CSS = Path(color_schemes.__file__).parent / 'static/core/css/site.css'
NAMES = [*(str(n) for n in range(1, 9)), 'other']


def _luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip('#')
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    lighter, darker = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _block(selector: str) -> dict[str, str]:
    css = CSS.read_text()
    block = re.search(re.escape(selector) + r' \{(.*?)\}', css[css.index('Chart palette') :], re.S)
    assert block, selector
    return dict(re.findall(r'--c-chart-([a-z0-9]+): (#[0-9a-f]{6});', block.group(1)))


def test_both_modes_define_every_slot():
    assert sorted(_block(':root')) == sorted(NAMES)
    assert sorted(_block('.dark')) == sorted(NAMES)


@pytest.mark.parametrize('scheme', color_schemes.all_schemes(), ids=lambda s: s.id)
@pytest.mark.parametrize('high_contrast', [False, True])
def test_every_colour_is_3_to_1_on_the_card(scheme, high_contrast):
    light, dark = _block(':root'), _block('.dark')
    card_light = color_schemes.palette(scheme, high_contrast=high_contrast).surface
    card_dark = color_schemes.palette(scheme, dark=True, high_contrast=high_contrast).gray[800]
    low = {
        **{f'light {n}': round(_contrast(light[n], card_light), 2) for n in NAMES},
        **{f'dark {n}': round(_contrast(dark[n], card_dark), 2) for n in NAMES},
    }
    assert {k: v for k, v in low.items() if v < 3} == {}


def test_the_colours_are_distinct():
    assert len(set(_block(':root').values())) == len(NAMES)
    assert len(set(_block('.dark').values())) == len(NAMES)
