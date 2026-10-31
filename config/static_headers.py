"""Extra response headers for static files served by WhiteNoise.

Django-free on purpose, like the other small helpers `settings.py` imports.

WhiteNoise gives an unhashed static file `Cache-Control: max-age=60` outside DEBUG, which is right for
CSS and JS that change with a deploy. The web fonts do not: they are named files that almost never
change, so they get a year and `immutable` and a browser stops asking about them. **If a font file is
ever replaced, give the new file a new name** (and point `core/css/site.css` at it), otherwise visitors
keep the old one for up to a year.
"""

from wsgiref.headers import Headers

#: The URL path, under STATIC_URL, that holds the self-hosted fonts (apps/core/static/core/fonts/).
FONTS_PATH = '/core/fonts/'

FONT_CACHE_CONTROL = 'public, max-age=31536000, immutable'


def add_static_headers(headers: Headers, path: str, url: str) -> None:
    """`WHITENOISE_ADD_HEADERS_FUNCTION`: called for every static file as WhiteNoise indexes it."""
    if FONTS_PATH in url and url.endswith('.woff2'):
        headers['Cache-Control'] = FONT_CACHE_CONTROL
