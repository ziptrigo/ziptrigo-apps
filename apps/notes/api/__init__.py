"""The Notes JWT API (`/api/notes/`, issue #260 PR 6): notes, their versions, share links, tags,
saved filters, settings and copying a shared note -- logged-in users only, calling the same
`apps.notes.services` the web pages do. See `router.py` for why the endpoint modules share one
`Router` instance, and `common.py` for the status codes.
"""

from . import filters as _filters  # noqa: F401 -- imported for its side effect (registers endpoints)
from . import links as _links  # noqa: F401 -- same
from . import notes as _notes  # noqa: F401 -- same
from . import settings as _settings  # noqa: F401 -- same
from . import shared as _shared  # noqa: F401 -- same
from . import tags as _tags  # noqa: F401 -- same
from . import versions as _versions  # noqa: F401 -- same
from .router import router

__all__ = ['router']
