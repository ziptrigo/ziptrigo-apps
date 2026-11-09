"""The site's colour schemes: one accent scale and one tinted neutral scale each.

A scheme is independent of light and dark (`User.theme`): every scheme has both, because the
templates pick steps of the two scales (`bg-sage-300`, `dark:bg-gray-800`) and a scheme only says
which colours those steps are. The Tailwind config in `core/base.html` maps `sage-*`, `brand-*` and
`gray-*` to the CSS variables `--c-brand-<step>` / `--c-gray-<step>`, and
`core/css/color_schemes.css` gives the variables their values per `<html data-scheme="...">`.

This module is the source of truth. `./manage.py build_color_schemes` writes the stylesheet and the
recoloured logos from it, and a test fails while the committed stylesheet differs. `core` cannot
import `accounts`, so the active scheme is read off the user as a plain attribute
(`scheme_for`), the way `base.html` reads `user.theme`.
"""

from dataclasses import dataclass

BRAND_STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900)
GRAY_STEPS = (*BRAND_STEPS, 950)

DEFAULT = 'sage'

#: Where the stylesheet and the recoloured logos live, relative to a `static/` directory.
CSS_PATH = 'core/css/color_schemes.css'
LOGO_SIZES = (32, 128)


@dataclass(frozen=True)
class ColorScheme:
    id: str
    label: str
    description: str
    #: Hex colours without `#`, lightest first: the accent (`BRAND_STEPS`) and the neutrals (`GRAY_STEPS`).
    brand: tuple[str, ...]
    gray: tuple[str, ...]
    #: In light mode the primary button is the dark accent with a white label, instead of the
    #: light accent with a dark label: a pale navy or grey button looks washed out.
    solid: bool = False

    def brand_hex(self, step: int) -> str:
        return '#' + self.brand[BRAND_STEPS.index(step)]

    @property
    def theme_color(self) -> str:
        """The browser chrome colour (`<meta name="theme-color">`)."""
        return self.brand_hex(300)

    @property
    def swatches(self) -> list[str]:
        """A few accent steps that show the scheme in the picker."""
        return [self.brand_hex(step) for step in (100, 300, 500, 700)]

    def logo_path(self, size: int) -> str:
        """The static path of the logo in this scheme's colours."""
        if self.id == DEFAULT:
            return f'core/images/logo_{size}x{size}.png'
        return f'core/images/schemes/{self.id}/logo_{size}x{size}.png'

    @property
    def logo_32(self) -> str:
        return self.logo_path(32)

    @property
    def logo_128(self) -> str:
        return self.logo_path(128)


_SCHEMES = (
    ColorScheme(
        'sage',
        'Sage',
        'Muted green',
        (
            'f4f7f6',
            'd4e0da',
            'b5c7be',
            '8fa89e',
            '728e84',
            '5a736a',
            '475a53',
            '3b4a47',
            '2c3432',
            '1e2422',
        ),
        (
            'f6f8f7',
            'eef1f0',
            'dfe4e2',
            'c6cecb',
            '98a39f',
            '66716d',
            '4f5956',
            '3c4441',
            '282e2c',
            '1a1f1d',
            '111514',
        ),
    ),
    ColorScheme(
        'honey',
        'Honey',
        'Gold on cream',
        (
            'fbf8ec',
            'f6ecc9',
            'eedb98',
            'e3c463',
            'd1a93a',
            'ab8527',
            '7f611e',
            '664e1c',
            '473717',
            '2f2410',
        ),
        (
            'faf8f3',
            'f3f0e8',
            'e6e1d5',
            'd1cabb',
            'a59d8b',
            '746c5c',
            '5b5446',
            '453f35',
            '2e2a23',
            '1f1c17',
            '151310',
        ),
    ),
    ColorScheme(
        'ember',
        'Ember',
        'Terracotta and orange',
        (
            'fcf4f0',
            'f8e1d6',
            'f0c3ae',
            'e59d7e',
            'd67753',
            'bb5837',
            '96442b',
            '743524',
            '4f251b',
            '341913',
        ),
        (
            'faf7f6',
            'f3eeec',
            'e7dfdc',
            'd3c8c4',
            'a89a95',
            '766964',
            '5c514d',
            '463d3a',
            '2f2927',
            '201b1a',
            '161211',
        ),
    ),
    ColorScheme(
        'sky',
        'Sky',
        'Light blue',
        (
            'f1f8fc',
            'd9ecf7',
            'b3d9ef',
            '7fbfe2',
            '4fa1cf',
            '3383b3',
            '296890',
            '235372',
            '1b3c52',
            '132837',
        ),
        (
            'f6f8fa',
            'edf1f5',
            'dee4ea',
            'c5ced8',
            '96a2b0',
            '64707e',
            '4d5864',
            '3b434d',
            '272d34',
            '1a1e24',
            '111419',
        ),
    ),
    ColorScheme(
        'midnight',
        'Midnight',
        'Deep navy',
        (
            'f1f4fb',
            'dde4f5',
            'bccaeb',
            '8fa5dc',
            '6480c8',
            '4762ad',
            '364c8c',
            '2b3c6e',
            '1f2a4d',
            '151c33',
        ),
        (
            'f6f7fb',
            'eeeff6',
            'dfe1ec',
            'c7cada',
            '999db3',
            '676b82',
            '505368',
            '3c3f50',
            '282a37',
            '1a1c27',
            '10111a',
        ),
        solid=True,
    ),
    ColorScheme(
        'lagoon',
        'Lagoon',
        'Teal',
        (
            'f0f9f8',
            'd3eeeb',
            'a9ddd8',
            '74c5be',
            '47a8a1',
            '308a84',
            '276e6a',
            '225855',
            '1a3f3d',
            '122a29',
        ),
        (
            'f5f8f8',
            'ecf1f1',
            'dde4e4',
            'c4cece',
            '95a3a3',
            '637171',
            '4c5858',
            '3a4343',
            '272d2d',
            '1a1f1f',
            '111515',
        ),
    ),
    ColorScheme(
        'plum',
        'Plum',
        'Soft violet',
        (
            'f8f4fa',
            'ecdff1',
            'd9c0e3',
            'bf9bd0',
            'a279b8',
            '855d9c',
            '6a497d',
            '543b63',
            '3a2a45',
            '261c2e',
        ),
        (
            'f8f6f9',
            'f1eef3',
            'e4dfe7',
            'cfc8d4',
            'a39aaa',
            '716878',
            '58505e',
            '433d48',
            '2d2931',
            '1e1b21',
            '141216',
        ),
    ),
    ColorScheme(
        'graphite',
        'Graphite',
        'Near monochrome',
        (
            'f6f6f7',
            'e6e7e9',
            'cdcfd3',
            'a9acb3',
            '7f838c',
            '62666f',
            '4b4e56',
            '3a3d43',
            '27292e',
            '18191c',
        ),
        (
            'f7f7f8',
            'efeff1',
            'e1e2e5',
            'c9cbcf',
            '9a9da3',
            '696c73',
            '51545a',
            '3e4045',
            '292b2e',
            '1b1c1f',
            '121214',
        ),
        solid=True,
    ),
)

