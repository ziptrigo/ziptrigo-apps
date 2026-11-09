"""Write the colour schemes' stylesheet and recoloured logos from `apps.core.color_schemes`."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image

from apps.core import color_schemes

STATIC = Path(__file__).resolve().parents[2] / 'static'


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _luminance(rgb: tuple[int, int, int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _lookup_tables(
    source: color_schemes.ColorScheme, target: color_schemes.ColorScheme
) -> list[int]:
    """For each grey level, the target colour that sits where that level sits on the source scale.

    The logo is drawn in the source (sage) scale only, so a pixel's brightness says which step of
    the scale it is; the same step of the target scale is its new colour.
    """
    # Darkest first, so the luminances rise.
    source_levels = [_luminance(_rgb(value)) for value in reversed(source.brand)]
    target_colors = [_rgb(value) for value in reversed(target.brand)]
    last = len(source_levels) - 1
    table: list[tuple[int, int, int]] = []
    for level in range(256):
        if level <= source_levels[0]:
            # Below the darkest step: keep the same share of its brightness.
            share = level / source_levels[0]
            table.append(tuple(round(c * share) for c in target_colors[0]))  # ty: ignore[invalid-argument-type]
            continue
        if level >= source_levels[last]:
            share = (level - source_levels[last]) / (255 - source_levels[last])
            table.append(tuple(round(c + (255 - c) * share) for c in target_colors[last]))  # ty: ignore[invalid-argument-type]
            continue
        index = max(i for i in range(last) if source_levels[i] <= level)
        share = (level - source_levels[index]) / (source_levels[index + 1] - source_levels[index])
        low, high = target_colors[index], target_colors[index + 1]
        table.append(tuple(round(a + (b - a) * share) for a, b in zip(low, high)))  # ty: ignore[invalid-argument-type]
    return [color[channel] for channel in range(3) for color in table]


def recolor(image: Image.Image, target: color_schemes.ColorScheme) -> Image.Image:
    """`image` (the sage logo) in `target`'s accent colours, transparency kept."""
    source = color_schemes.SCHEMES[color_schemes.DEFAULT]
    rgba = image.convert('RGBA')
    grey = rgba.convert('L')
    colored = Image.merge('RGB', (grey, grey, grey)).point(_lookup_tables(source, target))
    colored.putalpha(rgba.getchannel('A'))
    return colored


class Command(BaseCommand):
    help = (
        'Write core/css/color_schemes.css and the per-scheme logos from apps/core/color_schemes.py.'
    )

    def handle(self, *args, **options):
        css = STATIC / color_schemes.CSS_PATH
        css.write_text(color_schemes.render_css())
        self.stdout.write(f'Wrote {css.relative_to(STATIC)}')

        reference = Path(settings.PROJECT_ROOT) / color_schemes.REFERENCE_JS_PATH
        reference.write_text(color_schemes.render_reference_js())
        self.stdout.write(f'Wrote {color_schemes.REFERENCE_JS_PATH}')

        default = color_schemes.SCHEMES[color_schemes.DEFAULT]
        for scheme in color_schemes.all_schemes():
            if scheme.id == color_schemes.DEFAULT:
                continue
            for size in color_schemes.LOGO_SIZES:
                target = STATIC / scheme.logo_path(size)
                target.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(STATIC / default.logo_path(size)) as original:
                    recolor(original, scheme).save(target, optimize=True)
                self.stdout.write(f'Wrote {target.relative_to(STATIC)}')
