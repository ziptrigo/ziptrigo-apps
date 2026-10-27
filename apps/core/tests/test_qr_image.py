import struct

import pytest

from apps.core.services.qr_image import MAX_BORDER, MAX_SCALE, render_png

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def _size(png: bytes) -> tuple[int, int]:
    """Width and height from the IHDR chunk."""
    return struct.unpack('>II', png[16:24])


def test_renders_a_png():
    png = render_png('https://app.ziptrigo.com/s/abc1234')
    assert png.startswith(PNG_SIGNATURE)
    width, height = _size(png)
    assert width == height > 0


def test_scale_makes_the_image_bigger():
    small = _size(render_png('https://example.com', scale=2))[0]
    large = _size(render_png('https://example.com', scale=8))[0]
    assert large == small * 4


def test_border_widens_the_quiet_zone_by_scale_pixels_per_module():
    without = _size(render_png('https://example.com', scale=4, border=0))[0]
    with_border = _size(render_png('https://example.com', scale=4, border=3))[0]
    assert with_border - without == 2 * 3 * 4


def test_same_content_renders_the_same_image():
    assert render_png('https://example.com/a') == render_png('https://example.com/a')


def test_different_content_renders_a_different_image():
    assert render_png('https://example.com/a') != render_png('https://example.com/b')


@pytest.mark.parametrize(
    'kwargs',
    [
        {'scale': 0},
        {'scale': MAX_SCALE + 1},
        {'border': -1},
        {'border': MAX_BORDER + 1},
    ],
)
def test_out_of_range_options_are_refused(kwargs):
    with pytest.raises(ValueError):
        render_png('https://example.com', **kwargs)


def test_empty_content_is_refused():
    with pytest.raises(ValueError):
        render_png('')


def test_content_too_long_for_a_qr_code_is_refused():
    with pytest.raises(ValueError):
        render_png('x' * 10_000)