SCHEMES: dict[str, ColorScheme] = {scheme.id: scheme for scheme in _SCHEMES}

#: `(value, label)` pairs for a model or form field.
CHOICES = [(scheme.id, scheme.label) for scheme in _SCHEMES]


def all_schemes() -> list[ColorScheme]:
    return list(_SCHEMES)


def get_scheme(scheme_id: object) -> ColorScheme:
    """The scheme with that id; the default for anything else, so a stale value never breaks a page."""
    if isinstance(scheme_id, str) and scheme_id in SCHEMES:
        return SCHEMES[scheme_id]
    return SCHEMES[DEFAULT]


def scheme_for(user: object) -> ColorScheme:
    """The scheme a page renders for `user`: its `color_scheme`, the default without one (a visitor)."""
    return get_scheme(getattr(user, 'color_scheme', None))


def _channels(hex_color: str) -> str:
    """`8fa89e` as `143 168 158`, the form `rgb(var(--x) / <alpha>)` needs."""
    return ' '.join(str(int(hex_color[i : i + 2], 16)) for i in (0, 2, 4))


def _variables(scheme: ColorScheme) -> str:
    lines = [
        f'  --c-brand-{step}: {_channels(value)};' for step, value in zip(BRAND_STEPS, scheme.brand)
    ]
    lines += [
        f'  --c-gray-{step}: {_channels(value)};' for step, value in zip(GRAY_STEPS, scheme.gray)
    ]
    return '\n'.join(lines)


def render_css() -> str:
    """The stylesheet that gives the colour variables their values, per `<html data-scheme>`."""
    parts = [
        '/* Generated by `./manage.py build_color_schemes` from apps/core/color_schemes.py. Do not edit. */',
    ]
    for scheme in _SCHEMES:
        selector = f'html[data-scheme="{scheme.id}"]'
        if scheme.id == DEFAULT:
            # Also the fallback for a page without the attribute or with an unknown value.
            selector = f':root, {selector}'
        parts.append(f'{selector} {{\n{_variables(scheme)}\n}}')
    for scheme in _SCHEMES:
        if not scheme.solid:
            continue
        # Light mode only: in dark mode white on the mid accent is under 4.5:1, so the common
        # recipe (light accent, dark label) applies there.
        light = f'html[data-scheme="{scheme.id}"]:not(.dark)'
        parts.append(
            f'{light} .bg-brand-primary {{ background-color: rgb(var(--c-brand-600)); color: #fff; }}\n'
            f'{light} .bg-brand-primary:hover {{ background-color: rgb(var(--c-brand-700)); }}'
        )
    return '\n\n'.join(parts) + '\n'
