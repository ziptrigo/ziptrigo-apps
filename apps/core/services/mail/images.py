"""The size of an image, read from its header alone (no pixel data is decoded, so a decompression
bomb costs nothing)."""

import io
import warnings

from PIL import Image

#: The content types `image_dimensions` is meant for.
IMAGE_TYPES = frozenset({'image/jpeg', 'image/png', 'image/webp', 'image/gif'})


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """`(width, height)` of an image, or `None` if it can't be opened."""
    try:
        with warnings.catch_warnings():
            # Pillow warns (and past twice its limit raises) when a header claims a huge image.
            warnings.simplefilter('ignore')
            with Image.open(io.BytesIO(data)) as image:
                return image.size
    except Exception:
        return None
