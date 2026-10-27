"""The Short URLs JWT API (`/api/s/`, issue #162 phase 4): create, list, read, edit, enable,
disable and delete links, their statistics, QR image and preview -- logged-in users only, calling
the same `apps.short_url.services` the web pages do. See `common.py` for the status codes and
`router.py` for why the endpoint modules share one `Router` instance.
"""

from . import links as _links  # noqa: F401 -- imported for its side effect (registers endpoints)
from . import stats as _stats  # noqa: F401 -- same
from .router import router

__all__ = ['router']
